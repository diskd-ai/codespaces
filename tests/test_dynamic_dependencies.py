from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.lang import LANGUAGES


class DynamicDependencyTests(unittest.TestCase):
    def _without_pythonpath(self) -> dict[str, str]:
        return {
            name: value
            for name, value in os.environ.items()
            if name != "PYTHONPATH"
        }

    def test_python_only_build_needs_no_tree_sitter_packages(self) -> None:
        """/* REQ-DEPS-001: A Python-only map builds when every optional Tree-sitter package is absent. */"""
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "worker.py").write_text(
                "def execute():\n    return True\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-B",
                    str(repository_root / "scripts" / "build_belief_map.py"),
                    "--root",
                    str(target),
                ],
                cwd=repository_root,
                capture_output=True,
                check=False,
                env=self._without_pythonpath(),
                text=True,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((target / ".belief_map.sexp").is_file())

    def test_missing_detected_language_dependency_has_targeted_error(self) -> None:
        """/* REQ-DEPS-002: A detected language with absent parser packages fails before publication with its exact install command. */"""
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "service.ts").write_text(
                "export const execute = () => true;\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-B",
                    str(repository_root / "scripts" / "build_belief_map.py"),
                    "--root",
                    str(target),
                ],
                cwd=repository_root,
                capture_output=True,
                check=False,
                env=self._without_pythonpath(),
                text=True,
            )

            self.assertEqual(2, completed.returncode)
            self.assertIn("TypeScript", completed.stderr)
            self.assertIn("tree-sitter-typescript==0.23.2", completed.stderr)
            self.assertIn("requirements/typescript.txt", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertFalse((target / ".belief_map.sexp").exists())
            self.assertFalse((target / ".belief_map_cache.json").exists())

    def test_language_requirement_files_match_registry_contracts(self) -> None:
        """/* REQ-DEPS-003: Per-language install files exactly match the parser versions declared by each adapter. */"""
        repository_root = Path(__file__).resolve().parents[1]
        dependency_languages = [
            language for language in LANGUAGES if language.dependencies
        ]

        for language in dependency_languages:
            requirements_path = (
                repository_root / "requirements" / f"{language.name}.txt"
            )
            installed_requirements = requirements_path.read_text(
                encoding="utf-8"
            ).splitlines()
            expected_requirements = [
                dependency.requirement for dependency in language.dependencies
            ]
            self.assertEqual(expected_requirements, installed_requirements)


if __name__ == "__main__":
    unittest.main()
