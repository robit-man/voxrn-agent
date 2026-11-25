import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import rfc8785

from service.provider import Ollama
from service.server import create_app
from service.wire import CONTRACTS, validate
from service.workflows import BINDING_SET, digest
from support import connect, mcp, setup_env


class Stub:
    identity = {
        "model": "qwen-local",
        "model_revision": "synthetic",
        "engine": "ollama",
        "engine_revision": "synthetic",
    }

    def __init__(self):
        self.started = threading.Event()

    async def start(self):
        pass

    async def close(self):
        pass

    async def ready(self):
        return True

    async def complete(self, messages, tokens, schema=None):
        if not schema:
            return "Finished", "stop"
        if messages[-1]["content"] == "Wait until cancelled":
            self.started.set()
            await asyncio.sleep(20)
        return json.dumps(
            {"action": "record", "day": "Friday", "reply": "candidate"}
        ), "stop"

    async def stream(self, messages, tokens):
        yield "Finished", None
        yield "", "stop"


def provision(path, provider):
    path.mkdir()
    example = json.loads((CONTRACTS / "examples.json").read_text())["workflow_bundle"]
    source = b"Record one confirmed weekday."
    instructions = b"Return a decision."
    example.update(
        {
            "entrypoint": "record_intake",
            "identity": provider.identity,
            "source_sha256": digest(source),
            "executable_sha256": digest(instructions),
            "binding_set_sha256": digest(rfc8785.dumps(BINDING_SET)),
        }
    )
    (path / "manifest.json").write_text(json.dumps(example))
    (path / "source.txt").write_bytes(source)
    (path / "instructions.txt").write_bytes(instructions)


