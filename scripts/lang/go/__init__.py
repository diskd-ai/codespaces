from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import (
    BoundLanguage,
    DiscoveryExclusions,
    FileResult,
    LanguageDependency,
)
from .imports import BoundGoLanguage


def parse_go_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    from .parser import parse_go_treesitter as parse_go

    return parse_go(path, content, repo, mtime)


@dataclass(frozen=True)
class GoLanguage:
    name: str = "go"
    result_languages: tuple[str, ...] = ("go",)
    lsp_result_languages: tuple[str, ...] = ("go",)
    project_config_names: tuple[str, ...] = ("go.mod", "go.work")
    lsp_command: tuple[str, ...] = ("gopls",)
    lsp_label: str = "Go"
    display_name: str = "Go"
    cli_label: str = "go"
    dependencies: tuple[LanguageDependency, ...] = (
        LanguageDependency("tree-sitter", "0.25.2"),
        LanguageDependency("tree-sitter-go", "0.25.0"),
    )
    source_extensions: tuple[str, ...] = (".go",)

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(".go")

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_go_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        return BoundGoLanguage.create(
            root,
            path_to_id,
            discovery_exclusions,
        )

    def normalize_module_id(self, relative_path: str) -> str:
        if not relative_path.endswith(".go"):
            raise ValueError(f"Go module path must end in .go: {relative_path}")
        return relative_path[:-3]

    def output_language_code(self, result_language: str) -> str:
        if result_language != "go":
            raise ValueError(f"Unsupported Go result language: {result_language}")
        return "go"

    def lsp_language_id(self, path: str) -> str:
        return "go"


GO_LANGUAGE = GoLanguage()


__all__ = [
    "GO_LANGUAGE",
    "GoLanguage",
    "parse_go_treesitter",
]
