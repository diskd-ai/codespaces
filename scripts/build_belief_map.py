#!/usr/bin/env python3
"""
Belief Map Builder -- Architecture Graph Generator.

Generates ``belief_map.sexp``, a flat S-expression graph of all modules,
entities, and relationships in a codebase. Each line is a self-contained
fact, queryable with ``rg`` or ``scripts/belief_search.py``.

Based on the Theory of Code Space (ToCS) methodology for reducing the
"Active-Passive Gap" in AI code agents.

Parsers
-------
- **TypeScript/TSX**: tree-sitter (``tree-sitter-typescript``). Handles
  decorators, heritage, all methods, object literals, arrow functions.
- **Python**: stdlib ``ast``. Full class/function/import extraction.
- **Rust**: tree-sitter (``tree-sitter-rust``). Handles Cargo crates,
  declarations, implementations, methods, and ``use``/``mod`` dependencies.
- **C#**: tree-sitter (``tree-sitter-c-sharp``). Handles namespaces, types,
  methods, attributes, inheritance, and local type dependencies.
- **Java**: tree-sitter (``tree-sitter-java``). Handles packages, types,
  methods, annotations, inheritance, and local type dependencies.
- **Go**: tree-sitter (``tree-sitter-go``). Handles modules, packages, types,
  functions, receiver methods, and local package dependencies.

Modes
-----
**Default** (fast, ~5-8s for full workspace):
    Parses all .py/.ts/.tsx/.rs/.cs/.java/.go files and builds
    import/reference/data-flow edges.

**LSP-enhanced** (``--lsp``, ~1-5min):
    After default pass, starts ``typescript-language-server`` and
    ``pyright-langserver``, ``rust-analyzer``, ``csharp-ls``, ``jdtls``, or
    ``gopls`` per project to query call hierarchy and references. Adds precise
    ``calls`` edges and ``:lsp`` annotations.

Edge Types
----------
- ``imports`` -- module imports module. ``:via-base`` if target exports
  abstract types.
- ``calls-api`` -- implements/extends abstract type. ``:via-ifc``.
- ``data-flow`` -- data flows to validation module. ``:validated``.
- ``refs`` -- entity-level cross-file reference (``Module::Entity``).
- ``calls`` (LSP only) -- function calls function with call-site lines.

Output Format
-------------
Flat S-expression facts (``belief_map.sexp``)::

    (node module/id ts "purpose description")
    (cls module/id ClassName 49 (:deco Injectable) (:methods create update delete))
    (fn module/id functionName 126)
    (imports module/a module/b :via-base)
    (refs module/a module/b::EntityName)
    (calls module/a::fnA module/b::fnB :lines 52 67 :lsp)

Requirements
------------
::

    python3 -m pip install -r requirements.txt

Optional for ``--lsp``: ``typescript-language-server``, ``pyright-langserver``,
``rust-analyzer``, ``csharp-ls``, ``jdtls``, or ``gopls``.

Usage
-----
::

    python3 /absolute/path/to/scripts/build_belief_map.py \
        --root /absolute/path/to/project
    python3 /absolute/path/to/scripts/build_belief_map.py \
        --root /absolute/path/to/project --full
    python3 /absolute/path/to/scripts/build_belief_map.py \
        --root /absolute/path/to/project --lsp
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Optional, Sequence, TypeVar
from urllib.parse import quote, unquote

try:
    if __package__:
        from .lang import (
            LANGUAGES,
            BoundLanguage,
            FileResult,
            language_for_file,
            language_for_name,
            language_for_result,
        )
        from .lang.purpose import infer_purpose
        from .lang.interface import EntityPayload
        from .lang.source import file_hash
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from lang import (
            LANGUAGES,
            BoundLanguage,
            FileResult,
            language_for_file,
            language_for_name,
            language_for_result,
        )
        from lang.purpose import infer_purpose
        from lang.interface import EntityPayload
        from lang.source import file_hash
except ModuleNotFoundError as dependency_error:
    print(
        f"Error: missing Python dependency {dependency_error.name}. "
        "Install pinned dependencies with "
        "`python3 -m pip install -r requirements.txt`.",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist",
    ".next", ".turbo", "bin", "build", "obj", "coverage", ".tox", "site-packages",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "vendor",
    ".worktrees", ".ralphy-worktrees", ".ralphy-sandboxes",
}

CACHE_FILE = ".belief_map_cache.json"
OUTPUT_FILE = ".belief_map.sexp"
CACHE_SCHEMA_VERSION = 1
MAP_SCHEMA_VERSION = 1
BUILDER_CONFIG_VERSION = "2026-08-08"

# Default timeout for LSP requests (seconds)
LSP_REQUEST_TIMEOUT = 15.0
# Timeout for LSP server initialization (seconds)
LSP_INIT_TIMEOUT = 60.0

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


Result = Ok[T] | Err[E]


@dataclass(frozen=True)
class NodeIdCollision:
    node_id: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class GraphBuildError:
    collisions: tuple[NodeIdCollision, ...]


@dataclass(frozen=True)
class GraphBuildOutput:
    nodes: list[dict]
    edges: list[dict]


@dataclass
class LspEdge:
    """An edge discovered by LSP analysis."""
    source: str       # "node_id::entity_name" or "node_id"
    target: str       # "node_id::entity_name" or "node_id"
    edge_type: str    # "CALLS", "REFERENCES"
    metadata: dict = field(default_factory=dict)


@dataclass
class ProjectGroup:
    """A group of files belonging to the same LSP project."""
    root: str              # absolute path to project root
    language: str          # language adapter name
    config_file: str       # language-owned project configuration
    files: list[FileResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def path_to_uri(path: str) -> str:
    """Convert an absolute file path to a file:// URI."""
    abs_path = os.path.abspath(path)
    return "file://" + quote(abs_path, safe="/:")


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an absolute file path."""
    if uri.startswith("file://"):
        return unquote(uri[7:])
    return uri


def find_entity_column(content: str, entity_name: str, line_1based: int) -> int:
    """Find the 0-based column of *entity_name* on the given 1-based line."""
    lines = content.split("\n")
    if line_1based < 1 or line_1based > len(lines):
        return 0
    line_text = lines[line_1based - 1]
    idx = line_text.find(entity_name)
    return max(idx, 0)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def parse_file(args: tuple[str, str, str]) -> FileResult:
    path, language_name, repo = args
    mtime = os.path.getmtime(path)
    with open(path, "r", encoding="utf-8", errors="replace") as source_file:
        content = source_file.read()
    return language_for_name(language_name).parse(path, content, repo, mtime)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def detect_source_language(filename: str) -> Optional[str]:
    """Return the supported parser language for a source filename."""
    language = language_for_file(filename)
    return language.name if language is not None else None


def discover_files(root: str) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    root_path = Path(root).resolve()

    def _walk(dir_path: Path, repo: str):
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if entry.name in SKIP_DIRS:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        child_repo = repo
                        if entry.name != ".git" and (dir_path / entry.name / ".git").exists():
                            child_repo = entry.name
                        _walk(dir_path / entry.name, child_repo)
                    elif entry.is_file(follow_symlinks=False):
                        language = language_for_file(entry.name)
                        if language is not None:
                            results.append((entry.path, language.name, repo))
        except PermissionError as exc:
            print(f"[belief-map] WARNING: could not scan {dir_path}: {exc}", file=sys.stderr)

    _walk(root_path, root_path.name)
    return results


# ---------------------------------------------------------------------------
# Incremental cache
# ---------------------------------------------------------------------------

def _builder_fingerprint() -> str:
    """Fingerprint parser code, dependencies, and behavior-affecting config."""
    dependency_packages = sorted({
        package
        for language in LANGUAGES
        for package in language.dependency_packages
    })
    dependency_versions = "|".join(
        f"{name}={importlib.metadata.version(name)}"
        for name in dependency_packages
    )
    config = "|".join(
        (
            BUILDER_CONFIG_VERSION,
            dependency_versions,
            ",".join(sorted(SKIP_DIRS)),
        )
    )
    digest = hashlib.sha256()
    digest.update(config.encode("utf-8"))
    source_paths = (
        Path(__file__),
        *sorted((Path(__file__).parent / "lang").rglob("*.py")),
    )
    for source_path in source_paths:
        digest.update(str(source_path.relative_to(Path(__file__).parent)).encode())
        with open(source_path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: str, content: str) -> None:
    """Atomically replace a text file after durable same-directory staging."""
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary_path = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError:
        if os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError as cleanup_error:
                print(
                    f"[belief-map] ERROR: could not remove {temporary_path}: "
                    f"{cleanup_error}",
                    file=sys.stderr,
                )
        raise


def load_cache(root: str) -> dict:
    """Load compatible cache entries or rebuild with an explicit warning."""
    cache_path = os.path.join(root, CACHE_FILE)
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[belief-map] WARNING: could not load cache {cache_path}: {exc}; "
            "rebuilding",
            file=sys.stderr,
        )
        return {}

    if not isinstance(cache, dict):
        print(
            f"[belief-map] WARNING: incompatible cache {cache_path}: "
            "top-level value must be an object; rebuilding",
            file=sys.stderr,
        )
        return {}
    expected_fingerprint = _builder_fingerprint()
    if (
        cache.get("schema_version") != CACHE_SCHEMA_VERSION
        or cache.get("builder_fingerprint") != expected_fingerprint
        or not isinstance(cache.get("entries"), dict)
    ):
        print(
            f"[belief-map] WARNING: incompatible cache {cache_path}: "
            "schema or builder fingerprint changed; rebuilding",
            file=sys.stderr,
        )
        return {}
    entries = cache["entries"]
    if isinstance(entries, dict):
        return entries
    return {}


def save_cache(root: str, entries: dict) -> None:
    cache_path = os.path.join(root, CACHE_FILE)
    cache = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "builder_fingerprint": _builder_fingerprint(),
        "entries": entries,
    }
    serialized = json.dumps(cache, sort_keys=True, separators=(",", ":")) + "\n"
    _atomic_write_text(cache_path, serialized)


def decode_cache_entry(entry: object) -> Result[FileResult, str]:
    """Validate a cached parse result at the persistence boundary."""
    if not isinstance(entry, dict):
        return Err("cache entry must be an object")
    raw_result = entry.get("result")
    if not isinstance(raw_result, dict):
        return Err("cache entry result must be an object")

    string_fields = ("path", "language", "repo", "content_hash", "purpose", "naming_convention")
    for field_name in string_fields:
        if not isinstance(raw_result.get(field_name), str):
            return Err(f"cache result field {field_name} must be a string")
    if not isinstance(raw_result.get("mtime"), (int, float)):
        return Err("cache result field mtime must be numeric")
    if not isinstance(raw_result.get("has_validation"), bool):
        return Err("cache result field has_validation must be boolean")
    list_fields = (
        "imports",
        "exports_abstract",
        "implements",
        "extends",
        "entities",
        "imported_names",
        "exported_names",
    )
    for field_name in list_fields:
        if not isinstance(raw_result.get(field_name), list):
            return Err(f"cache result field {field_name} must be a list")

    try:
        return Ok(FileResult(**raw_result))
    except TypeError as error:
        return Err(f"cache result shape is invalid: {error}")


def is_cache_entry_current(path: str, entry: object) -> bool:
    """Return whether a cache entry matches the file's complete content hash."""
    if not isinstance(entry, dict):
        return False
    result = entry.get("result")
    if not isinstance(result, dict):
        return False
    cached_hash = result.get("content_hash")
    if not isinstance(cached_hash, str) or not cached_hash:
        return False
    current_hash = file_hash(path)
    return bool(current_hash) and current_hash == cached_hash