class AgentConformance(unittest.TestCase):
    def setUp(self):
        self.enterContext(setup_env())
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.stub = Stub()
        bundle = directory / "bundle"
        provision(bundle, self.stub)
        self.client = self.enterContext(
            connect(
                create_app(
                    provider=self.stub, directory=directory, workflow_paths=[bundle]
                )
            )
        )

    def start_run(self):
        result = self.client.post(
            "/v1/runs",
            headers={"Idempotency-Key": "start"},
            json={"workflow_id": "intake", "workflow_revision": "1.0.0"},
        )
        self.assertEqual(result.status_code, 201, result.text)
        validate(result.json(), "Run")
        return result.json()["id"]

    def submit(self, run, input_text):
        return self.client.post(
            f"/v1/runs/{run}/turns",
            headers={"Idempotency-Key": "turn"},
            json={"input": input_text, "expected_revision": 0},
        )

    def events(self, run, cursor):
        response = self.client.get(
            f"/v1/runs/{run}/events",
            headers={"Accept": "text/event-stream", "Last-Event-ID": str(cursor)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]

    def final(self, run):
        for _ in range(100):
            value = self.client.get(f"/v1/runs/{run}").json()
            if value["status"] in ("completed", "failed", "cancelled"):
                return value
            time.sleep(0.025)
        self.fail("Run did not reach a terminal state")

    def test_durable_run_idempotency_replay_and_mcp_readback(self):
        run = self.start_run()
        first = self.submit(run, "Please record Friday.")
        self.assertEqual(first.status_code, 202, first.text)
        result = self.final(run)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"], {"day": "Friday"})
        self.assertEqual(first.json(), self.submit(run, "Please record Friday.").json())
        self.assertEqual(self.submit(run, "Please record Saturday.").status_code, 409)
        events = self.events(run, cursor=2)
        self.assertEqual(
            [item["type"] for item in events],
            ["action.receipt", "state.committed", "turn.completed", "run.completed"],
        )
        self.assertEqual(events[0]["status"], "recorded")
        readback = mcp(
            self.client, "tools/call", name="agent_get_run", arguments={"run_id": run}
        )
        self.assertEqual(readback["structuredContent"]["data"], result)

    def test_hard_negative_preserves_empty_state(self):
        run = self.start_run()
        self.assertEqual(
            self.submit(run, "Please do not record Friday.").status_code, 202
        )
        result = self.final(run)
        self.assertEqual((result["status"], result["state"]), ("failed", {}))
        events = self.events(run, cursor=0)
        self.assertNotIn("action.receipt", [event["type"] for event in events])

    def test_cancel_fences_a_running_turn(self):
        run = self.start_run()
        self.assertEqual(self.submit(run, "Wait until cancelled").status_code, 202)
        self.assertTrue(self.stub.started.wait(3))
        response = self.client.post(
            f"/v1/runs/{run}/cancel",
            headers={"Idempotency-Key": "cancel"},
            json={"expected_revision": 1},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertEqual(self.final(run)["status"], "cancelled")
        self.assertEqual(
            [e["type"] for e in self.events(run, cursor=2)],
            ["run.cancelling", "turn.failed", "run.cancelled"],
        )

    def test_chat_and_mcp_infer(self):
        args = {
            "model": "qwen-local",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        buffered = self.client.post("/v1/chat/completions", json=args)
        validate(buffered.json(), "ChatResponse")
        stream = self.client.post("/v1/chat/completions", json={**args, "stream": True})
        self.assertIn("data: [DONE]", stream.text)
        self.assertIn("Finished", stream.text)
        result = mcp(self.client, "tools/call", name="agent_infer", arguments=args)
        self.assertFalse(result["isError"])


class ProviderConformance(unittest.IsolatedAsyncioTestCase):
    async def test_exact_serialized_buffered_and_streaming_messages(self):
        config = {
            "VOXRN_AGENT_OLLAMA_URL": "http://offline-test",
            "VOXRN_AGENT_MODEL_TAG": "test",
            "VOXRN_AGENT_MODEL_DIGEST": "sha256:test",
            "VOXRN_AGENT_ENGINE_REVISION": "1.2.3",
            "VOXRN_AGENT_CONTEXT_TOKENS": "65536",
        }
        with patch.dict("os.environ", config):
            provider = Ollama()
        bodies = []

        def respond(request):
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "1.2.3"})
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200, json={"models": [{"name": "test", "digest": "sha256:test"}]}
                )
            body = json.loads(request.content)
            bodies.append(body)
            packet = {
                "done": True,
                "done_reason": "stop",
                "message": {"content": "Reply"},
            }
            return (
                httpx.Response(200, text=json.dumps(packet) + "\n")
                if body["stream"]
                else httpx.Response(200, json=packet)
            )

        provider.client = httpx.AsyncClient(
            base_url=provider.url, transport=httpx.MockTransport(respond)
        )
        self.addAsyncCleanup(provider.close)
        messages = [{"role": "system", "content": " Preserve source exactly.  "}]
        messages += [
            {"role": "user", "content": f" History turn {i}\n"} for i in range(45)
        ]
        messages += [
            {
                "role": "user",
                "content": "x" * 23000 + '{"selectedUnit":"last","binding":"sealed"}',
            }
        ]
        self.assertEqual(await provider.complete(messages, 16), ("Reply", "stop"))
        self.assertEqual(
            [event async for event in provider.stream(messages, 16)],
            [("Reply", None), ("", "stop")],
        )
        self.assertEqual([body["messages"] for body in bodies], [messages, messages])
        self.assertEqual([body["stream"] for body in bodies], [False, True])


class RecoveryConformance(unittest.TestCase):
    def test_restart_reclaims_and_fences_an_expired_job(self):
        from service.state import RunStore
        from service.workflows import load_bundle

        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        provider = Stub()
        bundle = directory / "bundle"
        provision(bundle, provider)
        workflow = load_bundle(bundle, provider.identity)
        db = directory / "runs.sqlite"
        owner = "offline-subject"
        store = RunStore(db)
        run = store.create(
            owner,
            "start",
            {"workflow_id": "intake", "workflow_revision": "1.0.0"},
            {("intake", "1.0.0"): workflow},
        )
        self.assertEqual(
            store.submit(
                owner, run["id"], "turn", {"input": "Friday", "expected_revision": 0}
            )["revision"],
            1,
        )
        old_job = store.claim()
        self.assertIsNotNone(old_job)
        with store.connection(write=True) as connection:
            connection.execute(
                "UPDATE jobs SET lease_expires=0 WHERE run_id=?", (run["id"],)
            )
        recovered = RunStore(db)
        new_job = recovered.claim()
        self.assertEqual(
            (new_job["turn_id"], new_job["input"]), (old_job["turn_id"], "Friday")
        )
        self.assertFalse(store.finish(old_job, workflow, proposal={"day": "Friday"}))
        self.assertTrue(recovered.finish(new_job, workflow, proposal={"day": "Friday"}))
        self.assertEqual(recovered.get(owner, run["id"])["state"], {"day": "Friday"})
        with recovered.connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1
            )
