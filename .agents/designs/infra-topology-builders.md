Infrastructure Topology Builders for Belief Map
=================================================

Context and motivation
----------------------

The belief map currently models **source-code** relationships: imports, entity references, data flows, and interface implementations. It has no visibility into **infrastructure topology** -- which services exist, how they connect at the network level, what databases/queues/caches they depend on, and how they are deployed.

This creates a blind spot: when an agent investigates a cross-service issue or plans a deployment change, the belief map shows code dependencies but not the runtime service graph. The data exists in three IaC formats already present in the codebase:

- **Kustomize** (`.k8s/base/kustomization.yaml`): 20+ services in the mono workspace, each with deployment manifests declaring env vars, service endpoints, and resource dependencies (postgres, rabbitmq, minio, vault)
- **Helm charts** (`charts/*/Chart.yaml`): avionica streaming services (sara-mock, transformer, ergoss-push, nats, prometheus, dashboard)
- **Terraform** (`infra/terraform/*.tf`): infrastructure prerequisites (namespaces, secrets, PVCs, configmaps) with module dependencies

Goals:
- Extract service topology from all three IaC formats and emit sexp edges into the belief map
- Detect service-to-service dependencies: env var references, service DNS names, port mappings
- Detect service-to-infrastructure dependencies: databases, message queues, object stores, vaults
- Output is the same `.belief_map.sexp` format, appended or merged by the main builder

Non-goals for first implementation (v1):
- No runtime service discovery (no kubectl, no cluster queries)
- No Tilt/docker-compose parsing (separate effort)
- No ingress/route topology (just service-to-service and service-to-infra)
- No multi-cluster or multi-namespace topology
- No cost or resource quota analysis


Implementation considerations
------------------------------

**Pure static analysis.** All three parsers read YAML/HCL files from disk. No cluster access needed.

**Additive to existing graph.** Infrastructure edges are a new layer on top of the source-code graph. They use new edge types (`k8s-service`, `k8s-depends`, `infra-resource`) that don't conflict with existing `imports`/`calls-api`/`data-flow` edges.

**Node identity.** Infrastructure nodes use the service name from k8s metadata (e.g., `app-service`, `drive-postgres`, `nats`) prefixed with `infra/` to distinguish from source-code module IDs. Example: `infra/app-service`, `infra/drive-postgres`.

**Minimal dependencies.** Kustomize and Helm parsers use only PyYAML (already available). Terraform parser uses `hcl2` Python library or falls back to regex if unavailable.

**Invocation.** Each script is standalone but designed to be called from `build_belief_map.py` as an optional post-pass:

```bash
python3 scripts/build_infra_topology.py [--kustomize PATH] [--helm PATH] [--terraform PATH]
```

Or integrated into the main builder via `--infra` flag.


High-level behavior
-------------------

### Discovery

1. Scanner walks the workspace looking for:
   - `kustomization.yaml` / `kustomization.yml` files (kustomize)
   - `Chart.yaml` files (helm)
   - `*.tf` files in directories containing `main.tf` (terraform)

2. For each discovered IaC root, the appropriate parser runs.

3. All parsers emit the same intermediate format: a list of `InfraNode` and `InfraEdge` dataclasses.

4. A merge step deduplicates nodes by service name and writes sexp output.

### Kustomize parser

Reads each kustomization.yaml and its referenced resources:

1. Parse `kustomization.yaml` -> extract `resources` list
2. For each resource directory, parse `deployment.yaml` / `statefulset.yaml`:
   - Extract service name from `metadata.name` or `app.kubernetes.io/name` label
   - Extract container image -> maps service to codebase repo
   - Extract `env` and `envFrom` -> detect service endpoint references:
     - `*_HOST`, `*_URL`, `*_SERVICE_HOST` -> depends-on target service
     - `DATABASE_URL`, `POSTGRES_*` -> depends-on database
     - `RABBITMQ_*`, `AMQP_*`, `NATS_*` -> depends-on message queue
     - `REDIS_*` -> depends-on cache
     - `S3_*`, `MINIO_*` -> depends-on object store
3. Parse `service.yaml` -> extract ports and service type

