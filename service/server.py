"""Standalone agent inference, workflow execution, and MCP entry point."""

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack, aclosing, suppress
from pathlib import Path

from anyio import CancelScope
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from .model_control import ModelControl
from .provider_registry import load_providers
from .state import IDEMPOTENCY_TTL, RUN_IDLE_TTL, TERMINAL, RunStore
from .wire import (
    CALLER,
    JSON_LIMIT,
    OPENAPI,
    PROTOCOL,
    Admission,
    Problem,
    build_service,
    canonical_json,
    event_sse,
    finish_thread,
    json_body,
    new_id,
    request_context,
    validate,
)
from .workflows import load_bundle

LOG = logging.getLogger(__name__)
MAX_TOKENS = 1024
IDENTIFIER = OPENAPI["components"]["schemas"]["RunRequestTool"]["properties"][
    "idempotency_key"
]


class ChatStream(StreamingResponse):
    def __init__(self, content, *, resources, headers):
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self.resources = resources

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await self.resources.aclose()


def provider_features(provider):
    return getattr(
        provider,
        "features",
        {"chat": True, "chat_stream": True, "structured_outputs": True},
    )


async def open_provider(provider):
    await provider.start()


async def close_provider(provider):
    await provider.close()


def create_app(*, provider=None, providers=None, directory=None, workflow_paths=None):
    if provider is not None:
        if providers is not None:
            raise ValueError("Supply either one provider or a provider registry.")
        providers = {provider.identity["model"]: provider}
    if providers is None:
        providers = load_providers()
    directory = Path(directory or os.environ.get("VOXRN_DATA_DIR", ".data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    control = ModelControl(
        providers,
        directory / "model-control.json",
        load=open_provider,
        unload=close_provider,
        default_enabled={
            name
            for name, candidate in providers.items()
            if getattr(candidate, "default_enabled", True)
        },
    )
    if workflow_paths is None:
        configured = os.environ.get("VOXRN_AGENT_WORKFLOW_DIRS") or os.environ.get(
            "VOXRN_AGENT_WORKFLOW_DIR", ""
        )
        workflow_paths = [path for path in configured.split(os.pathsep) if path]
    workflows = {}
    workflow_models = {
        name: candidate.identity
        for name, candidate in providers.items()
        if provider_features(candidate)["structured_outputs"]
    }
    for path in workflow_paths:
        workflow = load_bundle(path, model_identities=workflow_models)
        identity = (workflow.manifest["id"], workflow.manifest["revision"])
        if identity in workflows:
            raise RuntimeError("Workflow IDs and revisions must be unique.")
        workflows[identity] = workflow
    store = RunStore(directory / "runs.sqlite")
    chat_admission = Admission(2)
    worker = None

    async def execute_job(job, workflow, provider):
        async def keep_lease():
            while True:
                await asyncio.sleep(1)
                if not await finish_thread(store.renew, job):
                    return

        operation = asyncio.create_task(
            workflow.propose(
                provider,
                json.loads(job["history"]),
                json.loads(job["state"]),
                job["input"],
                MAX_TOKENS,
            )
        )
        lease = asyncio.create_task(keep_lease())
        try:
            async with asyncio.timeout(120):
                done, _ = await asyncio.wait(
                    (operation, lease), return_when=asyncio.FIRST_COMPLETED
                )
            if lease in done:
                lease.result()
                return
            await finish_thread(
                store.finish, job, workflow, proposal=operation.result()
            )
        except Problem as exc:
            await finish_thread(store.finish, job, workflow, error=exc)
        except TimeoutError:
            await finish_thread(
                store.finish,
                job,
                workflow,
                error=Problem(
                    504, "turn_deadline", "The turn exceeded its execution deadline."
                ),
            )
        except Exception as exc:
            LOG.error(
                "Workflow execution failure run=%s type=%s",
                job["run_id"],
                type(exc).__name__,
            )
            await finish_thread(
                store.finish,
                job,
                workflow,
                error=Problem(
                    500, "workflow_failed", "The durable turn did not complete."
                ),
            )
        finally:
            for task in (operation, lease):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, lease, return_exceptions=True)

    async def drive_job(job):
        identity = json.loads(job["workflow"])
        workflow = workflows.get((identity["id"], identity["revision"]))
        try:
            if workflow is None or workflow.identity != identity:
                raise Problem(
                    503,
                    "pinned_workflow_not_ready",
                    "The exact admitted workflow revision is unavailable.",
                )
            model = identity["identity"]
            async with control.acquire(model["model"]) as provider:
                if provider.identity != model:
                    raise Problem(
                        503,
                        "pinned_model_not_ready",
                        "The pinned model identity is unavailable.",
                    )
                await execute_job(job, workflow, provider)
        except Problem as exc:
            if exc.code == "model_unavailable":
                # The model entered drain between claiming the job and taking
                # its provider lease. Keep the admitted turn queued unchanged.
                await finish_thread(store.release, job)
            else:
                await finish_thread(store.finish, job, workflow, error=exc)

    async def run_jobs():
        next_cleanup = 0.0
        while True:
            try:
                with request_context():
                    if time.monotonic() >= next_cleanup:
                        await finish_thread(store.purge)
                        next_cleanup = time.monotonic() + 60
                    unavailable = providers.keys() - control.available().keys()
                    job = await finish_thread(store.claim, unavailable)
                    if job:
                        await drive_job(job)
                    else:
                        await asyncio.sleep(0.1)
            except Exception as exc:
                LOG.error("Durable worker iteration failed type=%s", type(exc).__name__)
                await asyncio.sleep(1)

    async def start():
        nonlocal worker
        await control.start()
        worker = asyncio.create_task(run_jobs())

    async def stop():
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await control.stop()

    async def available_models():
        configured = control.available()
        statuses = await asyncio.gather(
            *(candidate.ready() for candidate in configured.values())
        )
        available = control.available()
        return {
            name: candidate.identity
            for (name, candidate), healthy in zip(configured.items(), statuses)
            if healthy and available.get(name) is candidate
        }

    async def ready():
        return (
            worker is not None and not worker.done() and bool(await available_models())
        )

    async def capabilities(_principal, _arguments):
        available = await available_models()
        return {
            "service": "agent",
            "contract_version": "1.0.0",
            "service_revision": os.environ.get("VOXRN_SERVICE_REVISION", "0.1.0"),
            "models": [
                {
                    "identity": identity,
                    "features": {
                        **provider_features(providers[name]),
                        "workflow": any(
                            workflow.identity["identity"] == identity
                            for workflow in workflows.values()
                        ),
                    },
                }
                for name, identity in available.items()
            ],
            "limits": {
                "max_request_bytes": JSON_LIMIT,
                "max_active_turns_per_run": 1,
                "idempotency_ttl_seconds": IDEMPOTENCY_TTL,
                "run_idle_ttl_seconds": RUN_IDLE_TTL,
                "max_output_tokens": MAX_TOKENS,
            },
            "mcp_protocol_version": PROTOCOL,
        }

    async def list_workflows(_principal, _args):
        available = await available_models()
        return {
            "workflows": [
                workflow.identity
                for workflow in workflows.values()
                if available.get(workflow.identity["identity"]["model"])
                == workflow.identity["identity"]
            ]
        }

    async def create_run(principal, args):
        body = {name: args[name] for name in ("workflow_id", "workflow_revision")}
        return await finish_thread(
            store.create,
            principal.owner,
            args["idempotency_key"],
            body,
            workflows,
            available_models=await available_models(),
        )

    async def get_run(principal, args):
        return await finish_thread(store.get, principal.owner, args["run_id"])

    async def submit_turn(principal, args):
        body = {name: args[name] for name in ("expected_revision", "input")}
        return await finish_thread(
            store.submit,
            principal.owner,
            args["run_id"],
            args["idempotency_key"],
            body,
            available_models=await available_models(),
        )

    async def cancel_run(principal, args):
        return await finish_thread(
            store.request_cancel,
            principal.owner,
            args["run_id"],
            args["idempotency_key"],
            {"expected_revision": args["expected_revision"]},
        )

    def validate_chat(options):
        if options["model"] not in providers:
            raise Problem(
                422,
                "unsupported_model",
                "Choose a model from the capabilities catalog.",
            )
        tokens = options.get("max_completion_tokens", MAX_TOKENS)
        if tokens > MAX_TOKENS:
            raise Problem(
                422,
                "unsupported_output_limit",
                "Use the advertised maximum output token limit.",
            )
        return tokens

    def chat_response(stamp, kind, choice):
        chat_id, created, model = stamp
        return {
            "id": chat_id,
            "object": kind,
            "created": created,
            "model": model,
            "choices": [{"index": 0, **choice}],
        }

    async def buffered_chat(options):
        tokens = validate_chat(options)
        async with chat_admission.use(), control.acquire(options["model"]) as provider:
            try:
                async with asyncio.timeout(120):
                    text, reason = await provider.complete(options["messages"], tokens)
            except TimeoutError as exc:
                raise Problem(
                    504,
                    "provider_deadline",
                    "Provider inference exceeded its deadline.",
                    retryable=True,
                ) from exc
        stamp = (new_id(), int(time.time()), options["model"])
        result = validate(
            chat_response(
                stamp,
                "chat.completion",
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": reason,
                },
            ),
            "ChatResponse",
            status=502,
        )
        return result, provider.identity

    async def infer(_principal, options):
        result, _ = await buffered_chat(options)
        return result

    app, mcp = build_service(
        "agent",
        ready,
        {
            "agent_capabilities": capabilities,
            "agent_list_workflows": list_workflows,
            "agent_get_run": get_run,
            "agent_create_run": create_run,
            "agent_submit_turn": submit_turn,
            "agent_cancel_run": cancel_run,
            "agent_infer": infer,
        },
        start=start,
        shutdown=stop,
        model_control=control,
    )

    def command_key(request):
        return validate(request.headers.get("idempotency-key"), IDENTIFIER)

    @app.get("/v1/workflows")
    async def workflows_http():
        principal = CALLER.get()
        principal.require(["agent:read"])
        return await list_workflows(principal, {})

    @app.post("/v1/runs")
    async def create_http(request: Request):
        principal = CALLER.get()
        principal.require(["agent:run"])
        body = await json_body(request, "RunRequest")
        run = await create_run(
            principal, {"idempotency_key": command_key(request), **body}
        )
        return JSONResponse(
            run, status_code=201, headers={"Location": f"/v1/runs/{run['id']}"}
        )

    @app.get("/v1/runs/{run_id}")
    async def get_http(run_id: str):
        principal = CALLER.get()
        principal.require(["agent:read"])
        return await get_run(principal, {"run_id": run_id})

    @app.post("/v1/runs/{run_id}/turns")
    async def submit_http(run_id: str, request: Request):
        principal = CALLER.get()
        principal.require(["agent:run"])
        body = await json_body(request, "TurnRequest")
        result = await submit_turn(
            principal,
            {"run_id": run_id, "idempotency_key": command_key(request), **body},
        )
        return JSONResponse(result, status_code=202)

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_http(run_id: str, request: Request):
        principal = CALLER.get()
        principal.require(["agent:run"])
        body = await json_body(request, "CancelRequest")
        result = await cancel_run(
            principal,
            {"run_id": run_id, "idempotency_key": command_key(request), **body},
        )
        return JSONResponse(result, status_code=202)

    @app.get("/v1/runs/{run_id}/events")
    async def events_http(run_id: str, request: Request):
        principal = CALLER.get()
        principal.require(["agent:read"])
        accept = request.headers.get("accept", "")
        if "text/event-stream" not in [
            part.split(";", 1)[0].strip() for part in accept.split(",")
        ]:
            raise Problem(406, "not_acceptable", "Request text/event-stream.")
        raw = request.headers.get("last-event-id", "0")
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 18:
            raise Problem(
                400, "invalid_cursor", "Use a nonnegative decimal event sequence."
            )
        cursor = int(raw)
        initial = await finish_thread(store.poll, principal.owner, run_id, cursor)

        async def follow():
            current = cursor
            status, last_seq, events = initial
            next_pulse = time.monotonic() + 10
            max_session = time.monotonic() + 120
            while time.monotonic() < max_session:
                for event in events:
                    current = event["seq"]
                    yield event_sse(event, replay=True)
                if status in TERMINAL and current >= last_seq:
                    return
                if not events:
                    if time.monotonic() >= next_pulse:
                        yield ": heartbeat\n\n"
                        next_pulse = time.monotonic() + 10
                    await asyncio.sleep(0.1)
                try:
                    status, last_seq, events = await finish_thread(
                        store.poll, principal.owner, run_id, current
                    )
                except Problem:
                    return

        return StreamingResponse(
            follow(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.post("/v1/chat/completions")
    async def chat_http(request: Request):
        principal = CALLER.get()
        principal.require(["agent:infer"])
        options = await json_body(request, "ChatRequest")

        def provenance(identity):
            return {
                "X-Model-Revision": identity["model_revision"],
                "X-Engine": identity["engine"],
                "X-Engine-Revision": identity["engine_revision"],
            }

        if not options.get("stream"):
            result, identity = await buffered_chat(options)
            return JSONResponse(result, headers=provenance(identity))
        tokens = validate_chat(options)
        # A real streaming request owns the inference slot and receives typed HTTP errors
        # until its first provider packet can be read.
        resources = AsyncExitStack()
        try:
            await resources.enter_async_context(chat_admission.use())
            provider = await resources.enter_async_context(
                control.acquire(options["model"])
            )
            stream = await resources.enter_async_context(
                aclosing(provider.stream(options["messages"], tokens))
            )
            async with asyncio.timeout(90):
                first = await anext(stream)
        except BaseException as exc:
            with CancelScope(shield=True):
                await resources.aclose()
            if isinstance(exc, StopAsyncIteration):
                raise Problem(
                    502, "provider_incomplete", "The provider returned an empty stream."
                ) from exc
            if isinstance(exc, TimeoutError):
                raise Problem(
                    504,
                    "provider_deadline",
                    "Provider inference exceeded its deadline.",
                    retryable=True,
                ) from exc
            raise

        stamp = (new_id(), int(time.time()), options["model"])

        def encode(delta, reason=None):
            value = chat_response(
                stamp,
                "chat.completion.chunk",
                {"delta": delta, "finish_reason": reason},
            )
            validate(value, "ChatChunk", status=502)
            return f"data: {canonical_json(value)}\n\n"

        async def pump():
            yield encode({"role": "assistant"})
            packet = first
            try:
                async with asyncio.timeout(120):
                    while True:
                        text, reason = packet
                        if text:
                            yield encode({"content": text})
                        if reason:
                            yield encode({}, reason)
                            yield "data: [DONE]\n\n"
                            return
                        packet = await anext(stream)
            except (Problem, TimeoutError, StopAsyncIteration):
                return

        return ChatStream(
            pump(), resources=resources, headers=provenance(provider.identity)
        )

    app.mount("/", mcp)
    return app
