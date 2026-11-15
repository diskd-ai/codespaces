from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_belief_map import Ok, build_graph
from scripts.lang.interface import FileResult
from scripts.lang.rust import RUST_LANGUAGE, parse_rust_treesitter


class RustLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_rust_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_rust_entities_impls_and_grouped_uses(self) -> None:
        """/* REQ-RUST-001: Rust parsing preserves declarations, impl relations, methods, and use leaves. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "src/lib.rs",
                """
use crate::{model, service::Service};
use super::shared as shared_module;

pub trait Runner {
    fn run(&self);
}

pub struct Worker;
pub enum Status { Ready, Done }
pub type WorkerId = u64;

impl Runner for Worker {
    fn run(&self) {}
}

impl Worker {
    pub fn new() -> Self { Self }
}

pub fn execute() {}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("rust", result.language)
        self.assertEqual(
            ["crate::model", "crate::service::Service", "super::shared"],
            result.imports,
        )
        self.assertEqual("interface", entities["Runner"]["kind"])
        self.assertEqual(["run"], entities["Runner"].get("methods", []))
        self.assertEqual("class", entities["Worker"]["kind"])
        self.assertEqual(["new", "run"], entities["Worker"].get("methods", []))
        self.assertEqual(["Runner"], entities["Worker"].get("bases", []))
        self.assertEqual("enum", entities["Status"]["kind"])
        self.assertEqual("type", entities["WorkerId"]["kind"])
        self.assertEqual("function", entities["execute"]["kind"])
        self.assertEqual(["Runner"], result.exports_abstract)
        self.assertEqual(["Runner"], result.implements)

    def test_graph_resolves_rust_modules_and_workspace_crates(self) -> None:
        """/* REQ-RUST-002: Rust graph edges resolve mod, crate, and workspace-crate paths. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/core-lib", "crates/app"]\n',
                encoding="utf-8",
            )
            core_root = root / "crates" / "core-lib"
            app_root = root / "crates" / "app"
            core_root.mkdir(parents=True)
            app_root.mkdir(parents=True)
            (core_root / "Cargo.toml").write_text(
                '[package]\nname = "core-lib"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            (app_root / "Cargo.toml").write_text(
                '[package]\nname = "app"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )

            results = [
                self._parse(
                    root,
                    "crates/core-lib/src/lib.rs",
                    "pub mod model;\n",
                ),
                self._parse(
                    root,
                    "crates/core-lib/src/model.rs",
                    "pub struct Thing;\n",
                ),
                self._parse(
                    root,
                    "crates/app/src/main.rs",
                    "use core_lib::model::Thing;\nmod local;\nuse crate::local::run;\nfn main() {}\n",
                ),
                self._parse(
                    root,
                    "crates/app/src/local.rs",
                    "pub fn run() {}\n",
                ),
            ]

            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Rust graph build failed: {graph_result.error}")
            edges = graph_result.value.edges

        imports = {
            (edge["source"], edge["target"])
            for edge in edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("crates/core-lib/src/lib", "crates/core-lib/src/model"),
                ("crates/app/src/main", "crates/core-lib/src/model"),
                ("crates/app/src/main", "crates/app/src/local"),
            },
            imports,
        )

    def test_graph_resolves_bare_super_glob_to_parent_module(self) -> None:
        """/* REQ-RUST-004: A bare super glob preserves the child-to-parent module dependency. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "app"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            results = [
                self._parse(
                    root,
                    "src/lib.rs",
                    "mod handlers;\npub struct RootState;\n",
                ),
                self._parse(
                    root,
                    "src/handlers.rs",
                    "use super::*;\npub mod payments;\npub struct AppState;\n",
                ),
                self._parse(
                    root,
                    "src/handlers/payments.rs",
                    "use super::*;\npub fn process(_: AppState) {}\n",
                ),
            ]

            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Rust graph build failed: {graph_result.error}")
            imports = {
                (edge["source"], edge["target"])
                for edge in graph_result.value.edges
                if edge["type"] == "IMPORTS"
            }

        self.assertIn(
            ("src/handlers/payments", "src/handlers"),
            imports,
        )
        self.assertIn(("src/handlers", "src/lib"), imports)

    def test_rust_language_metadata_matches_rust_analyzer(self) -> None:
        """/* REQ-RUST-003: Rust registration exposes canonical module, output, and LSP metadata. */"""
        self.assertEqual(
            "crates/core/src/lib",
            RUST_LANGUAGE.normalize_module_id("crates/core/src/lib.rs"),
        )
        self.assertEqual("rs", RUST_LANGUAGE.output_language_code("rust"))
        self.assertEqual("rust", RUST_LANGUAGE.lsp_language_id("src/lib.rs"))
        self.assertEqual(("rust-analyzer",), RUST_LANGUAGE.lsp_command)
        self.assertEqual(("Cargo.toml",), RUST_LANGUAGE.project_config_names)


if __name__ == "__main__":
    unittest.main()
