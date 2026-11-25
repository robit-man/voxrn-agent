"""Desktop controls for a managed VoxRN systemd user service."""

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import gi

from host import (
    NAME,
    ROOT,
    USER_UNIT,
    available_port,
    load_settings,
    systemctl,
    write_settings,
)

from install_records import own_process
from models_cli import control
from tunnel_host import TUNNEL_UNIT

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as Indicator, Gdk, Gio, GLib, Gtk  # noqa: E402


class Tray:
    def __init__(self, path):
        self.path = path
        self.indicator = Indicator.Indicator.new(
            NAME, "system-run", Indicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_title(NAME)
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.menu = Gtk.Menu()
        self.summary = self.item("Checking service", None)
        self.panel = None
        self.remote_busy = False
        entry = self.item("Open controls", self.open_controls)
        self.indicator.set_secondary_activate_target(entry)
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

    def change(self, *arguments):
        result = systemctl(*arguments, USER_UNIT, check=False)
        self.refresh()
        if result.returncode:
            self.feedback.set_text(result.stderr.strip() or "Service action failed.")
        else:
            self.feedback.set_text("Service action completed.")

    def refresh(self):
        running = (
            systemctl("is-active", USER_UNIT, check=False).stdout.strip() or "unknown"
        )
        enabled = (
            systemctl("is-enabled", USER_UNIT, check=False).stdout.strip() or "unknown"
        )
        label = f"{NAME} · {running} · {enabled}"
        self.summary.set_label(label)
        if self.panel is not None and self.panel.get_visible():
            self.panel_status.set_text(label)
            self.refresh_models()
        return True

    def open_console(self, *_args):
        config = load_settings(self.path)
        subprocess.Popen(["xdg-open", f"http://127.0.0.1:{config['port']}/docs/"])

    def copy_token(self, *_args):
        config = load_settings(self.path)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(config["token"], -1)
        self.feedback.set_text("Local API token copied to the clipboard.")

    def open_controls(self, *_args):
        if self.panel is None:
            self.build_panel()
        self.panel.show_all()
        self.refresh()
        display = self.panel.get_display()
        device = display.get_default_seat().get_pointer()
        _screen, x, y = device.get_position()
        bounds = display.get_monitor_at_point(x, y).get_workarea()
        width, height = self.panel.get_size()
        self.panel.move(
            max(bounds.x, min(x - width // 2, bounds.x + bounds.width - width)),
            max(bounds.y, min(y + 8, bounds.y + bounds.height - height)),
        )
        self.panel.present()

    def build_panel(self):
        window = Gtk.Window(title=f"{NAME} controls", decorated=False)
        window.set_default_size(530, -1)
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.connect("delete-event", lambda *_: (window.hide(), True)[1])
        window.connect("focus-out-event", self.schedule_dismiss)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        window.add(panel)
        self.panel_status = Gtk.Label(xalign=0)
        panel.pack_start(self.panel_status, False, False, 0)
        self.feedback = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.widgets = {}

        def section(label, widget):
            expander = Gtk.Expander(label=label)
            expander.add(widget)
            panel.pack_start(expander, False, False, 0)
            return expander

        service = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        for label, command in (
            ("Start", ("start",)),
            ("Stop", ("stop",)),
            ("Restart", ("restart",)),
            ("Enable at login", ("enable",)),
            ("Disable at login", ("disable", "--now")),
        ):
            self.button(service, label, lambda *_, cmd=command: self.change(*cmd))
        self.button(service, "Open API console", self.open_console)
        self.button(service, "Copy local token", self.copy_token)
        section("Service", service).set_expanded(True)
        self.model_grid = Gtk.Grid(column_spacing=12, row_spacing=6, margin=6)
        self.model_section = section("Models", self.model_grid)
        self.model_section.connect("notify::expanded", lambda *_: self.refresh_models())
        remote = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        for label, command in (
            ("Open Quick Tunnel", "open"),
            ("Copy Pages link", "link"),
            ("Copy endpoint", "endpoint"),
            ("Copy direct console link", "direct"),
            ("Copy session key", "key"),
            ("Rotate key and copy link", "rotate"),
            ("Close tunnel", "close"),
            ("Tunnel status", "status"),
        ):
            self.button(remote, label, lambda *_, cmd=command: self.tunnel(cmd))
        section("Remote session", remote)
        section("Settings", self.settings_panel())
        panel.pack_start(self.feedback, False, False, 0)
        self.button(panel, "Close", lambda *_: window.hide())
        self.panel = window

    def schedule_dismiss(self, window, _event):
        def dismiss():
            if window.get_visible() and not window.has_toplevel_focus():
                window.hide()
            return False

        GLib.timeout_add(150, dismiss)
        return False

    @staticmethod
    def button(container, label, callback):
        button = Gtk.Button(label=label)
        button.connect("clicked", callback)
        container.add(button)

    def refresh_models(self):
        if self.panel is None or not self.model_section.get_expanded():
            return
        try:
            rows = control(self.path)["models"]
        except (OSError, RuntimeError, ValueError) as exc:
            self.feedback.set_text(str(exc))
            return
        for row in rows:
            model = row["identity"]["model"]
            if model not in self.widgets:
                index = len(self.widgets)
                status = Gtk.Label(xalign=0)
                button = Gtk.Button()
                button.connect("clicked", self.toggle_model, model)
                self.widgets[model] = button, status
                self.model_grid.attach(status, 0, index, 1, 1)
                self.model_grid.attach(button, 1, index, 1, 1)
                status.show()
                button.show()
            self.show_model(row)

    def show_model(self, row):
        model = row["identity"]["model"]
        button, status = self.widgets[model]
        label = f"{model} · {row['state']} · {row['active_requests']} active"
        if installation := row.get("installation"):
            if not installation["installed"]:
                label += f" · download on enable ({installation['disk_mib']} MiB disk)"
            if installation["hardware"] == "cuda":
                label += " · requires CUDA"
        if row.get("error_code"):
            label += f" · {row.get('error_detail') or row['error_code']}"
        status.set_text(label)
        button.set_label("Disable" if row["enabled"] else "Enable")
        button.set_sensitive(row["state"] != "draining")

    def toggle_model(self, button, model):
        try:
            self.show_model(control(self.path, model, button.get_label() == "Enable"))
            self.feedback.set_text("")
        except (OSError, RuntimeError, ValueError) as exc:
            self.feedback.set_text(str(exc))

    def tunnel(self, operation):
        if self.remote_busy:
            return
        if (
            operation == "open"
            and not systemctl("is-active", TUNNEL_UNIT, check=False).returncode
        ):
            self.feedback.set_text(
                "A tunnel is active. Copy its link or close it before opening another."
            )
            return
        command = (
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "tunnel_cli.py"),
            "--prefix",
            str(self.path.parent),
            operation,
        )
        if operation == "direct":
            command = (*command[:-1], "link", "--direct")
        try:
            child = Gio.Subprocess.new(
                command,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
        except GLib.Error as exc:
            self.feedback.set_text(str(exc))
            return
        self.remote_busy = True
        self.feedback.set_text("Contacting the tunnel service…")

        def finished(process, result, _unused):
            self.remote_busy = False
            try:
                _, output, error = process.communicate_utf8_finish(result)
                if not process.get_successful():
                    self.feedback.set_text(error.strip())
                elif operation in {
                    "open",
                    "link",
                    "key",
                    "rotate",
                    "endpoint",
                    "direct",
                }:
                    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(
                        output.strip(), -1
                    )
                    value = {"key": "Session key", "endpoint": "Endpoint"}.get(
                        operation, "Connection link"
                    )
                    self.feedback.set_text(f"{value} copied to the clipboard.")
                else:
                    self.feedback.set_text(output.strip())
            except GLib.Error as exc:
                self.feedback.set_text(str(exc))

        child.communicate_utf8_async(None, None, finished, None)

    def settings_panel(self):
        saved = load_settings(self.path)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin=6)
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
        save = Gtk.Button(label="Save settings")
        save.connect("clicked", self.save_settings, port, origins, rotate)
        grid.attach(save, 0, 3, 2, 1)
        return grid

    def save_settings(self, _button, port, origins, rotate):
        saved = load_settings(self.path)
        new_port = port.get_value_as_int()
        selected = origins.get_text().split()
        if new_port == saved.get("tunnel_port") or (
            new_port != saved["port"] and not available_port(new_port)
        ):
            self.feedback.set_text("The local port is already in use.")
            return
        if any(
            urlsplit(origin).scheme != "https"
            or not urlsplit(origin).netloc
            or urlsplit(origin).path
            or urlsplit(origin).query
            or urlsplit(origin).fragment
            for origin in selected
        ):
            self.feedback.set_text(
                "Enter HTTPS origins without any path, query or fragment."
            )
            return
        config = {**saved, "port": new_port, "origins": selected}
        if rotate.get_active():
            config["token"] = secrets.token_urlsafe(32)
        running = not systemctl("is-active", USER_UNIT, check=False).returncode
        write_settings(self.path, config)
        if running:
            result = systemctl("restart", USER_UNIT, check=False)
            if result.returncode:
                write_settings(self.path, saved)
                systemctl("restart", USER_UNIT, check=False)
                self.feedback.set_text(
                    result.stderr.strip()
                    or "The service did not accept the new settings."
                )
                return
        rotate.set_active(False)
        self.feedback.set_text("Settings saved.")
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
