"""Bounded runtime measurements with no request payloads or credential storage."""

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import time
from collections import OrderedDict, deque
from contextvars import ContextVar
from pathlib import Path
from subprocess import DEVNULL, SubprocessError, check_output
from urllib.request import ProxyHandler, build_opener

TRACE = ContextVar("runtime_trace", default=None)
CLIENT_WINDOW = 60
MAX_RECORDS = 8192
CLIENT_ID = re.compile(r"[a-zA-Z0-9_-]{12,80}")


def mark_client(scope, principal):
    trace = TRACE.get()
    if trace is None:
        return
    headers = dict(scope.get("headers", ()))
    supplied = headers.get(b"x-voxrn-client-id", b"").decode("ascii", errors="ignore")
    if not supplied and scope["type"] == "websocket":
        supplied = next(
            (
                name.removeprefix("voxrn-client.")
                for name in scope.get("subprotocols", ())
                if name.startswith("voxrn-client.")
            ),
            "",
        )
    if not CLIENT_ID.fullmatch(supplied):
        peer = (scope.get("client") or ("unknown",))[0]
        agent = headers.get(b"user-agent", b"")[:192]
        supplied = str(peer) + "|" + agent.decode("utf-8", errors="replace")
    signature = hashlib.sha256((principal.owner + "|" + supplied).encode()).digest()
    trace["client"] = signature
    trace["telemetry"].client(signature)


def mark_failure():
    if (trace := TRACE.get()) is not None:
        trace["failed"] = True


def memory_available(host_available):
    try:
        members = Path("/proc/self/cgroup").read_text().splitlines()
        member = next((line[3:] for line in members if line.startswith("0::")), "/")
        root = Path("/sys/fs/cgroup") / member.lstrip("/")
        for item in (root, *root.parents):
            if not item.is_relative_to("/sys/fs/cgroup"):
                break
            try:
                maximum = (item / "memory.max").read_text().strip()
                used = int((item / "memory.current").read_text())
                if maximum != "max":
                    host_available = min(host_available, max(0, int(maximum) - used))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return host_available


def read_host(path):
    try:
        cpu = list(
            map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9])
        )
        mem = dict(
            line.split(":", 1)
            for line in Path("/proc/meminfo").read_text().splitlines()
        )
        total = int(mem["MemTotal"].split()[0]) * 1024
        available = int(mem["MemAvailable"].split()[0]) * 1024
        disk = shutil.disk_usage(path)
        return sum(cpu), cpu[3] + cpu[4], total, available, disk.total, disk.free
    except (OSError, ValueError, KeyError, IndexError):
        return None


def read_gpus():
    if not shutil.which("nvidia-smi"):
        return []
    try:
        values = check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=DEVNULL,
            timeout=2,
        )
        rows = []
        for index, line in enumerate(values.splitlines()):
            name, usage, used, total = (value.strip() for value in line.rsplit(",", 3))
            rows.append(
                {
                    "index": index,
                    "name": name,
                    "utilization": int(usage) if usage.isdigit() else None,
                    "memory_used_mib": int(used),
                    "memory_total_mib": int(total),
                }
            )
        return rows
    except (OSError, ValueError, TimeoutError, SubprocessError):
        return []


