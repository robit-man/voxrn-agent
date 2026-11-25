"""Bound in-flight SSE frames and disconnect stalled readers."""

import asyncio
from contextlib import aclosing

from anyio import CancelScope, create_task_group
from starlette.responses import StreamingResponse

from .telemetry import mark_failure
from .wire import Problem

HEARTBEAT_SECONDS = 10
WRITE_SECONDS = 15
HEARTBEAT = ": heartbeat\n\n"


async def first_packet(request, stream):
    # The HTTP request body has already been consumed. Observe disconnects
    # during provider preflight without starting the response or changing its errors.
    with CancelScope() as waiting:

        async def monitor():
            while (await request.receive())["type"] != "http.disconnect":
                pass
            waiting.cancel()

        watcher = asyncio.create_task(monitor())
        try:
            return await anext(stream)
        finally:
            watcher.cancel()
            with CancelScope(shield=True):
                await asyncio.gather(watcher, return_exceptions=True)
    raise Problem(
        499, "client_disconnected", "The client disconnected during preflight."
    )


async def with_heartbeats(source):
    requests = asyncio.Queue(maxsize=1)

    async def produce():
        async with aclosing(source):
            while True:
                delivery = await requests.get()
                try:
                    frame = await anext(source)
                except StopAsyncIteration:
                    delivery.set_result(None)
                    return
                delivery.set_result(frame)

    producer = asyncio.create_task(produce())
    try:
        while True:
            delivery = asyncio.get_running_loop().create_future()
            requests.put_nowait(delivery)
            while True:
                done, _ = await asyncio.wait(
                    (delivery, producer),
                    timeout=HEARTBEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break
                yield HEARTBEAT
            if producer in done:
                await producer
            frame = delivery.result()
            if frame is None:
                return
            yield frame
    finally:
        with CancelScope(shield=True):
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)


class BoundedSSE(StreamingResponse):
    def __init__(self, frames, *, headers=None, resources=None, keepalive=True):
        super().__init__(
            with_heartbeats(frames) if keepalive else frames,
            media_type="text/event-stream",
            headers={**(headers or {}), "X-Accel-Buffering": "no"},
        )
        self.resources = resources

    async def __call__(self, scope, receive, send):
        try:
            # Read disconnect notifications even when the publisher has no data
            # and the server implements ASGI 2.4's send-error semantics.
            async with create_task_group() as tasks:

                async def publish():
                    try:
                        await self.stream_response(send)
                    except OSError:
                        pass
                    finally:
                        tasks.cancel_scope.cancel()

                tasks.start_soon(publish)
                await self.listen_for_disconnect(receive)
                tasks.cancel_scope.cancel()
        finally:
            with CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    if self.resources is not None:
                        await self.resources.aclose()


class SSEWriteDeadline:
    """Apply the deadline to the real ASGI send outside response middleware."""

    def __init__(self, app, paths):
        self.app = app
        self.paths = paths

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not any(
            pattern.fullmatch(path) for pattern in self.paths
        ):
            return await self.app(scope, receive, send)

        streaming = False

        async def bounded_send(message):
            nonlocal streaming
            if message["type"] == "http.response.start":
                media_type = dict(message.get("headers", ())).get(b"content-type", b"")
                streaming = media_type.split(b";", 1)[0] == b"text/event-stream"
            if not streaming:
                return await send(message)
            try:
                async with asyncio.timeout(WRITE_SECONDS):
                    await send(message)
            except TimeoutError as exc:
                mark_failure()
                raise ConnectionError(
                    "The SSE downstream write deadline expired."
                ) from exc

        await self.app(scope, receive, bounded_send)
