from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_belief_map import Ok, build_graph, discover_files
from scripts.lang.csharp import CSHARP_LANGUAGE, parse_csharp_treesitter
from scripts.lang.interface import FileResult


class CSharpLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_csharp_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_csharp_types_methods_bases_and_usings(self) -> None:
        """/* REQ-CSHARP-001: C# parsing preserves types, methods, bases, attributes, and local type candidates. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "Controllers/WidgetController.cs",
                """
using Demo.Services;
namespace Demo.Controllers;

[ApiController]
public class WidgetController : BaseController, IWidgetController
{
    private readonly WidgetService service;
    public WidgetController(WidgetService service) { this.service = service; }
    public Widget Get() => service.Get();
}

public interface IWidgetController
{
    Widget Get();
}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("csharp", result.language)
        self.assertIn("Demo.Services", result.imports)
        self.assertIn("Demo.Services.WidgetService", result.imports)
        self.assertEqual("class", entities["WidgetController"]["kind"])
        self.assertEqual(["Get"], entities["WidgetController"].get("methods", []))
        self.assertEqual(
            ["BaseController", "IWidgetController"],
            entities["WidgetController"].get("bases", []),
        )
        self.assertEqual(
            ["ApiController"],
            entities["WidgetController"].get("decorators", []),
        )
        self.assertEqual("interface", entities["IWidgetController"]["kind"])
        self.assertEqual(["IWidgetController"], result.exports_abstract)

    def test_graph_resolves_csharp_namespace_type_references(self) -> None:
        """/* REQ-CSHARP-002: C# local type references resolve through declared namespaces without guessing ambiguous files. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self._parse(
                root,
                "Services/WidgetService.cs",
                "namespace Demo.Services;\npublic class WidgetService {}\n",
            )
            controller = self._parse(
                root,
                "Controllers/WidgetController.cs",
                "using Demo.Services;\nnamespace Demo.Controllers;\n"
                "public class WidgetController { private WidgetService service; }\n",
            )
            graph_result = build_graph([service, controller], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"C# graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {("Controllers/WidgetController", "Services/WidgetService")},
            imports,
        )

    def test_graph_does_not_guess_from_unused_csharp_namespace(self) -> None:
        """/* REQ-CSHARP-005: An unused C# namespace using does not create a module edge by cardinality. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self._parse(
                root,
                "Services/WidgetService.cs",
                "namespace Demo.Services;\npublic class WidgetService {}\n",
            )
            controller = self._parse(
                root,
                "Controllers/WidgetController.cs",
                "using Demo.Services;\nnamespace Demo.Controllers;\n"
                "public class WidgetController {}\n",
            )
            graph_result = build_graph([service, controller], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"C# graph build failed: {graph_result.error}")

        imports = [
            edge for edge in graph_result.value.edges if edge["type"] == "IMPORTS"
        ]
        self.assertEqual([], imports)

    def test_csharp_language_metadata_matches_csharp_ls(self) -> None:
        """/* REQ-CSHARP-003: C# registration exposes canonical file, output, project, and LSP metadata. */"""
        self.assertEqual(
            "src/WidgetController",
            CSHARP_LANGUAGE.normalize_module_id("src/WidgetController.cs"),
        )
        self.assertEqual("cs", CSHARP_LANGUAGE.output_language_code("csharp"))
        self.assertEqual("csharp", CSHARP_LANGUAGE.lsp_language_id("Widget.cs"))
        self.assertEqual(("csharp-ls",), CSHARP_LANGUAGE.lsp_command)
        self.assertTrue(CSHARP_LANGUAGE.accepts_project_config("App.csproj"))

    def test_discovery_excludes_dotnet_build_outputs(self) -> None:
        """/* REQ-CSHARP-004: C# discovery excludes generated source under standard .NET build-output directories. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Source.cs").write_text("class Source {}\n", encoding="utf-8")
            for output_directory in ("bin", "obj"):
                generated = root / output_directory / "Generated.cs"
                generated.parent.mkdir(parents=True, exist_ok=True)
                generated.write_text("class Generated {}\n", encoding="utf-8")

            discovered = discover_files(str(root))

        self.assertEqual(["Source.cs"], [Path(item[0]).name for item in discovered])


if __name__ == "__main__":
    unittest.main()