# ---------------------------------------------------------------------------
# Import resolution is owned by the bound language adapters.


def make_node_id(path: str, root: str) -> str:
    relative_path = os.path.relpath(path, root).replace(os.sep, "/")
    language = language_for_file(os.path.basename(path))
    if language is None:
        raise ValueError(f"Unsupported source path: {relative_path}")
    return language.normalize_module_id(relative_path)


# Graph building
# ---------------------------------------------------------------------------

def _build_node_indexes(
    results: list[FileResult],
    root: str,
) -> Result[tuple[dict[str, str], dict[str, FileResult]], GraphBuildError]:
    """Build collision-free module indexes before any graph facts are emitted."""
    paths_by_id: dict[str, list[str]] = {}
    for result in results:
        node_id = make_node_id(result.path, root)
        relative_path = os.path.relpath(result.path, root).replace(os.sep, "/")
        paths_by_id.setdefault(node_id, []).append(relative_path)

    collisions = tuple(
        NodeIdCollision(node_id, tuple(sorted(paths)))
        for node_id, paths in sorted(paths_by_id.items())
        if len(paths) > 1
    )
    if collisions:
        return Err(GraphBuildError(collisions))

    path_to_id: dict[str, str] = {}
    id_to_result: dict[str, FileResult] = {}
    for result in sorted(results, key=lambda item: make_node_id(item.path, root)):
        node_id = make_node_id(result.path, root)
        path_to_id[result.path] = node_id
        id_to_result[node_id] = result
    return Ok((path_to_id, id_to_result))


def _imported_name_parts(raw: object) -> Optional[tuple[str, str]]:
    """Read the local name and module from a validated import descriptor."""
    if not isinstance(raw, dict):
        return None
    local_name = raw.get("local")
    module = raw.get("module")
    if not isinstance(local_name, str) or not isinstance(module, str):
        return None
    return local_name, module


