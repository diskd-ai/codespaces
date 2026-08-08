from __future__ import annotations

import re

import tree_sitter_go as _tsg
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash
from ..treesitter import node_text, unique_strings, walk_named


_GO_LANGUAGE = _TreeSitterLanguage(_tsg.language())


def _field_children(node: Node, field_name: str) -> list[Node]:
    return [
        child
        for index, child in enumerate(node.children)
        if node.field_name_for_child(index) == field_name
    ]


def _import_specs(root: Node) -> list[tuple[str, str]]:
    imports: list[tuple[str, str]] = []
    for node in walk_named(root):
        if node.type != "import_spec":
            continue
        import_path = node_text(node.child_by_field_name("path")).strip('"`')
        alias = node_text(node.child_by_field_name("name"))
        if import_path:
            imports.append((import_path, alias))
    return imports


def _type_entities(root: Node) -> list[Entity]:
    entities: list[Entity] = []
    for declaration in root.named_children:
        if declaration.type != "type_declaration":
            continue
        for spec in declaration.named_children:
            if spec.type not in ("type_spec", "type_alias"):
                continue
            name = node_text(spec.child_by_field_name("name"))
            type_node = spec.child_by_field_name("type")
            if not name or type_node is None:
                continue
            if type_node.type == "struct_type":
                kind = "class"
                methods: list[str] = []
            elif type_node.type == "interface_type":
                kind = "interface"
                methods = unique_strings(
                    node_text(method.child_by_field_name("name"))
                    for method in walk_named(type_node)
                    if method.type == "method_elem"
                )
            else:
                kind = "type"
                methods = []
            entities.append(Entity(
                name=name,
                kind=kind,
                line=spec.start_point[0] + 1,
                methods=methods,
                decorators=[],
                bases=[],
            ))
    return entities


def _value_entities(root: Node) -> list[Entity]:
    entities: list[Entity] = []
    declaration_types = {"const_declaration", "var_declaration"}
    spec_types = {"const_spec", "var_spec"}
    for declaration in root.named_children:
        if declaration.type not in declaration_types:
            continue
        for spec in walk_named(declaration):
            if spec.type not in spec_types:
                continue
            for name_node in _field_children(spec, "name"):
                name = node_text(name_node)
                if name:
                    entities.append(Entity(
                        name=name,
                        kind="function",
                        line=spec.start_point[0] + 1,
                        methods=[],
                        decorators=[],
                        bases=[],
                    ))
    return entities


def _function_entities(root: Node) -> list[Entity]:
    return [
        Entity(
            name=name,
            kind="function",
            line=node.start_point[0] + 1,
            methods=[],
            decorators=[],
            bases=[],
        )
        for node in root.named_children
        if node.type == "function_declaration"
        if (name := node_text(node.child_by_field_name("name")))
    ]


def _receiver_type(node: Node) -> str:
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return ""
    return next(
        (
            node_text(descendant)
            for descendant in walk_named(receiver)
            if descendant.type == "type_identifier"
        ),
        "",
    )


def _attach_methods(root: Node, entities: list[Entity]) -> None:
    entities_by_name = {entity.name: entity for entity in entities}
    for node in root.named_children:
        if node.type != "method_declaration":
            continue
        receiver = _receiver_type(node)
        method = node_text(node.child_by_field_name("name"))
        entity = entities_by_name.get(receiver)
        if entity is not None and method and method not in entity.methods:
            entity.methods.append(method)


def parse_go_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    root = Parser(_GO_LANGUAGE).parse(content.encode("utf-8")).root_node
    import_specs = _import_specs(root)
    entities = [
        *_type_entities(root),
        *_value_entities(root),
        *_function_entities(root),
    ]
    _attach_methods(root, entities)
    imports = unique_strings(import_path for import_path, _ in import_specs)
    imported_names = [
        ImportedName(
            local_name=alias or import_path.rsplit("/", 1)[-1],
            original_name=import_path.rsplit("/", 1)[-1],
            module=import_path,
        )
        for import_path, alias in import_specs
        if alias != "_"
    ]
    entity_payloads = [entity.to_dict() for entity in entities]
    return FileResult(
        path=path,
        language="go",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=[
            entity.name for entity in entities if entity.kind == "interface"
        ],
        implements=[],
        extends=[],
        purpose=infer_purpose(path, content, "go", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=bool(re.search(
            r"go-playground/validator|validate:\"|binding:\"",
            content,
        )),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=[entity.name for entity in entities],
    )
