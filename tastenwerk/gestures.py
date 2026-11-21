"""Touchpad-Gesten (Wischen/Zusammenziehen mit 3–5 Fingern) über den Touchégg-Dienst.

Zorin bringt Touchégg mit. Dessen Systemdienst liest das Touchpad aus und verteilt erkannte
Gesten über D-Bus an alle, die zuhören (GNOME-Erweiterung, Touchégg-Client, jetzt auch
Tastenwerk). Dafür braucht es weder Root-Rechte noch einen API-Schlüssel.
"""

from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("tastenwerk")

import os as _os
from .i18n import tr, trn  # noqa: E402
DBUS_ADDRESS = _os.environ.get("TASTENWERK_TOUCHEGG_ADDRESS", "unix:abstract=touchegg")
DBUS_IFACE = "io.github.joseexposito.Touchegg"
DBUS_PATH = "/io/github/joseexposito/Touchegg"

KINDS = ("swipe", "pinch")
DIRECTIONS = {"swipe": ("up", "down", "left", "right"), "pinch": ("in", "out")}
FINGERS = (3, 4, 5)
_DIR_CODE = {1: "up", 2: "down", 3: "left", 4: "right", 5: "in", 6: "out"}
_KIND_CODE = {1: "swipe", 2: "pinch"}

_ALIASES = {
    "swipe": "swipe", "wischen": "swipe", "wisch": "swipe",
    "pinch": "pinch", "zoom": "pinch", "kneifen": "pinch",
    "up": "up", "hoch": "up", "oben": "up", "rauf": "up", "↑": "up",
    "down": "down", "runter": "down", "unten": "down", "↓": "down",
    "left": "left", "links": "left", "←": "left",
    "right": "right", "rechts": "right", "→": "right",
    "in": "in", "zusammen": "in", "rein": "in",
    "out": "out", "auseinander": "out", "raus": "out",
}
ARROW = {"up": "↑", "down": "↓", "left": "←", "right": "→", "in": "→←", "out": "←→"}
DIR_LABEL = {"up": tr("nach oben"), "down": tr("nach unten"), "left": tr("nach links"), "right": tr("nach rechts"),
             "in": tr("zusammenziehen"), "out": tr("auseinanderziehen")}


class GestureError(ValueError):
    pass


@dataclass(frozen=True)
class Gesture:
    kind: str        # swipe | pinch
    direction: str   # up/down/left/right | in/out
    fingers: int

    @classmethod
    def parse(cls, text: str) -> "Gesture":
        """Versteht „swipe up 4“, „4 finger swipe up“, „4 Finger hoch wischen“, „pinch in 4“ …"""
        kind = direction = None
        fingers = None
        for tok in re.findall(r"\d+|[^\s\d,+\-_]+", str(text).lower()):
            if tok.isdigit():
                fingers = int(tok)
            elif tok in ("finger", "fingers", "f", "mit", "nach"):
                continue
            elif tok in _ALIASES:
                v = _ALIASES[tok]
                if v in KINDS:
                    kind = v
                else:
                    direction = v
            else:
                raise GestureError(f"Unbekanntes Wort in Geste {text!r}: {tok!r}")
        if direction and not kind:
            kind = "pinch" if direction in ("in", "out") else "swipe"
        if not kind or not direction or fingers is None:
            raise GestureError(f"Geste {text!r} unvollständig – Beispiel: 'swipe up 4' oder 'pinch in 4'")
        if direction not in DIRECTIONS[kind]:
            raise GestureError(f"{kind} kennt nur {', '.join(DIRECTIONS[kind])}")
        if fingers not in FINGERS:
            raise GestureError(tr("Gesten gehen mit 3, 4 oder 5 Fingern (2 Finger sind Scrollen)"))
        return cls(kind, direction, fingers)

    def __str__(self) -> str:
        return f"{self.kind} {self.direction} {self.fingers}"

    @property
    def label(self) -> str:
        if self.kind == "swipe":
            return tr("{n} Finger {direction} wischen").format(n=self.fingers, direction=DIR_LABEL[self.direction])
        return tr("{n} Finger {direction}").format(n=self.fingers, direction=DIR_LABEL[self.direction])

    @property
    def short(self) -> str:
        return f"{self.fingers}☝ {ARROW[self.direction]}"


def all_gestures() -> list[Gesture]:
    return [Gesture(k, d, f) for f in FINGERS for k in KINDS for d in DIRECTIONS[k]]


# ---------------------------------------------------------------- Belegte Gesten
TOUCHEGG_USER = Path.home() / ".config" / "touchegg" / "touchegg.conf"
TOUCHEGG_SYSTEM = Path("/usr/share/touchegg/touchegg.conf")
_TOUCHEGG_ACTIONS = {
    "SHOW_DESKTOP": tr("Schreibtisch zeigen"), "SEND_KEYS": tr("Tasten senden"), "MAXIMIZE_RESTORE_WINDOW": tr("Maximieren"),
    "MINIMIZE_WINDOW": tr("Minimieren"), "TILE_WINDOW": tr("Fenster andocken"), "FULLSCREEN_WINDOW": tr("Vollbild"),
    "CLOSE_WINDOW": tr("Fenster schließen"), "CHANGE_DESKTOP": tr("Arbeitsfläche wechseln"), "RUN_COMMAND": tr("Befehl"),
    "MOUSE_CLICK": tr("Mausklick"),
}


