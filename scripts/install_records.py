"""Record ownership of local settings, launchers and processes for clean teardown."""

import fcntl
import os
import re
import signal
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from host import NAME, ROOT, load_settings, write_settings

MARKER = ".voxrn-install.json"
ROLES = {"api", "ollama", "tray"}
LEGACY_ARTIFACTS = {
    "settings.json",
    "compose.env",
    "venv",
    "models",
    "data",
    "workflow",
    "ollama",
    "ollama-setup.log",
    ".operation.lock",
}


def marker_path(settings):
    return settings.parent / MARKER


def load_marker(settings):
    record = load_settings(marker_path(settings))
    if (
        record.get("version") != 1
        or record.get("service") != NAME
        or record.get("uid") != os.getuid()
        or record.get("prefix") != str(settings.parent)
        or record.get("checkout") != str(ROOT)
    ):
        raise RuntimeError(
            "The installation marker does not match this service, owner, checkout and prefix."
        )
    identifier = record.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise RuntimeError("Installation marker has an invalid identifier.")
    return record


def check_prefix(settings):
    if marker_path(settings).exists():
        return load_marker(settings)
    prefix = settings.parent
    if prefix.exists():
        names = {path.name for path in prefix.iterdir()} - {".operation.lock"}
        if names:
            if names - LEGACY_ARTIFACTS or "settings.json" not in names:
                raise RuntimeError(
                    "Use an empty prefix or a previous dedicated VoxRN installation."
                )
            config = load_settings(settings)
            if not all(
                field in config for field in ("environment", "token", "port", "origins")
            ):
                raise RuntimeError(
                    "The existing directory does not contain a valid VoxRN installation."
                )
    return None


def claim_prefix(settings):
    if existing := check_prefix(settings):
        return existing
    prefix = settings.parent
    record = {
        "version": 1,
        "service": NAME,
        "prefix": str(prefix),
        "checkout": str(ROOT),
        "uid": os.getuid(),
        "id": uuid.uuid4().hex,
    }
    write_settings(marker_path(settings), record)
    return record


@contextmanager
def install_lock(settings, *, create=False):
    prefix = settings.parent
    if create:
        prefix.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        prefix / ".operation.lock", os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def process_start(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
        return None if fields[0] == "Z" else int(fields[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_record(settings, role):
    if role not in ROLES:
        raise ValueError("Unknown service process role.")
    return settings.parent / "run" / f"{role}.json"


def own_process(settings, role, install_id):
    if install_id is None:
        return
    with install_lock(settings):
        _record_process(settings, role, install_id)


def _record_process(settings, role, install_id):
    owner = load_marker(settings)
    if owner["id"] != install_id:
        raise RuntimeError("Process settings do not match the installation marker.")
    path = process_record(settings, role)
    if path.exists():
        previous = load_settings(path)
        if (
            previous.get("install_id") == owner["id"]
            and process_start(previous["pid"]) == previous["start"]
        ):
            raise RuntimeError(
                f"The installation already has a running {role} process."
            )
    pid = os.getpid()
    write_settings(
        path, {"pid": pid, "start": process_start(pid), "install_id": owner["id"]}
    )


def stop_owned_process(settings, owner, role, timeout=45):
    path = process_record(settings, role)
    if not path.is_file():
        return
    record = load_settings(path)
    if record.get("install_id") != owner["id"]:
        raise RuntimeError(
            f"The {role} process record belongs to a different installation."
        )
    try:
        descriptor = os.pidfd_open(record["pid"])
    except ProcessLookupError:
        return
    try:
        if process_start(record["pid"]) != record["start"]:
            return
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process_start(record["pid"]) != record["start"]:
                return
            state = (
                Path(f"/proc/{record['pid']}/stat")
                .read_text()
                .rsplit(")", 1)[-1]
                .split()[0]
            )
            if state == "Z":
                return
            time.sleep(0.15)
        raise RuntimeError(
            f"The owned {role} process did not release its runtime. Retry after it stops."
        )
    except (FileNotFoundError, ProcessLookupError):
        return
    finally:
        os.close(descriptor)
