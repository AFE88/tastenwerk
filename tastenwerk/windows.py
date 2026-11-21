"""Fenster andocken, maximieren, auf andere Monitore schieben (EWMH, X11)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from Xlib import X, Xatom, error as xerror

from .x11 import X11


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px, py) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    def inside(self, other: "Rect") -> bool:
        return (self.x >= other.x and self.y >= other.y and
                self.x + self.w <= other.x + other.w and self.y + self.h <= other.y + other.h)


class WindowOps:
    def __init__(self, x: X11):
        self.x = x

    # ---------- Monitore ----------
    def monitors(self) -> list[tuple[Rect, Rect]]:
        """Liste von (Monitor, Arbeitsbereich ohne Leisten), von links nach rechts."""
        try:
            mons = [Rect(m.x, m.y, m.width_in_pixels, m.height_in_pixels)
                    for m in self.x.root.xrandr_get_monitors().monitors]
        except Exception:
            g = self.x.root.get_geometry()
            mons = [Rect(0, 0, g.width, g.height)]
        desk = (self.x.prop(self.x.root, "_NET_CURRENT_DESKTOP") or [0])[0]
        was = self.x.prop(self.x.root, f"_GTK_WORKAREAS_D{desk}") or []
        areas = [Rect(*was[i:i + 4]) for i in range(0, len(was) - 3, 4)]
        out = []
        for m in mons:
            wa = next((a for a in areas if a.inside(m)), m)
            out.append((m, wa))
        out.sort(key=lambda t: (t[0].x, t[0].y))
        return out

    def monitor_index_for(self, r: Rect) -> int:
        mons = self.monitors()
        cx, cy = r.center()
        for i, (m, _) in enumerate(mons):
            if m.contains(cx, cy):
                return i
        return 0

    # ---------- Geometrie ----------
    def _extents(self, win, name) -> list[int]:
        v = self.x.prop(win, name, Xatom.CARDINAL)
        return list(v) if v and len(v) == 4 else [0, 0, 0, 0]

    def visible_rect(self, win) -> Rect:
        g = win.get_geometry()
        t = self.x.root.translate_coords(win, 0, 0)
        l, r, tp, b = self._extents(win, "_NET_FRAME_EXTENTS")
        gl, gr, gt, gb = self._extents(win, "_GTK_FRAME_EXTENTS")
        return Rect(t.x - l + gl, t.y - tp + gt, g.width + l + r - gl - gr, g.height + tp + b - gt - gb)

    def move_resize(self, win, target: Rect):
        state = self.x.wm_state(win)
        if state & {"_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ", "_NET_WM_STATE_FULLSCREEN"}:
            self.x.set_state(win, 0, "_NET_WM_STATE_FULLSCREEN")
            self.x.set_state(win, 0, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            self.x.d.sync()
            time.sleep(0.08)
        l, r, t, b = self._extents(win, "_NET_FRAME_EXTENTS")
        gl, gr, gt, gb = self._extents(win, "_GTK_FRAME_EXTENTS")
        # NorthWest-Gravity: x/y = linke obere Ecke des Rahmens, w/h = Größe des Fensterinhalts
        x = target.x - gl
        y = target.y - gt
        w = max(50, target.w - l - r + gl + gr)
        h = max(50, target.h - t - b + gt + gb)
        flags = 1 | (0xF << 8) | (2 << 12)
        self.x.client_message(win, "_NET_MOVERESIZE_WINDOW", [flags, x, y, w, h], target=self.x.root)
        self.x.d.sync()

    # ---------- Fenster finden ----------
    def active(self):
        return self.x.active_window()

    def under_cursor(self):
        p = self.x.root.query_pointer()
        cur = (self.x.prop(self.x.root, "_NET_CURRENT_DESKTOP") or [0])[0]
        for win in reversed(self.x.client_list(stacking=True)):
            try:
                types = {self.x.d.get_atom_name(a) for a in (self.x.prop(win, "_NET_WM_WINDOW_TYPE", Xatom.ATOM) or [])}
                if types & {"_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK"}:
                    continue
                if "_NET_WM_STATE_HIDDEN" in self.x.wm_state(win):
                    continue
                desk = (self.x.prop(win, "_NET_WM_DESKTOP") or [cur])[0]
                if desk not in (cur, 0xFFFFFFFF):
                    continue
                if self.visible_rect(win).contains(p.root_x, p.root_y):
                    return win
            except xerror.XError:
                continue
        return None

    # ---------- Aktionen ----------
    def run(self, op: str):
        win = self.under_cursor() if op == "minimize_under_cursor" else self.active()
        if win is None:
            raise RuntimeError("Kein Fenster gefunden")
        if op in ("minimize", "minimize_under_cursor"):
            self.x.client_message(win, "WM_CHANGE_STATE", [3])  # IconicState
            return
        if op == "close":
            self.x.client_message(win, "_NET_CLOSE_WINDOW", [X.CurrentTime, 2])
            return
        if op == "fullscreen":
            self.x.set_state(win, 2, "_NET_WM_STATE_FULLSCREEN")
            return
        if op == "maximize":
            self.x.set_state(win, 1, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            return
        if op == "unmaximize":
            self.x.set_state(win, 0, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            return
        if op == "toggle_maximize":
            self.x.set_state(win, 2, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            return

        cur = self.visible_rect(win)
        mons = self.monitors()
        idx = self.monitor_index_for(cur)
        _, wa = mons[idx]

        if op in ("next_monitor", "prev_monitor", "next_monitor_maximized"):
            if len(mons) < 2:
                return
            was_max = "_NET_WM_STATE_MAXIMIZED_VERT" in self.x.wm_state(win)
            new = (idx + (-1 if op == "prev_monitor" else 1)) % len(mons)
            _, nwa = mons[new]
            fx = (cur.x - wa.x) / max(1, wa.w)
            fy = (cur.y - wa.y) / max(1, wa.h)
            nw = min(cur.w, nwa.w)
            nh = min(cur.h, nwa.h)
            nx = nwa.x + min(int(fx * nwa.w), nwa.w - nw)
            ny = nwa.y + min(int(fy * nwa.h), nwa.h - nh)
            self.move_resize(win, Rect(max(nwa.x, nx), max(nwa.y, ny), nw, nh))
            if op == "next_monitor_maximized" or was_max:
                time.sleep(0.1)
                self.x.set_state(win, 1, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            return

        if op == "center":
            w, h = min(cur.w, wa.w), min(cur.h, wa.h)
            self.move_resize(win, Rect(wa.x + (wa.w - w) // 2, wa.y + (wa.h - h) // 2, w, h))
            return

        x, y, w, h = wa.x, wa.y, wa.w, wa.h
        hw, hh = w // 2, h // 2
        t3 = w // 3
        layouts = {
            "left_half": (x, y, hw, h),
            "right_half": (x + hw, y, w - hw, h),
            "top_half": (x, y, w, hh),
            "bottom_half": (x, y + hh, w, h - hh),
            "left_third": (x, y, t3, h),
            "center_third": (x + t3, y, t3, h),
            "right_third": (x + 2 * t3, y, w - 2 * t3, h),
            "left_two_thirds": (x, y, 2 * t3, h),
            "right_two_thirds": (x + t3, y, w - t3, h),
            "top_left": (x, y, hw, hh),
            "top_right": (x + hw, y, w - hw, hh),
            "bottom_left": (x, y + hh, hw, h - hh),
            "bottom_right": (x + hw, y + hh, w - hw, h - hh),
        }
        if op not in layouts:
            raise RuntimeError(f"Unbekannte Fenster-Aktion {op}")
        self.move_resize(win, Rect(*layouts[op]))

    def workspace(self, where: str):
        cur = (self.x.prop(self.x.root, "_NET_CURRENT_DESKTOP") or [0])[0]
        n = (self.x.prop(self.x.root, "_NET_NUMBER_OF_DESKTOPS") or [1])[0]
        if where in ("next", "right"):
            target = min(cur + 1, n - 1)
        elif where in ("prev", "left"):
            target = max(cur - 1, 0)
        else:
            target = max(0, min(int(where) - 1, n - 1))
        self.x.client_message(self.x.root, "_NET_CURRENT_DESKTOP", [target, X.CurrentTime])
