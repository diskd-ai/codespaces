from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import BoundLanguage, FileResult, LanguageDependency
from .imports import BoundPythonLanguage
from .parser import parse_python


@dataclass(frozen=True)
class PythonLanguage:
    name: str = "python"
    result_languages: tuple[str, ...] = ("python",)
    lsp_result_languages: tuple[str, ...] = ("python",)
    project_config_names: tuple[str, ...] = (
        "pyrightconfig.json",
        "pyproject.toml",
    )
    lsp_command: tuple[str, ...] = ("pyright-langserver", "--stdio")
    lsp_label: str = "Py"
    display_name: str = "Python"
    cli_label: str = "py"
    dependencies: tuple[LanguageDependency, ...] = ()
    source_extensions: tuple[str, ...] = (".py",)

    def accepts_file(self, file_name: str) -> bool:
        return file_name.endswith(".py")

    def accepts_project_config(self, file_name: str) -> bool:
        return file_name in self.project_config_names

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_python(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundLanguage:
        return BoundPythonLanguage(root, path_to_id)

    def normalize_module_id(self, relative_path: str) -> str:
        if not relative_path.endswith(".py"):
            raise ValueError(f"Python module path must end in .py: {relative_path}")
        module_id = relative_path[:-3]
        if module_id.endswith("/index"):
            return module_id[: -len("/index")]
        return module_id

    def output_language_code(self, result_language: str) -> str:
        if result_language != "python":
            raise ValueError(f"Unsupported Python result language: {result_language}")
        return "py"

    def lsp_language_id(self, path: str) -> str:
        return "python"


PYTHON_LANGUAGE = PythonLanguage()


__all__ = ["PYTHON_LANGUAGE", "PythonLanguage", "parse_python"]
