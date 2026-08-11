from __future__ import annotations

from dataclasses import dataclass
import re

from ..interface import Entity, FileResult, ImportedName
from ..purpose import infer_purpose
from ..source import detect_naming, file_hash


_ROUTINE_KEYWORDS = {
    "constructor",
    "destructor",
    "function",
    "procedure",
}
_SECTION_KEYWORDS = {
    "begin",
    "const",
    "implementation",
    "initialization",
    "finalization",
    "label",
    "procedure",
    "function",
    "constructor",
    "destructor",
    "resourcestring",
    "threadvar",
    "uses",
    "var",
}
_CONTAINER_KEYWORDS = {"class", "interface", "object", "record"}


@dataclass(frozen=True)
class PascalToken:
    kind: str
    value: str
    line: int

    @property
    def lower(self) -> str:
        return self.value.casefold()


@dataclass(frozen=True)
class PascalImport:
    name: str
    explicit_path: str = ""


def tokenize_pascal(content: str) -> list[PascalToken]:
    tokens: list[PascalToken] = []
    index = 0
    line = 1
    length = len(content)

    def consume_comment(end_marker: str, start: int) -> int:
        nonlocal line
        end = content.find(end_marker, start)
        if end < 0:
            line += content[start:].count("\n")
            return length
        finish = end + len(end_marker)
        line += content[start:finish].count("\n")
        return finish

    while index < length:
        char = content[index]
        if char.isspace():
            if char == "\n":
                line += 1
            index += 1
            continue
        if content.startswith("//", index):
            newline = content.find("\n", index + 2)
            index = length if newline < 0 else newline
            continue
        if char == "{":
            start_line = line
            end = content.find("}", index + 1)
            finish = length if end < 0 else end + 1
            body = content[index + 1:end if end >= 0 else length]
            if body.lstrip().startswith("$"):
                tokens.append(PascalToken("directive", body.strip(), start_line))
            line += content[index:finish].count("\n")
            index = finish
            continue
        if content.startswith("(*", index):
            start_line = line
            end = content.find("*)", index + 2)
            finish = length if end < 0 else end + 2
            body = content[index + 2:end if end >= 0 else length]
            if body.lstrip().startswith("$"):
                tokens.append(PascalToken("directive", body.strip(), start_line))
            line += content[index:finish].count("\n")
            index = finish
            continue
        if char == "'":
            start_line = line
            index += 1
            value: list[str] = []
            while index < length:
                if content[index] == "'":
                    if index + 1 < length and content[index + 1] == "'":
                        value.append("'")
                        index += 2
                        continue
                    index += 1
                    break
                if content[index] == "\n":
                    line += 1
                value.append(content[index])
                index += 1
            tokens.append(PascalToken("string", "".join(value), start_line))
            continue
        if char.isalpha() or char == "_":
            start = index
            index += 1
            while index < length and (
                content[index].isalnum() or content[index] == "_"
            ):
                index += 1
            tokens.append(PascalToken("ident", content[start:index], line))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < length and (
                content[index].isalnum() or content[index] in "._"
            ):
                index += 1
            tokens.append(PascalToken("number", content[start:index], line))
            continue
        tokens.append(PascalToken("symbol", char, line))
        index += 1

    return tokens


def _qualified_name(tokens: list[PascalToken], start: int) -> tuple[str, int]:
    if start >= len(tokens) or tokens[start].kind != "ident":
        return "", start
    parts = [tokens[start].value]
    index = start + 1
    while (
        index + 1 < len(tokens)
        and tokens[index].value == "."
        and tokens[index + 1].kind == "ident"
    ):
        parts.append(tokens[index + 1].value)
        index += 2
    return ".".join(parts), index


def _parse_dependency_clause(
    tokens: list[PascalToken], start: int
) -> tuple[list[PascalImport], int]:
    imports: list[PascalImport] = []
    index = start
    while index < len(tokens) and tokens[index].value != ";":
        name, after_name = _qualified_name(tokens, index)
        if not name:
            index += 1
            continue
        index = after_name
        explicit_path = ""
        if index < len(tokens) and tokens[index].lower == "in":
            index += 1
            if index < len(tokens) and tokens[index].kind == "string":
                explicit_path = tokens[index].value
                index += 1
        imports.append(PascalImport(name, explicit_path))
        while index < len(tokens) and tokens[index].value not in {",", ";"}:
            index += 1
        if index < len(tokens) and tokens[index].value == ",":
            index += 1
    return imports, index + 1


