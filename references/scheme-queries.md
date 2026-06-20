# Scheme Query Language Reference

Use `query '<expr>'` for one-shot or `repl` for interactive mode.

## Primitives (return module sets)

```scheme
(resolve "pattern")         ; find module IDs matching pattern (suffix > substring)
(boundary "id")             ; target + imports + dependents (change boundary)
(deps "id" depth)           ; outgoing dependencies (default depth 2)
(rdeps "id" depth)          ; reverse dependencies (default depth 1)
(imports "id")              ; direct import targets only
(dependents "id")           ; direct dependents only
(entity "Name")             ; modules defining an entity
(refs-to "Name")            ; modules referencing an entity
```

## Set Operations

```scheme
(intersect A B)             ; set intersection
(union A B ...)             ; set union (variadic)
(diff A B)                  ; A minus B
(filter SET "pattern")      ; keep members matching regex (case-insensitive)
```

## Boundary Analysis

```scheme
(layer "id")                ; architecture layer: domain/api/service/infra/shared/test/ui/config/other
(layer-of SET "layer")      ; filter set to modules in a specific layer
(violations)                ; all modules with boundary violations
(violations "id")           ; violations for a specific module's scope
```

## Function & Type Search (return module-set)

```scheme
(find-function "name")      ; modules containing matching function definitions
(find-type "name")          ; modules containing matching type/class/ifc/enum defs
(find-callers "fn" depth)   ; modules that call a function (default depth 2)
(find-calls "fn" depth)     ; modules that a function calls (default depth 2)
(diff-functions "ref")      ; print changed functions (side-effect, returns empty)
```

## Output Transforms

```scheme
(files SET)                 ; resolve module IDs to file paths
(count SET)                 ; count set members (returns int)
(analyze "id")              ; run full analysis (prints sexp, returns empty set)
(search "pattern")          ; grep .belief_map.sexp (prints matches, returns empty set)
```

## Composition Examples

```scheme
; Files shared between two modules
(intersect (deps "controller" 2) (deps "service" 2))

; Only DTOs in a boundary
(filter (boundary "operatives.service") "dto")

; How many files in the blast radius?
(count (deps "drive_schema" 3))

; Which TS modules reference a Python entity?
(filter (refs-to "DrivePath") "app-service")

; Domain modules that violate boundaries
(violations "all")

; Infra files in the boundary
(layer-of (boundary "operatives.service") "infra")

; Files to read (resolved paths)
(files (filter (boundary "X") "service"))
```

## Result Format

```scheme
(result <module-or-path>)   ; one per set member
(result-empty)              ; empty set
(result-count 42)           ; from (count ...)
```

String results (from `layer`) print as `(result <value>)`.
