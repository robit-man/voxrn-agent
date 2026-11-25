"""Generate the checked-in GitHub Pages documentation from the service contracts."""

import argparse
import hashlib
import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "docs"
HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
    "query",
}
SERVICE_NAMES = {"asr": "VoxRN ASR", "tts": "VoxRN TTS", "agent": "VoxRN Agent"}
CONTRACT_LABELS = {
    "openapi.json": (
        "OpenAPI",
        "HTTP operations, schemas, scope requirements, and error responses.",
    ),
    "mcp-tools.json": (
        "MCP tool catalog",
        "Tool names, descriptions, input and output schemas, and required scopes.",
    ),
    "examples.json": (
        "Examples",
        "Illustrative HTTP and MCP payloads, with streaming examples where applicable.",
    ),
    "asyncapi.json": ("AsyncAPI", "Live ASR WebSocket messages and stream operations."),
    "workflow-bundle.schema.json": (
        "Workflow bundle schema",
        "The agent service's workflow admission envelope.",
    ),
}
AUTH = (
    "Obtain the runtime base URL and an access token from its operator. The default Pages URLs serve documentation. "
    "The HTTPS runtime serves the API and MCP endpoint, and credentials belong at that runtime."
)
MCP_RESULT = (
    "Check `result.isError` and `result.structuredContent.ok`. Read `data` on success or `problem` on a tool failure. "
    "HTTP authentication, JSON-RPC and protocol failures can arrive outside the tool result."
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def code(text):
    """Escape inline Markdown code for the matching HTML guide."""
    parts = text.split("`")
    return "".join(
        f"<code>{escape(part)}</code>" if position % 2 else escape(part)
        for position, part in enumerate(parts)
    )


def page_files():
    config = read_json(ROOT / "scripts" / "pages.json")
    kind = config["service"]
    title = SERVICE_NAMES[kind]
    contracts = {
        name: read_json(ROOT / "contracts" / name)
        for name in CONTRACT_LABELS
        if (ROOT / "contracts" / name).is_file()
    }
    openapi = contracts["openapi.json"]
    catalog = contracts["mcp-tools.json"]
    version = catalog["protocolVersion"]
    # Resolve local OpenAPI references at the URL from which the document is fetched.
    openapi.pop("$self", None)
    openapi["info"]["description"] = (
        "VoxRN HTTP service contract. See the [integration guide](../index.md) "
        "for runtime discovery, authentication, and MCP usage."
    )
    tools = catalog["tools"]
    examples = contracts["examples.json"]["mcp"]
    listed_tools = {item["name"] for item in tools}
    if listed_tools != config["tool_http"].keys() or any(
        item["tool"] not in listed_tools for item in examples
    ):
        raise ValueError(
            "The MCP guide and example tool names must match the canonical tool catalog."
        )

    operations = [
        (f"{method.upper()} {path}", details["summary"])
        for path, methods in openapi["paths"].items()
        for method, details in methods.items()
        if method in HTTP_METHODS
    ]
    rest_routes = {path for path, _ in operations}
    for mapping in config["tool_http"].values():
        if mapping not in rest_routes:
            raise ValueError(
                f"MCP guide references an undeclared HTTP operation: {mapping}"
            )
    operations.extend(config.get("additional_operations", []))
    related = {
        name: f"https://robit-man.github.io/voxrn-{service}/"
        for service, name in SERVICE_NAMES.items()
        if service != kind
    }
    resources = {
        label: (f"contracts/{name}", description)
        for name, (label, description) in CONTRACT_LABELS.items()
        if name in contracts
    }
    metadata = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {
            "name": "voxrn-docs-example",
            "version": "1.0.0",
        },
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    bootstrap = f"""curl "$VOXRN_URL/mcp" \\
  -H "Authorization: Bearer $VOXRN_TOKEN" \\
  -H 'Accept: application/json, text/event-stream' \\
  -H 'Content-Type: application/json' \\
  -H 'MCP-Protocol-Version: {version}' \\
  -H 'Mcp-Method: server/discover' \\
  --data-binary @- <<'MCP_REQUEST'
{json_text({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": metadata}}).rstrip()}
MCP_REQUEST"""
    discover_steps = [
        f"Use Streamable HTTP `POST /mcp` with `MCP-Protocol-Version: {version}` and request metadata. "
        "Send `server/discover` to read server capabilities.",
        "Send `tools/list` with `Mcp-Method: tools/list`. Preserve the protocol metadata and change the JSON-RPC method to match.",
        "For a call, send `Mcp-Method: tools/call` and `Mcp-Name: <tool>` with matching "
        "`params.name` and the input under `params.arguments`.",
    ]
    auth_notes = (
        "For OAuth deployments, follow the `WWW-Authenticate` resource metadata link or fetch "
        "`/.well-known/oauth-protected-resource/mcp` from the runtime. Request the tool's scopes and target "
        "the deployed MCP resource as audience. The deployment may also issue a local development bearer token."
    )
    example = examples[0]
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": example["tool"],
            "arguments": example["arguments"],
            "_meta": metadata,
        },
    }
    call_hint = (
        f"To call `{example['tool']}`, keep the other headers from discovery and set "
        f"`Mcp-Method: tools/call` and `Mcp-Name: {example['tool']}`. Send this body after meeting the prerequisites in the integration steps."
    )

    markdown_links = [
        f"- [{name}]({path}): {description}"
        for name, (path, description) in resources.items()
    ]
    related_md = [f"- [{name}]({url})" for name, url in related.items()]
    tool_md = [
        f"| `{tool['name']}` | `{tool['x-required-scope']}` | `{config['tool_http'][tool['name']]}` | "
        f"{tool['description']} |"
        for tool in tools
    ]
    markdown = "\n".join(
        [
            f"# {title}",
            "",
            config["description"],
            "",
            AUTH,
            "",
            "## Integrate",
            "",
            *[f"{index}. {line}" for index, line in enumerate(config["start"], 1)],
            "",
            "## MCP",
            "",
            auth_notes,
            "",
            *[f"{index}. {line}" for index, line in enumerate(discover_steps, 1)],
            "",
            "```sh",
            bootstrap,
            "```",
            "",
            call_hint,
            "",
            "```json",
            json_text(call).rstrip(),
            "```",
            "",
            MCP_RESULT,
            "",
            "| Tool | Required scopes | HTTP operation | Purpose |",
            "| --- | --- | --- | --- |",
            *tool_md,
            "",
            "## HTTP and streaming",
            "",
            *[
                f"- `{operation}` — {description}"
                for operation, description in operations
            ],
            "",
            "## Contracts",
            "",
            *markdown_links,
            "",
            "## Related services",
            "",
            "These are the default project Pages locations after publication.",
            "",
            *related_md,
            "",
        ]
    )

    llms = "\n".join(
        [
            f"# {title}",
            "",
            f"> {config['description']}",
            "",
            AUTH,
            "",
            f"MCP uses Streamable HTTP `POST /mcp` at the runtime, revision `{version}`.",
            "Use `server/discover`, `tools/list`, then `tools/call` with the runtime bearer token.",
            "",
            "## Integration",
            "",
            "- [Service and MCP guide](index.md): Service setup, scope requirements, handshake-free discovery, and request examples.",
            "- [Service discovery metadata](discovery.json): Machine-readable paths, tool mappings, and links.",
            *markdown_links,
            "",
            "## Optional",
            "",
            *related_md,
            "",
        ]
    )
    descriptor = {
        "schemaVersion": 1,
        "name": title,
        "description": config["description"],
        "documentation": {
            "html": "index.html",
            "markdown": "index.md",
            "agentIndex": "llms.txt",
        },
        "runtime": {
            "baseUrl": None,
            "baseUrlSource": "Obtain the HTTPS service URL from its operator.",
            "capabilitiesPath": "/v1/capabilities",
            "authentication": "Bearer",
            "oauthResourceMetadataPath": "/.well-known/oauth-protected-resource/mcp",
        },
        "mcp": {
            "path": "/mcp",
            "transport": "streamable-http",
            "protocolVersion": version,
            "methods": ["server/discover", "tools/list", "tools/call"],
            "catalog": "contracts/mcp-tools.json",
            "tools": [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "requiredScopes": tool["x-required-scope"].split(),
                    "httpOperation": config["tool_http"][tool["name"]],
                }
                for tool in tools
            ],
        },
        "contracts": {name: path for name, (path, _) in resources.items()},
        "relatedDocumentation": related,
    }

    def list_html(items):
        return "\n".join(f"<li>{code(item)}</li>" for item in items)

    tool_rows = "\n".join(
        f"<tr><td><code>{escape(tool['name'])}</code></td><td><code>{escape(tool['x-required-scope'])}</code></td>"
        f"<td><code>{escape(config['tool_http'][tool['name']])}</code></td><td>{escape(tool['description'])}</td></tr>"
        for tool in tools
    )
    resource_rows = "\n".join(
        f'<li><a href="{path}">{escape(name)}</a> <span>{escape(description)}</span></li>'
        for name, (path, description) in resources.items()
    )
    operation_rows = "\n".join(
        f"<dt><code>{escape(operation)}</code></dt><dd>{escape(description)}</dd>"
        for operation, description in operations
    )
    related_rows = "\n".join(
        f'<li><a href="{url}">{escape(name)}</a></li>' for name, url in related.items()
    )
    asset_rev = hashlib.sha256(
        b"".join(
            (PAGES / name).read_bytes()
            for name in ("style.css", "console.js", "runtime.js")
        )
    ).hexdigest()[:12]
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="referrer" content="no-referrer">
  <script>
    (() => {{
      function hydrateLink() {{
        const url = new URL(location.href);
        const fragment = new URLSearchParams(url.hash.slice(1));
        const fields = ["endpoint", "key", "transport"];
        const source = fragment.has("key") || fragment.has("endpoint") ? fragment : url.searchParams;
        if (!source.has("key") && !source.has("endpoint")) return;
        window.voxrnConnection = Object.fromEntries(fields.map(key => [key, source.get(key)]));
        fields.forEach(key => url.searchParams.delete(key));
        if (source === fragment) url.hash = "";
        history.replaceState(null, "", url);
        window.dispatchEvent(new Event("voxrn:connection"));
      }}
      addEventListener("hashchange", hydrateLink);
      hydrateLink();
    }})();
  </script>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(config["description"], quote=True)}">
  <title>{title} Console</title>
  <link rel="stylesheet" href="style.css?v={asset_rev}">
  <script src="console.js?v={asset_rev}" defer></script>
  <script src="runtime.js?v={asset_rev}" defer></script>
  <link rel="describedby" href="llms.txt">
  <link rel="alternate" type="text/markdown" href="index.md">
  <link rel="service-desc" type="application/json" href="contracts/openapi.json">
