# Codespaces Skill

> **Install:** `npx skills add diskd-ai/codespaces` | [skills.sh](https://skills.sh)
>
> **Release notes:** [Changelog](CHANGELOG.md)

Query a `.belief_map.sexp` graph to discover module boundaries, dependencies, and blast radius before reading source -- so you read the minimal set of files, never whole directories.

---

## Scope & Purpose

This skill bundles a belief-map query engine and graph builders for architecture discovery, covering:

* Module boundary and ownership discovery
* Dependency and reverse-dependency (blast-radius) tracing
* Architecture-layer and boundary-violation checks
* Naming-convention (invariant) checks
* Call-chain and data-flow tracing
* Cross-language analysis (Python, TypeScript, TSX)
* Infrastructure topology from Kustomize, Helm, and Terraform

---

## When to Use This Skill

**Triggers:**
* "How does X call Y?" or "Where is this stored?"
* "What breaks if I change Z?"
* Root-cause investigations mentioning services, APIs, jobs, events, or UI flows
* Architecture reviews, impact analysis, or public-contract changes
* Before any non-trivial code change (boundary check first)

**Use cases:**
* Scoping the minimal files to read before implementing a change
* Blast-radius analysis before a refactor
* Detecting architecture-layer and naming violations
* Tracing call chains and data flow across modules
* Mapping infrastructure topology to source code

---

## Quick Reference

### Core Commands

Run from the skill's base directory: `python3 scripts/belief_search.py <command>`

| Command | Purpose |
|---------|---------|
| `search "<pattern>"` | Safe literal/`.*` pattern search for module IDs |
| `analyze <id>` | Full module analysis (PRIMARY command) |
| `quick <keyword>` | Search + analyze the first match (shortcut) |
| `boundary <id>` | Files to read for a change |
| `deps <id> [depth]` | Outgoing dependency tree |
| `rdeps <id> [depth]` | Blast radius (who depends on this) |
| `flow <id> <fn>` | Trace call/data flow |
| `boundaries [id\|all]` | Check architecture violations |
| `invariants [id\|all]` | Check naming-convention violations |
| `layers` | Show all modules grouped by layer |
| `query '<sexp>'` | Composable Scheme query |
| `repl` | Interactive Scheme REPL |

### Output Notation

All output is S-expression facts, one per line.

| Fact | Meaning |
|------|---------|
| `(boundary <mod> :lang :file :purpose)` | Module boundary file |
| `(boundary-dep <mod> <dep> :relation)` | Outgoing dependency edge |
| `(boundary-rdep <mod> <rdep> :relation)` | Reverse-dependency edge |
| `(entity-def cls <mod> <Name> <line>)` | Type/class definition |
| `(violation <src> <tgt> :src-layer :tgt-layer)` | Architecture violation |

`:lang` values: `py` (Python), `ts` (TypeScript), `tsx` (TSX).

---

## Workflow

1. **Locate or build the map**: check for `.belief_map.sexp`; if absent, build it
   ```bash
   SKILL_ROOT="$(pwd -P)"
   TARGET_ROOT="$(cd /absolute/path/to/project && pwd -P)"
   python3 -m pip install -r "$SKILL_ROOT/requirements.txt"
   python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT"
   python3 "$SKILL_ROOT/scripts/build_belief_map.py" --root "$TARGET_ROOT" --full
   ```
2. **Find the module ID**: `search "keyword"` or `entity EntityName` (IDs are full relative paths, never short codes -- never guess them)
3. **Analyze**: `analyze <module-id>` returns boundary files, deps, rdeps, layer, and violations
4. **Read only the boundary files** listed in the analyze output -- not entire directories
5. **Verify after changes**: re-run `boundaries <id>` to confirm no new violations

---

## TypeScript Resolution Contract

The AST pass supports TypeScript and TSX static imports, type-only and
side-effect imports, named/star/type re-exports, literal `import()`, literal
`require()`, `import = require()`, literal template imports, emitted
`.js`/`.jsx` specifiers, project-scoped `paths` aliases, and conventional exact
package self-imports.

It does not claim full Node/TypeScript resolver parity. Inherited `tsconfig`
aliases, `baseUrl`-only imports, package `source`/`exports` entrypoints, and
self-package export subpaths require LSP enrichment or direct source
verification.

Custom output locations can be queried explicitly:

```bash
python3 "$SKILL_ROOT/scripts/belief_search.py" \
  --map /absolute/path/to/map.sexp \
  --root "$TARGET_ROOT" \
  analyze module/id
```

---

## Example Agent Prompts

Drive an agent (Claude Code, Codex, ...) with the query-before-code workflow.

**Refactoring task (detailed):**
```
Extract the validation logic from OperativesService into a separate module.
First use the codespaces skill -- do not read source blindly:

1. Find the module: search "operatives.*service"
2. Run analyze on the found ID -- show boundary, deps, rdeps, layer, violations
3. Check the blast radius: rdeps <id> 2 -- who breaks if I change this
4. Read ONLY the boundary files from the analyze output, not the whole directory
5. Propose a refactoring plan accounting for the blast radius, then change the code
6. After the change, run boundaries <id> -- confirm there are no new violations
```

**Quick debugging (daily):**
```
Use the codespaces belief map before reading code.
Task: why does file upload in drive fail?
Run quick "drive upload", read only the boundary files, find the root cause.
```

**Standing instruction (pin in your agent config):**
```
Before any non-trivial code change, query the belief map first
(search -> analyze -> rdeps), read only boundary files, and run
boundaries after the change.
```

---

## Visualize Flows (Diagrams)

The belief map already stores `imports`, `calls`, `data-flow`, and `refs` edges, so an agent can render **code-flow, data-flow, and sequence diagrams straight from facts** -- not guessed from reading source. This is the fastest way to see the skill's power: ask for a diagram and the agent queries the graph, then emits Mermaid (renders natively on GitHub).

**Sequence diagram (from a call chain):**
```
Use the codespaces skill to draw a sequence diagram for file upload.
1. find_callchain handleUpload create 5   # trace the call path
2. flow drive_api handleUpload            # expand call/data flow
Render the result as a Mermaid sequenceDiagram, one participant per module.
```

**Code-flow / call graph (flowchart):**
```
Use the codespaces skill to draw the call flow around OperativesService.
Run: analyze operatives.service ; deps operatives.service 2
Render a Mermaid flowchart LR -- one node per module, edges = imports/calls.
```

**Data-flow diagram:**
```
Use the codespaces skill to draw the data flow for DrivePath.
Query: rg 'data-flow' .belief_map.sexp ; then refs-to "DrivePath"
Render a Mermaid flowchart showing where data is produced -> validated -> consumed.
```

Tip: add `--lsp` to the explicit-root build command for precise call-site edges -- sequence diagrams come out much sharper.

---

## Skill Structure

```
codespaces/
  SKILL.md                    # Skill definition and mandatory workflow
  README.md                   # This file (overview)
  LOG.md                      # Goal-completion change log
  scripts/                    # Bundled Python tools
    belief_search.py            # Query the belief-map graph
    build_belief_map.py         # Generate the belief map from source
    build_infra_topology.py     # Extract Kustomize/Helm/Terraform topology
    git_descendants.py          # Find commits descending from a ref
  references/                 # Supporting documentation
    sexp-notation.md            # S-expression fact format
    scheme-queries.md           # Scheme query language reference
  tests/
    test_belief_search.py
```

---

## Usage Guidelines

* **Query before you read.** Run a belief-map query before `rg`, `find`, `cat`, or opening directories -- the skill only helps as the first scoping step.
* **Never guess module IDs.** They are full relative paths (e.g. `pki-service/src/modules/ca/ca.service`); always `search` first.
* **`analyze` is primary.** Run it immediately after finding the ID -- it returns the full change context in one step.
* **Read boundary files only.** When the boundary shows 2-3 files, read only those; ignore the rest of the directory.
* **Use the bundled scripts.** Never use project-local copies -- they may be outdated and emit JSON instead of sexp.
* **Rebuild after structural changes** (added/removed files, changed imports, new classes).

---

## Best Practices

### Finding Modules
* Use `search` with literal text or a broad `.*` pattern first, then narrow with the returned full path. Patterns support literals, `.*`, boundary `^`/`$` anchors, and backslash escapes; other regex operators are rejected.
* On `(error no-match ...)`, do not retry the same ID -- broaden the search and use the `:suggestions` field if present.

### Analyzing Impact
* Use `rdeps <id>` to size the blast radius before a refactor.
* Use `boundaries <id>` and `layers` to confirm a change respects architecture layers.

### Composing Queries
* Build an analysis plan from Scheme primitives (`boundary`, `deps`, `rdeps`, `intersect`, `filter`, `files`, `count`) before touching code.
* Re-run `violations <id>` after changes to verify the boundary still holds.

---

## Resources

* **Skill definition**: [SKILL.md](SKILL.md)
* **S-expression notation**: [references/sexp-notation.md](references/sexp-notation.md)
* **Scheme query language**: [references/scheme-queries.md](references/scheme-queries.md)

---

## License

MIT
