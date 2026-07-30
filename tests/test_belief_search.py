import importlib.util
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "belief_search.py"
SPEC = importlib.util.spec_from_file_location("belief_search", SCRIPT_PATH)
belief_search = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = belief_search
SPEC.loader.exec_module(belief_search)


class BeliefSearchAliasTest(unittest.TestCase):
    def _load_graph(self) -> tuple[object, str]:
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


if __name__ == "__main__":
    unittest.main()
