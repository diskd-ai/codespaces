from __future__ import annotations

from collections.abc import Iterator

from tree_sitter import Node


def node_text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def walk_named(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from walk_named(child)


def unique_strings(values: Iterator[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
