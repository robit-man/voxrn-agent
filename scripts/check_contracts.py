"""Validate canonical contracts against official schemas and the local application."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import urlopen

from jsonschema import Draft7Validator, Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.jsonschema import DRAFT7, DRAFT202012

from contract_compatibility import HTTP_METHODS, check_base, dereference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SCHEMAS = {
    "openapi-3.2": (
        "https://spec.openapis.org/oas/3.2/schema/2026-11-12",
        "f1f7ee38d431a7bb1082e4df2288dbe1d4d3f49081b497af249c07b77737fb43",
    ),
    "asyncapi-3.1": (
        "https://raw.githubusercontent.com/asyncapi/spec-json-schemas/"
        "1ae5c69d7679f1925a20ab93fd2c7faeef0916cf/schemas/3.1.0.json",
        "52cdfc5a54ac7edd0b7c00d320d3f7b85b72e05377479a22a163e56f6d3b896c",
    ),
}
FIXED_HTTP_ROUTES = {
    ("GET", "/.well-known/oauth-protected-resource/mcp"),
    ("GET", "/contracts/openapi.json"),
}


def fail(message):
    raise ValueError(message)


def strict_json(data):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail(f"Duplicate JSON key {key!r}")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=object_pairs, parse_constant=fail)


def official_schema(name, cache):
    url, checksum = SCHEMAS[name]
    if cache is not None:
        data = (cache / f"{name}.json").read_bytes()
    else:
        with urlopen(url, timeout=30) as response:
            data = response.read(2_000_001)
    if hashlib.sha256(data).hexdigest() != checksum:
        fail(f"Official schema checksum mismatch for {name}")
    return strict_json(data)


def check_refs(node, document):
    if isinstance(node, dict):
        if "$ref" in node:
            dereference(node, document)
        for value in node.values():
            check_refs(value, document)
    elif isinstance(node, list):
        for value in node:
            check_refs(value, document)


def check_instance(value, schema, *, validator=Draft202012Validator):
    validator(schema, format_checker=FormatChecker()).validate(value)


def check_contract_example(value, document, pointer, *, validator=Draft202012Validator):
    dereference({"$ref": pointer}, document)
    spec = DRAFT7 if validator is Draft7Validator else DRAFT202012
    base = "urn:voxrn:canonical-contract"
    registry = Registry().with_resource(base, spec.create_resource(document))
    validator(
        {"$ref": f"{base}{pointer}"},
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(value)


def check_embedded_schemas(node):
    """Cover inline OpenAPI schemas, including streaming itemSchema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"schema", "itemSchema"}:
                Draft202012Validator.check_schema(value)
            elif key not in {"example", "examples", "x-examples"}:
                check_embedded_schemas(value)
    elif isinstance(node, list):
        for value in node:
            check_embedded_schemas(value)


def openapi_operations(document):
    seen = set()
    result = {}
    for path, node in document["paths"].items():
        path_item = dereference(node, document)
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            identifier = operation["operationId"]
            if identifier in seen:
                fail(f"Duplicate OpenAPI operationId {identifier!r}")
            seen.add(identifier)
            result[method.upper(), path] = operation
    return result


def check_routes(openapi, asyncapi, mcp_document, service):
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from starlette.routing import Mount
    from service.server import create_app
    from service.wire import PROTOCOL

    if mcp_document["protocolVersion"] != PROTOCOL:
        fail("MCP protocolVersion differs from the service protocol")

    # Compose the real app without a lifespan, provider I/O, or installed model
    # selection. Avoid sharing state or credentials with a developer's service.
    options = {"providers": {}} if service == "agent" else {"models": {}}
    with (
        TemporaryDirectory(prefix="voxrn-contract-") as directory,
        patch.dict(
            os.environ, {"VOXRN_DEV_TOKEN": "contract-ci-only-token-12345"}, clear=True
        ),
    ):
        app = create_app(directory=directory, **options)
        actual = {}
        websocket = set()
        mounts = set()
        for route in app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    key = (method, route.path_format)
                    if key in actual:
                        fail(f"Duplicate registered route {key}")
                    actual[key] = route.status_code
            elif isinstance(route, APIWebSocketRoute):
                if route.path_format in websocket:
                    fail(f"Duplicate WebSocket {route.path_format}")
                websocket.add(route.path_format)
            elif isinstance(route, Mount):
                mounts.add(route.path)
                if not route.path and not any(
                    child.path == "/mcp" for child in route.routes
                ):
                    fail("The mounted MCP application does not expose /mcp")
        if mounts != {"", "/docs"}:
            fail(f"Application mount drift: {sorted(mounts)}")

    declared = openapi_operations(openapi)
    missing = (set(declared) | FIXED_HTTP_ROUTES) - set(actual)
    undocumented = set(actual) - set(declared) - FIXED_HTTP_ROUTES
    if missing or undocumented:
        fail(
            f"REST route drift, missing={sorted(missing)}, undocumented={sorted(undocumented)}"
        )
    for key, operation in declared.items():
        status = actual[key]
        if status is not None and str(status) not in operation["responses"]:
            fail(f"Registered response status {status} is missing for {key}")

    channels = (asyncapi or {}).get("channels", {})
    expected_websocket = {channel["address"] for channel in channels.values()}
    if websocket != expected_websocket:
        fail(
            f"WebSocket route drift: expected {sorted(expected_websocket)}, registered {sorted(websocket)}"
        )


