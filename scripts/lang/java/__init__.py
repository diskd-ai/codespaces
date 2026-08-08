from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import BoundLanguage, FileResult
from .imports import BoundJavaLanguage
from .parser import parse_java_treesitter


@dataclass(frozen=True)
class JavaLanguage:
    name: str = "java"
    result_languages: tuple[str, ...] = ("java",)
    lsp_result_languages: tuple[str, ...] = ("java",)
    project_config_names: tuple[str, ...] = (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
    )
    lsp_command: tuple[str, ...] = ("jdtls",)
    lsp_label: str = "Java"
    cli_label: str = "java"
    dependency_packages: tuple[str, ...] = (
        "tree-sitter",
        "tree-sitter-java",
    )
    source_extensions: tuple[str, ...] = (".java",)

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(".java")

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_java_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundLanguage:
        return BoundJavaLanguage.create(path_to_id)

    def normalize_module_id(self, relative_path: str) -> str:
        if not relative_path.endswith(".java"):
            raise ValueError(f"Java module path must end in .java: {relative_path}")
        return relative_path[:-5]

    def output_language_code(self, result_language: str) -> str:
        if result_language != "java":
            raise ValueError(f"Unsupported Java result language: {result_language}")
        return "java"

    def lsp_language_id(self, path: str) -> str:
        return "java"


JAVA_LANGUAGE = JavaLanguage()


__all__ = [
    "JAVA_LANGUAGE",
    "JavaLanguage",
    "parse_java_treesitter",
]
