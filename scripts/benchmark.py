"""Measure a pinned VoxRN profile using the native buffered inference endpoint."""

import argparse
import hashlib
import io
import json
import math
import os
import platform
import statistics
import threading
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIND = json.loads((ROOT / "scripts" / "pages.json").read_text())["service"]
DEFAULT_MODEL = {"asr": "whisper-base", "tts": "supertonic", "agent": "qwen-local"}[
    KIND
]
PHRASE = "The blue bicycle is beside the library."
PROMPT = "What is two plus three? Reply with one digit."


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def percentiles(samples):
    if not samples:
        return None
    ordered = sorted(samples)
    return {
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2),
        "max": round(ordered[-1], 2),
    }


def fixture(model, voice):
    if KIND == "asr":
        audio = (ROOT / "fixtures" / "benchmark-sentence.wav").read_bytes()
        boundary = "voxrn-benchmark-2026"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nen\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode()
            + audio
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return (
            "/v1/audio/transcriptions",
            f"multipart/form-data; boundary={boundary}",
            body,
            audio,
        )
    if KIND == "tts":
        source = PHRASE.encode()
        path = "/v1/audio/speech"
        value = {"model": model, "voice": voice, "input": PHRASE}
    else:
        source = PROMPT.encode()
        path = "/v1/chat/completions"
        value = {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_completion_tokens": 64,
        }
    return path, "application/json", json.dumps(value).encode(), source


def request(url, credential, path, body=None, content=None, timeout=120):
    headers = {"Authorization": f"Bearer {credential}"}
    if body is not None:
        headers.update(
            {
                "Content-Type": content,
                "Accept": "audio/wav" if KIND == "tts" else "application/json",
            }
        )
    request = urllib.request.Request(url + path, data=body, headers=headers)
    return opener().open(request, timeout=timeout)


def accepts(body):
    try:
        if KIND == "tts":
            with wave.open(io.BytesIO(body), "rb") as audio:
                return audio.getnframes() > 1000 and audio.getsampwidth() == 2
        data = json.loads(body)
        if KIND == "asr":
            return "bicycle" in data["text"].lower()
        return data["choices"][0]["message"]["content"].strip() == "5"
    except (ValueError, KeyError, TypeError, IndexError, EOFError, wave.Error):
        return False


def call(url, key, path, content, body, timeout):
    start = time.perf_counter()
    row = {"status": None, "code": None, "request_id": None, "fixture_check": False}
    try:
        with request(url, key, path, body, content, timeout) as response:
            received = response.read()
            row.update(
                status=response.status,
                request_id=response.headers.get("X-Request-ID"),
                fixture_check=accepts(received),
                bytes=len(received),
            )
            # The benchmark sends a fixed public fixture. Preserve the actual
            # model output and terminal reason for diagnosing failed checks.
            decoded = json.loads(received)
            choices = decoded.get("choices", [])
            if choices:
                row["finish_reason"] = choices[0].get("finish_reason")
                row["fixture_response"] = choices[0].get("message", {}).get("content")
    except urllib.error.HTTPError as error:
        with error:
            try:
                problem = json.load(error)
            except ValueError:
                problem = {}
            row.update(
                status=error.code,
                code=problem.get("code"),
                request_id=error.headers.get("X-Request-ID"),
            )
    except (OSError, ValueError) as error:
        row["code"] = type(error).__name__
    row["ms"] = round((time.perf_counter() - start) * 1000, 2)
    return row


def hardware():
    model = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        platform.machine(),
    )
    return {
        "cpu": model,
        "logical_cpus": os.cpu_count(),
        "architecture": platform.machine(),
        "kernel": platform.release(),
        "system": platform.system(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--settings",
        type=Path,
        help="Private local settings.json, or set VOXRN_BENCHMARK_TOKEN.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--voice", default="stock-en-1")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--waves", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.waves <= 200 or any(
        not 1 <= width <= 64 for width in args.concurrency
    ):
        parser.error("Choose 1–200 waves and concurrency from 1 to 64.")
    key = (
        json.loads(args.settings.read_text())["token"]
        if args.settings
        else os.environ.get("VOXRN_BENCHMARK_TOKEN")
    )
    if not key:
        parser.error("Provide the private settings file or VOXRN_BENCHMARK_TOKEN.")
    url = args.url.rstrip("/")
    with request(url, key, "/v1/capabilities") as response:
        capability = json.load(response)
    identity = next(
        (
            row["identity"]
            for row in capability["models"]
            if row["identity"]["model"] == args.model
        ),
        None,
    )
    if identity is None:
        parser.error("Enable the requested model before running the benchmark.")
    path, content, body, source = fixture(args.model, args.voice)

    def infer():
        return call(url, key, path, content, body, args.timeout)

    def wave_call(barrier):
        barrier.wait()
        return infer()

    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "service": KIND,
        "path": path,
        "identity": identity,
        "hardware": hardware(),
        "fixture_sha256": hashlib.sha256(source).hexdigest(),
        "fixture_bytes": len(source),
        "first_after_activation": infer(),
        "steps": [],
    }
    for width in args.concurrency:
        rows = []
        elapsed = time.perf_counter()
        with ThreadPoolExecutor(max_workers=width) as pool:
            for _ in range(args.waves):
                barrier = threading.Barrier(width)
                pending = [pool.submit(wave_call, barrier) for _ in range(width)]
                rows.extend(item.result() for item in pending)
        duration = time.perf_counter() - elapsed
        good = [row for row in rows if row["status"] == 200 and row["fixture_check"]]
        report["steps"].append(
            {
                "concurrency": width,
                "attempted": len(rows),
                "fixture_successes": len(good),
                "admission_429": sum(row["status"] == 429 for row in rows),
                "admitted_latency_ms": percentiles(
                    [row["ms"] for row in rows if row["status"] == 200]
                ),
                "successful_per_second": round(len(good) / duration, 3),
                "successful_latency_ms": percentiles([row["ms"] for row in good]),
                "responses": rows,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        KIND,
        "benchmark evidence written with",
        sum(row["attempted"] for row in report["steps"]),
        "concurrent requests",
    )


if __name__ == "__main__":
    main()
