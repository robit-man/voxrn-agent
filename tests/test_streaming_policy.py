"""Exercise the production SSE route over a real local TCP listener."""

import asyncio
import json
import socket
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import uvicorn

from service import streaming_policy
from service.server import create_app
from support import HEADERS, TOKEN, setup_env
from test_service import Stub

PAGE = "/v1/chat/completions"
REQUEST = {
    "model": "qwen-local",
    "messages": [{"role": "user", "content": "A complete streaming question."}],
    "stream": True,
}


class SlowProvider(Stub):
    def __init__(self):
        super().__init__()
        self.mode = "normal"
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()
        self.produced = 0

    async def stream(self, messages, tokens):
        try:
            if self.mode == "preflight":
                self.waiting.set()
                await asyncio.Event().wait()
            yield "First.", None
            if self.mode == "flood":
                while True:
                    self.produced += 1
                    yield "x" * 16384, None
            self.waiting.set()
            if self.mode == "indefinite":
                await asyncio.Event().wait()
            await asyncio.sleep(0.12)
            yield " Second.", None
            yield "", "stop"
        finally:
            self.closed.set()


def position_after_stall(lines):
    return next(index for index, value in enumerate(lines) if " Second." in value)


def check_frames(test, rows):
    test.assertEqual(rows[-1], "[DONE]")
    events = [json.loads(row) for row in rows[:-1]]
    test.assertEqual(
        [e["choices"][0] for e in events],
        [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None},
            {"index": 0, "delta": {"content": "First."}, "finish_reason": None},
            {"index": 0, "delta": {"content": " Second."}, "finish_reason": None},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ],
    )


@asynccontextmanager
async def served(app):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = uvicorn.Config(
            app,
            log_config=None,
            log_level="critical",
            access_log=False,
            proxy_headers=False,
            http="h11",
            lifespan="on",
            timeout_graceful_shutdown=1,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("The test listener did not start.")
                    await asyncio.sleep(0.005)
            yield port
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


class NativeStreaming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(setup_env())
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.stub = SlowProvider()

    def app(self):
        return create_app(
            provider=self.stub, directory=self.directory, workflow_paths=[]
        )

    async def test_heartbeat_keeps_application_frames_and_terminal(self):
        self.enterContext(patch.object(streaming_policy, "HEARTBEAT_SECONDS", 0.04))
        async with served(self.app()) as port:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=5, headers=HEADERS
            ) as client:
                async with client.stream(
                    "POST", PAGE, json=REQUEST, headers={"Accept": "text/event-stream"}
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["x-accel-buffering"], "no")
                    lines = [line async for line in response.aiter_lines() if line]
        self.assertIn(": heartbeat", lines)
        self.assertLess(lines.index(": heartbeat"), position_after_stall(lines))
        check_frames(self, [line[6:] for line in lines if line.startswith("data: ")])
        self.assertTrue(self.stub.closed.is_set())

    async def test_abrupt_disconnect_closes_renderer_and_releases_admission(self):
        self.stub.mode = "indefinite"
        async with served(self.app()) as port:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=5, headers=HEADERS
            ) as client:
                async with client.stream(
                    "POST", PAGE, json=REQUEST, headers={"Accept": "text/event-stream"}
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    await asyncio.wait_for(self.stub.waiting.wait(), timeout=1)
                await asyncio.wait_for(self.stub.closed.wait(), timeout=1)
                status = await client.get("/v1/control/models")
                self.assertEqual(status.json()["models"][0]["active_requests"], 0)

    async def test_stalled_tcp_reader_bounds_production_and_releases_admission(self):
        self.stub.mode = "flood"
        self.enterContext(patch.object(streaming_policy, "WRITE_SECONDS", 0.15))
        async with served(self.app()) as port:
            with socket.socket() as slow:
                slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                slow.setblocking(False)
                loop = asyncio.get_running_loop()
                await loop.sock_connect(slow, ("127.0.0.1", port))
                body = json.dumps(REQUEST).encode()
                head = (
                    f"POST {PAGE} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Authorization: Bearer {TOKEN}\r\nAccept: text/event-stream\r\n"
                    f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
                ).encode()
                started_at = time.monotonic()
                await loop.sock_sendall(slow, head + body)
                await asyncio.wait_for(self.stub.closed.wait(), timeout=4)
                self.assertGreaterEqual(time.monotonic() - started_at, 0.12)
                self.assertGreater(self.stub.produced, 2)
                self.assertLess(self.stub.produced, 128)
                # The client has never read from this socket at this point.
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=5, headers=HEADERS
                ) as client:
                    status = await client.get("/v1/control/models")
                    self.assertEqual(status.json()["models"][0]["active_requests"], 0)

    async def test_disconnect_during_first_packet_wait_closes_provider(self):
        self.stub.mode = "preflight"
        async with served(self.app()) as port:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=5, headers=HEADERS
            ) as client:
                pending = asyncio.create_task(client.post(PAGE, json=REQUEST))
                await asyncio.wait_for(self.stub.waiting.wait(), timeout=1)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                await asyncio.wait_for(self.stub.closed.wait(), timeout=1)
                status = await client.get("/v1/control/models")
                self.assertEqual(status.json()["models"][0]["active_requests"], 0)
