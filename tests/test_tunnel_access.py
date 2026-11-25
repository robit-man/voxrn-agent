"""Verify that the public listener, and only it, accepts tunnel principals."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from service.tunnel_access import TunnelAccess, TunnelMiddleware


class TunnelTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.state = Path(temporary) / "tunnel-session.json"
        self.origin = "https://pages.example"
        self.endpoint = "https://service.trycloudflare.com"
        self.key = "a" * 43
        self.subject = "1" * 32
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "VOXRN_TUNNEL_PORT": "9311",
                    "VOXRN_TUNNEL_STATE": str(self.state),
                    "VOXRN_TUNNEL_PAGES_ORIGIN": self.origin,
                },
            )
        )
        self.state.with_name("tunnel-connector.json").write_text(
            json.dumps({"endpoint": self.endpoint})
        )
        self.write_key(self.key)

        async def identity(request):
            return JSONResponse(
                {
                    "subject": request.scope.get("state", {}).get("tunnel_subject"),
                    "authorization": request.headers.get("authorization"),
                    "origin": request.headers.get("origin"),
                }
            )

        async def live(socket):
            await socket.accept(subprotocol="voxrn.asr.v1")
            await socket.send_text(socket.scope["state"]["tunnel_subject"])

        app = Starlette(
            routes=[
                Route("/v1/capabilities", identity),
                Route("/docs/", identity),
                WebSocketRoute("/live", live),
            ]
        )
        app.add_middleware(TunnelMiddleware, access=TunnelAccess())
        self.client = self.enterContext(
            TestClient(app, base_url="https://edge.test:9311")
        )

    def write_key(self, key):
        self.state.write_text(json.dumps({"key": key, "subject": self.subject}))

    def get(self, *, key=None, origin=None, path="/v1/capabilities"):
        headers = {}
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        if origin is not None:
            headers["Origin"] = origin
        return self.client.get(path, headers=headers)

    def test_same_origin_console_and_pages_are_allowed_with_key(self):
        self.assertEqual(self.get(path="/").status_code, 200)
        self.assertEqual(self.get().status_code, 401)
        for origin in (self.origin, self.endpoint):
            result = self.get(key=self.key, origin=origin)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(
                result.json(),
                {
                    "subject": self.subject,
                    "authorization": None,
                    "origin": self.origin,
                },
            )
        self.assertEqual(
            self.get(key=self.key, origin="https://other.example").status_code, 401
        )
        self.assertEqual(
            self.get(path=f"/v1/capabilities?key={self.key}").status_code, 401
        )
        response = self.get(key=self.key, path="http://edge.test:8311/v1/capabilities")
        self.assertIsNone(response.json()["subject"])

    def test_rotation_and_close_revoke_credential(self):
        replacement = "b" * 43
        self.write_key(replacement)
        self.assertEqual(self.get(key=self.key).status_code, 401)
        self.assertEqual(self.get(key=replacement).json()["subject"], self.subject)
        self.state.unlink()
        self.assertEqual(self.get(key=replacement).status_code, 401)

    def test_websocket_selects_only_the_protocol_not_the_key(self):
        options = ["voxrn.asr.v1", f"voxrn-key.{self.key}"]
        url = "wss://edge.test:9311/live"
        with self.client.websocket_connect(
            url, headers={"Origin": self.endpoint}, subprotocols=options
        ) as socket:
            self.assertEqual(socket.accepted_subprotocol, "voxrn.asr.v1")
            self.assertEqual(socket.receive_text(), self.subject)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(url, subprotocols=["voxrn.asr.v1"]):
                self.fail("A keyless browser socket must be denied.")