def worker_memory(entry, runners):
    adapter = getattr(entry.adapter, "adapter", entry.adapter)
    if adapter is None:
        return None
    profile = getattr(adapter, "profile", None)
    if profile is not None:
        row = runners.get(profile.tag)
        if row is not None:
            return (
                max(0, row["size"] - row.get("size_vram", 0))
                if type(row.get("size")) is int
                else None
            )
    process = getattr(adapter, "process", None)
    process = getattr(process, "_process", process)
    if not (pid := getattr(process, "pid", None)):
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def ollama_runners():
    if not (settings := os.environ.get("VOXRN_MANAGED_SETTINGS")):
        return {}
    try:
        port = json.loads(Path(settings).read_text()).get("ollama_port")
        if not isinstance(port, int):
            return {}
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/ps", timeout=1) as result:
            return {row["name"]: row for row in json.load(result)["models"]}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def fit_health(entry, host, cpu, rss, audio_rtf):
    adapter = entry.adapter
    if host is None or not hasattr(adapter, "installation"):
        return None
    plan = adapter.installation()
    required = plan["memory_mib"] * 1024**2
    available = host.get(
        "memory_available_bytes", host["memory_bytes"] - host["memory_used_bytes"]
    )
    # Account for a loaded model's measured residency in the fit calculation.
    memory_ratio = (available + (rss or 0)) / max(required, rss or 0, 1)
    disk_required = plan["disk_mib"] * 1024**2
    disk_ratio = (
        2 if plan["installed"] else host["filesystem_free_bytes"] / disk_required
    )
    details = [f"Memory headroom {memory_ratio:.2f}× budget"]
    if not plan["installed"]:
        details.append(f"Filesystem headroom {disk_ratio:.2f}× installation budget")
    gpu_ready = plan["hardware"] != "cuda" or (
        (
            not Path("/run/ollama-unify/gpu-negotiator.sock").exists()
            or bool(os.environ.get("VOXRN_GPU_LEASE_OWNER"))
        )
        and os.environ.get("CUDA_VISIBLE_DEVICES", "").startswith("GPU-")
        and "," not in os.environ.get("CUDA_VISIBLE_DEVICES", "")
    )
    if not gpu_ready:
        details.append("No scoped GPU runtime configured")
    if not gpu_ready or memory_ratio < 1 or disk_ratio < 1:
        score = 0
    else:
        score = 56 + 44 * min(1, memory_ratio - 1, disk_ratio - 1)
        if cpu is not None and cpu > 65:
            score -= (cpu - 65) * 0.55
            details.append(f"Host CPU {cpu:.1f}%")
        if audio_rtf is not None:
            details.append(f"Observed audio RTF {audio_rtf:.2f}×")
            if audio_rtf > 1:
                score = min(score, 59 - min(40, (audio_rtf - 1) * 22))
        if entry.state == "failed":
            score = min(score, 24)
            details.append("Model runtime failed")
    score = max(0, min(100, round(score)))
    label = (
        "Excellent"
        if score >= 85
        else "Good"
        if score >= 70
        else "Nominal"
        if score >= 50
        else "Bad"
        if score >= 25
        else "Unusable"
    )
    return {
        "score": score,
        "label": label,
        "basis": "Measured + estimate"
        if rss is not None or audio_rtf is not None
        else "Estimated",
        "detail": details,
        "rss_bytes": rss,
        "audio_rtf": audio_rtf,
    }


