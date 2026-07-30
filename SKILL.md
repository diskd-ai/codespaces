---
name: codespaces
description: >
  Query `.belief_map.sexp` before non-trivial code changes to find module boundaries,
  dependencies, blast radius, and the minimal files to read. Use for architecture
  discovery, impact analysis, and boundary checks.
---

# Codespaces -- Belief Map Architecture Discovery

Query `.belief_map.sexp` to discover modules, boundaries, and dependencies before reading source files. Never read code blindly -- query the graph first.

## Scripts

This skill bundles Python scripts. Resolve the skill and target roots before
running them so the scanned project is always explicit:

- `scripts/belief_search.py` -- query the belief map graph
- `scripts/build_belief_map.py` -- generate the belief map from source
- `scripts/build_infra_topology.py` -- extract Kustomize, Helm, and Terraform topology facts
- `scripts/git_descendants.py` -- find all commits descending from HEAD (or a given ref)

Never use project-local copies of these scripts -- they may be outdated and output JSON instead of sexp.

## Prerequisite

Locate the belief map. If absent, build it:

```bash
ls .belief_map.sexp belief_map.sexp 2>/dev/null
SKILL_ROOT="$(pwd -P)"
TARGET_ROOT="$(cd /absolute/path/to/project && pwd -P)"
python3 -m pip install -r "$SKILL_ROOT/requirements.txt"
python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT"
python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT" --full
```

## Mandatory Workflow: Query Before Code

For non-trivial code work, run a belief-map query before `rg`, `find`, `sed`,
`cat`, `nl`, opening directories, or reading source files. The skill only helps
when it is used as the first scoping step, not as confirmation after source has
already been inspected.

**Fast first pass when the task has a clear keyword:**
```bash
python3 scripts/belief_search.py quick "keyword"
```
`quick` searches for the first matching module and immediately runs `analyze`.
Use it to get boundary files, dependencies, reverse dependencies, layers, and
violations in one step. If `quick` returns the wrong module or no match, use the
explicit `search` -> `analyze` flow below.

**Step 1 -- Find the module ID:**
```bash
python3 scripts/belief_search.py search "keyword"
python3 scripts/belief_search.py entity EntityName
```
Use `search` or `entity` to locate the module ID. Module IDs are **full relative paths** like `pki-service/src/modules/ca/ca.service`, not short codes like `ps34`. The belief map may contain short path-map IDs internally, but they are resolved automatically -- always pass human-readable path-like strings. Do NOT guess or invent module IDs -- always confirm with `search` first.

**Step 2 -- Analyze the module (do this IMMEDIATELY, not optionally):**
```bash
python3 scripts/belief_search.py analyze <module-id>
```
`analyze` is the PRIMARY command. It returns the full context: boundary files, dependencies, reverse dependencies, architecture layer, and violations. Always run it before reading any source.

**Step 3 -- Read only the boundary files listed in the analyze output.**

**Step 4 -- Do NOT read entire directories.** If the boundary shows 2-3 relevant files, read only those files. Ignore everything else in the directory.

## Command Reference

