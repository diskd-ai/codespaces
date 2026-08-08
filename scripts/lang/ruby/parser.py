from __future__ import annotations

import re

import tree_sitter_ruby as _tsruby
from tree_sitter import Language as _TreeSitterLanguage
from tree_sitter import Node, Parser

from ..interface import Entity, FileResult, ImportedName, SourceRelationPayload
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash
from ..treesitter import node_text, unique_strings


_RUBY_LANGUAGE = _TreeSitterLanguage(_tsruby.language())
_DECLARATION_NODES = frozenset({"class", "module"})
_ASSOCIATIONS = frozenset({
    "belongs_to",
    "has_one",
    "has_many",
    "has_and_belongs_to_many",
})
_MIXINS = frozenset({"include", "prepend", "extend"})
_CALLBACK_PREFIXES = ("before_", "after_", "around_")


def _qualified_name(raw_name: str, namespace: str) -> str:
    name = raw_name.removeprefix("::")
    if not name or "::" in name or not namespace:
        return name
    return f"{namespace}::{name}"


def _direct_methods(body: Node | None, owner: str) -> list[Entity]:
    if body is None:
        return []
    methods: list[Entity] = []
    for child in body.named_children:
        if child.type in ("method", "singleton_method"):
            method_name = node_text(child.child_by_field_name("name"))
            if not method_name:
                continue
            is_singleton = child.type == "singleton_method"
            separator = "." if is_singleton else "#"
            methods.append(Entity(
                name=f"{owner}{separator}{method_name}",
                kind="function",
                line=child.start_point[0] + 1,
                methods=[],
                decorators=["singleton"] if is_singleton else [],
                bases=[],
            ))
            continue
        if child.type != "singleton_class":
            continue
        singleton_body = child.child_by_field_name("body")
        if singleton_body is None:
            continue
        for method in singleton_body.named_children:
            if method.type != "method":
                continue
            method_name = node_text(method.child_by_field_name("name"))
            if method_name:
                methods.append(Entity(
                    name=f"{owner}.{method_name}",
                    kind="function",
                    line=method.start_point[0] + 1,
                    methods=[],
                    decorators=["singleton"],
                    bases=[],
                ))
    return methods


def _constant_assignment_name(node: Node, namespace: str) -> str:
    if node.type != "assignment":
        return ""
    left = node.child_by_field_name("left")
    if left is None or left.type not in ("constant", "scope_resolution"):
        return ""
    return _qualified_name(node_text(left), namespace)


def _extract_entities(root: Node) -> tuple[list[Entity], list[str]]:
    entities: list[Entity] = []
    exported_names: list[str] = []

    def visit(node: Node, namespace: str) -> None:
        if node.type in _DECLARATION_NODES:
            declaration_name = _qualified_name(
                node_text(node.child_by_field_name("name")),
                namespace,
            )
            if not declaration_name:
                return
            body = node.child_by_field_name("body")
            method_entities = _direct_methods(body, declaration_name)
            base = ""
            superclass = node.child_by_field_name("superclass")
            if superclass is not None:
                base = next(
                    (
                        node_text(child)
                        for child in superclass.named_children
                        if child.type in ("constant", "scope_resolution")
                    ),
                    "",
                )
            entities.append(Entity(
                name=declaration_name,
                kind="class" if node.type == "class" else "module",
                line=node.start_point[0] + 1,
                methods=unique_strings(iter(
                    method.name.rsplit("#", 1)[-1].rsplit(".", 1)[-1]
                    for method in method_entities
                )),
                decorators=[],
                bases=[base] if base else [],
            ))
            entities.extend(method_entities)
            exported_names.append(declaration_name)
            if body is not None:
                for child in body.named_children:
                    if child.type not in (
                        "method",
                        "singleton_method",
                        "singleton_class",
                    ):
                        visit(child, declaration_name)
            return

        assigned_name = _constant_assignment_name(node, namespace)
        if assigned_name:
            entities.append(Entity(
                name=assigned_name,
                kind="type",
                line=node.start_point[0] + 1,
                methods=[],
                decorators=["constant"],
                bases=[],
            ))
            exported_names.append(assigned_name)
            right = node.child_by_field_name("right")
            if right is not None:
                visit(right, namespace)
            return

        if node.type in ("method", "singleton_method") and not namespace:
            method_name = node_text(node.child_by_field_name("name"))
            if method_name:
                entities.append(Entity(
                    name=method_name,
                    kind="function",
                    line=node.start_point[0] + 1,
                    methods=[],
                    decorators=[],
                    bases=[],
                ))
            return

        for child in node.named_children:
            visit(child, namespace)

    visit(root, "")
    return entities, unique_strings(iter(exported_names))


