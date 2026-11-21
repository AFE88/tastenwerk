"""Aktionen ausführen: Tasten senden, Text tippen/einfügen, Fenster, Befehle, KI."""

from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass

from . import ai
from .config import Config, Rule
from .keys import Combo, parse_sequence
from .windows import WindowOps
from .x11 import X11

log = logging.getLogger("tastenwerk")


def notify(title: str, body: str = "", urgent: bool = False):
    try:
        cmd = ["notify-send", "-a", "Tastenwerk", "-i", "input-keyboard"]
        if urgent:
            cmd += ["-u", "critical"]
        subprocess.Popen(cmd + [title, body], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


class Injection:
    """Merkt sich, wann Tastenwerk selbst Tasten sendet – damit es nicht auf sich selbst reagiert."""

    def __init__(self):
        self._until = 0.0
        self._depth = 0
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self._depth += 1

    def __exit__(self, *exc):
        with self._lock:
            self._depth -= 1
            self._until = time.monotonic() + 0.25

    def active(self) -> bool:
        return self._depth > 0 or time.monotonic() < self._until


@dataclass
class Trigger:
    kind: str           # "hotkey" | "abbr" | "gesture"
    key: str            # Rule.trigger_key
    combo: Combo | None = None
    text: str | None = None


_PLACEHOLDER = re.compile(r"\{(date|time|datetime|clipboard|cursor)(?::([^}]*))?\}")
CURSOR = "\x00CURSOR\x00"


def expand(text: str, clipboard_getter=None) -> str:
    text = text.replace("{{", "\x01").replace("}}", "\x02")
    now = dt.datetime.now()

    def repl(m):
        kind, fmt = m.group(1), m.group(2)
        if kind == "date":
            return now.strftime(fmt or "%d.%m.%Y")
        if kind == "time":
            return now.strftime(fmt or "%H:%M")
        if kind == "datetime":
            return now.strftime(fmt or "%d.%m.%Y %H:%M")
        if kind == "clipboard":
            return (clipboard_getter() if clipboard_getter else "") or ""
        if kind == "cursor":
            return CURSOR
        return m.group(0)

    return _PLACEHOLDER.sub(repl, text).replace("\x01", "{").replace("\x02", "}")


class Executor:
    def __init__(self, config: Config, injection: Injection, clipboard, passthrough=None):
        self.injection = injection
        self.clipboard = clipboard
        self.passthrough = passthrough   # fn(Combo) – Taste an das Programm durchreichen
        self.on_abbrev_done = None       # fn() – Tippspeicher leeren
        self.history = None              # clipboard.History – eigene Zugriffe nicht mitschreiben
        self.on_clipboard = None         # fn(cmd, prev_xid) – Verlaufsfenster zeigen / leeren
        self.q: queue.Queue[Trigger] = queue.Queue()
        self.x = X11()
        self.win = WindowOps(self.x)
        self.set_config(config)
        self._thread = threading.Thread(target=self._loop, name="executor", daemon=True)

    def start(self):
        self._thread.start()

    def set_config(self, config: Config):
        self.config = config
        by_key: dict[str, list[Rule]] = {}
        for r in config.enabled_rules():
            by_key.setdefault(r.trigger_key, []).append(r)
        # App-spezifische Regeln vor globalen prüfen
        for lst in by_key.values():
            lst.sort(key=lambda r: 0 if r.apps else 1)
        self.by_key = by_key

    @property
    def settings(self) -> dict:
        return self.config.settings

    def submit(self, trig: Trigger):
        self.q.put(trig)

    # ---------- Ablauf ----------
    def _loop(self):
        while True:
            trig = self.q.get()
            try:
                with self._quiet():
                    trig() if callable(trig) else self.handle(trig)
            except Exception as e:  # nie den Thread sterben lassen
                log.exception("Fehler bei %s", getattr(trig, "key", trig))
                if self.settings.get("notify", True):
                    notify("Tastenwerk: Fehler", str(e), urgent=True)

    def _quiet(self):
        import contextlib
        return self.history.quiet() if self.history is not None else contextlib.nullcontext()

    def paste_history(self, item: dict, mode: str, prev_xid: int | None):
        """Eintrag aus dem Verlauf in die Zwischenablage legen und (mode=paste) einfügen."""
        from .mainthread import run_on_main
        run_on_main(self.history.set_clipboard, item)
        run_on_main(self.history.touch, item["id"])
        if mode != "paste":
            return
        win = self.x.window(prev_xid) if prev_xid else None
        if win is not None:
            self.x.activate(win)
            for _ in range(40):
                cur = self.x.active_window()
                if cur is not None and cur.id == win.id:
                    break
                time.sleep(0.02)
            else:
                # Kein Fenstermanager reagiert – Fokus direkt setzen
                from Xlib import X
                win.set_input_focus(X.RevertToParent, X.CurrentTime)
                self.x.d.sync()
            time.sleep(0.06)
        self._app = self.x.wm_class(win) if win is not None else self.x.active_app()
        self._clear = not self.wait_modifiers_released()
        self.send_keys(self.paste_combo())

    def focus_or_launch(self, key: str, if_active: str = "next"):
        """Programm nach vorne holen; läuft es nicht, starten. Ist es schon vorne: if_active."""
        from .apps import Catalog
        from .mainthread import run_on_main
        cat = run_on_main(Catalog.get)
        wins = []
        for w in self.x.client_list(stacking=True):          # unten → oben
            try:
                if cat.matches(key, self.x.wm_class(w)):
                    wins.append(w)
            except Exception:
                continue
        if wins:
            active = self.x.active_window()
            ids = [w.id for w in wins]
            if active is not None and active.id in ids:
                if if_active == "minimize":
                    self.x.client_message(active, "WM_CHANGE_STATE", [3])   # IconicState
                elif if_active == "next" and len(wins) > 1:
                    self.x.activate(wins[0])   # das am längsten nicht benutzte – so geht es reihum
                return
            self.x.activate(wins[-1])
            return
        info = cat.lookup(key) or run_on_main(lambda: Catalog.get(refresh=True)).lookup(key)
        if info is not None:
            log.info("Starte %s", info.get_display_name())
            run_on_main(lambda: info.launch([], None))
            return
        try:
            subprocess.Popen([key], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise RuntimeError(f"Programm „{key}“ nicht gefunden") from None

    def paste_history_nr(self, n: int):
        """Eintrag Nr. n des Verlaufs (1 = zuletzt kopiert, ohne Angeheftete) direkt einfügen."""
        if self.history is None:
            raise RuntimeError("Zwischenablage-Verlauf ist nicht verfügbar")
        from .mainthread import run_on_main
        recent = run_on_main(lambda: [i for i in self.history.ordered() if not i.get("pinned")])
        if len(recent) < n:
            notify("Tastenwerk", f"Im Zwischenablage-Verlauf gibt es keinen Eintrag Nr. {n}")
            return
        prev = self.x.active_window()
        self.paste_history(recent[n - 1], "paste", prev.id if prev is not None else None)

    def resolve(self, trig: Trigger) -> tuple[Rule | None, tuple[str, ...]]:
        app = self.x.active_app()
        for r in self.by_key.get(trig.key, []):
            if r.matches_app(app):
                return r, app
        return None, app

    def handle(self, trig: Trigger):
        rule, app = self.resolve(trig)
        if rule is None:
            return
        log.info("▶ %s (%s) in %s", rule.name, rule.trigger_text, app[1] if len(app) > 1 else app)
        self._app = app
        self._clear = not self.wait_modifiers_released()
        if trig.kind == "abbr" and trig.text:
            self.send_keys(" ".join(["BackSpace"] * len(trig.text)), delay_ms=4)
        for a in rule.actions:
            self.run_action(a)
        if trig.kind == "abbr" and self.on_abbrev_done:
            self.on_abbrev_done()

    # ---------- Bausteine ----------
    def wait_modifiers_released(self, timeout: float = 1.5) -> bool:
        """Warten, bis alle Tasten (v. a. die des Auslösers) losgelassen sind. True = losgelassen.

        Sonst würden gleichzeitig gedrückte Modifikatoren die gesendeten Tasten verfälschen,
        und eine noch gedrückte Auslöser-Taste ließe sich nicht erneut tippen."""
        d = self.x.d
        mod_codes = {kc for row in d.get_modifier_mapping() for kc in row if kc}
        end = time.monotonic() + timeout
        while True:
            keymap = d.query_keymap()
            held = [kc for kc in range(8, 256) if keymap[kc // 8] & (1 << (kc % 8))]
            if not held:
                return True
            if time.monotonic() > end:
                log.debug("Tasten noch gedrückt: %s", held)
                # Nur wenn Modifikatoren festgehalten werden, müssen sie beim Senden gelöst werden
                return not any(kc in mod_codes for kc in held)
            time.sleep(0.015)

    def xdotool(self, *args: str):
        with self.injection:
            subprocess.run(["xdotool", *args], check=False)

    def _flags(self) -> list[str]:
        # Nur wenn der Nutzer Modifikatoren noch festhält, diese vorübergehend lösen
        return ["--clearmodifiers"] if getattr(self, "_clear", False) else []

    def send_keys(self, seq: str, delay_ms: int | None = None):
        combos = [c.xdotool() for c in parse_sequence(seq)]
        d = str(self.settings.get("key_delay_ms", 25) if delay_ms is None else delay_ms)
        self.xdotool("key", *self._flags(), "--delay", d, *combos)

    def is_terminal(self) -> bool:
        term = {t.lower() for t in self.settings.get("terminal_apps", [])}
        return bool({c.lower() for c in getattr(self, "_app", ())} & term)

    def paste_combo(self) -> str:
        return "ctrl+shift+v" if self.is_terminal() else "ctrl+v"

    def copy_combo(self) -> str:
        return "ctrl+shift+c" if self.is_terminal() else "ctrl+c"

    def insert_text(self, text: str, method: str = "auto"):
        text = expand(text, self.clipboard.get_text)
        back = 0
        if CURSOR in text:
            before, _, after = text.partition(CURSOR)
            text = before + after.replace(CURSOR, "")
            back = len(after.replace(CURSOR, ""))
        if method == "auto":
            long_ = len(text) >= int(self.settings.get("paste_threshold", 200))
            method = "paste" if ("\n" in text or long_) else "type"
        if method == "paste":
            self.paste_text(text)
        else:
            self.xdotool("type", *self._flags(), "--delay", str(self.settings.get("type_delay_ms", 6)), "--", text)
        if back:
            self.send_keys(" ".join(["Left"] * back), delay_ms=2)

    def paste_text(self, text: str):
        old = self.clipboard.get_text() if self.settings.get("restore_clipboard", True) else None
        self.clipboard.set_text(text)
        time.sleep(0.05)
        self.send_keys(self.paste_combo())
        if old is not None:
            time.sleep(0.3)
            self.clipboard.set_text(old)

    def copy_selection(self, mode: str = "selection", old: str | None = None) -> str | None:
        """Markierten Text kopieren. mode: selection | line (bis Zeilenanfang) | all."""
        marker = f"tastenwerk-marker-{time.time()}"
        self.clipboard.set_text(marker)
        time.sleep(0.05)
        if mode == "all":
            self.send_keys("ctrl+a")
        elif mode == "line":
            self.send_keys("shift+Home")
        self.send_keys(self.copy_combo())
        text = None
        for _ in range(20):
            time.sleep(0.05)
            cur = self.clipboard.get_text()
            if cur and cur != marker:
                text = cur
                break
        if text is None and old is not None:
            self.clipboard.set_text(old)
        return text

    def run_action(self, a: dict):
        kind = next(k for k in a if k in ("keys", "type", "paste", "run", "open", "window",
                                           "workspace", "delay", "lock", "notify", "ai", "clipboard",
                                           "screenshot", "app"))
        val = a[kind]
        if kind == "keys":
            self.send_keys(str(val))
        elif kind == "type":
            self.insert_text(str(val), a.get("method", "auto"))
        elif kind == "paste":
            self.insert_text(str(val), "paste")
        elif kind == "delay":
            time.sleep(float(val))
        elif kind == "window":
            self.win.run(str(val))
        elif kind == "workspace":
            self.win.workspace(str(val))
        elif kind == "lock":
            self.lock_screen()
        elif kind == "notify":
            notify("Tastenwerk", expand(str(val)))
        elif kind == "open":
            target = os.path.expanduser(expand(str(val)))
            subprocess.Popen(["xdg-open", target], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif kind == "run":
            self.run_command(str(val), a)
        elif kind == "ai":
            self.ai_transform(str(val), a)
        elif kind == "screenshot":
            self.send_keys(screenshot_keys(str(val)))
        elif kind == "app":
            self.focus_or_launch(str(val).strip(), a.get("if_active", "next"))
        elif kind == "clipboard":
            from .clipboard_defaults import command
            cmd, n = command(val) or ("show", 0)
            if cmd == "paste":
                self.paste_history_nr(n)
                return
            if self.on_clipboard is None:
                raise RuntimeError("Zwischenablage-Verlauf ist nicht verfügbar")
            prev = self.x.active_window()
            self.on_clipboard(cmd, prev.id if prev is not None else None)

    def run_command(self, cmd: str, a: dict):
        cmd = expand(cmd, self.clipboard.get_text)
        output = a.get("output")  # None | type | paste | notify
        if not output:
            subprocess.Popen(cmd, shell=True, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=a.get("timeout", 30))
        out = res.stdout.rstrip("\n")
        if output == "notify":
            notify("Tastenwerk", out or res.stderr.strip())
        elif out:
            self.insert_text(out.replace("{", "{{").replace("}", "}}"), "paste" if output == "paste" else "auto")

    def lock_screen(self):
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
                            "--object-path", "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.Lock"],
                           capture_output=True)
        if r.returncode != 0:
            subprocess.run(["loginctl", "lock-session"], check=False)

    def ai_transform(self, instruction: str, a: dict):
        old = self.clipboard.get_text()
        text = self.copy_selection(mode=a.get("input", "selection"), old=old)
        if not text:
            notify("Tastenwerk", "Kein Text markiert.")
            return
        notify("Tastenwerk", "Claude formuliert …")
        try:
            result = ai.transform(text, instruction, self.settings)
        except Exception:
            if old is not None:
                self.clipboard.set_text(old)
            raise
        self.clipboard.set_text(result)
        time.sleep(0.05)
        self.send_keys(self.paste_combo())
        if old is not None and self.settings.get("restore_clipboard", True):
            time.sleep(0.3)
            self.clipboard.set_text(old)


WINDOW_LABELS = {
    "left_half": "linke Hälfte", "right_half": "rechte Hälfte", "top_half": "obere Hälfte",
    "bottom_half": "untere Hälfte", "left_third": "linkes Drittel", "center_third": "mittleres Drittel",
    "right_third": "rechtes Drittel", "left_two_thirds": "linke zwei Drittel",
    "right_two_thirds": "rechte zwei Drittel", "top_left": "oben links", "top_right": "oben rechts",
    "bottom_left": "unten links", "bottom_right": "unten rechts", "maximize": "maximieren",
    "unmaximize": "Maximierung aufheben", "toggle_maximize": "maximieren an/aus",
    "minimize": "minimieren", "minimize_under_cursor": "Fenster unter Maus minimieren",
    "fullscreen": "Vollbild an/aus", "center": "zentrieren", "close": "schließen",
    "next_monitor": "auf nächsten Monitor", "prev_monitor": "auf vorherigen Monitor",
    "next_monitor_maximized": "auf nächsten Monitor, maximiert",
}


_PRETTY_MOD = {"ctrl": "Strg", "alt": "Alt", "shift": "Umschalt", "super": "Super", "altgr": "AltGr"}
_PRETTY_KEY = {"Return": "Enter", "KP_Enter": "Enter", "Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
               "BackSpace": "Rücktaste", "Delete": "Entf", "Escape": "Esc", "space": "Leertaste",
               "Prior": "Bild↑", "Next": "Bild↓", "Home": "Pos1", "End": "Ende", "Print": "Druck",
               "numbersign": "#", "less": "<", "plus": "+", "minus": "-", "period": ".", "comma": ",",
               "ssharp": "ß", "XF86AudioNext": "Nächster Titel", "XF86AudioPrev": "Voriger Titel"}


def pretty_keys(seq: str) -> str:
    """„ctrl+v tab Return“ -> „Strg+V, Tab, Enter“"""
    out = []
    for part in str(seq).split():
        try:
            c = Combo.parse(part)
        except Exception:
            out.append(part)
            continue
        from .keys import MOD_ORDER
        key = _PRETTY_KEY.get(c.key, c.key.upper() if len(c.key) == 1 else c.key)
        out.append("+".join([_PRETTY_MOD[m] for m in MOD_ORDER if m in c.mods] + [key]))
    return ", ".join(out)


_SHOT_SETTING = {"area": ("show-screenshot-ui", "Print"), "screen": ("screenshot", "shift+Print"),
                 "window": ("screenshot-window", "alt+Print")}
_ACCEL_MODS = {"shift": "shift", "control": "ctrl", "primary": "ctrl", "ctrl": "ctrl", "alt": "alt",
               "super": "super", "meta": "super"}


def screenshot_keys(mode: str) -> str:
    """GNOME-Kürzel fürs Bildschirmfoto aus den Systemeinstellungen (falls umbelegt), sonst Standard."""
    key, default = _SHOT_SETTING.get(mode, _SHOT_SETTING["area"])
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.shell.keybindings", key],
                             capture_output=True, text=True, timeout=3).stdout
        accel = re.findall(r"'([^']+)'", out)[0]
        mods = [_ACCEL_MODS.get(m.lower()) for m in re.findall(r"<(\w+)>", accel)]
        name = re.sub(r"<\w+>", "", accel)
        if name and all(mods):
            return "+".join(mods + [name])
    except (IndexError, OSError, subprocess.SubprocessError):
        pass
    return default


def pretty_text(text: str) -> str:
    """Platzhalter lesbar machen: {date} -> [Datum], {cursor} entfällt."""
    def repl(m):
        kind, fmt = m.group(1), m.group(2) or ""
        if kind == "cursor":
            return ""
        if kind == "clipboard":
            return "[Zwischenablage]"
        if kind == "date" and ("%H" in fmt or "%M" in fmt) and "%d" not in fmt:
            return "[Uhrzeit]"
        return {"date": "[Datum]", "time": "[Uhrzeit]", "datetime": "[Datum + Zeit]"}[kind]
    return _PLACEHOLDER.sub(repl, text).replace("{{", "{").replace("}}", "}")


def describe_action(a: dict) -> str:
    """Kurzbeschreibung einer Aktion für Listen und den Import-Assistenten."""
    for k, v in a.items():
        if k == "keys":
            return f"Tasten {pretty_keys(v)}"
        if k == "type":
            s = pretty_text(str(v)).replace("\n", "⏎")
            return f"Tippen „{s[:40]}{'…' if len(s) > 40 else ''}“"
        if k == "paste":
            s = pretty_text(str(v)).replace("\n", "⏎")
            return f"Einfügen „{s[:40]}{'…' if len(s) > 40 else ''}“"
        if k == "run":
            return f"Befehl {shlex.quote(str(v))[:60]}"
        if k == "open":
            return f"Öffnen {v}"
        if k == "window":
            return "Fenster: " + WINDOW_LABELS.get(str(v), str(v))
        if k == "workspace":
            return f"Arbeitsfläche: {v}"
        if k == "delay":
            return f"{v} s warten"
        if k == "lock":
            return "Bildschirm sperren"
        if k == "notify":
            return f"Hinweis „{v}“"
        if k == "app":
            try:
                from .apps import Catalog
                name = Catalog.get().name(str(v))
            except Exception:
                name = str(v)
            return f"Programm: {name}"
        if k == "screenshot":
            from .config import SCREENSHOT_MODES
            return "Bildschirmfoto: " + SCREENSHOT_MODES.get(str(v), str(v))
        if k == "clipboard":
            from .clipboard_defaults import command
            cmd, n = command(v) or ("show", 0)
            if cmd == "paste":
                return {1: "Letzte Kopie einfügen", 2: "Vorletzte Kopie einfügen"}.get(n, f"Verlaufseintrag Nr. {n} einfügen")
            return "Zwischenablage-Verlauf leeren" if cmd == "clear" else "Zwischenablage-Verlauf zeigen"
        if k == "ai":
            s = str(v).replace("\n", " ")
            return f"Claude: {s[:50]}{'…' if len(s) > 50 else ''}"
    return str(a)