class Telemetry:
    def __init__(self, routes, tools, model_control):
        self.routes = [
            (
                f"{method.upper()} {pattern}",
                method.upper(),
                re.compile(
                    "^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(pattern)) + "$"
                ),
            )
            for pattern, methods in routes.items()
            for method, spec in methods.items()
            if isinstance(spec, dict) and "responses" in spec
        ]
        self.routes += (
            [
                (
                    "WS /v1/audio/transcriptions/live",
                    "WS",
                    re.compile("^/v1/audio/transcriptions/live$"),
                )
            ]
            if any("/v1/audio/transcriptions" in key for key in routes)
            else []
        )
        self.tools = frozenset(tools)
        self.control = model_control
        self.requests = deque(maxlen=MAX_RECORDS)
        self.calls = deque(maxlen=MAX_RECORDS)
        self.events = deque(maxlen=512)
        self.samples = deque(maxlen=230)
        self.clients = OrderedDict()
        self.active = {}
        self.started = time.time()
        self.cpu = None
        self.gpus = []
        self.runners = {}
        self.audio = {}
        self.health = {}
        self.previous = None
        self.host = None
        self.path = Path(os.environ.get("VOXRN_DATA_DIR", "."))

    def client(self, signature):
        self.clients[signature] = time.time()
        self.clients.move_to_end(signature)
        while len(self.clients) > 4096:
            self.clients.popitem(last=False)

    def endpoint(self, scope):
        path = scope.get("path", "")
        method = "WS" if scope["type"] == "websocket" else scope.get("method")
        if path == "/mcp":
            headers = dict(scope.get("headers", ()))
            tool = headers.get(b"mcp-name", b"").decode("ascii", errors="ignore")
            protocol = headers.get(b"mcp-method", b"").decode("ascii", errors="ignore")
            if tool in self.tools:
                return "MCP " + tool
            if protocol in {"server/discover", "tools/list"}:
                return "MCP " + protocol
        if path.startswith("/docs/") or path in {"/", "/docs"}:
            return "GET /docs/"
        return next(
            (
                name
                for name, verb, pattern in self.routes
                if verb == method and pattern.fullmatch(path)
            ),
            "Unmatched route",
        )

    def event(self, subject, state, detail=None):
        if len(self.events) >= 2:
            previous = self.events[-1]
            if previous["subject"] == subject and previous["state"] == state:
                return
        self.events.append(
            {"time": time.time(), "subject": subject, "state": state, "detail": detail}
        )

    def audio_factor(self, model, wall_seconds, audio_seconds):
        if audio_seconds > 0:
            samples = self.audio.setdefault(model, deque(maxlen=12))
            samples.append((wall_seconds, audio_seconds))

    def model_health(self, name, entry):
        samples = self.audio.get(name, ())
        rtf = (
            round(sum(row[0] for row in samples) / sum(row[1] for row in samples), 3)
            if samples
            else None
        )
        return fit_health(
            entry, self.host, self.cpu, worker_memory(entry, self.runners), rtf
        )

    def model_enter(self, model):
        trace = TRACE.get()
        if trace is not None:
            trace["model"] = model
        return time.monotonic()

    def model_exit(self, model, start, failed):
        trace = TRACE.get()
        failed = failed or (trace is not None and trace["failed"])
        self.calls.append(
            (time.time(), model, failed, round((time.monotonic() - start) * 1000))
        )

    def counts(self):
        now = time.time()
        while self.clients and next(iter(self.clients.values())) < now - CLIENT_WINDOW:
            self.clients.popitem(last=False)
        sockets = [
            trace
            for trace in self.active.values()
            if trace["socket"] and trace["client"]
        ]
        active_clients = set(self.clients) | {trace["client"] for trace in sockets}
        return (
            len(active_clients),
            len(sockets),
            sum(not trace["probe"] for trace in self.active.values()),
        )

    def model_states(self):
        return (
            {
                name: {
                    "state": entry.state,
                    "active": entry.active_requests,
                    "loaded": entry.loaded,
                    "enabled": entry.enabled,
                }
                for name, entry in self.control.entries.items()
            }
            if self.control
            else {}
        )

    async def sample(self):
        loop = 0
        while True:
            reading = read_host(self.path)
            if reading:
                total, idle, memory, available, disk, free = reading
                self.host = {
                    "architecture": platform.machine(),
                    "logical_cpus": os.cpu_count(),
                    "memory_bytes": memory,
                    "memory_used_bytes": memory - available,
                    "memory_available_bytes": memory_available(available),
                    "filesystem_bytes": disk,
                    "filesystem_free_bytes": free,
                }
                if self.previous and total > self.previous[0]:
                    self.cpu = round(
                        100
                        * (1 - (idle - self.previous[1]) / (total - self.previous[0])),
                        1,
                    )
                self.previous = total, idle
            if loop % 5 == 0:
                self.gpus = await asyncio.to_thread(read_gpus)
            self.runners = await asyncio.to_thread(ollama_runners)
            if self.control:
                self.health = {
                    name: health
                    for name, entry in self.control.entries.items()
                    if (health := self.model_health(name, entry)) is not None
                }
            models = self.model_states()
            clients, sockets, active = self.counts()
            self.samples.append(
                {
                    "time": time.time(),
                    "cpu": self.cpu,
                    "memory": round(
                        100
                        * self.host["memory_used_bytes"]
                        / self.host["memory_bytes"],
                        1,
                    )
                    if self.host
                    else None,
                    "active": active,
                    "clients": clients,
                    "websockets": sockets,
                    "loaded": sum(row["loaded"] for row in models.values()),
                    "busy": sum(row["active"] > 0 for row in models.values()),
                }
            )
            loop += 1
            await asyncio.sleep(4)

    def snapshot(self, window, model=None, endpoint=None):
        cutoff = time.time() - window
        clients, sockets, active = self.counts()
        endpoints = {
            key: [0, 0, 0]
            for key in (
                *[row[0] for row in self.routes],
                "GET /docs/",
                *["MCP " + tool for tool in sorted(self.tools)],
                "MCP server/discover",
                "MCP tools/list",
                "Unmatched route",
            )
            if endpoint in (None, key)
        }
        models = self.model_states()
        model_summary = {key: [0, 0, 0] for key in models if model in (None, key)}
        for timestamp, name, selected, failed, ms in self.requests:
            if (
                timestamp < cutoff
                or name not in endpoints
                or model not in (None, selected)
            ):
                continue
            item = endpoints[name]
            item[0] += 1
            item[1] += failed
            item[2] += ms
        events = [event for event in reversed(self.events) if event["time"] >= cutoff]
        for timestamp, selected, failed, ms in (
            self.calls
            if endpoint is None
            else (
                (stamp, selected, failed, ms)
                for stamp, verb, selected, failed, ms in self.requests
                if verb == endpoint
            )
        ):
            if timestamp >= cutoff and selected in model_summary:
                item = model_summary[selected]
                item[0] += 1
                item[1] += failed
                item[2] += ms
        if model:
            events = [
                event for event in events if event["subject"] in (model, "service")
            ]

        def summarize(data):
            return {
                "calls": data[0],
                "errors": data[1],
                "mean_ms": round(data[2] / data[0]) if data[0] else None,
            }

        return {
            "health": self.health,
            "since": self.started,
            "window_seconds": window,
            "client_window_seconds": CLIENT_WINDOW,
            "clients": clients,
            "websockets": sockets,
            "active_requests": active,
            "host": {**self.host, "cpu": self.cpu, "gpus": self.gpus}
            if self.host
            else None,
            "samples": [row for row in self.samples if row["time"] >= cutoff],
            "endpoints": [
                {"name": key, **summarize(values)} for key, values in endpoints.items()
            ],
            "models": [
                {"name": key, **models[key], **summarize(values)}
                for key, values in model_summary.items()
            ],
            "lifecycle": events[:200],
            "endpoint_options": sorted(
                {row[0] for row in self.routes}
                | {
                    "GET /docs/",
                    "Unmatched route",
                    "MCP server/discover",
                    "MCP tools/list",
                }
                | {"MCP " + tool for tool in self.tools}
            ),
            "model_options": sorted(models),
        }