| Command | Purpose | Example |
|---|---|---|
| `search "<pattern>"` | Safe literal/`.*` search on graph | `search "operative.*service"` |
| `analyze <id>` | Full module analysis (PRIMARY command) | `analyze drive/modules/drive/drive_schema` |
| `quick <keyword>` | Search + analyze first match (shortcut) | `quick operatives` |
| `boundary <id>` | Files to read for a change | `boundary operatives.service` |
| `boundary <id> --files-only` | File paths only (compact) | `boundary operatives.service --files-only` |
| `entity <Name>` | Find definitions + references | `entity DrivePath` |
| `deps <id> [depth]` | Outgoing dependency tree | `deps operatives.service 3` |
| `rdeps <id> [depth]` | Blast radius (who depends on this) | `rdeps drive_schema 2` |
| `flow <id> <fn>` | Trace call/data flow | `flow drive_api fetchFiles` |
| `module <id>` | Single module with edges | `module operatives.controller` |
| `boundaries [id\|all]` | Check architecture violations | `boundaries all` |
| `invariants [id\|all]` | Check naming convention violations | `invariants all` |
| `layers` | Show all modules by layer | `layers` |
| `find_function <name>` | Find function/method definitions | `find_function createOrder` |
| `find_type <name>` | Find type/class/ifc/enum definitions | `find_type DriveDbCommitResponse` |
| `find_callchain <src> <tgt> [depth]` | Trace call path between functions | `find_callchain createOrder processPayment` |
| `find_callers <fn> [depth]` | Who calls this function | `find_callers handleRequest 3` |
| `find_calls <fn> [depth]` | What does this function call | `find_calls createOrder 2` |
| `grep_functions <pattern>` | Search function bodies in source | `grep_functions "validate.*input"` |
| `diff_functions [ref]` | Changed functions in git diff | `diff_functions HEAD~3` |
| `query '<sexp>'` | Composable Scheme query | `query '(count (boundary "X"))'` |
| `repl` | Interactive Scheme REPL | `repl` |

## Quick Reference: rg Queries

When you need fast, targeted lookups without invoking the Python script, query the belief map directly with `rg`. The bundled builder writes `.belief_map.sexp`; if a repo still uses the legacy `belief_map.sexp`, substitute that filename in the commands below:

```bash
# All facts about a module
rg 'drive/modules/drive_db/api ' .belief_map.sexp

# Who imports a module
rg 'imports .* drive/modules/commons/result' .belief_map.sexp

# What does a module import
rg 'imports drive/modules/drive_db/api' .belief_map.sexp

# All references to an entity
rg 'DriveDbCommitResponse' .belief_map.sexp

# All classes in a repo
rg '^\(cls drive/' .belief_map.sexp

# Data flow edges
rg 'data-flow' .belief_map.sexp
```

These one-liners are useful for quick checks. For structured output and full analysis, use the Python script commands.

## Output Notation

All output is S-expression facts, one per line. See [references/sexp-notation.md](references/sexp-notation.md) for the full format.

Key patterns:
```scheme
(boundary <mod> :lang py :file <path> :purpose "<desc>")
(boundary-dep <mod> <dep> :lang ts :file <path> :relation imports)
(boundary-rdep <mod> <rdep> :lang tsx :file <path> :relation imports)
(boundary-summary <mod> :total 10 :deps 7 :rdeps 2)
(entity-def cls <mod> <Name> <line> :lang py :file <path>)
(violation <src> <tgt> :src-layer domain :tgt-layer api :via imports :lang ts "<reason>")
(result <module-or-path>)
(result-count 42)
```

**`:lang` values**: `py` = Python (snake_case), `ts` = TypeScript (camelCase), `tsx` = TSX (PascalCase).

## Scheme Query Language

Compose queries for complex analysis. See [references/scheme-queries.md](references/scheme-queries.md) for full reference.

**Essentials:**
```scheme
(boundary "id")                ; target + deps + rdeps (change boundary)
(entity "Name")                ; modules defining entity
(deps "id" depth)              ; outgoing dependencies
(rdeps "id" depth)             ; reverse dependencies
(find-function "name")         ; modules with matching function defs
(find-type "name")             ; modules with matching type defs
(find-callers "fn" depth)      ; modules calling a function
(find-calls "fn" depth)        ; modules a function calls
(intersect A B)                ; set intersection
(filter SET "pattern")         ; safe literal/.* filter
(files SET)                    ; resolve to file paths
(count SET)                    ; count members
(violations)                   ; modules with boundary violations
(layer "id")                   ; architecture layer of a module
```

## Analysis Plan Pattern

Before modifying code, compose a plan from queries:

```scheme
; 1. Scope
(boundary "operatives.service")
(count (boundary "operatives.service"))

; 2. Architecture constraints
(violations "operatives.service")
(layer "operatives.service")

; 3. Cross-language boundaries
(filter (refs-to "DrivePath") "app-service")

; 4. Narrow to actionable files
(filter (boundary "operatives.service") "dto")
(intersect (deps "controller" 2) (deps "service" 2))

; 5. After changes: verify
(violations "operatives.service")
```

