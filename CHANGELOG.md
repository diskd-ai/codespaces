# Changelog

## 2026-08-08

### Changes

- Added official Tree-sitter support for C#, Java, and Go source discovery,
  entity extraction, local dependency resolution, project recognition, and LSP
  metadata.
- Made Tree-sitter grammars optional and language-scoped: the builder loads only
  detected languages and reports exact per-language install commands when a
  required parser is absent or incompatible.
- Added a language-neutral adapter contract so source discovery, parsing,
  module identity, dependency resolution, output, and LSP metadata can be
  extended without adding language branches to graph orchestration.
- Added Rust source discovery, Tree-sitter entity extraction, Cargo workspace
  import resolution, and rust-analyzer metadata.
- Reworked the README around natural code-search questions, a shorter quick
  start, supported languages, common impact-analysis workflows, and honest
  accuracy limits.
- Expanded the skill and repository descriptions with architecture-aware code
  search, dependency graph, Rust, monorepo, and AI coding-agent discovery terms.

### Fixes

- Excluded standard .NET `bin`/`obj` outputs and Go `vendor` dependencies from
  source discovery.
- Prevented unused C# namespace and Java wildcard imports from creating guessed
  dependency edges based only on package cardinality.
- Prevented unused C#, Java, Go, Rust, or TypeScript grammars from blocking
  Python-only and other single-language map builds.
- Rust files, local Cargo modules, workspace crate dependencies, and imported
  Rust entity references are now represented in generated architecture maps.

## 2026-07-30

### Changes

- Required an explicit absolute project root for map builds and added custom
  output/map-root query options.
- Added pinned Python dependencies and documented the supported TypeScript and
  safe search-pattern contracts.

### Fixes

- Made `CALLS_API` resolution deterministic by following explicit imports and
  failing closed on ambiguous unqualified providers.
- Rejected colliding module IDs before publication instead of silently
  discarding source files.
- Versioned incremental caches by schema and builder provenance.
- Published cache and map files atomically under a per-project writer lock.
- Rejected malformed maps, unsafe search patterns, and invalid query depths
  with structured diagnostics.
- Preserved source paths containing spaces and recognized TypeScript
  `import = require()` plus literal template imports.

## 2026-07-14

### Changes

- Added dependency discovery for TypeScript re-exports, literal dynamic imports, and literal CommonJS requires.
- Added project-scoped TypeScript path aliases and exact package self-import resolution.

### Fixes

- Fixed missing dependency edges from TSX files.
- Fixed path aliases with the same name resolving through an unrelated monorepo package.
- Fixed exact path aliases and package self-imports being omitted from architecture maps.
