from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import (
    BoundLanguage,
    DiscoveryExclusions,
    FileResult,
    LanguageDependency,
)
from .imports import BoundRubyLanguage


def parse_ruby_treesitter(
    path: str,
    content: str,
    repo: str,
    mtime: float,
) -> FileResult:
    from .parser import parse_ruby_treesitter as parse_ruby

    return parse_ruby(path, content, repo, mtime)


@dataclass(frozen=True)
class RubyLanguage:
    name: str = "ruby"
    result_languages: tuple[str, ...] = ("ruby",)
    lsp_result_languages: tuple[str, ...] = ("ruby",)
    project_config_names: tuple[str, ...] = (
        "Gemfile",
        "Gemfile.lock",
        ".ruby-version",
    )
    lsp_command: tuple[str, ...] = ("ruby-lsp",)
    lsp_label: str = "Ruby"
    display_name: str = "Ruby"
    cli_label: str = "rb"
    dependencies: tuple[LanguageDependency, ...] = (
        LanguageDependency("tree-sitter", "0.25.2"),
        LanguageDependency("tree-sitter-ruby", "0.23.1"),
    )
    source_extensions: tuple[str, ...] = (".rb", ".rake")

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(self.source_extensions)

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names or file_name.endswith(".gemspec")

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_ruby_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        return BoundRubyLanguage.create(
            root,
            path_to_id,
            discovery_exclusions,
        )

    def normalize_module_id(self, relative_path: str) -> str:
        for extension in self.source_extensions:
            if relative_path.endswith(extension):
                return relative_path[: -len(extension)]
        raise ValueError(f"Ruby module path must end in .rb or .rake: {relative_path}")

    def output_language_code(self, result_language: str) -> str:
        if result_language != "ruby":
            raise ValueError(f"Unsupported Ruby result language: {result_language}")
        return "rb"

    def lsp_language_id(self, path: str) -> str:
        return "ruby"


RUBY_LANGUAGE = RubyLanguage()


__all__ = [
    "RUBY_LANGUAGE",
    "RubyLanguage",
    "parse_ruby_treesitter",
]
