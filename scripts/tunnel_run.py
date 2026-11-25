"""Run the install's Cloudflare connector under the user service manager."""

import argparse
import signal
import subprocess
from pathlib import Path

from host import load_settings, write_settings
from install_records import own_process
from tunnel_host import (
    QUICK_URL,
    cloudflared_binary,
    cloudflared_env,
    connector_path,
    options_path,
    session_path,
)


def serve(settings):
    config = load_settings(settings)
    options = load_settings(options_path(settings))
    state = session_path(settings)
    if not state.is_file():
        raise RuntimeError("Start the session using tunnelctl.")
    own_process(settings, "tunnel", config["install_id"])
    target = connector_path(settings)
    target.unlink(missing_ok=True)
    binary = cloudflared_binary(settings)
    if not binary:
        raise RuntimeError("cloudflared is unavailable. Re-run setup.sh --with-tunnel.")
    command = [binary, "--no-autoupdate", "tunnel", "--protocol", "http2"]
    endpoint = options.get("endpoint")
    if endpoint:
        command += ["run", "--token-file", str(settings.parent / "tunnel-token")]
    else:
        command += ["--url", f"http://127.0.0.1:{config['tunnel_port']}"]
    child = subprocess.Popen(
        command,
        env=cloudflared_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    def stop(_signal, _frame):
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for line in child.stdout:
            print(line.rstrip(), flush=True)
            if not endpoint and (match := QUICK_URL.search(line)):
                endpoint = match.group()
            if endpoint and "Registered tunnel connection" in line:
                write_settings(target, {"endpoint": endpoint, "mode": options["mode"]})
        return child.wait()
    finally:
        target.unlink(missing_ok=True)
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings", type=Path)
    args = parser.parse_args()
    raise SystemExit(serve(args.settings.expanduser().resolve()))


if __name__ == "__main__":
    main()
