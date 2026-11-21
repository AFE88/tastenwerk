"""Hauptfenster von Tastenwerk."""

from __future__ import annotations

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk  # noqa: E402

from .. import __version__, control  # noqa: E402
from ..actions import describe_action  # noqa: E402
from ..config import ConfigError  # noqa: E402
from ..keys import Combo, KeyError_  # noqa: E402
from ..store import RuleItem, Store  # noqa: E402
from .widgets import AppCatalog, action_tile, app_image, install_css, trigger_widget  # noqa: E402

CATEGORIES = [
    ("all", "Alle Regeln", "view-list-symbolic"),
    ("hotkeys", "Tastenkürzel", "input-keyboard-symbolic"),
    ("abbr", "Tastenfolgen", "insert-text-symbolic"),
    ("gestures", "Touchpad-Gesten", "input-touchpad-symbolic"),
    ("window", "Fenster", "view-dual-symbolic"),
    ("ai", "Mit Claude", "starred-symbolic"),
    ("review", "Zu prüfen", "dialog-warning-symbolic"),
    ("off", "Ausgeschaltet", "action-unavailable-symbolic"),
]


def esc(s) -> str:
    return GLib.markup_escape_text(str(s))


def matches(cat: str, it: RuleItem) -> bool:
    kinds = {k for a in it.actions for k in a}
    if cat == "all":
        return True
    if cat == "hotkeys":
        return bool(it.hotkey)
    if cat == "abbr":
        return bool(it.abbreviation)
    if cat == "gestures":
        return bool(it.gesture)
    if cat == "window":
        return bool(kinds & {"window", "workspace"})
    if cat == "ai":
        return "ai" in kinds
    if cat == "review":
        return bool(it.notes)
    if cat == "off":
        return not it.enabled
    if cat == "global":
        return not it.apps
    if cat.startswith("app:"):
        return cat[4:].lower() in {a.lower() for a in it.apps}
    return True


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Tastenwerk", default_width=1080, default_height=760)
        self.set_size_request(360, 420)
        self.category = "all"
        self.query = ""
        self.system_shortcuts: dict | None = None
        self.system_gestures: dict | None = None
        try:
            self.store = Store()
            self.load_error = None
        except ConfigError as e:
            self.store = None
            self.load_error = str(e)

        self.toast_overlay = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=230, max_sidebar_width=280, sidebar_width_fraction=0.24)
        self.toast_overlay.set_child(self.split)
        self.set_content(self.toast_overlay)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 680sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())

        if self.load_error:
            self._show_load_error()
            return
        self.refresh()
        self._poll_status()
        GLib.timeout_add_seconds(2, self._tick)
        threading.Thread(target=self._load_system_shortcuts, daemon=True).start()

    # ================================================================ Aufbau
    def _build_sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Aus BetterTouchTool importieren …", "app.import")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Einstellungen", "app.preferences")
        sec.append("Tastenkürzel von Tastenwerk", "app.shortcuts")
        sec.append("Über Tastenwerk", "app.about")
        menu.append_section(None, sec)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Hauptmenü", primary=True)
        hb.pack_end(mb)
        title = Adw.WindowTitle(title="Tastenwerk")
        hb.set_title_widget(title)
        tv.add_top_bar(hb)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.cat_list = Gtk.ListBox(css_classes=["navigation-sidebar"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.cat_list.connect("row-selected", self._on_cat_selected)
        box.append(self.cat_list)
        self.app_heading = Gtk.Label(label="PROGRAMME", xalign=0, css_classes=["sidebar-heading"])
        box.append(self.app_heading)
        self.app_list = Gtk.ListBox(css_classes=["navigation-sidebar"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.app_list.connect("row-selected", self._on_cat_selected)
        box.append(self.app_list)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
        tv.set_content(sc)

        # Status des Hintergrunddienstes
        card = Gtk.Box(spacing=10, css_classes=["status-card"], margin_start=10, margin_end=10, margin_bottom=10,
                       margin_top=6)
        self.dot = Gtk.Box(css_classes=["status-dot"], valign=Gtk.Align.CENTER)
        card.append(self.dot)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.status_title = Gtk.Label(xalign=0, css_classes=["heading"])
        self.status_sub = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        v.append(self.status_title)
        v.append(self.status_sub)
        card.append(v)
        self.status_switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Hintergrunddienst starten/stoppen")
        self._switch_handler = self.status_switch.connect("state-set", self._on_status_switch)
        card.append(self.status_switch)
        tv.add_bottom_bar(card)
        return Adw.NavigationPage(title="Tastenwerk", child=tv)

    def _build_content(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.content_title = Adw.WindowTitle(title="Alle Regeln")
        hb.set_title_widget(self.content_title)
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Suchen (Strg+F)")
        hb.pack_start(self.search_btn)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Neue Regel (Strg+N)")
        add.connect("clicked", lambda *_: self.new_rule())
        hb.pack_end(add)
        tv.add_top_bar(hb)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Name, Kürzel, Text oder Programm", hexpand=True)
        clamp = Adw.Clamp(maximum_size=600, child=self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.search_entry.connect("search-changed", self._on_search)
        tv.add_top_bar(self.search_bar)

        self.banner = Adw.Banner(title="Tastenwerk ist gestoppt – Kürzel, Tastenfolgen und Gesten reagieren gerade nicht.",
                                 button_label="Starten")
        self.banner.connect("button-clicked", lambda *_: self._start_daemon())
        tv.add_top_bar(self.banner)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        tv.set_content(self.scroller)
        self.content_page = Adw.NavigationPage(title="Regeln", child=tv)
        return self.content_page

    # ================================================================ Befüllen
    def refresh(self):
        if not self.store:
            return
        self._fill_sidebar()
        self._fill_content()

    def _cat_row(self, key, label, icon=None, gicon_wm=None, count=0):
        row = Gtk.ListBoxRow()
        row.cat = key
        h = Gtk.Box(spacing=12, margin_top=2, margin_bottom=2)
        if gicon_wm:
            h.append(app_image(gicon_wm, 18))
        else:
            h.append(Gtk.Image(icon_name=icon))
        h.append(Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=3))
        row.count_label = Gtk.Label(label=str(count) if count else "", css_classes=["count"])
        h.append(row.count_label)
        row.set_child(h)
        return row

    def _sidebar_entries(self):
        items = self.store.items
        cats = []
        for key, label, icon in CATEGORIES:
            n = sum(1 for it in items if matches(key, it))
            if key in ("ai", "review", "off", "window") and n == 0 and self.category != key:
                continue
            cats.append((key, label, icon, None, n))
        apps: dict[str, int] = {}
        for it in items:
            for a in it.apps:
                apps[a] = apps.get(a, 0) + 1
        cat = AppCatalog.get()
        app_entries = [("global", "Überall", "applications-system-symbolic", None, sum(1 for it in items if not it.apps))]
        for a in sorted(apps, key=lambda x: cat.name(x).casefold()):
            app_entries.append(("app:" + a, cat.name(a), None, a, apps[a]))
        return cats, app_entries

    def _fill_sidebar(self):
        cats, app_entries = self._sidebar_entries()
        if not any(k == self.category for k, *_ in cats + app_entries):
            self.category = "all"
        for lb, entries in ((self.cat_list, cats), (self.app_list, app_entries)):
            lb.handler_block_by_func(self._on_cat_selected)
            rows = []
            r = lb.get_first_child()
            while r:
                rows.append(r)
                r = r.get_next_sibling()
            if [getattr(r, "cat", None) for r in rows] == [e[0] for e in entries]:
                # Gleiche Struktur: nur Zähler aktualisieren (kein Flackern, Fokus bleibt)
                for r, e in zip(rows, entries):
                    r.count_label.set_label(str(e[4]) if e[4] else "")
            else:
                for r in rows:
                    lb.remove(r)
                for key, label, icon, wm, n in entries:
                    lb.append(self._cat_row(key, label, icon, gicon_wm=wm, count=n))
                rows = []
                r = lb.get_first_child()
                while r:
                    rows.append(r)
                    r = r.get_next_sibling()
            target = next((r for r in rows if r.cat == self.category), None)
            if target is not None:
                if lb.get_selected_row() is not target:
                    lb.select_row(target)
            else:
                lb.unselect_all()
            lb.handler_unblock_by_func(self._on_cat_selected)

    def _cat_label(self, cat):
        for key, label, _ in CATEGORIES:
            if key == cat:
                return label
        if cat == "global":
            return "Überall"
        if cat.startswith("app:"):
            return AppCatalog.get().name(cat[4:])
        return "Regeln"

    def _visible_items(self):
        q = self.query.casefold()
        out = []
        for it in self.store.items:
            if not matches(self.category, it):
                continue
            if q:
                hay = " ".join([it.name, str(it.hotkey or ""), str(it.abbreviation or ""),
                                it.gesture_obj.label if it.gesture_obj else "", " ".join(it.apps),
                                " ".join(AppCatalog.get().name(a) for a in it.apps),
                                " ".join(describe_action(a) for a in it.actions)]).casefold()
                if q not in hay:
                    continue
            out.append(it)
        return out

    def _fill_content(self):
        items = self._visible_items()
        self.content_title.set_title(self._cat_label(self.category))
        n = len(items)
        self.content_title.set_subtitle(f"{n} Regel{'n' if n != 1 else ''}" + (" gefunden" if self.query else ""))
        self.content_page.set_title(self._cat_label(self.category))

        if not items:
            if self.query:
                sp = Adw.StatusPage(icon_name="system-search-symbolic", title="Nichts gefunden",
                                    description="Versuche einen anderen Suchbegriff.")
            elif not self.store.items:
                sp = Adw.StatusPage(icon_name=control.APP_ID, title="Willkommen bei Tastenwerk",
                                    description="Lege deine erste Regel an – oder übernimm deine Einstellungen aus BetterTouchTool.")
                b = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
                n1 = Gtk.Button(label="Neue Regel", css_classes=["pill", "suggested-action"])
                n1.connect("clicked", lambda *_: self.new_rule())
                n2 = Gtk.Button(label="Aus BetterTouchTool importieren", css_classes=["pill"])
                n2.connect("clicked", lambda *_: self.get_application().activate_action("import"))
                b.append(n1)
                b.append(n2)
                sp.set_child(b)
            else:
                sp = Adw.StatusPage(icon_name="view-list-symbolic", title="Hier ist noch nichts",
                                    description="In dieser Ansicht gibt es keine Regeln.")
            self.scroller.set_child(sp)
            return

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=32,
                      margin_start=12, margin_end=12)
        warns = self.store.warnings()
        if warns and self.category in ("all", "review") and not self.query:
            g = Adw.PreferencesGroup()
            ex = Adw.ExpanderRow(title=f"{len(warns)} Hinweis{'e' if len(warns) != 1 else ''} zur Konfiguration",
                                 subtitle="Doppelte Belegungen und ähnliches")
            ex.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            for w in warns[:30]:
                ex.add_row(Adw.ActionRow(title=esc(w), title_lines=3))
            g.add(ex)
            box.append(g)

        # Gruppieren: Überall zuerst, dann je Programm
        groups: dict[str, list[RuleItem]] = {}
        single = self.category.startswith("app:") or self.category == "global"
        for it in items:
            key = "" if single else (", ".join(sorted(it.apps, key=str.lower)) if it.apps else "*")
            groups.setdefault(key, []).append(it)
        cat = AppCatalog.get()

        def gkey(k):
            return (k != "*", k.lower())
        for key in sorted(groups, key=gkey):
            if single:
                title = ""
            elif key == "*":
                title = "Überall"
            else:
                title = ", ".join(cat.name(a) for a in key.split(", "))
            g = Adw.PreferencesGroup(title=esc(title))
            for it in sorted(groups[key], key=lambda i: (not i.enabled, i.name.casefold())):
                g.add(self._rule_row(it))
            box.append(g)
        clamp = Adw.Clamp(maximum_size=880, tightening_threshold=640, child=box)
        self.scroller.set_child(clamp)

    def _rule_row(self, it: RuleItem):
        descs = [describe_action(a) for a in it.actions[:2]]
        # Importierte Regeln heißen oft wie ihre Aktion – dann nichts doppelt anzeigen
        if descs and it.name.startswith(descs[0][:30]):
            descs = descs[1:]
        sub = " · ".join(descs)
        if len(it.actions) > 2:
            sub += f" · +{len(it.actions) - 2}"
        row = Adw.ActionRow(title=esc(it.name), subtitle=esc(sub), activatable=True, subtitle_lines=1, title_lines=1)
        row.add_prefix(action_tile(it.actions, dim=not it.enabled))
        problem = None
        if it.hotkey and self.system_shortcuts:
            try:
                c = Combo.parse(str(it.hotkey))
                if c in self.system_shortcuts:
                    problem = f"Von GNOME belegt ({self.system_shortcuts[c]}) – wird nicht ausgelöst"
            except KeyError_:
                problem = "Ungültiges Kürzel"
        if it.gesture:
            g = it.gesture_obj
            if g is None:
                problem = "Ungültige Geste"
            elif self.system_gestures and g in self.system_gestures:
                problem = f"Wird auch vom System genutzt ({self.system_gestures[g]}) – beides löst aus"
        if it.notes:
            problem = (problem + "\n" if problem else "") + "Hinweise vom Import: " + "; ".join(it.notes[:2])
        if problem:
            ic = Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"], tooltip_text=problem,
                           valign=Gtk.Align.CENTER)
            row.add_suffix(ic)
        row.add_suffix(trigger_widget(it.data, dim=not it.enabled))
        sw = Gtk.Switch(active=it.enabled, valign=Gtk.Align.CENTER, tooltip_text="Ein/aus")
        sw.connect("state-set", lambda _s, st, item=it: self._toggle(item, st))
        row.add_suffix(sw)
        row.connect("activated", lambda *_: self.edit(it))
        # Rechtsklick-Menü
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y, item=it, r=row: self._context(item, r, x, y))
        row.add_controller(click)
        return row

    def _context(self, it, row, x, y):
        pop = Gtk.Popover(has_arrow=True)
        pop.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["menu"])
        for label, fn in (("Bearbeiten", lambda: self.edit(it)),
                          ("Duplizieren", lambda: self._dup(it)),
                          ("Löschen", lambda: self.delete_item(it))):
            b = Gtk.Button(label=label, css_classes=["flat"], halign=Gtk.Align.FILL)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, f=fn: (pop.popdown(), f()))
            box.append(b)
        pop.set_child(box)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    # ================================================================ Aktionen
    def toast(self, text, button=None, on_button=None):
        t = Adw.Toast(title=text, timeout=3)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: on_button())
        self.toast_overlay.add_toast(t)

    def _toggle(self, it, state):
        try:
            self.store.set_enabled(it, state)
        except ConfigError as e:
            self.toast(f"Fehler: {e}")
            return True
        GLib.idle_add(lambda: (self.refresh(), False)[1])
        return False

    def edit(self, it):
        from .editor import RuleEditor
        RuleEditor(self, it).present(self)

    def new_rule(self):
        from .editor import RuleEditor
        tpl = {}
        if self.category.startswith("app:"):
            tpl["apps"] = [self.category[4:]]
        if self.category == "abbr":
            tpl["abbreviation"] = "##"
        if self.category == "gestures":
            tpl["_mode"] = "gesture"
        if self.category == "window":
            tpl["do"] = [{"window": "left_half"}]
        if self.category == "ai":
            tpl["do"] = [{"ai": ""}]
        RuleEditor(self, None, tpl).present(self)

    def _dup(self, it):
        new = self.store.duplicate(it)
        self.refresh()
        self.toast("Kopie angelegt (ausgeschaltet)")
        self.edit(new)

    def delete_item(self, it):
        rules = self.store.files[it.file]["rules"]
        idx = next(i for i, r in enumerate(rules) if r is it.data)
        data, file = it.data, it.file
        self.store.delete(it)
        self.refresh()

        def undo():
            self.store.files[file]["rules"].insert(idx, data)
            self.store.save(file)
            self.store.load()
            self.refresh()
        self.toast(f"„{it.name}“ gelöscht", "Rückgängig", undo)

    def reload_store(self):
        self.store.load()
        self.refresh()

    def show_category(self, cat):
        self.category = cat
        self.refresh()

    # ================================================================ Ereignisse
    def _on_cat_selected(self, lb, row):
        if row is None:
            return
        other = self.app_list if lb is self.cat_list else self.cat_list
        other.handler_block_by_func(self._on_cat_selected)
        other.unselect_all()
        other.handler_unblock_by_func(self._on_cat_selected)
        self.category = row.cat
        self._fill_content()
        self.split.set_show_content(True)

    def _on_search(self, entry):
        self.query = entry.get_text().strip()
        self._fill_content()

    def _tick(self):
        self._poll_status()
        if self.store and self.get_visible_dialog() is None and self.store.changed_on_disk():
            try:
                self.store.load()
                self.refresh()
            except ConfigError as e:
                self.toast(f"Konfiguration fehlerhaft: {e}")
        return True

    def _poll_status(self):
        pid = control.running_pid()
        self.status_switch.handler_block(self._switch_handler)
        self.status_switch.set_active(bool(pid))
        self.status_switch.set_state(bool(pid))
        self.status_switch.handler_unblock(self._switch_handler)
        self.dot.remove_css_class("stopped")
        if pid:
            self.status_title.set_label("Aktiv")
            n = sum(1 for it in self.store.items if it.enabled) if self.store else 0
            self.status_sub.set_label(f"{n} Regeln reagieren")
        else:
            self.dot.add_css_class("stopped")
            self.status_title.set_label("Gestoppt")
            self.status_sub.set_label("Kürzel reagieren nicht")
        self.banner.set_revealed(not pid)

    def _on_status_switch(self, sw, state):
        ok = control.start() if state else control.stop()
        if not ok:
            self.toast("Konnte den Dienst nicht " + ("starten" if state else "stoppen") + " – siehe Protokoll")
        self._poll_status()
        return True

    def _start_daemon(self):
        if control.start():
            self.toast("Tastenwerk läuft")
        else:
            self.toast("Start fehlgeschlagen – siehe Protokoll in den Einstellungen")
        self._poll_status()

    def _load_system_shortcuts(self):
        try:
            from ..gnome import system_shortcuts
            s = system_shortcuts()
        except Exception:
            s = {}
        try:
            from ..gestures import system_gestures
            g = system_gestures()
        except Exception:
            g = {}
        GLib.idle_add(self._got_system, s, g)

    def _got_system(self, s, g=None):
        self.system_shortcuts = s
        self.system_gestures = g or {}
        if self.get_visible_dialog() is None:
            self._fill_content()
        return False

    def _show_load_error(self):
        sp = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Konfiguration fehlerhaft",
                            description=esc(self.load_error))
        b = Gtk.Button(label="Ordner öffnen", css_classes=["pill"], halign=Gtk.Align.CENTER)
        from ..config import CONFIG_DIR
        b.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(CONFIG_DIR.as_uri(), None))
        sp.set_child(b)
        self.scroller.set_child(sp)


class TastenwerkApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=control.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None
        for name, fn, accel in (("preferences", self._prefs, "<Control>comma"),
                                ("import", self._import, "<Control>i"),
                                ("about", self._about, None),
                                ("shortcuts", self._shortcuts, "<Control>question"),
                                ("new", lambda *_: self.win and self.win.new_rule(), "<Control>n"),
                                ("search", lambda *_: self.win and self.win.search_btn.set_active(
                                    not self.win.search_btn.get_active()), "<Control>f"),
                                ("quit", lambda *_: self.quit(), "<Control>q")):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", fn)
            self.add_action(act)
            if accel:
                self.set_accels_for_action(f"app.{name}", [accel])

    def do_activate(self):
        if not self.win:
            install_css()
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
                str(control.PROJECT / "data"))
            self.win = MainWindow(self)
        self.win.present()

    def _prefs(self, *_):
        from .prefs import Preferences
        Preferences(self.win).present(self.win)

    def _import(self, *_):
        from .importdlg import ImportDialog
        ImportDialog(self.win).present(self.win)

    def _about(self, *_):
        d = Adw.AboutDialog(application_name="Tastenwerk", application_icon=control.APP_ID, version=__version__,
                            developer_name="Tastenwerk",
                            comments="Tastenkürzel, Tastenfolgen, Touchpad-Gesten, Zwischenablage-Verlauf und "
                                     "Fenster-Aktionen für Linux.\n"
                                     "Mit BetterTouchTool-Import und Claude-Textumformung.",
                            website="https://github.com/AFE88/tastenwerk",
                            issue_url="https://github.com/AFE88/tastenwerk/issues",
                            license_type=Gtk.License.GPL_3_0)
        d.present(self.win)

    def _shortcuts(self, *_):
        d = Adw.Dialog(title="Tastenkürzel von Tastenwerk", content_width=420)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup()
        from .widgets import keycaps
        for combo, text in (("ctrl+n", "Neue Regel"), ("ctrl+f", "Suchen"), ("ctrl+i", "Aus BetterTouchTool importieren"),
                            ("ctrl+comma", "Einstellungen"), ("ctrl+q", "Beenden")):
            r = Adw.ActionRow(title=text)
            r.add_suffix(keycaps(combo))
            g.add(r)
        page.add(g)
        tv.set_content(page)
        d.set_child(tv)
        d.present(self.win)


def snapshot(widget: Gtk.Widget, path: str) -> bool:
    """Fenster als PNG speichern (für Tests und Dokumentation)."""
    w, h = widget.get_width(), widget.get_height()
    if w <= 0 or h <= 0:
        return False
    paintable = Gtk.WidgetPaintable(widget=widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return False
    renderer = widget.get_native().get_renderer()
    rect = Graphene.Rect().init(0, 0, w, h)
    tex = renderer.render_texture(node, rect)
    tex.save_to_png(path)
    return True


def main(argv=None):
    GLib.set_prgname(control.APP_ID)
    GLib.set_application_name("Tastenwerk")
    app = TastenwerkApp()
    return app.run(argv if argv is not None else sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
