#!/usr/bin/env python3
"""Generate the standalone HTTP SDKs from contracts/openapi.json."""

import argparse
import hashlib
import json
import keyword
import pprint
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
JSON = "application/json"
MULTIPART = "multipart/form-data"
EVENT_STREAM = "text/event-stream"


def words(name):
    return re.findall(r"[A-Z]+(?=[A-Z][a-z]|[^a-zA-Z]|$)|[A-Z]?[a-z0-9]+", name)


def snake(name):
    result = "_".join(word.lower() for word in words(name))
    return result + "_" if keyword.iskeyword(result) else result


def pascal(name):
    return "".join(word.capitalize() for word in words(name))


def camel(name):
    result = pascal(name)
    return result[:1].lower() + result[1:]


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def dereference(contract, item):
    if "$ref" not in item:
        return item
    target = item["$ref"]
    if not target.startswith("#/"):
        raise ValueError(f"External OpenAPI reference is unsupported: {target}")
    for piece in target[2:].split("/"):
        contract = contract[piece.replace("~1", "/").replace("~0", "~")]
    return contract


@dataclass
class Parameter:
    name: str
    location: str
    schema: dict
    required: bool


@dataclass
class Operation:
    name: str
    path: str
    method: str
    authenticated: bool
    parameters: list[Parameter]
    media: tuple[str, ...]
    accept: str
    stream_selector: str | None
    arguments: list[tuple[str, dict, bool]]
    result: dict

    def metadata(self, naming):
        return (
            self.method,
            self.path,
            self.authenticated,
            tuple((naming(p.name), p.name, p.location) for p in self.parameters),
            self.media,
            self.accept,
            self.stream_selector,
        )


def named_schema(schemas, name, schema):
    if schema.get("type") == "object" and schema.get("properties"):
        if name in schemas and schemas[name] != schema:
            raise ValueError(f"SDK schema name collision: {name}")
        schemas[name] = schema
        return ref(name)
    return schema


def describe_operation(contract, schemas, path, route, method, operation):
    name = operation["operationId"]
    authenticated = bool(operation.get("security", contract.get("security", [])))
    merged_parameters = {}
    for item in [*route.get("parameters", []), *operation.get("parameters", [])]:
        parameter = dereference(contract, item)
        location = parameter["in"]
        if location not in {"header", "path", "query"}:
            raise ValueError(f"{name} uses unsupported parameter location {location}.")
        parameter_name = parameter["name"]
        if parameter["schema"].get("type") not in {
            "string",
            "boolean",
            "number",
            "integer",
        }:
            raise ValueError(f"{name}.{parameter_name} needs a parameter encoder.")
        merged_parameters[(location, parameter_name)] = Parameter(
            parameter_name,
            location,
            parameter["schema"],
            parameter.get("required", False),
        )
    parameters = list(merged_parameters.values())
    arguments = [(p.name, p.schema, p.required) for p in parameters]
    if {p.name for p in parameters if p.location == "path"} != set(
        re.findall(r"\{([^{}]+)\}", path)
    ):
        raise ValueError(f"{name} does not declare the parameters in {path}.")

    request = dereference(contract, operation.get("requestBody", {}))
    contents = request.get("content", {})
    media = tuple(contents)
    request_schema = {}
    if media in {(JSON,), (MULTIPART,)}:
        request_schema = contents[media[0]]["schema"]
        if media == (MULTIPART,):
            # OpenAPI describes the audio part through the encoding declaration.
            file_fields = contents[MULTIPART].get("encoding", {})
            request_schema = dereference(contract, request_schema)
            properties = {
                key: {"x-sdk-upload-part": True} if key in file_fields else value
                for key, value in request_schema["properties"].items()
            }
            request_schema = {**request_schema, "properties": properties}
        request_schema = named_schema(schemas, pascal(name) + "Body", request_schema)
        arguments.append(("body", request_schema, request.get("required", False)))
    elif media:
        if not all(
            kind.startswith(("audio/", "application/octet-stream")) for kind in media
        ):
            raise ValueError(f"{name} needs an encoder for {media}.")
        arguments.extend(
            [
                ("body", {"x-sdk-bytes": True}, request.get("required", False)),
                ("content_type", {"enum": list(media)}, request.get("required", False)),
            ]
        )

    success = {}
    for status, item in operation["responses"].items():
        if status.startswith("2"):
            success.update(dereference(contract, item).get("content", {}))
    if not success:
        accept, response = JSON, {"x-sdk-empty": True}
    else:
        accept = next((kind for kind in success if kind != EVENT_STREAM), EVENT_STREAM)
        response = (
            named_schema(schemas, pascal(name) + "Result", success[JSON]["schema"])
            if accept == JSON
            else {"x-sdk-binary-result": True}
        )
    stream_selector = None
    if EVENT_STREAM in success:
        body = dereference({"components": {"schemas": schemas}}, request_schema)
        stream_selector = "body" if "stream" in body.get("properties", {}) else "accept"
    return Operation(
        name,
        path,
        method.upper(),
        authenticated,
        parameters,
        media,
        accept,
        stream_selector,
        arguments,
        response,
    )


