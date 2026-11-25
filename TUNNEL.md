# Cloudflare sessions

`./setup.sh --with-tunnel` installs the service, registers the per-service user unit and command, and prepares the terminal menu. The connector starts when you open a session. On Linux x86-64 and arm64, setup can download the pinned official cloudflared binary into the installation when a system binary is unavailable. [Cloudflare release](https://github.com/cloudflare/cloudflared/releases/tag/2026.8.2)

Open **voxrn-agent → Open controls → Remote session** in the tray. The panel stays open through updates and section expansion. Start a Quick Tunnel, copy its endpoint or a console link, rotate the key, or close forwarding. Run the registered command without arguments to open the terminal menu.

```sh
voxrn-agent-tunnel open --direct
voxrn-agent-tunnel endpoint
voxrn-agent-tunnel key
voxrn-agent-tunnel link
voxrn-agent-tunnel link --direct
voxrn-agent-tunnel link --query
voxrn-agent-tunnel rotate --direct
voxrn-agent-tunnel close
```

Opening the bare endpoint redirects to its own console. Paste the same bare origin into a separate console's **Runtime URL** field or use it as the base for `/v1/…` and `/mcp` with `Authorization: Bearer <session-key>`. A console link fills the endpoint and key in memory, checks the available models and opens management. The connection fields are removed from the browser URL. The default link uses fragment parameters to keep the key out of HTTP requests to the static site. Use `--query` when literal query parameters are needed. Query parameters are visible to the frontend host when loading the page.

Each session has its own principal and current key. Rotation preserves that session's media and runs and revokes the old key for new requests. The local API token stays separate. Closing the session revokes its key and stops forwarding. The console documents and assets can be viewed at the tunnel hostname. API and MCP requests require the session key. Use the authorized Pages origin or the tunnel's own console for browser API requests.

Native setup registers `voxrn-agent-tunnel.service`. Inspect it with `systemctl --user status voxrn-agent-tunnel.service` and `journalctl --user -u voxrn-agent-tunnel.service`. Set `--pages-url` at setup to use a different static console.

## Streaming through Cloudflare

Quick Tunnels support buffered HTTP, JSON MCP and ASR WebSockets. Cloudflare requires a named tunnel for Server-Sent Events. Use a named tunnel to test TTS PCM streams or Agent SSE. [Cloudflare Quick Tunnel limits](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

For a remotely managed named tunnel, set its public hostname's HTTP service to `http://127.0.0.1:9313`, the dedicated tunnel port. Use the port passed to `--tunnel-port` if you changed it. Supply the connector token from a private file.

```sh
voxrn-agent-tunnel open --named-url https://service.example --token-file /path/to/connector-token --direct
```

The Cloudflare connector token configures the tunnel. The generated VoxRN session key controls requests to the API. [Cloudflare named tunnel setup](https://developers.cloudflare.com/tunnel/get-started/)
