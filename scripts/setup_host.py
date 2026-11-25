"""Prepare model assets and install the standalone VoxRN service for the current user."""

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from host import (
    BASE,
    CONFIG,
    DEFAULT_PORT,
    KIND,
    NAME,
    PROVIDER_UNIT,
    ROOT,
    USER_UNIT,
    available_port,
    load_settings,
    local_env,
    ollama_env,
    systemctl,
    wait_endpoint,
    write_settings,
)

REQUIREMENTS = {
    "asr": "requirements-whisper.txt",
    "tts": "requirements-supertonic.txt",
    "agent": "requirements.txt",
}
ASSETS = {
    "asr": (
        "Systran/faster-whisper-base",
        "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
        "VOXRN_ASR_WHISPER_DIR",
    ),
    "tts": (
        "Supertone/supertonic",
        "b6856d033f622c63ea29441795be266a1133e227",
        ["onnx/*", "voice_styles/*", "LICENSE*"],
        "VOXRN_TTS_SUPERTONIC_DIR",
    ),
}
OLLAMA_VERSION = "0.40.18"
OLLAMA_HASHES = {
    "amd64": "b82649006cb5b54ed1237fa2d1a26a7f939a4fd03150033759acbff7ff315adf",
    "arm64": "63f01dae3b65641866991e75e2aecb7aed8a4041f4c2c3c7d92862c5a47ebed6",
}
QWEN_TAG = "qwen3:0.6b"
QWEN_DIGEST = "7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435"


def run(*argv, **options):
    return subprocess.run(argv, check=True, **options)


def prepare_venv(prefix):
    binary = prefix / "venv" / "bin" / "python"
    if not binary.exists():
        run(sys.executable, "-m", "venv", str(prefix / "venv"))
    packages = ["-r", str(ROOT / REQUIREMENTS[KIND])]
    if KIND in ASSETS:
        packages.append("huggingface-hub==0.36.0")
    run(str(binary), "-m", "pip", "install", "--disable-pip-version-check", *packages)
    return binary


