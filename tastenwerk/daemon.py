"""Der Hintergrunddienst: verbindet Kürzel, Tastenfolgen und Aktionen."""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

from .mainthread import GLib, Gtk, Clipboard
from . import config as cfgmod
from .abbrev import AbbrevWatcher
from .actions import Executor, Injection, Trigger, notify
from .gestures import GestureListener
from .hotkeys import HotkeyGrabber

log = logging.getLogger("tastenwerk")


class Daemon:
    def __init__(self, config_path: Path | None = None):
        self.config_path = Path(config_path or cfgmod.CONFIG_FILE)
        self.config = cfgmod.load(self.config_path)
        self.injection = Injection()
        self.clipboard = Clipboard()
        self.hotkeys = HotkeyGrabber(self._on_hotkey, self.injection)
        self.executor = Executor(self.config, self.injection, self.clipboard, passthrough=self.hotkeys.passthrough)
        self.abbrev = AbbrevWatcher(self._on_abbrev, self.injection)
        self.executor.on_abbrev_done = self.abbrev.reset
        self.gestures = GestureListener(self._on_gesture)
        from .clipboard import ClipPopup, History
        self.history = History(lambda: self.config.settings)
        self.executor.history = self.history
        self.popup = ClipPopup(self.history, self._on_clip_choice)
        self.executor.on_clipboard = self._on_clipboard
        self._mtimes = self._stat()

    # ---------- Auslöser ----------
    def _on_hotkey(self, combo, app) -> bool:
        key = "hotkey:" + str(combo).lower()
        if not any(r.matches_app(app) for r in self.executor.by_key.get(key, [])):
            return False  # Kein Treffer für dieses Programm – Taste durchreichen
        self.executor.submit(Trigger("hotkey", key, combo=combo))
        return True

    def _on_combo_seen(self, combo, t):
        """Sicherheitsnetz: Kürzel wurde gedrückt. Kam es beim Grab nicht an (GNOME hat es
        wegen der Super-Taste geschluckt), lösen wir hier selbst aus."""
        def check():
            if self.hotkeys.saw_press(t):
                return
            log.info("Kürzel %s über das Sicherheitsnetz erkannt", combo)
            self.executor.submit(Trigger("hotkey", "hotkey:" + str(combo).lower(), combo=combo))
        threading.Timer(0.25, check).start()

    def _on_abbrev(self, text):
        self.executor.submit(Trigger("abbr", "abbr:" + text, text=text))

    def _on_gesture(self, gesture):
        self.executor.submit(Trigger("gesture", "gesture:" + str(gesture)))

    def _on_clipboard(self, cmd, prev_xid):
        """Aus dem Aktions-Thread: Verlaufsfenster zeigen oder Verlauf leeren."""
        def go():
            if cmd == "clear":
                self.history.clear()
                notify("Tastenwerk", "Zwischenablage-Verlauf geleert (Angeheftete bleiben)")
            elif self.popup.is_front():
                self.popup.hide()  # zweiter Druck schließt – aber nur, wenn er wirklich vorne ist
            elif self.popup.visible():
                self.popup.prev_xid = prev_xid or self.popup.prev_xid
                self.popup.bring_to_front()
            else:
                self.popup.show(prev_xid)
            return False
        GLib.idle_add(go)

    def _on_clip_choice(self, item, mode, prev_xid):
        self.executor.submit(lambda: self.executor.paste_history(item, mode, prev_xid))

    # ---------- Konfiguration anwenden ----------
    def apply(self, announce=False):
        cfg = self.config
        combos = sorted({r.hotkey for r in cfg.enabled_rules() if r.hotkey}, key=str)
        failed = self.hotkeys.set_combos(combos)
        self.abbrev.set_combo_watch(dict(self.hotkeys.grabbed), self._on_combo_seen)
        abbrs = [r.abbreviation for r in cfg.enabled_rules() if r.abbreviation]
        self.abbrev.set_abbreviations(abbrs, bool(cfg.settings.get("abbreviation_case_sensitive")))
        gestures = {r.gesture for r in cfg.enabled_rules() if r.gesture}
        self.gestures.set_gestures(gestures, cfg.settings.get("gesture_threshold", 20))
        self.executor.set_config(cfg)
        log.info("%d Regeln aktiv: %d Kürzel, %d Tastenfolgen, %d Gesten", len(cfg.enabled_rules()),
                 len(combos), len(abbrs), len(gestures))
        for w in cfg.warnings:
            log.warning(w)
        if failed:
            lines = [f"{c}: {why}" for c, why in failed]
            log.warning("Nicht belegbar:\n  " + "\n  ".join(lines))
        if cfg.settings.get("notify", True) and (announce or failed):
            body = f"{len(combos)} Kürzel, {len(abbrs)} Tastenfolgen aktiv"
            if gestures:
                body = body.replace(" aktiv", f", {len(gestures)} Gesten aktiv")
            if failed:
                body += f"\n{len(failed)} Kürzel nicht belegbar: " + ", ".join(str(c) for c, _ in failed[:5])
            notify("Tastenwerk", body)

    def pause(self, *_):
        """Alle Kürzel und Tastenfolgen vorübergehend freigeben (z. B. beim Aufnehmen in der Oberfläche)."""
        self.hotkeys.set_combos([])
        self.abbrev.set_combo_watch({}, None)
        self.abbrev.set_abbreviations([])
        self.gestures.set_gestures([])
        log.info("pausiert")
        return True

    def resume(self, *_):
        self.apply()
        log.info("fortgesetzt")
        return True

    def _stat(self):
        out = {}
        files = list(self.config.files)
        imp = self.config_path.parent / "imported"
        if imp.is_dir():
            files += list(imp.glob("*.y*ml"))
        for f in files:
            try:
                out[str(f)] = os.stat(f).st_mtime
            except FileNotFoundError:
                pass
        return out

    def reload(self, *_):
        self.history.load()   # z. B. nach „Verlauf leeren“ in der Oberfläche
        try:
            self.config = cfgmod.load(self.config_path)
        except cfgmod.ConfigError as e:
            log.error("Konfiguration fehlerhaft, alte bleibt aktiv: %s", e)
            notify("Tastenwerk: Konfiguration fehlerhaft", str(e)[:300], urgent=True)
            self._mtimes = self._stat()
            return True
        self._mtimes = self._stat()
        self.apply(announce=True)
        return True

    def _watch(self):
        if self._stat() != self._mtimes:
            log.info("Konfiguration geändert – lade neu")
            self.reload()
        return True

    def run(self):
        Gtk.init([])
        self.hotkeys.start()
        self.executor.start()
        self.abbrev.start()
        self.history.start()
        self.apply(announce=True)
        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(2, self._watch)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self.reload)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.pause)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, self.resume)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda *a: loop.quit() or False)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *a: loop.quit() or False)
        self._start_control()
        log.info("Tastenwerk läuft (Strg+C beendet)")
        loop.run()
        self._stop_control()
        log.info("Tastenwerk beendet")

    # ---------- Steuer-Kanal (tastenwerk clipboard …) ----------
    def _start_control(self):
        from gi.repository import Gio
        from .control import SOCKET
        try:
            SOCKET.unlink()
        except FileNotFoundError:
            pass
        try:
            svc = Gio.SocketService()
            svc.add_address(Gio.UnixSocketAddress.new(str(SOCKET)), Gio.SocketType.STREAM,
                            Gio.SocketProtocol.DEFAULT, None)
            os.chmod(SOCKET, 0o600)
            svc.connect("incoming", self._on_control)
            svc.start()
            self._control = svc
        except Exception as e:
            log.warning("Steuer-Kanal nicht verfügbar (%s) – 'tastenwerk clipboard' geht dann nicht",
                        getattr(e, "message", e))

    def _stop_control(self):
        from .control import SOCKET
        try:
            SOCKET.unlink()
        except FileNotFoundError:
            pass

    def _on_control(self, _svc, conn, _src):
        from gi.repository import Gio
        try:
            line, _ = Gio.DataInputStream.new(conn.get_input_stream()).read_line_utf8(None)
        except Exception:
            line = ""
        conn.close(None)
        self._control_command((line or "").strip())
        return True

    def _control_command(self, text: str):
        from .clipboard_defaults import command
        parts = text.split(None, 1)
        if parts and parts[0] == "screenshot":
            from .config import SCREENSHOT_MODES
            mode = parts[1] if len(parts) > 1 else "area"
            if mode not in SCREENSHOT_MODES:
                log.warning("Unbekannter Bildschirmfoto-Modus: %r", mode[:30])
                return
            log.info("▶ Befehl: %s", text)
            ex = self.executor

            def shot():
                ex._app = ex.x.active_app()
                ex._clear = not ex.wait_modifiers_released()
                ex.run_action({"screenshot": mode})
            ex.submit(shot)
            return
        if not parts or parts[0] != "clipboard":
            log.warning("Unbekannter Steuer-Befehl: %r", text[:60])
            return
        cmd = command(parts[1] if len(parts) > 1 else "show")
        if cmd is None:
            log.warning("Unbekannter Zwischenablage-Befehl: %r", text[:60])
            return
        log.info("▶ Befehl: %s", text)
        ex = self.executor
        if cmd[0] == "paste":
            ex.submit(lambda: ex.paste_history_nr(cmd[1]))
        else:
            def go():
                prev = ex.x.active_window()
                self._on_clipboard(cmd[0], prev.id if prev is not None else None)
            ex.submit(go)
