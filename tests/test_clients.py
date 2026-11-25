"""Conformance of the checked-in Python and TypeScript SDKs without model inference."""

import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "contracts" / "openapi.json").read_text())
SERVICE = CONTRACT["info"]["title"].removeprefix("VoxRN ").lower()
CLIENT_NAME = f"Voxrn{SERVICE.capitalize()}Client"
PYTHON = ROOT / "clients" / "python"
sys.path.insert(0, str(PYTHON))
try:
    sdk = importlib.import_module(f"voxrn_{SERVICE}_sdk")
finally:
    sys.path.remove(str(PYTHON))

PROBLEM = {
    "type": "urn:voxrn:problem:synthetic",
    "title": "Synthetic",
    "status": 503,
    "detail": "Model temporarily unavailable.",
    "code": "synthetic",
    "request_id": "request-id",
    "retryable": True,
    "new_detail": {"capacity": 2},
}
TICKET_VALUE = "a" * 43
TICKET_SUBPROTOCOL = f"voxrn.asr.ticket.{TICKET_VALUE}"


class ByteStream(httpx.SyncByteStream):
    def __init__(self):
        self.read = False
        self.closed = False

    def __iter__(self):
        self.read = True
        yield b"data: one\n\n"
        yield b"data: two\n\n"

    def close(self):
        self.closed = True


