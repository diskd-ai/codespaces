import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_audit_regressions import (
    BUILD_SCRIPT,
    belief_search,
    build_belief_map,
)


class IssueOneRegressionTest(unittest.TestCase):
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

    def _import_edges(
        self,
        results: list[object],
        root: Path,
    ) -> set[tuple[str, str]]:
        graph_result = build_belief_map.build_graph(results, str(root))
        self.assertIsInstance(graph_result, build_belief_map.Ok)
        return {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }

    def test_calls_api_uses_explicit_import_in_every_result_order(self) -> None:
        """/* REQ-CS-022: duplicate interface names must not make CALLS_API order-dependent. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            provider_a = self._parse_typescript(
                root,
                "provider_a.ts",
                "export interface Contract { run(): void; }\n",
            )
            provider_b = self._parse_typescript(
                root,
                "provider_b.ts",
                "export interface Contract { run(): void; }\n",
            )
            consumer = self._parse_typescript(
                root,
                "consumer.ts",
                "\n".join(
                    (
                        'import { Contract } from "./provider_a";',
                        "export class Impl implements Contract { run() {} }",
                    )
                ),
            )

            outputs = []
            for results in (
                [provider_a, provider_b, consumer],
                [provider_b, provider_a, consumer],
            ):
                graph_result = build_belief_map.build_graph(results, str(root))
                self.assertIsInstance(graph_result, build_belief_map.Ok)
                outputs.append(graph_result.value)

            call_edges = [
                {
                    (edge["source"], edge["target"])
                    for edge in output.edges
                    if edge["type"] == "CALLS_API"
                }
                for output in outputs
            ]
            rendered = [
                build_belief_map.render_sexp(
                    output.nodes,
                    output.edges,
                    str(root),
                    "regex/AST",
                    3,
                )
                for output in outputs
            ]

        self.assertEqual(call_edges, [{("consumer", "provider_a")}] * 2)
        self.assertEqual(rendered[0], rendered[1])

    def test_module_id_collisions_fail_with_every_conflicting_path(self) -> None:
        """/* REQ-CS-023: colliding module IDs must hard-fail without discarding files. */"""
        fixtures = (
            (
                ("dual.py", "VALUE = 1\n", "python"),
                ("dual.ts", "export const VALUE = 1;\n", "typescript"),
                "dual",
            ),
            (
                ("pkg.py", "VALUE = 1\n", "python"),
                ("pkg/index.py", "VALUE = 2\n", "python"),
                "pkg",
            ),
        )
        for first, second, expected_id in fixtures:
            with self.subTest(expected_id=expected_id):
                with tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    results = []
                    for relative_path, content, language in (first, second):
                        path = root / relative_path
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content, encoding="utf-8")
                        result = build_belief_map.parse_file(
                            (str(path), language, "fixture")
                        )
                        self.assertIsNotNone(result)
                        results.append(result)

                    graph_result = build_belief_map.build_graph(results, str(root))
                    map_path = root / ".belief_map.sexp"
                    cache_path = root / ".belief_map_cache.json"
                    map_path.write_bytes(b"prior-map\n")
                    cache_path.write_bytes(b"prior-cache\n")
                    cli_result = subprocess.run(
                        [
                            sys.executable,
                            str(BUILD_SCRIPT),
                            "--root",
                            str(root),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    preserved_map = map_path.read_bytes()
                    preserved_cache = cache_path.read_bytes()

                self.assertIsInstance(graph_result, build_belief_map.Err)
                collision = graph_result.error.collisions[0]
                self.assertEqual(collision.node_id, expected_id)
                self.assertEqual(
                    collision.paths,
                    tuple(sorted((first[0], second[0]))),
                )
                self.assertEqual(cli_result.returncode, 1)
                self.assertIn("module ID collision", cli_result.stderr)
                self.assertEqual(preserved_map, b"prior-map\n")
                self.assertEqual(preserved_cache, b"prior-cache\n")

    def test_incompatible_and_legacy_caches_are_rebuilt(self) -> None:
        """/* REQ-CS-024: cache shape and provenance must gate incremental reuse. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            command = [
                sys.executable,
                str(BUILD_SCRIPT),
                "--root",
                str(root),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            cache_path = root / ".belief_map_cache.json"

            cache_path.write_text("[]\n", encoding="utf-8")
            wrong_shape = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("top-level value must be an object", wrong_shape.stderr)
            self.assertIn("Parsing 1 changed files", wrong_shape.stdout)

            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            source_cache_key = os.path.realpath(source)
            stale_result = cache["entries"][source_cache_key]
            stale_result["result"]["purpose"] = "semantically stale"
            legacy_cache = {source_cache_key: stale_result}
            cache_path.write_text(json.dumps(legacy_cache), encoding="utf-8")
            stale = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            rebuilt_cache = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertIn("schema or builder fingerprint changed", stale.stderr)
        self.assertIn("Parsing 1 changed files", stale.stdout)
        self.assertEqual(
            rebuilt_cache["schema_version"],
            build_belief_map.CACHE_SCHEMA_VERSION,
        )
        self.assertNotEqual(
            rebuilt_cache["entries"][source_cache_key]["result"]["purpose"],
            "semantically stale",
        )

    def test_atomic_write_preserves_prior_file_when_replace_fails(self) -> None:
        """/* REQ-CS-025: interrupted publication must retain the last good file. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / ".belief_map.sexp"
            output_path.write_text("prior-good\n", encoding="utf-8")

            with patch.object(
                build_belief_map.os,
                "replace",
                side_effect=OSError("injected interruption"),
            ):
                with self.assertRaisesRegex(OSError, "injected interruption"):
                    build_belief_map._atomic_write_text(
                        str(output_path),
                        "new-content\n",
                    )

            preserved_content = output_path.read_text(encoding="utf-8")
            temporary_files = list(Path(tmpdir).glob(".*.tmp"))

        self.assertEqual(preserved_content, "prior-good\n")
        self.assertEqual(temporary_files, [])

    def test_explicit_root_and_output_work_from_another_directory(self) -> None:
        """/* REQ-CS-026: documented builds must target an explicit project root. */"""
        with tempfile.TemporaryDirectory() as target_tmpdir:
            with tempfile.TemporaryDirectory() as caller_tmpdir:
                root = Path(target_tmpdir)
                (root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
                output_path = root / "custom-map.sexp"

                result = subprocess.run(
                    [
                        sys.executable,
                        str(BUILD_SCRIPT),
                        "--root",
                        str(root),
                        "--output",
                        str(output_path),
                    ],
                    cwd=caller_tmpdir,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                graph = belief_search.BeliefGraph()
                load_result = graph.load(str(output_path), str(root))

        self.assertIn(f"Scanning {os.path.realpath(root)}", result.stdout)
        self.assertIsInstance(load_result, belief_search.MapLoadOk)
        self.assertIn("source", graph.nodes)

    def test_additional_literal_typescript_dependency_forms_create_edges(self) -> None:
        """/* REQ-CS-027: import-equals and literal templates must create edges. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = self._parse_typescript(
                root,
                "src/index.ts",
                "\n".join(
                    (
                        'import legacy = require("./legacy");',
                        "const lazy = import(`./lazy`);",
                    )
                ),
            )
            legacy = self._parse_typescript(
                root,
                "src/legacy.ts",
                "export const legacy = 1;\n",
            )
            lazy = self._parse_typescript(
                root,
                "src/lazy.ts",
                "export const lazy = 1;\n",
            )

            import_edges = self._import_edges([entry, legacy, lazy], root)

        self.assertEqual(
            import_edges,
            {
                ("src", "src/legacy"),
                ("src", "src/lazy"),
            },
        )

    def test_quoted_paths_round_trip_and_malformed_maps_fail(self) -> None:
        """/* REQ-CS-028: path quoting and structural validation must preserve map reachability. */"""
        nodes = [
            {
                "id": "pkg/space name",
                "language": "python",
                "repo": "fixture",
                "invariant": {"naming": "snake_case", "package": "fixture"},
                "purpose": "general module",
                "entities": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            map_path = Path(tmpdir) / ".belief_map.sexp"
            build_belief_map.write_sexp(
                str(map_path),
                nodes,
                [],
                tmpdir,
                "regex/AST",
                1,
            )
            graph = belief_search.BeliefGraph()
            load_result = graph.load(str(map_path))

            malformed_path = Path(tmpdir) / "malformed.sexp"
            malformed_path.write_text("(paths\n)\n", encoding="utf-8")
            malformed_result = belief_search.BeliefGraph().load(str(malformed_path))

        self.assertIsInstance(load_result, belief_search.MapLoadOk)
        self.assertIn("pkg/space name", graph.nodes)
        self.assertIsInstance(malformed_result, belief_search.MapLoadErr)
        self.assertIn("header is missing", malformed_result.error)

    def test_active_build_lock_rejects_a_second_writer(self) -> None:
        """/* REQ-CS-029: concurrent builders must not publish over one another. */"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
            canonical_root = os.path.realpath(root)
            lock_path = build_belief_map._lock_path(canonical_root)
            with open(lock_path, "a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(BUILD_SCRIPT),
                        "--root",
                        str(root),
                    ],
                    capture_output=True,
                    text=True,
                )
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            map_exists = (root / ".belief_map.sexp").exists()

        self.assertEqual(result.returncode, 1)
        self.assertIn("another build is active", result.stderr)
        self.assertFalse(map_exists)

    def test_missing_parser_dependency_has_install_diagnostic(self) -> None:
        """/* REQ-CS-032: missing parser dependencies must fail without a traceback. */"""
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if key != "PYTHONPATH"
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "service.ts").write_text(
                "export const execute = () => true;\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(BUILD_SCRIPT),
                    "--root",
                    str(root),
                ],
                capture_output=True,
                env=clean_environment,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("TypeScript", result.stderr)
        self.assertIn("requirements/typescript.txt", result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
