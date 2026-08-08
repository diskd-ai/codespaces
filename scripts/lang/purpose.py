"""Language-shared architectural purpose inference."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from .interface import Entity, EntityPayload


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _fuzzy_suffix_match(name: str, suffix: str, max_dist: int = 1) -> bool:
    """Check if the tail of `name` fuzzy-matches `suffix` within edit distance."""
    if len(suffix) < 6:
        return False  # too short for fuzzy -- "Store"/"Storey" false positives
    tail = name[-len(suffix) - max_dist:]  # grab suffix + slack for insertions
    return _levenshtein(tail.lower(), suffix.lower()) <= max_dist


def infer_purpose(
    path: str,
    content: str,
    language: str,
    entities: Sequence[Entity | EntityPayload] = (),
) -> str:
    """Infer architectural purpose from path, content, and entity names."""
    parts = []
    rel = path.lower()

    # Path-based heuristics
    if "/domain/" in rel:
        parts.append("domain logic")
    elif "/infrastructure/" in rel or "/infra/" in rel:
        parts.append("infrastructure adapter")
    elif "/api/" in rel:
        parts.append("API layer")
    elif "/app/" in rel or "/application/" in rel:
        parts.append("application service")
    elif "/components/" in rel or "/ui/" in rel:
        parts.append("UI component")
    elif "/hooks/" in rel:
        parts.append("React hook")
    elif "/pages/" in rel or "/routes/" in rel:
        parts.append("page/route")
    elif "/migrations/" in rel:
        parts.append("database migration")
    elif "/test" in rel or ".test." in rel or ".spec." in rel:
        parts.append("test")
    elif "/config" in rel:
        parts.append("configuration")
    elif "/utils/" in rel or "/helpers/" in rel or "/commons/" in rel:
        parts.append("shared utilities")
    elif "/repositories/" in rel or "/repo/" in rel:
        parts.append("repository")
    elif "/services/" in rel:
        parts.append("service")
    elif "/dto/" in rel:
        parts.append("DTO")
    elif "/guards/" in rel or "guard" in rel:
        parts.append("access guard")
    elif "/store/" in rel or "/stores/" in rel:
        parts.append("state store")
    elif "/schema" in rel:
        parts.append("schema")
    elif "/sdk/" in rel:
        parts.append("SDK")
    elif "/api-client/" in rel or "/api_client/" in rel:
        parts.append("API client")

    # Docstring extraction
    head = content[:2048]
    if language == "python":
        match = re.search(r'^"""(.*?)"""', head, re.DOTALL)
        if not match:
            match = re.search(r"^'''(.*?)'''", head, re.DOTALL)
        if match:
            parts.append(match.group(1).strip().split("\n")[0][:120])
    else:
        match = re.search(r"/\*\*\s*\n?\s*\*?\s*(.+?)(?:\n|\*/)", head)
        if match:
            parts.append(match.group(1).strip()[:120])

    # Entity-name heuristics (Gap 3 fix: reduces "general module" fallback)
    # -- Suffix -> (purpose label, dedup keyword) --
    _ENTITY_SUFFIX_MAP = [
        # Core architecture roles
        ("Service", "service", "service"),
        ("Controller", "API controller", "controller"),
        ("Repository", "repository", "repository"),
        ("Repo", "repository", "repository"),
        ("Gateway", "gateway", "gateway"),
        ("Registry", "registry", "registry"),
        ("Store", "state store", "store"),
        ("Guard", "access guard", "guard"),
        ("Module", "module", "module"),
        ("Adapter", "adapter", "adapter"),
        ("Provider", "provider", "provider"),
        ("Processor", "processor", "processor"),
        ("Agent", "agent", "agent"),
        ("Resolver", "resolver", "resolver"),
        ("Scheduler", "scheduler", "scheduler"),
        ("Worker", "worker", "worker"),
        ("Client", "client", "client"),
        ("Connector", "connector", "connector"),
        ("Manager", "manager", "manager"),
        ("Subscriber", "subscriber", "subscriber"),
        ("Dispatcher", "dispatcher", "dispatcher"),
        ("Emitter", "emitter", "emitter"),
        ("Interceptor", "interceptor", "interceptor"),
        ("Pipe", "validation pipe", "pipe"),
        ("Handler", "handler", "handler"),
        ("Listener", "event listener", "listener"),
        # GoF patterns
        ("Factory", "Factory pattern", "factory"),
        ("Builder", "Builder pattern", "builder"),
        ("Singleton", "Singleton pattern", "singleton"),
        ("Observer", "Observer pattern", "observer"),
        ("Strategy", "Strategy pattern", "strategy"),
        ("Command", "Command pattern", "command"),
        ("Mediator", "Mediator pattern", "mediator"),
        ("Visitor", "Visitor pattern", "visitor"),
        ("Decorator", "Decorator pattern", "decorator"),
        ("Proxy", "Proxy pattern", "proxy"),
        ("Facade", "Facade pattern", "facade"),
        ("Composite", "Composite pattern", "composite"),
        ("Iterator", "Iterator pattern", "iterator"),
        ("ChainHandler", "Chain of Responsibility", "chain"),
        # Fowler / DDD patterns
        ("Specification", "Specification pattern", "specification"),
        ("Spec", "Specification pattern", "specification"),
        ("ValueObject", "value object", "value"),
        ("Aggregate", "aggregate root", "aggregate"),
        ("UnitOfWork", "Unit of Work", "unitofwork"),
        ("DomainEvent", "domain event", "domainevent"),
        ("IdentityMap", "Identity Map", "identitymap"),
        ("Policy", "policy", "policy"),
        ("Plugin", "plugin", "plugin"),
        # SDK / API
        ("Sdk", "SDK", "sdk"),
        ("SDK", "SDK", "sdk"),
        ("Api", "API client", "api client"),
        ("API", "API client", "api client"),
    ]

    if entities:
        joined_lower = " ".join(parts).lower()
        for ent in entities:
            ename = ent.get("name", "") if isinstance(ent, dict) else ent.name
            ekind = ent.get("kind", "") if isinstance(ent, dict) else ent.kind
            if ekind not in ("class", "interface"):
                continue
            # Exact suffix match first
            matched = False
            for suffix, purpose_label, dedup_kw in _ENTITY_SUFFIX_MAP:
                if ename.endswith(suffix):
                    if dedup_kw not in joined_lower:
                        parts.append(purpose_label)
                        joined_lower = " ".join(parts).lower()
                    matched = True
                    break
            # Fuzzy suffix fallback (distance <= 1, suffixes >= 4 chars)
            if not matched and len(ename) >= 5:
                for suffix, purpose_label, dedup_kw in _ENTITY_SUFFIX_MAP:
                    if _fuzzy_suffix_match(ename, suffix):
                        if dedup_kw not in joined_lower:
                            parts.append(purpose_label)
                            joined_lower = " ".join(parts).lower()
                        break

    # Pattern detection -- only flag patterns when the file DEFINES one,
    # not when it merely imports from a module named factory/strategy.
    joined_check = " ".join(parts).lower()

    # GoF Creational
    if "factory" not in joined_check:
        if re.search(r"class \w*Factory|def (?:create|make|build)_\w+.*->|def get_\w+.*factory", head):
            parts.append("Factory pattern")
    if "strategy" not in joined_check:
        if re.search(r"class \w*Strategy|strategy_map|STRATEGIES", head):
            parts.append("Strategy pattern")
    if "builder" not in joined_check:
        if re.search(r"class \w+Builder\b", head):
            parts.append("Builder pattern")
    if "singleton" not in joined_check:
        if re.search(r"getInstance\s*\(|_instance\s*[:=]|private\s+static\s+instance", head):
            parts.append("Singleton pattern")

    # GoF Structural
    if "composite" not in joined_check:
        if re.search(r"class \w+Composite\b|addChild\s*\(|getChildren\s*\(", head):
            parts.append("Composite pattern")
    if "proxy" not in joined_check:
        if re.search(r"class \w+Proxy\b", head):
            parts.append("Proxy pattern")
    if "facade" not in joined_check:
        if re.search(r"class \w+Facade\b", head):
            parts.append("Facade pattern")

    # GoF Behavioral
    if "observer" not in joined_check:
        if re.search(r"class \w+Observer\b|\.subscribe\s*\(|EventEmitter", head):
            parts.append("Observer pattern")
    if "command" not in joined_check:
        if re.search(r"class \w+Command\b", head):
            parts.append("Command pattern")
    if "mediator" not in joined_check:
        if re.search(r"class \w+Mediator\b", head):
            parts.append("Mediator pattern")
    if "visitor" not in joined_check:
        if re.search(r"class \w+Visitor\b|\.accept\s*\(\s*visitor", head):
            parts.append("Visitor pattern")
    if "chain" not in joined_check:
        if re.search(r"class \w+Chain\w*\b|setNext\s*\(|handleNext\s*\(", head):
            parts.append("Chain of Responsibility")
    if "memento" not in joined_check:
        if re.search(r"class \w+Memento\b", head):
            parts.append("Memento pattern")

    # Fowler / DDD patterns
    if "specification" not in joined_check:
        if re.search(r"class \w+Specification\b|isSatisfiedBy\s*\(", head):
            parts.append("Specification pattern")
    if "aggregate" not in joined_check:
        if re.search(r"class \w+Aggregate\b", head):
            parts.append("aggregate root")
    if "unitofwork" not in joined_check.replace(" ", ""):
        if re.search(r"class \w+UnitOfWork\b", head):
            parts.append("Unit of Work")
    if "domainevent" not in joined_check.replace(" ", ""):
        if re.search(r"class \w+DomainEvent\b|class \w+Event\b(?!Emitter|Handler|Listener)", head):
            parts.append("domain event")
    if "identitymap" not in joined_check.replace(" ", ""):
        if re.search(r"class \w+IdentityMap\b", head):
            parts.append("Identity Map")
    if "value object" not in joined_check:
        if re.search(r"class \w+ValueObject\b", head):
            parts.append("value object")

    # NestJS decorators -- only label when entity-name heuristics didn't
    # provide a more specific purpose (controller, service, gateway, etc.)
    _specific_kws = {"controller", "service", "repository", "guard", "gateway",
                     "adapter", "provider", "processor", "registry", "agent",
                     "resolver", "scheduler", "worker", "store", "handler",
                     "interceptor", "pipe", "listener", "subscriber",
                     "dispatcher", "emitter", "factory", "builder", "observer",
                     "strategy", "command", "mediator", "visitor", "proxy",
                     "facade", "composite", "specification", "aggregate",
                     "policy", "plugin", "manager", "client", "connector",
                     "sdk", "api client"}
    joined_after = " ".join(parts).lower()
    has_specific = any(kw in joined_after for kw in _specific_kws)
    if not has_specific:
        if re.search(r"@Controller\b", head):
            parts.append("API controller")
        elif re.search(r"@Module\b", head):
            parts.append("NestJS wiring module")
        elif re.search(r"@Injectable\b", head):
            parts.append("NestJS injectable")

    if re.search(r"Router|router\s*\(|createRouter", head):
        if "router" not in " ".join(parts).lower():
            parts.append("router")
    if re.search(r"middleware|Middleware", head):
        if "middleware" not in " ".join(parts).lower():
            parts.append("middleware")

    # --- File-stem based heuristics ---
    stem = Path(path).stem.lower()
    for sfx in (".test", ".spec", ".e2e", ".stories", ".d"):
        if stem.endswith(sfx):
            stem = stem[: -len(sfx)]

    if not parts:
        if stem.endswith("dto") or stem.endswith("dtos"):
            parts.append("data transfer object")
        elif stem.endswith("mapper") or stem.endswith("mappers"):
            parts.append("data mapper")
        elif stem.endswith("helpers") or stem.endswith("helper") or stem.endswith("utils") or stem.endswith("util"):
            parts.append("utility helpers")
        elif stem.endswith("constants") or stem.endswith("const"):
            parts.append("constants")
        elif stem.endswith("types") or stem.endswith("type"):
            parts.append("type definitions")
        elif stem.endswith("interfaces") or stem.endswith("interface"):
            parts.append("interface definitions")
        elif stem.endswith("enums") or stem.endswith("enum"):
            parts.append("enum definitions")
        elif stem.endswith("models") or stem.endswith("model"):
            parts.append("data model")
        elif stem.endswith("entities") or stem.endswith("entity"):
            parts.append("domain entity")
        elif stem.endswith("errors") or stem.endswith("error") or stem.endswith("exceptions") or stem.endswith("exception"):
            parts.append("error definitions")
        elif stem.endswith("factories") or stem.endswith("factory"):
            parts.append("factory")
        elif stem.endswith("validators") or stem.endswith("validator"):
            parts.append("validation")
        elif stem.endswith("handlers") or stem.endswith("handler"):
            parts.append("event handler")
        elif stem.endswith("listeners") or stem.endswith("listener"):
            parts.append("event listener")
        elif stem.endswith("middleware"):
            parts.append("middleware")
        elif stem.endswith("interceptor"):
            parts.append("interceptor")
        elif stem.endswith("guard"):
            parts.append("access guard")
        elif stem.endswith("pipe"):
            parts.append("validation pipe")
        elif stem.endswith("decorators") or stem.endswith("decorator"):
            parts.append("decorator")
        elif stem.endswith("migration"):
            parts.append("database migration")
        elif stem.endswith("seed") or stem.endswith("seeder"):
            parts.append("database seed")
        elif stem.endswith("fixtures") or stem.endswith("fixture"):
            parts.append("test fixture")
        elif stem.endswith("mocks") or stem.endswith("mock"):
            parts.append("test mock")
        # Core architecture roles (stem-based)
        elif stem.endswith("controller") or stem.endswith("controllers"):
            parts.append("API controller")
        elif stem.endswith("service") or stem.endswith("services"):
            parts.append("service")
        elif stem.endswith("repository") or stem.endswith("repositories"):
            parts.append("repository")
        elif stem.endswith("gateway") or stem.endswith("gateways"):
            parts.append("gateway")
        elif stem.endswith("registry") or stem.endswith("registries"):
            parts.append("registry")
        elif stem.endswith("agent") or stem.endswith("agents"):
            parts.append("agent")
        elif stem.endswith("store") or stem.endswith("stores"):
            parts.append("state store")
        elif stem.endswith("policy") or stem.endswith("policies"):
            parts.append("policy")
        elif stem.endswith("resolver") or stem.endswith("resolvers"):
            parts.append("resolver")
        elif stem.endswith("scheduler") or stem.endswith("schedulers"):
            parts.append("scheduler")
        elif stem.endswith("worker") or stem.endswith("workers"):
            parts.append("worker")
        elif stem.endswith("client") or stem.endswith("clients"):
            parts.append("client")
        elif stem.endswith("connector") or stem.endswith("connectors"):
            parts.append("connector")
        elif stem.endswith("manager") or stem.endswith("managers"):
            parts.append("manager")
        elif stem.endswith("subscriber") or stem.endswith("subscribers"):
            parts.append("subscriber")
        elif stem.endswith("dispatcher") or stem.endswith("dispatchers"):
            parts.append("dispatcher")
        elif stem.endswith("emitter") or stem.endswith("emitters"):
            parts.append("emitter")
        elif stem.endswith("adapter") or stem.endswith("adapters"):
            parts.append("adapter")
        elif stem.endswith("provider") or stem.endswith("providers"):
            parts.append("provider")
        elif stem.endswith("processor") or stem.endswith("processors"):
            parts.append("processor")
        # GoF pattern stems
        elif stem.endswith("observer"):
            parts.append("Observer pattern")
        elif stem.endswith("builder"):
            parts.append("Builder pattern")
        elif stem.endswith("visitor"):
            parts.append("Visitor pattern")
        elif stem.endswith("mediator"):
            parts.append("Mediator pattern")
        elif stem.endswith("facade"):
            parts.append("Facade pattern")
        elif stem.endswith("proxy"):
            parts.append("Proxy pattern")
        elif stem.endswith("strategy") or stem.endswith("strategies"):
            parts.append("Strategy pattern")
        elif stem.endswith("command") or stem.endswith("commands"):
            parts.append("Command pattern")
        elif stem.endswith("singleton"):
            parts.append("Singleton pattern")
        elif stem.endswith("composite"):
            parts.append("Composite pattern")
        # Fowler / DDD pattern stems
        elif stem.endswith("aggregate") or stem.endswith("aggregates"):
            parts.append("aggregate root")
        elif stem.endswith("specification") or stem.endswith("specifications"):
            parts.append("Specification pattern")
        elif stem.endswith("plugin") or stem.endswith("plugins"):
            parts.append("plugin")
        elif stem.endswith("sdk"):
            parts.append("SDK")
        elif stem.endswith("api"):
            parts.append("API client")
        elif stem in ("index", "__init__"):
            parts.append("module entry point")
        elif stem in ("main", "app"):
            parts.append("application entry point")
        elif stem in ("setup", "bootstrap"):
            parts.append("application bootstrap")

    # --- Fuzzy stem fallback (distance <= 1, stems >= 4 chars) ---
    _STEM_SUFFIX_MAP = [
        ("controller", "API controller"), ("service", "service"),
        ("repository", "repository"), ("gateway", "gateway"),
        ("registry", "registry"), ("agent", "agent"),
        ("store", "state store"), ("factory", "factory"),
        ("strategy", "Strategy pattern"), ("observer", "Observer pattern"),
        ("builder", "Builder pattern"), ("command", "Command pattern"),
        ("mediator", "Mediator pattern"), ("visitor", "Visitor pattern"),
        ("facade", "Facade pattern"), ("proxy", "Proxy pattern"),
        ("composite", "Composite pattern"), ("aggregate", "aggregate root"),
        ("specification", "Specification pattern"), ("adapter", "adapter"),
        ("provider", "provider"), ("processor", "processor"),
        ("handler", "event handler"), ("listener", "event listener"),
        ("middleware", "middleware"), ("interceptor", "interceptor"),
        ("scheduler", "scheduler"), ("worker", "worker"),
        ("dispatcher", "dispatcher"), ("subscriber", "subscriber"),
        ("resolver", "resolver"), ("manager", "manager"),
        ("connector", "connector"), ("client", "client"),
        ("policy", "policy"), ("plugin", "plugin"),
    ]
    if not parts and len(stem) >= 5:
        for sfx, purpose_label in _STEM_SUFFIX_MAP:
            if _fuzzy_suffix_match(stem, sfx):
                parts.append(purpose_label)
                break

    # --- Content-based heuristics (checked against first 4K) ---
    head4k = content[:4096]
    if not parts:
        if re.search(r"export\s+(?:const\s+)?enum\s+", head4k):
            parts.append("enum definitions")
        elif re.search(r"export\s+(?:type|interface)\s+", head4k) and not re.search(
            r"export\s+(?:class|function|const\s+\w+\s*=)", head4k
        ):
            parts.append("type definitions")
        elif re.search(r"^export\s+\{", head4k, re.MULTILINE) and not re.search(
            r"^(?:class|function|const|let|var)\s", head4k, re.MULTILINE
        ):
            parts.append("barrel re-exports")
        elif re.search(r"export\s+(?:default\s+)?(?:const\s+)?(?:config|CONFIG|configuration)\b", head4k):
            parts.append("configuration")
        elif (
            language == "python"
            and re.search(r"^from\s+\.\w+\s+import\s+", head4k, re.MULTILINE)
            and len(content) < 500
        ):
            parts.append("package re-exports")

    # --- Entity-kind heuristic: only type/interface/enum entities => type definitions ---
    if not parts and entities:
        kinds = {
            (ent.get("kind", "") if isinstance(ent, dict) else ent.kind)
            for ent in entities
        }
        if kinds and kinds <= {"interface", "type", "enum"}:
            if kinds == {"enum"}:
                parts.append("enum definitions")
            else:
                parts.append("type definitions")

    return "; ".join(parts) if parts else "general module"