def check_examples(openapi, mcp, asyncapi, examples, scopes):
    from mcp.types import Tool

    required = {"http", "mcp"}
    if asyncapi is not None:
        required.add("websocket")
    if any(
        "text/event-stream" in dereference(response, openapi).get("content", {})
        for operation in openapi_operations(openapi).values()
        for response in operation["responses"].values()
    ):
        required.add("sse")
    if (CONTRACTS / "workflow-bundle.schema.json").is_file():
        required.add("workflow_bundle")
    missing_examples = sorted(kind for kind in required if not examples.get(kind))
    if missing_examples:
        fail(f"Missing canonical examples: {missing_examples}")

    tools = {}
    if not mcp["tools"]:
        fail("The MCP tool catalog is empty")
    for tool in mcp["tools"]:
        name = Tool.model_validate(tool).name
        if name in tools:
            fail(f"Duplicate MCP tool {name!r}")
        tools[name] = tool
        scope = set(tool["x-required-scope"].split())
        if not scope or not scope <= scopes:
            fail(f"MCP tool {name!r} has an undefined or empty scope")
        for key in ("inputSchema", "outputSchema"):
            schema = tool[key]
            Draft202012Validator.check_schema(schema)
            check_refs(schema, schema)

    for item in [*examples["http"], *examples.get("sse", [])]:
        check_contract_example(item["value"], openapi, item["schema"])
    for item in examples["mcp"]:
        tool = tools[item["tool"]]
        check_instance(item["arguments"], tool["inputSchema"])
        check_instance(item["structuredContent"], tool["outputSchema"])

    if asyncapi:
        messages = asyncapi["components"]["messages"]
        for message in messages.values():
            Draft7Validator.check_schema(message["payload"])
        for event in examples["websocket"]:
            check_contract_example(
                event,
                asyncapi,
                f"#/components/messages/{event['type']}/payload",
                validator=Draft7Validator,
            )
    if "workflow_bundle" in examples:
        schema = strict_json((CONTRACTS / "workflow-bundle.schema.json").read_bytes())
        check_refs(schema, schema)
        Draft202012Validator.check_schema(schema)
        workflow = examples["workflow_bundle"]
        check_instance(workflow, schema)
        Draft202012Validator.check_schema(workflow["state_schema"])
        check_instance(workflow["initial_state"], workflow["state_schema"])


def validate_contracts(cache):
    required_formats = {"date-time", "uri", "uri-reference"}
    if not required_formats <= set(FormatChecker.checkers):
        fail("Install jsonschema[format-nongpl] as described in CI_CONTRACTS.md")

    def read(name):
        return strict_json((CONTRACTS / f"{name}.json").read_bytes())

    openapi = read("openapi")
    check_instance(openapi, official_schema("openapi-3.2", cache))
    check_refs(openapi, openapi)
    for schema in openapi["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)
    check_embedded_schemas(openapi["paths"])
    check_embedded_schemas(
        {key: value for key, value in openapi["components"].items() if key != "schemas"}
    )

    mcp_document = read("mcp-tools")
    examples = read("examples")
    asyncapi = read("asyncapi") if (CONTRACTS / "asyncapi.json").is_file() else None
    if asyncapi is not None:
        check_instance(
            asyncapi, official_schema("asyncapi-3.1", cache), validator=Draft7Validator
        )
        check_refs(asyncapi, asyncapi)
    scopes = set(
        openapi["components"]["securitySchemes"]["oauth"]["flows"]["clientCredentials"][
            "scopes"
        ]
    )
    defined_schemes = openapi["components"]["securitySchemes"].keys()
    for key, operation in openapi_operations(openapi).items():
        for requirements in operation.get("security", openapi.get("security", [])):
            if requirements.keys() - defined_schemes:
                fail(f"OpenAPI operation {key} uses an undefined security scheme")
            if not set(requirements.get("oauth", [])) <= scopes:
                fail(f"OpenAPI operation {key} uses an undefined OAuth scope")
    for name, channel in (asyncapi or {}).get("channels", {}).items():
        required_scopes = set(channel.get("x-required-scope", "").split())
        if not required_scopes or not required_scopes <= scopes:
            fail(f"AsyncAPI channel {name!r} has an undefined or empty scope")
    check_examples(openapi, mcp_document, asyncapi, examples, scopes)
    service = read_pages_profile()["service"]
    check_routes(openapi, asyncapi, mcp_document, service)
    print(f"{service}: official schemas, examples, and registered routes passed")


def read_pages_profile():
    return strict_json((ROOT / "scripts/pages.json").read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema-cache",
        type=Path,
        help="Directory with the pinned official schema JSON files",
    )
    parser.add_argument(
        "--base", help="Git revision against which to check compatibility"
    )
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    validate_contracts(args.schema_cache)
    if args.base:
        check_base(ROOT, args.base)
    subprocess.run(
        [sys.executable, "-B", "scripts/build_pages.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    generator = ROOT / "scripts/generate_clients.py"
    if generator.is_file() or (ROOT / "clients").is_dir():
        subprocess.run(
            [sys.executable, "-B", str(generator), "--check"], cwd=ROOT, check=True
        )


if __name__ == "__main__":
    try:
        main()
    except (SchemaError, ValidationError) as exc:
        raise SystemExit(
            f"Contract validation failed at {exc.json_path}: {exc.message}"
        ) from exc
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Contract check failed: {exc}") from exc
