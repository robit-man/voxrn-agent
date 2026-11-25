# Model profiles and control

## Install and select

```sh
./setup.sh --list-models
./setup.sh --model qwen3.5-4b
./modelctl list
./modelctl enable qwen3.5-4b
./modelctl disable qwen3.5-4b
```

A fresh native setup installs the service runtime and lists the model library without downloading or activating model weights. Enable a profile from the tray, the Pages console, or `modelctl`. The first enable checks available filesystem space and host memory against the profile budget and provisions exact revisions. CUDA profiles also require a supported GPU scope and sufficient reserved memory. Each Python model uses a private virtual environment under `profiles/<model>/venv`. Agent models use the pinned managed Ollama engine and retain a separate profile receipt for each exact model manifest.

The API reports whether a model is installed, the runtime type, its resource budget and activation status. Disable stops admission, drains requests and releases the worker or runner. The installed environment and weights can be reused on the next enable. Uninstall removes the owned installation. Use the installed command `voxrn-agent-modelctl` anywhere, or supply `--prefix` to the repository commands to select a separate installation.

You can also provision a model explicitly with `./setup.sh --model <model-id>` while keeping its activation separate.

For an asset that needs an explicit optional feature, use `./setup.sh --model-feature <model-id>:<feature>`. Use the Pages console's **Manage model library** panel, the desktop tray's **Models** section, or the CLI. An OAuth token needs the `agent:manage` scope for these operations.

| Endpoint | Effect |
| --- | --- |
| `GET /v1/control/models` | Lists available profiles, installation and resource metadata, desired enablement, and in-flight counts |
| `PUT /v1/control/models/{model_id}` | Persists `{"enabled": true}` or `{"enabled": false}` and returns 202 |

While a disable is draining, the service stops admitting new requests for that model. Each admitted request holds a lease until its physical work finishes. Then the model releases its worker or owned engine resources. Inspect the installed model list to verify completion. Control stays reachable if every model is disabled. Installed weights and environments stay available for a later enable.

## Adapter boundaries

Providers own version and weight checks and the native request/response interpreter. The workflow runtime owns source admission, binding authority, durable state and operation receipts. The provider's full identity follows the admitted workflow revision.

Register future packages in `scripts/model-profiles.json`. A profile declares a stable ID, its pinned requirements file where applicable, and pinned model assets. A Hugging Face asset declares the repository, immutable revision, paths to download, and its runtime environment key. A file asset uses HTTPS with a SHA-256. Setup prepares isolated dependency and model paths, and the service loader resolves them to an adapter with explicit identity and feature metadata.

Keep engine dependencies and response interpretation inside the adapter or its subprocess. Register with the shared model controller and hold its lease around physical requests, streams and cancellation. Set capabilities only for actually supported operations. Verify both native HTTP and the linked MCP tool, including disabled and unsupported capability cases.

## Hugging Face access

Open **Hugging Face access** in **Model library** after connecting the dashboard with `agent:manage` or an admin tunnel key. Add a Hugging Face read token with access to the repositories you plan to install. For a gated repository, request access on Hugging Face first. **Key saved** confirms that the service stored the token. Successful model installation verifies download access.

The service saves the key in a private file under its data directory. Each managed Hugging Face download reads the saved key at the start of provisioning and sends it only to the official Hub. Replace or remove the key from the same dashboard control. Removing a key takes effect for future downloads and leaves installed models available. Deployments using ambient Hugging Face credentials retain that installation option.

| Endpoint | Effect |
| --- | --- |
| `GET /v1/control/huggingface` | Returns `{"configured": true}` or `{"configured": false}` |
| `PUT /v1/control/huggingface` | Saves the supplied `token` field and returns its configuration status |
| `DELETE /v1/control/huggingface` | Removes the saved key and returns `{"configured": false}` |

## Uninstall

```sh
./uninstall.sh --plan
./uninstall.sh
```

Uninstall stops the owned service units, tray, and recorded standalone processes. It removes the installation's model snapshots, Python environments, cache, credentials, runtime logs, media, run data, and labelled Compose resources. It checks the installation marker and process start identity before stopping or deleting anything. Compose resources are matched to the installation-specific project label. Shared OS packages, external inference providers, and the Git checkout are outside the installation directory. Retain any run or audit material you still need before purging that directory.

See [deployment and verification](DEPLOY.md) and the service's [model review](MODEL_REVIEW.md).
