#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    exec python3 "$repo_dir/scripts/setup_host.py" "$@"
fi
if [[ ! -r /etc/os-release ]] || ! command -v apt-get >/dev/null; then
    printf '%s\n' 'Use Ubuntu 24.04 or Debian 12 for native installation. See DEPLOY.md for Docker.' >&2
    exit 1
fi
if [[ $EUID -eq 0 ]]; then
    printf '%s\n' 'Run setup as the service owner. Setup uses sudo only for missing OS packages.' >&2
    exit 1
fi

packages=(python3 python3-venv ca-certificates curl)
profile=$(sed -n 's/.*"service": "\([^"]*\)".*/\1/p' "$repo_dir/scripts/pages.json")
case "$profile" in
    asr) packages+=(ffmpeg libgomp1);;
    tts) packages+=(libsndfile1 libgomp1);;
    agent) packages+=(zstd);;
    *) printf '%s\n' 'Missing VoxRN service profile.' >&2; exit 1;;
esac

tray=true
for argument in "$@"; do
    if [[ $argument == --no-tray || $argument == --prepare-only ]]; then tray=false; fi
done
if "$tray"; then
    packages+=(python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 xdg-utils)
    if [[ ${XDG_CURRENT_DESKTOP:-} == *GNOME* || ${XDG_CURRENT_DESKTOP:-} == *ubuntu* ]]; then
        packages+=(gnome-shell-extension-appindicator)
    fi
fi

missing=()
for package in "${packages[@]}"; do
    if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) != installed ]]; then
        missing+=("$package")
    fi
done
if (( ${#missing[@]} )); then
    if ! command -v sudo >/dev/null; then
        printf 'An administrator must install these packages: %s\n' "${missing[*]}" >&2
        exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
fi
exec /usr/bin/python3 "$repo_dir/scripts/setup_host.py" "$@"
