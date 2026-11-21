"""Installierte Programme kennen und Fenster einem Programm zuordnen (ohne GTK, nur Gio)."""

from __future__ import annotations

import threading

from gi.repository import Gio


class Catalog:
    """Alle startbaren Programme mit den Namen, unter denen ihre Fenster auftauchen (WM_CLASS)."""

    _inst = None
    _lock = threading.Lock()
    ALIASES = {"gnome-terminal-server": "gnome-terminal", "navigator": "zen", "code-url-handler": "code"}

    @classmethod
    def get(cls, refresh: bool = False) -> "Catalog":
        with cls._lock:
            if cls._inst is None or refresh:
                cls._inst = cls()
            return cls._inst

    def __init__(self):
        self.by_key: dict[str, Gio.AppInfo] = {}
        self.apps: list[tuple[str, Gio.AppInfo]] = []
        for info in Gio.AppInfo.get_all():
            if not info.should_show():
                continue
            keys = []
            if isinstance(info, Gio.DesktopAppInfo):
                wm = info.get_startup_wm_class()
                if wm:
                    keys.append(wm)
                did = (info.get_id() or "").removesuffix(".desktop")
                if did:
                    keys.append(did)
                    keys.append(did.split(".")[-1])
            exe = info.get_executable()
            if exe:
                keys.append(exe.split("/")[-1])
            if not keys:
                continue
            keys.append(info.get_display_name())
            for k in keys:
                self.by_key.setdefault(k.lower(), info)
            self.apps.append((keys[0], info))
        self.apps.sort(key=lambda t: t[1].get_display_name().casefold())

    def lookup(self, wm: str) -> Gio.AppInfo | None:
        w = self.ALIASES.get(wm.lower(), wm.lower())
        if w in self.by_key:
            return self.by_key[w]
        for k, info in self.by_key.items():
            if k.endswith("." + w) or w.endswith("." + k):
                return info
        return None

    def name(self, wm: str) -> str:
        info = self.lookup(wm)
        return info.get_display_name() if info else wm

    def icon(self, wm: str) -> Gio.Icon:
        info = self.lookup(wm)
        icon = info.get_icon() if info else None
        return icon or Gio.ThemedIcon.new("application-x-executable-symbolic")

    def matches(self, key: str, wm_class: tuple[str, ...]) -> bool:
        """Gehört ein Fenster mit dieser WM_CLASS zum Programm ``key``?"""
        if not wm_class:
            return False
        k = key.lower()
        low = [c.lower() for c in wm_class]
        if k in low or self.ALIASES.get(low[-1]) == k:
            return True
        target = self.lookup(key)
        if target is None:
            return False
        tid = target.get_id()
        return any((i := self.lookup(c)) is not None and i.get_id() == tid for c in wm_class)
