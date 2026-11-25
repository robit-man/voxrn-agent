import copy
from contextlib import redirect_stdout
from io import StringIO
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.contract_compatibility import (
    Compatibility,
    check_base,
    compatibility_digest,
)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def read(name):
    return json.loads((CONTRACTS / name).read_bytes())


class CompatibilityChecks(unittest.TestCase):
    def test_reports_removed_routes_statuses_and_changed_oauth_scope(self):
        name = "openapi.json"
        previous = read(name)
        candidate = copy.deepcopy(previous)
        candidate["paths"].pop("/readyz")
        read_operation = candidate["paths"]["/v1/capabilities"]["get"]
        read_operation["responses"].pop("200")
        read_operation["security"] = [{"oauth": ["replaced:read"]}]
        changes = Compatibility({name: previous}, {name: candidate}).run()
        self.assertIn("OpenAPI: removed path /readyz", changes)
        self.assertIn("GET /v1/capabilities: removed status 200", changes)
        self.assertTrue(any("authorization scopes changed" in item for item in changes))

    def test_required_request_field_is_detected_through_nested_reference(self):
        name = "openapi.json"
        previous = read(name)
        schemas = previous["components"]["schemas"]
        schemas["ModelEnableRequest"]["properties"]["ownership"] = {
            "$ref": "#/components/schemas/Ownership"
        }
        schemas["Ownership"] = {"type": "object", "properties": {}}
        candidate = copy.deepcopy(previous)
        updated = candidate["components"]["schemas"]["Ownership"]
        updated["properties"]["epoch"] = {"type": "integer"}
        updated["required"] = ["epoch"]
        changes = Compatibility({name: previous}, {name: candidate}).run()
        self.assertTrue(
            any(
                "ownership: newly required input field epoch" in item
                for item in changes
            )
        )

    def test_optional_response_addition_preserves_existing_wire(self):
        name = "openapi.json"
        previous = read(name)
        candidate = copy.deepcopy(previous)
        candidate["components"]["schemas"]["Capabilities"]["properties"][
            "optional_detail"
        ] = {"type": "string"}
        self.assertEqual(Compatibility({name: previous}, {name: candidate}).run(), [])

    def test_runtime_map_allows_optional_placement_and_protects_existing_fields(self):
        def health_fields(document):
            return document["components"]["schemas"]["RuntimeTelemetry"]["properties"][
                "health"
            ]["additionalProperties"]["properties"]

        name = "openapi.json"
        previous = read(name)
        candidate = copy.deepcopy(previous)
        health_fields(previous).pop("placement", None)
        fields = health_fields(candidate)
        fields.setdefault("placement", {"type": "object"})
        self.assertEqual(Compatibility({name: previous}, {name: candidate}).run(), [])

        fields.pop("rss_bytes")
        fields["score"]["type"] = "string"
        changes = Compatibility({name: previous}, {name: candidate}).run()
        self.assertTrue(
            any(
                "health.additionalProperties: removed field rss_bytes" in item
                for item in changes
            )
        )
        self.assertTrue(
            any(
                "health.additionalProperties.score: type changed" in item
                for item in changes
            )
        )

    def test_schema_valued_map_preserves_request_and_response_requiredness(self):
        previous = {
            "additionalProperties": {
                "type": "object",
                "properties": {"placement": {"type": "string"}},
            }
        }
        candidate = copy.deepcopy(previous)
        candidate["additionalProperties"]["required"] = ["placement"]

        request = Compatibility({}, {})
        request.schema(
            "model map", previous, candidate, "request", (previous, candidate)
        )
        self.assertEqual(
            request.changes,
            {"model map.additionalProperties: newly required input field placement"},
        )

        response = Compatibility({}, {})
        response.schema(
            "model map", previous, candidate, "response", (previous, candidate)
        )
        self.assertEqual(response.changes, set())

    def test_mcp_changes_are_reported_and_fingerprint_is_order_independent(self):
        name = "mcp-tools.json"
        previous = read(name)
        candidate = copy.deepcopy(previous)
        removed = candidate["tools"].pop()["name"]
        candidate["protocolVersion"] = "other-protocol"
        changes = Compatibility({name: previous}, {name: candidate}).run()
        self.assertIn(f"MCP: removed tool {removed}", changes)
        self.assertTrue(any("protocol version changed" in item for item in changes))
        self.assertEqual(
            compatibility_digest(changes), compatibility_digest(changes[::-1])
        )
        self.assertNotEqual(
            compatibility_digest(changes), compatibility_digest(changes[:1])
        )

    def test_websocket_subprotocol_change(self):
        if not (CONTRACTS / "asyncapi.json").is_file():
            self.skipTest("This service uses HTTP streaming")
        name = "asyncapi.json"
        previous = read(name)
        candidate = copy.deepcopy(previous)
        channel = next(iter(candidate["channels"].values()))
        channel["x-websocket-subprotocol"] = "replacement"
        changes = Compatibility({name: previous}, {name: candidate}).run()
        self.assertTrue(
            any("x-websocket-subprotocol changed" in item for item in changes)
        )

    def test_exact_git_base_and_findings_approval(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "contracts").mkdir()
        baseline = read("openapi.json")
        contract = root / "contracts/openapi.json"
        contract.write_text(json.dumps(baseline), encoding="utf-8")
        for command in (
            ["init", "--quiet"],
            ["add", "contracts/openapi.json"],
            [
                "-c",
                "user.name=Contract CI",
                "-c",
                "user.email=contract-ci@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "baseline",
            ],
        ):
            subprocess.run(["git", *command], cwd=root, check=True)
        baseline["paths"].pop("/readyz")
        contract.write_text(json.dumps(baseline), encoding="utf-8")
        with redirect_stdout(StringIO()) as captured:
            with self.assertRaisesRegex(SystemExit, "Unapproved"):
                check_base(root, "HEAD")

        report = captured.getvalue()
        base = re.search(r"against ([a-f0-9]{40})", report)[1]
        fingerprint = re.search(r"Fingerprint ([a-f0-9]{64})", report)[1]
        (root / ".github").mkdir()
        (root / ".github/contract-compatibility.json").write_text(
            json.dumps(
                {
                    "approvals": [
                        {
                            "base": base,
                            "fingerprint": fingerprint,
                            "migration": "Health route migration",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with redirect_stdout(StringIO()) as captured:
            check_base(root, "HEAD")
        self.assertIn(
            "Documented migration: Health route migration", captured.getvalue()
        )
