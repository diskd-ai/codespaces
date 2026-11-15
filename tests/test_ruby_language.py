from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_belief_map import Ok, build_graph, discover_files, render_sexp
from scripts.lang.interface import FileResult
from scripts.lang.ruby import RUBY_LANGUAGE, parse_ruby_treesitter


class RubyLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_ruby_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def _build(self, root: Path, results: list[FileResult]):
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"Ruby graph build failed: {graph_result.error}")
        return graph_result.value

    def test_parser_extracts_ruby_declarations_methods_and_dsl_relations(self) -> None:
        """/* REQ-RUBY-001: Ruby parsing preserves qualified declarations, methods, inheritance, mixins, requires, and Rails DSL relations. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "app/services/billing/charge.rb",
                """
require "json"
require_relative "support"

module Billing
  class Charge < ApplicationService
    include Auditable
    prepend Traceable
    extend Callable
    belongs_to :order, class_name: "Order"
    before_validation :normalize

    def call
      ChargeJob.perform_later(order.id)
      ReceiptMailer.paid(order).deliver_later
    end

    def self.build
      new
    end

    private

    def normalize
      true
    end
  end
end
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("ruby", result.language)
        self.assertEqual("module", entities["Billing"]["kind"])
        self.assertEqual("class", entities["Billing::Charge"]["kind"])
        self.assertEqual(
            ["ApplicationService"],
            entities["Billing::Charge"].get("bases", []),
        )
        self.assertEqual(
            ["call", "build", "normalize"],
            entities["Billing::Charge"].get("methods", []),
        )
        self.assertIn("Billing::Charge#call", entities)
        self.assertIn("Billing::Charge.build", entities)
        self.assertIn("Billing::Charge#normalize", entities)
        self.assertEqual(
            {"Billing", "Billing::Charge"},
            set(result.exported_names),
        )
        self.assertEqual(
            {"concern", "association", "callback", "job", "mailer"},
            {relation["relation"] for relation in result.relations},
        )

    def test_graph_resolves_zeitwerk_constants_and_marks_rails_relations(self) -> None:
        """/* REQ-RUBY-002: Rails constants resolve through project-local Zeitwerk roots and retain relation metadata. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "app/models/application_record.rb",
                    "class ApplicationRecord\nend\n",
                ),
                self._parse(
                    root,
                    "app/models/order.rb",
                    "class Order < ApplicationRecord\nend\n",
                ),
                self._parse(
                    root,
                    "app/models/concerns/auditable.rb",
                    "module Auditable\nend\n",
                ),
                self._parse(
                    root,
                    "app/jobs/charge_job.rb",
                    "class ChargeJob\nend\n",
                ),
                self._parse(
                    root,
                    "app/mailers/receipt_mailer.rb",
                    "class ReceiptMailer\nend\n",
                ),
                self._parse(
                    root,
                    "app/services/billing/charge.rb",
                    """
module Billing
  class Charge < ApplicationRecord
    include Auditable
    belongs_to :order
    before_validation :normalize

    def call
      ChargeJob.perform_later(order.id)
      ReceiptMailer.paid(order).deliver_later
    end

    def normalize
      true
    end
  end
