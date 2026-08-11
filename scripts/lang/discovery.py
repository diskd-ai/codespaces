from __future__ import annotations

import os
from collections.abc import Iterable

from .interface import DiscoveryExclusions


def normalize_discovery_path(path: str) -> str:
    """Normalize an existing discovery path consistently across adapters."""
    return os.path.normcase(os.path.realpath(path))


def is_excluded_path(
    exclusions: DiscoveryExclusions,
    path: str,
) -> bool:
    """Return whether a file or directory has an exact configured exclusion."""
    return normalize_discovery_path(path) in exclusions.paths


def is_excluded_directory(
    exclusions: DiscoveryExclusions,
    parent: str,
    name: str,
) -> bool:
    """Apply basename and repository-ignore policy before descending."""
    return name in exclusions.directory_names or is_excluded_path(
        exclusions, os.path.join(parent, name)
    )


def retained_directory_names(
    exclusions: DiscoveryExclusions,
    parent: str,
    names: Iterable[str],
) -> list[str]:
    """Return walkable child directory names in their original order."""
    return [
        name for name in names if not is_excluded_directory(exclusions, parent, name)
    ]
