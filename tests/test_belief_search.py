import importlib.util
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Protocol


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "belief_search.py"
SPEC = importlib.util.spec_from_file_location("belief_search", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load belief search module from {SCRIPT_PATH}")
belief_search = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = belief_search
SPEC.loader.exec_module(belief_search)


class BeliefGraphContract(Protocol):
    def resolve_module(self, query: str) -> list[str]: ...


class BeliefSearchAliasTest(unittest.TestCase):
    def _load_graph(self) -> tuple[BeliefGraphContract, str]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        sexp_path = Path(tmpdir.name) / ".belief_map.sexp"
        sexp_path.write_text(
            textwrap.dedent(
                """
                (belief-map :schema 1 :files 2 :nodes 2 :edges 1 :violations 0)
                (paths
                  (app-service
                    (app-service
                      (src
                        (workspace-operatives
                          (asp543 workspace-operative.service)
                          (asp544 workspace-operative.service.spec)
                        )
                      )
                    )
                  )
                )
                (node asp543 ts service)
                (node asp544 ts test)
                (cls asp543 WorkspaceOperativeService 16)
                (imports asp544 asp543)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        graph = belief_search.BeliefGraph()
        load_result = graph.load(str(sexp_path))
        self.assertIsInstance(load_result, belief_search.MapLoadOk)
        return graph, str(sexp_path)

    def test_resolve_module_accepts_path_aliases(self) -> None:
        """/* REQ-CS-001: analyze must accept internal path-map aliases returned by older belief maps. */"""
        graph, _sexp_path = self._load_graph()

        self.assertEqual(
            graph.resolve_module("asp543"),
            ["app-service/app-service/src/workspace-operatives/workspace-operative.service"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_analyze(graph, "asp543")

        self.assertIn(
            "(analyze app-service/app-service/src/workspace-operatives/workspace-operative.service",
            output.getvalue(),
        )

    def test_search_returns_resolved_module_ids(self) -> None:
        """/* REQ-CS-002: search must emit resolved module IDs instead of raw path-map aliases. */"""
        graph, sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_search(graph, "workspace-operative.service", sexp_path)

        lines = output.getvalue().strip().splitlines()
        self.assertEqual(
            lines,
            [
                "(result app-service/app-service/src/workspace-operatives/workspace-operative.service)",
                "(result app-service/app-service/src/workspace-operatives/workspace-operative.service.spec)",
                "(result-count 2)",
            ],
        )

    def test_quick_runs_analyze_for_first_resolved_module(self) -> None:
        """/* REQ-CS-003: quick must provide full analyze output for the first matching module. */"""
        graph, sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_quick(graph, "workspace-operative.service", sexp_path)

        text = output.getvalue()
        self.assertIn(
            "(analyze app-service/app-service/src/workspace-operatives/workspace-operative.service",
            text,
        )
        self.assertIn(
            "(boundary-file app-service/app-service/src/workspace-operatives/workspace-operative.service",
            text,
        )
        self.assertNotIn("(boundary app-service/app-service/src/workspace-operatives/workspace-operative.service", text)

    def test_quick_resolves_alias_from_raw_search_hits(self) -> None:
        """/* REQ-CS-004: quick must resolve legacy path-map aliases found in raw belief-map facts. */"""
        graph, sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_quick(graph, "WorkspaceOperativeService", sexp_path)

        self.assertIn(
            "(analyze app-service/app-service/src/workspace-operatives/workspace-operative.service",
            output.getvalue(),
        )

    def test_boundary_files_only_emits_minimal_file_list(self) -> None:
        """/* REQ-CS-005: boundary --files-only must emit the target and directly related files only. */"""
        graph, _sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_boundary(graph, "workspace-operative.service", files_only=True)

        self.assertEqual(
            output.getvalue().strip().splitlines(),
            [
                "app-service/app-service/src/workspace-operatives/workspace-operative.service",
                "app-service/app-service/src/workspace-operatives/workspace-operative.service.spec",
            ],
        )

    def test_rust_query_commands_resolve_physical_source_files(self) -> None:
        """/* REQ-CS-033: Rust query boundaries resolve to physical source paths. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flat_module = root / "src" / "handlers.rs"
            child_module = root / "src" / "handlers" / "payments.rs"
            nested_module = root / "src" / "storage" / "mod.rs"
            flat_module.parent.mkdir(parents=True)
            child_module.parent.mkdir(parents=True)
            nested_module.parent.mkdir(parents=True)
            flat_module.write_text("pub mod payments;\n", encoding="utf-8")
            child_module.write_text("use super::*;\n", encoding="utf-8")
            nested_module.write_text("pub struct Database;\n", encoding="utf-8")
            sexp_path = root / ".belief_map.sexp"
            sexp_path.write_text(
                textwrap.dedent(
                    """
                    (belief-map :schema 1 :files 3 :nodes 3 :edges 1 :violations 0)
                    (paths
                      (src
                        (r1 handlers)
                        (r3 storage)
                        (handlers
                          (r2 payments)
                        )
                      )
                    )
                    (node r1 rs service)
                    (node r2 rs service)
                    (node r3 rs shared)
                    (imports r2 r1)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            graph = belief_search.BeliefGraph()
            load_result = graph.load(str(sexp_path), str(root))
            self.assertIsInstance(load_result, belief_search.MapLoadOk)

            self.assertEqual(
                str(flat_module),
                belief_search._module_id_to_file("src/handlers", str(root)),
            )
            self.assertEqual(
                str(nested_module),
                belief_search._module_id_to_file("src/storage", str(root)),
            )

            boundary_output = io.StringIO()
            with redirect_stdout(boundary_output):
                belief_search.cmd_boundary(
                    graph,
                    "src/handlers/payments",
                    files_only=True,
                )
            self.assertEqual(
                boundary_output.getvalue().strip().splitlines(),
                [str(child_module), str(flat_module)],
            )

            analyze_output = io.StringIO()
            with redirect_stdout(analyze_output):
                belief_search.cmd_analyze(graph, "src/handlers/payments")
            self.assertIn(
                f"(boundary-file src/handlers/payments {child_module})",
                analyze_output.getvalue(),
            )
            self.assertIn(
                f"(boundary-file src/handlers/payments {flat_module})",
                analyze_output.getvalue(),
            )

    def test_no_match_includes_suggestions(self) -> None:
        """/* REQ-CS-006: no-match errors must surface actionable module suggestions. */"""
        graph, _sexp_path = self._load_graph()

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_analyze(graph, "workspace-operative.controller")

        text = output.getvalue()
        self.assertIn("(error no-match workspace-operative.controller :suggestions", text)
        self.assertIn("workspace-operative.service", text)

    def test_malformed_and_adversarial_patterns_fail_without_traceback(self) -> None:
        """/* REQ-CS-030: unsafe search patterns must fail quickly and clearly. */"""
        _graph, sexp_path = self._load_graph()

        for pattern in ("[", "(a+)+$"):
            with self.subTest(pattern=pattern):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT_PATH),
                        "--map",
                        sexp_path,
                        "search",
                        pattern,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("(error invalid-argument", result.stdout)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_invalid_depth_fails_without_loading_or_traceback(self) -> None:
        """/* REQ-CS-031: invalid numeric query arguments must return typed diagnostics. */"""
        _graph, sexp_path = self._load_graph()

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--map",
                sexp_path,
                "deps",
                "workspace-operative.service",
                "unbounded",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("depth must be an integer", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_qualified_ruby_method_is_not_duplicated_by_class_summary(self) -> None:
        """/* REQ-RUBY-010: Function search returns the precise Ruby method entity once when a class also lists that method. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        sexp_path = Path(tmpdir.name) / ".belief_map.sexp"
        sexp_path.write_text(
            textwrap.dedent(
                """
                (belief-map :schema 1 :files 1 :nodes 1 :edges 0 :violations 0)
                (paths
                  (app
                    (services
                      (charge rb0)
                    )
                  )
                )
                (node rb0 rb service :naming snake_case :pkg example)
                (cls rb0 Billing::Charge 3 (:methods call))
                (fn rb0 Billing::Charge#call 9)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        graph = belief_search.BeliefGraph()
        load_result = graph.load(str(sexp_path))
        self.assertIsInstance(load_result, belief_search.MapLoadOk)

        output = io.StringIO()
        with redirect_stdout(output):
            belief_search.cmd_find_function(graph, "call")

        result_lines = output.getvalue().splitlines()
        self.assertEqual(1, len(result_lines))
        self.assertIn("Billing::Charge#call :line 9", result_lines[0])


if __name__ == "__main__":
    unittest.main()
