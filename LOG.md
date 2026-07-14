## 2026-07-14

### Changes
- Added focused regression coverage for TSX imports, re-exports, literal runtime imports, project-scoped aliases, exact aliases, and package self-imports.

### Fixes
- Resolved TypeScript and TSX dependencies through the same module resolver.
- Kept duplicate path aliases within their owning `tsconfig.json` boundary and supported exact aliases plus multiple configured targets.
- Added import edges for `export ... from`, literal `import()`, and literal `require()` forms.
- Resolved exact package self-imports to the nearest owning package source entrypoint without shadowing installed dependencies.

### Motivation
- The Upgraide monorepo compiler oracle now measures 99.899% local dependency recall and 99.832% precision, reducing architecture-query omissions without embedding a complete TypeScript resolver.

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
- The audit findings were reproducible on isolated fixtures and the Upgraide mono workspace; these changes address the smallest correctness blockers while leaving unsupported languages and infrastructure formats explicit.

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