def _gsetting(schema: str, key: str) -> str:
    try:
        return subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def system_gestures() -> dict[Gesture, str]:
    """Gesten, die GNOME (Erweiterung „X11 Gestures“) oder Touchégg schon selbst verwenden."""
    out: dict[Gesture, str] = {}
    exts = _gsetting("org.gnome.shell", "enabled-extensions")
    if "x11gestures@" in exts:
        f = _gsetting("org.gnome.shell.extensions.x11gestures", "swipe-fingers")
        try:
            n = int(f.split()[-1])
        except (ValueError, IndexError):
            n = 3
        for d, what in (("left", tr("Arbeitsfläche wechseln")), ("right", tr("Arbeitsfläche wechseln")),
                        ("up", tr("Übersicht")), ("down", tr("Übersicht"))):
            out[Gesture("swipe", d, n)] = tr("GNOME → {what}").format(what=what)
    conf = TOUCHEGG_USER if TOUCHEGG_USER.exists() else TOUCHEGG_SYSTEM
    try:
        root = ET.parse(conf).getroot()
    except (OSError, ET.ParseError):
        return out
    for app in root.iter("application"):
        if "All" not in [a.strip() for a in (app.get("name") or "").split(",")]:
            continue
        for g in app.iter("gesture"):
            action = g.find("action")
            atype = action.get("type") if action is not None else ""
            if atype == "GNOME_SHELL":
                continue  # schon über die Erweiterung erfasst
            try:
                ges = Gesture.parse(f"{g.get('type')} {g.get('direction')} {g.get('fingers')}")
            except GestureError:
                continue
            out.setdefault(ges, "Touchégg → " + _TOUCHEGG_ACTIONS.get(atype, atype.title()))
    return out


def touchegg_available() -> bool:
    try:
        from gi.repository import Gio
        Gio.DBusConnection.new_for_address_sync(
            DBUS_ADDRESS, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT, None, None).close_sync(None)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- Zuhören
class GestureListener:
    """Hängt sich an den Touchégg-Dienst und meldet vollendete Gesten.

    Läuft im GLib-Hauptschleifen-Thread des Dienstes. Bricht die Verbindung ab (Touchégg neu
    gestartet), wird alle 5 Sekunden neu verbunden."""

    def __init__(self, callback, threshold: float = 20.0):
        self.callback = callback
        self.threshold = threshold
        self.wanted: set[Gesture] = set()
        self.conn = None
        self._retry = None
        self._warned = False

    def set_gestures(self, gestures, threshold: float | None = None):
        self.wanted = set(gestures)
        if threshold is not None:
            self.threshold = float(threshold)
        if self.wanted and self.conn is None and self._retry is None:
            self._connect()

    def _connect(self):
        from gi.repository import Gio
        self._retry = None
        try:
            conn = Gio.DBusConnection.new_for_address_sync(
                DBUS_ADDRESS, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT, None, None)
        except Exception as e:
            if not self._warned:
                log.warning("Touchpad-Gesten: Touchégg-Dienst nicht erreichbar (%s) – versuche es weiter",
                            getattr(e, "message", e))
                self._warned = True
            self._schedule_retry()
            return False
        conn.signal_subscribe(None, DBUS_IFACE, "OnGestureEnd", DBUS_PATH, None, 0, self._on_end)
        conn.connect("closed", self._on_closed)
        self.conn = conn
        self._warned = False
        log.info("Touchpad-Gesten: mit Touchégg verbunden")
        return False

    def _schedule_retry(self):
        from gi.repository import GLib
        if self._retry is None:
            self._retry = GLib.timeout_add_seconds(5, self._connect)

    def _on_closed(self, *_):
        log.info("Touchpad-Gesten: Verbindung zu Touchégg getrennt")
        self.conn = None
        if self.wanted:
            self._schedule_retry()

    def _on_end(self, conn, sender, path, iface, signal, params, *_):
        try:
            gtype, direction, percent, fingers, _device, _elapsed = params.unpack()
        except (ValueError, TypeError):
            return
        kind, d = _KIND_CODE.get(gtype), _DIR_CODE.get(direction)
        if not kind or not d or percent < self.threshold:
            return
        g = Gesture(kind, d, int(fingers))
        log.debug("Geste %s (%.0f %%)", g, percent)
        if g in self.wanted:
            self.callback(g)

    def close(self):
        self.wanted = set()
        if self.conn is not None:
            try:
                self.conn.close_sync(None)
            except Exception:
                pass
            self.conn = None
