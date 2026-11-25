"""Host configuration shared by the installer, systemd launcher and tray."""

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIND = json.loads((ROOT / "scripts" / "pages.json").read_text())["service"]
NAME = f"voxrn-{KIND}"
DEFAULT_PORT = {"asr": 8311, "tts": 8312, "agent": 8313}[KIND]
BASE = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "voxrn"
    / KIND
)
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
SETTINGS = BASE / "settings.json"
USER_UNIT = f"{NAME}.service"
PROVIDER_UNIT = "voxrn-agent-ollama.service"


def write_settings(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_settings(path):
    return json.loads(path.read_text())


def http_json(url, *, token=None, timeout=5):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(
        urllib.request.Request(url, headers=headers), timeout=timeout
    ) as response:
        return json.load(response)


def wait_endpoint(url, *, token=None, seconds=120, process=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return http_json(url, token=token, timeout=2)
        except (OSError, ValueError):
            if process is not None and process.poll() is not None:
                raise RuntimeError(
                    f"Service process exited with code {process.returncode}."
                )
            time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for {url}.")


def available_port(port):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def systemctl(*arguments, check=True):
    return subprocess.run(
        ["systemctl", "--user", *arguments], check=check, text=True, capture_output=True
    )


def local_env(path, config):
    return {
        **os.environ,
        **config["environment"],
        "VOXRN_DATA_DIR": str(path.parent / "data"),
        "VOXRN_DEV_TOKEN": config["token"],
        "VOXRN_LOCAL_PORT": str(config["port"]),
        "VOXRN_ALLOWED_ORIGINS": " ".join(config["origins"]),
    }


def ollama_env(path, config):
    return {
        **os.environ,
        "OLLAMA_HOST": f"127.0.0.1:{config['ollama_port']}",
        "OLLAMA_MODELS": str(path.parent / "models" / "ollama"),
        "CUDA_VISIBLE_DEVICES": "-1",
        "HIP_VISIBLE_DEVICES": "-1",
        "ROCR_VISIBLE_DEVICES": "-1",
        "OLLAMA_LLM_LIBRARY": "cpu",
        "OLLAMA_VULKAN": "0",
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NUM_PARALLEL": "1",
    }