def nested_python_schemas(schemas):
    """Name inline objects so Python requests and responses retain nested types."""
    result = {}

    def visit(schema, name):
        if schema.get("type") == "object" and schema.get("properties"):
            properties = {
                key: visit(value, name + pascal(key))
                for key, value in schema["properties"].items()
            }
            if name in result or (name in schemas and schema is not schemas[name]):
                raise ValueError(f"SDK schema name collision: {name}")
            result[name] = {**schema, "properties": properties}
            return ref(name)
        if schema.get("type") == "array" and "items" in schema:
            return {**schema, "items": visit(schema["items"], name + "Item")}
        return schema

    for name, schema in schemas.items():
        visited = visit(schema, name)
        if name not in result:
            result[name] = visited
    return result


def type_of(schema, language):
    py = language == "python"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("x-sdk-upload-part"):
        return "UploadPart"
    if schema.get("x-sdk-bytes"):
        return "bytes" if py else "Blob | ArrayBuffer | Uint8Array<ArrayBuffer>"
    if schema.get("x-sdk-binary-result"):
        return "bytes" if py else "ArrayBuffer"
    if schema.get("x-sdk-empty"):
        return "None" if py else "void"
    if "const" in schema or "enum" in schema:
        values = [schema["const"]] if "const" in schema else schema["enum"]
        if py:
            return "Literal[" + ", ".join(map(repr, values)) + "]"
        return " | ".join(map(json.dumps, values))
    for alternatives in ("oneOf", "anyOf"):
        if alternatives in schema:
            return " | ".join(type_of(item, language) for item in schema[alternatives])
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(type_of({**schema, "type": item}, language) for item in kind)
    if kind == "object":
        if schema.get("properties") and not py:
            required = set(schema.get("required", ()))
            properties = [
                f"{json.dumps(key)}{'' if key in required else '?'}: {type_of(value, language)}"
                for key, value in schema["properties"].items()
            ]
            return "{ " + "; ".join(properties) + " }"
        extra = schema.get("additionalProperties")
        value = (
            type_of(extra, language)
            if isinstance(extra, dict)
            else ("Any" if py else "unknown")
        )
        return f"dict[str, {value}]" if py else f"Record<string, {value}>"
    if kind == "array":
        item = type_of(schema.get("items", {}), language)
        return f"list[{item}]" if py else f"Array<{item}>"
    primitives = (
        {
            "boolean": "bool",
            "integer": "int",
            "number": "float",
            "string": "str",
            "null": "None",
        }
        if py
        else {
            "boolean": "boolean",
            "integer": "number",
            "number": "number",
            "string": "string",
            "null": "null",
        }
    )
    return primitives.get(kind, "Any" if py else "unknown")


def python_schemas(schemas):
    classes, aliases = [], []
    for name, schema in nested_python_schemas(schemas).items():
        if schema.get("type") != "object" or not schema.get("properties"):
            aliases.append(f"{name} = {type_of(schema, 'python')}")
            continue
        required = set(schema.get("required", ()))
        fields = ["class " + name + "(TypedDict):"]
        for key, value in schema["properties"].items():
            if not key.isidentifier() or keyword.iskeyword(key):
                raise ValueError(
                    f"{name}.{key} needs a Python TypedDict field encoder."
                )
            annotation = type_of(value, "python")
            fields.append(
                f"    {key}: {annotation if key in required else f'NotRequired[{annotation}]'}"
            )
        classes.append("\n".join(fields))
    return "\n\n\n".join(classes + aliases)


def python_methods(operations):
    methods = []
    for operation in operations:
        name = snake(operation.name)
        arguments = sorted(operation.arguments, key=lambda item: not item[2])
        parameters = [
            f"{snake(key)}: {type_of(schema, 'python')}"
            + ("" if required else " | None = None")
            for key, schema, required in arguments
        ]
        signature = ", *, " + ", ".join(parameters) if parameters else ""
        values = (
            "{"
            + ", ".join(f'"{snake(key)}": {snake(key)}' for key, _, _ in arguments)
            + "}"
        )
        if operation.accept != EVENT_STREAM:
            response = type_of(operation.result, "python")
            invocation = f'self._request("{operation.name}", {values})'
            methods.append(
                f"    def {name}_raw(self{signature}) -> httpx.Response:\n        return {invocation}"
            )
            call = (
                f"self.{name}_raw("
                + ", ".join(f"{snake(key)}={snake(key)}" for key, _, _ in arguments)
                + ")"
            )
            returned = (
                call
                if response == "None"
                else "return "
                + call
                + (".content" if response == "bytes" else ".json()")
            )
            methods.extend(
                [f"    def {name}(self{signature}) -> {response}:\n        {returned}"]
            )
        if operation.stream_selector:
            stream = name if operation.accept == EVENT_STREAM else name + "_stream"
            call = f'self._stream("{operation.name}", {values})'
            methods.append(
                f"    def {stream}(self{signature}) -> AbstractContextManager[httpx.Response]:\n        return {call}"
            )
    return "\n\n".join(methods)