class RuntimeMiddleware:
    def __init__(self, app, telemetry):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] not in {"http", "websocket"}
            or scope.get("method") == "OPTIONS"
        ):
            return await self.app(scope, receive, send)
        name = self.telemetry.endpoint(scope)
        trace = {
            "telemetry": self.telemetry,
            "model": None,
            "failed": False,
            "client": None,
            "socket": scope["type"] == "websocket",
            "probe": name == "GET /v1/control/runtime",
        }
        start = time.monotonic()
        status = 500
        token = TRACE.set(trace)
        self.telemetry.active[id(trace)] = trace

        async def observed(message):
            nonlocal status
            if message["type"] in {
                "http.response.start",
                "websocket.http.response.start",
            }:
                status = message["status"]
            elif message["type"] == "websocket.accept":
                status = 200
            elif message["type"] == "websocket.close" and status == 500:
                status = 403
            await send(message)

        try:
            await self.app(scope, receive, observed)
        finally:
            if trace["client"] is not None:
                self.telemetry.client(trace["client"])
            self.telemetry.requests.append(
                (
                    time.time(),
                    name,
                    trace["model"],
                    status >= 400 or trace["failed"],
                    round((time.monotonic() - start) * 1000),
                )
            )
            self.telemetry.active.pop(id(trace), None)
            TRACE.reset(token)
