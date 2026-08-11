from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..discovery import is_excluded_directory, is_excluded_path
from ..interface import DiscoveryExclusions, FileResult


_SOURCE_SUFFIXES = (".rb", ".rake")
_ACRONYM_PATTERN = re.compile(
    r"\b(?:inflect\.)?acronym\s*(?:\(\s*)?['\"]([A-Za-z][A-Za-z0-9]*)['\"]"
)
_AUTOLOAD_JOIN_PATTERN = re.compile(
    r"autoload_(?:once_)?paths[^\n]*?(?:Rails|config)\.root\.join\(([^)]*)\)"
)
_QUOTED_SEGMENT_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")


def _freeze_index(index: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {name: tuple(sorted(targets)) for name, targets in index.items()}


def _read_source(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(
            f"[belief-map] Cannot read Ruby {label} from {path}: {error}",
            file=sys.stderr,
        )
        return ""


def _project_acronyms(
    root: Path,
    discovery_exclusions: DiscoveryExclusions,
) -> dict[str, str]:
    inflections_path = root / "config" / "initializers" / "inflections.rb"
    if (
        is_excluded_path(discovery_exclusions, str(inflections_path))
        or not inflections_path.is_file()
    ):
        return {}
    content = _read_source(inflections_path, "inflections")
    return {acronym.lower(): acronym for acronym in _ACRONYM_PATTERN.findall(content)}


def _custom_autoload_roots(
    root: Path,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[Path, ...]:
    config_paths = (
        root / "config" / "application.rb",
        root / "config" / "environment.rb",
    )
    roots: set[Path] = set()
    for config_path in config_paths:
        if (
            is_excluded_path(discovery_exclusions, str(config_path))
            or not config_path.is_file()
        ):
            continue
        content = _read_source(config_path, "autoload configuration")
        for match in _AUTOLOAD_JOIN_PATTERN.finditer(content):
            segments = _QUOTED_SEGMENT_PATTERN.findall(match.group(1))
            if not segments:
                continue
            candidate = root.joinpath(*segments).resolve()
            if (
                candidate.is_dir()
                and _is_within(candidate, root)
                and not is_excluded_path(discovery_exclusions, str(candidate))
            ):
                roots.add(candidate)
    return tuple(sorted(roots))


def _default_autoload_roots(
    root: Path,
    discovery_exclusions: DiscoveryExclusions,
) -> tuple[Path, ...]:
    roots: set[Path] = set(_custom_autoload_roots(root, discovery_exclusions))
    app_root = root / "app"
    if app_root.is_dir() and not is_excluded_path(discovery_exclusions, str(app_root)):
        try:
            app_children = tuple(app_root.iterdir())
        except OSError as error:
            print(
                f"[belief-map] Cannot inspect Rails app roots in {app_root}: {error}",
                file=sys.stderr,
            )
            app_children = ()
        for child in app_children:
            if not child.is_dir() or is_excluded_directory(
                discovery_exclusions,
                str(app_root),
                child.name,
            ):
                continue
            roots.add(child.resolve())
            concerns = child / "concerns"
            if concerns.is_dir() and not is_excluded_directory(
                discovery_exclusions,
                str(child),
                concerns.name,
            ):
                roots.add(concerns.resolve())
    lib_root = root / "lib"
    if lib_root.is_dir() and not is_excluded_directory(
        discovery_exclusions,
        str(root),
        lib_root.name,
    ):
        roots.add(lib_root.resolve())
    return tuple(sorted(roots, key=lambda path: (-len(path.parts), str(path))))


def _camelize(segment: str, acronyms: Mapping[str, str]) -> str:
    parts = [part for part in segment.split("_") if part]
    return "".join(
        acronyms.get(part.lower(), part[:1].upper() + part[1:]) for part in parts
    )


def _expected_constant(
    path: Path,
    autoload_roots: tuple[Path, ...],
    acronyms: Mapping[str, str],
) -> str:
    for autoload_root in autoload_roots:
        try:
            relative = path.relative_to(autoload_root)
        except ValueError:
            continue
        without_suffix = relative
        for suffix in _SOURCE_SUFFIXES:
            if relative.name.endswith(suffix):
                without_suffix = relative.with_name(relative.name[: -len(suffix)])
                break
        return "::".join(
            _camelize(part, acronyms) for part in without_suffix.parts if part
        )
    return ""


def _source_namespaces(constants: tuple[str, ...], expected: str) -> tuple[str, ...]:
    candidates: set[str] = set()
    for constant in (*constants, expected):
        current = constant
        while current:
            candidates.add(current)
            current, separator, _ = current.rpartition("::")
            if not separator:
                break
    return tuple(
        sorted(candidates, key=lambda name: (-name.count("::"), -len(name), name))
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_variants(base: Path) -> tuple[Path, ...]:
    if base.suffix in _SOURCE_SUFFIXES:
        return (base,)
    return (
        base,
        Path(f"{base}.rb"),
        Path(f"{base}.rake"),
        base / "init.rb",
    )


def _singularize(name: str) -> str:
    if name.endswith("ies") and len(name) > 3:
        return f"{name[:-3]}y"
    if name.endswith("sses"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _resolve_ancestor_names(
    bases: tuple[str, ...],
    namespaces: tuple[str, ...],
    constant_index: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    ancestors: list[str] = []
    for raw_base in bases:
        base = raw_base.removeprefix("::")
        candidates = [base]
        if not raw_base.startswith("::") and "::" not in base:
            candidates = [
                *(f"{namespace}::{base}" for namespace in namespaces),
                base,
            ]
        for candidate in candidates:
            if len(constant_index.get(candidate, ())) == 1:
                ancestors.append(candidate)
                break
    return tuple(dict.fromkeys(ancestors))


@dataclass(frozen=True)
class BoundRubyLanguage:
    root: Path
    path_to_id: Mapping[str, str]
    constant_to_ids: Mapping[str, tuple[str, ...]]
    source_namespaces: Mapping[str, tuple[str, ...]]
    source_ancestors: Mapping[str, tuple[str, ...]]
    autoload_roots: tuple[Path, ...]
    acronyms: Mapping[str, str]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundRubyLanguage:
        from .parser import ruby_declarations

        root_path = Path(root).resolve()
        normalized_paths = {
            str(Path(path).resolve()): node_id for path, node_id in path_to_id.items()
        }
        acronyms = _project_acronyms(root_path, discovery_exclusions)
        autoload_roots = _default_autoload_roots(
            root_path,
            discovery_exclusions,
        )
        constant_index: dict[str, set[str]] = {}
        namespaces: dict[str, tuple[str, ...]] = {}
        raw_bases: dict[str, tuple[str, ...]] = {}

        for raw_path, node_id in sorted(normalized_paths.items()):
            path = Path(raw_path)
            if path.suffix not in _SOURCE_SUFFIXES:
                continue
            content = _read_source(path, "declarations")
            declarations, declared_bases = ruby_declarations(content)
            expected = _expected_constant(path, autoload_roots, acronyms)
            for constant in declarations:
                constant_index.setdefault(constant, set()).add(node_id)
            if expected and (not declarations or expected in declarations):
                constant_index.setdefault(expected, set()).add(node_id)
            namespaces[raw_path] = _source_namespaces(declarations, expected)
            raw_bases[raw_path] = tuple(
                base for bases in declared_bases.values() for base in bases
            )

        frozen_constant_index = _freeze_index(constant_index)
        ancestors = {
            path: _resolve_ancestor_names(
                bases,
                namespaces.get(path, ()),
                frozen_constant_index,
            )
            for path, bases in raw_bases.items()
        }

        return cls(
            root=root_path,
            path_to_id=normalized_paths,
            constant_to_ids=frozen_constant_index,
            source_namespaces=namespaces,
            source_ancestors=ancestors,
            autoload_roots=autoload_roots,
            acronyms=acronyms,
        )

    def _resolve_path(self, base: Path) -> str | None:
        for candidate in _path_variants(base):
            resolved = candidate.resolve()
            if not _is_within(resolved, self.root):
                continue
            target = self.path_to_id.get(str(resolved))
            if target is not None:
                return target
        return None

    def _resolve_require_relative(self, name: str, source_path: str) -> str | None:
        return self._resolve_path(Path(source_path).resolve().parent / name)

    def _resolve_require(self, name: str) -> str | None:
        search_roots = (self.root, self.root / "lib", *self.autoload_roots)
        for search_root in search_roots:
            target = self._resolve_path(search_root / name)
            if target is not None:
                return target
        return None

    def _resolve_constant(self, name: str, source_path: str) -> str | None:
        normalized = name.removeprefix("::")
        if not normalized:
            return None
        source_key = str(Path(source_path).resolve())
        candidates: list[str] = []
        if name.startswith("::") or "::" in normalized:
            candidates.append(normalized)
        if not name.startswith("::"):
            candidates.extend(
                f"{namespace}::{normalized}"
                for namespace in self.source_namespaces.get(source_key, ())
                if namespace != normalized
                and not normalized.startswith(f"{namespace}::")
            )
            candidates.extend(
                f"{ancestor}::{normalized}"
                for ancestor in self.source_ancestors.get(source_key, ())
            )
        candidates.append(normalized)

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            targets = self.constant_to_ids.get(candidate, ())
            if len(targets) == 1:
                return targets[0]
            if len(targets) > 1:
                return None
        return None

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        if import_name.startswith("ruby-require-relative:"):
            return self._resolve_require_relative(
                import_name.removeprefix("ruby-require-relative:"),
                source_path,
            )
        if import_name.startswith("ruby-require:"):
            return self._resolve_require(import_name.removeprefix("ruby-require:"))
        if import_name.startswith("ruby-association-one:"):
            constant = _camelize(
                import_name.removeprefix("ruby-association-one:"),
                self.acronyms,
            )
            return self._resolve_constant(constant, source_path)
        if import_name.startswith("ruby-association-many:"):
            association = _singularize(
                import_name.removeprefix("ruby-association-many:")
            )
            return self._resolve_constant(
                _camelize(association, self.acronyms),
                source_path,
            )
        if import_name.startswith("ruby-local-method:"):
            return self.path_to_id.get(str(Path(source_path).resolve()))
        return self._resolve_constant(
            import_name.removeprefix("ruby-constant:"),
            source_path,
        )

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
