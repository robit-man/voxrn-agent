"""Open, inspect, rekey and close this VoxRN Cloudflare session."""

import argparse
import fcntl
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

from host import (
    BASE,
    CONFIG,
    KIND,
    load_settings,
    systemctl,
    unit_quote,
    write_settings,
)
from install_records import load_marker
from tunnel_host import (
    DEFAULT_PAGES,
    TUNNEL_UNIT,
    cloudflared_binary,
    connector_path,
    named_url,
    options_path,
    session_path,
)


@contextmanager
def session_lock(settings):
    path = session_path(settings).with_suffix(".lock")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    handle = os.open(path, os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        os.close(handle)


def change_unit(action):
    response = systemctl(action, TUNNEL_UNIT, check=False)
    if response.returncode:
        raise RuntimeError(
            response.stderr.strip() or f"Could not {action} {TUNNEL_UNIT}."
        )


def status(settings):
    if systemctl("is-active", TUNNEL_UNIT, check=False).returncode:
        return {"state": "stopped"}
    path = connector_path(settings)
    if not path.is_file():
        return {"state": "connecting"}
    session = (
        load_settings(session_path(settings))
        if session_path(settings).is_file()
        else {}
    )
    return {
        "state": "connected",
        **load_settings(path),
        "scope": session.get("scope", "admin"),
        "expires_at": session.get("expires_at"),
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_ready(response, session):
    request = urllib.request.Request(
        response["endpoint"] + "/v1/capabilities",
        headers={"Authorization": "Bearer " + session["key"]},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=3) as result:
            return json.load(result)["service"] == KIND
    except (OSError, ValueError, TypeError, KeyError):
        return False


def current(settings, *, reachable=False):
    end = time.monotonic() + (60 if reachable else 35)
    while time.monotonic() < end:
        response = status(settings)
        if response["state"] == "connected":
            session = load_settings(session_path(settings))
            if not reachable or public_ready(response, session):
                return response, session
        if response["state"] == "stopped":
            break
        time.sleep(0.5)
    raise RuntimeError(
        f"No live tunnel route. Inspect journalctl --user -u {TUNNEL_UNIT}."
    )


def start(settings, public_endpoint=None, token_file=None, *, scope="admin", ttl=None):
    if not cloudflared_binary(settings):
        raise RuntimeError(
            "Run setup.sh --with-tunnel or install cloudflared on the service PATH."
        )
    if bool(public_endpoint) != bool(token_file):
        raise ValueError("Supply both --named-url and --token-file for a named tunnel.")
    if public_endpoint:
        endpoint = named_url(public_endpoint)
        token = token_file.expanduser().read_text(encoding="utf-8").strip()
        if (
            not token
            or len(token) > 4096
            or not token.isascii()
            or any(char.isspace() for char in token)
        ):
            raise ValueError("The Cloudflare token file must hold one token.")
        options = {"mode": "named", "endpoint": endpoint}
    else:
        token = None
        options = {"mode": "quick"}
    change_unit("stop")
    session_path(settings).unlink(missing_ok=True)
    stored_token = settings.parent / "tunnel-token"
    if token is None:
        stored_token.unlink(missing_ok=True)
    else:
        fd = os.open(
            stored_token, os.O_NOFOLLOW | os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    write_settings(options_path(settings), options)
    write_settings(
        session_path(settings),
        {
            "key": secrets.token_urlsafe(32),
            "subject": uuid.uuid4().hex,
            "scope": scope,
            "ttl": ttl,
            "expires_at": time.time() + ttl if ttl else None,
        },
    )
    try:
        change_unit("start")
        return current(settings, reachable=True)
    except (RuntimeError, OSError):
        session_path(settings).unlink(missing_ok=True)
        change_unit("stop")
        stored_token.unlink(missing_ok=True)
        raise


def stop(settings):
    session_path(settings).unlink(missing_ok=True)
    change_unit("stop")
    (settings.parent / "tunnel-token").unlink(missing_ok=True)


def rotate(settings, *, scope=None, ttl=None):
    response, session = current(settings)
    session["key"] = secrets.token_urlsafe(32)
    session["scope"] = scope or session.get("scope", "admin")
    session["ttl"] = ttl if ttl is not None else session.get("ttl")
    session["expires_at"] = time.time() + session["ttl"] if session["ttl"] else None
    write_settings(session_path(settings), session)
    return response, session


def link(settings, response, session, *, query=False, direct=False):
    page = (
        response["endpoint"] + "/docs/"
        if direct
        else load_settings(settings).get("pages_url", DEFAULT_PAGES)
    )
    params = urlencode(
        {
            "endpoint": response["endpoint"],
            "key": session["key"],
            "transport": response["mode"],
        }
    )
    return page + ("?" if query else "#") + params


def action(settings, op, args):
    with session_lock(settings):
        if op == "status":
            return json.dumps(status(settings))
        if op == "close":
            stop(settings)
            return "Session closed."
        if op == "open":
            response, session = start(
                settings,
                args.named_url,
                args.token_file,
                scope=getattr(args, "scope", None) or "admin",
                ttl=getattr(args, "ttl", None),
            )
        elif op == "rotate":
            response, session = rotate(
                settings,
                scope=getattr(args, "scope", None),
                ttl=getattr(args, "ttl", None),
            )
        else:
            response, session = current(settings)
        if (
            op in {"link", "key"}
            and session.get("expires_at") is not None
            and session["expires_at"] <= time.time()
        ):
            raise RuntimeError(
                "This session key has expired. Rotate it to issue a new key."
            )
        if op == "key":
            return session["key"]
        if op == "endpoint":
            return response["endpoint"]
        return link(
            settings,
            response,
            session,
            query=getattr(args, "query", False),
            direct=getattr(args, "direct", False),
        )


def tui(settings):
    if not shutil.which("whiptail") or not sys.stdin.isatty():
        raise RuntimeError(
            "Use an interactive terminal with whiptail, or run tunnelctl --help."
        )
    selections = (
        ("open", "Start a Quick Tunnel"),
        ("link", "Show the Pages connection link"),
        ("direct", "Show the direct console link"),
        ("endpoint", "Show the API endpoint"),
        ("key", "Show the current session key"),
        ("rotate", "Rotate the key and show the new link"),
        ("close", "Close the session"),
        ("status", "Show connector status"),
    )
    while True:
        menu = subprocess.run(
            [
                "whiptail",
                "--title",
                "VoxRN tunnel",
                "--menu",
                TUNNEL_UNIT,
                "18",
                "75",
                "7",
                *[word for item in selections for word in item],
            ],
            stderr=subprocess.PIPE,
            text=True,
        )
        if menu.returncode:
            return
        try:
            if menu.stderr == "open" and status(settings)["state"] != "stopped":
                confirm = subprocess.run(
                    [
                        "whiptail",
                        "--yesno",
                        "Replace the active tunnel session?",
                        "8",
                        "65",
                    ]
                )
                if confirm.returncode:
                    continue
            selection = "link" if menu.stderr == "direct" else menu.stderr
            message = action(
                settings,
                selection,
                argparse.Namespace(
                    named_url=None, token_file=None, direct=menu.stderr == "direct"
                ),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            message = str(exc)
        with tempfile.NamedTemporaryFile(
            dir=session_path(settings).parent, mode="w", encoding="utf-8"
        ) as display:
            display.write(message + "\n")
            display.flush()
            subprocess.run(
                ["whiptail", "--scrolltext", "--textbox", display.name, "12", "85"]
            )


def lifetime(value):
    seconds = int(value)
    if not 30 <= seconds <= 86400:
        raise argparse.ArgumentTypeError("Choose a lifetime of 30–86400 seconds.")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=BASE)
    commands = parser.add_subparsers(dest="op")
    for op in ("open", "link", "rotate", "key", "endpoint", "status", "close", "tui"):
        command = commands.add_parser(op)
        if op in {"open", "link", "rotate"}:
            command.add_argument(
                "--direct",
                action="store_true",
                help="Use the endpoint's own browser console.",
            )
            command.add_argument(
                "--query", action="store_true", help="Place the key in the URL query."
            )
        if op in {"open", "rotate"}:
            command.add_argument("--scope", choices=["admin", "inference"])
            command.add_argument(
                "--ttl", type=lifetime, help="Session key lifetime in seconds."
            )
        if op == "open":
            command.add_argument("--named-url")
            command.add_argument("--token-file", type=Path)
    args = parser.parse_args()
    settings = args.prefix.expanduser().resolve() / "settings.json"
    load_marker(settings)
    unit = CONFIG / "systemd" / "user" / TUNNEL_UNIT
    if not unit.is_file() or unit_quote(settings) not in unit.read_text():
        raise RuntimeError(
            "This prefix does not own the registered tunnel service. Run setup.sh."
        )
    if not load_settings(settings).get("tunnel_port"):
        raise RuntimeError(
            "Run setup.sh to register the tunnel service for this installation."
        )
    if args.op in {None, "tui"}:
        tui(settings)
    else:
        print(action(settings, args.op, args))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Tunnel command failed. {exc}", file=sys.stderr)
        sys.exit(1)
