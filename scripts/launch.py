"""Launch the local inference service or its dedicated provider."""

import argparse
import os
from pathlib import Path

from host import ROOT, load_settings, local_env, ollama_env
from install_records import own_process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings", type=Path)
    parser.add_argument("--ollama", action="store_true")
    args = parser.parse_args()
    args.settings = args.settings.expanduser().resolve()
    config = load_settings(args.settings)
    own_process(
        args.settings, "ollama" if args.ollama else "api", config.get("install_id")
    )
    os.chdir(ROOT)
    if args.ollama:
        binary = config["ollama_binary"]
        os.execve(binary, [binary, "serve"], ollama_env(args.settings, config))
    binary = str(args.settings.parent / "venv" / "bin" / "python")
    os.execve(
        binary,
        [
            binary,
            "-m",
            "uvicorn",
            "service.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(config["port"]),
            "--ws-max-size",
            "32768",
            "--no-access-log",
        ],
        local_env(args.settings, config),
    )


if __name__ == "__main__":
    main()
