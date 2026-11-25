"""Provision declared model assets and isolated worker environments."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from host import ROOT

PROFILE_FILE = ROOT / "scripts" / "model-profiles.json"
KEY = re.compile(r"[a-z][a-z0-9.-]{0,79}")
ENV = re.compile(r"VOXRN_[A-Z0-9_]+")
HF_PROGRAM = """
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
from service.hub_credentials import TOKEN_FILENAME, read_hub_token

repo, revision, assets, data_directory, *patterns = sys.argv[1:]
token = read_hub_token(Path(data_directory) / TOKEN_FILENAME)
auth = {"token": token, "endpoint": "https://huggingface.co"} if token else {}
print(snapshot_download(
    repo_id=repo, revision=revision, cache_dir=assets, allow_patterns=patterns, **auth
))
"""


def profiles():
    if not PROFILE_FILE.is_file():
        return {}
    specs = {}
    for profile in json.loads(PROFILE_FILE.read_text(encoding="utf-8"))["profiles"]:
        identifier = profile["id"]
        if not KEY.fullmatch(identifier) or identifier in specs:
            raise RuntimeError("Model profiles require unique lowercase IDs.")
        specs[identifier] = profile
    return specs


def parse_selection(installed, selected, feature_flags, specs):
    selection = {key: set(value) for key, value in installed.items() if key in specs}
    for key in selected:
        if key not in specs:
            raise RuntimeError(f"Unknown model profile {key}. Use --list-models.")
        selection.setdefault(key, set())
    for value in feature_flags:
        key, sep, feature = value.partition(":")
        choices = {
            asset.get("feature") for asset in specs.get(key, {}).get("assets", [])
        }
        if not sep or not feature or feature not in choices:
            raise RuntimeError(f"Unknown model feature {value}. Use --list-models.")
        selection.setdefault(key, set()).add(feature)
    return {key: sorted(values) for key, values in selection.items()}


def set_variable(result, key, value):
    if not isinstance(key, str) or not ENV.fullmatch(key) or not isinstance(value, str):
        raise RuntimeError(
            "Model profiles may set only explicit VOXRN environment variables."
        )
    if key in result and result[key] != value:
        raise RuntimeError(f"Model profiles configure conflicting values for {key}.")
    result[key] = value


def isolated_env(directory):
    temporary = directory / "tmp"
    temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        **os.environ,
        "HF_HOME": str(directory / "hf-home"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_XET_CACHE": str(directory / "xet"),
        "PIP_NO_CACHE_DIR": "1",
        "TMPDIR": str(temporary),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TORCH_HOME": str(directory / "torch"),
    }


def verified_file(directory, asset):
    filename = asset["filename"]
    url = urlsplit(asset["url"])
    expected = asset["sha256"]
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", filename)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise RuntimeError(
            "Custom model files require a pinned HTTPS URL, filename and SHA-256."
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / filename

    def verify(path):
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest() == expected

    if destination.is_file() and verify(destination):
        return destination
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run(
            [
                "curl",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--fail",
                "--location",
                "--retry",
                "3",
                "--output",
                str(temporary),
                asset["url"],
            ],
            check=True,
            timeout=900,
        )
        if not verify(temporary):
            raise RuntimeError(f"Model file {filename} failed SHA-256 verification.")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def fetch_snapshot(python, directory, asset, data_directory):
    if not re.fullmatch(r"[0-9a-f]{40}", asset["revision"]):
        raise RuntimeError("Model snapshots require full immutable Git revisions.")
    result = subprocess.run(
        [
            str(python),
            "-c",
            HF_PROGRAM,
            asset["repo"],
            asset["revision"],
            str(directory),
            str(data_directory),
            *asset["patterns"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=isolated_env(directory.parent),
        timeout=1800,
    ).stdout.strip()
    snapshot = Path(result)
    if (
        not snapshot.is_dir()
        or snapshot.name != asset["revision"]
        or not snapshot.is_relative_to(directory)
    ):
        raise RuntimeError("Model snapshot returned an unexpected artifact path.")
    return snapshot


def provision_profiles(prefix, base_python, selection, specs, report=None):
    result = {}
    for key, enabled_features in selection.items():
        profile = specs[key]
        destination = prefix / "profiles" / key
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        python = base_python
        if requirement := profile.get("requirements"):
            if report:
                report("installing_environment")
            requirement = (ROOT / requirement).resolve()
            if not requirement.is_relative_to(ROOT) or not requirement.is_file():
                raise RuntimeError(f"Requirements for {key} must reside in the repo.")
            python = destination / "venv" / "bin" / "python"
            if not python.is_file():
                subprocess.run(
                    [sys.executable, "-m", "venv", str(destination / "venv")],
                    check=True,
                )
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-r",
                    str(requirement),
                ],
                check=True,
                env=isolated_env(destination),
                timeout=1800,
            )
            key_env = "VOXRN_MODEL_PYTHON_" + re.sub(r"[-.]", "_", key.upper())
            set_variable(result, key_env, str(python))
            if alias := profile.get("python_env"):
                set_variable(result, alias, str(python))
        assets = [
            asset
            for asset in profile.get("assets", [])
            if not asset.get("feature") or asset["feature"] in enabled_features
        ]
        for index, asset in enumerate(assets):
            if report:
                report("downloading_weights", index, len(assets), "assets")
            if "url" in asset:
                artifact = verified_file(destination / "files", asset)
            else:
                artifact = fetch_snapshot(
                    base_python,
                    destination / "assets",
                    asset,
                    Path(os.environ.get("VOXRN_DATA_DIR", prefix / "data")),
                )
            set_variable(result, asset["env"], str(artifact))
        for variable, value in profile.get("environment", {}).items():
            set_variable(result, variable, value)
    return result
