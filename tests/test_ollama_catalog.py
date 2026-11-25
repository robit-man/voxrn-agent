"""Catalog interoperability through real Agent routes and an isolated Ollama transport."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from service.provider import Ollama, OllamaProfile
from service.server import create_app
from service.wire import Authorization, Principal
from support import HEADERS, TOKEN, connect
from test_service import provision


CATALOG = "/v1/control/ollama/catalog"
REGISTER = "/v1/control/ollama/registrations"
BACKEND = "http://catalog.test:11434"
BASE = "base:latest"
NEW = "library/chat:exact"
EMBED = "embedding:latest"
BASE_DIGEST = "a" * 64
CHAT_DIGEST = "b" * 64
EMBED_DIGEST = "c" * 64
ENGINE = "0.40.18"
MESSAGES = [
    {"role": "system", "content": " Preserve this exact history. "},
    {"role": "assistant", "content": " A retained reply. "},
    {"role": "user", "content": "  And preserve the final message.  "},
]


class CatalogConformance(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(
            patch.dict(
                "os.environ",
                {"VOXRN_DEV_TOKEN": TOKEN, "VOXRN_ALLOWED_HOSTS": "localhost:*"},
                clear=True,
            )
        )
        self.calls = []
        self.connections = []
        self.engine = ENGINE
        self.tags = {BASE: BASE_DIGEST, NEW: CHAT_DIGEST, EMBED: EMBED_DIGEST}
        self.capabilities = {NEW: ["completion"], EMBED: ["embedding"]}
        self.enterContext(
            patch("service.provider.ollama_client", side_effect=self.provider_client)
        )

    def provider_client(self, url, credential_env=None):
        self.assertEqual(url, BACKEND)
        self.connections.append((url, credential_env))
        return httpx.AsyncClient(
            base_url=url, transport=httpx.MockTransport(self.upstream)
        )

    def upstream(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path == "/api/version":
            return httpx.Response(200, json={"version": self.engine})
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": tag, "digest": digest, "size": 5280}
                        for tag, digest in self.tags.items()
                    ]
                },
            )
        if path == "/api/show":
            return httpx.Response(
                200, json={"capabilities": self.capabilities[body["model"]]}
            )
        if path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "message": {"content": "An exact isolated provider receipt."},
                    "done": True,
                    "done_reason": "stop",
                },
            )
        self.fail(f"Unexpected provider request {path}")

    def app(self, **options):
        return create_app(
            providers={
                "qwen-local": Ollama(
                    OllamaProfile(
                        model="qwen-local",
                        tag=BASE,
                        digest=BASE_DIGEST,
                        engine_revision=ENGINE,
                        url=BACKEND,
                        enabled=False,
                        managed=True,
                    )
                )
            },
            directory=self.directory,
            **options,
        )

    def await_state(self, client, alias, state):
        for _ in range(100):
            installed = client.get("/v1/control/models").json()["models"]
            profile = next(
                item for item in installed if item["identity"]["model"] == alias
            )
            if profile["state"] == state:
                return profile
            time.sleep(0.025)
        self.fail(f"Model did not reach {state}: {profile}")

    def test_discover_register_enable_chat_and_restart(self):
        with connect(self.app()) as client:
            result = client.get(CATALOG)
            self.assertEqual(result.status_code, 200, result.text)
            catalog = result.json()
            self.assertEqual(
                catalog["provider"],
                {"url": BACKEND, "engine": "ollama", "engine_revision": ENGINE},
            )
            by_tag = {entry["tag"]: entry for entry in catalog["models"]}
            self.assertEqual(
                by_tag[NEW], {"tag": NEW, "digest": CHAT_DIGEST, "size_bytes": 5280}
            )
            self.assertEqual(by_tag[BASE]["registered_model"], "qwen-local")
            self.assertEqual(
                self.calls, [("GET", "/api/version", None), ("GET", "/api/tags", None)]
            )

            registration = client.post(
                REGISTER, json={"tag": NEW, "digest": CHAT_DIGEST}
            )
            self.assertEqual(registration.status_code, 201, registration.text)
            profile = registration.json()
            identity = profile["identity"]
            alias = identity["model"]
            self.assertEqual(
                identity,
                {
                    "model": alias,
                    "model_revision": CHAT_DIGEST,
                    "engine": "ollama",
                    "engine_revision": ENGINE,
                },
            )
            self.assertEqual(
                (profile["enabled"], profile["state"]), (False, "disabled")
            )
            self.assertEqual(self.calls[-1], ("POST", "/api/show", {"model": NEW}))
            repeated = client.post(REGISTER, json={"digest": CHAT_DIGEST, "tag": NEW})
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(repeated.json(), profile)
            self.assertEqual(
                next(
                    item
                    for item in client.get(CATALOG).json()["models"]
                    if item["tag"] == NEW
                )["registered_model"],
                alias,
            )
            payload = {"model": alias, "messages": MESSAGES}
            disabled = client.post("/v1/chat/completions", json=payload)
            self.assertEqual(
                (disabled.status_code, disabled.json()["code"]),
                (503, "model_unavailable"),
            )
            self.assertNotIn("/api/chat", {path for _, path, _ in self.calls})

            enabled = client.put(f"/v1/control/models/{alias}", json={"enabled": True})
            self.assertEqual(enabled.status_code, 202, enabled.text)
            self.await_state(client, alias, "ready")
            capability = client.get("/v1/capabilities").json()["models"][0]
            self.assertEqual(capability["identity"], identity)
            self.assertFalse(capability["features"]["workflow"])
            self.assertFalse(capability["features"]["structured_outputs"])
            result = client.post("/v1/chat/completions", json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["model"], alias)
            self.assertEqual(result.headers["X-Model-Revision"], CHAT_DIGEST)
            self.assertEqual(result.headers["X-Engine-Revision"], ENGINE)
            actual = [body for _, path, body in self.calls if path == "/api/chat"]
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual[0]["model"], NEW)
            self.assertEqual(actual[0]["messages"], MESSAGES)

        with connect(self.app()) as client:
            restored = self.await_state(client, alias, "ready")
            self.assertTrue(restored["enabled"])
            self.assertEqual(restored["identity"], identity)
            self.tags[NEW] = "d" * 64
            self.assertEqual(client.get("/v1/capabilities").json()["models"], [])
            stale = client.post("/v1/chat/completions", json=payload)
            self.assertEqual(
                (stale.status_code, stale.json()["code"]), (503, "provider_not_ready")
            )
            self.assertEqual(sum(path == "/api/chat" for _, path, _ in self.calls), 1)

        bundle = self.directory / "imported-workflow"
        provision(bundle, type("Imported", (), {"identity": identity}))
        with self.assertRaisesRegex(RuntimeError, "model identity did not match"):
            self.app(workflow_paths=[bundle])

    def test_rejects_stale_pin_embedding_and_client_destination(self):
        with connect(self.app()) as client:
            malformed = client.post(
                REGISTER,
                json={
                    "tag": NEW,
                    "digest": CHAT_DIGEST,
                    "url": "https://request.invalid",
                },
            )
            self.assertEqual(malformed.status_code, 400, malformed.text)
            self.assertEqual(self.calls, [])
            stale = client.post(REGISTER, json={"tag": NEW, "digest": EMBED_DIGEST})
            self.assertEqual(
                (stale.status_code, stale.json()["code"]), (409, "ollama_pin_changed")
            )
            self.assertNotIn("/api/show", {path for _, path, _ in self.calls})
            unsupported = client.post(
                REGISTER, json={"tag": EMBED, "digest": EMBED_DIGEST}
            )
            self.assertEqual(
                (unsupported.status_code, unsupported.json()["code"]),
                (409, "ollama_chat_unsupported"),
            )
            self.assertFalse((self.directory / "ollama-registrations.json").exists())

    def test_catalog_requires_management_scope_before_provider_access(self):
        with TestClient(self.app(), base_url="http://localhost:8000") as client:
            for method, path in (("GET", CATALOG), ("POST", REGISTER)):
                self.assertEqual(client.request(method, path).status_code, 401)
            inference = Principal(
                "test", "scope", "inference", frozenset({"agent:infer"})
            )
            with patch.object(Authorization, "authenticate", return_value=inference):
                for method, path in (("GET", CATALOG), ("POST", REGISTER)):
                    result = client.request(method, path, headers=HEADERS)
                    self.assertEqual(
                        (result.status_code, result.json()["code"]),
                        (403, "insufficient_scope"),
                    )
            self.assertEqual(self.calls, [])

    def test_external_provider_can_boot_without_a_curated_profile(self):
        with patch.dict(
            "os.environ",
            {"VOXRN_AGENT_OLLAMA_CATALOG_URL": BACKEND},
        ):
            with connect(create_app(directory=self.directory)) as client:
                empty = client.get("/v1/control/models")
                self.assertEqual(empty.json()["models"], [])
                discovered = client.get(CATALOG)
                self.assertEqual(discovered.status_code, 200, discovered.text)
                self.assertEqual(
                    self.connections, [(BACKEND, "VOXRN_AGENT_OLLAMA_CATALOG_TOKEN")]
                )
                registration = client.post(
                    REGISTER, json={"tag": NEW, "digest": CHAT_DIGEST}
                )
                self.assertEqual(registration.status_code, 201, registration.text)
                self.assertFalse(registration.json()["enabled"])