Output edges:
```scheme
(infra-node infra/app-service :kind deployment :image upgraide/app-service :port 3001 :component api)
(infra-node infra/app-postgres :kind deployment :image postgres:16 :port 5432 :component database)
(k8s-depends infra/app-service infra/app-postgres :via env :transport tcp)
(k8s-depends infra/app-service infra/drive-service :via env :transport http)
(k8s-service infra/app-service app-service :namespace upgraide :port 3001)
```

### Helm parser

Reads Chart.yaml and values.yaml:

1. Parse `Chart.yaml` -> extract chart name, version, dependencies
2. Parse `values.yaml` -> extract:
   - `image.repository` / `image.tag` -> container image
   - `env.*` -> service endpoint references (same heuristics as kustomize)
   - `service.port` -> exposed port
3. Parse `Chart.yaml` `dependencies` -> direct chart-to-chart edges

Output edges:
```scheme
(infra-node infra/sara-mock :kind helm-chart :image sara-mock :port 8080 :chart sara-mock)
(helm-depends infra/sara-mock infra/nats :via chart-dependency)
```

### Terraform parser

Reads `*.tf` files:

1. Find all `module` blocks -> extract module name and source
2. Find all `resource` blocks -> extract resource type and name
3. Build dependency graph from:
   - `module.X.output_name` references in other modules
   - `depends_on` explicit declarations
   - Variable references across modules

Output edges:
```scheme
(infra-node infra/tf-namespace :kind terraform :resource kubernetes_namespace)
(infra-node infra/tf-secrets :kind terraform :resource module)
(tf-depends infra/tf-secrets infra/tf-namespace :via module-reference)
```


S-expression output format
---------------------------

New fact types (appended to `.belief_map.sexp`):

```scheme
; Infrastructure nodes
(infra-node <id> :kind <deployment|statefulset|helm-chart|terraform> :image <image> :port <port> :component <label>)

; Kustomize service dependencies
(k8s-depends <src-service> <tgt-service> :via <env|dns|volume> :transport <http|tcp|amqp|grpc>)

; Kustomize service declarations
(k8s-service <service-id> <dns-name> :namespace <ns> :port <port>)

; Helm chart dependencies
(helm-depends <src-chart> <tgt-chart> :via <chart-dependency|values-ref>)

; Terraform module dependencies
(tf-depends <src-module> <tgt-module> :via <module-reference|depends-on|variable>)

; Cross-layer: maps infra service to source-code repo
(infra-maps infra/app-service app-service :via image-name)
```

The `infra-maps` edge connects infrastructure nodes to source-code nodes, enabling queries like:
```scheme
(deps "infra/app-service" 2)  ; what infra does app-service depend on?
(rdeps "infra/drive-postgres" 1)  ; which services use this database?
```


Error handling and UX
---------------------

- Missing YAML files: skip with `; warning: skipped <path>: file not found` comment in output
- Malformed YAML: skip resource, emit `; error: parse failed <path>: <reason>`
- Missing PyYAML: exit with clear error message and install instructions
- Missing hcl2 (for terraform): fall back to regex-based parsing, emit `; warning: terraform parsed with regex fallback`
- No IaC files found: emit `; info: no infrastructure files found` and exit cleanly


Update cadence / Lifecycle
--------------------------

Infrastructure topology changes rarely compared to source code. The scripts should:
- Run only when `--infra` flag is passed to `build_belief_map.py`
- OR when the user explicitly runs `build_infra_topology.py`
- Cache results alongside `.belief_map_cache.json` using mtime-based invalidation on IaC files


Future-proofing
---------------

- **Tilt / docker-compose**: same intermediate format, different parser. Add `--tilt` and `--compose` flags.
- **Ingress topology**: parse `ingress.yaml` / `Ingress` resources for external routing
- **Multi-namespace**: extend `infra-node` with `:namespace` field
- **Service mesh**: parse Istio/Linkerd VirtualService/DestinationRule for traffic routing edges
- **GitOps**: parse ArgoCD Application manifests for deployment pipeline edges
- **Runtime enrichment**: optional `--live` flag to query kubectl for actual pod status


Implementation outline
----------------------

### Phase 1: Core infrastructure (shared)

