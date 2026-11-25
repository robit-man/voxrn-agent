"""Desktop controls for a managed VoxRN systemd user service."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import gi

from host import (
    NAME,
    USER_UNIT,
    available_port,
    load_settings,
    systemctl,
    write_settings,
)

from install_records import own_process
from models_cli import control

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as Indicator, Gdk, Gio, GLib, Gtk  # noqa: E402


class Tray:
    def __init__(self, path):
        self.path = path
        self.indicator = Indicator.Indicator.new(
            NAME, "network-server", Indicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_title(NAME)
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.menu = Gtk.Menu()
        self.summary = self.item("Checking service", None)
        self.item("Open API console", self.open_console)
        self.item("Copy bearer token", self.copy_token)
        self.menu.append(Gtk.SeparatorMenuItem())
        for label, action in (
            ("Start service", ("start",)),
            ("Stop service", ("stop",)),
            ("Restart service", ("restart",)),
            ("Enable service at login", ("enable",)),
            ("Disable service at login", ("disable", "--now")),
        ):
            self.item(label, lambda *_, args=action: self.change(*args))
        self.item("Models", self.models)
        self.item("Settings", self.settings)
        self.item("Quit tray", lambda *_: Gtk.main_quit())
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        self.refresh()
        GLib.timeout_add_seconds(5, self.refresh)

    def item(self, label, handler):
        entry = Gtk.MenuItem(label=label)
        self.menu.append(entry)
        if handler is None:
            entry.set_sensitive(False)
        else:
            entry.connect("activate", handler)
        return entry

    def message(self, text):
        dialog = Gtk.MessageDialog(
            parent=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.set_title(NAME)
        dialog.run()
        dialog.destroy()

    def change(self, *arguments):
        result = systemctl(*arguments, USER_UNIT, check=False)
        self.refresh()
        if result.returncode:
            self.message(
                result.stderr.strip() or "systemd could not complete that action."
            )

    def refresh(self):
        running = (
            systemctl("is-active", USER_UNIT, check=False).stdout.strip() or "unknown"
        )
        enabled = (
            systemctl("is-enabled", USER_UNIT, check=False).stdout.strip() or "unknown"
        )
        self.summary.set_label(f"{NAME} · {running} · {enabled}")
        return True

    def open_console(self, *_args):
        config = load_settings(self.path)
        subprocess.Popen(["xdg-open", f"http://127.0.0.1:{config['port']}/docs/"])

    def copy_token(self, *_args):
        config = load_settings(self.path)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(config["token"], -1)

    def models(self, *_args):
        try:
            rows = control(self.path)["models"]
        except (OSError, RuntimeError, ValueError) as exc:
            self.message(f"Could not read model state. {exc}")
            return
        dialog = Gtk.Dialog(title=f"{NAME} models", flags=0)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        grid = Gtk.Grid(column_spacing=12, row_spacing=6, margin=12)
        dialog.get_content_area().pack_start(grid, True, True, 0)
        widgets = {}
        footer = Gtk.Label(xalign=0)

        def show_state(row):
            model_id = row["identity"]["model"]
            button, status = widgets[model_id]
            label = f"{model_id} · {row['state']} · {row['active_requests']} active"
            if row.get("error_code"):
                label += f" · {row['error_code']}"
            status.set_text(label)
            button.set_label("Disable" if row["enabled"] else "Enable")
            button.set_sensitive(row["state"] not in {"loading", "draining"})

        def toggle(button, model_id):
            try:
                show_state(control(self.path, model_id, button.get_label() == "Enable"))
                footer.set_text("")
            except (OSError, RuntimeError, ValueError) as exc:
                footer.set_text(str(exc))

        def refresh_models():
            try:
                for row in control(self.path)["models"]:
                    if row["identity"]["model"] in widgets:
                        show_state(row)
                footer.set_text("")
            except (OSError, RuntimeError, ValueError) as exc:
                footer.set_text(str(exc))
            return True

        for index, row in enumerate(rows):
            model_id = row["identity"]["model"]
            status = Gtk.Label(xalign=0)
            button = Gtk.Button()
            widgets[model_id] = button, status
            button.connect("clicked", toggle, model_id)
            show_state(row)
            grid.attach(status, 0, index, 1, 1)
            grid.attach(button, 1, index, 1, 1)
        if not rows:
            footer.set_text("No installed model profiles.")
        grid.attach(footer, 0, len(rows), 2, 1)
        dialog.show_all()
        refresh_timer = GLib.timeout_add_seconds(2, refresh_models)
        try:
            dialog.run()
        finally:
            GLib.source_remove(refresh_timer)
            dialog.destroy()

    def settings(self, *_args):
        saved = load_settings(self.path)
        dialog = Gtk.Dialog(title=f"{NAME} settings", flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin=14)
        box.pack_start(grid, True, True, 0)
        port = Gtk.SpinButton.new_with_range(1024, 65535, 1)
        port.set_value(saved["port"])
        origins = Gtk.Entry(text=" ".join(saved["origins"]))
        origins.set_placeholder_text("https://account.github.io")
        rotate = Gtk.CheckButton(label="Rotate the local bearer token")
        grid.attach(Gtk.Label(label="Local port", xalign=0), 0, 0, 1, 1)
        grid.attach(port, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Allowed HTTPS origins", xalign=0), 0, 1, 1, 1)
        grid.attach(origins, 1, 1, 1, 1)
        grid.attach(rotate, 0, 2, 2, 1)
        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        new_port = port.get_value_as_int()
        selected = origins.get_text().split()
        rotate_token = rotate.get_active()
        dialog.destroy()
        if new_port != saved["port"] and not available_port(new_port):
            self.message("The selected port is already in use.")
            return
        for origin in selected:
            url = urlsplit(origin)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.path
                or url.query
                or url.fragment
                or url.username
            ):
                self.message(
                    "Use space-separated HTTPS origins without paths, credentials or query strings."
                )
                return
        config = {**saved, "port": new_port, "origins": selected}
        if rotate_token:
            config["token"] = secrets.token_urlsafe(32)
        running = not systemctl("is-active", USER_UNIT, check=False).returncode
        write_settings(self.path, config)
        if running:
            result = systemctl("restart", USER_UNIT, check=False)
            if result.returncode:
                write_settings(self.path, saved)
                systemctl("restart", USER_UNIT, check=False)
                self.message(
                    result.stderr.strip()
                    or "The service did not accept the new settings."
                )
        self.refresh()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Initialize the tray and exit without taking service actions.",
    )
    args = parser.parse_args()
    if not args.settings.is_file():
        parser.error("Settings are missing. Run setup.sh first.")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        parser.error("Start the tray inside the graphical desktop session.")
    if not args.check:
        instance = Gio.Application(
            application_id=f"com.voxrn.{NAME.removeprefix('voxrn-')}.tray"
        )
        instance.register(None)
        if instance.get_is_remote():
            return
    if not args.check:
        own_process(
            args.settings.expanduser().resolve(),
            "tray",
            load_settings(args.settings).get("install_id"),
        )
    tray = Tray(args.settings)
    if args.check:
        GLib.idle_add(Gtk.main_quit)
    Gtk.main()
    tray.indicator.set_status(Indicator.IndicatorStatus.PASSIVE)


if __name__ == "__main__":
    main()
