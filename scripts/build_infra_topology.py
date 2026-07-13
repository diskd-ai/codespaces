"""build_infra_topology.py -- scan Kustomize, Helm, and Terraform files and
emit infra topology facts in sexp format.

Usage:
    python3 build_infra_topology.py [ROOT] [OPTIONS]

Options:
    ROOT          Workspace root (default: .)
    --kustomize   Scan for kustomize files (default: auto-detect)
    --helm        Scan for helm charts (default: auto-detect)
    --terraform   Scan for terraform files (default: auto-detect)
    --append FILE Append output to existing sexp file
    --stdout      Print to stdout (default if no --append)
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# PyYAML guard
# ---------------------------------------------------------------------------

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    print(
        "Error: PyYAML is required. Install it with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".git", ".venv", "dist", "build", "coverage", "target", "__pycache__"}
)

KUSTOMIZE_FILENAMES: tuple[str, ...] = ("kustomization.yaml", "kustomization.yml")
HELM_CHART_FILENAME = "Chart.yaml"
TERRAFORM_MAIN_FILENAME = "main.tf"

# Env-var heuristic patterns: (compiled_regex, inferred_dep_kind, transport)
# The dep_kind is either a generic service hint or a named infrastructure type.
_ENV_HEURISTICS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"DATABASE_URL|POSTGRES_HOST|POSTGRES_URL|PG_HOST|PG_URL|PGHOST"), "postgres", "tcp"),
    (re.compile(r"RABBITMQ_HOST|RABBITMQ_URL|AMQP_HOST|AMQP_URL|AMQP_URI"), "rabbitmq", "amqp"),
    (re.compile(r"NATS_URL|NATS_HOST|NATS_URI"), "nats", "tcp"),
    (re.compile(r"REDIS_HOST|REDIS_URL|REDIS_URI"), "redis", "tcp"),
    (re.compile(r"S3_ENDPOINT|S3_HOST|MINIO_HOST|MINIO_URL|AWS_S3_ENDPOINT"), "minio", "http"),
    (re.compile(r"VAULT_ADDR|VAULT_HOST|VAULT_URL"), "vault", "http"),
    # Generic service references come last to avoid shadowing the above
    (re.compile(r"(.+?)_HOST$|(.+?)_URL$|(.+?)_SERVICE_HOST$|(.+?)_ENDPOINT$"), "service", "http"),
]

# Module-reference pattern inside Terraform: module.<name>.<attr>
_TF_MODULE_REF_RE = re.compile(r"module\.(\w+)\.")

# Terraform block parsers (no hcl2 dependency)
_TF_MODULE_BLOCK_RE = re.compile(r'module\s+"(\w+)"\s*\{([^}]*)\}', re.DOTALL)
_TF_MODULE_SOURCE_RE = re.compile(r'source\s*=\s*"([^"]+)"')
_TF_RESOURCE_BLOCK_RE = re.compile(r'resource\s+"(\w+)"\s+"(\w+)"\s*\{')


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InfraNode:
    id: str           # infra/<name>
    kind: str         # deployment | statefulset | helm-chart | terraform
    image: str        # container image or ""
    port: int         # primary port or 0
    component: str    # k8s app.kubernetes.io/component label or ""
    source_file: str  # path to the manifest


@dataclass(frozen=True)
class InfraEdge:
    source: str       # infra/<name>
    target: str       # infra/<name>
    edge_type: str    # k8s-depends | helm-depends | tf-depends | infra-maps | k8s-service
    via: str          # env | dns | chart-dependency | module-reference | image-name
    transport: str    # http | tcp | amqp | grpc | ""
    # For k8s-service facts the target holds the dns-name and extra is namespace
    extra: str = ""


@dataclass
class TopologyResult:
    nodes: list[InfraNode] = field(default_factory=list)
    edges: list[InfraEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure utilities
# ---------------------------------------------------------------------------


def _infra_id(name: str) -> str:
    """Return the canonical infra/ prefixed node id for a service name."""
    return f"infra/{name}"


def _safe_load_yaml(path: Path, warnings: list[str]) -> Optional[dict]:
    """Load a YAML file, appending a warning on failure and returning None."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except FileNotFoundError:
        warnings.append(f"; warning: skipped {path}: file not found")
        return None
    except yaml.YAMLError as exc:
        warnings.append(f"; error: parse failed {path}: {exc}")
        return None


