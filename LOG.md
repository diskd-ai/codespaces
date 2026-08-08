# Completion Log

## 2026-08-08

### Changes
- Added a language-neutral `Language` protocol and registry for source
  ownership, parsing, import resolution, module IDs, output codes, and LSP
  configuration.
- Moved Python parsing and import resolution to `scripts/lang/python` and
  TypeScript/TSX parsing and import resolution to `scripts/lang/typescript`.
- Added a Rust adapter under `scripts/lang/rust` with Tree-sitter entity
  extraction, trait and inherent implementation methods, Cargo workspace
  resolution, and rust-analyzer metadata.
- Added official Tree-sitter adapters for C#, Java, and Go with language-owned
  parsing, project recognition, module identity, local import resolution, and
  language-server metadata.
- Moved Tree-sitter packages into exact per-language requirement files and made
  parser loading, dependency validation, and cache provenance depend only on
  languages discovered in the target repository.
- Added focused registry, script-mode CLI, Rust parser, and Cargo dependency
  tests while retaining the hardened builder and TypeScript recall coverage.
- Rewrote the README and public descriptions around user questions, quick code
  search, dependency graphs, blast-radius analysis, supported languages, and
  AI-agent workflows.

### Fixes
- Parser dispatch now surfaces unsupported languages and parse failures instead
  of silently dropping failed files.
- Preserved the hardened explicit-root, collision, cache-provenance, atomic
  publication, locking, and safe-query contracts while extracting languages.
- Compared the extracted Python and TypeScript adapters with the upstream
  pre-extraction builder on the current Upgraide mono checkout: all 7,215 files,
  7,215 nodes, and 55,972 edges produced identical facts and graph hashes.
- Verified Rust support against `diskd-cli`: all 4 tracked Rust files were
  discovered, all 256 source-oracle entities matched, and both local crate
  dependency edges matched, with 100% precision and recall.
- Verified C# on `avsync-api-core`: 254/254 authored files, 286/286 entities,
  and 728/728 source-derived local import edges matched with no parse errors.
- Verified Java on `nextnet-tangerine-service`: 28/28 files, 28/28 entities,
  and 46/46 source-derived local import edges matched with no parse errors.
- Verified Go on the Avionica `go-gobwas` target: 1/1 file and 14/14 entities
  matched with no parse errors; its source has no local package-import recall
  denominator, so a focused multi-package fixture verified both expected local
  package edges instead.
- Excluded .NET build output and Go vendored dependencies, and required an
  actual type reference before resolving C# namespaces or Java wildcards.
- Verified a Python-only build with no site packages and a C#-only build with
  only `tree-sitter` and `tree-sitter-c-sharp` installed; missing TypeScript
  packages now produce a targeted install command without publishing artifacts.

### Motivation
- Make new source-language support additive at one explicit boundary, prove
  Rust, C#, Java, and Go coverage on real source repositories, and make the
  project easier to find and use without weakening existing graph correctness
  guarantees.

## 2026-07-30

### Changes
- Added regression coverage for completion-order stability, module-ID
  collisions, cache provenance, atomic publication, writer locking, explicit
  roots/output, additional literal TypeScript imports, quoted paths, malformed
  maps, unsafe patterns, and invalid depths.
- Added exact Python dependency pins and explicit builder/query path contracts.

### Fixes
- Resolved `CALLS_API` providers through consumer imports and failed closed when
  an unqualified provider name remained ambiguous.
- Sorted parsed results and rejected every colliding normalized module ID before
  cache or map publication.
- Added cache/map schema validation, builder fingerprints, atomic replacement,
  and a per-project non-blocking writer lock.
- Replaced user-controlled regular expressions with a bounded linear pattern
  grammar and surfaced numeric, source-read, and structural map errors.
- Encoded unsafe paths as quoted definitions and recognized TypeScript
  `import = require()` and literal template imports.

### Motivation
- GitHub issue #1 reproduced silent semantic changes, discarded modules, stale
  cache trust, unsafe concurrent writes, ambiguous targeting, and unbounded
  query input. These changes make correctness deterministic and failures local,
  explicit, and non-destructive.

## 2026-07-14

### Changes
- Added focused regression coverage for TSX imports, re-exports, literal runtime imports, project-scoped aliases, exact aliases, and package self-imports.

### Fixes
- Resolved TypeScript and TSX dependencies through the same module resolver.
- Kept duplicate path aliases within their owning `tsconfig.json` boundary and supported exact aliases plus multiple configured targets.
- Added import edges for `export ... from`, literal `import()`, and literal `require()` forms.
- Resolved exact package self-imports to the nearest owning package source entrypoint without shadowing installed dependencies.

### Motivation
- An author-run external monorepo compiler oracle measured 99.899% local
  dependency recall and 99.832% precision. The oracle and dataset are not
  committed here, so this is provenance for the change rather than a
  reproducible repository benchmark.

## 2026-07-13

### Changes
- Added focused regression evals for cache validity, Python and TypeScript import impact, LSP batch progress, source-root resolution, layer classification, deterministic output, language gating, and infrastructure diagnostics.
- Made the existing belief-search tests load the checked-out repository script instead of a machine-specific installed path.

### Fixes
- Invalidated incremental cache entries by complete SHA-256 content hash instead of mtime and partial-file hashing.
- Restored source-aware queries by resolving source paths from the directory containing the belief map.
- Prevented reference-only LSP batches from looping without progress.
- Included nested and parent-relative Python imports in dependency edges.
- Resolved TypeScript ESM `.js` import specifiers to their checked-in `.ts` and `.tsx` sources.
- Stopped backend modules named as stores from being classified as UI unless their path has a frontend boundary.
- Removed generated timestamps from the semantic map and made empty infrastructure output name its supported formats.
- Canonicalized the scan root once so macOS path aliases do not leak into module IDs.

### Motivation
- The audit findings were reproducible on isolated fixtures and a large
  polyrepo workspace; these changes address the smallest correctness blockers
  while leaving unsupported languages and infrastructure formats explicit.

## 2026-07-12

### Changes
- Removed the obsolete `.agents` infrastructure-topology design notes and their README tree entry.

### Motivation
- The topology builder is implemented in the source, so retaining its original design notes duplicated completed work and left an unused `.agents` directory.

## 2026-06-19

### Changes
- Updated the codespaces skill workflow so agents are directed to query the belief map before reading source and to use `quick` as a first-pass `analyze` shortcut.
- Changed `belief_search.py quick` to run full `analyze` output instead of boundary-only output, and made it resolve legacy path-map aliases found in raw belief-map hits.
- Added focused unit coverage for quick analyze output, alias resolution from raw hits, boundary files-only output, and no-match suggestions.

### Motivation
- Session-log review showed that agents usually used `search` and `analyze` after activation, but often queried too late and did not discover shortcut or boundary commands.
