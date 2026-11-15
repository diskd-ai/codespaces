from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Mapping

from ..interface import FileResult


def _freeze_index(index: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(sorted(targets))
        for name, targets in index.items()
    }


@dataclass(frozen=True)
class BoundCSharpLanguage:
    type_to_ids: Mapping[str, tuple[str, ...]]

    @classmethod
    def create(cls, path_to_id: Mapping[str, str]) -> BoundCSharpLanguage:
        from .parser import declared_csharp_types

        type_index: dict[str, set[str]] = {}
        for path, node_id in sorted(path_to_id.items()):
            if not path.endswith(".cs"):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as source:
                    content = source.read()
            except OSError as error:
                print(
                    f"[belief-map] Cannot index C# declarations from {path}: {error}",
                    file=sys.stderr,
                )
                continue
            for full_name in declared_csharp_types(content):
                type_index.setdefault(full_name, set()).add(node_id)
        return cls(_freeze_index(type_index))

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        candidate = import_name
        while candidate:
            type_targets = self.type_to_ids.get(candidate, ())
            if len(type_targets) == 1:
                return type_targets[0]
            candidate, separator, _ = candidate.rpartition(".")
            if not separator:
                break
        return None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