def _walk_dirs(root: Path) -> list[Path]:
    """Yield all sub-directories under root, skipping SKIP_DIRS."""
    results: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        results.append(Path(dirpath))
    return results


def _infer_dep_from_env(env_name: str) -> Optional[tuple[str, str]]:
    """Return (dep_kind, transport) inferred from an env var name, or None."""
    for pattern, dep_kind, transport in _ENV_HEURISTICS:
        m = pattern.match(env_name)
        if m:
            if dep_kind == "service":
                # Extract the service name from the prefix captured group
                prefix = next((g for g in m.groups() if g), None)
                if prefix:
                    # Convert SCREAMING_SNAKE to kebab-case service name
                    service_name = prefix.lower().replace("_", "-")
                    return (service_name, transport)
                return None
            return (dep_kind, transport)
    return None


def _extract_env_deps(
    env_list: list[dict],
    source_id: str,
    known_node_names: frozenset[str],
) -> list[InfraEdge]:
    """Return dependency edges inferred from a list of k8s env var dicts."""
    edges: list[InfraEdge] = []
    seen: set[str] = set()

    for env in env_list:
        name = env.get("name", "")
        if not name:
            continue
        result = _infer_dep_from_env(name)
        if result is None:
            continue
        dep_kind, transport = result

        # Resolve target: prefer a known infra node by name, else use the dep_kind
        target_name = dep_kind
        # Check if any known node name contains the dep_kind as a substring
        for node_name in known_node_names:
            if dep_kind in node_name or node_name.endswith(dep_kind):
                target_name = node_name
                break

        target_id = _infra_id(target_name)
        key = f"{source_id}->{target_id}"
        if key in seen:
            continue
        seen.add(key)

        edges.append(
            InfraEdge(
                source=source_id,
                target=target_id,
                edge_type="k8s-depends",
                via="env",
                transport=transport,
            )
        )
    return edges


# ---------------------------------------------------------------------------
# Kustomize parser
# ---------------------------------------------------------------------------


def _parse_kustomization_resources(
    kustom_path: Path,
    warnings: list[str],
) -> list[str]:
    """Return the list of resource paths declared in a kustomization.yaml."""
    data = _safe_load_yaml(kustom_path, warnings)
    if data is None:
        return []
    resources = data.get("resources", []) or []
    return [str(r) for r in resources if r]


def _find_workload_manifest(resource_dir: Path) -> Optional[Path]:
    """Find the primary workload manifest inside a resource directory."""
    candidates = (
        "deployment.yaml",
        "deployment.yml",
        "statefulset.yaml",
        "statefulset.yml",
    )
    for name in candidates:
        p = resource_dir / name
        if p.is_file():
            return p
    return None


def _find_service_manifest(resource_dir: Path) -> Optional[Path]:
    """Find the service manifest inside a resource directory."""
    for name in ("service.yaml", "service.yml"):
        p = resource_dir / name
        if p.is_file():
            return p
    return None


def _parse_service_port(service_path: Path, warnings: list[str]) -> tuple[int, str]:
    """Return (port, namespace) from a Service manifest."""
    data = _safe_load_yaml(service_path, warnings)
    if data is None:
        return (0, "")
    namespace: str = (data.get("metadata") or {}).get("namespace", "")
    ports: list[dict] = ((data.get("spec") or {}).get("ports") or [])
    port: int = 0
    if ports:
        port = int(ports[0].get("port", 0))
    return (port, namespace)


def _extract_container_env(container: dict) -> list[dict]:
    """Return the flat env list from a container spec (env only, not envFrom)."""
    return container.get("env", []) or []


