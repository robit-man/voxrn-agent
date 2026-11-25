# VoxRN Agent

Run stateless text inference and execute versioned workflows with durable state and action receipts.

Obtain the runtime base URL and an access token from its operator. The default Pages URLs serve documentation. The HTTPS runtime serves the API and MCP endpoint, and credentials belong at that runtime.

## Integrate

1. Read `GET /v1/capabilities` or call `agent_capabilities` to select an installed model. Use `POST /v1/chat/completions` for stateless inference, or `agent_infer` for a buffered MCP response.
2. For workflow execution, list authorized revisions, create a run, and submit a finalized participant turn with the expected run revision. Use `Idempotency-Key` for HTTP commands or `idempotency_key` for their MCP equivalents.
3. Read the committed run state by ID and use `GET /v1/runs/{run_id}/events` for SSE replay. Resume after a durable event using `Last-Event-ID`. Treat turn admission, provisional output, and action receipts as distinct events.

## MCP

For OAuth deployments, follow the `WWW-Authenticate` resource metadata link or fetch `/.well-known/oauth-protected-resource/mcp` from the runtime. Request the tool's scopes and target the deployed MCP resource as audience. The deployment may also issue a local development bearer token.

1. Use Streamable HTTP `POST /mcp` with `MCP-Protocol-Version: 2026-07-28` and request metadata. Send `server/discover` to read server capabilities.
2. Send `tools/list` with `Mcp-Method: tools/list`. Preserve the protocol metadata and change the JSON-RPC method to match.
3. For a call, send `Mcp-Method: tools/call` and `Mcp-Name: <tool>` with matching `params.name` and the input under `params.arguments`.

```sh
curl "$VOXRN_URL/mcp" \
  -H "Authorization: Bearer $VOXRN_TOKEN" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  --data-binary @- <<'MCP_REQUEST'
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "voxrn-docs-example",
        "version": "1.0.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
MCP_REQUEST
```

To call `agent_infer`, keep the other headers from discovery and set `Mcp-Method: tools/call` and `Mcp-Name: agent_infer`. Send this body after meeting the prerequisites in the integration steps.

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "agent_infer",
    "arguments": {
      "model": "qwen-local",
      "messages": [
        {
          "role": "user",
          "content": "Summarize this in one sentence."
        }
      ]
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "voxrn-docs-example",
        "version": "1.0.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

Check `result.isError` and `result.structuredContent.ok`. Read `data` on success or `problem` on a tool failure. HTTP authentication, JSON-RPC and protocol failures can arrive outside the tool result.

| Tool | Required scopes | HTTP operation | Purpose |
| --- | --- | --- | --- |
| `agent_cancel_run` | `agent:run` | `POST /v1/runs/{run_id}/cancel` | Request workflow cancellation and later read the final state. |
| `agent_capabilities` | `agent:read` | `GET /v1/capabilities` | List available inference models and limits. |
| `agent_create_run` | `agent:run` | `POST /v1/runs` | Initialize an authorized workflow revision. |
| `agent_get_run` | `agent:read` | `GET /v1/runs/{run_id}` | Read committed workflow run state. |
| `agent_infer` | `agent:infer` | `POST /v1/chat/completions` | Run buffered stateless text inference. |
| `agent_list_workflows` | `agent:read` | `GET /v1/workflows` | List authorized workflow revisions. |
| `agent_submit_turn` | `agent:run` | `POST /v1/runs/{run_id}/turns` | Admit a finalized participant turn after authorization. |

## HTTP and streaming

- `GET /livez` — Process liveness
- `GET /readyz` — Configured service readiness
- `GET /v1/capabilities` — Discover model revisions, features and limits
- `GET /v1/workflows` — Discover admitted workflow revisions
- `POST /v1/runs` — Initialize a durable workflow run
- `GET /v1/runs/{run_id}` — Read committed run state
- `POST /v1/runs/{run_id}/turns` — Append one finalized participant turn
- `POST /v1/runs/{run_id}/cancel` — Accept cancellation of a nonterminal run
- `GET /v1/runs/{run_id}/events` — Replay and follow durable run events
- `POST /v1/chat/completions` — Stateless text inference

## Contracts

- [OpenAPI](contracts/openapi.json): HTTP operations, schemas, scope requirements, and error responses.
- [MCP tool catalog](contracts/mcp-tools.json): Tool names, descriptions, input and output schemas, and required scopes.
- [Examples](contracts/examples.json): Illustrative HTTP and MCP payloads, with streaming examples where applicable.
- [Workflow bundle schema](contracts/workflow-bundle.schema.json): The agent service's workflow admission envelope.

## Related services

These are the default project Pages locations after publication.

- [VoxRN ASR](https://robit-man.github.io/voxrn-asr/)
- [VoxRN TTS](https://robit-man.github.io/voxrn-tts/)