def extract_pascal_imports(content: str) -> tuple[PascalImport, ...]:
    tokens = tokenize_pascal(content)
    imports: list[PascalImport] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "directive":
            match = re.match(r"\$(?:i|include)\s+(.+?)\s*$", token.value, re.I)
            if match:
                path = match.group(1).strip().strip("'\"")
                if path:
                    imports.append(PascalImport(f"pascal-include:{path}", path))
            index += 1
            continue
        if token.lower in {"contains", "requires", "uses"}:
            found, index = _parse_dependency_clause(tokens, index + 1)
            imports.extend(found)
            continue
        index += 1

    unique: list[PascalImport] = []
    seen: set[tuple[str, str]] = set()
    for item in imports:
        key = (item.name.casefold(), item.explicit_path.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def extract_pascal_module_name(content: str) -> str:
    tokens = tokenize_pascal(content)
    for index, token in enumerate(tokens[:-1]):
        if token.lower in {"library", "package", "program", "unit"}:
            name, _ = _qualified_name(tokens, index + 1)
            return name
    return ""


def extract_pascal_type_entities(content: str) -> tuple[Entity, ...]:
    entities, _ = _type_entities(tokenize_pascal(content))
    return tuple(entities)


def _matching_end(tokens: list[PascalToken], start: int) -> int:
    depth = 1
    index = start + 1
    while index < len(tokens):
        token = tokens[index]
        if token.lower in _CONTAINER_KEYWORDS:
            previous = tokens[index - 1].lower if index else ""
            before_previous = tokens[index - 2].lower if index > 1 else ""
            next_token = tokens[index + 1].lower if index + 1 < len(tokens) else ""
            is_type_definition = previous == "=" or (
                previous == "packed" and before_previous == "="
            )
            if is_type_definition and next_token != "of":
                depth += 1
        elif token.lower == "end":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(tokens)


def _routine_name(tokens: list[PascalToken], start: int) -> tuple[str, int]:
    index = start + 1
    if index < len(tokens) and tokens[index].kind == "ident":
        name, end = _qualified_name(tokens, index)
        return name, end
    return "", index


def _container_methods(
    tokens: list[PascalToken], start: int, end: int
) -> list[str]:
    methods: list[str] = []
    for index in range(start + 1, min(end, len(tokens))):
        if tokens[index].lower not in _ROUTINE_KEYWORDS:
            continue
        name, _ = _routine_name(tokens, index)
        if name and "." not in name and name not in methods:
            methods.append(name)
    return methods


def _base_names(tokens: list[PascalToken], start: int) -> tuple[list[str], int]:
    index = start + 1
    if index >= len(tokens) or tokens[index].value != "(":
        return [], start
    index += 1
    bases: list[str] = []
    generic_depth = 0
    expect_base = True
    while index < len(tokens):
        token = tokens[index]
        if token.value == ")" and generic_depth == 0:
            break
        if token.value == "<":
            generic_depth += 1
            index += 1
            continue
        if token.value == ">" and generic_depth:
            generic_depth -= 1
            index += 1
            continue
        if token.value == "," and generic_depth == 0:
            expect_base = True
            index += 1
            continue
        if expect_base and token.lower == "specialize":
            index += 1
            continue
        if expect_base:
            name, after_name = _qualified_name(tokens, index)
            if name:
                bases.append(name)
                expect_base = False
                index = after_name
                continue
        index += 1
    return bases, index


def _declaration_end(tokens: list[PascalToken], start: int) -> int:
    depths = {"(": 0, "[": 0, "<": 0}
    closing = {")": "(", "]": "[", ">": "<"}
    index = start
    while index < len(tokens):
        value = tokens[index].value
        if value in depths:
            depths[value] += 1
        elif value in closing and depths[closing[value]]:
            depths[closing[value]] -= 1
        elif value == ";" and not any(depths.values()):
            return index
        index += 1
    return len(tokens)


def _type_entities(
    tokens: list[PascalToken],
) -> tuple[list[Entity], list[tuple[int, int]]]:
    entities: list[Entity] = []
    container_ranges: list[tuple[int, int]] = []
    index = 0
    in_type_section = False
    while index < len(tokens):
        token = tokens[index]
        if token.lower == "type":
            in_type_section = True
            index += 1
            continue
        if in_type_section and token.lower in _SECTION_KEYWORDS:
            in_type_section = False
        if not in_type_section:
            index += 1
            continue

        name_index = index
        if token.lower == "generic" and index + 1 < len(tokens):
            name_index = index + 1
        if tokens[name_index].kind != "ident":
            index += 1
            continue

        equals = name_index + 1
        if equals < len(tokens) and tokens[equals].value == "<":
            generic_depth = 1
            equals += 1
            while equals < len(tokens) and generic_depth:
                generic_depth += tokens[equals].value == "<"
                generic_depth -= tokens[equals].value == ">"
                equals += 1
        if equals >= len(tokens) or tokens[equals].value != "=":
            index += 1
            continue

        rhs = equals + 1
        if rhs < len(tokens) and tokens[rhs].lower == "packed":
            rhs += 1
        if rhs < len(tokens) and tokens[rhs].lower in {"generic", "specialize"}:
            rhs += 1
        kind_token = tokens[rhs].lower if rhs < len(tokens) else ""
        name = tokens[name_index].value
        is_class_reference = (
            kind_token == "class"
            and rhs + 1 < len(tokens)
            and tokens[rhs + 1].lower == "of"
        )
        if kind_token not in _CONTAINER_KEYWORDS or is_class_reference:
            entities.append(Entity(name, "type", token.line, [], [], []))
            index = _declaration_end(tokens, rhs) + 1
            continue

        bases, body_start = _base_names(tokens, rhs)
        if body_start == rhs:
            body_start = rhs
        after_header = body_start + 1
        is_forward = (
            after_header < len(tokens) and tokens[after_header].value == ";"
        )
        body_end = rhs if is_forward else _matching_end(tokens, rhs)
        kind = "interface" if kind_token == "interface" else (
            "class" if kind_token in {"class", "object"} else "type"
        )
        methods = [] if is_forward else _container_methods(tokens, body_start, body_end)
        entities.append(Entity(name, kind, token.line, methods, [], bases))
        container_ranges.append((rhs, body_end))
        index = body_end + 1
    merged: list[Entity] = []
    indexes: dict[str, int] = {}
    for entity in entities:
        key = entity.name.casefold()
        existing_index = indexes.get(key)
        if existing_index is None:
            indexes[key] = len(merged)
            merged.append(entity)
            continue
        existing = merged[existing_index]
        merged[existing_index] = Entity(
            name=entity.name,
            kind=(
                entity.kind
                if entity.kind != "type" or existing.kind == "type"
                else existing.kind
            ),
            line=entity.line,
            methods=list(dict.fromkeys([*existing.methods, *entity.methods])),
            decorators=list(dict.fromkeys([
                *existing.decorators,
                *entity.decorators,
            ])),
            bases=list(dict.fromkeys([*existing.bases, *entity.bases])),
        )
    return merged, container_ranges


def _routine_entities(
    tokens: list[PascalToken], container_ranges: list[tuple[int, int]]
) -> list[Entity]:
    entities: list[Entity] = []
    seen: set[str] = set()
    for index, token in enumerate(tokens):
        if token.lower not in _ROUTINE_KEYWORDS:
            continue
        if any(start <= index <= end for start, end in container_ranges):
            continue
        name, _ = _routine_name(tokens, index)
        if not name or "." in name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        entities.append(Entity(name, "function", token.line, [], [], []))
    return entities


def parse_pascal(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    tokens = tokenize_pascal(content)
    imports = list(extract_pascal_imports(content))
    type_entities, container_ranges = _type_entities(tokens)
    routine_entities = _routine_entities(tokens, container_ranges)
    entities = [*type_entities, *routine_entities]
    interface_names = {
        entity.name.casefold()
        for entity in entities
        if entity.kind == "interface"
    }
    extends: list[str] = []
    implements: list[str] = []
    for entity in type_entities:
        if entity.kind != "class":
            continue
        for position, base in enumerate(entity.bases):
            if (
                base.casefold() in interface_names
                or position > 0
                or re.match(r"^I[A-Z]", base)
            ):
                if base not in implements:
                    implements.append(base)
            elif position == 0 and base not in extends:
                extends.append(base)

    entity_payloads = [entity.to_dict() for entity in entities]
    import_names = [item.name for item in imports]
    return FileResult(
        path=path,
        language="pascal",
        repo=repo,
        mtime=mtime,
        content_hash=file_hash(path),
        imports=import_names,
        exports_abstract=[
            entity.name for entity in entities if entity.kind == "interface"
        ],
        implements=implements,
        extends=extends,
        purpose=infer_purpose(path, content, "pascal", entity_payloads),
        naming_convention=detect_naming(path),
        has_validation=False,
        entities=entity_payloads,
        imported_names=[
            *[
                ImportedName(item.name, item.name, item.name).to_dict()
                for item in imports
                if not item.name.startswith("pascal-include:")
            ],
            *[
                ImportedName(
                    base,
                    base,
                    f"pascal-type:{base}",
                ).to_dict()
                for entity in type_entities
                for base in entity.bases
            ],
        ],
        exported_names=[entity.name for entity in entities],
    )


__all__ = [
    "PascalImport",
    "extract_pascal_imports",
    "extract_pascal_module_name",
    "extract_pascal_type_entities",
    "parse_pascal",
    "tokenize_pascal",
]