def parse_kustomize(root: Path) -> TopologyResult:
    """Scan root for kustomization.yaml files and extract infra topology."""
    result = TopologyResult()

    kustom_files: list[Path] = []
    for d in _walk_dirs(root):
        for fname in KUSTOMIZE_FILENAMES:
            p = d / fname
            if p.is_file():
                kustom_files.append(p)

    if not kustom_files:
        return result

    # First pass: collect all node names for dependency resolution
    workload_data: list[tuple[Path, Path, dict]] = []  # (kustom_path, manifest, data)
    for kustom_path in kustom_files:
        kustom_dir = kustom_path.parent
        for resource_str in _parse_kustomization_resources(kustom_path, result.warnings):
            resource_dir = kustom_dir / resource_str
            if not resource_dir.is_dir():
                # Could be a direct file reference
                continue
            manifest = _find_workload_manifest(resource_dir)
            if manifest is None:
                continue
            data = _safe_load_yaml(manifest, result.warnings)
            if data and data.get("kind") in ("Deployment", "StatefulSet"):
                workload_data.append((kustom_path, manifest, data))

    # Collect known names for env heuristic resolution
    known_names: set[str] = set()
    for _, _, data in workload_data:
        name = (data.get("metadata") or {}).get("name", "")
        if name:
            known_names.add(name)
    known_names_frozen = frozenset(known_names)

    # Second pass: emit nodes and edges
    for kustom_path, manifest, data in workload_data:
        kustom_dir = kustom_path.parent
        k8s_kind = data.get("kind", "Deployment").lower()
        metadata = data.get("metadata", {}) or {}
        name: str = metadata.get("name", "")
        if not name:
            continue

        labels = metadata.get("labels", {}) or {}
        component: str = labels.get("app.kubernetes.io/component", "")

        spec = data.get("spec", {}) or {}
        template = spec.get("template", {}) or {}
        pod_spec = template.get("spec", {}) or {}
        containers: list[dict] = pod_spec.get("containers", []) or []
        if not containers:
            continue

        first_container = containers[0]
        image: str = first_container.get("image", "")
        ports: list[dict] = first_container.get("ports", []) or []
        port: int = int(ports[0].get("containerPort", 0)) if ports else 0

        node_id = _infra_id(name)
        result.nodes.append(
            InfraNode(
                id=node_id,
                kind=k8s_kind,
                image=image,
                port=port,
                component=component,
                source_file=str(manifest.relative_to(root) if manifest.is_relative_to(root) else manifest),
            )
        )

        # Service manifest for k8s-service fact
        # Determine the resource dir from the manifest's parent
        resource_dir = manifest.parent
        svc_manifest = _find_service_manifest(resource_dir)
        if svc_manifest:
            svc_port, namespace = _parse_service_port(svc_manifest, result.warnings)
            if svc_port == 0:
                svc_port = port
            result.edges.append(
                InfraEdge(
                    source=node_id,
                    target=name,
                    edge_type="k8s-service",
                    via="service-manifest",
                    transport="",
                    extra=namespace,
                )
            )

        # Env-var dependency edges
        env_list = _extract_container_env(first_container)
        dep_edges = _extract_env_deps(env_list, node_id, known_names_frozen)
        result.edges.extend(dep_edges)

        # infra-maps cross-layer edge (image name -> source code node)
        if image:
            image_base = image.split(":")[0].split("/")[-1]
            if image_base:
                result.edges.append(
                    InfraEdge(
                        source=node_id,
                        target=image_base,
                        edge_type="infra-maps",
                        via="image-name",
                        transport="",
                    )
                )

    return result


# ---------------------------------------------------------------------------
# Helm parser
# ---------------------------------------------------------------------------


def _parse_chart_yaml(chart_path: Path, warnings: list[str]) -> Optional[dict]:
    """Load and return Chart.yaml contents."""
    return _safe_load_yaml(chart_path, warnings)


def _parse_values_yaml(values_path: Path, warnings: list[str]) -> dict:
    """Load values.yaml; return empty dict on failure."""
    if not values_path.is_file():
        return {}
    data = _safe_load_yaml(values_path, warnings)
    return data if isinstance(data, dict) else {}


def _extract_helm_image(values: dict) -> str:
    """Extract image string from values.yaml using common conventions."""
    image_section = values.get("image", {}) or {}
    if isinstance(image_section, dict):
        repo = image_section.get("repository", "")
        tag = image_section.get("tag", "")
        if repo:
            return f"{repo}:{tag}" if tag else repo
    return ""


def _extract_helm_port(values: dict) -> int:
    """Extract primary service port from values.yaml."""
    svc = values.get("service", {}) or {}
    if isinstance(svc, dict):
        return int(svc.get("port", 0))
    return 0


