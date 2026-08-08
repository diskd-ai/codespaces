from __future__ import annotations

import re
from dataclasses import dataclass

import tree_sitter_java as _tsj
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash
from ..treesitter import node_text, unique_strings, walk_named


_JAVA_LANGUAGE = _TreeSitterLanguage(_tsj.language())
_TYPE_NODES = frozenset({
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
})
_KIND_BY_NODE = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "annotation_type_declaration": "interface",
}


@dataclass(frozen=True)
class JavaImport:
    target: str
    is_static: bool
    is_wildcard: bool


def _package_name(root: Node) -> str:
    package = next(
        (child for child in root.named_children if child.type == "package_declaration"),
        None,
    )
    if package is None or not package.named_children:
        return ""
    return node_text(package.named_children[0])


def declared_java_types(content: str) -> tuple[str, ...]:
    root = Parser(_JAVA_LANGUAGE).parse(content.encode("utf-8")).root_node
    package = _package_name(root)
    return tuple(
        f"{package}.{name}" if package else name
        for node in root.named_children
        if node.type in _TYPE_NODES
        if (name := node_text(node.child_by_field_name("name")))
    )


def _annotations(node: Node) -> list[str]:
    modifiers = next(
        (child for child in node.named_children if child.type == "modifiers"),
        None,
    )
    if modifiers is None:
        return []
    return unique_strings(
        node_text(annotation.child_by_field_name("name"))
        for annotation in modifiers.named_children
        if annotation.type in ("annotation", "marker_annotation")
    )


def _method_names(node: Node) -> list[str]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    method_types = {"method_declaration", "annotation_type_element_declaration"}
    return unique_strings(
        node_text(child.child_by_field_name("name"))
        for child in body.named_children
        if child.type in method_types
    )


def _enum_members(node: Node) -> list[str]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    return unique_strings(
        node_text(child.child_by_field_name("name"))
        for child in body.named_children
        if child.type == "enum_constant"
    )


def _base_names(node: Node) -> list[str]:
    return unique_strings(
        node_text(descendant)
        for child in node.named_children
        if child.type in ("superclass", "super_interfaces", "extends_interfaces")
        for descendant in walk_named(child)
        if descendant.type == "type_identifier"
    )


def _entity(node: Node) -> Entity | None:
    name = node_text(node.child_by_field_name("name"))
    kind = _KIND_BY_NODE.get(node.type)
    if not name or kind is None:
        return None
    methods = _enum_members(node) if kind == "enum" else _method_names(node)
    return Entity(
        name=name,
        kind=kind,
        line=node.start_point[0] + 1,
        methods=methods,
        decorators=_annotations(node),
        bases=_base_names(node),
    )


def _parse_import(node: Node) -> JavaImport | None:
    target_node = next(
        (
            child
            for child in node.named_children
            if child.type in ("identifier", "scoped_identifier")
        ),
        None,
    )
    target = node_text(target_node)
    if not target:
        return None
    text = node_text(node)
    is_wildcard = any(child.type == "asterisk" for child in node.named_children)
    return JavaImport(
        target=f"{target}.*" if is_wildcard else target,
        is_static=bool(re.match(r"import\s+static\b", text)),
        is_wildcard=is_wildcard,
    )


def _referenced_type_names(root: Node) -> list[str]:
    return unique_strings(
        node_text(node)
        for node in walk_named(root)
        if node.type == "type_identifier"
    )


def parse_java_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    root = Parser(_JAVA_LANGUAGE).parse(content.encode("utf-8")).root_node
    package = _package_name(root)
    declaration_nodes = [
        node for node in root.named_children if node.type in _TYPE_NODES
    ]
    entity_nodes = [
        (entity, node)
        for node in declaration_nodes
        if (entity := _entity(node)) is not None
    ]
    entities = [entity for entity, _ in entity_nodes]
    declared_names = {entity.name for entity in entities}
    referenced_types = [
        name
        for name in _referenced_type_names(root)
        if name not in declared_names
    ]
    java_imports = [
        java_import
        for node in root.named_children
        if node.type == "import_declaration"
        if (java_import := _parse_import(node)) is not None
    ]

    imports: list[str] = []
    imported_names: list[ImportedName] = []
    for java_import in java_imports:
        imports.append(java_import.target)
        if java_import.is_wildcard:
            namespace = java_import.target.removesuffix(".*")
            for type_name in referenced_types:
                candidate = f"{namespace}.{type_name}"
                imports.append(candidate)
                imported_names.append(ImportedName(
                    local_name=type_name,
                    original_name=type_name,
                    module=candidate,
                ))
            continue

        original = java_import.target.rsplit(".", 1)[-1]
        imported_names.append(ImportedName(
            local_name=original,
            original_name=original,
            module=java_import.target,
        ))

    if package:
        for type_name in referenced_types:
            candidate = f"{package}.{type_name}"
            imports.append(candidate)
            imported_names.append(ImportedName(
                local_name=type_name,
                original_name=type_name,
                module=candidate,
            ))

    entity_payloads = [entity.to_dict() for entity in entities]
    exports_abstract = [
        entity.name
        for entity, node in entity_nodes
        if entity.kind == "interface"
        or re.search(r"\babstract\s+class\b", node_text(node)) is not None
    ]
    bases = unique_strings(
        base for entity in entities for base in entity.bases
    )
    return FileResult(
        path=path,
        language="java",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=unique_strings(iter(imports)),
        exports_abstract=exports_abstract,
        implements=bases,
        extends=bases,
        purpose=infer_purpose(path, content, "java", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=bool(re.search(
            r"@(Valid|Validated|NotNull|NotBlank|Size|Pattern)\b|javax\.validation|jakarta\.validation",
            content,
        )),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=[entity.name for entity in entities],
    )
