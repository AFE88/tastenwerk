"""Einstellungen."""

from __future__ import annotations

import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import control  # noqa: E402


def _string_list(labels):
    return Gtk.StringList.new(list(labels))


MODELS = [("haiku", "Schnell", "Haiku – Antwort in wenigen Sekunden"),
          ("sonnet", "Ausgewogen", "Sonnet – bessere Formulierungen, etwas langsamer"),
          ("opus", "Gründlich", "Opus – beste Qualität, am langsamsten")]


class Preferences(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__(title="Einstellungen", search_enabled=False)
        self.win = window
        self.store = window.store
        s = self.store.settings

        # ---------------- Allgemein ----------------
        page = Adw.PreferencesPage(title="Allgemein", icon_name="preferences-system-symbolic")
        g = Adw.PreferencesGroup(title="Hintergrunddienst",
                                 description="Der Dienst reagiert auf deine Kürzel – auch wenn dieses Fenster geschlossen ist.")
        self.autostart = Adw.SwitchRow(title="Beim Anmelden starten", active=control.autostart_enabled())
        self.autostart.connect("notify::active", lambda r, *_: (control.set_autostart(r.get_active()),
                                                                 window.toast("Autostart " + ("an" if r.get_active() else "aus"))))
        g.add(self.autostart)
        from ..tray import available as tray_available
        tray_sub = ("Mit Menü: Zwischenablage, Pausieren, Oberfläche öffnen, Beenden" if tray_available() else
                    "Benötigt das Paket gir1.2-ayatanaappindicator3-0.1")
        self._switch(g, "Symbol in der oberen Leiste", tray_sub, "tray", s, default=True)
        log = Adw.ActionRow(title="Protokoll", subtitle="Was der Dienst zuletzt getan hat", activatable=True)
        log.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        log.connect("activated", lambda *_: self._open(control.LOG_FILE))
        g.add(log)
        page.add(g)

        g = Adw.PreferencesGroup(title="Verhalten")
        self._switch(g, "Benachrichtigungen", "Beim Start, bei Fehlern und während Claude arbeitet", "notify", s)
        self._switch(g, "Groß-/Kleinschreibung bei Tastenfolgen beachten",
                     "Aus: „##Mail“ und „##mail“ lösen beide aus", "abbreviation_case_sensitive", s)
        self._switch(g, "Zwischenablage wiederherstellen",
                     "Nach dem Einfügen von Text den vorherigen Inhalt zurückholen", "restore_clipboard", s)
        spin = Adw.SpinRow.new_with_range(1, 40, 1)
        spin.set_title("Tipp-Geschwindigkeit")
        spin.set_subtitle("Millisekunden pro Zeichen – höher, falls Programme Zeichen verschlucken")
        spin.set_value(int(s.get("type_delay_ms", 6)))
        spin.connect("notify::value", lambda r, *_: self.store.set_setting("type_delay_ms", int(r.get_value())))
        g.add(spin)
        page.add(g)

        g = Adw.PreferencesGroup(title="Dateien")
        cfg = Adw.ActionRow(title="Konfigurationsordner", subtitle=GLib.markup_escape_text(str(self.store.path.parent)),
                            activatable=True)
        cfg.add_suffix(Gtk.Image(icon_name="folder-open-symbolic"))
        cfg.connect("activated", lambda *_: self._open(self.store.path.parent))
        g.add(cfg)
        page.add(g)
        self.add(page)

        # ---------------- Zwischenablage ----------------
        self.clip_page = self._clipboard_page(s)
        self.add(self.clip_page)

        # ---------------- Claude ----------------
        page = Adw.PreferencesPage(title="Claude", icon_name="starred-symbolic")
        g = Adw.PreferencesGroup(title="Verbindung",
                                 description="Text-Umformungen laufen über das Claude-Code-Programm mit deinem Abo – ohne API-Schlüssel.")
        self.status = Adw.ActionRow(title="Prüfe …")
        self.status_icon = Gtk.Image(icon_name="content-loading-symbolic")
        self.status.add_prefix(self.status_icon)
        self.login = Gtk.Button(label="Anmelden", valign=Gtk.Align.CENTER, visible=False)
        self.login.connect("clicked", self._login)
        self.status.add_suffix(self.login)
        g.add(self.status)
        page.add(g)

        g = Adw.PreferencesGroup(title="Modell")
        ai = s.get("ai") or {}
        cur = str(ai.get("model") or "haiku").lower()
        self.model = Adw.ComboRow(title="Qualität", model=Gtk.StringList.new([m[1] for m in MODELS]))
        idx = next((i for i, m in enumerate(MODELS) if m[0] in cur), 0)
        self.model.set_selected(idx)
        self.model.set_subtitle(MODELS[idx][2])

        def model_changed(r, *_):
            m = MODELS[r.get_selected()]
            r.set_subtitle(m[2])
            self.store.set_setting("model", m[0], sub="ai")
        self.model.connect("notify::selected", model_changed)
        g.add(self.model)
        adv = Adw.ExpanderRow(title="Erweitert")
        cmd = Adw.EntryRow(title="Programm", text=str(ai.get("command", "claude")), show_apply_button=True)
        cmd.connect("apply", lambda r: (self.store.set_setting("command", r.get_text().strip() or "claude", sub="ai"),
                                        self._check_claude()))
        adv.add_row(cmd)
        to = Adw.SpinRow.new_with_range(10, 600, 10)
        to.set_title("Zeitlimit (Sekunden)")
        to.set_value(int(ai.get("timeout", 120)))
        to.connect("notify::value", lambda r, *_: self.store.set_setting("timeout", int(r.get_value()), sub="ai"))
        adv.add_row(to)
        g.add(adv)
        page.add(g)
        self.add(page)
        self._check_claude()

    def _clipboard_page(self, s):
        from ..clipboard_defaults import DEFAULTS
        c = {**DEFAULTS, **(s.get("clipboard") or {})}
        page = Adw.PreferencesPage(title="Zwischenablage", icon_name="edit-paste-symbolic")

        g = Adw.PreferencesGroup(title="Verlauf",
                                 description="Merkt sich, was du kopierst. Abrufen über eine Regel mit der "
                                             "Aktion „Zwischenablage-Verlauf“ – z. B. Super+Shift+V.")
        def sw(title, subtitle, key):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(c.get(key)))
            row.connect("notify::active", lambda r, *_: self.store.set_setting(key, r.get_active(), sub="clipboard"))
            g.add(row)
        sw("Verlauf mitschreiben", "Aus: Es wird nichts mehr gespeichert, der bisherige Verlauf bleibt", "history")
        sw("Bilder merken", "Kopierte Bilder und Bildschirmfotos", "images")
        sw("Eingefügtes nach oben", "Ein eingefügter Eintrag rückt an die Spitze", "move_to_top")
        from ..clipboard_defaults import CLEAR_SCOPES, keep_hours
        keep = [(1, "1 Stunde"), (4, "4 Stunden"), (8, "8 Stunden"), (24, "1 Tag"), (72, "3 Tage"),
                (168, "1 Woche"), (336, "2 Wochen"), (720, "30 Tage"), (2160, "90 Tage"), (8760, "1 Jahr"),
                (0, "Unbegrenzt")]
        cur = keep_hours(c)
        if not any(abs(h - cur) < 0.01 for h, _ in keep):
            keep.insert(0, (cur, f"{cur:g} Stunden"))
        days = Adw.ComboRow(title="Aufbewahren", subtitle="Ältere Einträge verschwinden automatisch · Angeheftetes bleibt immer",
                            model=_string_list([t for _, t in keep]))
        days.set_selected(next(i for i, (h, _) in enumerate(keep) if abs(h - cur) < 0.01))
        days.connect("notify::selected",
                     lambda r, *_: self.store.set_setting("keep_hours", keep[r.get_selected()][0], sub="clipboard"))
        g.add(days)
        mx = Adw.SpinRow.new_with_range(20, 5000, 10)
        mx.set_title("Höchstens Einträge")
        mx.set_value(int(c.get("max_items") or 300))
        mx.connect("notify::value", lambda r, *_: self.store.set_setting("max_items", int(r.get_value()), sub="clipboard"))
        g.add(mx)
        page.add(g)

        g = Adw.PreferencesGroup(title="Zwischenablage automatisch leeren",
                                 description="Nach der eingestellten Zeit lässt sich die letzte Kopie nicht mehr einfügen. "
                                             "„Nur Passwörter“ meint Kopien aus den Programmen unten und als Passwort "
                                             "markierte Inhalte. Kopierst du vorher etwas Neues, beginnt die Zeit neu.")
        scopes = list(CLEAR_SCOPES)
        scope = Adw.ComboRow(title="Leeren", model=_string_list(list(CLEAR_SCOPES.values())))
        scope.set_selected(scopes.index(c.get("clear_scope", "never")) if c.get("clear_scope") in scopes else 0)
        after = Adw.SpinRow.new_with_range(5, 3600, 5)
        after.set_title("Nach (Sekunden)")
        after.set_value(int(c.get("clear_after") or 60))

        def upd_after_hint(*_):
            v = int(after.get_value())
            after.set_subtitle(f"= {v // 60} Min. {v % 60} s" if v >= 60 and v % 60 else
                               (f"= {v // 60} Min." if v >= 60 else ""))
            after.set_sensitive(scope.get_selected() != 0)
        scope.connect("notify::selected", lambda r, *_: (self.store.set_setting("clear_scope", scopes[r.get_selected()],
                                                                                  sub="clipboard"), upd_after_hint()))
        after.connect("notify::value", lambda r, *_: (self.store.set_setting("clear_after", int(r.get_value()),
                                                                               sub="clipboard"), upd_after_hint()))
        upd_after_hint()
        g.add(scope)
        g.add(after)
        page.add(g)

        g = Adw.PreferencesGroup(title="Nicht mitschreiben aus",
                                 description="Kopien aus diesen Programmen landen nie im Verlauf und gelten als Passwörter – "
                                             "gedacht für Passwort-Manager.")
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                         tooltip_text="Programm hinzufügen")
        g.set_header_suffix(add)
        self._excl_group = g
        self._excl_rows = []
        self._excl = list(c.get("exclude_apps") or [])
        add.connect("clicked", self._add_exclusion)
        self._fill_exclusions()
        page.add(g)

        g = Adw.PreferencesGroup(title="Gespeicherte Einträge")
        self.clip_stats = Adw.ActionRow(title="", subtitle="Liegt nur auf diesem Rechner, nur für dich lesbar")
        clear = Gtk.Button(label="Leeren", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        clear.connect("clicked", self._clear_clipboard)
        self.clip_stats.add_suffix(clear)
        g.add(self.clip_stats)
        page.add(g)
        self._update_clip_stats()
        return page

    def _update_clip_stats(self):
        n, pinned = control.clipboard_stats()
        t = f"{n} Einträge" if n != 1 else "1 Eintrag"
        if pinned:
            t += f", davon {pinned} angeheftet"
        self.clip_stats.set_title(t)

    def _fill_exclusions(self):
        from .widgets import AppCatalog, app_image
        for r in self._excl_rows:
            self._excl_group.remove(r)
        self._excl_rows = []
        cat = AppCatalog.get()
        for wm in self._excl:
            row = Adw.ActionRow(title=GLib.markup_escape_text(cat.name(wm)), subtitle=GLib.markup_escape_text(wm))
            row.add_prefix(app_image(wm, 24))
            rm = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                            tooltip_text="Entfernen")
            rm.connect("clicked", lambda _b, w=wm: self._set_exclusions([x for x in self._excl if x != w]))
            row.add_suffix(rm)
            self._excl_group.add(row)
            self._excl_rows.append(row)

    def _set_exclusions(self, lst):
        self._excl = lst
        self.store.set_setting("exclude_apps", lst, sub="clipboard")
        self._fill_exclusions()

    def _add_exclusion(self, *_):
        from .widgets import AppPicker
        AppPicker(lambda wm: wm and self._set_exclusions(self._excl + [wm]), exclude=self._excl).present(self)

    def _clear_clipboard(self, *_):
        d = Adw.AlertDialog(heading="Verlauf leeren?",
                            body="Alle Einträge außer den angehefteten werden gelöscht. Das lässt sich nicht rückgängig machen.")
        d.add_response("cancel", "Abbrechen")
        d.add_response("clear", "Leeren")
        d.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, resp):
            if resp == "clear":
                n = control.clipboard_clear()
                self._update_clip_stats()
                self.win.toast(f"{n} Einträge gelöscht")
        d.connect("response", done)
        d.present(self)

    def _switch(self, group, title, subtitle, key, s, default=False):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(s.get(key, default)))
        row.connect("notify::active", lambda r, *_: self.store.set_setting(key, r.get_active()))
        group.add(row)

    def _open(self, path):
        if path.suffix and not path.exists():  # Protokoll gibt es erst nach dem ersten Start
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)

    def _check_claude(self):
        cmd = (self.store.settings.get("ai") or {}).get("command", "claude")

        def work():
            st = control.claude_status(cmd)
            GLib.idle_add(self._show_status, st)
        threading.Thread(target=work, daemon=True).start()

    def _show_status(self, st):
        if not st["found"]:
            self.status.set_title("Claude Code ist nicht installiert")
            self.status.set_subtitle("Im Terminal: curl -fsSL https://claude.ai/install.sh | bash")
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.status_icon.add_css_class("warning")
            self.login.set_visible(False)
        elif st["logged_in"]:
            self.status.set_title("Verbunden")
            via = "über dein Claude-Abo" if st.get("method") == "claude.ai" else f"über {st.get('method')}"
            self.status.set_subtitle(f"Angemeldet {via}")
            self.status_icon.set_from_icon_name("emblem-ok-symbolic")
            self.status_icon.add_css_class("success")
            self.login.set_visible(False)
        else:
            self.status.set_title("Nicht angemeldet")
            self.status.set_subtitle("Einmal mit deinem Claude-Konto anmelden")
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.status_icon.add_css_class("warning")
            self.login.set_visible(True)
        return False

    def _login(self, *_):
        st = control.claude_status((self.store.settings.get("ai") or {}).get("command", "claude"))
        exe = st.get("path") or "claude"
        for term in (["gnome-terminal", "--"], ["kgx", "-e"], ["x-terminal-emulator", "-e"]):
            try:
                subprocess.Popen(term + [exe, "auth", "login"], start_new_session=True)
                break
            except FileNotFoundError:
                continue
        GLib.timeout_add_seconds(5, lambda: (self._check_claude(), False)[1])
