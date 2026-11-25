"""Configuration and ownership for an install's Cloudflare connector."""

import os
import platform
import re
import shlex
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from host import NAME, ROOT

TUNNEL_UNIT = f"{NAME}-tunnel.service"
DEFAULT_PAGES = f"https://robit-man.github.io/{NAME}/"
QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com\b")


def pages_url(value):
    url = urlsplit(value)
    loopback = url.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        (url.scheme != "https" and not (url.scheme == "http" and loopback))
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Use an HTTPS Pages URL, or a loopback URL for a local preview."
        )
    return value


def origin(value):
    url = urlsplit(value)
    return f"{url.scheme}://{url.netloc}"


def named_url(value):
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.port
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or url.hostname.endswith(".trycloudflare.com")
    ):
        raise ValueError("Use the HTTPS hostname configured for your named tunnel.")
    return origin(value)


def session_path(settings):
    return settings.parent / "run" / "tunnel-session.json"


def connector_path(settings):
    return settings.parent / "run" / "tunnel-connector.json"


def options_path(settings):
    return settings.parent / "tunnel-options.json"


def commands(settings):
    base = Path.home() / ".local" / "bin"
    for name, script in (("tunnel", "tunnel_cli.py"), ("modelctl", "models_cli.py")):
        command = (
            "/usr/bin/python3",
            "-B",
            str(ROOT / "scripts" / script),
            "--prefix",
            str(settings.parent),
        )
        yield base / f"{NAME}-{name}", f'#!/bin/sh\nexec {shlex.join(command)} "$@"\n'


def install_command(settings):
    documents = dict(commands(settings))
    for target, expected in documents.items():
        if target.is_symlink() or (target.exists() and target.read_text() != expected):
            raise RuntimeError(
                f"An existing command owns {target}. Use the repository command."
            )
    for target, expected in documents.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8")
        target.chmod(0o755)


def remove_command(settings):
    for target, expected in commands(settings):
        if (
            not target.is_symlink()
            and target.is_file()
            and target.read_text() == expected
        ):
            target.unlink()


def cloudflared_env():
    return {
        key: value for key, value in os.environ.items() if not key.startswith("TUNNEL_")
    }


def cloudflared_binary(settings):
    binary = settings.parent / "tools" / "cloudflared"
    if binary.is_file() and os.access(binary, os.X_OK):
        return str(binary)
    return shutil.which("cloudflared")


def provision_cloudflared(settings):
    if cloudflared_binary(settings):
        return
    from provision_models import verified_file

    version = "2026.8.2"
    arch, digest = {
        "x86_64": (
            "amd64",
            "fcfb02b575a52ca1af2e3267af4e1517bcdeb30ac48c834c69abaed3c0576ad2",
        ),
        "aarch64": (
            "arm64",
            "7747d94570fb390cf47dcb4f9555c193c6355cda9793f0d878d9049e5d6a7790",
        ),
    }[platform.machine()]
    binary = verified_file(
        settings.parent / "tools",
        {
            "filename": "cloudflared",
            "url": f"https://github.com/cloudflare/cloudflared/releases/download/{version}/cloudflared-linux-{arch}",
            "sha256": digest,
        },
    )
    binary.chmod(0o700)
