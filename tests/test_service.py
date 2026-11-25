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

from service.provider import Ollama, OllamaProfile
from service.provider_registry import load_providers
from service.server import create_app
from service.wire import CONTRACTS, Problem, validate
from service.workflows import BINDING_SET, CLARIFICATION, DECISION, digest
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
        self.decision_schemas = []

    async def start(self):
        pass

    async def close(self):
        pass

    async def ready(self):
        return True

    async def complete(self, messages, tokens, schema=None):
        if not schema:
            return "Finished", "stop"
        self.decision_schemas.append(schema)
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

    def start_run(self, key="start"):
        result = self.client.post(
            "/v1/runs",
            headers={"Idempotency-Key": key},
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
        self.assertEqual(self.stub.decision_schemas, [DECISION])
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
        self.assertEqual(self.stub.decision_schemas, [CLARIFICATION])
        events = self.events(run, cursor=0)
        self.assertNotIn("action.receipt", [event["type"] for event in events])
        self.assertEqual(events[-1]["problem"]["code"], "source_evidence_missing")

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

    def test_disabled_model_retains_queue_until_reenabled(self):
        active = self.start_run("active")
        queued = self.start_run("queued")
        unsubmitted = self.start_run("unsubmitted")
        self.assertEqual(self.submit(active, "Wait until cancelled").status_code, 202)
        self.assertTrue(self.stub.started.wait(3))
        prompt = "Please record Friday."
        accepted = self.submit(queued, prompt)
        self.assertEqual(accepted.status_code, 202)

        control = "/v1/control/models/qwen-local"
        disabled = self.client.put(control, json={"enabled": False})
        self.assertEqual(disabled.status_code, 202, disabled.text)
        self.assertEqual(disabled.json()["state"], "draining")
        self.assertEqual(self.submit(queued, prompt).json(), accepted.json())
        rejected = self.submit(unsubmitted, prompt)
        self.assertEqual(rejected.status_code, 503, rejected.text)
        self.assertEqual(rejected.json()["code"], "pinned_model_not_ready")

        cancelled = self.client.post(
            f"/v1/runs/{active}/cancel",
            headers={"Idempotency-Key": "cancel"},
            json={"expected_revision": 1},
        )
        self.assertEqual(cancelled.status_code, 202)
        self.assertEqual(self.final(active)["status"], "cancelled")
        for _ in range(100):
            entry = self.client.get("/v1/control/models").json()["models"][0]
            if entry["state"] == "disabled":
                break
            time.sleep(0.025)
        self.assertEqual(entry["state"], "disabled", entry)
        time.sleep(
            0.2
        )  # Allow two worker polling intervals against the disabled model.
        snapshot = self.client.get(f"/v1/runs/{queued}").json()
        self.assertEqual((snapshot["status"], snapshot["revision"]), ("running", 1))
        self.assertEqual(self.stub.decision_schemas, [CLARIFICATION])

        enabled = self.client.put(control, json={"enabled": True})
        self.assertEqual(enabled.status_code, 202, enabled.text)
        result = self.final(queued)
        self.assertEqual(
            (result["status"], result["state"]), ("completed", {"day": "Friday"})
        )
        self.assertEqual(self.stub.decision_schemas, [CLARIFICATION, DECISION])
        self.assertEqual(
            [event["type"] for event in self.events(queued, cursor=2)],
            ["action.receipt", "state.committed", "turn.completed", "run.completed"],
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
    async def test_managed_release_requires_receipt_for_the_exact_runner(self):
        provider = Ollama(
            OllamaProfile(
                model="local",
                tag="test:1",
                digest="d" * 64,
                engine_revision="1.2.3",
                url="http://offline-test",
                managed=True,
            )
        )
        unloads = []

        def respond(request):
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "1.2.3"})
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200, json={"models": [{"name": "test:1", "digest": "d" * 64}]}
                )
            if request.url.path == "/api/chat":
                return httpx.Response(
                    200,
                    json={
                        "done": True,
                        "done_reason": "stop",
                        "message": {"content": "Reply"},
                    },
                )
            self.assertEqual(request.url.path, "/api/generate")
            unloads.append(json.loads(request.content))
            tag = "another:1" if len(unloads) == 1 else "test:1"
            return httpx.Response(
                200, json={"model": tag, "done": True, "done_reason": "unload"}
            )

        async with httpx.AsyncClient(
            base_url=provider.url, transport=httpx.MockTransport(respond)
        ) as client:
            provider.client = client
            self.assertEqual(
                await provider.complete([{"role": "user", "content": "Hello"}], 16),
                ("Reply", "stop"),
            )
            with self.assertRaises(Problem) as failure:
                await provider.close()
            self.assertEqual(failure.exception.code, "provider_release_unverified")
            self.assertFalse(client.is_closed)
            await provider.close()
            self.assertTrue(client.is_closed)
        self.assertEqual(
            unloads, [{"model": "test:1", "keep_alive": 0, "stream": False}] * 2
        )

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


