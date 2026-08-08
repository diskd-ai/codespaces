from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, TypedDict


class EntityPayloadRequired(TypedDict):
    name: str
    kind: str
    line: int


class EntityPayload(EntityPayloadRequired, total=False):
    methods: list[str]
    decorators: list[str]
    bases: list[str]


class ImportedNamePayload(TypedDict):
    local: str
    original: str
    module: str


@dataclass
class Entity:
    name: str
    kind: str
    line: int
    methods: list[str]
    decorators: list[str]
    bases: list[str]

    def to_dict(self) -> EntityPayload:
        result: EntityPayload = {
            "name": self.name,
            "kind": self.kind,
            "line": self.line,
        }
        if self.methods:
            result["methods"] = self.methods
        if self.decorators:
            result["decorators"] = self.decorators
        if self.bases:
            result["bases"] = self.bases
        return result


@dataclass
class ImportedName:
    """A single name imported from another module."""

    local_name: str
    original_name: str
    module: str

    def to_dict(self) -> ImportedNamePayload:
        return {
            "local": self.local_name,
            "original": self.original_name,
            "module": self.module,
        }


@dataclass
class FileResult:
    """Language-neutral source facts consumed by graph construction."""

    path: str
    language: str
    repo: str
    mtime: float
    content_hash: str
    imports: list[str]
    exports_abstract: list[str]
    implements: list[str]
    extends: list[str]
    purpose: str
    naming_convention: str
    has_validation: bool
    entities: list[EntityPayload]
    imported_names: list[ImportedNamePayload]
    exported_names: list[str]


class BoundLanguage(Protocol):
    """A language implementation bound to one graph-build context."""

    def resolve_import(self, import_name: str, source_path: str) -> str | None:
        """Resolve one source import to a graph node ID when it is local."""
        ...

    def resolve_additional_imports(self, result: FileResult) -> tuple[str, ...]:
        """Resolve language forms that carry more than one import target."""
        ...


class Language(Protocol):
    """Contract implemented by each source-language boundary."""

    @property
    def name(self) -> str: ...

    @property
    def result_languages(self) -> tuple[str, ...]: ...

    @property
    def lsp_result_languages(self) -> tuple[str, ...]: ...

    @property
    def project_config_names(self) -> tuple[str, ...]: ...

    @property
    def lsp_command(self) -> tuple[str, ...]: ...

    @property
    def lsp_label(self) -> str: ...

    @property
    def cli_label(self) -> str: ...

    @property
    def dependency_packages(self) -> tuple[str, ...]: ...

    @property
    def source_extensions(self) -> tuple[str, ...]: ...

    def accepts_file(self, file_name: str) -> bool:
        """Return whether this implementation owns the source file."""
        ...

    def accepts_project_config(self, file_name: str) -> bool:
        """Return whether a project config belongs to this language."""
        ...

    def parse(
        self,
        path: str,
        content: str,
        repo: str,
        mtime: float,
    ) -> FileResult:
        """Parse source into the shared, language-neutral result contract."""
        ...

    def bind(
        self,
        root: str,
        path_to_id: Mapping[str, str],
        skip_directories: frozenset[str],
    ) -> BoundLanguage:
        """Bind import resolution to one repository graph."""
        ...

    def normalize_module_id(self, relative_path: str) -> str:
        """Convert a source path to this language's canonical module ID."""
        ...

    def output_language_code(self, result_language: str) -> str:
        """Return the compact language code written to the belief map."""
        ...

    def lsp_language_id(self, path: str) -> str:
        """Return the LSP languageId for a source path."""
        ...
