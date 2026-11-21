"""Kleine X11/EWMH-Helfer.  Jeder Thread benutzt seine eigene Display-Verbindung."""

from __future__ import annotations

from Xlib import X, Xatom, display as xdisplay, error as xerror
from Xlib.protocol import event as xevent


class X11:
    def __init__(self, name: str | None = None):
        self.d = xdisplay.Display(name)
        self.root = self.d.screen().root
        self._atoms: dict[str, int] = {}

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def window(self, wid: int):
        return self.d.create_resource_object("window", wid)

    def prop(self, win, name: str, type_=X.AnyPropertyType):
        try:
            p = win.get_full_property(self.atom(name), type_)
        except (xerror.BadWindow, xerror.BadMatch):
            return None
        return p.value if p else None

    def active_window(self):
        v = self.prop(self.root, "_NET_ACTIVE_WINDOW", Xatom.WINDOW)
        if not v or not v[0]:
            return None
        return self.window(int(v[0]))

    def wm_class(self, win) -> tuple[str, ...]:
        if win is None:
            return ()
        try:
            cls = win.get_wm_class()
        except (xerror.BadWindow, xerror.BadMatch):
            return ()
        return tuple(cls) if cls else ()

    def active_app(self) -> tuple[str, ...]:
        return self.wm_class(self.active_window())

    def title(self, win) -> str:
        v = self.prop(win, "_NET_WM_NAME")
        if v is None:
            v = self.prop(win, "WM_NAME")
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        return str(v or "")

    def client_list(self, stacking: bool = False):
        v = self.prop(self.root, "_NET_CLIENT_LIST_STACKING" if stacking else "_NET_CLIENT_LIST", Xatom.WINDOW) or []
        return [self.window(int(w)) for w in v]

    def wm_state(self, win) -> set[str]:
        v = self.prop(win, "_NET_WM_STATE", Xatom.ATOM) or []
        names = set()
        for a in v:
            try:
                names.add(self.d.get_atom_name(a))
            except Exception:
                pass
        return names

    def client_message(self, win, type_name: str, data: list[int], target=None):
        # negative Werte (z. B. Position links vom Bildschirm) als 32-Bit-Zweierkomplement
        data = [int(v) & 0xFFFFFFFF for v in (list(data) + [0] * 5)[:5]]
        ev = xevent.ClientMessage(window=win, client_type=self.atom(type_name), data=(32, data))
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        (target or self.root).send_event(ev, event_mask=mask)
        self.d.flush()

    def set_state(self, win, action: int, *names: str):
        """action: 0 = entfernen, 1 = setzen, 2 = umschalten"""
        atoms = [self.atom(n) for n in names] + [0, 0]
        self.client_message(win, "_NET_WM_STATE", [action, atoms[0], atoms[1], 1])

    def activate(self, win):
        self.client_message(win, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])
