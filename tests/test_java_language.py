from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_belief_map import Ok, build_graph
from scripts.lang.interface import FileResult
from scripts.lang.java import JAVA_LANGUAGE, parse_java_treesitter


class JavaLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_java_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_java_types_methods_bases_and_imports(self) -> None:
        """/* REQ-JAVA-001: Java parsing preserves declarations, methods, inheritance, annotations, and imports. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "src/main/java/demo/controllers/WidgetController.java",
                """
package demo.controllers;

import demo.services.WidgetService;

@RestController
public class WidgetController extends BaseController implements Runnable {
    private final WidgetService service;
    public WidgetController(WidgetService service) { this.service = service; }
    public void run() {}
}

interface WidgetPort {
    void execute();
}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("java", result.language)
        self.assertIn("demo.services.WidgetService", result.imports)
        self.assertEqual("class", entities["WidgetController"]["kind"])
        self.assertEqual(["run"], entities["WidgetController"].get("methods", []))
        self.assertEqual(
            ["BaseController", "Runnable"],
            entities["WidgetController"].get("bases", []),
        )
        self.assertEqual(
            ["RestController"],
            entities["WidgetController"].get("decorators", []),
        )
        self.assertEqual("interface", entities["WidgetPort"]["kind"])
        self.assertIn("WidgetPort", result.exports_abstract)

    def test_graph_resolves_java_class_imports(self) -> None:
        """/* REQ-JAVA-002: Java local class imports resolve through package declarations. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self._parse(
                root,
                "src/main/java/demo/services/WidgetService.java",
                "package demo.services;\npublic class WidgetService {}\n",
            )
            controller = self._parse(
                root,
                "src/main/java/demo/controllers/WidgetController.java",
                "package demo.controllers;\nimport demo.services.WidgetService;\n"
                "public class WidgetController { private WidgetService service; }\n",
            )
            graph_result = build_graph([service, controller], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Java graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                (
                    "src/main/java/demo/controllers/WidgetController",
                    "src/main/java/demo/services/WidgetService",
                )
            },
            imports,
        )

    def test_graph_does_not_guess_from_unused_java_wildcard(self) -> None:
        """/* REQ-JAVA-004: An unused Java wildcard import does not create a module edge by package cardinality. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self._parse(
                root,
                "src/main/java/demo/services/WidgetService.java",
                "package demo.services;\npublic class WidgetService {}\n",
            )
            controller = self._parse(
                root,
                "src/main/java/demo/controllers/WidgetController.java",
                "package demo.controllers;\nimport demo.services.*;\n"
                "public class WidgetController {}\n",
            )
            graph_result = build_graph([service, controller], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Java graph build failed: {graph_result.error}")

        imports = [
            edge for edge in graph_result.value.edges if edge["type"] == "IMPORTS"
        ]
        self.assertEqual([], imports)

    def test_graph_resolves_referenced_java_wildcard_type(self) -> None:
        """/* REQ-JAVA-005: A Java wildcard import resolves a local type only when source references that type. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self._parse(
                root,
                "src/main/java/demo/services/WidgetService.java",
                "package demo.services;\npublic class WidgetService {}\n",
            )
            controller = self._parse(
                root,
                "src/main/java/demo/controllers/WidgetController.java",
                "package demo.controllers;\nimport demo.services.*;\n"
                "public class WidgetController { WidgetService service; }\n",
            )
            graph_result = build_graph([service, controller], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Java graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                (
                    "src/main/java/demo/controllers/WidgetController",
                    "src/main/java/demo/services/WidgetService",
                )
            },
            imports,
        )

    def test_java_language_metadata_matches_jdtls(self) -> None:
        """/* REQ-JAVA-003: Java registration exposes canonical file, output, project, and LSP metadata. */"""
        self.assertEqual(
            "src/main/java/demo/Widget",
            JAVA_LANGUAGE.normalize_module_id("src/main/java/demo/Widget.java"),
        )
        self.assertEqual("java", JAVA_LANGUAGE.output_language_code("java"))
        self.assertEqual("java", JAVA_LANGUAGE.lsp_language_id("Widget.java"))
        self.assertEqual(("jdtls",), JAVA_LANGUAGE.lsp_command)
        self.assertTrue(JAVA_LANGUAGE.accepts_project_config("pom.xml"))


if __name__ == "__main__":
    unittest.main()
