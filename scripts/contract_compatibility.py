"""Check public wire surfaces for compatibility with a committed baseline."""

import hashlib
import json
from pathlib import Path
import re
import subprocess

HTTP_METHODS = frozenset("get put post delete options head patch trace query".split())
SPEC_FILES = (
    "openapi.json",
    "asyncapi.json",
    "mcp-tools.json",
    "workflow-bundle.schema.json",
)
SCHEMA_KEYWORDS = (
    "type",
    "format",
    "const",
    "pattern",
    "multipleOf",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "uniqueItems",
    "patternProperties",
    "dependentRequired",
    "propertyNames",
    "unevaluatedProperties",
    "contains",
    "if",
    "then",
    "else",
    "not",
)


def dereference(node, document):
    """Resolve the local JSON pointers used by the canonical documents."""
    visited = set()
    while isinstance(node, dict) and "$ref" in node:
        reference = node["$ref"]
        if not reference.startswith("#/") or reference in visited:
            raise ValueError(
                f"Expected an acyclic local JSON pointer, got {reference!r}"
            )
        visited.add(reference)
        node = document
        for part in reference[2:].split("/"):
            token = part.replace("~1", "/").replace("~0", "~")
            node = node[int(token) if isinstance(node, list) else token]
    return node


def render(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def security_signature(requirements):
    return sorted(
        render({name: sorted(scopes) for name, scopes in choice.items()})
        for choice in requirements
    )


class Compatibility:
    def __init__(self, old, new):
        self.old = old
        self.new = new
        self.changes = set()

    def changed(self, where, field, left, right):
        if left != right:
            self.changes.add(
                f"{where}: {field} changed from {render(left)} to {render(right)}"
            )

    def shared(self, where, kind, old, new):
        for name in old.keys() - new.keys():
            self.changes.add(f"{where}: removed {kind} {name}")
        return sorted(old.keys() & new.keys())

    def schema(self, where, old, new, direction, roots, active=frozenset()):
        old, new = (dereference(node, root) for node, root in zip((old, new), roots))
        pair = (id(old), id(new))
        if pair in active:
            return
        if not isinstance(old, dict) or not isinstance(new, dict):
            self.changed(where, "schema", old, new)
            return
        active = active | {pair}
        required_old, required_new = (
            set(old.get("required", [])),
            set(new.get("required", [])),
        )
        if direction in {"request", "both"}:
            for name in required_new - required_old:
                self.changes.add(f"{where}: newly required input field {name}")
        if direction in {"response", "both"}:
            for name in required_old - required_new:
                self.changes.add(f"{where}: response field {name} no longer required")

        old_props, new_props = old.get("properties", {}), new.get("properties", {})
        for key in self.shared(where, "field", old_props, new_props):
            self.schema(
                f"{where}.{key}",
                old_props[key],
                new_props[key],
                direction,
                roots,
                active,
            )
        for keyword in SCHEMA_KEYWORDS:
            self.changed(where, keyword, old.get(keyword), new.get(keyword))
        if "enum" in old and "enum" in new:
            before = {render(value) for value in old["enum"]}
            after = {render(value) for value in new["enum"]}
            removed = before - after if direction in {"request", "both"} else set()
            added = after - before if direction in {"response", "both"} else set()
            if removed or added:
                self.changed(where, "enum", sorted(before), sorted(after))
        else:
            self.changed(where, "enum", old.get("enum"), new.get("enum"))

        if "items" in old and "items" in new:
            self.schema(
                f"{where}[]", old["items"], new["items"], direction, roots, active
            )
        else:
            self.changed(where, "items", old.get("items"), new.get("items"))

        previous_values, candidate_values = (
            old.get("additionalProperties"),
            new.get("additionalProperties"),
        )
        if isinstance(previous_values, dict) and isinstance(candidate_values, dict):
            self.schema(
                f"{where}.additionalProperties",
                previous_values,
                candidate_values,
                direction,
                roots,
                active,
            )
        else:
            self.changed(
                where, "additionalProperties", previous_values, candidate_values
            )
        for keyword in ("allOf", "oneOf", "anyOf", "prefixItems"):
            if not (keyword in old or keyword in new):
                continue
            left, right = old.get(keyword, []), new.get(keyword, [])
            self.changed(where, f"{keyword} branch count", len(left), len(right))
            for index, (previous, candidate) in enumerate(zip(left, right)):
                self.schema(
                    f"{where}.{keyword}[{index}]",
                    previous,
                    candidate,
                    direction,
                    roots,
                    active,
                )

    def content(self, where, old, new, direction, roots):
        previous, candidate = old.get("content", {}), new.get("content", {})
        for key in self.shared(where, "media type", previous, candidate):
            for field in ("schema", "itemSchema"):
                left, right = previous[key].get(field), candidate[key].get(field)
                label = f"{where} {key} {field}"
                if left is None or right is None:
                    self.changed(
                        label, "schema presence", left is not None, right is not None
                    )
                else:
                    self.schema(label, left, right, direction, roots)
            for field in ("encoding", "itemEncoding", "prefixEncoding"):
                self.changed(
                    f"{where} {key}",
                    field,
                    previous[key].get(field),
                    candidate[key].get(field),
                )

    def parameters(self, where, old, new, roots):
        def parameter_map(items, root):
            parameters = {}
            for item in items:
                parameter = dereference(item, root)
                parameters[f"{parameter['in']}:{parameter['name']}"] = parameter
            return parameters

        previous, candidate = (
            parameter_map(node, root) for node, root in zip((old, new), roots)
        )
        for name in candidate.keys() - previous.keys():
            if candidate[name].get("required", False):
                self.changes.add(f"{where}: new required parameter {name}")
        for name in self.shared(where, "parameter", previous, candidate):
            left, right = previous[name], candidate[name]
            for field in ("required", "style", "explode", "allowReserved"):
                self.changed(
                    f"{where} {name}", field, left.get(field), right.get(field)
                )
            self.schema(
                f"{where} {name}",
                left.get("schema", {}),
                right.get("schema", {}),
                "request",
                roots,
            )
            self.content(f"{where} {name}", left, right, "request", roots)

    def openapi(self, old, new):
        roots = old, new
        self.changed("OpenAPI", "protocol version", old["openapi"], new["openapi"])

        def all_scopes(document):
            return document["components"]["securitySchemes"]["oauth"]["flows"][
                "clientCredentials"
            ]["scopes"]

        self.shared("OpenAPI", "OAuth scope", all_scopes(old), all_scopes(new))
        self.shared(
            "OpenAPI",
            "named schema",
            old["components"]["schemas"],
            new["components"]["schemas"],
        )
        for path in self.shared("OpenAPI", "path", old["paths"], new["paths"]):
            previous, candidate = (
                dereference(root["paths"][path], root) for root in roots
            )
            methods = (
                {key: node[key] for key in node.keys() & HTTP_METHODS}
                for node in (previous, candidate)
            )
            before, after = methods
            for method in self.shared(path, "operation", before, after):
                where = f"{method.upper()} {path}"
                left, right = before[method], after[method]
                self.changed(
                    where,
                    "operationId",
                    left.get("operationId"),
                    right.get("operationId"),
                )
                self.changed(
                    where,
                    "authorization scopes",
                    security_signature(left.get("security", old.get("security", []))),
                    security_signature(right.get("security", new.get("security", []))),
                )
                self.parameters(
                    where,
                    [*previous.get("parameters", []), *left.get("parameters", [])],
                    [*candidate.get("parameters", []), *right.get("parameters", [])],
                    roots,
                )
                request_old = dereference(left.get("requestBody", {}), old)
                request_new = dereference(right.get("requestBody", {}), new)
                self.changed(
                    where,
                    "request body required",
                    request_old.get("required", False),
                    request_new.get("required", False),
                )
                self.content(
                    f"{where} request", request_old, request_new, "request", roots
                )
                previous_responses, candidate_responses = (
                    left["responses"],
                    right["responses"],
                )
                for status in self.shared(
                    where, "status", previous_responses, candidate_responses
                ):
                    response_old = dereference(previous_responses[status], old)
                    response_new = dereference(candidate_responses[status], new)
                    label = f"{where} status {status}"
                    self.content(label, response_old, response_new, "response", roots)
                    old_headers, new_headers = (
                        node.get("headers", {}) for node in (response_old, response_new)
                    )
                    for header in self.shared(
                        label, "response header", old_headers, new_headers
                    ):
                        header_old, header_new = (
                            dereference(item[header], root)
                            for item, root in zip((old_headers, new_headers), roots)
                        )
                        self.schema(
                            f"{label} header {header}",
                            header_old.get("schema", {}),
                            header_new.get("schema", {}),
                            "response",
                            roots,
                        )

    def mcp(self, old, new):
        self.changed(
            "MCP", "protocol version", old["protocolVersion"], new["protocolVersion"]
        )
        old_tools, new_tools = (
            {item["name"]: item for item in root["tools"]} for root in (old, new)
        )
        for name in self.shared("MCP", "tool", old_tools, new_tools):
            left, right = old_tools[name], new_tools[name]
            self.changed(
                f"MCP {name}",
                "authorization scopes",
                sorted(left["x-required-scope"].split()),
                sorted(right["x-required-scope"].split()),
            )
            for key, direction in (
                ("inputSchema", "request"),
                ("outputSchema", "response"),
            ):
                self.schema(
                    f"MCP {name} {key}",
                    left[key],
                    right[key],
                    direction,
                    (left[key], right[key]),
                )

    def asyncapi(self, old, new):
        self.changed("AsyncAPI", "protocol version", old["asyncapi"], new["asyncapi"])
        for name in self.shared("AsyncAPI", "server", old["servers"], new["servers"]):
            left, right = old["servers"][name], new["servers"][name]
            self.changed(
                f"AsyncAPI server {name}",
                "protocol",
                left.get("protocol"),
                right.get("protocol"),
            )
        old_channels, new_channels = old["channels"], new["channels"]
        for name in self.shared("AsyncAPI", "channel", old_channels, new_channels):
            left, right = old_channels[name], new_channels[name]
            for field in (
                "address",
                "bindings",
                "x-websocket-subprotocol",
                "x-required-scope",
            ):
                self.changed(
                    f"AsyncAPI channel {name}", field, left.get(field), right.get(field)
                )
            self.shared(
                f"AsyncAPI channel {name}",
                "message",
                left["messages"],
                right["messages"],
            )
        old_ops, new_ops = old["operations"], new["operations"]
        for name in self.shared("AsyncAPI", "operation", old_ops, new_ops):
            left, right = old_ops[name], new_ops[name]
            for field in ("action", "channel", "security"):
                self.changed(
                    f"AsyncAPI operation {name}",
                    field,
                    left.get(field),
                    right.get(field),
                )
            left_refs, right_refs = (
                {node["$ref"]: node for node in operation["messages"]}
                for operation in (left, right)
            )
            for ref in self.shared(
                f"AsyncAPI operation {name}", "message", left_refs, right_refs
            ):
                old_message, new_message = (
                    dereference(node, root)
                    for node, root in zip((left_refs[ref], right_refs[ref]), (old, new))
                )
                direction = "request" if left["action"] == "receive" else "response"
                where = f"AsyncAPI {name} {ref}"
                self.changed(
                    where,
                    "contentType",
                    old_message.get("contentType"),
                    new_message.get("contentType"),
                )
                self.schema(
                    where,
                    old_message["payload"],
                    new_message["payload"],
                    direction,
                    (old, new),
                )

    def run(self):
        available = self.shared("Canonical", "document", self.old, self.new)
        comparisons = {
            "openapi.json": self.openapi,
            "asyncapi.json": self.asyncapi,
            "mcp-tools.json": self.mcp,
            "workflow-bundle.schema.json": lambda old, new: self.schema(
                "Workflow bundle", old, new, "both", (old, new)
            ),
        }
        for name in available:
            comparisons[name](self.old[name], self.new[name])
        return sorted(self.changes)


def compatibility_digest(changes):
    return hashlib.sha256(render(sorted(set(changes))).encode()).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True)