def _extract_helm_env_deps(
    values: dict,
    source_id: str,
    known_chart_names: frozenset[str],
) -> list[InfraEdge]:
    """Infer service dependencies from env entries in values.yaml."""
    env_section = values.get("env", {}) or {}
    if not isinstance(env_section, dict):
        return []
    env_list = [{"name": k, "value": str(v)} for k, v in env_section.items()]
    return _extract_env_deps(env_list, source_id, known_chart_names)


def parse_helm(root: Path) -> TopologyResult:
    """Scan root for Chart.yaml files and extract Helm chart topology."""
    result = TopologyResult()

    chart_files: list[Path] = []
    for d in _walk_dirs(root):
        p = d / HELM_CHART_FILENAME
        if p.is_file():
            chart_files.append(p)

    if not chart_files:
        return result

    # Collect chart names for cross-reference resolution
    chart_names: dict[str, Path] = {}
    chart_data_map: dict[Path, dict] = {}
    for chart_path in chart_files:
        data = _parse_chart_yaml(chart_path, result.warnings)
        if data is None:
            continue
        chart_name: str = data.get("name", "")
        if chart_name:
            chart_names[chart_name] = chart_path
            chart_data_map[chart_path] = data

    known_chart_names = frozenset(chart_names.keys())

    for chart_path, data in chart_data_map.items():
        chart_dir = chart_path.parent
        chart_name = data.get("name", "")
        if not chart_name:
            continue

        values = _parse_values_yaml(chart_dir / "values.yaml", result.warnings)
        image = _extract_helm_image(values)
        port = _extract_helm_port(values)

        node_id = _infra_id(chart_name)
        result.nodes.append(
            InfraNode(
                id=node_id,
                kind="helm-chart",
                image=image,
                port=port,
                component="",
                source_file=str(chart_path.relative_to(root) if chart_path.is_relative_to(root) else chart_path),
            )
        )

        # Chart dependency edges
        dependencies: list[dict] = data.get("dependencies", []) or []
        for dep in dependencies:
            dep_name: str = dep.get("name", "")
            if not dep_name:
                continue
            target_id = _infra_id(dep_name)
            result.edges.append(
                InfraEdge(
                    source=node_id,
                    target=target_id,
                    edge_type="helm-depends",
                    via="chart-dependency",
                    transport="",
                )
            )

        # Env-var dependency edges from values
        env_edges = _extract_helm_env_deps(values, node_id, known_chart_names)
        result.edges.extend(env_edges)

        # infra-maps cross-layer edge
        if image:
            image_base = image.split(":")[0].split("/")[-1]
            if image_base:
                result.edges.append(
                    InfraEdge(
                        source=node_id,
                        target=image_base,
                        edge_type="infra-maps",
                        via="image-name",
                        transport="",
                    )
                )

    return result


# ---------------------------------------------------------------------------
# Terraform parser (regex-based, no hcl2 dependency)
# ---------------------------------------------------------------------------


def _read_tf_files(tf_dir: Path) -> str:
    """Concatenate all .tf files in a directory into one string."""
    parts: list[str] = []
    for p in sorted(tf_dir.glob("*.tf")):
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


def _parse_tf_modules(content: str) -> dict[str, str]:
    """Return {module_name: source_path} from HCL content."""
    modules: dict[str, str] = {}
    for m in _TF_MODULE_BLOCK_RE.finditer(content):
        mod_name = m.group(1)
        body = m.group(2)
        src_match = _TF_MODULE_SOURCE_RE.search(body)
        source = src_match.group(1) if src_match else ""
        modules[mod_name] = source
    return modules


def _parse_tf_resources(content: str) -> list[tuple[str, str]]:
    """Return [(resource_type, resource_name)] from HCL content."""
    return [
        (m.group(1), m.group(2))
        for m in _TF_RESOURCE_BLOCK_RE.finditer(content)
    ]


def _find_cross_module_refs(
    content: str,
    all_module_names: frozenset[str],
) -> set[str]:
    """Return set of module names referenced via module.<name>. syntax."""
    refs: set[str] = set()
    for m in _TF_MODULE_REF_RE.finditer(content):
        ref_name = m.group(1)
        if ref_name in all_module_names:
            refs.add(ref_name)
    return refs