## Trigger Examples

Use this skill before reading source for:

- Root-cause investigations that mention services, APIs, jobs, events, UI flows, or failing behavior.
- Cross-service or cross-module questions, including "how does X call Y?" and "where is this stored?"
- Redmine clarification when the answer depends on code behavior.
- Architecture reviews, impact analysis, public contract changes, or blast-radius checks.
- Reviews of non-trivial staged changes when dependency direction or module ownership matters.

Do not force it for commit-only tasks, git-log summaries, manual demo/server runs, pure browser testing, or session-log analysis.

## Error Recovery

If a command returns `(error no-match ...)`:
1. Do NOT retry with the same ID or guess another ID
2. Run `search` with a broader keyword to find the correct module ID
3. Use the `:suggestions` field if present -- it lists similar module IDs

```bash
# Wrong: guessing IDs
python3 scripts/belief_search.py analyze ps34        # fails: ps34 is a short path-map ID
python3 scripts/belief_search.py analyze ca.service  # fails: incomplete path

# Right: search first, then use the full path from results
python3 scripts/belief_search.py search "ca.*service"
# Output shows: pki-service/src/modules/ca/ca.service
python3 scripts/belief_search.py analyze pki-service/src/modules/ca/ca.service
```

## Anti-patterns

- **Guessing module IDs.** Module IDs are full relative paths (e.g., `pki-service/src/modules/ca/ca.service`). Short codes like `ps34` are internal path-map IDs and must not be used directly. Always `search` first.
- **Build but never query.** Building the belief map and then never running `search`, `analyze`, or `boundary` provides zero value.
- **Querying after reading source.** The first source-code read should come from `boundary-file` lines in `analyze` output.
- **Reading `.belief_map.sexp` as a whole file.** Use `search`, `analyze`, or `rg` to query specific facts.
- **Skipping the belief map for cross-module tasks.** Always query the graph to understand boundaries before reading source files.
- **Reading all files in a directory.** When the boundary shows 2-3 files, read only those.
- **Using project-local scripts.** Always use this skill's bundled scripts. Project copies may output JSON instead of sexp.

## Rebuilding the Graph

```bash
python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT"
python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT" --full
python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT" --lsp
python3 "$SKILL_ROOT/scripts/build_belief_map.py" \
  --root "$TARGET_ROOT" --output "$TARGET_ROOT/custom-map.sexp"
```

Rebuild after structural changes (add/remove files, change imports, add classes).
Search patterns support literals, `.*`, boundary `^`/`$` anchors, and
backslash escapes. Other regex operators are rejected.

The TypeScript AST contract covers TS/TSX imports and re-exports, literal
`import()`/`require()`, `import = require()`, literal template imports, emitted
`.js`/`.jsx` specifiers, project-scoped path aliases, and conventional exact
self-imports. It does not promise inherited `tsconfig` aliases,
`baseUrl`-only resolution, package `source`/`exports`, or self-package export
subpaths.

## Infrastructure Topology

Extract service topology from Kustomize, Helm, and Terraform files. This script requires `PyYAML` (`pip3 install pyyaml`).

```bash
python3 scripts/build_infra_topology.py                    # auto-detect all IaC
python3 scripts/build_infra_topology.py --kustomize .      # kustomize only
python3 scripts/build_infra_topology.py --helm ./charts    # helm charts only
python3 scripts/build_infra_topology.py --terraform ./infra # terraform only
python3 scripts/build_infra_topology.py --append .belief_map.sexp  # merge into graph
```

Emits `infra-node`, `k8s-depends`, `k8s-service`, `helm-depends`, `tf-depends`, and `infra-maps` edges. Query with:
```bash
rg 'infra-node' .belief_map.sexp                  # all services
rg 'k8s-depends' .belief_map.sexp                 # service dependencies
rg 'infra-node.*:component database' .belief_map.sexp  # all databases
rg 'infra-maps.*app-service' .belief_map.sexp     # infra -> source code mapping
```
