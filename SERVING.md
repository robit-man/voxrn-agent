# Serving VoxRN Agent

## Provider and workflow profile

Install `requirements.txt`. Provision a compatible Ollama server and model independently. Set these settings before running the app or preparing the example bundle.

| Setting | Purpose |
| --- | --- |
| `VOXRN_AGENT_OLLAMA_URL` | Base URL of the trusted Ollama provider. |
| `VOXRN_AGENT_MODEL_TAG` | Exact model tag as returned by `/api/tags`. The public alias is `qwen-local`. |
| `VOXRN_AGENT_MODEL_DIGEST` | Exact model digest from the provider catalog. |
| `VOXRN_AGENT_ENGINE_REVISION` | Exact engine version from `/api/version`. |
| `VOXRN_AGENT_CONTEXT_TOKENS` | Configured provider context window. Defaults to 8192. |
| `VOXRN_AGENT_THINK` | Optional explicit Ollama thinking mode, `true` or `false`. The installer uses `false` for its small Qwen profile. |
| `VOXRN_AGENT_PROVIDER_TOKEN` | Optional bearer credential for the trusted provider. |
| `VOXRN_AGENT_MODELS_FILE` | JSON registry of installed provider profiles. When set, the file defines the model selection. |
| `VOXRN_AGENT_WORKFLOW_DIR` | Directory containing an admitted workflow bundle. Omit for stateless inference only. |
| `VOXRN_AGENT_WORKFLOW_DIRS` | Path-separated list of admitted bundle directories. Takes precedence over the single-directory setting. |

The service verifies the engine version and tag digest before provider requests. Prevent provider-side model-tag changes while the service is active. The context gate uses a conservative JSON serialization bound plus output and framing allowances. It rejects oversized requests without dropping messages. Verify actual serialized bodies with the deployed provider before integrating Voryn.

### Installed model registry

Each entry in the registry names a stable public model ID and pins a concrete upstream tag, model digest, engine version, and endpoint. The chat API and MCP `agent_infer` select the profile by public ID. A workflow bundle pins the same full identity and can run only while the matching profile is available.

| Public model ID | Ollama tag | Deployment profile |
| --- | --- | --- |
| `qwen-local` | `qwen3:0.6b` | Existing local profile |
| `qwen3.5-4b` | `qwen3.5:4b` | Compact agent profile |
| `qwen3.6-27b` | `qwen3.6:27b` | Workstation and server profile |
| `gemma4-26b` | `gemma4:26b` | Independent workload comparison |

The [installation profiles](scripts/model-profiles.json) record the exact pinned digests. For an independently provisioned Ollama server, install the pinned tag and point a registry entry at that server.

```json
{
  "models": [
    {
      "model": "qwen3.5-4b",
      "tag": "qwen3.5:4b",
      "digest": "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04",
      "engine": "ollama",
      "engine_revision": "0.40.18",
      "url": "http://127.0.0.1:11434",
      "context_tokens": 8192,
      "think": false,
      "enabled": true
    }
  ]
}
```

Use `credential_env` to name an environment variable containing the provider bearer token. Set `structured_outputs` to false for a text-only profile. An entry enabled in configuration supplies an initial preference. Subsequent control decisions persist with the exact model identity. `GET /v1/capabilities` includes profiles that are enabled and whose upstream engine and digest still match.

The `agent:manage` scope grants access to `GET /v1/control/models` and `PUT /v1/control/models/{model_id}` with an `{"enabled":false}` body. Disabling a model blocks new calls and drains active inference. A durable turn accepted before disablement stays queued if the worker has yet to take a provider lease. It retains its run revision and exact idempotent admission response while waiting for re-enablement. Queued turns remain cancellable. Use `managed: true` only with an installer-owned runner and a tag exclusive to the profile. The adapter then verifies Ollama's unload receipt. For externally managed servers, disabling closes the VoxRN client.

The service layer resolves the public model ID and holds the lifecycle lease. The provider adapter opens a transport and verifies its upstream catalog identity. The Ollama interpreter serializes the complete message history and decodes terminal packets. A new engine supplies its own adapter and interpreter and registers its factory in `service/provider_registry.py`. The adapter declares its identity, capabilities, runner ownership and initial availability. It receives the common messages, token budget, and optional workflow schema.

### Register models already installed in Ollama

Use an `agent:manage` token to call `GET /v1/control/ollama/catalog`. The response reports the configured provider URL and version, each installed tag's exact digest and size, and the existing VoxRN model ID when already registered. VoxRN selects the single configured Ollama backend by default. For installations with several backends, set `VOXRN_AGENT_OLLAMA_CATALOG_MODEL` to a configured model ID.

