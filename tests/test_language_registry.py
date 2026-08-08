from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.lang import (
    UnsupportedLanguageError,
    language_for_file,
    language_for_name,
)
from scripts.lang.python import PYTHON_LANGUAGE
from scripts.lang.rust import RUST_LANGUAGE
from scripts.lang.typescript import TYPESCRIPT_LANGUAGE


class LanguageRegistryTests(unittest.TestCase):
    def test_source_files_are_routed_to_the_owning_language(self) -> None:
        """/* REQ-LANG-001: Source discovery delegates file ownership to language implementations. */"""
        self.assertIs(PYTHON_LANGUAGE, language_for_file("worker.py"))
        self.assertIs(TYPESCRIPT_LANGUAGE, language_for_file("service.ts"))
        self.assertIs(TYPESCRIPT_LANGUAGE, language_for_file("view.tsx"))
        self.assertIs(RUST_LANGUAGE, language_for_file("module.rs"))
        self.assertIsNone(language_for_file("generated.d.ts"))

    def test_language_implementations_parse_their_source_contracts(self) -> None:
        """/* REQ-LANG-002: Each language implementation produces the shared FileResult contract. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_path = root / "worker.py"
            python_source = "from jobs import run\n\ndef execute():\n    return run()\n"
            python_path.write_text(python_source, encoding="utf-8")
            python_result = PYTHON_LANGUAGE.parse(
                str(python_path),
                python_source,
                root.name,
                python_path.stat().st_mtime,
            )

            typescript_path = root / "service.ts"
            typescript_source = (
                'import { dependency } from "./dependency";\n'
                "export const execute = () => dependency;\n"
            )
            typescript_path.write_text(typescript_source, encoding="utf-8")
            typescript_result = TYPESCRIPT_LANGUAGE.parse(
                str(typescript_path),
                typescript_source,
                root.name,
                typescript_path.stat().st_mtime,
            )

        self.assertEqual("python", python_result.language)
        self.assertEqual(["jobs"], python_result.imports)
        self.assertIn("execute", python_result.exported_names)
        self.assertEqual("typescript", typescript_result.language)
        self.assertEqual(["./dependency"], typescript_result.imports)
        self.assertIn("execute", typescript_result.exported_names)

    def test_unknown_language_names_are_explicit_errors(self) -> None:
        """/* REQ-LANG-003: Unsupported parser dispatch never degrades into a silent fallback. */"""
        with self.assertRaises(UnsupportedLanguageError):
            language_for_name("go")

    def test_builder_cli_uses_language_implementations_in_script_mode(self) -> None:
        """/* REQ-LANG-004: Script-mode CLI composition loads and executes registered languages. */"""
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "worker.py").write_text(
                "def execute():\n    return True\n",
                encoding="utf-8",
            )
            (target / "service.ts").write_text(
                "export const execute = () => true;\n",
                encoding="utf-8",
            )
            (target / "rust_worker.rs").write_text(
                "pub fn execute() -> bool { true }\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(repository_root / "scripts" / "build_belief_map.py"),
                    "--root",
                    str(target),
                ],
                cwd=repository_root,
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn(
                "Found 3 source files (1 py, 1 ts, 1 rs)",
                completed.stdout,
            )
            self.assertTrue((target / ".belief_map.sexp").is_file())


if __name__ == "__main__":
    unittest.main()
