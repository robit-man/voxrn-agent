"""HTTP, authorization, validation, and MCP conventions shared by this service."""

import asyncio
import hmac
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from .telemetry import RuntimeMiddleware, Telemetry, mark_client, mark_failure
from .tunnel_access import TunnelAccess, TunnelMiddleware

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
OPENAPI = json.loads((CONTRACTS / "openapi.json").read_text())
CATALOG = json.loads((CONTRACTS / "mcp-tools.json").read_text())
PROTOCOL = CATALOG["protocolVersion"]
LOG = logging.getLogger(__name__)
REQUEST_ID = ContextVar("request_id", default="startup")
CALLER = ContextVar("caller")
JSON_LIMIT = 2 * 1024 * 1024


class Problem(Exception):
    def __init__(self, status, code, detail, *, retryable=False, required_scopes=()):
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.required_scopes = required_scopes

    def body(self):
        return {
            "type": f"urn:voxrn:problem:{self.code}",
            "status": self.status,
            "title": self.code.replace("_", " ").capitalize(),
            "detail": self.detail,
            "code": self.code,
            "request_id": REQUEST_ID.get(),
            "retryable": self.retryable,
        }


def new_id():
    return uuid.uuid4().hex


def canonical_json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def strict_json(raw):
    def map_fields(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("Duplicate JSON key")
        return value

    def reject_constant(key):
        raise ValueError(f"Unexpected constant {key}")

    try:
        value = json.loads(
            raw, object_pairs_hook=map_fields, parse_constant=reject_constant
        )
        # Also reject JSON escapes containing lone surrogates and numbers that overflowed.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Problem(
            400, "invalid_json", "Send valid JSON with unique keys and finite numbers."
        ) from exc


def validate(value, schema, *, status=400):
    if isinstance(schema, str):
        schema = {
            "$ref": f"#/components/schemas/{schema}",
            "components": OPENAPI["components"],
        }
    try:
        valid = Draft202012Validator(schema).is_valid(value)
    except (RecursionError, TypeError):
        valid = False
    if not valid:
        raise Problem(
            status,
            "invalid_request" if status == 400 else "invalid_output",
            "The value does not conform to the declared schema.",
        )
    return value


def event_sse(value, *, replay=False):
    event_id = f"id: {value['seq']}\n" if replay else ""
    return f"{event_id}event: {value['type']}\ndata: {canonical_json(value)}\n\n"