def _resolve_provider(
    symbol: str,
    source: FileResult,
    bound_language: BoundLanguage,
    provider_candidates: dict[str, set[str]],
) -> Optional[str]:
    """Resolve a provider through an explicit import, otherwise fail closed."""
    for imported_name in source.imported_names:
        imported_parts = _imported_name_parts(imported_name)
        if imported_parts is None:
            continue
        local_name, module = imported_parts
        if local_name != symbol:
            continue
        return bound_language.resolve_import(module, source.path)

    candidates = provider_candidates.get(symbol, set())
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def build_graph(
    results: list[FileResult], root: str,
) -> Result[GraphBuildOutput, GraphBuildError]:
    index_result = _build_node_indexes(results, root)
    if isinstance(index_result, Err):
        return index_result
    path_to_id, id_to_result = index_result.value

    result_languages = {result.language for result in results}
    bound_languages = {
        language.name: language.bind(root, path_to_id, frozenset(SKIP_DIRS))
        for language in LANGUAGES
        if result_languages.intersection(language.result_languages)
    }

    # Map every abstract/interface name to every provider. Resolution prefers
    # the consumer's explicit import and otherwise accepts only one candidate.
    provider_candidates: dict[str, set[str]] = {}
    for nid, r in id_to_result.items():
        for ab in r.exports_abstract:
            provider_candidates.setdefault(ab, set()).add(nid)

    for nid, r in id_to_result.items():
        for ent in r.entities:
            if not isinstance(ent, dict):
                continue
            ent_kind = ent.get("kind")
            ent_name = ent.get("name")
            if ent_kind == "interface" and isinstance(ent_name, str) and ent_name:
                provider_candidates.setdefault(ent_name, set()).add(nid)

    entity_candidates: dict[str, set[str]] = {}
    for nid, r in id_to_result.items():
        for name in r.exported_names:
            entity_candidates.setdefault(name, set()).add(nid)

    all_bases: set[str] = set()
    for r in id_to_result.values():
        for base in r.implements + r.extends:
            all_bases.add(base)
    for base_name in all_bases:
        provider_candidates.setdefault(base_name, set()).update(
            entity_candidates.get(base_name, set())
        )

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[tuple] = set()

    for node_id, r in id_to_result.items():
        language = language_for_result(r.language)
        bound_language = bound_languages[language.name]
        rel_path = os.path.relpath(r.path, root)

        # Gap 3 fix: enrich purpose with entity-name heuristics
        purpose = r.purpose
        if r.entities and purpose in ("general module", "NestJS wiring module", "NestJS injectable"):
            enhanced = infer_purpose(r.path, "", r.language, r.entities)
            if enhanced not in ("general module", "NestJS wiring module", "NestJS injectable"):
                purpose = enhanced

        # -- build node with entities --
        node: dict = {
            "id": node_id,
            "language": r.language,
            "path": rel_path,
            "repo": r.repo,
            "invariant": {
                "naming": r.naming_convention,
                "package": r.repo,
            },
            "purpose": purpose,
        }
        if r.entities:
            node["entities"] = r.entities
        nodes.append(node)

        # Some language forms carry additional local import targets.
        extra_submod_ids = bound_language.resolve_additional_imports(r)
        for target_id in extra_submod_ids:
            if target_id != node_id:
                edge_key = (node_id, target_id, "IMPORTS")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    target_r = id_to_result.get(target_id)
                    via_base = bool(target_r and target_r.exports_abstract)
                    edges.append({
                        "source": node_id,
                        "target": target_id,
                        "type": "IMPORTS",
                        "via_base": via_base,
                    })

        # -- module-level IMPORTS edges --
        for imp in r.imports:
            target_id = bound_language.resolve_import(imp, r.path)
            if target_id and target_id != node_id:
                edge_key = (node_id, target_id, "IMPORTS")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    target_r = id_to_result.get(target_id)
                    via_base = bool(target_r and target_r.exports_abstract)
                    edges.append({
                        "source": node_id,
                        "target": target_id,
                        "type": "IMPORTS",
                        "via_base": via_base,
                    })

        # -- CALLS_API edges (implements/extends + entity :bases) --
        # Source 1: FileResult.implements + extends (from heritage clauses)
        calls_api_targets: set[str] = set()
        for iface in r.implements + r.extends:
            provider_id = _resolve_provider(
                iface,
                r,
                bound_language,
                provider_candidates,
            )
            if provider_id and provider_id != node_id:
                calls_api_targets.add(provider_id)

        # Source 2: Entity :bases cross-reference (catches bases not in
        # heritage clauses, and bases defined in other modules)
        for ent in r.entities:
            for base_name in ent.get("bases", []):
                provider_id = _resolve_provider(
                    base_name,
                    r,
                    bound_language,
                    provider_candidates,
                )
                if provider_id and provider_id != node_id:
                    calls_api_targets.add(provider_id)

        for provider_id in calls_api_targets:
            edge_key = (node_id, provider_id, "CALLS_API")
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "source": node_id,
                    "target": provider_id,
                    "type": "CALLS_API",
                    "via_interface": True,
                })

        # -- DATA_FLOWS_TO edges --
        for imp in r.imports:
            target_id = bound_language.resolve_import(imp, r.path)
            if target_id and target_id != node_id:
                target_r = id_to_result.get(target_id)
                if target_r and target_r.has_validation:
                    edge_key = (node_id, target_id, "DATA_FLOWS_TO")
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "source": node_id,
                            "target": target_id,
                            "type": "DATA_FLOWS_TO",
                            "validated": True,
                        })

        # -- entity-level REFERENCES edges --
        # For each imported name, link source_entity -> target_entity
        for imp_name in r.imported_names:
            target_id = bound_language.resolve_import(
                imp_name["module"],
                r.path,
            )
            if not target_id or target_id == node_id:
                continue
            target_r = id_to_result.get(target_id)
            if not target_r:
                continue

            orig = imp_name["original"]
            local = imp_name["local"]

            # Find matching entity in target
            target_entity = None
            for ent in target_r.entities:
                if ent["name"] == orig or (orig == "default" and ent["name"] == local):
                    target_entity = ent["name"]
                    break
            if not target_entity:
                # Still useful: the name is exported even if not a class/function entity
                if orig in target_r.exported_names:
                    target_entity = orig
                elif orig == "default" and target_r.exported_names:
                    target_entity = target_r.exported_names[0]
                else:
                    continue

            # Find which local entities reference this imported name
            referencing_entities = _find_local_references(r.entities, local)

            if referencing_entities:
                for ref_ent in referencing_entities:
                    edge_key = (
                        f"{node_id}::{ref_ent}",
                        f"{target_id}::{target_entity}",
                        "REFERENCES",
                    )
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "source": f"{node_id}::{ref_ent}",
                            "target": f"{target_id}::{target_entity}",
                            "type": "REFERENCES",
                        })
            else:
                # Module-level reference (not inside a specific entity)
                edge_key = (node_id, f"{target_id}::{target_entity}", "REFERENCES")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": node_id,
                        "target": f"{target_id}::{target_entity}",
                        "type": "REFERENCES",
                    })

    # -- Gap 2: BOUNDARY violation detection --
    # -- HTTP_CALLS edges (cross-service HTTP client detection) --
    # Detect modules with entities named *Client, *ClientService, *ClientApi,
    # *External, *Remote and infer the target service from the class name.
    _HTTP_CLIENT_SUFFIXES = ("Client", "ClientService", "ClientApi", "External", "Remote")
    _REPO_NAMES = {r.repo for r in results}
    for node_id, r in id_to_result.items():
        for ent in r.entities:
            ent_name = ent.get("name", "")
            if not any(ent_name.endswith(sfx) for sfx in _HTTP_CLIENT_SUFFIXES):
                continue
            # Extract target service name from class name:
            # AgentExternalClient -> agent, McpHubClientService -> mcp-hub
            # LLMClientRemote -> llm
            stem = ent_name
            for sfx in sorted(_HTTP_CLIENT_SUFFIXES, key=len, reverse=True):
                if stem.endswith(sfx):
                    stem = stem[: -len(sfx)]
                    break
            if not stem:
                continue
            # Convert PascalCase to kebab-case for repo matching
            kebab = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", stem).lower()
            # Try to match against known repo names
            target_repo = None
            for repo in _REPO_NAMES:
                if repo == r.repo:
                    continue  # skip self
                repo_lower = repo.lower()
                if kebab == repo_lower or kebab.startswith(repo_lower) or repo_lower.startswith(kebab):
                    target_repo = repo
                    break
            if target_repo:
                edge_key = (node_id, target_repo, "HTTP_CALLS")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": node_id,
                        "target": target_repo,
                        "type": "HTTP_CALLS",
                        "transport": "http",
                        "client_entity": ent_name,
                    })

    # Domain modules should not import directly from infrastructure without
    # going through an interface (via_base). Flag violations.
    for edge in edges:
        if edge["type"] != "IMPORTS":
            continue
        src = edge["source"]
        tgt = edge["target"]
        src_purpose = id_to_result.get(src, None)
        tgt_purpose = id_to_result.get(tgt, None)
        if not src_purpose or not tgt_purpose:
            continue
        src_p = (src_purpose.purpose or "").lower()
        tgt_p = (tgt_purpose.purpose or "").lower()
        # Domain importing infrastructure without going through base class
        if ("domain" in src_p and "infrastructure" in tgt_p and not edge.get("via_base")):
            edges.append({
                "source": src, "target": tgt,
                "type": "VIOLATION",
                "rule": "boundary",
                "detail": "domain imports infrastructure without abstract base",
            })
        # UI/component importing infrastructure directly
        if ("ui component" in src_p and "infrastructure" in tgt_p and not edge.get("via_base")):
            edges.append({
                "source": src, "target": tgt,
                "type": "VIOLATION",
                "rule": "boundary",
                "detail": "UI imports infrastructure without abstract base",
            })

    return Ok(GraphBuildOutput(nodes, edges))


def _find_local_references(
    entities: Sequence[EntityPayload],
    name: str,
) -> list[str]:
    """
    Heuristic: check which local entities might reference the imported name.
    If a class extends/implements the name, or has it in bases, it references it.
    """
    refs = []
    for ent in entities:
        # Class extends/implements
        if name in ent.get("bases", []):
            refs.append(ent["name"])
        # Decorator usage
        if name in ent.get("decorators", []):
            refs.append(ent["name"])
    return refs


# ---------------------------------------------------------------------------
# LSP Client -- JSON-RPC over stdio
# ---------------------------------------------------------------------------

