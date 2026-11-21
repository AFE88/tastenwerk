"""Symbol in der oberen Leiste (AppIndicator) mit kleinem Menü.

Läuft im GTK-3-Hauptthread des Dienstes. Fehlt die Bibliothek (gir1.2-ayatanaappindicator3-0.1),
bleibt Tastenwerk einfach ohne Symbol.
"""

from __future__ import annotations

import logging
import subprocess

from .i18n import tr, trn  # noqa: E402

log = logging.getLogger("tastenwerk")

# GTK 3 und AppIndicator erst im Dienst laden – die Oberfläche (GTK 4) fragt nur available()
AI = None
Gtk = None

ICON = "tastenwerk-tray-symbolic"
ICON_PAUSED = "tastenwerk-tray-paused-symbolic"


def available() -> bool:
    """Ist die AppIndicator-Bibliothek installiert? (lädt nichts, auch nicht in der GTK-4-Oberfläche)"""
    try:
        import gi
        return bool(gi.Repository.get_default().enumerate_versions("AyatanaAppIndicator3"))
    except Exception:
        return False


def _load() -> bool:
    global AI, Gtk
    if AI is not None:
        return True
    try:
        from .mainthread import Gtk as _Gtk
        import gi
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as _AI
    except (ValueError, ImportError):
        return False
    AI, Gtk = _AI, _Gtk
    return True


class Tray:
    def __init__(self, daemon):
        self.daemon = daemon
        self.ind = None
        self.menu = None

    # ---------- Ein/aus ----------
    def set_visible(self, on: bool):
        if not on:
            if self.ind is not None:
                self.ind.set_status(AI.IndicatorStatus.PASSIVE)
            return
        if not _load():
            log.info("Tray-Symbol nicht verfügbar (Paket gir1.2-ayatanaappindicator3-0.1 fehlt)")
            return
        if self.ind is None:
            self._build()
        self.ind.set_status(AI.IndicatorStatus.ACTIVE)
        self.update()

    def _build(self):
        from .control import PROJECT
        self.ind = AI.Indicator.new("tastenwerk", ICON, AI.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_icon_theme_path(str(PROJECT / "data"))
        self.ind.set_title("Tastenwerk")
        m = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="")
        self.status_item.set_sensitive(False)
        m.append(self.status_item)
        m.append(Gtk.SeparatorMenuItem())
        self._item(m, tr("Zwischenablage-Verlauf"), lambda: self.daemon._control_command("clipboard show"))
        self.pause_item = self._item(m, tr("Pausieren"), self._toggle_pause)
        m.append(Gtk.SeparatorMenuItem())
        self._item(m, tr("Tastenwerk öffnen …"), self._open_gui)
        self._item(m, tr("Konfiguration neu laden"), lambda: self.daemon.reload())
        m.append(Gtk.SeparatorMenuItem())
        self._item(m, tr("Beenden"), self.daemon.quit)
        m.show_all()
        self.menu = m
        self.ind.set_menu(m)
        # Mittelklick auf das Symbol öffnet den Verlauf
        self.ind.set_secondary_activate_target(m.get_children()[2])

    @staticmethod
    def _item(menu, label, fn):
        it = Gtk.MenuItem(label=label)
        it.connect("activate", lambda *_: fn())
        menu.append(it)
        return it

    # ---------- Zustand ----------
    def update(self):
        if self.ind is None:
            return
        d = self.daemon
        if d.user_paused:
            self.ind.set_icon_full(ICON_PAUSED, tr("Tastenwerk pausiert"))
            self.status_item.set_label(tr("Pausiert – Kürzel ruhen"))
            self.pause_item.set_label(tr("Fortsetzen"))
        else:
            n = len(d.config.enabled_rules()) if d.config else 0
            self.ind.set_icon_full(ICON, tr("Tastenwerk aktiv"))
            self.status_item.set_label(trn("Aktiv – {n} Regel", "Aktiv – {n} Regeln", n).format(n=n))
            self.pause_item.set_label(tr("Pausieren"))

    def _toggle_pause(self):
        self.daemon.set_user_paused(not self.daemon.user_paused)

    def _open_gui(self):
        from .control import LAUNCHER
        subprocess.Popen([str(LAUNCHER), "gui"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
