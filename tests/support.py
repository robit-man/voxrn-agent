import os
from unittest.mock import patch

from fastapi.testclient import TestClient

TOKEN = "offline-synthetic-test-token-123456"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PROTOCOL = "2026-07-28"


def connect(app):
    return TestClient(app, base_url="http://localhost:8000", headers=HEADERS)


def mcp(client, method, *, name=None, arguments=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    params = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientInfo": {
                "name": "conformance",
                "version": "1.0.0",
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    if name:
        headers["Mcp-Name"] = name
        params.update({"name": name, "arguments": arguments})
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    if response.status_code != 200:
        raise AssertionError(response.text)
    result = response.json()
    if "result" not in result:
        raise AssertionError(result)
    return result["result"]


def setup_env():
    return patch.dict(
        os.environ, {"VOXRN_DEV_TOKEN": TOKEN, "VOXRN_ALLOWED_HOSTS": "localhost:*"}
    )
