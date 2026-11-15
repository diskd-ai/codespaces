from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..discovery import is_excluded_path, retained_directory_names
from ..interface import DiscoveryExclusions, FileResult


@dataclass(frozen=True)
class GoModule:
    import_path: str
    directory: str


def _load_go_modules(
    root: str,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[GoModule, ...]:
    modules: list[GoModule] = []
    for directory_path, directory_names, file_names in os.walk(root):
        directory_names[:] = retained_directory_names(
            discovery_exclusions,
            directory_path,
            directory_names,
        )
        if "go.mod" not in file_names:
            continue
        manifest_path = os.path.join(directory_path, "go.mod")
        if is_excluded_path(discovery_exclusions, manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest:
                content = manifest.read()
        except OSError as error:
            print(
                f"[belief-map] Cannot read Go module identity from {manifest_path}: {error}",
                file=sys.stderr,
            )
            continue
        match = re.search(r"^\s*module\s+([^\s]+)\s*$", content, re.MULTILINE)
        if match is not None:
            modules.append(
                GoModule(
                    import_path=match.group(1),
                    directory=os.path.normpath(directory_path),
                )
            )
    return tuple(
        sorted(
            modules,
            key=lambda module: len(module.import_path),
            reverse=True,
        )
    )


@dataclass(frozen=True)
class BoundGoLanguage:
    path_to_id: Mapping[str, str]
    modules: tuple[GoModule, ...]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundGoLanguage:
        return cls(
            path_to_id,
            _load_go_modules(root, discovery_exclusions),
        )

    def _package_ids(self, import_name: str) -> tuple[str, ...]:
        module = next(
            (
                candidate
                for candidate in self.modules
                if import_name == candidate.import_path
                or import_name.startswith(candidate.import_path + "/")
            ),
            None,
        )
        if module is None:
            return ()
        relative_package = import_name.removeprefix(module.import_path).lstrip("/")
        package_directory = os.path.normpath(
            os.path.join(module.directory, *relative_package.split("/"))
        )
        candidates = [
            node_id
            for path, node_id in self.path_to_id.items()
            if path.endswith(".go")
            and not path.endswith("_test.go")
            and Path(path).parent == Path(package_directory)
        ]
        return tuple(sorted(set(candidates)))

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        package_ids = self._package_ids(import_name)
        return package_ids[0] if package_ids else None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    node_id
                    for import_name in result.imports
                    for node_id in self._package_ids(import_name)
                }
            )
        )
