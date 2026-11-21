"""GTK läuft im Hauptthread; andere Threads reichen Aufgaben hierüber herein."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

_main_thread = threading.main_thread()


def run_on_main(fn, *args, timeout: float = 5.0):
    """``fn(*args)`` im GTK-Hauptthread ausführen und das Ergebnis zurückgeben."""
    if threading.current_thread() is _main_thread:
        return fn(*args)
    done = threading.Event()
    box: dict = {}

    def call():
        try:
            box["result"] = fn(*args)
        except Exception as e:  # an den Aufrufer weiterreichen
            box["error"] = e
        finally:
            done.set()
        return False

    GLib.idle_add(call)
    if not done.wait(timeout):
        raise TimeoutError("Hauptthread antwortet nicht")
    if "error" in box:
        raise box["error"]
    return box.get("result")


class Clipboard:
    """Zugriff auf die Zwischenablage (Strg+C/Strg+V) über GTK."""

    def _cb(self):
        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

    def get_text(self) -> str | None:
        return run_on_main(lambda: self._cb().wait_for_text())

    def set_text(self, text: str):
        def _set():
            cb = self._cb()
            cb.set_text(text, -1)
            cb.store()
        run_on_main(_set)