end
""".lstrip(),
                ),
            ]
            graph = self._build(root, results)

        imports = {
            (edge["source"], edge["target"]): set(edge.get("relations", []))
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
        }
        source = "app/services/billing/charge"
        self.assertEqual(
            {"concern"},
            imports[(source, "app/models/concerns/auditable")],
        )
        self.assertEqual(
            {"association"},
            imports[(source, "app/models/order")],
        )
        self.assertEqual({"job"}, imports[(source, "app/jobs/charge_job")])
        self.assertEqual(
            {"mailer"},
            imports[(source, "app/mailers/receipt_mailer")],
        )
        calls_api = {
            (edge["source"], edge["target"])
            for edge in graph.edges
            if edge["type"] == "CALLS_API"
        }
        self.assertIn(
            (source, "app/models/application_record"),
            calls_api,
        )
        callback_edges = [
            edge
            for edge in graph.edges
            if edge["type"] == "REFERENCES"
            and "callback" in edge.get("relations", [])
        ]
        self.assertEqual(
            [
                (
                    f"{source}::Billing::Charge",
                    f"{source}::Billing::Charge#normalize",
                )
            ],
            [
                (edge["source"], edge["target"])
                for edge in callback_edges
            ],
        )

    def test_graph_resolves_require_relative_and_rspec_constant_edges(self) -> None:
        """/* REQ-RUBY-003: Explicit relative requires and RSpec constant references create local reverse-dependency edges. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "app/services/orders/checkout.rb",
                    "module Orders\n  class Checkout\n  end\nend\n",
                ),
                self._parse(
                    root,
                    "spec/services/orders/support.rb",
                    "module OrdersSupport\nend\n",
                ),
                self._parse(
                    root,
                    "spec/services/orders/checkout_spec.rb",
                    """
require_relative "support"

RSpec.describe Orders::Checkout do
end
""".lstrip(),
                ),
            ]
            graph = self._build(root, results)

        imports = {
            (edge["source"], edge["target"]): set(edge.get("relations", []))
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
        }
        source = "spec/services/orders/checkout_spec"
        self.assertIn((source, "spec/services/orders/support"), imports)
        self.assertEqual(
            {"spec"},
            imports[(source, "app/services/orders/checkout")],
        )

    def test_rspec_include_matcher_is_not_classified_as_a_mixin(self) -> None:
        """/* REQ-RUBY-007: RSpec include matchers retain spec dependency metadata without false concern edges. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "app/commands/add_command.rb",
                    "class AddCommand\nend\n",
                ),
                self._parse(
                    root,
                    "spec/commands/registry_spec.rb",
                    """
RSpec.describe "registry" do
  it "contains the command" do
    expect(registry).to include(AddCommand)
  end
end

class RegistrySpecHelper
  def verify(registry)
    expect(registry).to include(AddCommand)
  end
end
""".lstrip(),
                ),
            ]
            graph = self._build(root, results)

        command_edge = next(
            edge
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
            and edge["target"] == "app/commands/add_command"
        )
        self.assertEqual(["spec"], command_edge.get("relations", []))

    def test_ambiguous_reopened_constant_does_not_create_a_guessed_edge(self) -> None:
        """/* REQ-RUBY-004: Reopened or ambiguous Ruby constants remain unresolved instead of producing guessed dependencies. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "app/models/shared_one.rb",
                    "class Shared\nend\n",
                ),
                self._parse(
                    root,
                    "app/models/shared_two.rb",
                    "class Shared\nend\n",
                ),
                self._parse(
                    root,
                    "app/services/consumer.rb",
                    "class Consumer\n  Shared.call\nend\n",
                ),
            ]
            graph = self._build(root, results)

        consumer_edges = [
            edge
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
            and edge["source"] == "app/services/consumer"
        ]
        self.assertEqual([], consumer_edges)

    def test_inherited_constant_resolves_through_the_local_superclass(self) -> None:
        """/* REQ-RUBY-008: Unqualified constants inherited from a local superclass resolve to that provider. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "app/commands/base_command.rb",
                    "class BaseCommand\n  ADD_TIME = 'add_time'\nend\n",
                ),
                self._parse(
                    root,
                    "app/commands/add_command.rb",
                    "class AddCommand < BaseCommand\n  def call\n    ADD_TIME\n  end\nend\n",
                ),
            ]
            graph = self._build(root, results)

        references = {
            (edge["source"], edge["target"])
            for edge in graph.edges
            if edge["type"] == "REFERENCES"
        }
        self.assertIn(
            (
                "app/commands/add_command",
                "app/commands/base_command::BaseCommand::ADD_TIME",
            ),
            references,
        )

    def test_project_acronyms_resolve_association_targets(self) -> None:
        """/* REQ-RUBY-005: Static Rails acronym configuration participates in association constant resolution. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "config/initializers/inflections.rb",
                    "ActiveSupport::Inflector.inflections do |inflect|\n"
                    "  inflect.acronym \"API\"\n"
                    "end\n",
                ),
                self._parse(
                    root,
                    "app/models/api_client.rb",
                    "class APIClient\nend\n",
                ),
                self._parse(
                    root,
                    "app/models/account.rb",
                    "class Account\n  belongs_to :api_client\nend\n",
                ),
            ]
            graph = self._build(root, results)

        association_edges = [
            edge
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
            and "association" in edge.get("relations", [])
        ]
        self.assertEqual(
            [("app/models/account", "app/models/api_client")],
            [
                (edge["source"], edge["target"])
                for edge in association_edges
            ],
        )

    def test_association_source_resolves_and_polymorphic_target_stays_open(self) -> None:
        """/* REQ-RUBY-009: Rails through/source associations resolve their declared model while polymorphic associations remain unresolved. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(root, "app/models/user.rb", "class User\nend\n"),
                self._parse(
                    root,
                    "app/models/subject.rb",
                    "class Subject\nend\n",
                ),
                self._parse(
                    root,
                    "app/models/account.rb",
                    """
class Account
  has_many :rated_users, through: :ratings, source: :user
  belongs_to :subject, polymorphic: true
end
""".lstrip(),
                ),
            ]
            graph = self._build(root, results)

        association_targets = {
            edge["target"]
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
            and "association" in edge.get("relations", [])
        }
        self.assertEqual({"app/models/user"}, association_targets)

    def test_ruby_metadata_discovery_and_relation_rendering(self) -> None:
        """/* REQ-RUBY-006: Ruby registration owns rb/rake discovery, canonical IDs, LSP metadata, and S-expression relation flags. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ruby_path = root / "app" / "models" / "order.rb"
            ruby_path.parent.mkdir(parents=True)
            ruby_path.write_text("class Order\nend\n", encoding="utf-8")
            rake_path = root / "lib" / "tasks" / "orders.rake"
            rake_path.parent.mkdir(parents=True)
            rake_path.write_text("task :orders do\nend\n", encoding="utf-8")
            discovered = discover_files(str(root))

        self.assertEqual(2, len(discovered))
        self.assertTrue(all(language == "ruby" for _, language, _ in discovered))
        self.assertEqual(
            "app/models/order",
            RUBY_LANGUAGE.normalize_module_id("app/models/order.rb"),
        )
        self.assertEqual(
            "lib/tasks/orders",
            RUBY_LANGUAGE.normalize_module_id("lib/tasks/orders.rake"),
        )
        self.assertEqual("rb", RUBY_LANGUAGE.output_language_code("ruby"))
        self.assertEqual("ruby", RUBY_LANGUAGE.lsp_language_id("order.rb"))
        self.assertEqual(("ruby-lsp",), RUBY_LANGUAGE.lsp_command)
        rendered = render_sexp(
            [
                {
                    "id": "app/models/account",
                    "language": "ruby",
                    "repo": "example",
                    "purpose": "data model",
                    "invariant": {"naming": "snake_case", "package": "example"},
                },
                {
                    "id": "app/models/order",
                    "language": "ruby",
                    "repo": "example",
                    "purpose": "data model",
                    "invariant": {"naming": "snake_case", "package": "example"},
                },
            ],
            [
                {
                    "source": "app/models/account",
                    "target": "app/models/order",
                    "type": "IMPORTS",
                    "via_base": False,
                    "relations": ["association"],
                }
            ],
            "/example",
            "syntax",
            2,
        )
        self.assertIn(":association", rendered)


if __name__ == "__main__":
    unittest.main()
