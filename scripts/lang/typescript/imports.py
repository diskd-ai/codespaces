from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from ..interface import FileResult


@dataclass(frozen=True)
class TsPathAlias:
    """One validated TypeScript path mapping owned by a tsconfig."""

    pattern: str
    target_patterns: tuple[str, ...]


@dataclass(frozen=True)
class TsPathAliasContext:
    """Path mappings whose scope starts at a tsconfig directory."""

    config_directory: str
    aliases: tuple[TsPathAlias, ...]


@dataclass(frozen=True)
class TsPackage:
    """A local TypeScript package identified by its package.json contract."""

    name: str
    directory: str


def _load_ts_path_aliases(root: str) -> tuple[TsPathAliasContext, ...]:
    """Load validated path mappings without merging independent projects."""
    import json as _json

    contexts: list[TsPathAliasContext] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in ("node_modules", ".git", "dist", "build", ".next", ".cache")
        ]
        for fname in filenames:
            if fname != "tsconfig.json":
                continue

            tsconfig_path = os.path.join(dirpath, fname)
            try:
                with open(tsconfig_path, "r", encoding="utf-8") as file:
                    raw = file.read()
                raw = re.sub(r"//.*$", "", raw, flags=re.MULTILINE)
                raw = re.sub(r",(\s*[}\]])", r"\1", raw)
                data = _json.loads(raw)
            except (OSError, _json.JSONDecodeError) as error:
                print(
                    f"[belief-map] Cannot read TS aliases from {tsconfig_path}: {error}",
                    file=sys.stderr,
                )
                continue

            if not isinstance(data, dict):
                continue
            compiler_options = data.get("compilerOptions")
            if not isinstance(compiler_options, dict):
                continue
            paths = compiler_options.get("paths")
            if not isinstance(paths, dict):
                continue
            configured_base = compiler_options.get("baseUrl", ".")
            if not isinstance(configured_base, str):
                continue
            base_directory = os.path.normpath(os.path.join(dirpath, configured_base))

            aliases: list[TsPathAlias] = []
            for alias_pattern, targets in paths.items():
                if not isinstance(alias_pattern, str):
                    continue
                if not isinstance(targets, list):
                    continue
                target_patterns = tuple(
                    os.path.normpath(os.path.join(base_directory, target))
                    for target in targets
                    if isinstance(target, str)
                )
                if target_patterns:
                    aliases.append(TsPathAlias(alias_pattern, target_patterns))

            if aliases:
                contexts.append(TsPathAliasContext(
                    config_directory=os.path.normpath(dirpath),
                    aliases=tuple(sorted(aliases, key=lambda alias: len(alias.pattern), reverse=True)),
                ))

    return tuple(sorted(
        contexts,
        key=lambda context: len(Path(context.config_directory).parts),
        reverse=True,
    ))


