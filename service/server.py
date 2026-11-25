"""Standalone agent inference, workflow execution, and MCP entry point."""

import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from pathlib import Path

from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from .provider import Ollama
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


def create_app(*, provider=None, directory=None, workflow_paths=None):
    if provider is None:
        provider = Ollama()
    directory = Path(directory or os.environ.get("VOXRN_DATA_DIR", ".data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if workflow_paths is None:
        configured = os.environ.get("VOXRN_AGENT_WORKFLOW_DIR")
        workflow_paths = [configured] if configured else []
    workflows = {}
    for path in workflow_paths:
        workflow = load_bundle(path, provider.identity)
        identity = (workflow.manifest["id"], workflow.manifest["revision"])
        if identity in workflows:
            raise RuntimeError("Workflow IDs and revisions must be unique.")
        workflows[identity] = workflow
    store = RunStore(directory / "runs.sqlite")
    chat_admission = Admission(2)
    worker = None

    async def drive_job(job):
        async def keep_lease():
            while True:
                await asyncio.sleep(1)
                if not await finish_thread(store.renew, job):
                    return

        identity = json.loads(job["workflow"])
        workflow = workflows.get((identity["id"], identity["revision"]))
        if workflow is None or workflow.identity != identity:
            await finish_thread(
                store.finish,
                job,
                None,
                error=Problem(
                    503,
                    "pinned_workflow_not_ready",
                    "The exact admitted workflow revision is unavailable.",
                ),
            )
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

    async def run_jobs():
        next_cleanup = 0.0
        while True:
            try:
                with request_context():
                    if time.monotonic() >= next_cleanup:
                        await finish_thread(store.purge)
                        next_cleanup = time.monotonic() + 60
                    job = await finish_thread(store.claim)
                    if job:
                        await drive_job(job)
                    else:
                        await asyncio.sleep(0.1)
            except Exception as exc:
                LOG.error("Durable worker iteration failed type=%s", type(exc).__name__)
                await asyncio.sleep(1)

    async def start():
        nonlocal worker
        await provider.start()
        worker = asyncio.create_task(run_jobs())

    async def stop():
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await provider.close()

    async def ready():
        return worker is not None and not worker.done() and await provider.ready()

    async def capabilities(_principal, _arguments):
        return {
            "service": "agent",
            "contract_version": "1.0.0",
            "service_revision": os.environ.get("VOXRN_SERVICE_REVISION", "0.1.0"),
            "models": [
                {
                    "identity": provider.identity,
                    "features": {
                        "chat": True,
                        "chat_stream": True,
                        "workflow": bool(workflows),
                        "structured_outputs": True,
                    },
                }
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
        return {"workflows": [workflow.identity for workflow in workflows.values()]}

    async def create_run(principal, args):
        body = {name: args[name] for name in ("workflow_id", "workflow_revision")}
        return await finish_thread(
            store.create, principal.owner, args["idempotency_key"], body, workflows
        )

    async def get_run(principal, args):
        return await finish_thread(store.get, principal.owner, args["run_id"])

    async def submit_turn(principal, args):
        body = {name: args[name] for name in ("expected_revision", "input")}
        return await finish_thread(
            store.submit, principal.owner, args["run_id"], args["idempotency_key"], body
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
        if options["model"] != provider.identity["model"]:
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

    async def infer(_principal, options):
        tokens = validate_chat(options)
        async with chat_admission.use():
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
        return validate(
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
        identity = provider.identity
        headers = {
            "X-Model-Revision": identity["model_revision"],
            "X-Engine": identity["engine"],
            "X-Engine-Revision": identity["engine_revision"],
        }
        if not options.get("stream"):
            return JSONResponse(await infer(principal, options), headers=headers)
        tokens = validate_chat(options)
        # A real streaming request owns the inference slot and receives typed HTTP errors
        # until its first provider packet can be read.
        context = chat_admission.use()
        await context.__aenter__()
        stream = provider.stream(options["messages"], tokens)
        try:
            async with asyncio.timeout(90):
                first = await anext(stream)
        except BaseException as exc:
            await stream.aclose()
            await context.__aexit__(None, None, None)
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
            finally:
                await stream.aclose()
                await context.__aexit__(None, None, None)

        return StreamingResponse(
            pump(), media_type="text/event-stream", headers=headers
        )

    app.mount("/", mcp)
    return app
