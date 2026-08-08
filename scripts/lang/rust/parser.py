from __future__ import annotations

import re
from dataclasses import dataclass

import tree_sitter_rust as _tsr
from tree_sitter import Language as _RustTreeSitterLanguage
from tree_sitter import Node, Parser as _RustParser

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash


_RUST_LANGUAGE = _RustTreeSitterLanguage(_tsr.language())


@dataclass(frozen=True)
class RustUse:
    path: str
    alias: str | None = None


def _node_text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _attribute_names(attributes: tuple[Node, ...]) -> list[str]:
    names: list[str] = []
    for attribute in attributes:
        match = re.match(r"#!?\[\s*([A-Za-z_][A-Za-z0-9_]*)", _node_text(attribute))
        if match is not None and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _use_leaves(node: Node, prefix: tuple[str, ...] = ()) -> list[RustUse]:
    if node.type in ("identifier", "type_identifier", "crate", "self", "super"):
        leaf = _node_text(node)
        return [RustUse("::".join((*prefix, leaf)))] if leaf else []

    if node.type == "scoped_identifier":
        path = _node_text(node)
        if prefix:
            path = "::".join((*prefix, path))
        return [RustUse(path)] if path else []

    if node.type == "use_as_clause":
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return []
        alias_node = next(
            (child for child in reversed(node.named_children) if child != path_node),
            None,
        )
        alias = _node_text(alias_node) or None
        return [RustUse(leaf.path, alias) for leaf in _use_leaves(path_node, prefix)]

    if node.type == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        path = _node_text(path_node)
        nested_prefix = (*prefix, *tuple(part for part in path.split("::") if part))
        use_list = next(
            (child for child in node.named_children if child.type == "use_list"),
            None,
        )
        return _use_leaves(use_list, nested_prefix) if use_list is not None else []

    if node.type == "use_list":
        leaves: list[RustUse] = []
        for child in node.named_children:
            if child.type == "self":
                path = "::".join(prefix)
                if path:
                    leaves.append(RustUse(path))
                continue
            leaves.extend(_use_leaves(child, prefix))
        return leaves

    if node.type == "use_wildcard":
        path = _node_text(node).removesuffix("::*")
        if prefix:
            path = "::".join((*prefix, path))
        return [RustUse(path)] if path else []

    argument = node.child_by_field_name("argument")
    return _use_leaves(argument, prefix) if argument is not None else []


def _collect_uses(root: Node) -> list[RustUse]:
    uses: list[RustUse] = []

    def _visit(node: Node) -> None:
        if node.type == "use_declaration":
            uses.extend(_use_leaves(node))
            return
        for child in node.named_children:
            _visit(child)

    _visit(root)
    unique: list[RustUse] = []
    seen: set[tuple[str, str | None]] = set()
    for rust_use in uses:
        key = (rust_use.path, rust_use.alias)
        if rust_use.path and key not in seen:
            seen.add(key)
            unique.append(rust_use)
    return unique


def _imported_name(rust_use: RustUse) -> ImportedName | None:
    parts = tuple(part for part in rust_use.path.split("::") if part)
    if len(parts) < 2:
        return None
    original_name = parts[-1]
    return ImportedName(
        local_name=rust_use.alias or original_name,
        original_name=original_name,
        module="::".join(parts[:-1]),
    )


def _method_names(body: Node | None) -> list[str]:
    if body is None:
        return []
    methods: list[str] = []
    for child in body.named_children:
        if child.type not in ("function_item", "function_signature_item"):
            continue
        name = _node_text(child.child_by_field_name("name"))
        if name and name not in methods:
            methods.append(name)
    return methods


def _walk_named(node: Node) -> list[Node]:
    descendants = [node]
    for child in node.named_children:
        descendants.extend(_walk_named(child))
    return descendants


def _type_name(node: Node | None) -> str:
    if node is None:
        return ""
    if node.type in ("identifier", "type_identifier", "primitive_type"):
        return _node_text(node)
    name = node.child_by_field_name("name")
    if name is not None:
        return _type_name(name)
    type_identifiers = [
        descendant
        for descendant in _walk_named(node)
        if descendant.type == "type_identifier"
    ]
    return _node_text(type_identifiers[-1]) if type_identifiers else ""


def _entity_from_item(node: Node, decorators: list[str]) -> Entity | None:
    name = _node_text(node.child_by_field_name("name"))
    if not name:
        return None

    kind_by_node = {
        "struct_item": "class",
        "union_item": "class",
        "trait_item": "interface",
        "enum_item": "enum",
        "type_item": "type",
        "function_item": "function",
        "const_item": "function",
        "static_item": "function",
    }
    kind = kind_by_node.get(node.type)
    if kind is None:
        return None

    methods: list[str] = []
    if node.type == "trait_item":
        methods = _method_names(node.child_by_field_name("body"))
    elif node.type == "enum_item":
        body = node.child_by_field_name("body")
        if body is not None:
            methods = [
                _node_text(child.child_by_field_name("name"))
                for child in body.named_children
                if child.type == "enum_variant"
                and _node_text(child.child_by_field_name("name"))
            ]

    return Entity(
        name=name,
        kind=kind,
        line=node.start_point[0] + 1,
        methods=methods,
        decorators=decorators,
        bases=[],
    )


def parse_rust_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    """Parse Rust source into the shared language-neutral graph contract."""
    tree = _RustParser(_RUST_LANGUAGE).parse(content.encode("utf-8"))
    root = tree.root_node
    rust_uses = _collect_uses(root)
    imports = [rust_use.path for rust_use in rust_uses]
    imported_names = [
        imported_name
        for rust_use in rust_uses
        if (imported_name := _imported_name(rust_use)) is not None
    ]

    entities: list[Entity] = []
    entities_by_name: dict[str, Entity] = {}
    pending_attributes: list[Node] = []
    for node in root.named_children:
        if node.type in ("attribute_item", "inner_attribute_item"):
            pending_attributes.append(node)
            continue

        decorators = _attribute_names(tuple(pending_attributes))
        pending_attributes.clear()
        entity = _entity_from_item(node, decorators)
        if entity is not None:
            entities.append(entity)
            entities_by_name[entity.name] = entity

    exports_abstract = [
        entity.name for entity in entities if entity.kind == "interface"
    ]
    implements: list[str] = []
    extends: list[str] = []

    for node in root.named_children:
        if node.type != "impl_item":
            continue
        implemented_type = _type_name(node.child_by_field_name("type"))
        implemented_trait = _type_name(node.child_by_field_name("trait"))
        methods = _method_names(node.child_by_field_name("body"))
        entity = entities_by_name.get(implemented_type)
        if entity is not None:
            entity.methods = sorted(set((*entity.methods, *methods)))
            if implemented_trait and implemented_trait not in entity.bases:
                entity.bases.append(implemented_trait)
        if implemented_trait and implemented_trait not in implements:
            implements.append(implemented_trait)

    for node in root.named_children:
        if node.type != "mod_item" or node.child_by_field_name("body") is not None:
            continue
        module_name = _node_text(node.child_by_field_name("name"))
        module_import = f"self::{module_name}"
        if module_name and module_import not in imports:
            imports.append(module_import)

    entity_payloads = [entity.to_dict() for entity in entities]
    return FileResult(
        path=path,
        language="rust",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=exports_abstract,
        implements=implements,
        extends=extends,
        purpose=infer_purpose(path, content, "rust", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=False,
        entities=entity_payloads,
        imported_names=[imported_name.to_dict() for imported_name in imported_names],
        exported_names=[entity.name for entity in entities],
    )
