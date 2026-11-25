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
        folder = self.descriptor.parent
        receipt = self._metadata(self.descriptor)
        engine = "ollama" if "ollama" in spec else "python-venv"
        if engine == "ollama":
            tag = spec["ollama"]["tag"]
            name, _, variant = tag.rpartition(":")
            manifest = (
                self.settings.parent
                / "models/ollama/manifests/registry.ollama.ai/library"
                / name
                / variant
            )
            model_bytes = receipt.get("model_bytes", 0) if manifest.is_file() else 0
            present = manifest.is_file()
            environment_present = Path(
                self._metadata(self.settings).get("ollama_binary", "/dev/null")
            ).is_file()
        else:
            model_bytes = self._file_bytes(folder / "assets") + self._file_bytes(
                folder / "files"
            )
            present = bool(receipt.get("environment")) and all(
                Path(value).exists()
                for value in receipt["environment"].values()
                if value.startswith("/")
            )
            environment_present = (folder / "venv/bin/python").is_file()
        installed = bool(receipt) and present and environment_present
        disk_state = (
            "installed"
            if installed
            else "missing"
            if receipt
            else "partial"
            if model_bytes or environment_present and engine != "ollama"
            else "not_installed"
        )
        progress = self._metadata(
            self.settings.parent / "run" / "model-progress" / (spec["id"] + ".json")
        )
        return {
            "installed": installed,
            "runtime": engine,
            "hardware": spec.get("hardware", "cpu"),
            "disk_mib": spec["disk_mib"],
            "memory_mib": spec["memory_mib"],
            "disk_state": disk_state,
            "environment_present": environment_present,
            "model_bytes": model_bytes,
            **({"progress": progress} if progress else {}),
            **({"vram_mib": spec["vram_mib"]} if "vram_mib" in spec else {}),
        }

    @staticmethod
    def _metadata(path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _file_bytes(path):
        total = 0
        for entry in path.rglob("*"):
            try:
                if not entry.is_symlink() and entry.is_file():
                    total += entry.stat().st_blocks * 512
            except OSError:
                continue
        return total

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
        candidate = self.factory(self.spec, self.settings)
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
