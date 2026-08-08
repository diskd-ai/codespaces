from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_belief_map import Ok, build_graph, discover_files
from scripts.lang.go import GO_LANGUAGE, parse_go_treesitter
from scripts.lang.interface import FileResult


class GoLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_go_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_go_types_functions_methods_and_imports(self) -> None:
        """/* REQ-GO-001: Go parsing preserves types, functions, receiver methods, constants, and imports. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "worker/worker.go",
                """
package worker

import (
    "example.com/app/model"
    alias "example.com/app/shared"
)

const MaxWorkers = 4

type Runner interface { Run() error }
type Worker struct{}

func NewWorker() *Worker { return &Worker{} }
func (worker *Worker) Run() error { return nil }
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("go", result.language)
        self.assertEqual(
            ["example.com/app/model", "example.com/app/shared"],
            result.imports,
        )
        self.assertEqual("interface", entities["Runner"]["kind"])
        self.assertEqual(["Run"], entities["Runner"].get("methods", []))
        self.assertEqual("class", entities["Worker"]["kind"])
        self.assertEqual(["Run"], entities["Worker"].get("methods", []))
        self.assertEqual("function", entities["NewWorker"]["kind"])
        self.assertEqual("function", entities["MaxWorkers"]["kind"])
        self.assertEqual(["Runner"], result.exports_abstract)

    def test_graph_resolves_go_module_imports_to_package_files(self) -> None:
        """/* REQ-GO-002: Go local package imports resolve to every source file in the imported package boundary. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text(
                "module example.com/app\n\ngo 1.22\n",
                encoding="utf-8",
            )
            main = self._parse(
                root,
                "cmd/app/main.go",
                'package main\nimport "example.com/app/internal/service"\nfunc main() {}\n',
            )
            service = self._parse(
                root,
                "internal/service/service.go",
                "package service\nfunc Run() {}\n",
            )
            helper = self._parse(
                root,
                "internal/service/helper.go",
                "package service\nfunc Help() {}\n",
            )
            graph_result = build_graph([main, service, helper], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Go graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("cmd/app/main", "internal/service/helper"),
                ("cmd/app/main", "internal/service/service"),
            },
            imports,
        )

    def test_go_language_metadata_matches_gopls(self) -> None:
        """/* REQ-GO-003: Go registration exposes canonical file, output, project, and LSP metadata. */"""
        self.assertEqual(
            "cmd/app/main",
            GO_LANGUAGE.normalize_module_id("cmd/app/main.go"),
        )
        self.assertEqual("go", GO_LANGUAGE.output_language_code("go"))
        self.assertEqual("go", GO_LANGUAGE.lsp_language_id("main.go"))
        self.assertEqual(("gopls",), GO_LANGUAGE.lsp_command)
        self.assertTrue(GO_LANGUAGE.accepts_project_config("go.mod"))

    def test_discovery_excludes_vendored_go_dependencies(self) -> None:
        """/* REQ-GO-004: Go discovery excludes source owned by the standard vendor dependency tree. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            vendored = root / "vendor/example.com/dependency/dependency.go"
            vendored.parent.mkdir(parents=True, exist_ok=True)
            vendored.write_text("package dependency\n", encoding="utf-8")

            discovered = discover_files(str(root))

        self.assertEqual(["main.go"], [Path(item[0]).name for item in discovered])


if __name__ == "__main__":
    unittest.main()