class LspClient:
    """
    Minimal Language Server Protocol client using JSON-RPC over stdio.

    Starts an LSP server as a subprocess, communicates via Content-Length
    framed JSON-RPC messages, and dispatches responses to waiting callers
    via a background reader thread.

    Supports:
    - initialize / initialized / shutdown / exit lifecycle
    - textDocument/didOpen, textDocument/didClose
    - textDocument/prepareCallHierarchy
    - callHierarchy/outgoingCalls, callHierarchy/incomingCalls
    - textDocument/references
    """

    def __init__(self, cmd: list[str], cwd: str):
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        self._stderr = self._proc.stderr
        self._msg_id = 0
        self._lock = threading.Lock()
        self._events: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._errors: dict[int, Any] = {}
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

    # -- Transport layer --

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def _send(self, msg: dict) -> None:
        """Send a JSON-RPC message with Content-Length header."""
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._stdin.write(header + body)
            self._stdin.flush()
        except (BrokenPipeError, OSError):
            self._alive = False

    def _read_loop(self) -> None:
        """Background thread: read Content-Length framed messages from stdout."""
        # Use os.read() for non-blocking reads. Python's BufferedReader.read(n)
        # blocks until exactly n bytes are available, which deadlocks on pipes.
        fd = self._stdout.fileno()
        buf = b""
        while self._alive:
            try:
                # Read until we have the full header separator
                while b"\r\n\r\n" not in buf:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        self._alive = False
                        self._wake_all()
                        return
                    buf += chunk

                header_end = buf.index(b"\r\n\r\n")
                headers = buf[:header_end].decode("ascii")
                buf = buf[header_end + 4:]

                content_length = 0
                for line in headers.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":", 1)[1].strip())

                # Read body
                while len(buf) < content_length:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        self._alive = False
                        self._wake_all()
                        return
                    buf += chunk

                body = buf[:content_length]
                buf = buf[content_length:]

                try:
                    msg = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                # Dispatch response
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._events:
                    if "result" in msg:
                        self._results[msg_id] = msg["result"]
                    elif "error" in msg:
                        self._errors[msg_id] = msg["error"]
                    else:
                        # Null result is valid (e.g., empty references)
                        self._results[msg_id] = None
                    self._events[msg_id].set()
                # Server-initiated notifications (diagnostics, progress) are ignored.

            except Exception:
                if self._alive:
                    continue
                return

    def _drain_stderr(self) -> None:
        """Drain stderr to prevent buffer deadlock."""
        fd = self._stderr.fileno()
        try:
            while self._alive:
                data = os.read(fd, 65536)
                if not data:
                    return
        except Exception:
            pass

    def _wake_all(self) -> None:
        """Wake all pending requests (server died or shutting down)."""
        for event in self._events.values():
            event.set()

    # -- Request / Notification helpers --

    def _request(self, method: str, params: Any, timeout: float = LSP_REQUEST_TIMEOUT) -> Any:
        """Send a request and wait for the response (blocking)."""
        if not self._alive:
            return None
        msg_id = self._next_id()
        event = threading.Event()
        self._events[msg_id] = event
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        if event.wait(timeout):
            self._events.pop(msg_id, None)
            if msg_id in self._errors:
                self._errors.pop(msg_id)
                return None
            return self._results.pop(msg_id, None)
        # Timeout
        self._events.pop(msg_id, None)
        return None

    def _request_fire(self, method: str, params: Any) -> int:
        """Send a request without waiting. Returns the message ID for later collection."""
        if not self._alive:
            return -1
        msg_id = self._next_id()
        event = threading.Event()
        self._events[msg_id] = event
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return msg_id

    def _request_collect(self, msg_id: int, timeout: float = LSP_REQUEST_TIMEOUT) -> Any:
        """Wait for a previously fired request's response."""
        if msg_id < 0 or msg_id not in self._events:
            return None
        event = self._events[msg_id]
        if event.wait(timeout):
            self._events.pop(msg_id, None)
            if msg_id in self._errors:
                self._errors.pop(msg_id)
                return None
            return self._results.pop(msg_id, None)
        self._events.pop(msg_id, None)
        return None

    def _notify(self, method: str, params: Any) -> None:
        """Send a notification (no response expected)."""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- LSP lifecycle --

    def initialize(self, root_uri: str) -> bool:
        """Send initialize request. Returns True on success."""
        result = self._request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "callHierarchy": {
                        "dynamicRegistration": False,
                    },
                    "references": {
                        "dynamicRegistration": False,
                    },
                    "synchronization": {
                        "didOpen": True,
                        "didClose": True,
                    },
                },
            },
            "workspaceFolders": [{"uri": root_uri, "name": os.path.basename(uri_to_path(root_uri))}],
        }, timeout=LSP_INIT_TIMEOUT)
        if result is not None:
            self._notify("initialized", {})
            return True
        return False

    def shutdown(self) -> None:
        """Gracefully shut down the LSP server."""
        if self._alive:
            self._request("shutdown", None, timeout=5.0)
            self._notify("exit", None)
        self._alive = False
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        return self._alive and self._proc.poll() is None

    # -- Document operations --

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Notify server that a document was opened."""
        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            },
        })

    def did_close(self, uri: str) -> None:
        """Notify server that a document was closed."""
        self._notify("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })

    # -- Query operations --

    def document_symbols(
        self, uri: str, timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """
        Get all symbols in a document. Returns a tree of DocumentSymbol objects.

        Each symbol has: name, kind (int), range, selectionRange, children.
        SymbolKind: 5=Class, 6=Method, 11=Interface, 12=Function, 13=Variable,
                    10=Enum, 26=TypeParameter, 15=Namespace.
        """
        result = self._request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        }, timeout=timeout)
        return result if isinstance(result, list) else []

    def prepare_call_hierarchy(
        self, uri: str, line: int, character: int, timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """
        Prepare call hierarchy at position. Returns list of CallHierarchyItem.
        Line and character are 0-based (LSP convention).
        """
        result = self._request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }, timeout=timeout)
        return result if isinstance(result, list) else []

    def outgoing_calls(
        self, item: dict, timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """Get outgoing calls from a CallHierarchyItem."""
        result = self._request("callHierarchy/outgoingCalls", {
            "item": item,
        }, timeout=timeout)
        return result if isinstance(result, list) else []

    def incoming_calls(
        self, item: dict, timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """Get incoming calls to a CallHierarchyItem."""
        result = self._request("callHierarchy/incomingCalls", {
            "item": item,
        }, timeout=timeout)
        return result if isinstance(result, list) else []

    def references(
        self, uri: str, line: int, character: int, timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> list[dict]:
        """
        Find all references to the symbol at position.
        Returns list of Location {uri, range}.
        """
        result = self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": False},
        }, timeout=timeout)
        return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Project discovery for LSP
# ---------------------------------------------------------------------------

def discover_projects(root: str, file_results: list[FileResult]) -> list[ProjectGroup]:
    """Group source files by the closest language-owned project config."""
    configs_by_language: dict[str, list[str]] = {
        language.name: [] for language in LANGUAGES
    }
    for directory_path, directory_names, file_names in os.walk(root):
        directory_names[:] = [
            name for name in directory_names if name not in SKIP_DIRS
        ]
        for file_name in file_names:
            for language in LANGUAGES:
                if language.accepts_project_config(file_name):
                    configs_by_language[language.name].append(
                        os.path.join(directory_path, file_name)
                    )

    projects: list[ProjectGroup] = []
    assigned_files: set[str] = set()
    for language in LANGUAGES:
        configs = sorted(
            configs_by_language[language.name],
            key=lambda path: -path.count(os.sep),
        )
        for config_path in configs:
            project_root = os.path.dirname(config_path)
            project = ProjectGroup(
                root=project_root,
                language=language.name,
                config_file=config_path,
            )
            for result in file_results:
                if (
                    result.language in language.lsp_result_languages
                    and result.path not in assigned_files
                    and result.path.startswith(project_root + os.sep)
                ):
                    project.files.append(result)
                    assigned_files.add(result.path)
            if project.files:
                projects.append(project)

    return projects


def _language_id(language: str, path: str) -> str:
    """Map a parsed result language to its LSP languageId."""
    return language_for_result(language).lsp_language_id(path)


# ---------------------------------------------------------------------------
# LSP analysis -- call hierarchy + references
# ---------------------------------------------------------------------------


# LSP SymbolKind constants
_SK_CLASS = 5
_SK_METHOD = 6
_SK_PROPERTY = 7
_SK_ENUM = 10
_SK_INTERFACE = 11
_SK_FUNCTION = 12
_SK_VARIABLE = 13
_SK_CONSTANT = 14
_SK_ENUM_MEMBER = 22
_SK_TYPE_PARAMETER = 26

_SK_TO_KIND = {
    _SK_CLASS: "class",
    _SK_METHOD: "method",
    _SK_INTERFACE: "interface",
    _SK_FUNCTION: "function",
    _SK_VARIABLE: "function",   # const exports
    _SK_CONSTANT: "function",
    _SK_ENUM: "enum",
    _SK_TYPE_PARAMETER: "type",
    _SK_PROPERTY: "function",
}


def _sym_line(sym: dict) -> int:
    """Extract 1-based line from a DocumentSymbol or SymbolInformation."""
    # DocumentSymbol format: { selectionRange: { start: { line } } }
    sel = sym.get("selectionRange")
    if sel:
        return sel.get("start", {}).get("line", 0) + 1
    # SymbolInformation format: { location: { range: { start: { line } } } }
    loc = sym.get("location")
    if loc:
        return loc.get("range", {}).get("start", {}).get("line", 0) + 1
    # Fallback to range
    rng = sym.get("range")
    if rng:
        return rng.get("start", {}).get("line", 0) + 1
    return 0


def _lsp_symbols_to_entities(
    symbols: list[dict],
) -> tuple[list[EntityPayload], list[str]]:
    """
    Convert LSP symbol results into Entity dicts and exported names.

    Handles both DocumentSymbol (nested, has children) and SymbolInformation
    (flat, has location) formats. Filters to top-level definitions only:
    classes, interfaces, functions, types, enums, and exported constants.
    """
    entities: list[EntityPayload] = []
    exported_names: list[str] = []
    seen_names: set[str] = set()

    # Detect format: DocumentSymbol has "children" or "selectionRange",
    # SymbolInformation has "location"
    is_doc_symbol = any("selectionRange" in s or "children" in s for s in symbols[:5])

    if is_doc_symbol:
        # DocumentSymbol: only process top-level symbols
        for sym in symbols:
            _process_doc_symbol(sym, entities, exported_names, seen_names)
    else:
        # SymbolInformation: flat list, filter to top-level kinds
        for sym in symbols:
            # Skip methods/properties (they belong to a class, not top-level)
            if sym.get("containerName", ""):
                continue
            _process_symbol_info(sym, entities, exported_names, seen_names)

    return entities, exported_names


def _process_doc_symbol(
    sym: dict,
    entities: list[EntityPayload],
    exported_names: list[str],
    seen_names: set[str],
) -> None:
    """Process a single DocumentSymbol (with children)."""
    kind_int = sym.get("kind", 0)
    name = sym.get("name", "")
    line = _sym_line(sym)

    if not name or name in seen_names:
        return

    children = sym.get("children", [])

    if kind_int in (_SK_CLASS, _SK_INTERFACE):
        methods = []
        for child in children:
            child_kind = child.get("kind", 0)
            child_name = child.get("name", "")
            if child_kind in (_SK_METHOD, _SK_PROPERTY, _SK_FUNCTION) and child_name:
                methods.append(child_name)
        kind = "class" if kind_int == _SK_CLASS else "interface"
        entities.append({
            "name": name, "kind": kind, "line": line,
            "methods": methods, "bases": [], "decorators": [],
        })
        seen_names.add(name)
        exported_names.append(name)

    elif kind_int == _SK_ENUM:
        members = [c.get("name", "") for c in children if c.get("kind") == _SK_ENUM_MEMBER]
        entities.append({
            "name": name, "kind": "enum", "line": line,
            "methods": members, "bases": [], "decorators": [],
        })
        seen_names.add(name)
        exported_names.append(name)

    elif kind_int == _SK_FUNCTION:
        entities.append({
            "name": name, "kind": "function", "line": line,
            "methods": [], "bases": [], "decorators": [],
        })
        seen_names.add(name)
        exported_names.append(name)

    elif kind_int in (_SK_VARIABLE, _SK_CONSTANT):
        # Only capture exported-looking names (PascalCase or camelCase identifiers)
        # Skip destructured locals, parameters, etc.
        if len(name) >= 2 and (name[0].isupper() or not name.startswith("_")):
            ent_kind = "type" if name[0].isupper() and not children else "function"
            methods = []
            if children:
                for child in children:
                    child_name = child.get("name", "")
                    if child_name:
                        methods.append(child_name)
            entities.append({
                "name": name, "kind": ent_kind, "line": line,
                "methods": methods, "bases": [], "decorators": [],
            })
            seen_names.add(name)
            exported_names.append(name)

    elif kind_int == _SK_TYPE_PARAMETER:
        entities.append({
            "name": name, "kind": "type", "line": line,
            "methods": [], "bases": [], "decorators": [],
        })
        seen_names.add(name)
        exported_names.append(name)


def _process_symbol_info(
    sym: dict,
    entities: list[EntityPayload],
    exported_names: list[str],
    seen_names: set[str],
) -> None:
    """Process a single SymbolInformation (flat, no children)."""
    kind_int = sym.get("kind", 0)
    name = sym.get("name", "")
    line = _sym_line(sym)

    if not name or name in seen_names:
        return

    kind = _SK_TO_KIND.get(kind_int)
    if not kind:
        return

    entities.append({
        "name": name, "kind": kind, "line": line,
        "methods": [], "bases": [], "decorators": [],
    })
    seen_names.add(name)
    exported_names.append(name)


def _drain_prepare_batch(
    client: "LspClient",
    batch: list[tuple[int, str, EntityPayload]],
    path_to_node_id: dict[str, str],
    source_node_id: str,
    lsp_edges: list[LspEdge],
    timeout: float,
) -> int:
    """
    Collect prepareCallHierarchy results, pipeline outgoingCalls, collect those.

    Returns number of CALLS edges found.
    """
    calls_found = 0
    # Collect prepare results and fire outgoingCalls
    outgoing_pending: list[tuple[int, str, str]] = []  # (msg_id, ent_name, node_id)
    for mid, ent_name, _ent in batch:
        items = client._request_collect(mid, timeout=timeout)
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("name") != ent_name:
                continue
            oc_mid = client._request_fire("callHierarchy/outgoingCalls", {"item": item})
            if oc_mid >= 0:
                outgoing_pending.append((oc_mid, ent_name, source_node_id))

    # Collect outgoingCalls results
    for oc_mid, ent_name, node_id in outgoing_pending:
        outgoing = client._request_collect(oc_mid, timeout=timeout)
        if not isinstance(outgoing, list):
            continue
        for call in outgoing:
            target = call.get("to", {})
            target_uri = target.get("uri", "")
            target_name = target.get("name", "")
            target_path = uri_to_path(target_uri)
            target_node_id = path_to_node_id.get(target_path)
            if not target_node_id:
                continue
            from_ranges = [fr["start"]["line"] + 1 for fr in call.get("fromRanges", [])]
            lsp_edges.append(LspEdge(
                source=f"{node_id}::{ent_name}",
                target=f"{target_node_id}::{target_name}",
                edge_type="CALLS",
                metadata={"from_lines": from_ranges, "lsp_verified": True},
            ))
            calls_found += 1
    return calls_found


def _partition_lsp_batch(
    pending_prepare: list[tuple[int, str, EntityPayload]],
    pending_refs: list[tuple[int, str, str]],
    batch_size: int,
) -> tuple[
    list[tuple[int, str, EntityPayload]],
    list[tuple[int, str, str]],
    list[tuple[int, str, EntityPayload]],
    list[tuple[int, str, str]],
]:
    """Take a bounded mixed LSP batch while guaranteeing queue progress."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    prepare_count = min(len(pending_prepare), batch_size // 2)
    reference_count = min(len(pending_refs), batch_size - prepare_count)
    remaining_capacity = batch_size - prepare_count - reference_count
    prepare_count += min(len(pending_prepare) - prepare_count, remaining_capacity)
    return (
        pending_prepare[:prepare_count],
        pending_refs[:reference_count],
        pending_prepare[prepare_count:],
        pending_refs[reference_count:],
    )