</head>
<body>
  <a class="skip-link" href="#runtime">Skip to runtime overview</a>
  <header class="topbar">
    <div class="wrap topbar-content">
      <a class="wordmark" href="#runtime">{title}</a>
      <nav aria-label="Console navigation">
        <a href="#runtime">Runtime</a>
        <a href="#model-control">Models</a>
        <a href="#console">Requests</a>
        <a href="#integrate">Reference</a>
      </nav>
    </div>
  </header>
  <main class="wrap">
    <div class="page-heading">
      <h1>{title}</h1>
      <p>{escape(config["description"])}</p>
    </div>
    <section id="runtime" class="panel overview" aria-labelledby="runtime-title">
      <header class="panel-header">
        <div><h2 id="runtime-title">Runtime overview</h2><p id="runtime-message">Connect to a runtime to see live activity.</p></div>
        <label class="compact-field">Chart <select id="runtime-series"><option value="load">Models and requests</option><option value="host">Host CPU and memory</option></select></label>
      </header>
      <div class="overview-grid">
        <figure class="trend">
          <div class="chart-frame"><canvas id="runtime-chart" aria-label="Runtime history"></canvas></div>
          <figcaption id="runtime-legend" class="legend"></figcaption>
        </figure>
        <div class="overview-details">
          <dl class="runtime-kpis">
            <div><dt>Loaded models</dt><dd id="runtime-loaded">—</dd></div>
            <div><dt>Models in use</dt><dd id="runtime-busy">—</dd></div>
            <div><dt>Active requests</dt><dd id="runtime-active">—</dd></div>
            <div><dt>Recent clients</dt><dd id="runtime-clients">—</dd></div>
          </dl>
          <p id="runtime-client-detail" class="micro">Authenticated clients and open WebSockets.</p>
        </div>
      </div>
      <div id="runtime-host" class="host-strip" aria-label="Host specifications"></div>
      <details class="nested activity" id="activity">
        <summary><strong>Activity and utilization</strong><span>Endpoints, models, and lifecycle</span></summary>
        <div class="disclosure-body">
          <div class="filters">
            <label>Window<select id="runtime-window"><option value="60">Last minute</option><option value="300" selected>Last 5 minutes</option><option value="900">Last 15 minutes</option></select></label>
            <label>Endpoint<select id="runtime-endpoint"><option value="">All endpoints</option></select></label>
            <label>Model<select id="runtime-model-filter"><option value="">All models</option></select></label>
            <label>Search<input id="runtime-search" type="search" autocomplete="off" placeholder="Filter visible rows"></label>
          </div>
          <div class="segment" role="group" aria-label="Activity view">
            <button type="button" data-view="endpoints" aria-pressed="true">Endpoints</button>
            <button type="button" data-view="models" aria-pressed="false">Models</button>
            <button type="button" data-view="lifecycle" aria-pressed="false">Lifecycle</button>
          </div>
          <label id="runtime-state-filter" class="compact-field" hidden>State <select id="runtime-state"><option value="">All states</option><option value="starting">Starting</option><option value="running">Running</option><option value="loading">Loading</option><option value="ready">Ready</option><option value="draining">Draining</option><option value="disabled">Disabled</option><option value="failed">Failed</option><option value="stopping">Stopping</option></select></label>
          <div id="runtime-table" class="table-scroll" role="region" aria-label="Filtered runtime activity" tabindex="0"></div>
        </div>
      </details>
    </section>
    <details class="panel connection" id="runtime-connection" open>
      <summary><strong>Connection</strong><span id="api-connection-status" role="status">Disconnected</span></summary>
      <div class="disclosure-body connection-fields">
        <label>Runtime URL <input id="api-url" type="url" inputmode="url" placeholder="https://service.example" required></label>
        <label>Session key or bearer token <input id="api-token" type="password" autocomplete="off"></label>
        <div class="actions">
          <button class="primary" id="api-connect" type="button">Connect</button>
          <button id="api-share" type="button" hidden>Copy link</button>
        </div>
      </div>
    </details>
    <div class="dashboard-columns">
      <section class="panel" id="model-control" aria-labelledby="models-heading">
        <header class="panel-header">
          <div><h2 id="models-heading">Model library</h2><p>Enable a profile to install its weights and start its runtime.</p></div>
          <button id="models-refresh" type="button">Refresh</button>
        </header>
        <div class="panel-body models-body">
          <p id="models-status" class="small-status" role="status">Connect to a runtime to view its models.</p>
          <div id="models-list" class="model-stack"></div>
        </div>
      </section>
      <section class="panel request" id="console" aria-labelledby="request-title">
        <header class="panel-header">
          <div><h2 id="request-title">Request console</h2><p>Test an HTTP endpoint or MCP tool.</p></div>
        </header>
        <div class="panel-body">
      <form id="api-console">
        <div class="form-grid">
          <label>Operation <select id="api-operation"></select></label>
          <label>Ready model <select id="api-model"><option value="">Use request body</option></select></label>
          <label id="api-accept-label">Response format <select id="api-accept"></select></label>
          <label id="api-id-label" hidden>Run or media ID <input id="api-id" autocomplete="off"></label>
          <label id="api-key-label" hidden>Idempotency key <input id="api-key" autocomplete="off"></label>
          <label id="api-cursor-label" hidden>Last event ID <input id="api-cursor" inputmode="numeric"></label>
          <label id="api-file-label" hidden>Audio file <input id="api-file" type="file" accept="audio/*"></label>
        </div>
        <label id="api-body-label" hidden>JSON request or multipart fields <textarea id="api-body" rows="7" spellcheck="false"></textarea></label>
        <div class="actions">
          <button class="primary" id="api-run" type="submit">Send request</button>
          <button id="api-stop" type="button" disabled>Cancel</button>
        </div>
      </form>
      <div class="nested response">
      <div class="response-header"><h3>Response</h3><span id="api-status" role="status">Loading operations…</span></div>
      <audio id="api-audio" controls hidden></audio>
      <a id="api-download" download="voxrn-audio.wav" hidden>Download WAV</a>
      <pre id="api-output" aria-label="Response body" tabindex="0"></pre>
      </div>
        </div>
      </section>
    </div>
    <div class="reference">
    <section id="integrate">
      <h2>Integrate</h2>
      <p>{escape(AUTH)}</p>
      <ol>{list_html(config["start"])}</ol>
    </section>
    <section id="mcp">
      <h2>MCP</h2>
      <p>{code(auth_notes)}</p>
      <ol>{list_html(discover_steps)}</ol>
      <details><summary>Discovery request</summary><pre><code>{escape(bootstrap)}</code></pre></details>
      <details><summary>Tool call example</summary><p>{code(call_hint)}</p><pre><code>{escape(json_text(call))}</code></pre></details>
      <p>{code(MCP_RESULT)}</p>
      <div class="table-scroll" role="region" aria-label="MCP tool catalog" tabindex="0">
        <table>
          <thead><tr><th scope="col">Tool</th><th scope="col">Required scopes</th><th scope="col">HTTP operation</th><th scope="col">Purpose</th></tr></thead>
          <tbody>{tool_rows}</tbody>
        </table>
      </div>
    </section>
    <section id="api">
      <h2>HTTP and streaming</h2>
      <dl>{operation_rows}</dl>
    </section>
    <section id="contracts">
      <h2>Contracts and agent files</h2>
      <ul class="resources">
        {resource_rows}
        <li><a href="llms.txt">llms.txt</a> <span>Agent entry point.</span></li>
        <li><a href="index.md">Markdown guide</a> <span>Text version of this page.</span></li>
        <li><a href="discovery.json">Service discovery metadata</a> <span>Machine-readable link map.</span></li>
      </ul>
    </section>
    <section>
      <h2>Related services</h2>
      <p>These projects use the following default Pages locations after publication.</p>
      <ul>{related_rows}</ul>
    </section>
    </div>
  </main>
</body>
</html>
"""
    return {
        ".nojekyll": "",
        "index.html": html,
        "index.md": markdown,
        "llms.txt": llms,
        "discovery.json": json_text(descriptor),
        **{f"contracts/{name}": json_text(value) for name, value in contracts.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check that the published files match their sources.",
    )
    args = parser.parse_args()
    stale = []
    for relative, content in page_files().items():
        path = PAGES / relative
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if stale:
        parser.exit(
            1, "Run python scripts/build_pages.py to update " + ", ".join(stale) + "\n"
        )


if __name__ == "__main__":
    main()