class PythonClientConformance(unittest.TestCase):
    def client(self, transport, **kwargs):
        return self.enterContext(
            getattr(sdk, CLIENT_NAME)(
                "https://example.test/mounted/",
                transport=httpx.MockTransport(transport),
                **kwargs,
            )
        )

    def test_public_and_bearer_routes_encoded_query_and_additive_json(self):
        requests = []
        calls = []

        def token():
            calls.append(True)
            return f"token-{len(calls)}"

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"new_capability": True})

        client = self.client(transport, token=token)
        self.assertEqual(client.liveness()["new_capability"], True)
        self.assertNotIn("Authorization", requests[0].headers)
        self.assertEqual(calls, [])
        client.capabilities()
        client.get_runtime_telemetry(
            window_seconds=60, model="a / b?é", endpoint="/v1/test"
        )
        self.assertEqual(requests[1].headers["Authorization"], "Bearer token-1")
        self.assertEqual(requests[2].headers["Authorization"], "Bearer token-2")
        self.assertEqual(requests[2].url.path, "/mounted/v1/control/runtime")
        self.assertEqual(
            dict(requests[2].url.params),
            {"window_seconds": "60", "model": "a / b?é", "endpoint": "/v1/test"},
        )

    def test_typed_problem_and_non_json_body(self):
        responses = [
            httpx.Response(
                503, json=PROBLEM, headers={"Content-Type": "application/problem+json"}
            ),
            httpx.Response(
                502, text="upstream unavailable", headers={"X-Request-ID": "fallback"}
            ),
            httpx.Response(302, headers={"Location": "https://other.test/credential"}),
        ]
        client = self.client(lambda _: responses.pop(0), token="token")
        with self.assertRaises(sdk.ApiError) as result:
            client.capabilities()
        error = result.exception
        self.assertEqual((error.status, error.request_id), (503, "request-id"))
        self.assertEqual(error.problem, PROBLEM)
        with self.assertRaises(sdk.ApiError) as result:
            client.capabilities()
        self.assertEqual(result.exception.body, "upstream unavailable")
        self.assertEqual(result.exception.request_id, "fallback")
        self.assertIsNone(result.exception.problem)
        with self.assertRaises(sdk.ApiError) as result:
            client.capabilities()
        self.assertEqual(result.exception.status, 302)
        self.assertFalse(responses)

    def test_model_path_and_json_encoding(self):
        def transport(request):
            self.assertEqual(
                request.url.raw_path, b"/mounted/v1/control/models/x%2Fy%20%25%3F%21"
            )
            self.assertEqual(request.method, "PUT")
            self.assertEqual(request.headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(request.content), {"enabled": False})
            return httpx.Response(
                202, json={"source_addition": []}, headers={"X-Request-ID": "model"}
            )

        client = self.client(transport)
        response = client.set_installed_model_enabled_raw(
            model_id="x/y %?!", body={"enabled": False}
        )
        self.assertEqual(
            (response.headers["X-Request-ID"], response.json()),
            ("model", {"source_addition": []}),
        )
        with self.assertRaises(ValueError):
            client.set_installed_model_enabled(model_id="..", body={"enabled": True})

    @unittest.skipUnless(SERVICE == "asr", "ASR media transport")
    def test_asr_raw_and_multipart_audio(self):
        requests = []

        def transport(request):
            requests.append(request)
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(201, json={"id": "uploaded"})

        client = self.client(transport)
        self.assertEqual(
            client.upload_media(body=b"RIFF", content_type="audio/wav")["id"],
            "uploaded",
        )
        self.assertEqual(requests[0].headers["Content-Type"], "audio/wav")
        self.assertEqual(requests[0].content, b"RIFF")
        client.transcribe_file(
            body={
                "model": "example",
                "diarization": False,
                "file": sdk.UploadPart(b"RIFF", "example.wav", "audio/wav"),
            }
        )
        self.assertTrue(
            requests[1]
            .headers["Content-Type"]
            .startswith("multipart/form-data; boundary=")
        )
        self.assertIn(
            b'filename="example.wav"\r\nContent-Type: audio/wav', requests[1].content
        )
        self.assertIn(b'name="diarization"\r\n\r\nfalse', requests[1].content)
        self.assertIn(b"\r\nRIFF\r\n", requests[1].content)
        self.assertIsNone(client.delete_media(media_id="uploaded"))

    @unittest.skipUnless(SERVICE == "asr", "ASR browser ticket")
    def test_asr_ticket_origin_is_bound_on_the_authenticated_http_request(self):
        ticket = {
            "ticket": TICKET_VALUE,
            "subprotocol": TICKET_SUBPROTOCOL,
            "expires_in": 30,
        }

        def transport(request):
            self.assertEqual(
                request.url.path, "/mounted/v1/audio/transcriptions/live/tickets"
            )
            self.assertEqual(request.headers["Origin"], "https://dashboard.test")
            self.assertEqual(request.headers["Authorization"], "Bearer token")
            self.assertEqual(request.content, b"")
            return httpx.Response(201, json=ticket)

        client = self.client(transport, token="token")
        self.assertEqual(
            client.create_live_transcription_ticket(origin="https://dashboard.test"),
            ticket,
        )

    @unittest.skipUnless(SERVICE in {"tts", "agent"}, "SSE endpoints")
    def test_lazy_streaming_preserves_raw_chunks_and_closes(self):
        source = ByteStream()

        def transport(request):
            self.assertEqual(request.headers["Accept"], "text/event-stream")
            if SERVICE == "agent":
                self.assertTrue(json.loads(request.content)["stream"])
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=source
            )

        client = self.client(transport)
        if SERVICE == "agent":
            body = {
                "model": "model",
                "messages": [{"role": "user", "content": "example"}],
            }
            stream = client.chat_completion_stream(body=body)
        else:
            body = {"model": "model", "voice": "voice", "input": "example"}
            stream = client.speak_stream(body=body)
        with stream as response:
            self.assertFalse(source.read)
            self.assertEqual(next(response.iter_bytes()), b"data: one\n\n")
            self.assertNotIn("stream", body)
        self.assertTrue(source.closed)

    @unittest.skipUnless(SERVICE == "tts", "TTS audio transport")
    def test_tts_binary_response_and_provenance(self):
        def transport(request):
            self.assertEqual(request.headers["Accept"], "audio/wav")
            return httpx.Response(
                200,
                content=b"RIFF",
                headers={"X-Model-Revision": "revision", "Content-Type": "audio/wav"},
            )

        client = self.client(transport)
        body = {"model": "model", "voice": "voice", "input": "example"}
        self.assertEqual(client.speak(body=body), b"RIFF")
        self.assertEqual(
            client.speak_raw(body=body).headers["X-Model-Revision"], "revision"
        )

    @unittest.skipUnless(SERVICE == "agent", "Agent cursor and idempotency")
    def test_agent_idempotency_key_and_resume_cursor(self):
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(202, json={})

        client = self.client(transport)
        client.submit_turn(
            run_id="run",
            idempotency_key="key",
            body={"input": "preserve me", "expected_revision": 0},
        )
        self.assertEqual(requests[0].headers["Idempotency-Key"], "key")
        self.assertEqual(json.loads(requests[0].content)["input"], "preserve me")
        with client.stream_run(run_id="run", last_event_id="4") as response:
            self.assertEqual(response.request.headers["Last-Event-ID"], "4")