def _drain_reference_batch(
    client: "LspClient",
    batch: list[tuple[int, str, str]],
    path_to_node_id: dict[str, str],
    node_id_to_result: dict[str, FileResult],
    lsp_edges: list[LspEdge],
    timeout: float,
) -> int:
    """Collect reference requests and append resolved cross-module edges."""
    refs_found = 0
    for mid, ent_name, src_node_id in batch:
        ref_locations = client._request_collect(mid, timeout=timeout)
        if not isinstance(ref_locations, list):
            continue
        for loc in ref_locations:
            ref_uri = loc.get("uri", "")
            ref_path = uri_to_path(ref_uri)
            ref_node_id = path_to_node_id.get(ref_path)
            if not ref_node_id or ref_node_id == src_node_id:
                continue
            ref_result = node_id_to_result.get(ref_node_id)
            ref_line_0 = loc.get("range", {}).get("start", {}).get("line", 0)
            ref_entity_name = None
            if ref_result:
                ref_entity_name = _find_enclosing_entity(
                    ref_result.entities,
                    ref_line_0,
                )
            source_key = (
                f"{ref_node_id}::{ref_entity_name}"
                if ref_entity_name
                else ref_node_id
            )
            target_key = f"{src_node_id}::{ent_name}"
            lsp_edges.append(LspEdge(
                source=source_key,
                target=target_key,
                edge_type="REFERENCES",
                metadata={"lsp_verified": True, "ref_line": ref_line_0 + 1},
            ))
            refs_found += 1
    return refs_found


def _find_enclosing_entity(
    entities: Sequence[EntityPayload], line_0based: int,
) -> Optional[str]:
    """
    Find the entity that encloses the given 0-based line.

    Uses a simple heuristic: the entity whose line is closest to (but not
    after) the target line.
    """
    best: Optional[EntityPayload] = None
    for ent in entities:
        ent_line_0 = ent["line"] - 1  # entity lines are 1-based
        if ent_line_0 <= line_0based:
            if best is None or ent_line_0 > best["line"] - 1:
                best = ent
    return best["name"] if best else None


