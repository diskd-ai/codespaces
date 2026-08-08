from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import BoundLanguage, FileResult, LanguageDependency
from .imports import BoundRustLanguage


def parse_rust_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    from .parser import parse_rust_treesitter as parse_rust

    return parse_rust(path, content, repo, mtime)


@dataclass(frozen=True)
class RustLanguage:
    name: str = "rust"
    result_languages: tuple[str, ...] = ("rust",)
    lsp_result_languages: tuple[str, ...] = ("rust",)
    project_config_names: tuple[str, ...] = ("Cargo.toml",)
    lsp_command: tuple[str, ...] = ("rust-analyzer",)
    lsp_label: str = "Rust"
    display_name: str = "Rust"
    cli_label: str = "rs"
    dependencies: tuple[LanguageDependency, ...] = (
        LanguageDependency("tree-sitter", "0.25.2"),
        LanguageDependency("tree-sitter-rust", "0.24.2"),
    )
    source_extensions: tuple[str, ...] = (".rs",)

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(".rs")

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_rust_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundLanguage:
        return BoundRustLanguage.create(root, path_to_id, skip_directories)

    def normalize_module_id(self, relative_path: str) -> str:
        if not relative_path.endswith(".rs"):
            raise ValueError(f"Rust module path must end in .rs: {relative_path}")
        module_id = relative_path[:-3]
        if module_id.endswith("/mod"):
            return module_id[:-4]
        return module_id

    def output_language_code(self, result_language: str) -> str:
        if result_language != "rust":
            raise ValueError(f"Unsupported Rust result language: {result_language}")
        return "rs"

    def lsp_language_id(self, path: str) -> str:
        return "rust"


RUST_LANGUAGE = RustLanguage()


__all__ = [
    "RUST_LANGUAGE",
    "RustLanguage",
    "parse_rust_treesitter",
]
