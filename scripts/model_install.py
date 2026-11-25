"""Check host capacity, then provision one model into its owned profile directory."""

import argparse
import fcntl
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from contextlib import contextmanager

from host import http_json, load_settings, ollama_env, wait_endpoint, write_settings
from install_records import load_marker
from provision_models import profiles, provision_profiles


class CapacityError(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


@contextmanager
def model_lock(settings):
    fd = os.open(
        settings.parent / ".model-library.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def available_memory():
    fields = dict(
        line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
    )
    available = int(fields["MemAvailable"].split()[0]) // 1024
    groups = Path("/proc/self/cgroup").read_text().splitlines()
    group = next((line[3:] for line in groups if line.startswith("0::")), "/")
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    for path in (root, *root.parents):
        if not path.is_relative_to("/sys/fs/cgroup"):
            break
        try:
            limit = (path / "memory.max").read_text().strip()
            used = int((path / "memory.current").read_text())
        except OSError:
            continue
        if limit != "max":
            available = min(available, max(0, (int(limit) - used) // 1024**2))
    return available


def require_gpu(spec):
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    if (
        platform.machine() != "x86_64"
        or not visible.startswith("GPU-")
        or "," in visible
    ):
        raise CapacityError(
            "cuda_scope_required",
            "Start this service with one reserved NVIDIA GPU before enabling the model.",
        )
    # On the broker host, inspect the live reservation before any CUDA worker starts.
    broker = Path("/run/ollama-unify/gpu-negotiator.sock")
    if broker.exists():
        owner = os.environ.get("VOXRN_GPU_LEASE_OWNER")
        result = subprocess.run(
            ["docker", "gpu", "discover"],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        report = json.loads(result.stdout)
        if visible in report.get("unregistered_gpu_quarantine", []) or not any(
            owner
            and row["owner"] == owner
            and row["gpu_uuids"] == [visible]
            and row["state"] in {"pending", "active"}
            and (row["requested_mib"] == 0 or row["requested_mib"] >= spec["vram_mib"])
            for row in report.get("active_leases", [])
        ):
            raise CapacityError(
                "insufficient_gpu_reservation",
                "Reserve the required GPU memory through the broker before activation.",
            )
    else:
        result = subprocess.check_output(
            [
                "nvidia-smi",
                "-i",
                visible,
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
        if int(result.strip()) < spec["vram_mib"]:
            raise CapacityError(
                "insufficient_gpu_memory",
                "The selected GPU has insufficient available memory.",
            )


def require_capacity(prefix, spec, *, installed=False, activation=True):
    free = shutil.disk_usage(prefix).free // 1024**2
    if not installed and free < spec["disk_mib"]:
        raise CapacityError(
            "insufficient_model_disk",
            f"Free at least {spec['disk_mib']} MiB on the model volume. Available: {free} MiB.",
        )
    memory = available_memory()
    if activation and memory < spec["memory_mib"]:
        raise CapacityError(
            "insufficient_model_memory",
            f"Activation needs {spec['memory_mib']} MiB host memory. Available: {memory} MiB.",
        )
    if activation and spec.get("hardware") == "cuda":
        require_gpu(spec)


def install_ollama(path, config, spec):
    profile = spec["ollama"]
    base = f"http://127.0.0.1:{config['ollama_port']}"
    if (
        wait_endpoint(base + "/api/version", seconds=20)["version"]
        != profile["engine_revision"]
    ):
        raise RuntimeError("Unexpected Ollama runtime revision.")

    def matches():
        return any(
            item["name"] == profile["tag"] and item["digest"] == profile["digest"]
            for item in http_json(base + "/api/tags")["models"]
        )

    if not matches():
        subprocess.run(
            [config["ollama_binary"], "pull", profile["tag"]],
            env=ollama_env(path, config),
            stdout=sys.stderr,
            stderr=sys.stderr,
            check=True,
            timeout=3600,
        )
    if not matches():
        raise RuntimeError("The pulled model did not match its exact manifest digest.")
    return {}


def install(path, name, config, spec, *, features=(), activate=True, python=None):
    prefix = path.parent
    descriptor = prefix / "profiles" / name / "installation.json"
    current = load_settings(descriptor) if descriptor.is_file() else {}
    valid = current.get("profile") == spec and current.get("features") == list(features)
    environment = current.get("environment", {}) if valid else {}
    valid = valid and all(
        Path(value).exists() for value in environment.values() if value.startswith("/")
    )
    require_capacity(prefix, spec, installed=valid, activation=activate)
    if "ollama" in spec:
        environment = install_ollama(path, config, spec)
    elif not valid:
        environment = provision_profiles(
            prefix, Path(python or sys.executable), {name: list(features)}, {name: spec}
        )
    if not valid and activate:
        require_capacity(prefix, spec, installed=True)
    write_settings(
        descriptor,
        {"profile": spec, "features": list(features), "environment": environment},
    )
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings", type=Path)
    parser.add_argument("model")
    args = parser.parse_args()
    path = args.settings
    try:
        with model_lock(path):
            load_marker(path)
            config = load_settings(path)
            spec = profiles()[args.model]
            # Redirect installers' progress to journald. stdout is reserved for
            # the one structured receipt consumed by the API process.
            saved = os.dup(1)
            try:
                sys.stdout.flush()
                os.dup2(2, 1)
                result = install(
                    path,
                    args.model,
                    config,
                    spec,
                    features=config.get("profiles", {}).get(args.model, ()),
                )
                receipt = {"environment": result}
                status = 0
            finally:
                sys.stdout.flush()
                os.dup2(saved, 1)
                os.close(saved)
    except (
        CapacityError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Model provisioning: {type(exc).__name__}: {exc}", file=sys.stderr)
        receipt = {
            "code": getattr(exc, "code", "model_provision_failed"),
            "detail": str(exc)
            if isinstance(exc, CapacityError)
            else "The model could not be provisioned. Inspect the service log and retry.",
        }
        status = 1
    print(json.dumps(receipt))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