def check_base(root: Path, revision: str):
    base = git(
        root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Could not resolve a full SHA for the compatibility base")
    if (
        not subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=root
        ).returncode
        == 0
    ):
        raise ValueError("The compatibility base must be an ancestor of the candidate")
    paths = set(
        git(root, "ls-tree", "-r", "--name-only", base, "contracts").splitlines()
    )
    previous, candidate = {}, {}
    for name in SPEC_FILES:
        filename = f"contracts/{name}"
        if filename in paths:
            previous[name] = json.loads(git(root, "show", f"{base}:{filename}"))
        if (path := root / filename).is_file():
            candidate[name] = json.loads(path.read_bytes())
    if not previous:
        print(f"Compatibility base {base} predates the canonical contracts")
        return
    changes = Compatibility(previous, candidate).run()
    if not changes:
        print(f"No wire compatibility changes against {base}")
        return
    fingerprint = compatibility_digest(changes)
    approval_path = root / ".github/contract-compatibility.json"
    approvals = (
        json.loads(approval_path.read_bytes())["approvals"]
        if approval_path.is_file()
        else []
    )
    report = "\n".join(f"  - {change}" for change in changes)
    print(
        f"Wire compatibility findings against {base}\n{report}\nFingerprint {fingerprint}",
        flush=True,
    )
    for approval in approvals:
        if (
            approval["base"] == base
            and approval["fingerprint"] == fingerprint
            and approval["migration"].strip()
        ):
            print(f"Documented migration: {approval['migration']}")
            return
    raise SystemExit("Unapproved wire compatibility findings")
