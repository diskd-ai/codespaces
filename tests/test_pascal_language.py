from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts.build_belief_map import Ok, build_graph, discover_files
from scripts.lang.interface import FileResult
from scripts.lang.pascal import PASCAL_LANGUAGE, parse_pascal


class PascalLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_pascal(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_units_uses_types_methods_and_routines(self) -> None:
        """/* REQ-PAS-001: Pascal parsing preserves architecture-relevant declarations and dependencies. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "src/uWorker.pas",
                """
unit uWorker;

interface

uses
  Classes, SysUtils,
  uModel in 'model/uModel.pas';

type
  IRunner = interface
    procedure Run;
  end;

  TBaseWorker = class
  end;

  TWorker = class(TBaseWorker, IRunner)
  public
    constructor Create;
    class function Count: Integer;
    procedure Run;
    function Name: string;
  end;

  TDeferredWorker = class;

  TDeferredWorker = class
  public
    procedure Start;
  end;

  TWorkerState = record
    Active: Boolean;
  end;

function BuildWorker: TWorker;

implementation

uses uDatabase;

function BuildWorker: TWorker;
begin
  Result := TWorker.Create;
end;

end.
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("pascal", result.language)
        self.assertEqual(
            ["Classes", "SysUtils", "uModel", "uDatabase"],
            result.imports,
        )
        self.assertEqual("interface", entities["IRunner"]["kind"])
        self.assertEqual(["Run"], entities["IRunner"].get("methods", []))
        self.assertEqual("class", entities["TWorker"]["kind"])
        self.assertEqual(
            ["Create", "Count", "Run", "Name"],
            entities["TWorker"].get("methods", []),
        )
        self.assertEqual(
            ["TBaseWorker", "IRunner"],
            entities["TWorker"].get("bases", []),
        )
        self.assertEqual("type", entities["TWorkerState"]["kind"])
        self.assertEqual("class", entities["TDeferredWorker"]["kind"])
        self.assertEqual(["Start"], entities["TDeferredWorker"].get("methods", []))
        self.assertEqual(
            1,
            sum(
                entity["name"] == "TDeferredWorker"
                for entity in result.entities
            ),
        )
        self.assertEqual("function", entities["BuildWorker"]["kind"])
        self.assertEqual(["IRunner"], result.exports_abstract)
        self.assertEqual(["TBaseWorker"], result.extends)
        self.assertEqual(["IRunner"], result.implements)

    def test_parser_ignores_keywords_inside_comments_strings_and_bodies(self) -> None:
        """/* REQ-PAS-002: Lexical noise cannot create false modules or declarations. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "Noise.pp",
                """
unit Noise;
interface
uses RealUnit;
{ uses FakeCommentUnit; }
(* type TFake = class end; *)
const MessageText = 'uses FakeStringUnit; type TFakeString = class end;';
procedure Execute;
implementation
procedure Execute;
begin
  if MessageText = '' then
    Execute;
end;
end.
""".lstrip(),
            )

        names = [entity["name"] for entity in result.entities]
        self.assertEqual(["RealUnit"], result.imports)
        self.assertEqual(["Execute"], names)

    def test_graph_resolves_units_case_insensitively_and_honors_in_paths(self) -> None:
        """/* REQ-PAS-003: Pascal uses clauses resolve by unit identity and explicit relative path. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer = self._parse(
                root,
                "app/Main.lpr",
                """
program Main;
uses UMODEL, AliasUnit in '../shared/uActual.pas';
begin
end.
""".lstrip(),
            )
            model = self._parse(
                root,
                "model/uModel.pas",
                "unit uModel; interface implementation end.\n",
            )
            actual = self._parse(
                root,
                "shared/uActual.pas",
                "unit AliasUnit; interface implementation end.\n",
            )
            graph_result = build_graph([consumer, model, actual], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("app/Main", "model/uModel"),
                ("app/Main", "shared/uActual"),
            },
            imports,
        )

    def test_ambiguous_unit_names_fail_closed(self) -> None:
        """/* REQ-PAS-004: Duplicate Pascal unit names never create guessed dependency edges. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer = self._parse(
                root,
                "Main.lpr",
                "program Main; uses Shared; begin end.\n",
            )
            first = self._parse(
                root,
                "one/Shared.pas",
                "unit Shared; interface implementation end.\n",
            )
            second = self._parse(
                root,
                "two/Shared.pas",
                "unit Shared; interface implementation end.\n",
            )
            graph_result = build_graph([consumer, first, second], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        self.assertFalse(
            any(edge["type"] == "IMPORTS" for edge in graph_result.value.edges)
        )

    def test_same_directory_unit_wins_over_ambiguous_global_name(self) -> None:
        """/* REQ-PAS-017: A local Pascal unit wins over same-named units elsewhere. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer = self._parse(
                root,
                "soft/ipAdmin.lpr",
                "program ipAdmin; uses fMain; begin end.\n",
            )
            local = self._parse(
                root,
                "soft/fMain.pas",
                "unit fMain; interface implementation end.\n",
            )
            component = self._parse(
                root,
                "components/widget/fMain.pas",
                "unit fMain; interface implementation end.\n",
            )
            graph_result = build_graph([consumer, local, component], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual({("soft/ipAdmin", "soft/fMain")}, imports)

    def test_same_directory_include_cannot_shadow_compilation_unit(self) -> None:
        """/* REQ-PAS-018: Include fragments never satisfy a uses-clause unit import. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer = self._parse(
                root,
                "app/Main.lpr",
                "program Main; uses Foo; begin end.\n",
            )
            include = self._parse(
                root,
                "app/Foo.inc",
                "const FooValue = 1;\n",
            )
            unit = self._parse(
                root,
                "units/Foo.pas",
                "unit Foo; interface implementation end.\n",
            )
            graph_result = build_graph([consumer, include, unit], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual({("app/Main", "units/Foo")}, imports)

    def test_unresolved_explicit_in_path_does_not_fallback_to_unit_name(self) -> None:
        """/* REQ-PAS-008: An explicit but unresolved Pascal path blocks guessed unit-name fallback. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer = self._parse(
                root,
                "app/Main.lpr",
                "program Main; uses Target in '../missing/Target.pas'; begin end.\n",
            )
            unrelated = self._parse(
                root,
                "elsewhere/Target.pas",
                "unit Target; interface implementation end.\n",
            )
            graph_result = build_graph([consumer, unrelated], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        self.assertFalse(
            any(edge["type"] == "IMPORTS" for edge in graph_result.value.edges)
        )

    def test_resolver_read_failures_are_reported(self) -> None:
        """/* REQ-PAS-019: Resolver source-read failures emit an actionable diagnostic. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._parse(
                root,
                "src/Broken.pas",
                "unit Broken; interface implementation end.\n",
            )
            stderr = io.StringIO()
            with (
                patch.object(
                    Path,
                    "read_text",
                    side_effect=OSError("unreadable"),
                ),
                redirect_stderr(stderr),
            ):
                graph_result = build_graph([source], str(root))

        self.assertIsInstance(graph_result, Ok)
        self.assertIn(
            f"[belief-map] Cannot index Pascal declarations from {source.path}: unreadable",
            stderr.getvalue(),
        )

    def test_procedural_type_parameters_do_not_end_the_type_section(self) -> None:
        """/* REQ-PAS-009: const/var parameters in procedural types preserve following declarations. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "Hooks.pas",
                """
unit Hooks;
interface
type
  THook = procedure(Sender: TObject; var Value: Integer; const Name: String);
  TAfterHook = class
  public
    procedure Execute;
  end;
implementation
end.
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("type", entities["THook"]["kind"])
        self.assertEqual("class", entities["TAfterHook"]["kind"])
        self.assertEqual(["Execute"], entities["TAfterHook"].get("methods", []))
        self.assertNotIn("Execute", [
            entity["name"]
            for entity in result.entities
            if entity["kind"] == "function"
        ])

    def test_class_reference_does_not_consume_following_types(self) -> None:
        """/* REQ-PAS-010: class-of references remain scalar types and preserve following declarations. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "ClassRefs.pas",
                """
unit ClassRefs;
interface
type
  TBase = class end;
  TBaseClass = class of TBase;
  TConcrete = class(TBase)
  end;
implementation
end.
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("type", entities["TBaseClass"]["kind"])
        self.assertEqual("class", entities["TConcrete"]["kind"])
        self.assertEqual(["TBase"], entities["TConcrete"].get("bases", []))

    def test_generic_and_specialized_base_types_preserve_outer_names(self) -> None:
        """/* REQ-PAS-011: Generic inheritance records base types without arguments or specialize markers. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "Generics.pas",
                """
unit Generics;
interface
type
  IFoo = interface end;
  TFirst = class(TBase<Integer>, IFoo) end;
  TSecond = class(specialize TGenericBase<String>, IFoo) end;
implementation
end.
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual(["TBase", "IFoo"], entities["TFirst"].get("bases", []))
        self.assertEqual(
            ["TGenericBase", "IFoo"],
            entities["TSecond"].get("bases", []),
        )
        self.assertEqual(["TBase", "TGenericBase"], result.extends)
        self.assertEqual(["IFoo"], result.implements)

    def test_inheritance_resolves_case_insensitively_through_used_unit(self) -> None:
        """/* REQ-PAS-012: Pascal base types resolve only through case-insensitive used-unit scope. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._parse(
                root,
                "base/BaseUnit.pas",
                """
unit BaseUnit;
interface
type
  TBase = class end;
  IRunner = interface end;
implementation
end.
""".lstrip(),
            )
            consumer = self._parse(
                root,
                "app/Consumer.pas",
                """
unit Consumer;
interface
uses BASEUNIT;
type TChild = class(tbase, irunner) end;
implementation
end.
""".lstrip(),
            )
            graph_result = build_graph([consumer, base], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        self.assertEqual(["tbase"], consumer.extends)
        self.assertEqual(["irunner"], consumer.implements)
        self.assertEqual(
            {("app/Consumer", "base/BaseUnit")},
            {
                (edge["source"], edge["target"])
                for edge in graph_result.value.edges
                if edge["type"] == "CALLS_API"
            },
        )

    def test_inheritance_without_uses_does_not_guess_global_provider(self) -> None:
        """/* REQ-PAS-013: Pascal inheritance never links a globally unique but unimported type. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = self._parse(
                root,
                "unrelated/BaseUnit.pas",
                "unit BaseUnit; interface type TBase = class end; implementation end.\n",
            )
            consumer = self._parse(
                root,
                "Consumer.pas",
                "unit Consumer; interface type TChild = class(TBase) end; implementation end.\n",
            )
            graph_result = build_graph([consumer, unrelated], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        self.assertFalse(
            any(edge["type"] == "CALLS_API" for edge in graph_result.value.edges)
        )

    def test_explicit_unit_path_disambiguates_inherited_type_provider(self) -> None:
        """/* REQ-PAS-014: Explicit unit paths scope inherited types when names are duplicated. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = self._parse(
                root,
                "one/BaseOne.pas",
                "unit BaseOne; interface type TBase = class end; implementation end.\n",
            )
            other = self._parse(
                root,
                "two/BaseTwo.pas",
                "unit BaseTwo; interface type TBase = class end; implementation end.\n",
            )
            consumer = self._parse(
                root,
                "app/Consumer.pas",
                """
unit Consumer;
interface
uses BaseOne in '../one/BaseOne.pas';
type TChild = class(TBase) end;
implementation
end.
""".lstrip(),
            )
            graph_result = build_graph([consumer, selected, other], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        self.assertEqual(
            {("app/Consumer", "one/BaseOne")},
            {
                (edge["source"], edge["target"])
                for edge in graph_result.value.edges
                if edge["type"] == "CALLS_API"
            },
        )

    def test_conditional_explicit_paths_remain_ambiguous(self) -> None:
        """/* REQ-PAS-015: Competing conditional explicit paths never select an arbitrary provider. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._parse(
                root,
                "one/BaseUnit.pas",
                "unit BaseUnit; interface type TBase = class end; implementation end.\n",
            )
            second = self._parse(
                root,
                "two/BaseUnit.pas",
                "unit BaseUnit; interface type TBase = class end; implementation end.\n",
            )
            consumer = self._parse(
                root,
                "app/Consumer.pas",
                """
unit Consumer;
interface
{$ifdef FIRST_BASE}
uses BaseUnit in '../one/BaseUnit.pas';
{$else}
uses BaseUnit in '../two/BaseUnit.pas';
{$endif}
type TChild = class(TBase) end;
implementation
end.
""".lstrip(),
            )
            graph_result = build_graph([consumer, first, second], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        consumer_edges = [
            edge
            for edge in graph_result.value.edges
            if edge["source"] == "app/Consumer"
            and edge["type"] in {"CALLS_API", "IMPORTS"}
        ]
        self.assertEqual([], consumer_edges)

    def test_language_metadata_and_discovery_cover_free_pascal_sources(self) -> None:
        """/* REQ-PAS-005: Pascal registration owns standard FPC and Lazarus source extensions. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("unit.pas", "unit.pp", "program.lpr", "shared.inc"):
                (root / name).write_text("", encoding="utf-8")
            (root / "notes.txt").write_text("", encoding="utf-8")
            discovered = discover_files(str(root))

        self.assertEqual(
            ["program.lpr", "shared.inc", "unit.pas", "unit.pp"],
            sorted(Path(item[0]).name for item in discovered),
        )
        self.assertEqual("src/uWorker", PASCAL_LANGUAGE.normalize_module_id("src/uWorker.pas"))
        self.assertEqual("pas", PASCAL_LANGUAGE.output_language_code("pascal"))
        self.assertEqual((), PASCAL_LANGUAGE.lsp_result_languages)

    def test_discovery_ignores_only_pascal_sources_in_lazarus_backup_dirs(self) -> None:
        """/* REQ-PAS-016: Lazarus backup copies are excluded without hiding other languages. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            live = root / "soft/live.pas"
            pascal_backup = root / "soft/backup/live.pas"
            python_backup = root / "soft/backup/helper.py"
            for path in (live, pascal_backup, python_backup):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            discovered = discover_files(str(root))

        self.assertEqual(
            {"soft/live.pas", "soft/backup/helper.py"},
            {
                str(Path(item[0]).relative_to(root))
                for item in discovered
            },
        )

    def test_include_and_compilation_unit_with_same_stem_have_distinct_ids(self) -> None:
        """/* REQ-PAS-006: Pascal include fragments cannot collide with compilation units. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = self._parse(
                root,
                "src/shared.pas",
                "unit shared; interface implementation end.\n",
            )
            include = self._parse(
                root,
                "src/shared.inc",
                "const SharedValue = 1;\n",
            )
            graph_result = build_graph([unit, include], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Pascal graph build failed: {graph_result.error}")

        self.assertEqual(
            {"src/shared", "src/shared.inc"},
            {node["id"] for node in graph_result.value.nodes},
        )
        self.assertEqual(
            "src/shared.inc",
            PASCAL_LANGUAGE.normalize_module_id("src/shared.inc"),
        )


if __name__ == "__main__":
    unittest.main()