JS_CHECK = r"""
const assert = require("node:assert/strict")
const [path, service, clientName, problemJson] = process.argv.slice(1)
const {ApiError, [clientName]: Client} = require(path)
const problem = JSON.parse(problemJson)
const ticketValue = "a".repeat(43)
const ticketSubprotocol = "voxrn.asr.ticket." + ticketValue
const calls = []
let bearerTime = 0
const signal = new AbortController().signal
const client = new Client("https://example.test/mounted/", {
  token: async () => `token-${++bearerTime}`,
  fetch: async (url, init) => {
    const request = new Request(url, init)
    calls.push(request)
    if (request.headers.get("accept") === "text/event-stream") {
      return new Response("data: event\n\n", {headers: {"Content-Type": "text/event-stream"}})
    }
    if (request.url.endsWith("/error")) {
      return new Response(JSON.stringify(problem), {status: 503, headers: {"Content-Type": "application/problem+json"}})
    }
    if (request.url.endsWith("/tickets")) {
      return Response.json({ticket: ticketValue, expires_in: 30, subprotocol: ticketSubprotocol}, {status: 201})
    }
    if (request.headers.get("accept") === "audio/wav") {
      return new Response(new TextEncoder().encode("RIFF"), {headers: {"Content-Type": "audio/wav"}})
    }
    return new Response(JSON.stringify({futureField: 7}), {headers: {"Content-Type": "application/json"}})
  },
})

async function main() {
  assert.equal((await client.liveness()).futureField, 7)
  assert.equal(calls.at(-1).headers.get("authorization"), null)
  await client.getRuntimeTelemetry({windowSeconds: 60, model: "a & é", endpoint: "/v1/example", signal})
  const query = new URL(calls.at(-1).url)
  assert.equal(query.pathname, "/mounted/v1/control/runtime")
  assert.equal(query.searchParams.get("model"), "a & é")
  assert.equal(query.searchParams.get("window_seconds"), "60")
  assert.equal(calls.at(-1).headers.get("authorization"), "Bearer token-1")
  assert.equal(calls.at(-1).redirect, "error")
  assert.equal(calls.at(-1).credentials, "omit")
  await client.setInstalledModelEnabled({modelId: "a/b %?!", body: {enabled: false}})
  assert.match(calls.at(-1).url, /\/models\/a%2Fb%20%25%3F%21$/)
  assert.deepEqual(await calls.at(-1).json(), {enabled: false})
  await assert.rejects(client.setInstalledModelEnabled({modelId: "error", body: {enabled: false}}), error => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.requestId, "request-id")
    assert.deepEqual(error.problem, problem)
    return true
  })
  if (service === "asr") {
    await client.uploadMedia({body: new Uint8Array([82, 73, 70, 70]), contentType: "audio/wav"})
    assert.equal(calls.at(-1).headers.get("content-type"), "audio/wav")
    assert.equal(await calls.at(-1).text(), "RIFF")
    await client.transcribeFile({body: {model: "model", diarization: false, file: {
      content: new Blob(["RIFF"], {type: "audio/wav"}), filename: "sample.wav",
    }}})
    assert.match(calls.at(-1).headers.get("content-type"), /^multipart\/form-data; boundary=/)
    const form = await calls.at(-1).formData()
    assert.equal(form.get("diarization"), "false")
    assert.equal(form.get("file").type, "audio/wav")
    assert.equal(form.get("file").name, "sample.wav")
    assert.equal(await form.get("file").text(), "RIFF")
    await client.createLiveTranscriptionTicket({origin: "https://dashboard.test"})
    assert.equal(calls.at(-1).headers.get("origin"), "https://dashboard.test")
    assert.match(calls.at(-1).headers.get("authorization"), /^Bearer token-/)
    global.WebSocket = class {
      constructor(url, protocols) {
        this.url = url.href
        this.protocols = protocols
      }
    }
    const socket = await client.openLiveTranscription({signal})
    assert.equal(socket.url, "wss://example.test/mounted/v1/audio/transcriptions/live")
    assert.deepEqual(socket.protocols, ["voxrn.asr.v1", ticketSubprotocol])
    assert.equal(calls.at(-1).body, null)
  }
  if (service === "tts") {
    const body = {model: "model", voice: "voice", input: "example"}
    assert.equal(new TextDecoder().decode(await client.speak({body})), "RIFF")
    const stream = await client.speakStream({body, signal})
    assert.equal(stream.bodyUsed, false)
    assert.match(await stream.text(), /data: event/)
  }
  if (service === "agent") {
    const body = {model: "model", messages: [{role: "user", content: "example"}]}
    const stream = await client.chatCompletionStream({body, signal})
    assert.equal(stream.bodyUsed, false)
    assert.equal((await calls.at(-1).json()).stream, true)
    assert.equal(body.stream, undefined)
    assert.match(await stream.text(), /data: event/)
    await client.submitTurn({runId: "run", idempotencyKey: "key", body: {input: "exact", expected_revision: 0}})
    assert.equal(calls.at(-1).headers.get("idempotency-key"), "key")
    await client.streamRun({runId: "run", lastEventId: "4"})
    assert.equal(calls.at(-1).headers.get("last-event-id"), "4")
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
"""


class TypeScriptClientConformance(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node") and shutil.which("tsc"), "Node and tsc are required"
    )
    def test_typecheck_and_fetch_transports(self):
        with tempfile.TemporaryDirectory() as output:
            subprocess.run(
                [
                    "tsc",
                    "-p",
                    str(ROOT / "clients" / "typescript" / "tsconfig.json"),
                    "--module",
                    "commonjs",
                    "--noEmit",
                    "false",
                    "--outDir",
                    output,
                ],
                check=True,
                timeout=45,
            )
            subprocess.run(
                [
                    "node",
                    "-e",
                    JS_CHECK,
                    str(Path(output) / "client.js"),
                    SERVICE,
                    CLIENT_NAME,
                    json.dumps(PROBLEM),
                ],
                check=True,
                timeout=25,
            )
