# Model profiles and control

## Install and select

```sh
./setup.sh --list-models
./setup.sh --model qwen3.5-4b
./modelctl list
./modelctl enable qwen3.5-4b
./modelctl disable qwen3.5-4b
```

Setup provisions a named profile and publishes it as installed. Optional profiles start disabled until explicitly enabled. A model ID selects a profile in the existing inference request; the service catalog lists the enabled models that finished loading. The model state is persisted by exact model and engine identity. A profile with a changed identity gets the configured default for that identity. Use `--prefix` across setup, modelctl and uninstall to select a separate install directory.

For an asset that needs an explicit optional feature, use `./setup.sh --model-feature <model-id>:<feature>`. Use the Pages console's **Manage installed models** panel, the desktop tray's **Models** dialog, or the CLI. An OAuth token needs the `agent:manage` scope for these operations.

| Endpoint | Effect |
| --- | --- |
| `GET /v1/control/models` | Lists installed models, desired enablement, loading or draining status, and in-flight counts |
| `PUT /v1/control/models/{model_id}` | Persists `{"enabled": true}` or `{"enabled": false}` and returns 202 |

While a disable is draining, the service stops admitting new requests for that model. Each admitted request holds a lease until its physical work finishes. Then the model releases its worker or owned engine resources. Inspect the installed model list to verify completion. Control stays reachable if every model is disabled. Installed weights and environments stay available for a later enable.

## Adapter boundaries

Providers own version and weight checks and the native request/response interpreter. The workflow runtime owns source admission, binding authority, durable state and operation receipts. The provider's full identity follows the admitted workflow revision.

Register future packages in `scripts/model-profiles.json`. A profile declares a stable ID, its pinned requirements file where applicable, and pinned model assets. A Hugging Face asset declares the repository, immutable revision, paths to download, and its runtime environment key. A file asset uses HTTPS with a SHA-256. Setup prepares isolated dependency and model paths, and the service loader resolves them to an adapter with explicit identity and feature metadata.

Keep engine dependencies and response interpretation inside the adapter or its subprocess. Register with the shared model controller and hold its lease around physical requests, streams and cancellation. Set capabilities only for actually supported operations. Verify both native HTTP and the linked MCP tool, including disabled and unsupported capability cases.

## Uninstall

```sh
./uninstall.sh --plan
./uninstall.sh
```

Uninstall stops the owned service units, tray, and recorded standalone processes. It removes the installation's model snapshots, Python environments, cache, credentials, runtime logs, media, run data, and labelled Compose resources. It checks the installation marker and process start identity before stopping or deleting anything. Compose resources are matched to the installation-specific project label. Shared OS packages, external inference providers, and the Git checkout are outside the installation directory. Retain any run or audit material you still need before purging that directory.

See [deployment and verification](DEPLOY.md) and the service's [model review](MODEL_REVIEW.md).
