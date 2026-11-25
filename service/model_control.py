"""Own installed model lifecycle and the leases held by in-flight requests."""

import asyncio
import fcntl
import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .wire import Problem

LOG = logging.getLogger(__name__)


@dataclass
class Entry:
    adapter: object
    identity: dict
    enabled: bool
    state: str = "disabled"
    active_requests: int = 0
    loaded: bool = False
    needs_cleanup: bool = False
    error: str | None = None
    idle: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def __post_init__(self):
        self.idle.set()

    def describe(self):
        result = {
            "identity": self.identity.copy(),
            "enabled": self.enabled,
            "state": self.state,
            "active_requests": self.active_requests,
        }
        if self.error is not None:
            result["error_code"] = self.error
        return result


class ModelControl:
    def __init__(self, models, state_path, *, load, unload, default_enabled=None):
        if default_enabled is None:
            configured = os.environ.get("VOXRN_DEFAULT_ENABLED_MODELS")
            default_enabled = (
                set(configured.split()) if configured is not None else set(models)
            )
        self.entries = {}
        for model_id, adapter in models.items():
            identity = adapter.identity.copy()
            if identity["model"] != model_id:
                raise ValueError(
                    f"Configured model ID {model_id} differs from its adapter identity."
                )
            self.entries[model_id] = Entry(
                adapter, identity, model_id in default_enabled
            )
        self.state_path = Path(state_path)
        self.load = load
        self.unload = unload
        self._lock_file = None
        self._stopping = False

    def _read_state(self):
        if not self.state_path.exists():
            return
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or not isinstance(state.get("models"), dict):
            raise RuntimeError("Model control state has an unsupported format.")
        for model_id, entry in self.entries.items():
            saved = state["models"].get(model_id)
            if (
                isinstance(saved, dict)
                and saved.get("identity") == entry.identity
                and type(saved.get("enabled")) is bool
            ):
                entry.enabled = saved["enabled"]

    def _save_state(self):
        payload = {
            "version": 1,
            "models": {
                model_id: {"identity": entry.identity, "enabled": entry.enabled}
                for model_id, entry in self.entries.items()
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.state_path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(payload, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                os.replace(temporary, self.state_path)
            finally:
                temporary.unlink(missing_ok=True)

    def _open_lock(self):
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.state_path.with_suffix(".lock")
        lock = path.open("a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock.close()
            raise RuntimeError(
                "Another service process owns this model state directory."
            ) from exc
        self._lock_file = lock

    async def start(self):
        self._open_lock()
        self._stopping = False
        try:
            self._read_state()
            tasks = [
                self._schedule(entry)
                for entry in self.entries.values()
                if entry.enabled
            ]
            if tasks:
                await asyncio.shield(asyncio.gather(*tasks))
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if self._lock_file is None:
            return
        self._stopping = True
        tasks = [self._schedule(entry) for entry in self.entries.values()]
        try:
            if tasks:
                transitions = asyncio.gather(*tasks)
                try:
                    await asyncio.shield(transitions)
                except asyncio.CancelledError:
                    await transitions
                    raise
            if any(
                entry.loaded or entry.needs_cleanup for entry in self.entries.values()
            ):
                raise RuntimeError(
                    "One or more model adapters could not release their runtime."
                )
        finally:
            self._lock_file.close()
            self._lock_file = None

    def available(self):
        if self._stopping:
            return {}
        return {
            model_id: entry.adapter
            for model_id, entry in self.entries.items()
            if entry.state == "ready" and entry.enabled
        }

    def list(self):
        return {
            "models": [
                self.entries[model_id].describe() for model_id in sorted(self.entries)
            ]
        }

    def _entry(self, model_id):
        try:
            return self.entries[model_id]
        except KeyError as exc:
            raise Problem(404, "unknown_model", "Select an installed model.") from exc

    def set_enabled(self, model_id, enabled):
        entry = self._entry(model_id)
        if self._stopping or self._lock_file is None:
            raise Problem(
                503,
                "control_unavailable",
                "Model control is not accepting changes.",
                retryable=True,
            )
        if entry.enabled != enabled:
            previous = entry.enabled
            entry.enabled = enabled
            try:
                self._save_state()
            except OSError as exc:
                entry.enabled = previous
                raise Problem(
                    503,
                    "model_state_write_failed",
                    "The model selection could not be saved.",
                    retryable=True,
                ) from exc
        self._schedule(entry)
        return entry.describe()

    @asynccontextmanager
    async def acquire(self, model_id):
        entry = self._entry(model_id)
        if not entry.enabled or entry.state != "ready" or self._stopping:
            raise Problem(
                503,
                "model_unavailable",
                "The selected model is not ready.",
                retryable=entry.enabled and not self._stopping,
            )
        entry.active_requests += 1
        entry.idle.clear()
        try:
            yield entry.adapter
        finally:
            entry.active_requests -= 1
            if entry.active_requests == 0:
                entry.idle.set()

    def _wanted(self, entry):
        return entry.enabled and not self._stopping

    def _schedule(self, entry):
        if not self._wanted(entry) and entry.loaded and entry.state == "ready":
            entry.state = "draining"
        if entry.task is None or entry.task.done():
            if self._wanted(entry) and not entry.loaded and not entry.needs_cleanup:
                entry.state = "loading"
            entry.task = asyncio.create_task(self._drive(entry))
        return entry.task

    def _fail(self, entry, action, error):
        entry.state = "failed"
        entry.error = (
            error.code if isinstance(error, Problem) else f"model_{action}_failed"
        )
        LOG.error(
            "Model %s %s failed (%s)",
            entry.identity["model"],
            action,
            type(error).__name__,
        )

    async def _cleanup(self, entry):
        entry.state = "draining"
        await entry.idle.wait()
        try:
            await self.unload(entry.adapter)
        except Exception as exc:
            entry.needs_cleanup = True
            self._fail(entry, "unload", exc)
            return False
        entry.loaded = False
        entry.needs_cleanup = False
        return True

    async def _drive(self, entry):
        while True:
            wanted = self._wanted(entry)
            if entry.needs_cleanup or (entry.loaded and not wanted):
                if not await self._cleanup(entry):
                    return
                continue
            if not wanted:
                entry.state = "disabled"
                entry.error = None
                return
            if entry.loaded:
                entry.state = "ready"
                entry.error = None
                return
            entry.state = "loading"
            entry.error = None
            try:
                await self.load(entry.adapter)
            except Exception as exc:
                entry.needs_cleanup = True
                if await self._cleanup(entry):
                    self._fail(entry, "load", exc)
                return
            entry.loaded = True
