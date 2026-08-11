from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..interface import DiscoveryExclusions, FileResult
from .parser import (
    PascalImport,
    extract_pascal_imports,
    extract_pascal_module_name,
    extract_pascal_type_entities,
)


_PASCAL_EXTENSIONS = (".pas", ".pp", ".lpr", ".inc")


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path))).casefold()


@dataclass(frozen=True)
class BoundPascalLanguage:
    path_to_id: Mapping[str, str]
    ids_by_name: Mapping[str, tuple[str, ...]]
    ids_by_path: Mapping[str, str]
    directories_by_id: Mapping[str, str]
    explicit_paths: Mapping[tuple[str, str], tuple[str | None, ...]]
    imports_by_source: Mapping[str, tuple[PascalImport, ...]]
    type_names_by_id: Mapping[str, frozenset[str]]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundPascalLanguage:
        del root, discovery_exclusions
        names: dict[str, set[str]] = {}
        ids_by_path = {
            _normalized_path(path): node_id for path, node_id in path_to_id.items()
        }
        directories_by_id = {
            node_id: os.path.dirname(_normalized_path(path))
            for path, node_id in path_to_id.items()
        }
        explicit_paths: dict[tuple[str, str], list[str | None]] = {}
        imports_by_source: dict[str, tuple[PascalImport, ...]] = {}
        type_names_by_id: dict[str, frozenset[str]] = {}

        for path, node_id in path_to_id.items():
            suffix = Path(path).suffix.casefold()
            if suffix not in _PASCAL_EXTENSIONS:
                continue
            if suffix != ".inc":
                names.setdefault(Path(path).stem.casefold(), set()).add(node_id)
            try:
                content = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                print(
                    f"[belief-map] Cannot index Pascal declarations from {path}: {error}",
                    file=sys.stderr,
                )
                continue
            declared_name = extract_pascal_module_name(content)
            if declared_name and suffix != ".inc":
                names.setdefault(declared_name.casefold(), set()).add(node_id)
            parsed_imports = extract_pascal_imports(content)
            imports_by_source[_normalized_path(path)] = parsed_imports
            type_names_by_id[node_id] = frozenset(
                entity.name.casefold()
                for entity in extract_pascal_type_entities(content)
            )
            for item in parsed_imports:
                if not item.explicit_path:
                    continue
                resolved = cls._path_id(
                    item.explicit_path,
                    path,
                    ids_by_path,
                )
                explicit_paths.setdefault(
                    (_normalized_path(path), item.name.casefold()),
                    [],
                ).append(resolved)

        return cls(
            path_to_id,
            {key: tuple(sorted(value)) for key, value in names.items()},
            ids_by_path,
            directories_by_id,
            {key: tuple(value) for key, value in explicit_paths.items()},
            imports_by_source,
            type_names_by_id,
        )

    @staticmethod
    def _path_id(
        import_path: str,
        source_path: str,
        ids_by_path: Mapping[str, str],
    ) -> str | None:
        candidate = Path(import_path.replace("\\", os.sep))
        if not candidate.is_absolute():
            candidate = Path(source_path).parent / candidate
        paths = [candidate]
        if not candidate.suffix:
            paths.extend(Path(f"{candidate}{suffix}") for suffix in _PASCAL_EXTENSIONS)
        matches = {
            ids_by_path[normalized]
            for path in paths
            if (normalized := _normalized_path(str(path))) in ids_by_path
        }
        return next(iter(matches)) if len(matches) == 1 else None

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        if import_name.startswith("pascal-type:"):
            type_name = import_name.removeprefix("pascal-type:").casefold()
            provider_ids: set[str] = set()
            for item in self.imports_by_source.get(
                _normalized_path(source_path), ()
            ):
                if item.name.startswith("pascal-include:"):
                    continue
                target_id = self.resolve_import(item.name, source_path)
                if (
                    target_id
                    and type_name in self.type_names_by_id.get(target_id, frozenset())
                ):
                    provider_ids.add(target_id)
            return next(iter(provider_ids)) if len(provider_ids) == 1 else None
        if import_name.startswith("pascal-include:"):
            return self._path_id(
                import_name.removeprefix("pascal-include:"),
                source_path,
                self.ids_by_path,
            )
        explicit_key = (_normalized_path(source_path), import_name.casefold())
        if explicit_key in self.explicit_paths:
            candidates = self.explicit_paths[explicit_key]
            return candidates[0] if len(candidates) == 1 else None
        candidates = self.ids_by_name.get(import_name.casefold(), ())
        source_directory = os.path.dirname(_normalized_path(source_path))
        local_candidates = tuple(
            candidate
            for candidate in candidates
            if self.directories_by_id.get(candidate) == source_directory
        )
        if len(local_candidates) == 1:
            return local_candidates[0]
        return candidates[0] if len(candidates) == 1 else None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()


__all__ = ["BoundPascalLanguage"]
