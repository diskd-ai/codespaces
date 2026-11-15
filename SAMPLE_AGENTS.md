# Repository Agent Instructions

## Mandatory Codespaces discovery before code

For every task that may create, edit, rename, or delete source code, use the
installed `codespaces` skill before reading source files or writing code. This
is a hard pre-write gate, not an optional validation step.

The belief map scopes the investigation. Source code remains the final source
of truth for behavior.

### 1. Resolve the exact environment

Before running a command, resolve and verify:

- the repository root with `git rev-parse --show-toplevel`;
- the active installed `codespaces` skill root from the agent's skill catalog;
- `scripts/build_belief_map.py` and `scripts/belief_search.py` under that skill;
- the current working directory and expected command exit code.

Use the installed skill scripts. Never use a project-local copy because it may
be stale or implement a different output contract.

In the examples below, replace the placeholders with verified absolute paths:

```bash
python3 "<codespaces-skill-root>/scripts/build_belief_map.py" \
  --root "<repository-root>"
```

The incremental build command creates the map when it is absent and refreshes
changed files when it already exists. Use `--full` only when a clean rebuild is
required by the task or when cache correctness is under investigation.

If the builder reports missing language dependencies, install only the exact
language requirement files named by the diagnostic, when dependency
installation is authorized. Do not install every supported parser.

### 2. Find the real module ID

Search the refreshed map using a concrete task keyword:

```bash
python3 "<codespaces-skill-root>/scripts/belief_search.py" \
  search "<task-keyword>"
```

Select a full, human-readable module ID from the results. Never guess a module
ID and never use an internal short ID.

If search returns no match, broaden the keyword or use `entity` for a known
type. Do not repeat the same failing command without changing its input or the
relevant state.

### 3. Analyze before reading source

Immediately analyze the confirmed module:

```bash
python3 "<codespaces-skill-root>/scripts/belief_search.py" \
  analyze "<confirmed-module-id>"
```

Before editing, record these facts in the working plan or progress update:

- selected module and why it owns the behavior;
- `boundary-file` paths that are safe to inspect;
- direct dependencies and reverse dependencies;
- architecture layer and reported violations;
- expected blast radius;
- how this evidence changed or constrained the implementation path.

Read only the source files returned by the boundary analysis and their directly
necessary adjacent contracts. Do not scan whole directories when the map has
already provided a smaller boundary.

For cross-module work, use `deps`, `rdeps`, `flow`, or `boundary` before opening
additional source files. Search first and analyze every newly selected module.

### 4. Pre-write completion gate

Do not write code until all of the following are true:

- the map was refreshed successfully;
- the target module ID was found rather than guessed;
- `analyze` completed successfully;
- the owning boundary and blast radius are understood;
- the implementation plan names only the required files;
- relevant repository instructions and coding conventions were read.

If the map is missing, invalid, truncated, or inconsistent with the current
checkout, rebuild or narrow the query before drawing conclusions. Treat
truncated output as incomplete evidence.

### 5. Verify after implementation

After structural changes such as adding, removing, renaming, or changing
imports between source files, refresh the map again. Then run focused checks:

```bash
python3 "<codespaces-skill-root>/scripts/belief_search.py" \
  boundaries "<confirmed-module-id>"
python3 "<codespaces-skill-root>/scripts/belief_search.py" \
  invariants "<confirmed-module-id>"
```

Also run the smallest affected code tests and static checks required by this
repository. A successful map build does not replace code-level verification.

Report the final boundary result, tests, remaining limitations, and any
disconfirming evidence. Do not treat tool invocation alone as proof that the
implementation is correct.

## Exceptions

Codespaces discovery is not required for tasks that cannot change source code,
such as commit-only operations, Git history summaries, issue comments, or
documentation-only edits. If a documentation task requires claims about code
behavior, query Codespaces before reading the relevant source.