async def read_body(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise Problem(
                413, "body_too_large", "Request exceeded the service byte limit."
            )
        body.extend(chunk)
    return bytes(body)


async def json_body(request, schema, *, limit=JSON_LIMIT):
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise Problem(415, "unsupported_media_type", "Send application/json.")
    return validate(strict_json(await read_body(request, limit)), schema)


@dataclass(frozen=True)
class Principal:
    issuer: str
    tenant: str
    subject: str
    scopes: frozenset[str]

    @property
    def owner(self):
        return canonical_json([self.issuer, self.tenant, self.subject])

    def require(self, scopes):
        if not set(scopes) <= self.scopes:
            raise Problem(
                403,
                "insufficient_scope",
                "The credential does not grant the operation's required scopes.",
                required_scopes=scopes,
            )


class Authorization:
    def __init__(self, kind):
        self.scopes = frozenset(
            OPENAPI["components"]["securitySchemes"]["oauth"]["flows"][
                "clientCredentials"
            ]["scopes"]
        )
        self.dev = os.environ.get("VOXRN_DEV_TOKEN")
        self.issuer = os.environ.get("VOXRN_OAUTH_ISSUER")
        self.resource = os.environ.get("VOXRN_MCP_RESOURCE")
        self.introspection = os.environ.get("VOXRN_OAUTH_INTROSPECTION_URL")
        self.auth = (
            os.environ.get("VOXRN_OAUTH_CLIENT_ID"),
            os.environ.get("VOXRN_OAUTH_CLIENT_SECRET"),
        )
        self.origins = set(os.environ.get("VOXRN_ALLOWED_ORIGINS", "").split())
        local_port = os.environ.get("VOXRN_LOCAL_PORT", "8000")
        self.origins.update(
            f"http://{host}:{local_port}"
            for host in ("127.0.0.1", "localhost", "[::1]")
        )
        self.hosts = os.environ.get(
            "VOXRN_ALLOWED_HOSTS", "localhost:* 127.0.0.1:* [::1]:*"
        ).split()
        self.kind = kind
        if not self.dev and not (
            self.resource
            and self.issuer
            and self.introspection
            and all(self.auth)
            and all(
                urlsplit(value).scheme == "https"
                for value in (self.resource, self.issuer, self.introspection)
            )
            and urlsplit(self.resource).path == "/mcp"
        ):
            raise RuntimeError(
                "Configure HTTPS OAuth introspection and MCP resource identity, or a local VOXRN_DEV_TOKEN."
            )
        if self.dev and len(self.dev) < 24:
            raise RuntimeError("VOXRN_DEV_TOKEN must be at least 24 characters.")

    def check_origin(self, headers):
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            raise Problem(
                403, "origin_not_allowed", "The request Origin is not allowed."
            )

    async def authenticate(self, headers, *, scope=None):
        if scope and (subject := scope.get("state", {}).get("tunnel_subject")):
            return Principal("voxrn-tunnel", "session", subject, self.scopes)
        self.check_origin(headers)
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 8192:
            raise Problem(401, "unauthorized", "Provide a valid bearer credential.")
        if self.dev and hmac.compare_digest(token.encode(), self.dev.encode()):
            return Principal("local", "local", "developer", self.scopes)
        if not (self.introspection and all(self.auth)):
            raise Problem(401, "unauthorized", "Provide a valid bearer credential.")
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.post(
                    self.introspection, data={"token": token}, auth=self.auth
                )
                response.raise_for_status()
                claims = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Problem(
                503,
                "authorization_unavailable",
                "Token verification is unavailable.",
                retryable=True,
            ) from exc
        if not isinstance(claims, dict):
            raise Problem(401, "unauthorized", "Provide a valid bearer credential.")
        audience = claims.get("aud", ())
        if isinstance(audience, str):
            audience = [audience]
        subject = claims.get("sub")
        scope = claims.get("scope", "")
        if (
            claims.get("active") is not True
            or claims.get("iss") != self.issuer
            or not isinstance(audience, (list, tuple))
            or self.resource not in audience
            or not isinstance(subject, str)
            or not subject
            or not isinstance(scope, str)
        ):
            raise Problem(
                401, "unauthorized", "Provide an active credential for this resource."
            )
        tenant = claims.get("tenant_id", subject)
        if not isinstance(tenant, str) or not tenant:
            raise Problem(
                401,
                "unauthorized",
                "Provide a credential with a valid tenant identity.",
            )
        return Principal(self.issuer, tenant, subject, frozenset(scope.split()))

    def problem_response(self, exc):
        headers = {}
        if exc.status in (429, 503) and exc.retryable:
            headers["Retry-After"] = "1"
        if exc.status == 401 or exc.code == "insufficient_scope":
            challenge = "Bearer"
            if exc.code == "insufficient_scope":
                required = " ".join(sorted(exc.required_scopes))
                challenge += f' error="insufficient_scope", scope="{required}"'
            if self.resource:
                location = (
                    self.resource.removesuffix("/mcp")
                    + "/.well-known/oauth-protected-resource/mcp"
                )
                challenge += (
                    ", " if exc.code == "insufficient_scope" else " "
                ) + f'resource_metadata="{location}"'
            headers["WWW-Authenticate"] = challenge
        return JSONResponse(
            exc.body(),
            status_code=exc.status,
            media_type="application/problem+json",
            headers=headers,
        )


def build_service(
    kind, ready, handlers, *, start=None, shutdown=None, model_control=None
):
    auth = Authorization(kind)
    tunnel = TunnelAccess()
    telemetry = Telemetry(
        OPENAPI["paths"], [item["name"] for item in CATALOG["tools"]], model_control
    )
    if model_control is not None:
        model_control.telemetry = telemetry
    tools = {}
    scopes = {}
    for entry in CATALOG["tools"]:
        scope_key = "x-required-scope"
        definition = types.Tool.model_validate(
            {key: value for key, value in entry.items() if key != scope_key}
        )
        tools[definition.name] = definition
        scopes[definition.name] = entry[scope_key].split()

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=list(tools.values()))

    async def call_tool(_context, params):
        tool = tools.get(params.name)
        if tool is None:
            from mcp.shared.exceptions import MCPError

            raise MCPError(
                types.ErrorData(code=types.INVALID_PARAMS, message="Unknown tool.")
            )
        try:
            principal = CALLER.get()
            principal.require(scopes[params.name])
            arguments = validate(params.arguments or {}, tool.input_schema)
            data = await handlers[params.name](principal, arguments)
            result = {"ok": True, "data": data}
            validate(result, tool.output_schema, status=502)
        except Problem as exc:
            mark_failure()
            result = {"ok": False, "problem": exc.body()}
        except Exception as exc:
            mark_failure()
            LOG.error(
                "Unhandled MCP failure request=%s type=%s",
                REQUEST_ID.get(),
                type(exc).__name__,
            )
            result = {
                "ok": False,
                "problem": Problem(
                    500, "internal_error", "Tool execution failed."
                ).body(),
            }
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=canonical_json(result))],
            structuredContent=result,
            isError=not result["ok"],
        )

    server = Server(
        f"voxrn-{kind}",
        version="0.1.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: (
            tools[name].input_schema if name in tools else None
        ),
    )
    mcp = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=JSON_LIMIT,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=auth.hosts,
            allowed_origins=sorted(
                auth.origins | ({tunnel.origin} if tunnel.origin else set())
            ),
        ),
    )

    @asynccontextmanager
    async def lifespan(_app):
        import asyncio

        telemetry.event("service", "starting")
        async with server.session_manager.run():
            if start:
                await start()
            telemetry.event("service", "running")
            sampler = asyncio.create_task(telemetry.sample())
            try:
                yield
            finally:
                telemetry.event("service", "stopping")
                sampler.cancel()
                await asyncio.gather(sampler, return_exceptions=True)
                if shutdown:
                    await shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = auth

    @app.middleware("http")
    async def identify(request, call_next):
        request_token = REQUEST_ID.set(new_id())
        caller_token = None
        try:
            try:
                if request.url.path == "/mcp" or request.url.path.startswith("/v1/"):
                    caller_token = CALLER.set(
                        await auth.authenticate(request.headers, scope=request.scope)
                    )
                    mark_client(request.scope, CALLER.get())
                if request.url.path == "/mcp" and request.method == "POST":
                    supplied = request.headers.get("mcp-protocol-version")
                    if supplied != PROTOCOL:
                        raise Problem(
                            400,
                            "unsupported_protocol_version",
                            f"Use MCP protocol revision {PROTOCOL}.",
                        )
                    if request.headers.get("mcp-method") == "tools/call":
                        required = scopes.get(request.headers.get("mcp-name"))
                        if required:
                            CALLER.get().require(required)
                response = await call_next(request)
            except Problem as exc:
                response = auth.problem_response(exc)
            except Exception as exc:
                LOG.error(
                    "Unhandled request failure request=%s type=%s",
                    REQUEST_ID.get(),
                    type(exc).__name__,
                )
                response = auth.problem_response(
                    Problem(500, "internal_error", "Service execution failed.")
                )
            response.headers["X-Request-ID"] = REQUEST_ID.get()
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            REQUEST_ID.reset(request_token)
            if caller_token is not None:
                CALLER.reset(caller_token)

    @app.exception_handler(Problem)
    async def problem_handler(_request, exc):
        return auth.problem_response(exc)

    @app.exception_handler(HTTPException)
    async def http_exception(_request, exc):
        return auth.problem_response(
            Problem(
                exc.status_code,
                "http_error",
                "The requested method or resource is unavailable.",
            )
        )

    @app.get("/livez")
    async def live():
        return {"status": "live"}

    @app.get("/readyz")
    async def readiness():
        if not await ready():
            raise Problem(
                503,
                "not_ready",
                "A required service dependency is unavailable.",
                retryable=True,
            )
        return {"status": "ready"}

    @app.get("/v1/capabilities")
    async def capabilities():
        principal = CALLER.get()
        principal.require([f"{kind}:read"])
        return await handlers[f"{kind}_capabilities"](principal, {})

    if model_control is not None:

        @app.get("/v1/control/runtime")
        async def runtime_telemetry(request: Request):
            CALLER.get().require([f"{kind}:manage"])
            args = request.query_params
            window = args.get("window_seconds", "300")
            if window not in {"60", "300", "900"}:
                raise Problem(
                    422, "invalid_window", "Choose a window of 60, 300, or 900 seconds."
                )
            return telemetry.snapshot(
                int(window), args.get("model"), args.get("endpoint")
            )

        @app.get("/v1/control/models")
        async def installed_models():
            CALLER.get().require([f"{kind}:manage"])
            return model_control.list()

        @app.put("/v1/control/models/{model_id}", status_code=202)
        async def set_model_enabled(model_id: str, request: Request):
            CALLER.get().require([f"{kind}:manage"])
            change = await json_body(request, "ModelEnableRequest", limit=4096)
            return model_control.set_enabled(model_id, change["enabled"])

    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def resource_metadata():
        if not auth.resource:
            raise Problem(
                404,
                "not_found",
                "OAuth discovery is available in the OAuth deployment profile.",
            )
        return {
            "resource": auth.resource,
            "authorization_servers": [auth.issuer],
            "scopes_supported": sorted(auth.scopes),
            "bearer_methods_supported": ["header"],
        }

    @app.get("/contracts/openapi.json")
    async def openapi():
        return OPENAPI

    app.mount(
        "/docs",
        StaticFiles(directory=CONTRACTS.parent / "docs", html=True),
        name="docs",
    )
    app.add_middleware(TunnelMiddleware, access=tunnel)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(
            auth.origins | ({tunnel.origin} if tunnel.origin else set())
        ),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
            "MCP-Protocol-Version",
            "Mcp-Method",
            "Mcp-Name",
            "X-VoxRN-Client-ID",
        ],
        expose_headers=["X-Request-ID", "Location", "Retry-After", "WWW-Authenticate"],
    )
    app.add_middleware(RuntimeMiddleware, telemetry=telemetry)
    return app, mcp


@contextmanager
def request_context():
    token = REQUEST_ID.set(new_id())
    try:
        yield
    finally:
        REQUEST_ID.reset(token)


class Admission:
    """Reject queue overflow instead of allowing unbounded inference work."""

    def __init__(self, slots):
        self.slots = slots
        self.active = 0

    @asynccontextmanager
    async def use(self):
        if self.active >= self.slots:
            raise Problem(
                429,
                "capacity_exhausted",
                "All inference slots are in use.",
                retryable=True,
            )
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


async def finish_thread(function, *args, **kwargs):
    """Keep admission owned until a blocking engine has actually returned."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise
