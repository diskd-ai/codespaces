# Codespaces - Code Search, Dependency Graphs, and Impact Analysis

> **Install:** `npx skills add diskd-ai/codespaces` | [skills.sh](https://skills.sh)
>
> **Release notes:** [Changelog](CHANGELOG.md)

Understand a large codebase before you change it. Codespaces is an
architecture-aware code search tool for Python, TypeScript, TSX, and Rust
repositories. It helps developers and AI coding agents find the right module,
trace dependencies, measure change impact, and read only the source files that
matter.

Use it for codebase architecture discovery, dependency graph search, blast-radius
analysis, call-flow tracing, monorepo navigation, and safer AI-assisted coding.

## What can Codespaces answer?

- Where is authentication, billing, upload, or another feature implemented?
- Which modules depend on this service, class, function, or type?
- What could break if I change this file?
- Which two or three files should I read before fixing a bug?
- How does a request or data value move through the system?
- Does this dependency cross an architecture boundary?
- Can you draw a call-flow, data-flow, or sequence diagram from the code?

Instead of searching a repository one text match at a time, Codespaces turns code
into a queryable map of modules, entities, imports, references, call relationships,
architecture layers, and infrastructure connections.

## Quick start for AI coding agents

Install the skill:

```bash
npx skills add diskd-ai/codespaces
```

Then ask your agent a natural question:

```text
Use the Codespaces skill before reading source.
Find where file uploads are handled, analyze that module, show its dependencies
and blast radius, then list only the boundary files needed for the change.
```

Codespaces works with Codex, Claude Code, and other clients that support Agent
Skills.

## Quick start from the command line

Clone the repository and install its pinned parser dependencies:

```bash
git clone https://github.com/diskd-ai/codespaces.git
cd codespaces
python3 -m pip install -r requirements.txt
```

Build an architecture map for an explicit project directory:

```bash
python3 scripts/build_belief_map.py --root /absolute/path/to/project
```

Run a relevant search and get the first matching module with its full context:

```bash
python3 scripts/belief_search.py \
  --map /absolute/path/to/project/.belief_map.sexp \
  --root /absolute/path/to/project \
  quick "file upload"
```

The map is written to `.belief_map.sexp`. Codespaces does not modify the source
files it analyzes.

## Common code searches

| Goal | Command |
|---|---|
| Find and analyze a feature | `quick "payments"` |
| Search for a module | `search "payment.*service"` |
| See full module context | `analyze path/to/module` |
| Find files needed for a change | `boundary path/to/module --files-only` |
| Trace dependencies | `deps path/to/module 2` |
| Measure blast radius | `rdeps path/to/module 2` |
| Find a class, interface, enum, or type | `find_type OrderService` |
| Find a function or method | `find_function createOrder` |
| Find callers | `find_callers createOrder 2` |
| Trace a call path | `find_callchain handleRequest createOrder 5` |
| Search inside function bodies | `grep_functions "validate.*input"` |
| Check architecture boundaries | `boundaries all` |
| Check naming conventions | `invariants all` |
| Inspect changed functions | `diff_functions HEAD~1` |

Pass the same `--map` and `--root` options shown in the quick start when the map
is outside the current directory.

## Supported languages and projects

| Language | What is indexed |
|---|---|
| Python | Modules, imports, classes, functions, methods, inheritance, and references |
| TypeScript and TSX | Imports, re-exports, classes, interfaces, functions, types, decorators, aliases, and references |
| Rust | Cargo crates, modules, `use` dependencies, structs, traits, enums, functions, implementations, and methods |

Codespaces is designed for single repositories and monorepos. It understands
Python project roots, TypeScript configuration and package aliases, Cargo package
names, and local Rust crate dependencies.

It can also map infrastructure relationships from Kustomize, Helm, and Terraform.

## A simple workflow

1. Build the map for the exact repository you want to understand.
2. Search for a feature, type, function, or module instead of guessing a path.
3. Analyze the returned module to see dependencies, reverse dependencies, layers,
   references, and the minimal boundary files.
4. Read only those files and make the change.
5. Rebuild the map after structural changes and check boundaries again.

This keeps code exploration focused and gives AI coding agents less irrelevant
context, clearer ownership, and better evidence for implementation decisions.

## Call graphs and diagrams

The default static analysis builds import, reference, data-flow, and architecture
edges. Add `--lsp` when supported language servers are installed to enrich the map
with precise call-site information:

```bash
python3 scripts/build_belief_map.py \
  --root /absolute/path/to/project \
  --lsp
```

You can then ask an agent to render Mermaid diagrams from the graph:

```text
Use Codespaces to trace the file upload flow and render a Mermaid sequence diagram.
Base every participant and edge on the belief-map facts.
```

## Accuracy and limits

Codespaces is an architecture and impact-analysis tool, not a compiler replacement.
The default map comes from deterministic static parsing. Optional LSP enrichment
adds call hierarchy and reference detail when the relevant language server is
available.

For generated imports, runtime dependency injection, reflection, or framework
behavior that is not explicit in source, verify the result against the running
application or the owning framework.

## Project resources

- [Skill workflow and complete command reference](SKILL.md)
- [S-expression map format](references/sexp-notation.md)
- [Composable query language](references/scheme-queries.md)
- [Changelog](CHANGELOG.md)
- [Completion log](LOG.md)

## License

MIT