def _load_ts_packages(
    root: str,
    skip_directories: frozenset[str],
) -> tuple[TsPackage, ...]:
    """Load local package identities used by workspace self-imports."""
    packages: list[TsPackage] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            directory for directory in dirnames if directory not in skip_directories
        ]
        if "package.json" not in filenames:
            continue

        package_path = os.path.join(dirpath, "package.json")
        try:
            with open(package_path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            print(
                f"[belief-map] Cannot read local package identity from {package_path}: {error}",
                file=sys.stderr,
            )
            continue

        if not isinstance(data, dict):
            continue
        name = data.get("name")
        if isinstance(name, str) and name:
            packages.append(TsPackage(name, os.path.normpath(dirpath)))

    return tuple(sorted(packages, key=lambda package: len(package.name), reverse=True))


def _typescript_resolution_bases(resolved: str) -> tuple[str, ...]:
    """Map emitted JavaScript specifiers back to their TypeScript source base."""
    for suffix in (".jsx", ".mjs", ".cjs", ".js"):
        if resolved.endswith(suffix):
            return (resolved[:-len(suffix)], resolved)
    return (resolved,)


def _resolve_typescript_path(
    resolved: str,
    root: str,
    path_to_id: Mapping[str, str],
) -> Optional[str]:
    for base in _typescript_resolution_bases(resolved):
        for ext in ("", ".ts", ".tsx", "/index.ts", "/index.tsx"):
            candidate = base + ext
            if candidate in path_to_id:
                return path_to_id[candidate]
            try:
                relative_candidate = os.path.relpath(candidate, root)
            except ValueError as error:
                print(
                    f"[belief-map] Cannot relativize {candidate} to {root}: {error}",
                    file=sys.stderr,
                )
                relative_candidate = candidate
            if relative_candidate in path_to_id:
                return path_to_id[relative_candidate]
    return None


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError as error:
        print(
            f"[belief-map] Cannot compare path scopes {path} and {directory}: {error}",
            file=sys.stderr,
        )
        return False


def _match_ts_alias(pattern: str, imp: str) -> Optional[str]:
    if "*" not in pattern:
        return "" if imp == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if not imp.startswith(prefix) or not imp.endswith(suffix):
        return None
    remainder_end = len(imp) - len(suffix) if suffix else len(imp)
    return imp[len(prefix):remainder_end]


def _resolve_ts_path_alias(
    imp: str,
    source_path: str,
    root: str,
    path_to_id: Mapping[str, str],
    alias_contexts: tuple[TsPathAliasContext, ...],
) -> Optional[str]:
    """Resolve an alias only through tsconfigs that own the source file."""
    if not alias_contexts:
        return None

    absolute_source = os.path.normpath(
        source_path if os.path.isabs(source_path) else os.path.join(root, source_path)
    )
    for context in alias_contexts:
        if not _is_within(absolute_source, context.config_directory):
            continue
        for alias in context.aliases:
            remainder = _match_ts_alias(alias.pattern, imp)
            if remainder is None:
                continue
            for target_pattern in alias.target_patterns:
                resolved = target_pattern.replace("*", remainder, 1)
                target_id = _resolve_typescript_path(resolved, root, path_to_id)
                if target_id:
                    return target_id

    return None


def _resolve_ts_package(
    imp: str,
    source_path: str,
    root: str,
    path_to_id: Mapping[str, str],
    packages: tuple[TsPackage, ...],
) -> Optional[str]:
    """Resolve package self-imports without shadowing installed dependencies."""
    absolute_source = os.path.normpath(
        source_path if os.path.isabs(source_path) else os.path.join(root, source_path)
    )
    owners = tuple(
        package
        for package in packages
        if _is_within(absolute_source, package.directory)
    )
    if not owners:
        return None

    owner = max(owners, key=lambda package: len(Path(package.directory).parts))
    if imp != owner.name:
        return None

    for candidate in (
        os.path.join(owner.directory, "src", "index"),
        os.path.join(owner.directory, "index"),
    ):
        target_id = _resolve_typescript_path(candidate, root, path_to_id)
        if target_id:
            return target_id
    return None


@dataclass(frozen=True)
class BoundTypeScriptLanguage:
    root: str
    path_to_id: Mapping[str, str]
    alias_contexts: tuple[TsPathAliasContext, ...]
    packages: tuple[TsPackage, ...]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundTypeScriptLanguage:
        alias_contexts = _load_ts_path_aliases(root)
        if alias_contexts:
            print(
                f"[belief-map] Loaded {len(alias_contexts)} TS alias contexts",
                file=sys.stderr,
            )
        packages = _load_ts_packages(root, skip_directories)
        if packages:
            print(
                f"[belief-map] Loaded {len(packages)} local TS packages",
                file=sys.stderr,
            )
        return cls(root, path_to_id, alias_contexts, packages)

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        if not import_name.startswith(".") and not import_name.startswith("/"):
            resolved_via_alias = _resolve_ts_path_alias(
                import_name,
                source_path,
                self.root,
                self.path_to_id,
                self.alias_contexts,
            )
            if resolved_via_alias:
                return resolved_via_alias
            return _resolve_ts_package(
                import_name,
                source_path,
                self.root,
                self.path_to_id,
                self.packages,
            )

        source_directory = os.path.dirname(source_path)
        resolved = os.path.normpath(os.path.join(source_directory, import_name))
        return _resolve_typescript_path(resolved, self.root, self.path_to_id)

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