1. Define `InfraNode` and `InfraEdge` dataclasses in `build_infra_topology.py`
2. Define env-var heuristic patterns for service/infra detection
3. Implement sexp writer for new fact types
4. Add `--infra` flag to `build_belief_map.py` that calls the topology builder post-pass

### Phase 2: Kustomize parser

1. Walk directories for `kustomization.yaml`
2. Parse resource references -> find deployment/service YAML
3. Extract service names, images, ports, env vars
4. Apply env-var heuristics to detect dependencies
5. Emit `infra-node`, `k8s-depends`, `k8s-service` facts

### Phase 3: Helm parser

1. Walk directories for `Chart.yaml`
2. Parse Chart.yaml dependencies
3. Parse values.yaml for image/port/env configuration
4. Parse templates/*.yaml for env var references (handle Go template syntax)
5. Emit `infra-node`, `helm-depends` facts

### Phase 4: Terraform parser

1. Walk directories for `main.tf`
2. Parse module blocks and resource blocks (via hcl2 or regex)
3. Build dependency graph from cross-module references
4. Emit `infra-node`, `tf-depends` facts

### Phase 5: Cross-layer mapping

1. For each `infra-node` with `:image`, match image name against known repo names
2. Emit `infra-maps` edges connecting infrastructure to source-code graph
3. Update `belief_search.py` to parse new edge types

### Phase 6: Integration

1. Add new edge types to `belief_search.py` edge parser
2. Update `sexp-notation.md` reference with new fact types
3. Update SKILL.md with infrastructure query examples


Testing approach
----------------

**Unit tests:**
- Kustomize parser: fixture kustomization.yaml + deployment.yaml -> expected InfraNode/InfraEdge list
- Helm parser: fixture Chart.yaml + values.yaml -> expected output
- Terraform parser: fixture main.tf with modules -> expected dependency graph
- Env-var heuristics: input env var name/value -> expected dependency type

**Integration tests:**
- Run kustomize parser against `mono/app-service/.k8s/base/` -> verify app-service depends on postgres
- Run helm parser against `nextnet-avsync-streaming/charts/` -> verify chart dependencies
- Run terraform parser against `avsync-stream-lab/infra/terraform/` -> verify module dependencies

**Manual verification:**
- Run `build_infra_topology.py` on mono workspace
- Query `belief_search.py rdeps infra/drive-postgres 1` -> verify all drive consumers listed
- Query `belief_search.py deps infra/app-service 2` -> verify database + queue dependencies shown


Acceptance criteria
-------------------

1. Given a workspace with `.k8s/base/` directories, when running `build_infra_topology.py --kustomize .`, then `infra-node` facts are emitted for each deployment with correct service name, image, and port.

2. Given a deployment.yaml with `DATABASE_URL` env var, when parsing, then a `k8s-depends` edge is emitted to the postgres service with `:transport tcp`.

3. Given a kustomization.yaml with `resources: [app-service, app-web, postgres]`, when parsing, then all three services appear as `infra-node` facts.

4. Given a Helm Chart.yaml with `dependencies`, when parsing, then `helm-depends` edges are emitted for each dependency.

5. Given Terraform modules with cross-references (`module.namespace.name`), when parsing, then `tf-depends` edges reflect the dependency order.

6. Given an `infra-node` with `:image upgraide/app-service`, when cross-layer mapping runs, then an `infra-maps` edge connects `infra/app-service` to the source-code `app-service` node.

7. Given `--infra` flag passed to `build_belief_map.py`, when building completes, then infrastructure facts appear in `.belief_map.sexp` alongside source-code facts.

8. Given no IaC files in the workspace, when running the topology builder, then it exits cleanly with an informational message and no errors.

9. `belief_search.py` correctly loads and queries `infra-node`, `k8s-depends`, `helm-depends`, `tf-depends`, and `infra-maps` edges.


Estimated scope
---------------

| Component | Lines | Effort |
|-----------|-------|--------|
| Shared infra types + sexp writer | ~80 | Small |
| Kustomize parser | ~200 | Medium |
| Helm parser | ~150 | Medium |
| Terraform parser | ~120 | Medium |
| Cross-layer mapping | ~40 | Small |
| belief_search.py integration | ~10 | Trivial |
| Tests | ~150 | Medium |
| **Total** | **~750** | ~2 days |