def typescript_methods(operations):
    methods = []
    for operation in operations:
        name = operation.name
        # The browser supplies Origin. Native fetch callers can pass it.
        browser_headers = {
            parameter.name
            for parameter in operation.parameters
            if parameter.location == "header" and parameter.name.lower() == "origin"
        }
        arguments = [
            (key, schema, required and key not in browser_headers)
            for key, schema, required in operation.arguments
        ]
        fields = [
            f"{camel(key)}{'' if required else '?'}: {type_of(schema, 'typescript')}"
            for key, schema, required in arguments
        ] + ["signal?: AbortSignal"]
        optional = "" if any(required for _, _, required in arguments) else " = {}"
        signature = "options: { " + "; ".join(fields) + " }" + optional
        if operation.accept != EVENT_STREAM:
            methods.append(
                f'  {name}Raw({signature}): Promise<Response> {{\n    return this.request("{name}", options)\n  }}'
            )
            response = type_of(operation.result, "typescript")
            call = f"await this.{name}Raw(options)"
            returned = (
                call
                if response == "void"
                else "return ("
                + call
                + ")"
                + (".arrayBuffer()" if response == "ArrayBuffer" else ".json()")
            )
            methods.append(
                f"  async {name}({signature}): Promise<{response}> {{\n    {returned}\n  }}"
            )
        if operation.stream_selector:
            stream = name if operation.accept == EVENT_STREAM else name + "Stream"
            methods.append(
                f'  {stream}({signature}): Promise<Response> {{\n    return this.request("{name}", options, true)\n  }}'
            )
    return "\n\n".join(methods)


def browser_live_method(contract, operations):
    channel = contract["channels"]["asrLive"]
    address = channel["address"]
    protocol = channel["x-websocket-subprotocol"]
    ticket = next(
        operation.name
        for operation in operations
        if operation.path == f"{address}/tickets"
    )
    return f"""  /** Get an origin-bound ticket and create the browser WebSocket. */
  async openLiveTranscription(options: {{ signal?: AbortSignal }} = {{}}): Promise<WebSocket> {{
    const ticket = await this.{ticket}(options)
    options.signal?.throwIfAborted()
    const url = new URL(this.baseUrl + {json.dumps(address)})
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
    return new WebSocket(url, [{json.dumps(protocol)}, ticket.subprotocol])
  }}"""


def generate(root):
    canonical = (root / "contracts" / "openapi.json").read_bytes()
    contract = json.loads(canonical)
    service = contract["info"]["title"].removeprefix("VoxRN ").lower()
    client = "Voxrn" + pascal(service) + "Client"
    source_hash = hashlib.sha256(canonical).hexdigest()
    schemas = dict(contract["components"]["schemas"])
    operations = [
        describe_operation(contract, schemas, path, route, method, operation)
        for path, route in contract["paths"].items()
        for method, operation in route.items()
        if method in METHODS
    ]
    if len({operation.name for operation in operations}) != len(operations):
        raise ValueError("OpenAPI operation IDs must be unique.")
    marker = (
        f"Generated from contracts/openapi.json (SHA-256 {source_hash}). Do not edit."
    )
    python = {
        "CONTEXTLIB": "AbstractContextManager, contextmanager"
        if any(operation.stream_selector for operation in operations)
        else "contextmanager",
        "SCHEMAS": python_schemas(schemas),
        "OPERATIONS": pprint.pformat(
            {item.name: item.metadata(snake) for item in operations}, sort_dicts=False
        ),
        "METHODS": python_methods(operations),
    }
    typescript = {
        "SCHEMAS": "\n\n".join(
            f"export type {name} = {type_of(schema, 'typescript')}"
            for name, schema in schemas.items()
        ),
        "OPERATIONS": json.dumps(
            {item.name: item.metadata(camel) for item in operations}, indent=2
        ),
        "METHODS": typescript_methods(operations),
    }
    if service == "asr":
        asyncapi = (root / "contracts" / "asyncapi.json").read_bytes()
        typescript["METHODS"] += "\n\n" + browser_live_method(
            json.loads(asyncapi), operations
        )
        digest = hashlib.sha256(asyncapi).hexdigest()
        typescript["METHODS"] += (
            f"\n\n  // Browser WebSocket source is contracts/asyncapi.json (SHA-256 {digest})."
        )
    for suffix, substitutions, output, comment in (
        ("python.py.txt", python, f"python/voxrn_{service}_sdk/__init__.py", "#"),
        ("typescript.ts.txt", typescript, "typescript/client.ts", "//"),
    ):
        template = (root / "scripts" / "client_templates" / suffix).read_text(
            encoding="utf-8"
        )
        for name, value in {**substitutions, "CLIENT": client}.items():
            template = template.replace(f"@{name}@", value)
        yield (
            root / "clients" / output,
            f"{comment} {marker}\n" + template.rstrip() + "\n",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exit nonzero when outputs are stale"
    )
    args = parser.parse_args()
    stale = []
    for path, expected in generate(ROOT):
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    if stale:
        parser.exit(
            1,
            "Run python3 scripts/generate_clients.py to refresh "
            + ", ".join(stale)
            + "\n",
        )


if __name__ == "__main__":
    main()
