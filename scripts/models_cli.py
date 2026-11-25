"""Inspect or change the model selection of this VoxRN installation."""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from host import BASE, load_settings


def control(path, model_id=None, enabled=None):
    settings = load_settings(path)
    url = f"http://127.0.0.1:{settings['port']}/v1/control/models"
    data = None
    headers = {"Authorization": f"Bearer {settings['token']}"}
    if model_id is not None:
        url += f"/{quote(model_id, safe='')}"
        data = json.dumps({"enabled": enabled}).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, method="PUT" if data else "GET", headers=headers
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            value = json.load(exc)
            message = value.get("detail", value.get("title", f"HTTP {exc.code}"))
        except (ValueError, TypeError, AttributeError):
            message = f"HTTP {exc.code}"
        raise RuntimeError(message) from exc


def wait_for_model(path, model_id, enabled, seconds):
    deadline = time.monotonic() + seconds
    target = "ready" if enabled else "disabled"
    while time.monotonic() < deadline:
        current = next(
            (
                item
                for item in control(path)["models"]
                if item["identity"]["model"] == model_id
            ),
            None,
        )
        if current is None:
            raise RuntimeError("The selected model left the installed catalog.")
        if current["enabled"] != enabled:
            raise RuntimeError("Another operator changed the model's intended state.")
        if current["state"] == target:
            return current
        if current["state"] == "failed":
            raise RuntimeError(current.get("error_detail") or current["error_code"])
        time.sleep(0.4)
    raise RuntimeError("The model transition is still pending. Check modelctl list.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=BASE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    for command in ("enable", "disable"):
        action = subparsers.add_parser(command)
        action.add_argument("model_id")
        action.add_argument("--wait-seconds", type=float, default=1500)
    args = parser.parse_args()
    path = args.prefix.expanduser().resolve() / "settings.json"
    if args.command == "list":
        result = control(path)
    else:
        enabled = args.command == "enable"
        result = control(path, args.model_id, enabled)
        if args.wait_seconds > 0:
            result = wait_for_model(path, args.model_id, enabled, args.wait_seconds)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Model control failed. {exc}", file=sys.stderr)
        sys.exit(1)
