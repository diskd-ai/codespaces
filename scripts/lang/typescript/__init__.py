from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..interface import BoundLanguage, FileResult
from .imports import BoundTypeScriptLanguage
from .parser import parse_typescript_treesitter


@dataclass(frozen=True)
class TypeScriptLanguage:
    name: str = "typescript"
    result_languages: tuple[str, ...] = ("typescript", "tsx")
    lsp_result_languages: tuple[str, ...] = ("typescript", "tsx")
    project_config_names: tuple[str, ...] = ("tsconfig.json",)
    lsp_command: tuple[str, ...] = ("typescript-language-server", "--stdio")
    lsp_label: str = "TS"
    cli_label: str = "ts"
    dependency_packages: tuple[str, ...] = (
        "tree-sitter",
        "tree-sitter-typescript",
    )
    source_extensions: tuple[str, ...] = (".ts", ".tsx")

    def accepts_file(self, file_name: str) -> bool:
        return (
            (file_name.endswith(".ts") or file_name.endswith(".tsx"))
            and not file_name.endswith(".d.ts")
        )

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        return parse_typescript_treesitter(path, content, repo, mtime)

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundLanguage:
        return BoundTypeScriptLanguage.create(root, path_to_id, skip_directories)

    def normalize_module_id(self, relative_path: str) -> str:
        for extension in (".tsx", ".ts"):
            if relative_path.endswith(extension):
                module_id = relative_path[: -len(extension)]
                break
        else:
            raise ValueError(
                f"TypeScript module path must end in .ts or .tsx: {relative_path}"
            )
        if module_id.endswith("/index"):
            return module_id[: -len("/index")]
        return module_id

    def output_language_code(self, result_language: str) -> str:
        if result_language == "typescript":
            return "ts"
        if result_language == "tsx":
            return "tsx"
        raise ValueError(f"Unsupported TypeScript result language: {result_language}")

    def lsp_language_id(self, path: str) -> str:
        return "typescriptreact" if path.endswith(".tsx") else "typescript"


TYPESCRIPT_LANGUAGE = TypeScriptLanguage()


__all__ = [
    "TYPESCRIPT_LANGUAGE",
    "TypeScriptLanguage",
    "parse_typescript_treesitter",
]
