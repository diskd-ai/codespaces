from __future__ import annotations

from .csharp import CSHARP_LANGUAGE
from .go import GO_LANGUAGE
from .interface import (
    BoundLanguage,
    DiscoveryExclusions,
    Entity,
    FileResult,
    ImportedName,
    Language,
    LanguageDependency,
)
from .java import JAVA_LANGUAGE
from .pascal import PASCAL_LANGUAGE
from .python import PYTHON_LANGUAGE
from .rust import RUST_LANGUAGE
from .ruby import RUBY_LANGUAGE
from .typescript import TYPESCRIPT_LANGUAGE


class UnsupportedLanguageError(ValueError):
    """Raised when graph orchestration has no owner for a language value."""


LANGUAGES: tuple[Language, ...] = (
    PYTHON_LANGUAGE,
    TYPESCRIPT_LANGUAGE,
    RUST_LANGUAGE,
    CSHARP_LANGUAGE,
    JAVA_LANGUAGE,
    GO_LANGUAGE,
    RUBY_LANGUAGE,
    PASCAL_LANGUAGE,
)


def language_for_file(file_name: str) -> Language | None:
    for language in LANGUAGES:
        if language.accepts_file(file_name):
            return language
    return None


def language_for_name(name: str) -> Language:
    for language in LANGUAGES:
        if language.name == name:
            return language
    raise UnsupportedLanguageError(f"Unsupported source language: {name}")


def language_for_result(result_language: str) -> Language:
    for language in LANGUAGES:
        if result_language in language.result_languages:
            return language
    raise UnsupportedLanguageError(
        f"Unsupported parsed result language: {result_language}"
    )


__all__ = [
    "BoundLanguage",
    "DiscoveryExclusions",
    "Entity",
    "FileResult",
    "ImportedName",
    "LANGUAGES",
    "Language",
    "LanguageDependency",
    "UnsupportedLanguageError",
    "language_for_file",
    "language_for_name",
    "language_for_result",
]
