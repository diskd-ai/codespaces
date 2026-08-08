from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import BoundLanguage, FileResult
from .imports import BoundCSharpLanguage
from .parser import parse_csharp_treesitter


@dataclass(frozen=True)
class CSharpLanguage:
    name: str = "csharp"
    result_languages: tuple[str, ...] = ("csharp",)
    lsp_result_languages: tuple[str, ...] = ("csharp",)
    project_config_names: tuple[str, ...] = (".sln", ".csproj")
    lsp_command: tuple[str, ...] = ("csharp-ls",)
    lsp_label: str = "C#"
    cli_label: str = "cs"
    dependency_packages: tuple[str, ...] = (
        "tree-sitter",
        "tree-sitter-c-sharp",
    )
    source_extensions: tuple[str, ...] = (".cs",)

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(".cs")

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name.endswith(self.project_config_names)

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_csharp_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundLanguage:
        return BoundCSharpLanguage.create(path_to_id)

    def normalize_module_id(self, relative_path: str) -> str:
        if not relative_path.endswith(".cs"):
            raise ValueError(f"C# module path must end in .cs: {relative_path}")
        return relative_path[:-3]

    def output_language_code(self, result_language: str) -> str:
        if result_language != "csharp":
            raise ValueError(f"Unsupported C# result language: {result_language}")
        return "cs"

    def lsp_language_id(self, path: str) -> str:
        return "csharp"


CSHARP_LANGUAGE = CSharpLanguage()


__all__ = [
    "CSHARP_LANGUAGE",
    "CSharpLanguage",
    "parse_csharp_treesitter",
]
