from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import build_belief_map
from scripts.git_ignore import GitIgnoreLoaded, load_git_ignored_paths
from scripts.lang.go.imports import _load_go_modules
from scripts.lang.interface import DiscoveryExclusions, FileResult
from scripts.lang.rust.imports import _load_rust_packages
from scripts.lang.typescript.imports import (
    _load_ts_packages,
    _load_ts_path_aliases,
)


BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_belief_map.py"


def _file_result(path: Path, language: str) -> FileResult:
    return FileResult(
        path=str(path),
        language=language,
        repo="fixture",
        mtime=0.0,
        content_hash="fixture",
        imports=[],
        exports_abstract=[],
        implements=[],
        extends=[],
        purpose="test fixture",
        naming_convention="snake_case",
        has_validation=False,
        entities=[],
        imported_names=[],
        exported_names=[],
    )


def _write_project_configuration(root: Path, project_name: str) -> Path:
    project = root / project_name
    source = project / "src" / "index.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const value = true;\n", encoding="utf-8")
    (project / "tsconfig.json").write_text(
        '{"compilerOptions":{"baseUrl":".","paths":{"@fixture/*":["src/*"]}}}\n',
        encoding="utf-8",
    )
    (project / "package.json").write_text(
        f'{{"name":"@fixture/{project_name}"}}\n',
        encoding="utf-8",
    )
    (project / "go.mod").write_text(
        f"module example.com/{project_name}\n",
        encoding="utf-8",
    )
    (project / "Cargo.toml").write_text(
        f'[package]\nname = "{project_name}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return source


class IssueSevenExclusionTests(unittest.TestCase):
    def test_cli_accepts_repeated_basenames_and_rejects_paths(self) -> None:
        """/* REQ-CS-034: Exclusion inputs are repeatable portable basenames. */"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            parsed = build_belief_map.parse_builder_options(
                [
                    "--root",
                    str(root),
                    "--exclude-dir",
                    "review-bundles",
                    "--exclude-dir",
                    "generated-fixtures",
                    "--exclude-dir",
                    "review-bundles",
                ]
            )
            if isinstance(parsed, build_belief_map.Err):
                self.fail(parsed.error)
            self.assertTrue(
                {
                    "review-bundles",
                    "generated-fixtures",
                }.issubset(parsed.value.skip_directories)
            )

            invalid_values = (
                "",
                ".",
                "..",
                "nested/review-bundles",
                r"nested\review-bundles",
                "/absolute/review-bundles",
                r"C:\review-bundles",
                "C:",
                "invalid\0name",
            )
            for invalid_value in invalid_values:
                with self.subTest(invalid_value=invalid_value):
                    invalid = build_belief_map.parse_builder_options(
                        [
                            "--root",
                            str(root),
                            "--exclude-dir",
                            invalid_value,
                        ]
                    )
                    self.assertIsInstance(invalid, build_belief_map.Err)

    def test_caller_exclusions_apply_to_every_configuration_walk(self) -> None:
        """/* REQ-CS-035: One policy owns source, package, tsconfig, and LSP discovery. */"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_source = _write_project_configuration(root, "live")
            stale_source = _write_project_configuration(root, "review-bundles")
            exclusions = DiscoveryExclusions(
                directory_names=frozenset(
                    build_belief_map.SKIP_DIRS | {"review-bundles"}
                ),
                paths=frozenset(),
            )

            discovered = build_belief_map.discover_files(str(root), exclusions)
            aliases = _load_ts_path_aliases(str(root), exclusions)
            packages = _load_ts_packages(str(root), exclusions)
            go_modules = _load_go_modules(str(root), exclusions)
            rust_packages = _load_rust_packages(str(root), exclusions)
            projects = build_belief_map.discover_projects(
                str(root),
                [
                    _file_result(live_source, "typescript"),
                    _file_result(stale_source, "typescript"),
                ],
                exclusions,
            )

        discovered_paths = {path for path, _, _ in discovered}
        self.assertIn(os.path.realpath(live_source), discovered_paths)
        self.assertNotIn(os.path.realpath(stale_source), discovered_paths)
        self.assertEqual(
            [str(root / "live")], [item.config_directory for item in aliases]
        )
        self.assertEqual(["@fixture/live"], [item.name for item in packages])
        self.assertEqual(
            ["example.com/live"], [item.import_path for item in go_modules]
        )
        self.assertEqual(["live"], [item.crate_name for item in rust_packages])
        self.assertEqual(
            [str(root / "live" / "tsconfig.json")],
            [project.config_file for project in projects],
        )

    def test_gitignore_excludes_tracked_source_and_configuration(self) -> None:
        """/* REQ-CS-036: Repository ignore rules apply even to tracked snapshot paths. */"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            live_source = _write_project_configuration(root, "live")
            stale_source = _write_project_configuration(root, "review-bundles")
            (root / ".gitignore").write_text(
                "review-bundles/\n",
                encoding="utf-8",
            )
            subprocess.run(("git", "add", "-f", "."), cwd=root, check=True)

            ignored_result = load_git_ignored_paths(
                str(root),
                frozenset(build_belief_map.SKIP_DIRS),
            )
            if not isinstance(ignored_result, GitIgnoreLoaded):
                self.fail(ignored_result.reason)
            exclusions = DiscoveryExclusions(
                directory_names=frozenset(build_belief_map.SKIP_DIRS),
                paths=ignored_result.paths,
            )
            discovered = build_belief_map.discover_files(str(root), exclusions)
            aliases = _load_ts_path_aliases(str(root), exclusions)
            packages = _load_ts_packages(str(root), exclusions)
            go_modules = _load_go_modules(str(root), exclusions)
            rust_packages = _load_rust_packages(str(root), exclusions)
            projects = build_belief_map.discover_projects(
                str(root),
                [
                    _file_result(live_source, "typescript"),
                    _file_result(stale_source, "typescript"),
                ],
                exclusions,
            )

        discovered_paths = {path for path, _, _ in discovered}
        self.assertIn(os.path.realpath(live_source), discovered_paths)
        self.assertNotIn(os.path.realpath(stale_source), discovered_paths)
        self.assertEqual(
            [str(root / "live")], [item.config_directory for item in aliases]
        )
        self.assertEqual(["@fixture/live"], [item.name for item in packages])
        self.assertEqual(
            ["example.com/live"], [item.import_path for item in go_modules]
        )
        self.assertEqual(["live"], [item.crate_name for item in rust_packages])
        self.assertEqual(
            [str(root / "live" / "tsconfig.json")],
            [project.config_file for project in projects],
        )

    def test_ignore_policy_changes_invalidate_cache_provenance(self) -> None:
        """/* REQ-CS-037: Ignore policy changes cannot reuse incompatible parse cache. */"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            (root / "live.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = root / "review-bundles" / "snapshot.py"
            snapshot.parent.mkdir()
            snapshot.write_text("STALE = True\n", encoding="utf-8")
            ignore_path = root / ".gitignore"
            ignore_path.write_text("review-bundles/\n", encoding="utf-8")
            subprocess.run(("git", "add", "-f", "."), cwd=root, check=True)
            command = (
                sys.executable,
                str(BUILD_SCRIPT),
                "--root",
                str(root),
            )

            excluded = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            excluded_map = (root / ".belief_map.sexp").read_text(encoding="utf-8")
            ignore_path.write_text("", encoding="utf-8")
            included = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            included_map = (root / ".belief_map.sexp").read_text(encoding="utf-8")

        self.assertIn("Found 1 source files", excluded.stdout)
        self.assertNotIn("review-bundles", excluded_map)
        self.assertIn("schema or builder fingerprint changed", included.stderr)
        self.assertIn("Found 2 source files", included.stdout)
        self.assertIn("review-bundles", included_map)


if __name__ == "__main__":
    unittest.main()