def parse_terraform(root: Path) -> TopologyResult:
    """Scan root for Terraform directories and extract module topology."""
    result = TopologyResult()

    # Find all directories containing main.tf
    tf_dirs: list[Path] = []
    for d in _walk_dirs(root):
        if (d / TERRAFORM_MAIN_FILENAME).is_file():
            tf_dirs.append(d)

    if not tf_dirs:
        return result

    result.warnings.append(
        "; warning: terraform parsed with regex fallback (no hcl2 dependency)"
    )

    # Each tf_dir is an independent Terraform root; we treat module blocks as nodes
    # and cross-module references as edges within the same root.
    for tf_dir in tf_dirs:
        content = _read_tf_files(tf_dir)
        if not content.strip():
            continue

        modules = _parse_tf_modules(content)
        resources = _parse_tf_resources(content)
        all_module_names = frozenset(modules.keys())

        rel_dir = str(tf_dir.relative_to(root) if tf_dir.is_relative_to(root) else tf_dir)

        # If there are resource blocks, emit per-resource nodes
        for res_type, res_name in resources:
            node_name = f"tf-{res_name}"
            node_id = _infra_id(node_name)
            result.nodes.append(
                InfraNode(
                    id=node_id,
                    kind="terraform",
                    image="",
                    port=0,
                    component=res_type,
                    source_file=rel_dir,
                )
            )

        # Emit a node for each module block
        for mod_name in modules:
            node_name = f"tf-{mod_name}"
            node_id = _infra_id(node_name)
            result.nodes.append(
                InfraNode(
                    id=node_id,
                    kind="terraform",
                    image="",
                    port=0,
                    component="module",
                    source_file=rel_dir,
                )
            )

        # Find cross-module dependencies: for each module block, scan its body
        # for references to other modules
        for m in _TF_MODULE_BLOCK_RE.finditer(content):
            src_mod_name = m.group(1)
            body = m.group(2)
            refs = _find_cross_module_refs(body, all_module_names - {src_mod_name})
            src_id = _infra_id(f"tf-{src_mod_name}")
            for ref in sorted(refs):
                tgt_id = _infra_id(f"tf-{ref}")
                result.edges.append(
                    InfraEdge(
                        source=src_id,
                        target=tgt_id,
                        edge_type="tf-depends",
                        via="module-reference",
                        transport="",
                    )
                )

    return result


# ---------------------------------------------------------------------------
# Sexp writer
# ---------------------------------------------------------------------------


