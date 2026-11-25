"""Describe managed profiles and provision them lazily on first activation."""

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

from .wire import Problem

ROOT = Path(__file__).resolve().parents[1]


class ManagedAdapter:
    def __init__(self, spec, settings, factory, adapter=None):
        self.spec = spec
        self.settings = settings
        self.factory = factory
        self.adapter = adapter
        self.process = None
        self.interrupt = False
        self.identity = {"model": spec["id"], **spec["identity"]}
        self.default_enabled = False
        self.descriptor = (
            settings.parent / "profiles" / spec["id"] / "installation.json"
        )

    def __getattr__(self, name):
        adapter = self.adapter
        if adapter is None:
            raise AttributeError(name)
        return getattr(adapter, name)

    def installation(self):
        spec = self.spec
        return {
            "installed": self.descriptor.is_file(),
            "runtime": "ollama" if "ollama" in spec else "python-venv",
            "hardware": spec.get("hardware", "cpu"),
            "disk_mib": spec["disk_mib"],
            "memory_mib": spec["memory_mib"],
            **({"vram_mib": spec["vram_mib"]} if "vram_mib" in spec else {}),
        }

    def cancel_preparing(self):
        self.interrupt = True
        if self.process and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    async def prepare(self):
        self.interrupt = False
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "model_install.py"),
            str(self.settings),
            self.identity["model"],
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self.process = worker
        if self.interrupt:
            self.cancel_preparing()
        try:
            output, _ = await worker.communicate()
        except asyncio.CancelledError:
            if worker.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(worker.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(worker.wait(), timeout=10)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(worker.pid, signal.SIGKILL)
                    await worker.wait()
            raise
        finally:
            self.process = None
        if self.interrupt:
            raise Problem(
                503, "model_provision_cancelled", "Model provisioning was cancelled."
            )
        try:
            answer = json.loads(output)
        except ValueError as exc:
            raise Problem(
                503,
                "model_provision_failed",
                "The model install process did not complete.",
            ) from exc
        if worker.returncode:
            raise Problem(503, answer["code"], answer["detail"], retryable=True)
        candidate = self.factory(self.identity["model"], answer["environment"])
        if candidate.identity != self.identity:
            raise Problem(
                503,
                "model_identity_mismatch",
                "The installed model did not match its pinned identity.",
            )
        self.adapter = candidate


def managed_models(models, factory):
    settings = os.environ.get("VOXRN_MANAGED_SETTINGS")
    if not settings:
        return models
    specs = json.loads((ROOT / "scripts" / "model-profiles.json").read_text())[
        "profiles"
    ]
    for spec in specs:
        name = spec["id"]
        models[name] = ManagedAdapter(spec, Path(settings), factory, models.get(name))
    return models


def configured_defaults(models):
    return {
        name
        for name, adapter in models.items()
        if getattr(adapter, "default_enabled", True)
    }
