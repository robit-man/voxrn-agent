"""Serve the local API and a separate loopback-only Cloudflare origin."""

import os
import socket
import sys
from contextlib import ExitStack

import uvicorn


def bind(port):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        return listener
    except BaseException:
        listener.close()
        raise


def main():
    local_port = int(sys.argv[1])
    ports = [local_port]
    if tunnel_port := os.environ.get("VOXRN_TUNNEL_PORT"):
        ports.append(int(tunnel_port))
    config = uvicorn.Config(
        "service.server:create_app",
        factory=True,
        host="127.0.0.1",
        port=local_port,
        ws_max_size=32768,
        access_log=False,
        proxy_headers=False,
    )
    with ExitStack() as cleanup:
        sockets = [cleanup.enter_context(bind(port)) for port in ports]
        uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