class RegistryConformance(unittest.TestCase):
    def test_decision_schema_discriminates_clarification_and_recording(self):
        reply = {"reply": "Which day would you like to record?"}
        for value in (
            {"action": "clarify", "day": ""},
            {"action": "record", "day": "Friday"},
        ):
            validate({**reply, **value}, DECISION)
        for value in (
            {"action": "clarify", "day": "Friday"},
            {"action": "record", "day": ""},
            {"action": "record", "day": "today"},
        ):
            with self.assertRaises(Problem):
                validate({**reply, **value}, DECISION)

    def test_file_validates_revisions_and_cannot_alias_a_managed_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            profile = {
                "model": "qwen3.5-4b",
                "tag": "qwen3.5:4b",
                "digest": "d" * 64,
                "engine": "ollama",
                "engine_revision": "0.40.18",
                "url": "http://127.0.0.1:18435",
                "managed": True,
                "think": False,
            }
            with patch.dict("os.environ", {"VOXRN_AGENT_MODELS_FILE": str(path)}):
                path.write_text(json.dumps({"models": [profile]}))
                providers = load_providers()
                self.assertEqual(
                    providers["qwen3.5-4b"].identity["model_revision"], "d" * 64
                )
                self.assertFalse(providers["qwen3.5-4b"].profile.thinking)
                path.write_text(
                    json.dumps({"models": [profile, {**profile, "model": "other"}]})
                )
                with self.assertRaisesRegex(ValueError, "single profile"):
                    load_providers()
                path.write_text(
                    json.dumps({"models": [{**profile, "digest": "latest"}]})
                )
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    load_providers()

    def test_control_changes_admission_without_retargeting_workflow_or_replay(self):
        class Candidate(Stub):
            def __init__(self, model, revision, healthy=True):
                super().__init__()
                self.identity = {
                    **self.identity,
                    "model": model,
                    "model_revision": revision,
                }
                self.healthy = healthy

            async def ready(self):
                return self.healthy

            async def stream(self, messages, tokens):
                yield self.identity["model"], None
                yield "", "stop"

        self.enterContext(setup_env())
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        legacy = Candidate("qwen-local", "legacy")
        installed = Candidate("qwen3.5-4b", "new-digest")
        missing = Candidate("catalog-mismatch", "unavailable", healthy=False)
        bundle = directory / "bundle"
        provision(bundle, installed)
        providers = {p.identity["model"]: p for p in (legacy, installed, missing)}
        with connect(
            create_app(
                providers=providers, directory=directory, workflow_paths=[bundle]
            )
        ) as client:
            models = client.get("/v1/capabilities").json()["models"]
            self.assertEqual(
                [
                    (item["identity"]["model"], item["features"]["workflow"])
                    for item in models
                ],
                [("qwen-local", False), ("qwen3.5-4b", True)],
            )
            chat = {
                "model": "qwen3.5-4b",
                "messages": [{"role": "user", "content": "Hello"}],
            }
            for stream in (False, True):
                response = client.post(
                    "/v1/chat/completions", json={**chat, "stream": stream}
                )
                self.assertEqual(response.headers["X-Model-Revision"], "new-digest")
            args = {"workflow_id": "intake", "workflow_revision": "1.0.0"}
            first = client.post(
                "/v1/runs", json=args, headers={"Idempotency-Key": "create"}
            )
            self.assertEqual(first.status_code, 201, first.text)
            run = first.json()
            self.assertEqual(run["workflow"]["identity"], installed.identity)
            result = client.put(
                "/v1/control/models/qwen3.5-4b", json={"enabled": False}
            )
            self.assertEqual(result.status_code, 202, result.text)
            self.assertEqual(client.get("/v1/workflows").json(), {"workflows": []})
            replay = client.post(
                "/v1/runs", json=args, headers={"Idempotency-Key": "create"}
            )
            self.assertEqual(replay.json(), run)
            blocked = client.post(
                f"/v1/runs/{run['id']}/turns",
                headers={"Idempotency-Key": "turn"},
                json={"expected_revision": 0, "input": "Please record Friday."},
            )
            self.assertEqual(
                (blocked.status_code, blocked.json()["code"]),
                (503, "pinned_model_not_ready"),
            )
            self.assertEqual(client.get(f"/v1/runs/{run['id']}").json(), run)
            self.assertEqual(
                client.post("/v1/chat/completions", json=chat).status_code, 503
            )
            other = client.post(
                "/v1/chat/completions", json={**chat, "model": "qwen-local"}
            )
            self.assertEqual(other.headers["X-Model-Revision"], "legacy")


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
