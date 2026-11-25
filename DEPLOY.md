# Deploy voxrn-agent

## Local service and tray

Run `./setup.sh` as the service owner on Ubuntu 24.04 or Debian 12 with systemd and a user session. The installer uses sudo for missing APT packages, checks architecture and ports, provisions an isolated Python environment and pinned model assets, installs the user service, and waits for readiness and authenticated capabilities. It enables service startup at user login. Keep the checkout in place because the service unit refers to it.

The service listens at `http://127.0.0.1:8313` by default. The API console lives at `/docs/`. Set `--port` to change it. The tray shows active and enabled status, opens the console, copies the bearer token, and controls start, stop, restart, enable, and disable. Its Settings dialog adjusts the local port, allowed browser origins and bearer token. It starts inside a graphical session with AppIndicator support. Use `--no-tray` for servers without a desktop. Store the token in the protected settings file and paste it into the console only when calling your runtime.

```sh
systemctl --user status voxrn-agent.service
journalctl --user -u voxrn-agent.service -e
```

The installer uses Ollama and Qwen3 0.6B on CPU. For accelerator deployments, provision an Ollama service separately, pin its version and model digest, and configure the VoxRN agent against its URL. The native installer supports x86-64 and arm64 CPU profiles. GPU enumeration, driver installation and automatic GPU leases are outside this installer.

The installer provisions a separate local Ollama process and pins `qwen3:0.6b` by digest. It reuses an installed Ollama binary when available and downloads a checksum-verified release otherwise. The native provider port defaults to 8314. Use `--ollama-port` to choose a different port. For an offline or interrupted installation, `--ollama-model-cache PATH` seeds only the pinned model and checks each blob before importing it. The Compose configuration pairs the installed workflow with the recorded engine version.


## Containers

Use Docker with the Compose plugin. Provision the private model and data directories before launching the containers. Choose a different port if the native service already uses the default.

```sh
./setup.sh --prepare-only --no-tray
docker compose --env-file "${XDG_DATA_HOME:-$HOME/.local/share}/voxrn/agent/compose.env" up -d --build --wait
```

The Compose default binds to loopback and uses the generated bearer token. For managed deployments, build the Docker image with an immutable revision tag, mount the configured model assets and persistent data, expose the application behind HTTPS, and use the OAuth settings in [SERVING.md](SERVING.md). Store credentials in the deployment's secret manager. In production, deploy the console and API on the same origin if possible. To use the GitHub Pages console against a separate HTTPS runtime, set `VOXRN_ALLOWED_ORIGINS` to the Pages origin, such as `https://robit-man.github.io`, and configure the expected runtime host. Browsers apply their own local-network access policies to requests from public sites.

## GitHub Pages

Select **Settings → Pages → Deploy from a branch**, then select `main` and `/docs`. Pages hosts the documentation and browser client. Inference requires a running VoxRN service and credentials for that service. Review the [GitHub Pages visibility and plan requirements](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site) before publishing.

The console can browse and call HTTP endpoints, run MCP discovery or tools, inspect SSE, and download returned WAV audio.

## Deployment verification

Verified on 2026-11-24 Pacific time using Ubuntu 24.04.4 x86-64 CPU. The native installer created a temporary user systemd unit, provisioned real weights, and passed readiness with authenticated capabilities. Chromium used the installed console for capabilities, MCP discovery and native inference. The GTK tray initialized under Xvfb. Checkout-specific Docker builds and Compose startup were exercised separately.

- Native Ollama 0.40.18 served `qwen3:0.6b`, digest `7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435`. Chat returned the correct arithmetic answer. A separate one-digit prompt returned `2 + 3 = 5`, which establishes the answer and leaves strict output formatting unqualified. Buffered request `ba5d5fda907b45e68128a04fbbb202bd`.
- SSE reached the completion marker and MCP `agent_infer` returned the answer. MCP request `f3e6fc4d2b174cc59486c54179351d77`.
- The installed example workflow admitted `Please record Tuesday.`, committed `{"day":"Tuesday"}`, and emitted a native `action.receipt` with status `recorded`. Run `649de1a78cc942d89d84c6df48fc730c`, event-readback request `e73cc099b0a940b1a1eb09cae95416f0`.
- The API and dedicated Ollama Compose containers passed readiness and answered the smoke prompt. The first attempt encountered exhausted Docker bridge address pools on the shared host. Releasing only the completed test stacks' networks allowed the same configuration to start.
- The first native setup attempt stopped on an upstream 503 at model-manifest retrieval. A hash-verified cache seed recovered the identical model and startup. A subsequent clean provisioning run fetched the pinned model without a cache seed. Offline checks exercised bounded retry and corrupt-blob rejection. See [agent registry issue #1](https://github.com/robit-man/voxrn-agent/issues/1).

These checks cover standalone operation. Fresh Debian and arm64 installations, automatic accelerator provisioning, and the Voryn production cutover remain to be verified.
