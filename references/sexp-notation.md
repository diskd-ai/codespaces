# S-expression Output Notation

Every output line is a self-contained fact. Each fact that references a module
carries a compact `:lang` value such as `py`, `ts`, `rs`, or `rb`.

**Module ID**: relative path from workspace root, no extension, no `/index`. Example: `drive/modules/drive_db/api/drive_db_api.ts` -> `drive/modules/drive_db/api/drive_db_api`.

## Raw Graph Facts (in .belief_map.sexp)

```scheme
(node <id> <lang> "<purpose>" :naming <convention> :pkg <repo>)
(fn <mod> <name> <line> :eid <uuid>)
(cls <mod> <name> <line> :eid <uuid> (:bases A) (:deco D) (:methods m1 m2))
(mod <mod> <name> <line> :eid <uuid> (:methods m1 m2))
(ifc <mod> <name> <line> :eid <uuid> (:methods m1 m2))
(typ <mod> <name> <line> :eid <uuid>)
(enm <mod> <name> <line> :eid <uuid>)
(imports <src> <tgt> [:via-base] [:association|:concern|:job|:mailer|:spec] [:lsp])
(calls-api <src> <tgt> :via-ifc [:lsp])
(data-flow <src> <tgt> [:validated])
(refs <src> <tgt>::<Entity> [:callback] [:lsp])
(calls <src>::<fn> <tgt>::<fn> :lines L1 L2 :lsp)
```

## boundary Output

```scheme
(boundary <mod> :lang py :file <path> :purpose "<desc>")
(cls <mod> <Name> <line> :lang py (:bases A) (:deco D) (:methods m1 m2))
(boundary-dep <mod> <dep> :lang ts :file <path> :relation imports)
(boundary-rdep <mod> <rdep> :lang py :file <path> :relation imports)
(boundary-summary <mod> :total 10 :deps 7 :rdeps 2)
```

## analyze Output

```scheme
(analyze <mod> :lang py "<purpose>" :file <path>)
(cls <mod> <Name> <line> :lang py (:bases A) (:deco D) (:methods m1 m2))
(analyze-import <mod> <tgt> :lang ts :file <path>)
(analyze-dataflow <mod> <tgt> :lang py :validated)
(refs <src> <tgt>::<Entity>)
(analyze-dependent <mod> <src> :lang tsx :via imports refs :file <path>)
(dep <root> <tgt> :lang py :via imports :depth 1)
(rdep <root> <src> :lang tsx :via imports :depth 1)
(key-entity <mod> <Name> :lang py :kind cls :line 32 :refs 7 :modules 4)
(key-entity-ref <Name> <ref-mod> :lang ts)
(boundary-file <mod> <file-path>)
(layer <mod> <dep-name> :layer domain)
(analyze-summary <mod> :entities 31 :imports 1 :dataflows 0 :refs 1 :dependents 50 :boundary-files 52)
```

## entity Output

```scheme
(entity-def cls <mod> <Name> <line> :lang py :file <path>)
(entity-def ifc <mod> <Name> <line> :lang ts :file <path>)
(refs <src-mod> <tgt-mod>::<Name>)
```

## deps / rdeps Output

```scheme
(deps-root <mod> :lang py)
(dep <root> <tgt> :lang ts :via imports :depth 1)
(dep <root> <tgt> :lang py :via data-flow :depth 2 :cycle)
(rdeps-root <mod> :lang ts)
(rdep <root> <src> :lang tsx :via imports :depth 1)
```

## flow Output

```scheme
(flow <src>::<fn> <tgt>::<fn> :lang py :via calls :depth 0)
(flow <src>::<fn> <tgt> :lang ts :via imports :depth 1 :module-level)
```

## boundaries Output

```scheme
(violation <src> <tgt> :src-layer domain :tgt-layer api :via imports :lang ts "<reason>")
(boundaries-summary :violations 2 :checked 3587)
(boundaries-ok :checked 3587)
```

## invariants Output

