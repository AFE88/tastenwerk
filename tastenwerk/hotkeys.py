"""Globale Tastenkürzel per XGrabKey abfangen."""

from __future__ import annotations

import collections
import logging
import os
import queue
import select
import threading

from Xlib import X, XK, error as xerror
from Xlib.ext import xtest

from .keys import RELEVANT_MASK, Combo, string_to_keysym
from .x11 import X11

log = logging.getLogger("tastenwerk")

# Varianten mit/ohne NumLock (Mod2) und CapsLock, damit Kürzel immer greifen
EXTRA_MASKS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
MOD_KEYSYMS = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L",
               "altgr": "ISO_Level3_Shift"}


class HotkeyGrabber(threading.Thread):
    def __init__(self, on_trigger, injection):
        super().__init__(name="hotkeys", daemon=True)
        self.on_trigger = on_trigger
        self.injection = injection
        self.x = X11()
        self.d = self.x.d
        self.cmds: queue.Queue = queue.Queue()
        self._r, self._w = os.pipe()
        self.combos: list[Combo] = []
        self.grabbed: dict[tuple[int, int], Combo] = {}
        self.failed: list[tuple[Combo, str]] = []
        self._last_release: dict[int, int] = {}
        self._seen = collections.deque(maxlen=64)   # Zeitstempel empfangener Tastendrücke

    def saw_press(self, t: int) -> bool:
        """Hat der Grab diesen Tastendruck (X-Server-Zeit) selbst bekommen?"""
        return any(abs(t - s) <= 2 for s in list(self._seen))

    # ---------- von außen aufrufbar ----------
    def _send(self, cmd, arg=None, wait=False):
        ev = threading.Event() if wait else None
        box: dict = {}
        self.cmds.put((cmd, arg, ev, box))
        os.write(self._w, b"x")
        if ev:
            ev.wait(10)
        return box.get("result")

    def set_combos(self, combos, wait=True) -> list[tuple[Combo, str]]:
        return self._send("set", list(combos), wait=wait) or []

    def passthrough(self, combo: Combo):
        self._send("pass", combo)

    # ---------- intern ----------
    def keycodes(self, combo: Combo) -> list[int]:
        """Alle physischen Tasten, die diesen Tastennamen ohne Umschalten erzeugen."""
        ks = string_to_keysym(combo.key)
        if not ks:
            return []
        codes = sorted({kc for kc, idx in self.d.keysym_to_keycodes(ks) if idx == 0})
        if not codes:
            first = self.d.keysym_to_keycode(ks)
            codes = [first] if first else []
        return codes

    def keycode(self, combo: Combo) -> int:
        codes = self.keycodes(combo)
        return codes[0] if codes else 0

    def _grab_one(self, kc: int, combo: Combo) -> bool:
        done = []
        for extra in EXTRA_MASKS:
            catcher = xerror.CatchError(xerror.BadAccess)
            self.x.root.grab_key(kc, combo.mask | extra, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catcher)
            self.d.sync()
            if catcher.get_error():
                for m in done:
                    self.x.root.ungrab_key(kc, m)
                self.d.sync()
                return False
            done.append(combo.mask | extra)
        self.grabbed[(kc, combo.mask)] = combo
        return True

    def _grab(self, combo: Combo, only: int | None = None) -> str | None:
        codes = [only] if only else self.keycodes(combo)
        if not codes:
            return "Diese Taste gibt es auf der aktuellen Tastaturbelegung nicht"
        ok = [kc for kc in codes if self._grab_one(kc, combo)]
        if not ok:
            return "Schon von einem anderen Programm belegt (meist GNOME selbst)"
        return None

    def _ungrab(self, combo: Combo):
        for kc in self.keycodes(combo):
            for extra in EXTRA_MASKS:
                self.x.root.ungrab_key(kc, combo.mask | extra)
            self.grabbed.pop((kc, combo.mask), None)
        self.d.sync()

    def _apply(self, combos: list[Combo]):
        for c in list(self.grabbed.values()):
            self._ungrab(c)
        self.grabbed.clear()
        self.failed = []
        self.combos = combos
        for c in combos:
            err = self._grab(c)
            if err:
                self.failed.append((c, err))
                log.warning("Kürzel %s nicht verfügbar: %s", c, err)
        return self.failed

    def _do_passthrough(self, combo: Combo, kc: int | None = None):
        # Die Modifikatoren sind noch gedrückt – nur die Taste selbst erneut senden,
        # dann kommt beim Programm genau die ursprüngliche Kombination an.
        kc = kc or self.keycode(combo)
        if not kc:
            return
        for extra in EXTRA_MASKS:
            self.x.root.ungrab_key(kc, combo.mask | extra)
        keymap = self.d.query_keymap()
        still_down = bool(keymap[kc // 8] & (1 << (kc % 8)))
        mods_held = (self.x.root.query_pointer().mask & combo.mask) == combo.mask
        events: list[tuple[int, int]] = []
        if not mods_held:
            # Modifikatoren schon losgelassen: komplette Kombination senden
            mod_kcs = [self.d.keysym_to_keycode(XK.string_to_keysym(MOD_KEYSYMS[m])) for m in combo.mods]
            events += [(X.KeyPress, m) for m in mod_kcs]
            events += [(X.KeyPress, kc), (X.KeyRelease, kc)]
            events += [(X.KeyRelease, m) for m in reversed(mod_kcs)]
        elif still_down:
            # Taste noch gedrückt – ein erneutes Drücken würde verschluckt, also lösen und
            # neu drücken; das echte Loslassen folgt vom Nutzer.
            events += [(X.KeyRelease, kc), (X.KeyPress, kc)]
        else:
            events += [(X.KeyPress, kc), (X.KeyRelease, kc)]
        with self.injection:
            for typ, code in events:
                xtest.fake_input(self.d, typ, code)
            self.d.sync()
        self.grabbed.pop((kc, combo.mask), None)
        self._grab(combo, only=kc)

    def _handle_cmds(self):
        try:
            os.read(self._r, 4096)
        except BlockingIOError:
            pass
        while True:
            try:
                cmd, arg, ev, box = self.cmds.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd == "set":
                    box["result"] = self._apply(arg)
                elif cmd == "pass":
                    self._do_passthrough(arg)
            except Exception:
                log.exception("Fehler im Hotkey-Thread")
            finally:
                if ev:
                    ev.set()

    def _handle_event(self, e):
        if e.type == X.MappingNotify:
            self.d.refresh_keyboard_mapping(e)
            self._apply(self.combos)
            return
        if e.type == X.KeyRelease:
            self._last_release[e.detail] = e.time
            return
        if e.type != X.KeyPress:
            return
        log.debug("KeyPress kc=%s state=%#x time=%s", e.detail, e.state, e.time)
        self._seen.append(e.time)
        # Tastatur sofort freigeben, damit nachfolgende Tasten beim Programm ankommen
        self.d.ungrab_keyboard(X.CurrentTime)
        self.d.flush()
        if self._last_release.get(e.detail) == e.time:
            log.debug("  als Autorepeat verworfen")
            return  # Autorepeat
        combo = self.grabbed.get((e.detail, e.state & RELEVANT_MASK))
        if combo is None:
            log.debug("  kein Kürzel für state %#x", e.state & RELEVANT_MASK)
            return
        if self.injection.active():
            # Von Tastenwerk selbst gesendet – gehört dem Programm, nicht uns
            log.debug("  als eigene Eingabe durchgereicht")
            self._do_passthrough(combo, e.detail)
            return
        # on_trigger entscheidet anhand des aktiven Programms; False = nicht zuständig
        if not self.on_trigger(combo, self.x.active_app()):
            self._do_passthrough(combo, e.detail)

    def run(self):
        fd = self.d.fileno()
        while True:
            try:
                while self.d.pending_events():
                    self._handle_event(self.d.next_event())
                r, _, _ = select.select([fd, self._r], [], [], 1.0)
                if self._r in r:
                    self._handle_cmds()
            except Exception:
                log.exception("Fehler im Hotkey-Thread")
