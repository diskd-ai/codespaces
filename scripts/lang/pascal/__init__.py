from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import (
    BoundLanguage,
    DiscoveryExclusions,
    FileResult,
    LanguageDependency,
)
from .imports import BoundPascalLanguage
from .parser import parse_pascal


@dataclass(frozen=True)
class PascalLanguage:
    name: str = "pascal"
    result_languages: tuple[str, ...] = ("pascal",)
    lsp_result_languages: tuple[str, ...] = ()
    project_config_names: tuple[str, ...] = ()
    lsp_command: tuple[str, ...] = ()
    lsp_label: str = "Pascal"
    display_name: str = "Pascal"
    cli_label: str = "pas"
    dependencies: tuple[LanguageDependency, ...] = ()
    source_extensions: tuple[str, ...] = (".pas", ".pp", ".lpr", ".inc")

    def accepts_file(self, file_name: str) -> bool:
        return file_name.casefold().endswith(self.source_extensions)

    def accepts_project_config(self, file_name: str) -> bool:
        return False

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_pascal(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        discovery_exclusions: DiscoveryExclusions,
    ) -> BoundLanguage:
        return BoundPascalLanguage.create(root, path_to_id, discovery_exclusions)

    def normalize_module_id(self, relative_path: str) -> str:
        lowered = relative_path.casefold()
        if lowered.endswith(".inc"):
            return relative_path
        for extension in self.source_extensions:
            if lowered.endswith(extension):
                return relative_path[: -len(extension)]
        raise ValueError(f"Pascal module path has unsupported extension: {relative_path}")

    def output_language_code(self, result_language: str) -> str:
        if result_language != "pascal":
            raise ValueError(f"Unsupported Pascal result language: {result_language}")
        return "pas"

    def lsp_language_id(self, path: str) -> str:
        return "pascal"


PASCAL_LANGUAGE = PascalLanguage()


__all__ = ["PASCAL_LANGUAGE", "PascalLanguage", "parse_pascal"]