def ruby_declarations(
    content: str,
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    root = Parser(_RUBY_LANGUAGE).parse(content.encode("utf-8")).root_node
    entities, exported_names = _extract_entities(root)
    bases = {
        entity.name: tuple(entity.bases)
        for entity in entities
        if entity.kind == "class" and entity.bases
    }
    return tuple(exported_names), bases


def _literal_value(node: Node | None) -> str:
    if node is None:
        return ""
    raw = node_text(node).strip()
    if node.type in ("simple_symbol", "symbol") and raw.startswith(":"):
        return raw[1:].strip("'\"")
    if node.type == "string" and len(raw) >= 2:
        if "#{" in raw:
            return ""
        if raw[0] == raw[-1] and raw[0] in ("'", '"'):
            return raw[1:-1]
    return ""


def _call_method(node: Node) -> str:
    return node_text(node.child_by_field_name("method"))


def _call_arguments(node: Node) -> tuple[Node, ...]:
    arguments = node.child_by_field_name("arguments")
    return tuple(arguments.named_children) if arguments is not None else ()


def _keyword_argument(arguments: tuple[Node, ...], name: str) -> Node | None:
    for argument in arguments:
        if argument.type != "pair":
            continue
        key = node_text(argument.child_by_field_name("key")).strip(":")
        if key == name:
            return argument.child_by_field_name("value")
    return None


def _receiver_constant(node: Node | None) -> str:
    current = node
    while current is not None:
        if current.type in ("constant", "scope_resolution"):
            return node_text(current).removeprefix("::")
        if current.type != "call":
            return ""
        current = current.child_by_field_name("receiver")
    return ""


def _walk_dependencies(
    root: Node,
    path: str,
    entities: list[Entity],
) -> tuple[list[str], list[ImportedName], list[SourceRelationPayload]]:
    imports: list[str] = []
    imported_names: list[ImportedName] = []
    relations: list[SourceRelationPayload] = []
    relation_keys: set[tuple[str, str, str, str]] = set()
    imported_keys: set[tuple[str, str]] = set()
    entity_by_name = {entity.name: entity for entity in entities}
    is_spec = "/spec/" in path.replace("\\", "/") or path.endswith("_spec.rb")

    def add_import(target: str, constant_name: str = "") -> None:
        imports.append(target)
        if not constant_name:
            return
        key = (constant_name.rsplit("::", 1)[-1], target)
        if key in imported_keys:
            return
        imported_keys.add(key)
        imported_names.append(ImportedName(
            local_name=key[0],
            original_name=constant_name,
            module=target,
        ))

    def add_relation(
        target: str,
        relation: str,
        constant_name: str = "",
        source_entity: str = "",
        target_entity: str = "",
    ) -> None:
        add_import(target, constant_name)
        key = (target, relation, source_entity, target_entity)
        if key not in relation_keys:
            relation_keys.add(key)
            payload: SourceRelationPayload = {
                "target": target,
                "relation": relation,
            }
            if source_entity:
                payload["source_entity"] = source_entity
            if target_entity:
                payload["target_entity"] = target_entity
            relations.append(payload)

    def add_constant(constant_name: str, relation: str = "") -> None:
        normalized = constant_name.removeprefix("::")
        if not normalized:
            return
        target = f"ruby-constant:{normalized}"
        if relation:
            add_relation(target, relation, normalized)
        else:
            add_import(target, normalized)

    def decorate(owner: str, value: str) -> None:
        entity = entity_by_name.get(owner)
        if entity is not None and value not in entity.decorators:
            entity.decorators.append(value)

    def visit(node: Node, owner: str, declaration_scope: bool) -> None:
        if node.type in _DECLARATION_NODES:
            declaration_name = _qualified_name(
                node_text(node.child_by_field_name("name")),
                owner,
            )
            superclass = node.child_by_field_name("superclass")
            if superclass is not None:
                visit(superclass, declaration_name, False)
            body = node.child_by_field_name("body")
            if body is not None:
                visit(body, declaration_name, True)
            return

        if node.type == "assignment":
            right = node.child_by_field_name("right")
            if right is not None:
                visit(right, owner, False)
            return

        if node.type in ("method", "singleton_method", "singleton_class"):
            for child in node.named_children:
                visit(child, owner, False)
            return

        if node.type == "scope_resolution":
            add_constant(node_text(node), "spec" if is_spec else "")
            return

        if node.type == "constant":
            add_constant(node_text(node), "spec" if is_spec else "")
            return

        if node.type == "call":
            method = _call_method(node)
            arguments = _call_arguments(node)
            if method in ("require", "require_relative") and arguments:
                required = _literal_value(arguments[0])
                if required:
                    prefix = (
                        "ruby-require-relative:"
                        if method == "require_relative"
                        else "ruby-require:"
                    )
                    add_import(f"{prefix}{required}")
            elif method in _MIXINS and owner and declaration_scope:
                for argument in arguments:
                    constant_name = _receiver_constant(argument)
                    if constant_name:
                        target = f"ruby-constant:{constant_name}"
                        add_relation(target, "concern", constant_name)
                        decorate(owner, f"{method}:{constant_name}")
            elif method in _ASSOCIATIONS and arguments and declaration_scope:
                class_name = _literal_value(
                    _keyword_argument(arguments, "class_name")
                )
                source_name = _literal_value(
                    _keyword_argument(arguments, "source")
                )
                is_polymorphic = node_text(
                    _keyword_argument(arguments, "polymorphic")
                ) == "true"
                association_name = _literal_value(arguments[0])
                if not is_polymorphic:
                    if class_name:
                        target = f"ruby-constant:{class_name}"
                        add_relation(target, "association", class_name)
                    elif source_name:
                        target = f"ruby-association-one:{source_name}"
                        add_relation(target, "association")
                    elif association_name:
                        cardinality = "many" if method in (
                            "has_many",
                            "has_and_belongs_to_many",
                        ) else "one"
                        target = (
                            f"ruby-association-{cardinality}:{association_name}"
                        )
                        add_relation(target, "association")
                if association_name:
                    decorate(owner, f"{method}:{association_name}")
            elif declaration_scope and (
                method.startswith(_CALLBACK_PREFIXES) or method == "validate"
            ):
                for argument in arguments:
                    callback = _literal_value(argument)
                    if callback:
                        target = f"ruby-local-method:{callback}"
                        add_relation(
                            target,
                            "callback",
                            source_entity=owner,
                            target_entity=f"{owner}#{callback}",
                        )
                        decorate(owner, f"{method}:{callback}")

            receiver_constant = _receiver_constant(
                node.child_by_field_name("receiver")
            )
            if method == "perform_later" and receiver_constant:
                add_relation(
                    f"ruby-constant:{receiver_constant}",
                    "job",
                    receiver_constant,
                )
            elif method == "deliver_later" and receiver_constant:
                add_relation(
                    f"ruby-constant:{receiver_constant}",
                    "mailer",
                    receiver_constant,
                )

            for child in node.named_children:
                visit(child, owner, False)
            return

        for child in node.named_children:
            visit(child, owner, declaration_scope)

    visit(root, "", False)
    return (
        unique_strings(iter(imports)),
        imported_names,
        relations,
    )


def parse_ruby_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    root = Parser(_RUBY_LANGUAGE).parse(content.encode("utf-8")).root_node
    entities, exported_names = _extract_entities(root)
    imports, imported_names, relations = _walk_dependencies(
        root,
        path,
        entities,
    )
    entity_payloads = [entity.to_dict() for entity in entities]
    bases = unique_strings(iter(
        base
        for entity in entities
        if entity.kind == "class"
        for base in entity.bases
    ))
    return FileResult(
        path=path,
        language="ruby",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=imports,
        exports_abstract=[],
        implements=[],
        extends=bases,
        purpose=infer_purpose(path, content, "ruby", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=bool(re.search(
            r"^\s*(?:validates|validate)\b",
            content,
            re.MULTILINE,
        )),
        entities=entity_payloads,
        imported_names=[item.to_dict() for item in imported_names],
        exported_names=exported_names,
        relations=relations,
    )