def _quote(s: str) -> str:
    """Wrap s in double quotes, escaping inner quotes and backslashes."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _needs_quoting(s: str) -> bool:
    """Return True if s contains whitespace or sexp-special characters."""
    return bool(re.search(r'[\s"()\\]', s))


def _atom(s: str) -> str:
    """Return s as a sexp atom, quoting if necessary."""
    if not s:
        return '""'
    return _quote(s) if _needs_quoting(s) else s


def render_sexp(nodes: list[InfraNode], edges: list[InfraEdge]) -> list[str]:
    """Return a list of sexp lines representing the infra topology."""
    lines: list[str] = ["", "; --- infrastructure topology ---"]

    # Deduplicate nodes by id, keeping first occurrence
    seen_nodes: set[str] = set()
    unique_nodes: list[InfraNode] = []
    for node in nodes:
        if node.id not in seen_nodes:
            seen_nodes.add(node.id)
            unique_nodes.append(node)

    for node in sorted(unique_nodes, key=lambda n: n.id):
        parts: list[str] = [f"(infra-node {node.id}"]
        parts.append(f":kind {node.kind}")
        if node.image:
            parts.append(f":image {_atom(node.image)}")
        if node.port:
            parts.append(f":port {node.port}")
        if node.component:
            parts.append(f":component {_atom(node.component)}")
        lines.append(" ".join(parts) + ")")

    if unique_nodes:
        lines.append("")

    # Deduplicate edges by (source, target, edge_type, via)
    seen_edges: set[tuple[str, str, str, str]] = set()
    unique_edges: list[InfraEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.edge_type, edge.via)
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(edge)

    for edge in sorted(unique_edges, key=lambda e: (e.edge_type, e.source, e.target)):
        if edge.edge_type == "k8s-depends":
            transport_str = f" :transport {edge.transport}" if edge.transport else ""
            lines.append(
                f"({edge.edge_type} {edge.source} {edge.target}"
                f" :via {edge.via}{transport_str})"
            )
        elif edge.edge_type == "k8s-service":
            ns_str = f" :namespace {_atom(edge.extra)}" if edge.extra else ""
            # target holds the dns service name for k8s-service facts
            lines.append(
                f"(k8s-service {edge.source} {_atom(edge.target)}{ns_str})"
            )
        elif edge.edge_type == "helm-depends":
            lines.append(
                f"(helm-depends {edge.source} {edge.target} :via {edge.via})"
            )
        elif edge.edge_type == "tf-depends":
            lines.append(
                f"(tf-depends {edge.source} {edge.target} :via {edge.via})"
            )
        elif edge.edge_type == "infra-maps":
            lines.append(
                f"(infra-maps {edge.source} {_atom(edge.target)} :via {edge.via})"
            )

    return lines


# ---------------------------------------------------------------------------
# Merge results
# ---------------------------------------------------------------------------


def merge_results(results: list[TopologyResult]) -> TopologyResult:
    """Combine multiple TopologyResult instances into one."""
    merged = TopologyResult()
    for r in results:
        merged.nodes.extend(r.nodes)
        merged.edges.extend(r.edges)
        merged.warnings.extend(r.warnings)
    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> tuple[Path, bool, bool, bool, Optional[Path], bool]:
    """Parse CLI arguments.

    Returns (root, do_kustomize, do_helm, do_terraform, append_file, stdout_mode).
    """
    root = Path(".")
    do_kustomize: Optional[bool] = None
    do_helm: Optional[bool] = None
    do_terraform: Optional[bool] = None
    append_file: Optional[Path] = None
    stdout_mode = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--kustomize":
            do_kustomize = True
        elif arg == "--helm":
            do_helm = True
        elif arg == "--terraform":
            do_terraform = True
        elif arg == "--stdout":
            stdout_mode = True
        elif arg == "--append":
            i += 1
            if i < len(argv):
                append_file = Path(argv[i])
        elif not arg.startswith("--"):
            root = Path(arg)
        i += 1

    # Auto-detect mode: run all parsers unless explicitly requested
    if do_kustomize is None and do_helm is None and do_terraform is None:
        do_kustomize = True
        do_helm = True
        do_terraform = True

    return (
        root,
        bool(do_kustomize),
        bool(do_helm),
        bool(do_terraform),
        append_file,
        stdout_mode or (append_file is None),
    )


def main() -> None:
    root, do_kustomize, do_helm, do_terraform, append_file, stdout_mode = _parse_args(
        sys.argv[1:]
    )

    if not root.is_dir():
        print(f"Error: root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    results: list[TopologyResult] = []

    if do_kustomize:
        results.append(parse_kustomize(root))
    if do_helm:
        results.append(parse_helm(root))
    if do_terraform:
        results.append(parse_terraform(root))

    merged = merge_results(results)

    # Emit warnings to stderr
    for w in merged.warnings:
        print(w, file=sys.stderr)

    if not merged.nodes and not merged.edges:
        empty_message = "; info: no supported Kustomize, Helm, or Terraform files found"
        print(empty_message, file=sys.stderr)
        if not stdout_mode and append_file is None:
            return
        # Still write the comment so callers know the script ran
        output_lines = ["", "; --- infrastructure topology ---", empty_message]
    else:
        output_lines = render_sexp(merged.nodes, merged.edges)

    output_text = "\n".join(output_lines) + "\n"

    if stdout_mode:
        sys.stdout.write(output_text)

    if append_file is not None:
        try:
            with append_file.open("a", encoding="utf-8") as fh:
                fh.write(output_text)
        except OSError as exc:
            print(f"Error: could not append to {append_file}: {exc}", file=sys.stderr)
            sys.exit(1)

    node_count = len({n.id for n in merged.nodes})
    edge_count = len(merged.edges)
    print(
        f"[infra-topology] Done: {node_count} nodes, {edge_count} edges",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
