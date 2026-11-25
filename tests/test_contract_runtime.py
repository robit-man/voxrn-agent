import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from service.server import create_app
from service.wire import Principal
from support import HEADERS, TOKEN, mcp

ROOT = Path(__file__).resolve().parents[1]


class ContractRuntime(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.dict(os.environ, {"VOXRN_DEV_TOKEN": TOKEN}, clear=True)
        )
        directory = self.enterContext(tempfile.TemporaryDirectory())
        profile = json.loads((ROOT / "scripts/pages.json").read_bytes())
        self.kind = profile["service"]
        options = {"providers": {}} if self.kind == "agent" else {"models": {}}
        self.app = create_app(directory=directory, **options)
        self.client = self.enterContext(
            TestClient(
                self.app,
                base_url="http://localhost:8000",
            )
        )

    def test_live_contract_and_authorization(self):
        unauthorized = self.client.get("/v1/capabilities")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers["www-authenticate"], "Bearer")
        self.assertEqual(unauthorized.json()["code"], "unauthorized")
        response = self.client.get("/v1/capabilities", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"], [])
        registered = self.client.get("/contracts/openapi.json")
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(
            registered.json(),
            json.loads((ROOT / "contracts/openapi.json").read_bytes()),
        )

    def test_management_scope_is_required_even_for_an_authenticated_reader(self):
        reader = Principal(
            "test-issuer",
            "test-tenant",
            "test-reader",
            frozenset({f"{self.kind}:read"}),
        )
        with patch.object(
            self.app.state.auth, "authenticate", AsyncMock(return_value=reader)
        ):
            allowed = self.client.get("/v1/capabilities", headers=HEADERS)
            denied = self.client.put(
                "/v1/control/models/ci-missing-model",
                headers=HEADERS,
                json={"enabled": True},
            )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            (denied.status_code, denied.json()["code"]), (403, "insufficient_scope")
        )
        self.assertIn(f"{self.kind}:manage", denied.headers["www-authenticate"])

    def test_control_rejects_invalid_or_unavailable_work(self):
        route = "/v1/control/models/ci-missing-model"
        invalid = self.client.put(route, headers=HEADERS, json={"enabled": "true"})
        self.assertEqual(
            (invalid.status_code, invalid.json()["code"]), (400, "invalid_request")
        )
        missing = self.client.put(route, headers=HEADERS, json={"enabled": True})
        self.assertEqual(
            (missing.status_code, missing.json()["code"]), (404, "unknown_model")
        )
        unready = self.client.get("/readyz")
        self.assertEqual(
            (unready.status_code, unready.json()["code"]), (503, "not_ready")
        )
        self.assertEqual(unready.headers["retry-after"], "1")

    def test_mcp_runtime_lists_the_canonical_tool_schemas(self):
        self.client.headers.update(HEADERS)
        result = mcp(self.client, "tools/list")
        canonical = json.loads((ROOT / "contracts/mcp-tools.json").read_bytes())[
            "tools"
        ]
        expected = {tool["name"]: tool for tool in canonical}
        actual = {tool["name"]: tool for tool in result["tools"]}
        self.assertEqual(actual.keys(), expected.keys())
        for name, tool in expected.items():
            with self.subTest(tool=name):
                self.assertEqual(actual[name]["inputSchema"], tool["inputSchema"])
                self.assertEqual(actual[name]["outputSchema"], tool["outputSchema"])