To connect to a separately operated backend, set the trusted deployment environment variable `VOXRN_AGENT_OLLAMA_CATALOG_URL` and optionally `VOXRN_AGENT_OLLAMA_CATALOG_TOKEN`. This mode can start without a preconfigured model. Provider addresses and credentials are configured by the operator before launching the service.

Submit a catalog entry's `tag` and `digest` to `POST /v1/control/ollama/registrations`. A new registration returns a disabled chat profile. Use `PUT /v1/control/models/{model_id}` to enable the returned alias, then select it for buffered chat, SSE chat, or MCP `agent_infer`. Registration rejects a changed digest and models whose Ollama metadata explicitly lacks text completion. It reads metadata without downloading or loading a model. The provider version and digest are verified before inference and returned in the provenance headers. [Ollama model information API](https://github.com/ollama/ollama/blob/main/docs/api.md#show-model-information)

Registrations persist in the Agent data directory and stay bound to the configured backend. A provider upgrade or tag change requires a new registration for the new identity. Ollama continues to manage its own weights and runner residency. Imported profiles serve stateless chat. Durable workflows keep their sealed bundle identity and require a separately configured profile with structured outputs.

To use the standalone request-recording example, generate its pinned manifest and point the service at its directory.

```sh
python -m examples.intake.prepare
export VOXRN_AGENT_WORKFLOW_DIR="$PWD/examples/intake"
```

Use `--model qwen3.5-4b` when preparing the example against that configured profile. Prepare bundles with distinct workflow IDs or revisions when both must be installed. Existing bundles keep their sealed model identities through control-plane changes.

The example workflow uses the `record_intake` entry point and `native_request_log` binding. It commits an explicit weekday and a local recording receipt in one transaction. There is no external executor in this ABI. The workflow authoring pipeline and Voryn-specific retained executables remain in Voryn. To run the example in a container, prepare the bundle outside the image and mount it read-only.

Use a dedicated persistent data directory for the single-node SQLite store. A worker resumes queued jobs and reacquires expired leases on restart. The writer fence keeps an expired or cancelled worker from committing. The transactional record adapter has no dispatch window outside the store. Supporting distributed execution, external scheduling, and full Voryn source workflow artifacts requires separate capability and parity work.

## Common serving configuration

Use Python 3.12. From this repository's root, create a virtual environment and install the profile's requirements. Keep the data directory writable by the service and private to its runtime identity. To use a single local development principal, supply an unpredictable bearer token of at least 24 characters as `VOXRN_DEV_TOKEN` and bind to loopback.

Start the service with the settings in this document.

```sh
python -m uvicorn service.server:create_app --factory --host 127.0.0.1 --port 8000 --ws-max-size 32768
```

Check `/livez`, `/readyz`, then `/v1/capabilities` with the bearer credential. The OpenAPI document is served at `/contracts/openapi.json`. MCP uses the same bearer credential locally and Streamable HTTP at `/mcp`. See [MCP discovery](CONVENTIONS.md#mcp) and the [tool schemas](contracts/mcp-tools.json).

| Setting | Purpose |
| --- | --- |
| `VOXRN_DATA_DIR` | Private writable data directory. Defaults to `.data`. |
| `VOXRN_SERVICE_REVISION` | Deployed source revision exposed by the catalog. |
| `VOXRN_ALLOWED_HOSTS` | Space-separated MCP host patterns. Defaults to localhost, 127.0.0.1 and ::1 with a port. |
| `VOXRN_ALLOWED_ORIGINS` | Space-separated exact origins for browser access. Configure a gateway for browser credentials and CORS. |
| `VOXRN_OAUTH_ISSUER` | HTTPS OAuth issuer with published discovery metadata. |
| `VOXRN_OAUTH_INTROSPECTION_URL` | HTTPS RFC 7662 token introspection URL. |
| `VOXRN_OAUTH_CLIENT_ID`, `VOXRN_OAUTH_CLIENT_SECRET` | Resource-server introspection credentials. |
| `VOXRN_MCP_RESOURCE` | Public HTTPS MCP resource URI ending in `/mcp`. This profile uses that URI as the expected audience for both APIs. |

For an OAuth deployment, leave `VOXRN_DEV_TOKEN` unset, terminate TLS at the serving boundary, and configure the host and resource settings. Configure the identity service to return `active`, `iss`, `aud`, `sub` and space-separated `scope`; an optional `tenant_id` defaults to the subject. MCP publishes protected-resource discovery at `/.well-known/oauth-protected-resource/mcp`.

The included Dockerfile packages a single-process CPU profile. Supply model assets, OAuth configuration, and a writable data volume at runtime. See [deployment verification](DEPLOY.md#deployment-verification) for the independent native and container checks. Model promotion and integration into Voryn require separate qualification.

```sh
python -m unittest discover -s tests -v
```
