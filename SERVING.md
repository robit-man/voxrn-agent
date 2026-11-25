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
| `VOXRN_AGENT_PROVIDER_TOKEN` | Optional bearer credential for the trusted provider. |
| `VOXRN_AGENT_WORKFLOW_DIR` | Directory containing an admitted workflow bundle. Omit for stateless inference only. |

The service verifies the engine version and tag digest before provider requests. Prevent provider-side model-tag changes while the service is active. The context gate uses a conservative JSON serialization bound plus output and framing allowances. It rejects oversized requests without dropping messages. Verify actual serialized bodies with the deployed provider before integrating Voryn.

To use the standalone request-recording example, generate its pinned manifest and point the service at its directory.

```sh
python -m examples.intake.prepare
export VOXRN_AGENT_WORKFLOW_DIR="$PWD/examples/intake"
```

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

The included Dockerfile packages a single-process CPU profile. Supply model assets, OAuth configuration, and a writable data volume at runtime. This phase verified the application contract in-process, with synthetic fixtures and stub inference adapters. Image builds, real model quality, and integration into Voryn require separate verification.

```sh
python -m unittest discover -s tests -v
```