def analyze_project_lsp(
    project: ProjectGroup,
    root: str,
    path_to_node_id: dict[str, str],
    node_id_to_result: dict[str, FileResult],
    request_timeout: float,
) -> list[LspEdge]:
    """
    Analyze a single project with LSP for precise call graph and references.

    Returns a list of LspEdge instances to merge into the main graph.
    """
    language = language_for_name(project.language)
    cmd = list(language.lsp_command)

    # Check server availability
    server_bin = cmd[0]
    from shutil import which
    if not which(server_bin):
        print(f"  [lsp] WARNING: {server_bin} not found, skipping {project.root}")
        return []

    proj_name = os.path.relpath(project.root, root)
    print(f"  [lsp] Starting {server_bin} for {proj_name} ({len(project.files)} files)")

    client = LspClient(cmd, cwd=project.root)
    lsp_edges: list[LspEdge] = []

    try:
        # Initialize
        root_uri = path_to_uri(project.root)
        if not client.initialize(root_uri):
            print(f"  [lsp] WARNING: initialize failed for {proj_name}")
            return []

        # Open all project files (batch)
        file_contents: dict[str, str] = {}
        for r in project.files:
            try:
                with open(r.path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                file_contents[r.path] = content
                uri = path_to_uri(r.path)
                lang_id = _language_id(r.language, r.path)
                client.did_open(uri, lang_id, content)
            except OSError as error:
                print(
                    f"  [lsp] WARNING: could not open {r.path}: {error}",
                    file=sys.stderr,
                )
                continue

        # Give the server a moment to process opened files
        time.sleep(1.0)

        # -- Phase 1: Merge LSP documentSymbol with regex entities --
        # LSP gives precise line numbers and finds entities regex misses
        # (object literals, Zod schemas). Regex gives method lists via
        # brace scanning. Merge: take LSP entities as base, carry over
        # regex methods for classes/interfaces that LSP returns without children.
        symbols_replaced = 0
        for r in project.files:
            if not client.alive:
                break
            node_id = path_to_node_id.get(r.path)
            if not node_id:
                continue
            uri = path_to_uri(r.path)
            lsp_syms = client.document_symbols(uri, timeout=request_timeout)
            if lsp_syms:
                new_entities, new_exports = _lsp_symbols_to_entities(lsp_syms)
                # Build regex method index: entity_name -> methods list
                regex_methods: dict[str, list[str]] = {}
                for old_ent in r.entities:
                    methods = old_ent.get("methods", [])
                    if methods:
                        regex_methods[old_ent["name"]] = methods
                # Merge: carry over regex methods when LSP has none
                for ent in new_entities:
                    if ent["kind"] in ("class", "interface") and not ent.get("methods"):
                        regex_m = regex_methods.get(ent["name"])
                        if regex_m:
                            ent["methods"] = regex_m
                    # Also carry over bases/decorators from regex
                    if not ent.get("bases") or not ent.get("decorators"):
                        for old_ent in r.entities:
                            if old_ent["name"] == ent["name"]:
                                if not ent.get("bases") and old_ent.get("bases"):
                                    ent["bases"] = old_ent.get("bases", [])
                                if not ent.get("decorators") and old_ent.get("decorators"):
                                    ent["decorators"] = old_ent.get(
                                        "decorators",
                                        [],
                                    )
                                break
                # Add regex entities not found by LSP (e.g., Zod schemas)
                lsp_names = {e["name"] for e in new_entities}
                for old_ent in r.entities:
                    if old_ent["name"] not in lsp_names:
                        new_entities.append(old_ent)
                        if old_ent["name"] not in new_exports:
                            new_exports.append(old_ent["name"])
                r.entities = new_entities
                r.exported_names = list(set(new_exports))
                symbols_replaced += 1

        if symbols_replaced:
            print(f"  [lsp] {proj_name}: merged entities in {symbols_replaced} files via documentSymbol")

        # -- Phase 2: Pipelined call hierarchy + references --
        # Strategy: for each file, fire all prepareCallHierarchy and
        # references requests at once (pipelined), then collect results.
        # This overlaps network round-trips with server processing.
        total_entities = sum(len(r.entities) for r in project.files)
        processed = 0
        calls_found = 0
        refs_found = 0
        PIPELINE_BATCH = 40  # max concurrent in-flight requests

        for r in project.files:
            if not client.alive:
                print(f"  [lsp] WARNING: server died while processing {proj_name}")
                break

            content = file_contents.get(r.path)
            if not content:
                continue

            node_id = path_to_node_id.get(r.path)
            if not node_id:
                continue

            uri = path_to_uri(r.path)

            # -- Batch 1: fire prepareCallHierarchy + references for all entities --
            pending_prepare: list[tuple[int, str, EntityPayload]] = []
            pending_refs: list[tuple[int, str, str]] = []  # (msg_id, ent_name, node_id)

            for ent in r.entities:
                ent_name = ent["name"]
                ent_line_0 = ent["line"] - 1
                ent_col = find_entity_column(content, ent_name, ent["line"])

                if ent["kind"] in ("function", "class"):
                    mid = client._request_fire("textDocument/prepareCallHierarchy", {
                        "textDocument": {"uri": uri},
                        "position": {"line": ent_line_0, "character": ent_col},
                    })
                    if mid >= 0:
                        pending_prepare.append((mid, ent_name, ent))

                if ent_name in r.exported_names:
                    mid = client._request_fire("textDocument/references", {
                        "textDocument": {"uri": uri},
                        "position": {"line": ent_line_0, "character": ent_col},
                        "context": {"includeDeclaration": False},
                    })
                    if mid >= 0:
                        pending_refs.append((mid, ent_name, node_id))

                # Drain pipeline if too many in-flight
                while len(pending_prepare) + len(pending_refs) >= PIPELINE_BATCH:
                    prepare_batch, reference_batch, pending_prepare, pending_refs = (
                        _partition_lsp_batch(
                            pending_prepare,
                            pending_refs,
                            PIPELINE_BATCH,
                        )
                    )
                    calls_found += _drain_prepare_batch(
                        client, prepare_batch,
                        path_to_node_id, node_id, lsp_edges, request_timeout,
                    )
                    refs_found += _drain_reference_batch(
                        client,
                        reference_batch,
                        path_to_node_id,
                        node_id_to_result,
                        lsp_edges,
                        request_timeout,
                    )

            # -- Collect remaining prepareCallHierarchy results --
            calls_found += _drain_prepare_batch(
                client, pending_prepare, path_to_node_id, node_id, lsp_edges, request_timeout,
            )

            # -- Collect remaining references results --
            refs_found += _drain_reference_batch(
                client,
                pending_refs,
                path_to_node_id,
                node_id_to_result,
                lsp_edges,
                request_timeout,
            )

            processed += len(r.entities)
            if processed % 200 < len(r.entities):
                print(f"  [lsp] {proj_name}: {processed}/{total_entities} entities "
                      f"({calls_found} calls, {refs_found} refs)")

            client.did_close(uri)

        print(f"  [lsp] {proj_name}: done -- {calls_found} calls, {refs_found} refs from {processed} entities")

    except Exception as exc:
        print(f"  [lsp] ERROR in {proj_name}: {exc}")
    finally:
        client.shutdown()

    return lsp_edges


# ---------------------------------------------------------------------------
# LSP merge -- integrate LSP edges into the graph
# ---------------------------------------------------------------------------

def merge_lsp_edges(
    edges: list[dict],
    lsp_edges: list[LspEdge],
) -> list[dict]:
    """
    Merge LSP-derived edges into the existing edge list.

    - New CALLS edges are added directly.
    - REFERENCES edges that match existing ones get ``lsp_verified: true``.
    - New REFERENCES edges not found by the regex pass are added.
    """
    # Index existing edges for fast lookup
    existing_keys: dict[tuple, int] = {}
    for idx, edge in enumerate(edges):
        key = (edge["source"], edge["target"], edge["type"])
        existing_keys[key] = idx

    new_edges: list[dict] = []
    verified_count = 0
    new_count = 0

    for le in lsp_edges:
        key = (le.source, le.target, le.edge_type)
        if key in existing_keys:
            # Mark existing edge as LSP-verified
            edges[existing_keys[key]]["lsp_verified"] = True
            verified_count += 1
        else:
            # Add new edge
            edge_dict: dict = {
                "source": le.source,
                "target": le.target,
                "type": le.edge_type,
            }
            edge_dict.update(le.metadata)
            new_edges.append(edge_dict)
            new_count += 1
            # Track so we don't add duplicates
            existing_keys[key] = -1

    print(f"[belief-map] LSP merge: {verified_count} verified, {new_count} new edges")
    return edges + new_edges


# ---------------------------------------------------------------------------
# LSP enrichment orchestrator
# ---------------------------------------------------------------------------

def enrich_with_lsp(
    root: str,
    results: list[FileResult],
    nodes: list[dict],
    edges: list[dict],
    request_timeout: float,
) -> tuple[list[dict], list[dict]]:
    """Run language-owned LSP analysis and merge its graph evidence."""
    # Build lookup tables
    path_to_node_id: dict[str, str] = {}
    node_id_to_result: dict[str, FileResult] = {}
    for r in results:
        nid = make_node_id(r.path, root)
        path_to_node_id[r.path] = nid
        node_id_to_result[nid] = r

    # Discover projects
    projects = discover_projects(root, results)
    if not projects:
        config_names = ", ".join(
            sorted({
                config_name
                for language in LANGUAGES
                for config_name in language.project_config_names
            })
        )
        print(
            "[belief-map] No LSP-compatible projects found "
            f"(expected one of: {config_names})"
        )
        return nodes, edges

    language_stats: list[str] = []
    for language in LANGUAGES:
        language_projects = [
            project for project in projects if project.language == language.name
        ]
        if language_projects:
            file_count = sum(len(project.files) for project in language_projects)
            language_stats.append(
                f"{len(language_projects)} {language.lsp_label}/{file_count} files"
            )
    print(
        f"[belief-map] LSP: {len(projects)} projects "
        f"({', '.join(language_stats)})"
    )

    all_lsp_edges: list[LspEdge] = []

    for project in projects:
        proj_edges = analyze_project_lsp(
            project, root, path_to_node_id, node_id_to_result, request_timeout,
        )
        all_lsp_edges.extend(proj_edges)

    if all_lsp_edges:
        edges = merge_lsp_edges(edges, all_lsp_edges)

    # Rebuild nodes with LSP-replaced entities
    node_by_id = {n["id"]: n for n in nodes}
    for r in results:
        nid = path_to_node_id.get(r.path)
        if nid and nid in node_by_id:
            node_by_id[nid]["entities"] = r.entities
    nodes = list(node_by_id.values())

    return nodes, edges


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _sexp_escape(s: str) -> str:
    """Escape a string for S-expression output (double-quote delimited)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _entity_id(module_path: str, entity_name: str) -> str:
    """Generate a short unique ID for an entity based on path + name SHA."""
    raw = f"{module_path}::{entity_name}"
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Path trie for compressed path definitions
# ---------------------------------------------------------------------------

def _build_path_tree(path_sid_pairs: list[tuple[str, str]]) -> list[str]:
    """
    Build a compressed path trie and emit as indented S-expression tree.

    Shares common path prefixes to reduce repetition. Collapses single-child
    chains into combined segments (path compression).

    Output format::

        (paths
          (agent-hub/packages/agent-research/src
            (ah0 ReasoningEngine)
            (ah1 ResearchAgent))
          (app-service/app-service/src/operatives
            (asp340 operatives.controller)))
    """
    # Build trie: dict of dicts, leaves are {"_sid": "ah0"}
    # A node can be BOTH a directory (has children) and a leaf (has _sid)
    # when index.ts strips to the directory name.
    root: dict = {}
    for full_path, sid in path_sid_pairs:
        segments = full_path.split("/")
        node = root
        for seg in segments[:-1]:
            node = node.setdefault(seg, {})
        # Leaf: store sid. If the key already exists as a dict (directory),
        # merge _sid into it rather than overwriting children.
        leaf_key = segments[-1]
        existing = node.get(leaf_key)
        if isinstance(existing, dict) and "_sid" not in existing:
            existing["_sid"] = sid
        else:
            node[leaf_key] = {"_sid": sid}

    # Compress: collapse single-child internal nodes
    def compress(node: dict) -> dict:
        result: dict = {}
        for key, child in node.items():
            if isinstance(child, dict) and "_sid" in child:
                result[key] = child  # leaf, keep as-is
            elif isinstance(child, dict):
                compressed = compress(child)
                # If single non-leaf child, merge keys
                while len(compressed) == 1:
                    only_key, only_val = next(iter(compressed.items()))
                    if isinstance(only_val, dict) and "_sid" not in only_val:
                        key = f"{key}/{only_key}"
                        compressed = only_val
                    else:
                        break
                result[key] = compressed
            else:
                result[key] = child
        return result

    compressed = compress(root)

    # Emit as indented S-expressions
    def emit(node: dict, depth: int) -> list[str]:
        lines: list[str] = []
        indent = "  " * depth
        for key in sorted(node.keys()):
            if key == "_sid":
                continue  # handled by parent
            child = node[key]
            if isinstance(child, dict) and "_sid" in child:
                # Check if this is BOTH a leaf and a directory (has other keys)
                other_keys = [k for k in child if k != "_sid"]
                if other_keys:
                    # Emit the _sid as a :self entry, then recurse for children
                    lines.append(f"{indent}({key}")
                    lines.append(f"{indent}  ({child['_sid']} :self)")
                    lines.extend(emit(child, depth + 1))
                    lines.append(f"{indent})")
                else:
                    lines.append(f"{indent}({child['_sid']} {key})")
            elif isinstance(child, dict):
                lines.append(f"{indent}({key}")
                lines.extend(emit(child, depth + 1))
                lines.append(f"{indent})")
        return lines

    return emit(compressed, 1)


def _is_safe_path_atom(path: str) -> bool:
    """Return whether every path segment is safe in the unquoted trie syntax."""
    return bool(re.fullmatch(r"[A-Za-z0-9_./@+-]+", path))


def render_sexp(
    nodes: list[dict],
    edges: list[dict],
    _root: str,
    mode: str,
    total_files: int,
) -> str:
    """
    Write the belief map as flat S-expression facts with path-map compression.

    The file starts with ``(def <id> <full-path>)`` bindings that assign short
    numeric IDs to every module path. All subsequent lines use these IDs instead
    of full paths, reducing file size by ~60-70%.

    Format::

        ; --- paths ---
        (def 0 agent-hub/packages/agent-research/src/ReasoningEngine)
        (def 1 agent-hub/packages/agent-research/src/ResearchAgent)

        ; --- nodes ---
        (node 0 ty "general module" :naming PascalCase :pkg agent-hub)

        ; --- entities ---
        (fn 0 isToolIncluded 5)
        (cls 1 ResearchAgent 322 (:bases BaseAgent) (:methods ...))

        ; --- edges ---
        (imports 1 0 :via-base)
        (refs 1 0::ReasoningSchemaType)

        ; --- violations ---
        (violation boundary 42 99 "domain imports infrastructure without abstract base")
    """
    sorted_nodes = sorted(nodes, key=lambda n: n["id"])

    # Build path map: full path -> short prefixed ID (repo-prefix + seq number)
    # e.g. ah0 = agent-hub first module, dr42 = drive 43rd module
    _REPO_PREFIX: dict[str, str] = {}
    _repo_counters: dict[str, int] = {}
    path_to_sid: dict[str, str] = {}

    for node in sorted_nodes:
        repo = node.get("repo", "unknown")
        if repo not in _REPO_PREFIX:
            # Build 2-char prefix from repo name
            parts = repo.replace("-", " ").split()
            if len(parts) >= 2:
                prefix = parts[0][0] + parts[1][0]
            else:
                prefix = repo[:2]
            # Deduplicate prefixes
            base = prefix
            idx = 2
            while prefix in _REPO_PREFIX.values():
                prefix = base + repo[idx:idx + 1] if idx < len(repo) else base + str(idx)
                idx += 1
            _REPO_PREFIX[repo] = prefix
            _repo_counters[repo] = 0

        seq = _repo_counters[repo]
        _repo_counters[repo] = seq + 1
        sid = f"{_REPO_PREFIX[repo]}{seq}"
        path_to_sid[node["id"]] = sid

    def _pid(path: str) -> str:
        """Resolve a path (possibly with ::entity suffix) to short ID."""
        if "::" in path:
            mod, ent = path.split("::", 1)
            sid = path_to_sid.get(mod)
            return f"{sid}::{ent}" if sid is not None else path
        sid = path_to_sid.get(path)
        return sid if sid is not None else path

    lines: list[str] = []
    violations = [e for e in edges if e.get("type") == "VIOLATION"]
    real_edges = [e for e in edges if e.get("type") != "VIOLATION"]

    lines.append("; Belief Map")
    lines.append(f"; mode {mode}")
    lines.append(f"; {total_files} files {len(sorted_nodes)} nodes {len(real_edges)} edges {len(violations)} violations")
    lines.append(
        f"(belief-map :schema {MAP_SCHEMA_VERSION} :files {total_files} "
        f":nodes {len(sorted_nodes)} :edges {len(real_edges)} "
        f":violations {len(violations)})"
    )
    lines.append("")

    # -- Path tree (compressed trie) --
    lines.append("; --- paths ---")
    safe_nodes = [
        node for node in sorted_nodes if _is_safe_path_atom(node["id"])
    ]
    unsafe_nodes = [
        node for node in sorted_nodes if not _is_safe_path_atom(node["id"])
    ]
    for node in unsafe_nodes:
        sid = path_to_sid[node["id"]]
        escaped_path = _sexp_escape(node["id"])
        lines.append(f'(def {sid} "{escaped_path}")')
    trie_pairs = [(node["id"], path_to_sid[node["id"]]) for node in safe_nodes]
    lines.append("(paths")
    lines.extend(_build_path_tree(trie_pairs))
    lines.append(")")
    lines.append("")

    # -- Nodes with invariant (Gap 1 fix) --
    lines.append("; --- nodes ---")
    for node in sorted_nodes:
        sid = path_to_sid[node["id"]]
        language = language_for_result(node["language"])
        lang = language.output_language_code(node["language"])
        purpose = _sexp_escape(node.get("purpose", ""))
        inv = node.get("invariant", {})
        naming = inv.get("naming", "mixed")
        pkg = inv.get("package", "")
        lines.append(f'(node {sid} {lang} "{purpose}" :naming {naming} :pkg {pkg})')
    lines.append("")

    # -- Entities (each has a unique eid based on sha(path::name)) --
    lines.append("; --- entities ---")
    for node in sorted_nodes:
        sid = path_to_sid[node["id"]]
        for ent in node.get("entities", []):
            kind = ent["kind"]
            name = ent["name"]
            line_num = ent.get("line", 0)
            eid = _entity_id(node["id"], name)
            kind_tag = {
                "class": "cls", "function": "fn", "interface": "ifc",
                "type": "typ", "enum": "enm",
            }.get(kind, kind[:3])

            bases = ent.get("bases", [])
            deco = ent.get("decorators", [])
            methods = ent.get("methods", [])

            if not bases and not deco and not methods:
                lines.append(f"({kind_tag} {sid} {name} {line_num} :eid {eid})")
            else:
                parts = [f"({kind_tag} {sid} {name} {line_num} :eid {eid}"]
                if bases:
                    parts.append(f"(:bases {' '.join(bases)})")
                if deco:
                    parts.append(f"(:deco {' '.join(deco)})")
                if methods:
                    parts.append(f"(:methods {' '.join(methods)})")
                lines.append(" ".join(parts) + ")")
    lines.append("")

    # -- Edges --
    lines.append("; --- edges ---")
    sorted_edges = sorted(real_edges, key=lambda e: (e["type"], e["source"], e["target"]))
    for edge in sorted_edges:
        src = _pid(edge["source"])
        tgt = _pid(edge["target"])
        etype = edge["type"]

        if etype == "IMPORTS":
            flags = " :via-base" if edge.get("via_base") else ""
            lsp = " :lsp" if edge.get("lsp_verified") else ""
            lines.append(f"(imports {src} {tgt}{flags}{lsp})")
        elif etype == "CALLS_API":
            lsp = " :lsp" if edge.get("lsp_verified") else ""
            lines.append(f"(calls-api {src} {tgt} :via-ifc{lsp})")
        elif etype == "DATA_FLOWS_TO":
            flags = " :validated" if edge.get("validated") else ""
            lines.append(f"(data-flow {src} {tgt}{flags})")
        elif etype == "REFERENCES":
            lsp = " :lsp" if edge.get("lsp_verified") else ""
            lines.append(f"(refs {src} {tgt}{lsp})")
        elif etype == "CALLS":
            fl = edge.get("from_lines", [])
            lns = f" :lines {' '.join(str(line) for line in fl)}" if fl else ""
            lines.append(f"(calls {src} {tgt}{lns} :lsp)")
        elif etype == "HTTP_CALLS":
            client = edge.get("client_entity", "")
            client_str = f" :client {client}" if client else ""
            lines.append(f"(http-calls {src} {tgt} :transport http{client_str})")

    # -- Violations (Gap 2 fix) --
    if violations:
        lines.append("")
        lines.append("; --- violations ---")
        for v in violations:
            src = _pid(v["source"])
            tgt = _pid(v["target"])
            rule = v.get("rule", "unknown")
            detail = _sexp_escape(v.get("detail", ""))
            lines.append(f'(violation {rule} {src} {tgt} "{detail}")')

    return "\n".join(lines) + "\n"


def write_sexp(
    output_path: str,
    nodes: list[dict],
    edges: list[dict],
    root: str,
    mode: str,
    total_files: int,
) -> None:
    """Atomically publish a fully rendered belief map."""
    content = render_sexp(nodes, edges, root, mode, total_files)
    _atomic_write_text(output_path, content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuilderOptions:
    root: str
    output_path: str
    full_rebuild: bool
    use_lsp: bool
    lsp_timeout: float


def _builder_usage() -> str:
    return (
        "usage: build_belief_map.py --root ABSOLUTE_PATH "
        "[--output ABSOLUTE_PATH] [--full] [--lsp] "
        "[--lsp-timeout SECONDS]"
    )


def parse_builder_options(args: list[str]) -> Result[BuilderOptions, str]:
    """Validate builder CLI inputs before filesystem work starts."""
    root_argument: Optional[str] = None
    output_argument: Optional[str] = None
    full_rebuild = False
    use_lsp = False
    lsp_timeout = LSP_REQUEST_TIMEOUT

    index = 0
    while index < len(args):
        argument = args[index]
        if argument in ("--root", "--output", "--lsp-timeout"):
            if index + 1 >= len(args):
                return Err(f"{argument} requires a value")
            value = args[index + 1]
            if argument == "--root":
                root_argument = value
            elif argument == "--output":
                output_argument = value
            else:
                try:
                    lsp_timeout = float(value)
                except ValueError:
                    return Err(f"invalid --lsp-timeout value: {value}")
            index += 2
            continue
        if argument == "--full":
            full_rebuild = True
            index += 1
            continue
        if argument == "--lsp":
            use_lsp = True
            index += 1
            continue
        return Err(f"unknown argument: {argument}")

    if root_argument is None:
        return Err("--root is required")
    if not os.path.isabs(root_argument):
        return Err("--root must be an absolute path")
    root = os.path.realpath(root_argument)
    if not os.path.isdir(root):
        return Err(f"{root} is not a directory")

    if not math.isfinite(lsp_timeout) or lsp_timeout <= 0:
        return Err("--lsp-timeout must be a positive finite number")

    output_path = output_argument or os.path.join(root, OUTPUT_FILE)
    if not os.path.isabs(output_path):
        return Err("--output must be an absolute path")
    output_path = os.path.realpath(output_path)
    output_directory = os.path.dirname(output_path)
    if not os.path.isdir(output_directory):
        return Err(f"output directory does not exist: {output_directory}")

    return Ok(BuilderOptions(
        root=root,
        output_path=output_path,
        full_rebuild=full_rebuild,
        use_lsp=use_lsp,
        lsp_timeout=lsp_timeout,
    ))


def _cache_entry_result(path: str, entry: object) -> Result[FileResult, str]:
    if not is_cache_entry_current(path, entry):
        return Err("content hash changed or cache entry is invalid")
    return decode_cache_entry(entry)


def _serialize_file_result(result: FileResult) -> dict:
    return {
        "mtime": result.mtime,
        "result": {
            "path": result.path,
            "language": result.language,
            "repo": result.repo,
            "mtime": result.mtime,
            "content_hash": result.content_hash,
            "imports": result.imports,
            "exports_abstract": result.exports_abstract,
            "implements": result.implements,
            "extends": result.extends,
            "purpose": result.purpose,
            "naming_convention": result.naming_convention,
            "has_validation": result.has_validation,
            "entities": result.entities,
            "imported_names": result.imported_names,
            "exported_names": result.exported_names,
        },
    }


def _run_build(options: BuilderOptions) -> int:
    root = options.root
    t0 = time.monotonic()
    mode = "LSP-enhanced" if options.use_lsp else "syntax"
    print(f"[belief-map] Scanning {root} ... (mode: {mode})")

    files = discover_files(root)
    language_counts = {
        language.name: sum(
            1 for _, language_name, _ in files if language_name == language.name
        )
        for language in LANGUAGES
    }
    count_summary = ", ".join(
        f"{language_counts[language.name]} {language.cli_label}"
        for language in LANGUAGES
    )
    extensions = ", ".join(
        extension
        for language in LANGUAGES
        for extension in language.source_extensions
    )
    print(f"[belief-map] Found {len(files)} source files ({count_summary})")
    print(f"[belief-map] Supported source: {extensions}")

    cache = {} if options.full_rebuild else load_cache(root)
    to_parse: list[tuple[str, str, str]] = []
    cached_results: list[FileResult] = []

    for path, lang, repo in files:
        entry = cache.get(path)
        if entry and not options.full_rebuild:
            cached_result = _cache_entry_result(path, entry)
            if isinstance(cached_result, Ok):
                cached_results.append(cached_result.value)
                continue
            print(
                f"[belief-map] WARNING: rebuilding invalid cache entry for "
                f"{path}: {cached_result.error}",
                file=sys.stderr,
            )
        to_parse.append((path, lang, repo))

    print(f"[belief-map] Parsing {len(to_parse)} changed files ({len(cached_results)} cached)")

    new_results: list[FileResult] = []
    if to_parse:
        workers = min(os.cpu_count() or 4, len(to_parse))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(parse_file, item): item for item in to_parse}
            for fut in as_completed(futures):
                new_results.append(fut.result())

    all_results = sorted(
        cached_results + new_results,
        key=lambda result: os.path.relpath(result.path, root).replace(os.sep, "/"),
    )
    graph_result = build_graph(all_results, root)
    if isinstance(graph_result, Err):
        for collision in graph_result.error.collisions:
            paths = ", ".join(collision.paths)
            print(
                f"[belief-map] ERROR: module ID collision "
                f"{collision.node_id}: {paths}",
                file=sys.stderr,
            )
        print(
            "[belief-map] ERROR: no cache or map was published",
            file=sys.stderr,
        )
        return 1
    nodes = graph_result.value.nodes
    edges = graph_result.value.edges

    syntax_elapsed = time.monotonic() - t0
    print(
        f"[belief-map] Syntax pass: {syntax_elapsed:.2f}s -- "
        f"{len(nodes)} nodes, {len(edges)} edges"
    )

    # LSP enrichment pass (if requested)
    if options.use_lsp:
        t_lsp_start = time.monotonic()
        print(
            f"[belief-map] Starting LSP enrichment "
            f"(timeout={options.lsp_timeout}s per request) ..."
        )
        nodes, edges = enrich_with_lsp(
            root,
            all_results,
            nodes,
            edges,
            options.lsp_timeout,
        )
        t_lsp = time.monotonic() - t_lsp_start
        print(f"[belief-map] LSP pass: {t_lsp:.2f}s")

    new_cache = {
        result.path: _serialize_file_result(result)
        for result in all_results
    }
    map_content = render_sexp(nodes, edges, root, mode, len(all_results))
    save_cache(root, new_cache)
    _atomic_write_text(options.output_path, map_content)

    elapsed = time.monotonic() - t0
    print(f"[belief-map] Done in {elapsed:.2f}s -- {len(nodes)} nodes, {len(edges)} edges")
    print(f"[belief-map] Output: {options.output_path}")

    # Stats
    repos = sorted(set(n["repo"] for n in nodes))
    print(f"[belief-map] Repos: {', '.join(repos)}")

    from collections import Counter
    edge_types = Counter(e["type"] for e in edges)
    total_entities = sum(len(n.get("entities", [])) for n in nodes)
    lsp_verified = sum(1 for e in edges if e.get("lsp_verified"))
    print(f"[belief-map] Entities: {total_entities}")
    for etype, count in sorted(edge_types.items()):
        print(f"[belief-map]   {etype}: {count}")
    if lsp_verified:
        print(f"[belief-map]   LSP-verified: {lsp_verified}")
    return 0


def _lock_path(root: str) -> str:
    root_digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:20]
    return os.path.join(
        tempfile.gettempdir(),
        f"codespaces-belief-map-{root_digest}.lock",
    )


def main(args: Optional[list[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if args is None else args)
    if raw_args in (["-h"], ["--help"]):
        print(_builder_usage())
        return 0
    parsed_options = parse_builder_options(raw_args)
    if isinstance(parsed_options, Err):
        print(f"Error: {parsed_options.error}", file=sys.stderr)
        print(_builder_usage(), file=sys.stderr)
        return 2

    options = parsed_options.value
    lock_path = _lock_path(options.root)
    try:
        lock_file = open(lock_path, "a+", encoding="utf-8")
    except OSError as error:
        print(
            f"[belief-map] ERROR: could not open build lock {lock_path}: {error}",
            file=sys.stderr,
        )
        return 1

    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                f"[belief-map] ERROR: another build is active for {options.root}",
                file=sys.stderr,
            )
            return 1
        return _run_build(options)
    except OSError as error:
        print(f"[belief-map] ERROR: build failed: {error}", file=sys.stderr)
        return 1
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            print(
                f"[belief-map] ERROR: could not release build lock: {error}",
                file=sys.stderr,
            )
        lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
