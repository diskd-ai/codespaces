from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..interface import FileResult


@dataclass(frozen=True)
class RustPackage:
    """One local Cargo package and its Rust crate identifier."""

    crate_name: str
    directory: str


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError as error:
        print(
            f"[belief-map] Cannot compare Rust path scopes {path} and {directory}: {error}",
            file=sys.stderr,
        )
        return False


def _load_rust_packages(
    root: str,
    skip_directories: frozenset[str],
) -> tuple[RustPackage, ...]:
    packages: list[RustPackage] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            directory for directory in dirnames if directory not in skip_directories
        ]
        if "Cargo.toml" not in filenames:
            continue

        manifest_path = os.path.join(dirpath, "Cargo.toml")
        try:
            with open(manifest_path, "rb") as manifest_file:
                manifest = tomllib.load(manifest_file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            print(
                f"[belief-map] Cannot read Rust package identity from {manifest_path}: {error}",
                file=sys.stderr,
            )
            continue

        package = manifest.get("package")
        if not isinstance(package, dict):
            continue
        package_name = package.get("name")
        if not isinstance(package_name, str) or not package_name:
            continue
        packages.append(
            RustPackage(
                crate_name=package_name.replace("-", "_"),
                directory=os.path.normpath(dirpath),
            )
        )

    return tuple(
        sorted(
            packages,
            key=lambda package: len(Path(package.directory).parts),
            reverse=True,
        )
    )


def _candidate_id(
    candidate: str,
    path_to_id: Mapping[str, str],
) -> str | None:
    return path_to_id.get(os.path.normpath(candidate))


def _resolve_from_directory(
    directory: str,
    segments: tuple[str, ...],
    path_to_id: Mapping[str, str],
    fallback_files: tuple[str, ...] = (),
) -> str | None:
    for segment_count in range(len(segments), 0, -1):
        module_path = os.path.join(directory, *segments[:segment_count])
        for candidate in (f"{module_path}.rs", os.path.join(module_path, "mod.rs")):
            target_id = _candidate_id(candidate, path_to_id)
            if target_id is not None:
                return target_id

    for fallback_file in fallback_files:
        target_id = _candidate_id(fallback_file, path_to_id)
        if target_id is not None:
            return target_id
    return None


def _package_entrypoints(package: RustPackage) -> tuple[str, ...]:
    source_directory = os.path.join(package.directory, "src")
    return (
        os.path.join(source_directory, "lib.rs"),
        os.path.join(source_directory, "main.rs"),
    )


def _owning_package(
    source_path: str,
    packages: tuple[RustPackage, ...],
) -> RustPackage | None:
    absolute_source = os.path.normpath(source_path)
    return next(
        (
            package
            for package in packages
            if _is_within(absolute_source, package.directory)
        ),
        None,
    )


def _logical_module_directory(source_path: str) -> str:
    source_path = os.path.normpath(source_path)
    filename = os.path.basename(source_path)
    if filename in ("lib.rs", "main.rs", "mod.rs"):
        return os.path.dirname(source_path)
    return os.path.splitext(source_path)[0]


def _crate_source_directory(
    source_path: str,
    owner: RustPackage | None,
) -> str:
    if owner is not None:
        return os.path.join(owner.directory, "src")

    source = Path(source_path)
    for parent in source.parents:
        if parent.name == "src":
            return str(parent)
    return os.path.dirname(source_path)


@dataclass(frozen=True)
class BoundRustLanguage:
    root: str
    path_to_id: Mapping[str, str]
    packages: tuple[RustPackage, ...]

    @classmethod
    def create(
        cls,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundRustLanguage:
        packages = _load_rust_packages(root, skip_directories)
        if packages:
            print(
                f"[belief-map] Loaded {len(packages)} local Rust packages",
                file=sys.stderr,
            )
        return cls(os.path.normpath(root), path_to_id, packages)

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        segments = tuple(segment for segment in import_name.split("::") if segment)
        if not segments:
            return None

        absolute_source = os.path.normpath(source_path)
        owner = _owning_package(absolute_source, self.packages)
        first = segments[0]

        if first == "crate":
            source_directory = _crate_source_directory(absolute_source, owner)
            fallbacks = (
                _package_entrypoints(owner) if owner is not None else (absolute_source,)
            )
            return _resolve_from_directory(
                source_directory,
                segments[1:],
                self.path_to_id,
                fallbacks,
            )

        if first in ("self", "super"):
            module_directory = _logical_module_directory(absolute_source)
            index = 0
            while index < len(segments) and segments[index] in ("self", "super"):
                if segments[index] == "super":
                    module_directory = os.path.dirname(module_directory)
                index += 1
            return _resolve_from_directory(
                module_directory,
                segments[index:],
                self.path_to_id,
                (absolute_source,),
            )

        package = next(
            (package for package in self.packages if package.crate_name == first),
            None,
        )
        if package is None:
            return None
        return _resolve_from_directory(
            os.path.join(package.directory, "src"),
            segments[1:],
            self.path_to_id,
            _package_entrypoints(package),
        )

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        return ()
