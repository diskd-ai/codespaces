from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CAMEL_CASE_RE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
PASCAL_CASE_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def file_hash(path: str) -> str:
    """Hash complete contents so restored mtimes cannot hide source changes."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source_file:
            while chunk := source_file.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        print(
            f"[belief-map] ERROR: could not hash {path}: {error}",
            file=sys.stderr,
        )
        return ""


def detect_naming(filename: str) -> str:
    stem = Path(filename).stem
    for suffix in (".test", ".spec", ".e2e", ".stories", ".d"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if SNAKE_CASE_RE.match(stem):
        return "snake_case"
    if KEBAB_CASE_RE.match(stem):
        return "kebab-case"
    if PASCAL_CASE_RE.match(stem):
        return "PascalCase"
    if CAMEL_CASE_RE.match(stem):
        return "camelCase"
    return "mixed"