```scheme
(invariant-violation <mod> :expected snake_case :actual PascalCase :lang py :pkg <repo>)
(invariants-summary :violations 12 :checked 3587)
(invariants-ok :checked 3587)
```

## layers Output

```scheme
(layer-group api :count 144)
(layer-member api <mod> :lang ts)
```

## find_function / find_type Output

```scheme
(fn-def <mod>::<name> :line 42 :lang ts :file <path> :kind function)
(fn-def <mod>::<cls>.<method> :line 42 :lang py :file <path> :kind method)
(type-def cls <mod>::<Name> :line 10 :lang ts :file <path> (:bases A) (:methods m1 m2))
(type-def ifc <mod>::<Name> :line 10 :lang ts :file <path>)
```

## find_callers / find_calls Output

```scheme
(callers-target <mod>::<fn> :line 42 :lang ts :file <path>)
(caller <src>::<fn> -> <tgt>::<fn> :lang ts :file <path> :lines L1 L2 :lsp)
(caller-module <src> -> <tgt> :via imports :depth 1 :lang ts :file <path>)
(calls-source <mod>::<fn> :line 42 :lang ts :file <path>)
(calls-target <src>::<fn> -> <tgt>::<fn> :lang ts :file <path>)
(calls-module <src> -> <tgt> :via imports :depth 1 :lang ts :file <path>)
```

## find_callchain Output

```scheme
(callchain-step 0 <mod>::<fn> :lang ts :file <path>)
(callchain-step 1 <mod>::<fn> :lang py :file <path>)
(callchain-summary :length 3 :hops 2)
(callchain-module-step 0 <mod> :lang ts :file <path>)
(callchain-module-summary :length 2 :hops 1)
(callchain-none "srcFn" -> "tgtFn" :max-depth 5)
```

## grep_functions Output

```scheme
(grep-fn <mod>::<fn> :line 55 :lang ts :file <path> :match "matched line text")
(grep-functions-summary :matches 12 :pattern "pattern")
(grep-functions-empty "pattern")
```

## diff_functions Output

```scheme
(diff-fn <mod>::<name> :kind fn :line 42 :lang ts :file <path>)
(diff-functions-summary :changed 5 :files 3 :ref "HEAD")
(diff-file <path> :hunks 2)
(diff-functions-no-match :files 3 :ref "HEAD")
(diff-functions-empty "HEAD")
```

## query / repl Output

```scheme
(result <module-or-path>)
(result-empty)
(result-count 42)
```

## :lang Values

| Value | Language | Naming | Example |
|---|---|---|---|
| `py` | Python | snake_case fns, PascalCase classes | `drive_path_to_entry`, `DrivePath` |
| `ts` | TypeScript | camelCase fns, PascalCase classes | `calculateTotal`, `OperativesService` |
| `tsx` | TSX/React | PascalCase components, camelCase hooks | `FileBrowser`, `useQuery` |
| `rs` | Rust | snake_case modules and functions | `drive_path`, `DrivePath` |
| `cs` | C# | PascalCase types | `DriveController` |
| `java` | Java | PascalCase types | `DriveService` |
| `go` | Go | PascalCase exports, camelCase locals | `DrivePath`, `loadPath` |
| `rb` | Ruby/Rails | snake_case files, CamelCase constants | `order_payment`, `OrderPayment` |

## Architecture Layers

| Layer | Path patterns | Role |
|---|---|---|
| `domain` | `/domain/`, `/entities/`, `_schema` | Pure business logic |
| `api` | `/api/`, `/routes/`, `/controllers/` | HTTP/RPC handlers |
| `service` | `/services/`, `/application/` | Business orchestration |
| `infra` | `/repositories/`, `/db/`, `/infra/` | Database, storage, messaging |
| `shared` | `/commons/`, `/utils/`, `/types/` | Cross-cutting utilities |
| `test` | `/tests/`, `.test.`, `.spec.` | Test files |
| `ui` | `/components/`, `/pages/`, `/hooks/` | Frontend UI |
| `config` | `/config/`, `/env/` | Configuration |
| `other` | (no match) | Unclassified |
