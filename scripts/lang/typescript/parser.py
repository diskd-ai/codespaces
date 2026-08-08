from __future__ import annotations

import re

import tree_sitter_typescript as _tst
from tree_sitter import Language as _TsLanguage
from tree_sitter import Node, Parser as _TsParser

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash


_TS_LANG = _TsLanguage(_tst.language_typescript())
_TSX_LANG = _TsLanguage(_tst.language_tsx())


def _node_text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _detect_validation(content: str) -> bool:
    return bool(re.search(
        r"\.safeParse|\.parse\(|zod\.|z\.|Joi\.|yup\.|ajv|validateSchema",
        content[:4096],
    ))

def _ts_get_decorators(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract @Decorator names from a node's preceding decorator nodes."""
    decorators = []
    for child in node.children:
        if child.type == "decorator":
            # decorator -> @expression -- get the identifier
            for dchild in child.children:
                if dchild.type == "identifier":
                    decorators.append(dchild.text.decode("utf-8"))
                elif dchild.type == "call_expression":
                    fn = dchild.child_by_field_name("function")
                    if fn:
                        decorators.append(fn.text.decode("utf-8").split(".")[-1])
    return decorators


def _ts_get_heritage(node) -> tuple[list[str], list[str]]:  # type: ignore[no-untyped-def]
    """Extract extends and implements from class_heritage."""
    extends: list[str] = []
    implements: list[str] = []
    for child in node.children:
        if child.type == "class_heritage":
            for hchild in child.children:
                if hchild.type == "extends_clause":
                    for t in hchild.children:
                        if t.type in ("type_identifier", "identifier"):
                            extends.append(t.text.decode("utf-8"))
                elif hchild.type == "implements_clause":
                    for t in hchild.children:
                        if t.type in ("type_identifier", "identifier"):
                            implements.append(t.text.decode("utf-8"))
    return extends, implements


def _ts_get_methods(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract method/property names from a class or interface body."""
    methods = []
    body = node.child_by_field_name("body")
    if not body:
        return methods
    skip = {"constructor", "if", "for", "while", "switch", "return"}
    for member in body.children:
        if member.type in ("method_definition", "method_signature",
                           "public_field_definition", "property_signature",
                           "abstract_method_signature"):
            name_node = member.child_by_field_name("name")
            if name_node:
                name = name_node.text.decode("utf-8")
                if name not in skip:
                    methods.append(name)
    return methods


# NestJS decorators that imply constructor-parameter dependency injection.
_NESTJS_INJECTION_DECOS: frozenset[str] = frozenset({
    "Injectable", "Controller", "Module", "Guard", "Interceptor", "Pipe", "Resolver",
})


def _ts_get_constructor_injections(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract interface/type names from constructor parameter types and @Inject decorators.

    Handles NestJS constructor injection patterns where typed constructor parameters
    declare interface dependencies. Only PascalCase names are returned to avoid
    tracking primitive or built-in types.
    """
    injected: list[str] = []
    body = node.child_by_field_name("body")
    if not body:
        return injected
    for member in body.children:
        if member.type != "method_definition":
            continue
        name_node = member.child_by_field_name("name")
        if not name_node or not name_node.text:
            continue
        if name_node.text.decode("utf-8") != "constructor":
            continue
        params = member.child_by_field_name("parameters")
        if not params:
            continue
        for param in params.children:
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            # Check for @Inject(TOKEN) decorator on the parameter
            for child in param.children:
                if child.type == "decorator":
                    for dc in child.children:
                        if dc.type == "call_expression":
                            fn = dc.child_by_field_name("function")
                            if fn and fn.text and fn.text.decode("utf-8") == "Inject":
                                args = dc.child_by_field_name("arguments")
                                if args:
                                    for arg in args.children:
                                        if arg.type in ("identifier", "string", "template_string"):
                                            text = arg.text
                                            if text:
                                                token = text.decode("utf-8").strip("'\"`")
                                                if token and token[0].isupper():
                                                    injected.append(token)
            # Check type annotation on the parameter
            type_ann = param.child_by_field_name("type")
            if type_ann:
                for tc in type_ann.children:
                    if tc.type in ("type_identifier", "identifier"):
                        ttext = tc.text
                        if ttext:
                            tname = ttext.decode("utf-8")
                            if tname and tname[0].isupper():
                                injected.append(tname)
    return injected


def _ts_get_obj_keys(node) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract top-level property names from an object literal."""
    keys = []
    for child in node.children:
        if child.type in ("pair", "method_definition", "shorthand_property_identifier"):
            if child.type == "shorthand_property_identifier":
                keys.append(child.text.decode("utf-8"))
            else:
                key = child.child_by_field_name("key")
                if key:
                    keys.append(key.text.decode("utf-8"))
    return keys


def _ts_dependency_specifiers(root) -> list[str]:  # type: ignore[no-untyped-def]
    """Extract static and literal runtime dependencies in source order."""
    dependencies: list[str] = []
    seen: set[str] = set()

    def _append_literal(node) -> None:  # type: ignore[no-untyped-def]
        if node is None or not node.text:
            return
        if node.type == "string":
            specifier = node.text.decode("utf-8").strip("'\"")
        elif node.type == "template_string":
            if any(
                child.type == "template_substitution"
                for child in node.named_children
            ):
                return
            specifier = node.text.decode("utf-8").strip("`")
        else:
            return
        if specifier and specifier not in seen:
            seen.add(specifier)
            dependencies.append(specifier)

    def _visit(node) -> None:  # type: ignore[no-untyped-def]
        if node.type in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            if source is None and node.type == "import_statement":
                import_require = next(
                    (
                        child
                        for child in node.named_children
                        if child.type == "import_require_clause"
                    ),
                    None,
                )
                if import_require is not None:
                    source = import_require.child_by_field_name("source")
            _append_literal(source)
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            function_name = (
                function.text.decode("utf-8")
                if function is not None and function.text
                else ""
            )
            if function_name in ("import", "require"):
                arguments = node.child_by_field_name("arguments")
                if arguments is not None:
                    first_argument = next(iter(arguments.named_children), None)
                    _append_literal(first_argument)

        for child in node.named_children:
            _visit(child)

    _visit(root)
    return dependencies


def parse_typescript_treesitter(
    path: str, content: str, repo: str, mtime: float,
) -> FileResult:
    """Parse a TypeScript file using tree-sitter for precise entity extraction."""
    lang = _TSX_LANG if path.endswith(".tsx") else _TS_LANG
    parser = _TsParser(lang)
    tree = parser.parse(content.encode("utf-8"))
    root = tree.root_node

    imports = _ts_dependency_specifiers(root)
    exports_abstract: list[str] = []
    implements_list: list[str] = []
    extends_list: list[str] = []
    entities: list[Entity] = []
    imported_names: list[ImportedName] = []
    exported_names: list[str] = []

    for node in root.children:
        is_export = node.type == "export_statement"
        target = node
        if is_export:
            # The actual declaration is a child of export_statement.
            # Skip keywords, decorators, and punctuation to find the declaration.
            for child in node.children:
                if child.type in ("export", "default", "comment", "decorator",
                                  "{", "}", ",", ";"):
                    continue
                target = child
                break

        # -- Imports --
        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            if source_node:
                specifier = _node_text(source_node).strip("'\"")
                # Extract named imports
                for child in node.children:
                    if child.type == "import_clause":
                        for ic in child.children:
                            if ic.type == "identifier":
                                # default import
                                imported_names.append(ImportedName(
                                    local_name=_node_text(ic),
                                    original_name="default",
                                    module=specifier,
                                ))
                            elif ic.type == "named_imports":
                                for spec in ic.children:
                                    if spec.type == "import_specifier":
                                        name_node = spec.child_by_field_name("name")
                                        alias_node = spec.child_by_field_name("alias")
                                        if name_node:
                                            orig = _node_text(name_node)
                                            local = _node_text(alias_node) or orig
                                            imported_names.append(ImportedName(
                                                local_name=local,
                                                original_name=orig,
                                                module=specifier,
                                            ))

        # -- Class --
        elif target.type == "class_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            decorators = _ts_get_decorators(node if is_export else target)
            ext, impl = _ts_get_heritage(target)
            methods = _ts_get_methods(target)
            extends_list.extend(ext)
            implements_list.extend(impl)
            # NestJS @Injectable/@Controller/@Module: constructor param types are
            # interface dependencies that should create CALLS_API edges.
            if _NESTJS_INJECTION_DECOS.intersection(decorators):
                constructor_deps = _ts_get_constructor_injections(target)
                implements_list.extend(constructor_deps)
            # Check if abstract
            is_abstract = any(c.type == "abstract" for c in target.children)
            if is_abstract:
                exports_abstract.append(name)
            entities.append(Entity(
                name=name, kind="class", line=line,
                methods=methods, decorators=decorators, bases=ext + impl,
            ))
            exported_names.append(name)

        # -- Interface --
        elif target.type == "interface_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            methods = _ts_get_methods(target)
            exports_abstract.append(name)
            entities.append(Entity(
                name=name, kind="interface", line=line,
                methods=methods, decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Type alias --
        elif target.type == "type_alias_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            entities.append(Entity(
                name=name, kind="type", line=line,
                methods=[], decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Enum --
        elif target.type == "enum_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            entities.append(Entity(
                name=name, kind="enum", line=line,
                methods=[], decorators=[], bases=[],
            ))
            exported_names.append(name)

        # -- Function --
        elif target.type == "function_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                continue
            name = _node_text(name_node)
            line = target.start_point[0] + 1
            decorators = _ts_get_decorators(node if is_export else target)
            entities.append(Entity(
                name=name, kind="function", line=line,
                methods=[], decorators=decorators, bases=[],
            ))
            exported_names.append(name)

        # -- Lexical declaration (const/let) --
        elif target.type == "lexical_declaration":
            for declarator in target.children:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                if not name_node or name_node.type != "identifier":
                    continue
                name = _node_text(name_node)
                line = declarator.start_point[0] + 1
                value = declarator.child_by_field_name("value")
                if not value:
                    continue
                # Object literal: extract keys as methods
                if value.type == "object":
                    obj_keys = _ts_get_obj_keys(value)
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=obj_keys, decorators=[], bases=[],
                    ))
                # new Constructor(...)
                elif value.type == "new_expression":
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[],
                    ))
                # Arrow function or call expression
                elif value.type in ("arrow_function", "call_expression"):
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[],
                    ))
                # Other const (e.g., z.object, string literal, etc.)
                else:
                    entities.append(Entity(
                        name=name, kind="function", line=line,
                        methods=[], decorators=[], bases=[],
                    ))
                exported_names.append(name)

    file_lang = "tsx" if path.endswith(".tsx") else "typescript"
    return FileResult(
        path=path, language=file_lang, repo=repo, mtime=mtime,
        content_hash=file_hash(path),
        imports=imports, exports_abstract=exports_abstract,
        implements=implements_list, extends=extends_list,
        purpose=infer_purpose(path, content, "typescript", [e.to_dict() for e in entities]),
        naming_convention=detect_naming(path),
        has_validation=_detect_validation(content),
        entities=[e.to_dict() for e in entities],
        imported_names=[n.to_dict() for n in imported_names],
        exported_names=exported_names,
    )
