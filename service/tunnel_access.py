"""Authenticate requests arriving on the dedicated Cloudflare origin listener."""

import hmac
import json
import os
import re
import time
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, RedirectResponse

KEY = re.compile(r"[A-Za-z0-9_-]{43}")
SUBJECT = re.compile(r"[a-f0-9]{32}")


def session_scope(session):
    level = session.get("scope", "admin")
    expires = session.get("expires_at")
    if level not in {"admin", "inference"} or (
        expires is not None
        and (type(expires) not in {int, float} or not time.time() < expires)
    ):
        return None
    return level


class TunnelAccess:
    def __init__(self):
        port = os.environ.get("VOXRN_TUNNEL_PORT")
        self.port = int(port) if port else None
        self.state = Path(os.environ["VOXRN_TUNNEL_STATE"]) if self.port else None
        self.origin = os.environ.get("VOXRN_TUNNEL_PAGES_ORIGIN")

    def accepts_origin(self, origin):
        if origin is None or origin == self.origin:
            return True
        try:
            connected = json.loads(
                self.state.with_name("tunnel-connector.json").read_text(
                    encoding="utf-8"
                )
            )
            return origin == connected["endpoint"]
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def subject(self, scope, headers):
        try:
            session = json.loads(self.state.read_text(encoding="utf-8"))
            key, subject = session["key"], session["subject"]
        except (OSError, ValueError, TypeError, KeyError):
            return None
        if not isinstance(subject, str) or not SUBJECT.fullmatch(subject):
            return None
        if not isinstance(key, str) or not KEY.fullmatch(key):
            return None
        origin = headers.get("origin")
        if not self.accepts_origin(origin):
            return None
        scheme, _, supplied = headers.get("authorization", "").partition(" ")
        if not supplied and scope["type"] == "websocket":
            offered = [
                protocol.removeprefix("voxrn-key.")
                for protocol in scope.get("subprotocols", ())
                if protocol.startswith("voxrn-key.")
            ]
            if len(offered) == 1:
                scheme, supplied = "bearer", offered[0]
        if (
            scheme.lower() == "bearer"
            and KEY.fullmatch(supplied)
            and hmac.compare_digest(supplied, key)
        ):
            level = session_scope(session)
            return (subject, level) if level else None
        return None


class TunnelMiddleware:
    def __init__(self, app, access):
        self.app = app
        self.access = access

    async def __call__(self, scope, receive, send):
        server = scope.get("server")
        if (
            self.access.port is None
            or not server
            or server[1] != self.access.port
            or scope["type"] not in {"http", "websocket"}
        ):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope["type"] == "http" and scope["method"] in {"GET", "HEAD"}:
            if path in {"/", "/docs"}:
                response = RedirectResponse(
                    "/docs/", status_code=307, headers={"Cache-Control": "no-store"}
                )
                await response(scope, receive, send)
                return
            if path.startswith("/docs/"):
                await self.app(scope, receive, send)
                return

        headers = Headers(scope=scope)
        if (
            scope["type"] == "http"
            and scope["method"] == "OPTIONS"
            and headers.get("origin") == self.access.origin
            and "access-control-request-method" in headers
        ):
            await self.app(scope, receive, send)
            return
        principal = self.access.subject(scope, headers)
        if principal is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse(
                    {
                        "type": "about:blank",
                        "title": "Unauthorized",
                        "status": 401,
                        "code": "tunnel_unauthorized",
                        "detail": "Provide the current tunnel session key.",
                    },
                    status_code=401,
                    media_type="application/problem+json",
                    headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
            return

        forwarded = dict(scope)
        forwarded["headers"] = [
            (key, value)
            for key, value in scope["headers"]
            if key not in {b"host", b"authorization", b"origin"}
        ] + [(b"host", f"127.0.0.1:{server[1]}".encode("ascii"))]
        if headers.get("origin") and self.access.origin:
            forwarded["headers"].append((b"origin", self.access.origin.encode("ascii")))
        subject, level = principal
        forwarded["state"] = {
            **scope.get("state", {}),
            "tunnel_subject": subject,
            "tunnel_scope": level,
        }
        await self.app(forwarded, receive, send)