def download_model(python, prefix):
    repo_id, revision, patterns, variable = ASSETS[KIND]
    model = run(
        str(python),
        "-c",
        "from huggingface_hub import snapshot_download; import sys; print(snapshot_download("
        "repo_id=sys.argv[1], revision=sys.argv[2], cache_dir=sys.argv[3], allow_patterns=sys.argv[4:]))",
        repo_id,
        revision,
        str(prefix / "models" / "hf"),
        *patterns,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    return {variable: model}


def hash_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def ollama_binary(prefix, existing):
    if existing and Path(existing).is_file():
        return existing
    if binary := shutil.which("ollama"):
        return str(Path(binary).resolve())
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise RuntimeError("The pinned Ollama bundle supports x86-64 and arm64.")
    target = prefix / "ollama"
    binary = target / "bin" / "ollama"
    if binary.is_file():
        return str(binary)
    target.mkdir(mode=0o700, exist_ok=True)
    if shutil.disk_usage(target).free < 6 * 1024**3:
        raise RuntimeError(
            "Provision at least 6 GiB free for Ollama, model weights and installation workspace."
        )
    archive_name = f"ollama-linux-{architecture}.tar.zst"
    release = f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/{archive_name}"
    with tempfile.TemporaryDirectory(dir=prefix) as temporary:
        archive = Path(temporary) / archive_name
        run(
            "curl",
            "--proto",
            "=https",
            "-fL",
            "--retry",
            "3",
            "--output",
            str(archive),
            release,
        )
        if hash_file(archive) != OLLAMA_HASHES[architecture]:
            raise RuntimeError("The Ollama release checksum did not match.")
        run(
            "tar",
            "--zstd",
            "--extract",
            "--file",
            str(archive),
            "--directory",
            str(target),
        )
    return str(binary)


def seed_model_cache(cache, destination):
    relative = Path("manifests/registry.ollama.ai/library/qwen3/0.6b")
    manifest = cache / relative
    data = json.loads(manifest.read_bytes())
    digests = [item["digest"] for item in [data["config"], *data["layers"]]]
    if f"sha256:{QWEN_DIGEST}" not in digests:
        raise RuntimeError("The model cache does not contain the pinned Qwen weights.")
    for digest in digests:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise RuntimeError("The model manifest contains a malformed digest.")
        artifact = Path("blobs") / digest.replace(":", "-")
        blob = cache / artifact
        if hash_file(blob) != digest.split(":")[1]:
            raise RuntimeError(f"Model cache blob {digest} failed its hash check.")
        target = destination / artifact
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
        try:
            shutil.copyfile(blob, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    target_manifest = destination / relative
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest, target_manifest)


def pull_pinned_model(binary, environment):
    for attempt in range(3):
        try:
            run(binary, "pull", QWEN_TAG, env=environment, timeout=600)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            print(
                f"Ollama model download attempt {attempt + 1} failed. Retrying the same checkpoint.",
                flush=True,
            )
            time.sleep(3 * (attempt + 1))


def prepare_agent(path, config, python, use_managed, model_cache):
    prefix = path.parent
    config["ollama_binary"] = ollama_binary(prefix, config.get("ollama_binary"))
    config.setdefault("ollama_port", 8314)
    base_url = f"http://127.0.0.1:{config['ollama_port']}"
    environment = ollama_env(path, config)
    process = None
    if model_cache is not None:
        if not available_port(config["ollama_port"]):
            raise RuntimeError(
                "Stop the managed Ollama provider before seeding its cache."
            )
        seed_model_cache(
            model_cache.expanduser().resolve(), prefix / "models" / "ollama"
        )
    try:
        if available_port(config["ollama_port"]):
            (prefix / "models" / "ollama").mkdir(
                mode=0o700, parents=True, exist_ok=True
            )
            with (prefix / "ollama-setup.log").open("ab") as log:
                process = subprocess.Popen(
                    [config["ollama_binary"], "serve"],
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
        elif (
            not use_managed
            or systemctl("is-active", PROVIDER_UNIT, check=False).returncode
        ):
            raise RuntimeError(
                f"Ollama port {config['ollama_port']} is occupied by another process. Choose --ollama-port."
            )
        version = wait_endpoint(f"{base_url}/api/version", seconds=30, process=process)[
            "version"
        ]
        entries = wait_endpoint(f"{base_url}/api/tags", seconds=15, process=process)[
            "models"
        ]
        if not any(
            item["name"] == QWEN_TAG and item["digest"] == QWEN_DIGEST
            for item in entries
        ):
            pull_pinned_model(config["ollama_binary"], environment)
            entries = wait_endpoint(
                f"{base_url}/api/tags", seconds=15, process=process
            )["models"]
        if not any(
            item["name"] == QWEN_TAG and item["digest"] == QWEN_DIGEST
            for item in entries
        ):
            raise RuntimeError(
                "Ollama returned a model digest other than the release pin."
            )
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    config["environment"] = {
        "VOXRN_AGENT_OLLAMA_URL": base_url,
        "VOXRN_AGENT_MODEL_TAG": QWEN_TAG,
        "VOXRN_AGENT_THINK": "false",
        "VOXRN_AGENT_MODEL_DIGEST": QWEN_DIGEST,
        "VOXRN_AGENT_ENGINE_REVISION": version,
    }
    bundle = prefix / "workflow"
    bundle.mkdir(mode=0o700, exist_ok=True)
    run(
        str(python),
        "-m",
        "examples.intake.prepare",
        "--output-dir",
        str(bundle),
        cwd=ROOT,
        env=local_env(path, config),
    )
    config["environment"]["VOXRN_AGENT_WORKFLOW_DIR"] = str(bundle)


def unit_quote(value, *, desktop=False):
    text = str(value)
    if "\n" in text or "\r" in text:
        raise RuntimeError("Installation paths cannot include line breaks.")
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if desktop:
        text = text.replace("$", "\\$").replace("`", "\\`")
    else:
        text = text.replace("$", "$$")
    return f'"{text}"'


def install_files(path, with_tray):
    units = CONFIG / "systemd" / "user"
    units.mkdir(parents=True, exist_ok=True)
    launch = " ".join(
        unit_quote(item)
        for item in (sys.executable, ROOT / "scripts" / "launch.py", path)
    )
    common = "Restart=on-failure\nRestartSec=2s\nUMask=0077\nNoNewPrivileges=yes\n"
    dependencies = ""
    if KIND == "agent":
        dependencies = f"Requires={PROVIDER_UNIT}\nAfter={PROVIDER_UNIT}\n"
        provider = (
            f"[Unit]\nDescription=VoxRN Agent local Ollama provider\nPartOf={USER_UNIT}\n"
            f"[Service]\nExecStart={launch} --ollama\n{common}"
        )
        (units / PROVIDER_UNIT).write_text(provider)
    service = (
        f"[Unit]\nDescription={NAME} inference service\n{dependencies}"
        f"[Service]\nExecStart={launch}\n{common}[Install]\nWantedBy=default.target\n"
    )
    (units / USER_UNIT).write_text(service)
    if with_tray:
        desktop = CONFIG / "autostart"
        desktop.mkdir(parents=True, exist_ok=True)
        start = " ".join(
            unit_quote(item, desktop=True)
            for item in (sys.executable, ROOT / "scripts" / "tray.py", path)
        )
        (desktop / f"{NAME}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName={NAME}\nExec={start}\nIcon=network-server\nTerminal=false\n"
        )
        return str(desktop / f"{NAME}.desktop")
    return None


def export_compose(path, config):
    env = {
        "VOXRN_INSTALL_DIR": str(path.parent),
        "VOXRN_DEV_TOKEN": config["token"],
        "VOXRN_UID": str(os.getuid()),
        "VOXRN_GID": str(os.getgid()),
        "VOXRN_PORT": str(config["port"]),
    }
    if KIND == "agent":
        env["VOXRN_AGENT_ENGINE_REVISION"] = config["environment"][
            "VOXRN_AGENT_ENGINE_REVISION"
        ]
    target = path.parent / "compose.env"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        for key, value in env.items():
            if any(char in value for char in ("'", "\\", "\n", "\r")):
                raise RuntimeError(
                    "Compose settings cannot use quotes, backslashes, or line breaks."
                )
            output.write(f"{key}='{value}'\n")
    os.replace(temporary, target)
    (path.parent / "data").mkdir(mode=0o700, exist_ok=True)
    return target


def port_number(value):
    parsed = int(value)
    if not 1024 <= parsed <= 65535:
        raise argparse.ArgumentTypeError(
            "Choose an unprivileged port from 1024 to 65535."
        )
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        type=Path,
        default=BASE,
        help="Private install, model and data directory.",
    )
    parser.add_argument("--port", type=port_number, help="Local HTTP port.")
    if KIND == "agent":
        parser.add_argument(
            "--ollama-port", type=port_number, help="Dedicated local Ollama port."
        )
    if KIND == "agent":
        parser.add_argument(
            "--ollama-model-cache",
            type=Path,
            help="Seed the pinned model from an existing Ollama cache.",
        )
    parser.add_argument(
        "--no-tray", action="store_true", help="Skip desktop autostart registration."
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Provision a launchable install without changing services.",
    )
    args = parser.parse_args()
    path = args.prefix.expanduser().resolve() / "settings.json"
    install = not args.prepare_only
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or 3.12 is required.")
    if platform.machine() not in {"x86_64", "aarch64"}:
        raise RuntimeError("Native wheels target x86-64 and arm64.")
    if install and systemctl("show-environment", check=False).returncode:
        raise RuntimeError(
            "Start a systemd user session before installing, or use --prepare-only."
        )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = path.exists()
    config = (
        load_settings(path)
        if existing
        else {"token": secrets.token_urlsafe(32), "origins": [], "environment": {}}
    )
    port = args.port or config.get("port", DEFAULT_PORT)
    if not available_port(port) and (
        not existing
        or not install
        or port != config["port"]
        or systemctl("is-active", USER_UNIT, check=False).returncode
    ):
        raise RuntimeError(f"Port {port} is occupied. Choose a free --port.")
    config["port"] = port
    maybe_ollama = getattr(args, "ollama_port", None)
    if maybe_ollama:
        config["ollama_port"] = maybe_ollama
    if KIND == "agent" and config.get("ollama_port", 8314) == port:
        raise RuntimeError("The HTTP and Ollama ports must differ.")
    print(
        f"{NAME}: provisioning {platform.machine()} CPU inference at {path.parent}.",
        flush=True,
    )
    python = prepare_venv(path.parent)
    if KIND in ASSETS:
        config["environment"] = download_model(python, path.parent)
    else:
        prepare_agent(
            path, config, python, existing and install, args.ollama_model_cache
        )
    write_settings(path, config)
    compose_env = export_compose(path, config)
    print(f"Bearer credential is stored in {path} with mode 0600.", flush=True)
    if not install:
        print(f"Docker Compose environment: {compose_env}", flush=True)
        print(
            f'Launch with: python3 "{ROOT / "scripts" / "launch.py"}" "{path}"',
            flush=True,
        )
        return
    desktop_file = install_files(path, not args.no_tray)
    systemctl("daemon-reload")
    systemctl("enable", USER_UNIT)
    systemctl("restart", USER_UNIT)
    service_url = f"http://127.0.0.1:{port}"
    wait_endpoint(f"{service_url}/readyz", seconds=180)
    catalog = wait_endpoint(
        f"{service_url}/v1/capabilities", token=config["token"], seconds=15
    )
    if catalog["service"] != KIND or not catalog["models"]:
        raise RuntimeError("The runtime did not report the expected model catalog.")
    if desktop_file:
        print(
            f"Tray startup entry installed at {desktop_file}. It is loaded on the next desktop login."
        )
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "tray.py"), str(path)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    print(f"{NAME} is ready. Open {service_url}/docs/.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Setup failed. {exc}", file=sys.stderr)
        sys.exit(1)
