# Codespaces Skill

> **Install:** `npx skills add diskd-ai/codespaces` | [skills.sh](https://skills.sh)

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
| `search "<pattern>"` | Regex grep on the graph to find module IDs |
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
   pip3 install tree-sitter tree-sitter-typescript tree-sitter-python
   python3 scripts/build_belief_map.py          # incremental ~5s
   python3 scripts/build_belief_map.py --full   # full rebuild ~30s
   ```
2. **Find the module ID**: `search "keyword"` or `entity EntityName` (IDs are full relative paths, never short codes -- never guess them)
3. **Analyze**: `analyze <module-id>` returns boundary files, deps, rdeps, layer, and violations
4. **Read only the boundary files** listed in the analyze output -- not entire directories
5. **Verify after changes**: re-run `boundaries <id>` to confirm no new violations

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
  .agents/designs/
    infra-topology-builders.md  # Design notes for the topology builder
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
* Use `search` with a broad regex first, then narrow with the returned full path.
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
