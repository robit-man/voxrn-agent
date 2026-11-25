"""Stop an owned VoxRN installation and remove its private models, environments and data."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from host import BASE, CONFIG, KIND, NAME, PROVIDER_UNIT, ROOT, USER_UNIT, systemctl
from install_records import install_lock, load_marker, marker_path, stop_owned_process
from host import unit_quote
from tunnel_host import TUNNEL_UNIT, remove_command, session_path


def docker_resources(command, label):
    result = subprocess.run(
        ["docker", *command, "--filter", f"label={label}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.split())


def clean_compose(settings, record):
    if (
        not shutil.which("docker")
        or not (environment := settings.with_name("compose.env")).is_file()
    ):
        return
    project = f"{NAME}-{record['id'][:12]}"
    image = f"{NAME}:install-{record['id'][:12]}"
    owner = f"io.voxrn.installation={record['id']}"
    try:
        project_label = f"com.docker.compose.project={project}"
        matches = all(
            docker_resources(command, owner) == docker_resources(command, project_label)
            for command in (
                ("ps", "-aq"),
                ("network", "ls", "-q"),
                ("volume", "ls", "-q"),
            )
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Docker is unavailable while checking this installation's Compose resources."
        ) from exc
    if not matches:
        raise RuntimeError(
            "Compose project labels do not match the installation. Its resources were left intact."
        )
    # This also handles a partially created project's labelled bridge network.
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(environment),
        "down",
        "--volumes",
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    inspect = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True
    )
    if inspect.returncode == 0:
        subprocess.run(
            ["docker", "image", "rm", image], check=True, capture_output=True, text=True
        )


def owned_units(settings):
    candidates = [TUNNEL_UNIT, USER_UNIT]
    if KIND == "agent":
        candidates.append(PROVIDER_UNIT)
    for unit in candidates:
        path = CONFIG / "systemd" / "user" / unit
        if path.is_file() and unit_quote(settings) in path.read_text():
            yield unit, path


def remove_installation(path, record):
    units = list(owned_units(path))
    desktop = CONFIG / "autostart" / f"{NAME}.desktop"
    session_path(path).unlink(missing_ok=True)
    if units:
        for unit, unit_path in units:
            if unit == USER_UNIT:
                systemctl("disable", "--now", unit)
            else:
                systemctl("stop", unit)
            unit_path.unlink()
        systemctl("daemon-reload")
    if desktop.is_file() and unit_quote(path, desktop=True) in desktop.read_text():
        desktop.unlink()
    remove_command(path)
    for role in ("tunnel", "tray", "api", "ollama"):
        stop_owned_process(path, record, role)
    clean_compose(path, record)
    shutil.rmtree(path.parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=BASE)
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the owned paths and processes without modifying them.",
    )
    args = parser.parse_args()
    prefix = args.prefix.expanduser().absolute()
    if prefix.is_symlink():
        raise RuntimeError(
            "Pass the actual installation directory instead of a symlink."
        )
    path = prefix.resolve() / "settings.json"
    if not marker_path(path).is_file():
        if not path.parent.exists():
            print(f"{NAME} is already absent at {path.parent}.")
            return
        raise RuntimeError(
            "No ownership marker was found. Re-run setup for an existing dedicated VoxRN prefix first."
        )
    record = load_marker(path)
    units = list(owned_units(path))
    desktop = CONFIG / "autostart" / f"{NAME}.desktop"
    owns_desktop = (
        desktop.is_file() and unit_quote(path, desktop=True) in desktop.read_text()
    )
    if args.plan:
        print(
            f"Remove the installation at {path.parent}, including private data and model profiles."
        )
        print(f"Owned user units: {', '.join(unit for unit, _ in units) or 'none'}")
        if owns_desktop:
            print(f"Remove {desktop}.")
        print(
            f"Stop the recorded API, tunnel, tray, and local provider processes and Compose project for {record['id'][:12]}."
        )
        return
    with install_lock(path):
        # Recheck ownership after any in-progress setup finished.
        record = load_marker(path)
        remove_installation(path, record)
    print(f"Removed {NAME} installation at {path.parent}.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Uninstall failed. {exc}", file=sys.stderr)
        sys.exit(1)
