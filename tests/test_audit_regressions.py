import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_belief_map.py"


def _load_script(module_name: str, relative_path: str) -> ModuleType:
    """Load a repository script so focused tests exercise the checked-out code."""
    script_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load test module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_belief_map = _load_script("audit_build_belief_map", "scripts/build_belief_map.py")
belief_search = _load_script("audit_belief_search", "scripts/belief_search.py")
build_infra_topology = _load_script(
    "audit_build_infra_topology",
    "scripts/build_infra_topology.py",
)


class AuditRegressionTest(unittest.TestCase):
    """Protect the audit findings that have small, locally owned fixes."""

    def _parse_typescript(
        self,
        root: Path,
        relative_path: str,
        content: str,
    ) -> object:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result = build_belief_map.parse_file((str(path), "typescript", "fixture"))
        self.assertIsNotNone(result)
        return result

    def _import_edges(self, results: list[object], root: Path) -> set[tuple[str, str]]:
        _nodes, edges = build_belief_map.build_graph(results, str(root))
        return {
            (edge["source"], edge["target"])
            for edge in edges
            if edge["type"] == "IMPORTS"
        }

    def test_file_hash_covers_changes_after_first_eight_kibibytes(self) -> None:
        """/* REQ-CS-007: cache hashes must cover the complete source file. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "large.py"
            source_path.write_bytes((b"a" * 9000) + b"x")
            before = build_belief_map.file_hash(str(source_path))

            source_path.write_bytes((b"a" * 9000) + b"y")
            after = build_belief_map.file_hash(str(source_path))

        self.assertNotEqual(before, after)

    def test_incremental_build_invalidates_same_mtime_content_change(self) -> None:
        """/* REQ-CS-008: same-mtime edits must rebuild their dependency edges. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "pkg"
            package.mkdir()
            source = package / "source.py"
            source.write_text("import pkg.before\n", encoding="utf-8")
            (package / "before.py").write_text("VALUE = 'before'\n", encoding="utf-8")
            (package / "after.py").write_text("VALUE = 'after'\n", encoding="utf-8")

            subprocess.run(
                [sys.executable, str(BUILD_SCRIPT), "--full", str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            original_stat = source.stat()
            source.write_text("import pkg.after\n", encoding="utf-8")
            os.utime(
                source,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            result = subprocess.run(
                [sys.executable, str(BUILD_SCRIPT), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            graph = belief_search.BeliefGraph()
            graph.load(str(root / ".belief_map.sexp"))

        targets = {edge.target for edge in graph.edges_from["pkg/source"]}
        self.assertIn("Parsing 1 changed files", result.stdout)
        self.assertEqual(targets, {"pkg/after"})

    def test_nested_parent_relative_import_creates_dependency_edge(self) -> None:
        """/* REQ-CS-009: nested relative imports must participate in test impact. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            feature_dir = root / "pkg" / "feature"
            feature_dir.mkdir(parents=True)
            source = feature_dir / "source.py"
            shared = root / "pkg" / "shared.py"
            source.write_text(
                textwrap.dedent(
                    """
                    from typing import TYPE_CHECKING

                    if TYPE_CHECKING:
                        from ..shared import VALUE
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            shared.write_text("VALUE = 'shared'\n", encoding="utf-8")

            source_result = build_belief_map.parse_file(
                (str(source), "python", "fixture")
            )
            shared_result = build_belief_map.parse_file(
                (str(shared), "python", "fixture")
            )
            self.assertIsNotNone(source_result)
            self.assertIsNotNone(shared_result)
            _nodes, edges = build_belief_map.build_graph(
                [source_result, shared_result],
                str(root),
            )

        import_edges = {
            (edge["source"], edge["target"])
            for edge in edges
            if edge["type"] == "IMPORTS"
        }
        self.assertIn(("pkg/feature/source", "pkg/shared"), import_edges)

    def test_typescript_js_specifier_resolves_to_typescript_source(self) -> None:
        """/* REQ-CS-016: ESM .js specifiers must resolve to checked-in TypeScript files. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.ts"
            target = root / "target.ts"
            source.write_text(
                "import { target } from './target.js';\n",
                encoding="utf-8",
            )
            target.write_text("export const target = true;\n", encoding="utf-8")

            source_result = build_belief_map.parse_file(
                (str(source), "typescript", "fixture")
            )
            target_result = build_belief_map.parse_file(
                (str(target), "typescript", "fixture")
            )
            self.assertIsNotNone(source_result)
            self.assertIsNotNone(target_result)
            _nodes, edges = build_belief_map.build_graph(
                [source_result, target_result],
                str(root),
            )

        import_edges = {
            (edge["source"], edge["target"])
            for edge in edges
            if edge["type"] == "IMPORTS"
        }
        self.assertIn(("source", "target"), import_edges)

    def test_tsx_relative_import_resolves_to_typescript_module(self) -> None:
        """/* REQ-CS-017: TSX imports must use TypeScript module resolution. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            component = self._parse_typescript(
                root,
                "src/component.tsx",
                'import { helper } from "./helper";\nexport const Component = helper;\n',
            )
            helper = self._parse_typescript(
                root,
                "src/helper.ts",
                "export const helper = 1;\n",
            )

            import_edges = self._import_edges([component, helper], root)

        self.assertIn(("src/component", "src/helper"), import_edges)

    def test_reexports_and_literal_runtime_imports_create_edges(self) -> None:
        """/* REQ-CS-018: Literal TypeScript dependency forms must create edges. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = self._parse_typescript(
                root,
                "src/index.ts",
                "\n".join(
                    (
                        'export { value } from "./value";',
                        'export * from "./types";',
                        'const lazy = import("./lazy");',
                        'const legacy = require("./legacy");',
                    )
                ),
            )
            dependencies = [
                self._parse_typescript(
                    root, "src/value.ts", "export const value = 1;\n"
                ),
                self._parse_typescript(
                    root, "src/types.ts", "export type Value = number;\n"
                ),
                self._parse_typescript(root, "src/lazy.ts", "export const lazy = 1;\n"),
                self._parse_typescript(
                    root, "src/legacy.ts", "export const legacy = 1;\n"
                ),
            ]

            import_edges = self._import_edges([entry, *dependencies], root)

        self.assertEqual(
            {
                ("src", "src/value"),
                ("src", "src/types"),
                ("src", "src/lazy"),
                ("src", "src/legacy"),
            },
            import_edges,
        )

    def test_duplicate_aliases_resolve_within_the_owning_project(self) -> None:
        """/* REQ-CS-019: TS path aliases must stay within their owning project. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            results: list[object] = []
            for package in ("a", "b"):
                package_root = root / "packages" / package
                package_root.mkdir(parents=True)
                (package_root / "tsconfig.json").write_text(
                    '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}',
                    encoding="utf-8",
                )
                results.extend(
                    (
                        self._parse_typescript(
                            root,
                            f"packages/{package}/src/consumer.ts",
                            'import { target } from "@/target";\n',
                        ),
                        self._parse_typescript(
                            root,
                            f"packages/{package}/src/target.ts",
                            f'export const target = "{package}";\n',
                        ),
                    )
                )

            import_edges = self._import_edges(results, root)

        self.assertIn(
            ("packages/a/src/consumer", "packages/a/src/target"),
            import_edges,
        )
        self.assertIn(
            ("packages/b/src/consumer", "packages/b/src/target"),
            import_edges,
        )

    def test_exact_alias_resolves_to_directory_index(self) -> None:
        """/* REQ-CS-020: Exact TS path aliases must resolve without wildcards. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tsconfig.json").write_text(
                """{
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@workspace/shared": ["packages/shared/src"]}
                    }
                }""",
                encoding="utf-8",
            )
            consumer = self._parse_typescript(
                root,
                "packages/app/src/consumer.ts",
                'import { shared } from "@workspace/shared";\n',
            )
            shared = self._parse_typescript(
                root,
                "packages/shared/src/index.ts",
                "export const shared = 1;\n",
            )

            import_edges = self._import_edges([consumer, shared], root)

        self.assertIn(
            ("packages/app/src/consumer", "packages/shared/src"),
            import_edges,
        )

    def test_package_self_import_resolves_source_entrypoint(self) -> None:
        """/* REQ-CS-021: Package self-imports must resolve to source entrypoints. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package_root = root / "packages" / "sdk"
            package_root.mkdir(parents=True)
            (package_root / "package.json").write_text(
                '{"name":"@workspace/sdk","main":"./dist/index.js"}',
                encoding="utf-8",
            )
            consumer = self._parse_typescript(
                root,
                "packages/sdk/examples/consumer.ts",
                'import { sdk } from "@workspace/sdk";\n',
            )
            entrypoint = self._parse_typescript(
                root,
                "packages/sdk/src/index.ts",
                "export const sdk = 1;\n",
            )

            import_edges = self._import_edges([consumer, entrypoint], root)

        self.assertIn(
            ("packages/sdk/examples/consumer", "packages/sdk/src"),
            import_edges,
        )

    def test_reference_only_lsp_batch_always_makes_progress(self) -> None:
        """/* REQ-CS-010: a full reference batch must be drained without looping. */"""
        references = [(index, f"name{index}", "module") for index in range(40)]

        taken_prepare, taken_refs, remaining_prepare, remaining_refs = (
            build_belief_map._partition_lsp_batch([], references, 40)
        )

        self.assertEqual(taken_prepare, [])
        self.assertEqual(taken_refs, references)
        self.assertEqual(remaining_prepare, [])
        self.assertEqual(remaining_refs, [])

    def test_map_root_is_the_directory_containing_the_map(self) -> None:
        """/* REQ-CS-011: source-aware queries must resolve from the map directory. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            map_path = root / ".belief_map.sexp"
            map_path.write_text("(paths\n)\n", encoding="utf-8")
            graph = belief_search.BeliefGraph()

            graph.load(str(map_path))

        self.assertEqual(graph._root, str(root))

    def test_backend_state_store_is_not_classified_as_ui(self) -> None:
        """/* REQ-CS-012: store naming alone must not imply a UI boundary. */"""
        backend_layer = belief_search._classify_layer(
            "platform-api/src/messagesStore/messagesStore",
            "state store",
        )
        frontend_layer = belief_search._classify_layer(
            "app-service/web/src/calendar/store/calendar-store",
            "state store",
        )

        self.assertEqual(backend_layer, "other")
        self.assertEqual(frontend_layer, "ui")

    def test_semantic_map_output_is_byte_deterministic(self) -> None:
        """/* REQ-CS-013: identical graphs must produce identical map bytes. */"""
        nodes = [
            {
                "id": "pkg/module",
                "language": "python",
                "repo": "fixture",
                "invariant": {"naming": "snake_case", "package": "fixture"},
                "purpose": "general module",
                "entities": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            first_path = Path(tmpdir) / "first.sexp"
            second_path = Path(tmpdir) / "second.sexp"
            with patch.object(
                build_belief_map.time,
                "strftime",
                side_effect=["first", "second"],
            ):
                build_belief_map.write_sexp(
                    str(first_path), nodes, [], tmpdir, "regex/AST", 1
                )
                build_belief_map.write_sexp(
                    str(second_path), nodes, [], tmpdir, "regex/AST", 1
                )

            first = first_path.read_bytes()
            second = second_path.read_bytes()

        self.assertEqual(first, second)

    def test_language_detector_makes_unsupported_coverage_explicit(self) -> None:
        """/* REQ-CS-014: language gates must distinguish supported source files. */"""
        self.assertEqual(build_belief_map.detect_source_language("module.py"), "python")
        self.assertEqual(
            build_belief_map.detect_source_language("module.tsx"), "typescript"
        )
        self.assertIsNone(build_belief_map.detect_source_language("module.swift"))
        self.assertIsNone(build_belief_map.detect_source_language("bootstrap.sh"))

    def test_empty_infra_result_names_the_supported_formats(self) -> None:
        """/* REQ-CS-015: empty topology must not imply that no infrastructure exists. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(sys, "argv", ["build_infra_topology.py", tmpdir]):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    build_infra_topology.main()

        expected = "no supported Kustomize, Helm, or Terraform files found"
        self.assertIn(expected, stdout.getvalue())
        self.assertIn(expected, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
