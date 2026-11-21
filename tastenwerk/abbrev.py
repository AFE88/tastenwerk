"""Getippte Tastenfolgen (z. B. „##mail“) über die XRecord-Erweiterung erkennen."""

from __future__ import annotations

import logging
import threading

from Xlib import X, XK
from Xlib.ext import record
from Xlib.protocol import rq

from .keys import RELEVANT_MASK, keysym_to_char
from .x11 import X11

log = logging.getLogger("tastenwerk")

# Tasten, die den Tippspeicher nicht verändern
_IGNORE = {XK.string_to_keysym(n) for n in (
    "Shift_L", "Shift_R", "Caps_Lock", "ISO_Level3_Shift", "Mode_switch", "Num_Lock",
    "ISO_Next_Group", "ISO_Prev_Group")}
_BACKSPACE = XK.string_to_keysym("BackSpace")
_DEAD_FIRST = 0xFE50
_DEAD_LAST = 0xFE8F
MAX_BUFFER = 64


class AbbrevWatcher(threading.Thread):
    def __init__(self, on_match, injection, case_sensitive=False):
        super().__init__(name="abbrev", daemon=True)
        self.on_match = on_match
        self.injection = injection
        self.case_sensitive = case_sensitive
        self.abbrevs: list[str] = []
        self.buf = ""
        self._lock = threading.Lock()
        self.ctrl = X11()   # steuert den Mitschnitt
        self.data = X11()   # empfängt die Ereignisse
        self.ctx = None
        # Sicherheitsnetz für Kürzel: (keycode, Modifikator-Maske) -> Combo
        self.combo_watch: dict = {}
        self.on_combo = None
        self._held: set[int] = set()
        self._mod_bits: dict[int, int] = {}
        self._last_release: dict[int, int] = {}

    def set_combo_watch(self, grabbed: dict, on_combo):
        """Kürzel zusätzlich hier erkennen – unabhängig davon, ob GNOME die Taste zuerst schnappt."""
        mods: dict[int, int] = {}
        try:
            for bit, row in enumerate(self.ctrl.d.get_modifier_mapping()):
                for kc in row:
                    if kc:
                        mods[kc] = mods.get(kc, 0) | (1 << bit)
        except Exception:
            log.debug("Modifikator-Belegung nicht lesbar", exc_info=True)
        with self._lock:
            self._mod_bits = mods
            self.combo_watch = dict(grabbed)
            self.on_combo = on_combo

    def _combo_event(self, ev):
        """Modifikatoren selbst mitzählen (Reihenfolge egal) und Kürzel erkennen."""
        kc = ev.detail
        if ev.type == X.KeyRelease:
            self._held.discard(kc)
            self._last_release[kc] = ev.time
            return
        if self._last_release.get(kc) == ev.time:
            return  # Autorepeat
        bits = self._mod_bits.get(kc)
        if bits is not None:
            self._held.add(kc)
            return
        if not self.combo_watch or self.injection.active():
            return
        mask = 0
        for h in self._held:
            mask |= self._mod_bits.get(h, 0)
        mask &= RELEVANT_MASK
        combo = self.combo_watch.get((kc, mask))
        if combo is not None and self.on_combo:
            self.on_combo(combo, ev.time)

    def set_abbreviations(self, abbrevs, case_sensitive=False):
        with self._lock:
            self.case_sensitive = case_sensitive
            # längste zuerst, damit „##ad“ vor „##a“ geprüft wird
            self.abbrevs = sorted(set(abbrevs), key=len, reverse=True)
            self.buf = ""

    def reset(self):
        with self._lock:
            self.buf = ""

    # ---------- Zeichen bestimmen ----------
    def _keysym(self, keycode: int, state: int) -> int:
        d = self.data.d
        shift = bool(state & X.ShiftMask)
        level3 = bool(state & X.Mod5Mask)
        idx = (4 if level3 else 0) + (1 if shift else 0)
        ks = d.keycode_to_keysym(keycode, idx)
        if not ks and idx:
            ks = d.keycode_to_keysym(keycode, idx & ~1)
        if not ks:
            ks = d.keycode_to_keysym(keycode, 0)
        # CapsLock bei Buchstaben berücksichtigen
        if state & X.LockMask and ks and 0x61 <= ks <= 0x7A and not shift:
            ks -= 0x20
        elif state & X.LockMask and ks and 0x41 <= ks <= 0x5A and shift:
            ks += 0x20
        return ks

    def _key(self, keycode: int, state: int):
        if self.injection.active():
            return
        ks = self._keysym(keycode, state)
        if ks in _IGNORE or (0xFFE1 <= ks <= 0xFFEE):  # Modifikatoren
            return
        if _DEAD_FIRST <= ks <= _DEAD_LAST:
            return  # Tottaste – Zeichen kommt mit der nächsten Taste
        with self._lock:
            if state & (X.ControlMask | X.Mod1Mask | X.Mod4Mask):
                self.buf = ""
                return
            if ks == _BACKSPACE:
                self.buf = self.buf[:-1]
                return
            ch = keysym_to_char(ks)
            if ch is None:
                self.buf = ""
                return
            self.buf = (self.buf + ch)[-MAX_BUFFER:]
            cmp = self.buf if self.case_sensitive else self.buf.lower()
            hit = next((a for a in self.abbrevs if cmp.endswith(a)), None)
            if hit:
                self.buf = ""
        if hit:
            self.on_match(hit)

    # ---------- XRecord ----------
    def _callback(self, reply):
        if reply.category != record.FromServer or reply.client_swapped or not reply.data:
            return
        data = reply.data
        while len(data):
            ev, data = rq.EventField(None).parse_binary_value(data, self.data.d.display, None, None)
            if ev.type in (X.KeyPress, X.KeyRelease):
                try:
                    self._combo_event(ev)
                except Exception:
                    log.exception("Fehler im Kürzel-Sicherheitsnetz")
            if ev.type == X.KeyPress:
                self._key(ev.detail, ev.state)
            elif ev.type == X.ButtonPress:
                with self._lock:
                    self.buf = ""

    def run(self):
        c = self.data.d
        if not c.has_extension("RECORD"):
            log.error("X-Server ohne RECORD-Erweiterung – Tastenfolgen sind nicht verfügbar")
            return
        self.ctx = c.record_create_context(0, [record.AllClients], [{
            "core_requests": (0, 0), "core_replies": (0, 0),
            "ext_requests": (0, 0, 0, 0), "ext_replies": (0, 0, 0, 0),
            "delivered_events": (0, 0),
            "device_events": (X.KeyPress, X.ButtonPress),
            "errors": (0, 0), "client_started": False, "client_died": False,
        }])
        try:
            self.data.d.record_enable_context(self.ctx, self._callback)
        finally:
            c.record_free_context(self.ctx)
