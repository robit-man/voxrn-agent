# Service conventions

## Endpoint and identity

Each repository defines one independently packaged service with its own image, API base URL, process configuration, model or provider catalog, and release lifecycle. A developer can bind to loopback and use local credentials. Production uses HTTPS, an explicitly configured token issuer and audience, and a least-privilege workload identity. Store credentials and model caches outside the image. Require no Voryn checkout, database, carrier, or UI to run the standalone baseline. The runtime must fail readiness if its configured stores or required adapters cannot serve requests. GPUs, where configured, are operator supplied. On a host with a GPU lease broker, reserve the exact device scope before starting a CUDA workload.

`GET /livez` indicates the process is alive. `GET /readyz` returns 503 until mandatory dependencies and the configured default serving path are ready. Keep health probes private to the deployment network and return no dependency topology. Temporary capacity exhaustion on a ready replica returns 429. `GET /v1/capabilities` is authenticated and returns effective limits, available models and feature flags, contract version and immutable service revision. Pin model and engine revisions in release evidence. Disabled options fail admission explicitly.

Wire paths under `/v1` retain their semantics in minor releases. New request fields and content types are optional. Clients tolerate unknown response fields and event types introduced in compatible revisions. The schemas describe the exact 1.0.0 output vocabulary for conformance. A change to a required field, default behavior, enum meaning or terminal event protocol requires a new major API version. MCP protocol revisions are versioned independently of service contracts.

## Errors, streams, and retention

Every HTTP application response includes `X-Request-ID` and `Cache-Control: no-store`. Use `application/problem+json` before a stream begins. Its `type` is `urn:voxrn:problem:<code>`. `status` is the corresponding HTTP status. `retryable` describes the reported failure. A client still needs idempotency or knowledge that no durable effect began before retrying an unsafe operation. Respect `Retry-After` on overload or unavailable responses. Distinguish malformed input (400), unsupported capabilities (422), overload (429), upstream transport or invalid protocol (502), unavailable (503), and deadline (504). Cross-tenant objects are indistinguishable from unknown IDs. Expired objects return 410 to their former owner while expiry metadata remains available.

Streams with an application terminal event treat premature EOF as uncertain or failed delivery. A successful HTTP status establishes only admission of the stream. Bound per-tenant concurrency, buffering and total request time. Never discard admitted input to make a request fit. Cancel underlying work on disconnect where the service owns work only for that connection. A durable agent turn follows its explicit cancellation contract and survives reattachment by an authorized client.

Derive tenant and caller permissions from verified identity and scoped credentials. Authorize each access to a media object or run. Never accept client-supplied filesystem paths, provider credentials or arbitrary fetch URLs. Keep raw media, text, prompts and model output out of default logs and traces. Limit any exact request evidence to an access-controlled, time-bounded diagnostic store. Record model/revision, request and operation IDs, durations, queue wait, terminal reason, and deployment revision. Remove expired artifacts and apply retention to backups and replicas.

## MCP

Expose Streamable HTTP on `/mcp` implementing revision `2026-07-28`. Discover with `server/discover`, then use `tools/list` and `tools/call`. Each call identifies its protocol version in request metadata and HTTP headers as required by the spec. Validate the `Mcp-Method` and applicable `Mcp-Name` routing headers against the request body. Media IDs and run IDs are service-owned objects and are passed explicitly on each call. The core v1 profile serves the stateless revision. An older handshake-based client can use the HTTP service API or a separately versioned and tested bridge if one is implemented. Streamable HTTP carries MCP messages. Continuous application-level audio or event feeds use the native service transports.

Use the MCP authorization profile with OAuth protected resource discovery, audience-restricted tokens, and scope checks on each call. Validate allowed origins when an Origin is present. When scopes are insufficient, use the applicable authentication challenge. The JSON tool catalog's `x-required-scope` is deployment metadata. Remove that field when serializing MCP Tool objects. Keep the published tool list sorted by name. Advertise only the tools capability. Serve these bounded tools with JSON results; complete MCP results carry `resultType: "complete"`. Return successful structured data as `structuredContent: {"ok":true,"data":...}` with `isError:false`. On tool execution failure, return `isError:true` and `structuredContent: {"ok":false,"problem":...}`. Include a text content item containing the serialized structured object for clients that consume only text content. Protocol failures such as unknown tool names use MCP's JSON-RPC error handling. HTTP authentication and transport validation happen before tool dispatch. Clients inspecting MCP results must check `isError` and the typed problem before using output.

### Discovery example

```sh
curl "$SERVICE_URL/mcp" \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  --data '{
    "jsonrpc":"2.0","id":1,"method":"server/discover",
    "params":{"_meta":{
      "io.modelcontextprotocol/protocolVersion":"2026-07-28",
      "io.modelcontextprotocol/clientInfo":{"name":"example","version":"1.0.0"},
      "io.modelcontextprotocol/clientCapabilities":{}
    }}
  }'
```

For `tools/list`, set the method in the body and `Mcp-Method` header to `tools/list` and keep the same metadata. For `tools/call`, also send the selected name as `Mcp-Name` and `params.name`, with the tool input under `params.arguments`.

## Normative references

- [OpenAPI 3.2.1](https://spec.openapis.org/oas/v3.2.1.html) and [JSON Schema 2020-12](https://json-schema.org/draft/2020-12).
- [MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28), [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http), [tool results](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), and [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).
- [HTTP semantics, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html), [problem details, RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html), [OAuth protected resource metadata, RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html), and [Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html).
