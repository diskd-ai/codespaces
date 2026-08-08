from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..interface import FileResult


def _candidate_bases(
    root: str,
    source_path: str,
    import_name: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    relative_level = len(import_name) - len(import_name.lstrip("."))
    module_name = import_name[relative_level:]
    parts = tuple(part for part in module_name.split(".") if part)
    source_directory = Path(os.path.abspath(source_path)).parent
    root_path = Path(os.path.abspath(root))

    if relative_level:
        relative_base = source_directory
        for _ in range(relative_level - 1):
            relative_base = relative_base.parent
        return (str(relative_base),), parts

    bases = [str(root_path), str(source_directory)]
    first_segment = parts[0] if parts else ""
    current = source_directory
    while current == root_path or root_path in current.parents:
        current_path = str(current)
        if (
            first_segment
            and (current / first_segment).is_dir()
            and current_path not in bases
        ):
            bases.append(current_path)
        if current == root_path:
            break
        current = current.parent
    return tuple(bases), parts


@dataclass(frozen=True)
class BoundPythonLanguage:
    root: str
    path_to_id: Mapping[str, str]

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        bases, parts = _candidate_bases(self.root, source_path, import_name)
        for base in bases:
            for candidate in (
                os.path.join(base, *parts) + ".py",
                os.path.join(base, *parts, "__init__.py"),
            ):
                target_id = self.path_to_id.get(os.path.normpath(candidate))
                if target_id is not None:
                    return target_id
        return None

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        extra_imports: list[str] = []
        for imported_name in result.imported_names:
            module = imported_name.get("module", "")
            original_name = imported_name.get("original", "")
            if not module or not original_name:
                continue

            bases, parts = _candidate_bases(self.root, result.path, module)
            for base in bases:
                submodule_path = os.path.normpath(
                    os.path.join(base, *parts, original_name + ".py")
                )
                target_id = self.path_to_id.get(submodule_path)
                if target_id is not None:
                    if target_id not in extra_imports:
                        extra_imports.append(target_id)
                    break

        return tuple(extra_imports)
