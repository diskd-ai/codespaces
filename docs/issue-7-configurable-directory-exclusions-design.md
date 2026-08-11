Configurable Directory Exclusions for Belief-Map Builds
========================================================

Status
------

Implemented. This design covers [GitHub issue #7](https://github.com/diskd-ai/codespaces/issues/7)
and the completed contract in pull request
[#8](https://github.com/diskd-ai/codespaces/pull/8), including portable boundary
validation, TypeScript configuration discovery, and repository Git ignore
rules.

Context and motivation
----------------------

The belief-map builder owns a fixed `SKIP_DIRS` set in
`scripts/build_belief_map.py`. `discover_files` applies that set while walking
source trees. `build_graph` passes the same set into language adapters for local
import resolution, and `discover_projects` applies it while finding LSP project
configuration.

Callers cannot add repository-specific directory names. A tracked snapshot tree
therefore looks like live source when its basename is not in `SKIP_DIRS`. The
current checkout demonstrates the failure: a graph query for
`build_belief_map` selects `.canonical-fix.6WjsfQ/scripts/build_belief_map`
before the live `scripts/build_belief_map` module.

The builder needs one caller-extensible exclusion policy that every target-root
discovery component consumes. Git remains the owner of repository ignore
semantics; the builder consumes Git's decisions rather than implementing a
second pattern engine.

Goals
-----

- Accept repeatable `--exclude-dir NAME` arguments.
- Treat each value as one portable directory basename, not a path or pattern.
- Union caller values with the existing built-in exclusions.
- Match excluded basenames at every descendant depth under the target root.
- Apply the same effective set to source, package, alias/configuration, and LSP
  project discovery.
- Honor repository Git ignore rules, including tracked snapshot paths that
  still match `.gitignore`.
- Make the effective set part of cache provenance.
- Preserve built-in basename exclusions whether or not callers add values.
- Surface invalid input before scanning or publishing cache/map artifacts.

Non-goals
---------

- Parsing `.gitignore` patterns inside the builder or adding another glob
  syntax.
- Treating top-level `tsconfig.exclude` as a global source prohibition. It only
  constrains TypeScript include resolution and does not block imported files.
- Accepting individual files, extensions, absolute paths, or relative paths
  through the public CLI.
- Allowing callers to remove built-in exclusions.
- Following excluded symlinks or changing current symlink behavior.
- Excluding the target root itself. Callers choose a narrower root instead.
- Changing infrastructure-topology discovery, which is a separate command and
  ownership boundary.
- Preserving cache compatibility across different effective exclusion sets.

Design principles
-----------------

The builder remains the single owner of the resolved exclusion policy. The CLI
validates caller intent once, Git resolves repository ignore rules, and the
composition root passes one immutable value through existing function
contracts. Discovery components do not read global CLI state or invent local
exclusion lists.

The memorable rule is: one resolved policy controls every walk and its cache
provenance.

C4 boundaries
-------------

### System context

The caller is a developer or coding agent. It invokes the Codespaces belief-map
builder for a target repository and then queries the published map. The target
repository is input data. Git is the external owner for repository ignore
semantics. Optional language servers are external systems used only during LSP
enrichment.

Contract:

```text
Caller -> build_belief_map.py CLI -> cache and belief-map artifacts
                                  -> Git ignore resolution
                                  -> optional language-server processes
```

### Container boundary

`scripts/build_belief_map.py` is the builder process and composition root. It
owns CLI parsing, effective configuration, orchestration, cache publication,
and map publication. Language adapters under `scripts/lang/` are in-process
components behind the `Language` and `BoundLanguage` protocols.

The CLI/container contract is:

```text
build_belief_map.py --root ABSOLUTE_PATH
  [--exclude-dir DIRECTORY_BASENAME]...
```

Success returns exit code `0`. Invalid CLI input returns `2`, writes a specific
error plus usage to stderr, and performs no target-root scan or publication.
Runtime build failures retain the existing exit code `1` contract.

### Component boundaries

| Component | C4 level | Reason to change | Contract |
| --- | --- | --- | --- |
| CLI parser | Component | Public builder grammar or validation changes | `list[str] -> Result[BuilderOptions, str]` |
| Git ignore adapter | Component | Repository ignore resolution changes | `(root, built_in_names) -> GitIgnoreLoaded | GitIgnoreUnavailable` |
| Source discovery | Component | Supported files or traversal policy changes | `(root, exclusions) -> source work items` |
| Language adapters | Component | Language-specific parsing/import resolution changes | `Language.bind(root, path_to_id, exclusions)` |
| LSP project discovery | Component | Project ownership/config discovery changes | `(root, results, exclusions) -> ProjectGroup[]` |
| Cache boundary | Component | Cache schema or semantic provenance changes | `(languages, exclusions, builder sources) -> fingerprint` |
| Publisher | Component | Map/cache durability changes | validated build output -> atomic artifacts |

SOLID applies at these boundaries:

- SRP: CLI validation owns public input; each discovery component owns only its
  discovery kind; cache owns reuse compatibility.
- OCP: new language adapters consume the existing exclusion contract without
  changing CLI semantics.
- LSP: every `Language.bind` implementation receives the same effective policy
  and remains substitutable.
- ISP: discovery functions receive the immutable resolved policy they use, not
  the complete CLI options object.
- DIP: orchestration depends on `Language`/`BoundLanguage`; language adapters do
  not depend on CLI parsing.

No domain object crosses a boundary. The CLI passes a validated configuration
value, language parsers return `FileResult` contract values, and optional LSP
processes communicate through LSP DTOs.

### Code boundaries

The implementation affects these source-owned contracts:

- `parse_builder_options` and `BuilderOptions` in
  `scripts/build_belief_map.py`.
- `discover_files`, `build_graph`, `_builder_fingerprint`, `load_cache`, and
  `save_cache` in `scripts/build_belief_map.py`.
- `discover_projects` and `enrich_with_lsp` in
  `scripts/build_belief_map.py`.
- `Language.bind` in `scripts/lang/interface.py` and the existing adapter
  implementations.
- `load_git_ignored_paths` in `scripts/git_ignore.py` and shared path predicates
  in `scripts/lang/discovery.py`.
- `_load_ts_path_aliases` and `_load_ts_packages` in
  `scripts/lang/typescript/imports.py`.
- `_load_go_modules` in `scripts/lang/go/imports.py`.
- `_load_rust_packages` in `scripts/lang/rust/imports.py`.
- Ruby project configuration reads in `scripts/lang/ruby/imports.py`.

Public CLI contract
-------------------

`--exclude-dir` is repeatable. Order has no meaning and duplicates are
idempotent. Each value excludes directories with that exact, case-sensitive
basename at any descendant depth.

Valid examples:

```text
review-bundles
generated fixtures
.snapshots
```

Invalid examples:

```text
""
.
..
nested/review-bundles
nested\review-bundles
/absolute/review-bundles
C:\review-bundles
```

Boundary validation accepts a value only when all of these conditions hold:

1. It is non-empty.
2. It is neither `.` nor `..`.
3. It contains no NUL, `/`, or `\` character.
4. It has no Windows drive or UNC prefix.

Spaces and leading dots are valid basename characters. Shell quoting remains
the caller's responsibility.

The parser returns the existing `Err[str]` variant for invalid values. It does
not throw. A focused pure validation helper may be introduced if that keeps the
CLI loop locally correct and independently testable.

Internal configuration contract
--------------------------------

After parsing, orchestration computes:

```text
directory_names = frozenset(SKIP_DIRS) union caller_exclusions
ignored_paths = git check-ignore --no-index(candidate_paths)
effective_exclusions = DiscoveryExclusions(directory_names, ignored_paths)
```

`BuilderOptions` stores the immutable basename set. The composition root asks
Git for repository-owned path decisions and constructs the final immutable
policy. Machine-global Git ignore configuration is disabled so discovery is
reproducible from the repository boundary.

The implementation must not keep separate default lists in discovery
components. In particular, TypeScript alias discovery currently owns another
hard-coded list in `_load_ts_path_aliases`; it must accept the effective set in
the same way as TypeScript package discovery.

End-to-end behavior
-------------------

1. `parse_builder_options` validates every repeated exclusion and constructs
   `BuilderOptions`.
2. `_run_build` asks Git to resolve ignore decisions with index checks disabled,
   allowing tracked snapshot paths to remain excluded.
3. The composition root combines basename and path exclusions.
4. Source discovery prunes matching directories and files before parsing.
5. `build_graph` passes the same policy through `Language.bind`.
6. TypeScript aliases/packages, Go modules, Rust packages, and Ruby project
   configuration consume the same policy.
7. When LSP mode is active, `enrich_with_lsp` passes the policy to
   `discover_projects`, which prunes excluded project configurations before any
   language server starts.
8. Cache loading compares a fingerprint containing the sorted effective policy.
9. Cache saving records a fingerprint computed from that identical policy.
10. The publisher writes only results discovered under the active policy.

Cache compatibility
-------------------

The builder fingerprint includes the sorted effective basename and path sets in
addition to the existing builder version, active languages, dependency
versions, and builder source hashes.

Semantic rules:

- Reordering or repeating exclusions preserves the fingerprint.
- Adding or removing an exclusion changes the fingerprint.
- Changing built-in exclusions changes the fingerprint.
- Changing repository ignore decisions changes the fingerprint.
- A mismatch discards all old entries through the existing incompatible-cache
  path. This prevents previously cached snapshot files from leaking into a new
  map.
- Cache schema version remains unchanged because the serialized shape does not
  change; semantic compatibility is already owned by the fingerprint.

Error handling
--------------

- Invalid exclusion values return a typed CLI parse error and exit `2`.
- Missing values use the existing `<option> requires a value` error.
- Permission errors encountered during an allowed directory walk retain the
  existing explicit warning behavior.
- Adapter configuration read failures retain their existing surfaced warnings.
- Git unavailability is surfaced as a warning and preserves the explicit
  basename policy; it is never a silent fallback.
- No new catch, fallback, or silent guard is introduced.

Testing approach
----------------

Tests belong in a focused `tests/test_issue_7_exclusions.py` module rather than
the issue #1 regression class.

Pure boundary tests cover:

- one valid basename;
- repeated values and duplicate idempotence;
- empty, dot, dot-dot, POSIX path, Windows path, drive, UNC, and NUL inputs;
- preservation of all built-in exclusions.

Focused regression tests use temporary local fixtures. They cover:

- live and excluded TypeScript source;
- TypeScript `tsconfig.json` alias discovery inside an excluded tree;
- TypeScript `package.json`, Go `go.mod`, and Rust `Cargo.toml` discovery inside
  excluded trees;
- tracked source and configuration paths still matched by `.gitignore`;
- LSP project discovery without starting external infrastructure, by asserting
  `discover_projects` over parsed fixture results;
- repository ignore changes invalidating cache and repopulating the map.

Verification runs only the new focused test module plus the directly affected
language-adapter test modules. External LSP servers and infrastructure tests are
not required for this deterministic traversal contract.

Implementation outline
----------------------

1. Add basename validation to `parse_builder_options` and collect repeated
   values in a set.
2. Store the immutable caller-plus-built-in basename set in `BuilderOptions`.
3. Resolve repository ignore decisions through Git and construct one typed
   discovery policy.
4. Parameterize source discovery, graph construction, cache load/save, LSP
   enrichment, and project discovery with that policy.
5. Pass it through every `Language.bind` implementation.
6. Remove private configuration-walk skip lists and use the shared policy.
7. Add focused issue #7 tests with requirement descriptions.
8. Run the smallest affected test modules and `git diff --check`.
9. Rebuild the current repository with
   `--exclude-dir .canonical-fix.6WjsfQ` and verify the excluded module is absent
   while `scripts/build_belief_map` remains queryable.

Acceptance criteria
-------------------

- `--exclude-dir` can be supplied more than once.
- Every accepted value is a portable single directory basename.
- Invalid path-shaped values return exit code `2` before filesystem scanning.
- Built-in exclusions remain active and cannot be removed by caller input.
- Exact matching directories are absent from source discovery at every depth.
- Excluded TypeScript alias/package, Go module, Rust package, and LSP project
  configurations are not discovered.
- Repository Git ignore rules exclude matching source and configuration paths,
  including tracked paths.
- Changing the effective exclusion set invalidates cache reuse.
- Reordering or duplicating the same basename values preserves the effective
  immutable set.
- The current repository rebuild excludes `.canonical-fix.6WjsfQ` and a graph
  search resolves the live `scripts/build_belief_map` module without the
  snapshot candidate.
- Focused tests pass and no external infrastructure test is required.

Risks and mitigations
---------------------

- A common basename can exclude legitimate directories at multiple depths.
  This is intentional and documented; paths and globs are rejected to keep the
  contract predictable.
- A discovery component can accidentally retain a private skip list. Focused
  configuration/package tests cover each existing workspace walk.
- Git can be unavailable outside a worktree. The builder surfaces that state and
  continues only with explicit basename exclusions.
- Stale cache entries can survive a policy change. The effective set is part of
  both load and save fingerprints.
- Platform-specific path syntax can bypass validation. Validation explicitly
  rejects both POSIX and Windows separators and prefixes on every host OS.

Future considerations
---------------------

Future discovery components extend the existing exclusion contract rather than
adding ignore formats. A separate non-Git path/glob feature requires its own
issue and design because it changes matching semantics, cache provenance, and
caller expectations.
