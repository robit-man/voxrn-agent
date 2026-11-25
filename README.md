# VoxRN Agent

Stateless text inference and durable, governed execution of versioned agent workflows.

The version 1.0 contract describes the target API. The standalone runtime exposes the capabilities of its configured adapters. See the serving guide for what can run today and the specification for release acceptance criteria.

- [Model profiles, control and uninstall](MODEL_CONTROL.md)
- [Cloudflare sessions and browser links](TUNNEL.md)
- [Operational quality specification](QUALITY_SPEC.md)
- [Open-model research and ranked candidates](MODEL_REVIEW.md)
- [Standalone serving guide](SERVING.md)
- [Specification, usage and acceptance criteria](SPEC.md)
- [Shared HTTP, deployment, and MCP conventions](CONVENTIONS.md)
- [OpenAPI contract](contracts/openapi.json)
- [MCP tool schemas](contracts/mcp-tools.json)
- [Request and response examples](contracts/examples.json)

## Deploy on Ubuntu or Debian

Authenticate Git for this private GitHub repository, then run this single line to clone or fast-forward the checkout and install the service.

```sh
(command -v git >/dev/null || (sudo apt-get update && sudo apt-get install -y git ca-certificates)) && deploy_dir="${XDG_DATA_HOME:-$HOME/.local/share}/voxrn/checkouts/voxrn-agent" && (test -d "$deploy_dir/.git" || (mkdir -p "$(dirname "$deploy_dir")" && git clone https://github.com/robit-man/voxrn-agent.git "$deploy_dir")) && git -C "$deploy_dir" switch main && git -C "$deploy_dir" pull --ff-only origin main && "$deploy_dir/setup.sh"
```

The installer starts the service and its API console at `http://127.0.0.1:8313/docs/`. [Deployment options and tray controls](DEPLOY.md)

## GitHub Pages

Publish from `main` using the `/docs` folder. The static directory includes an [HTML landing page](docs/index.html), an [agent index](docs/llms.txt), a [machine-readable discovery file](docs/discovery.json), and contract downloads. Configure the MCP client with the deployed service URL and its credentials.

After changing the canonical contracts or the integration guide inputs, run `python3 scripts/build_pages.py` and commit the refreshed files in `docs/`. Run `python3 scripts/build_pages.py --check` to verify they match.
