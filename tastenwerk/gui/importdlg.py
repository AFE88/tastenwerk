"""BetterTouchTool-Import als geführter Dialog."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..actions import describe_action  # noqa: E402
from ..importer import btt  # noqa: E402
from ..importer.wizard import Wizard, trigger_text  # noqa: E402
from .widgets import AppCatalog, gesture_pill, keycaps  # noqa: E402
from ..i18n import tr, trn  # noqa: E402

STATUS_ICON = {"ok": "✅", "adapted": "🟡", "manual": "🛠", "unsupported": "⛔"}
CHOICES = [("j", tr("Übernehmen")), ("d", tr("Ausgeschaltet")), ("n", tr("Weglassen"))]
CHANGED_CHOICES = [("j", tr("Aktualisieren")), ("n", tr("Beibehalten"))]


def esc(s) -> str:
    return GLib.markup_escape_text(str(s))


class ImportDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title=tr("Aus BetterTouchTool übernehmen"), content_width=760, content_height=780)
        self.win = window
        self.paths: list[Path] = []
        self.wiz: Wizard | None = None
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        self.nav.add(self._page_files())

    # ------------------------------------------------------------------ 1
    def _page_files(self):
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        sp = Adw.StatusPage(icon_name="document-import-symbolic", title=tr("Aus BetterTouchTool übernehmen"),
                            description=tr("Wähle eine oder mehrere exportierte Preset-Dateien (.bttpreset oder .json).\n"
                                        "Deine bestehenden Regeln bleiben erhalten – du kannst jederzeit erneut "
                                        "importieren, dann wird nur Neues und Geändertes angeboten."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER)
        pick = Gtk.Button(label=tr("Dateien auswählen …"), css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        pick.connect("clicked", self._choose)
        box.append(pick)
        self.files_group = Adw.PreferencesGroup()
        clamp = Adw.Clamp(maximum_size=520, child=self.files_group)
        box.append(clamp)
        self.next1 = Gtk.Button(label=tr("Weiter"), css_classes=["pill"], halign=Gtk.Align.CENTER, sensitive=False)
        self.next1.connect("clicked", self._load)
        box.append(self.next1)
        self.spinner = Gtk.Spinner(halign=Gtk.Align.CENTER)
        box.append(self.spinner)
        sp.set_child(box)
        sc = Gtk.ScrolledWindow(child=sp)
        tv.set_content(sc)
        return Adw.NavigationPage(title=tr("Dateien"), child=tv)

    def _choose(self, *_):
        f = Gtk.FileFilter(name="BetterTouchTool-Presets")
        for pat in ("*.bttpreset", "*.json"):
            f.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        fd = Gtk.FileDialog(title=tr("Presets auswählen"), filters=filters, default_filter=f)

        def done(dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            for i in range(files.get_n_items()):
                p = Path(files.get_item(i).get_path())
                if p not in self.paths:
                    self.paths.append(p)
            self._show_files()
        fd.open_multiple(self.get_root(), None, done)

    def _show_files(self):
        parent = self.files_group.get_parent()
        new = Adw.PreferencesGroup(title=tr("Ausgewählt"))
        for p in self.paths:
            r = Adw.ActionRow(title=esc(p.name), subtitle=esc(p.parent))
            r.add_prefix(Gtk.Image(icon_name="text-x-generic-symbolic"))
            rm = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER)
            rm.connect("clicked", lambda _b, pp=p: (self.paths.remove(pp), self._show_files()))
            r.add_suffix(rm)
            new.add(r)
        parent.set_child(new)
        self.files_group = new
        self.next1.set_sensitive(bool(self.paths))
        self.next1.add_css_class("suggested-action") if self.paths else self.next1.remove_css_class("suggested-action")

    def _load(self, *_):
        self.next1.set_sensitive(False)
        self.spinner.start()
        paths = sorted(self.paths, key=lambda p: p.stat().st_mtime)

        def work():
            try:
                w = Wizard(paths, self.win.store.path, assume_defaults=True)
                w.load()
                w.load_existing()
                GLib.idle_add(self._loaded, w, None)
            except Exception as e:  # Datei kaputt o. Ä.
                GLib.idle_add(self._loaded, None, e)
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, w, err):
        self.spinner.stop()
        self.next1.set_sensitive(True)
        if err:
            d = Adw.AlertDialog(heading=tr("Datei lässt sich nicht lesen"), body=str(err))
            d.add_response("ok", "OK")
            d.present(self)
            return False
        self.wiz = w
        self.nav.push(self._page_overview())
        return False

    # ------------------------------------------------------------------ 2
    def _page_overview(self):
        w = self.wiz
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        counts = {s: 0 for s in btt.STATUS_ORDER}
        for c in w.cands:
            counts[c.status] += 1

        g = Adw.PreferencesGroup(title=tr("{n} Auslöser gefunden").format(n=len(w.cands)),
                                 description=tr("So lassen sie sich unter Linux übernehmen"))
        grid = Gtk.Box(spacing=12, homogeneous=True, margin_top=6)
        labels = {"ok": "direkt", "adapted": "angepasst", "manual": tr("Handarbeit"), "unsupported": tr("nicht übertragbar")}
        for st in ("ok", "adapted", "manual", "unsupported"):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["card"])
            card.set_margin_top(0)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=14, margin_bottom=14, spacing=2)
            inner.append(Gtk.Label(label=str(counts[st]), css_classes=["big-number"]))
            inner.append(Gtk.Label(label=f"{STATUS_ICON[st]} {labels[st]}", css_classes=["dim-label"]))
            card.append(inner)
            grid.append(card)
        g.add(grid)
        page.add(g)

        uns = [c for c in w.cands if c.status == "unsupported"]
        if uns:
            g = Adw.PreferencesGroup()
            ex = Adw.ExpanderRow(title=tr("Was nicht übertragbar ist"),
                                 subtitle=tr("Klick- und Maus-Gesten sowie die Touch Bar"))
            reasons: dict[str, int] = {}
            for c in uns:
                r = c.notes[0] if c.notes else "?"
                reasons[r] = reasons.get(r, 0) + 1
            for r, n in sorted(reasons.items(), key=lambda t: -t[1]):
                ex.add_row(Adw.ActionRow(title=esc(r), subtitle=f"{n} ×"))
            g.add(ex)
            page.add(g)

        uses_cmd = any(c.hotkey_mac and "cmd" in c.hotkey_mac[0] for c in w.cands if c.status != "unsupported")
        if uses_cmd:
            g = Adw.PreferencesGroup(title=tr("Die ⌘-Taste wird zu …"),
                                     description=tr("In gesendeten Kürzeln wird ⌘ immer zu Strg (⌘V → Strg+V)."))
            first = None
            for key, title, sub in (("super", tr("Super (Windows-Taste)"), tr("Empfohlen – kollidiert am wenigsten mit Programm-Kürzeln")),
                                    ("ctrl", tr("Strg"), tr("Wie unter Linux üblich, überschreibt aber oft Programm-Kürzel")),
                                    ("alt", tr("Alt"), tr("Gleiche Position wie ⌘ auf der Mac-Tastatur"))):
                chk = Gtk.CheckButton(group=first, valign=Gtk.Align.CENTER, active=(key == w.opts.trigger_cmd))
                first = first or chk
                row = Adw.ActionRow(title=title, subtitle=sub, activatable_widget=chk)
                row.add_prefix(chk)
                chk.connect("toggled", lambda c, k=key: c.get_active() and setattr(w.opts, "trigger_cmd", k))
                g.add(row)
            page.add(g)

        tv.set_content(page)
        tv.add_bottom_bar(self._bottom(tr("Weiter"), self._after_overview))
        return Adw.NavigationPage(title=tr("Überblick"), child=tv)

    def _bottom(self, label, cb, extra: Gtk.Widget | None = None):
        bar = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=18, margin_end=18)
        if extra:
            extra.set_hexpand(True)
            bar.append(extra)
        else:
            bar.append(Gtk.Box(hexpand=True))
        btn = Gtk.Button(label=label, css_classes=["pill", "suggested-action"])
        btn.connect("clicked", lambda *_: cb())
        bar.append(btn)
        self._bottom_btn = btn
        return bar

    def _after_overview(self):
        apps = self._mac_apps()
        self.nav.push(self._page_apps(apps) if apps else self._page_review())

    # ------------------------------------------------------------------ 3
    def _mac_apps(self):
        apps: dict[str, tuple[str, int]] = {}
        for c in self.wiz.cands:
            if c.status != "unsupported" and not c.is_global:
                name, n = apps.get(c.bundle, (c.app_name, 0))
                apps[c.bundle] = (name, n + 1)
        return apps

    def _page_apps(self, apps):
        w = self.wiz
        cat = AppCatalog.get()
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title=tr("Welche Programme ersetzen deine Mac-Programme?"),
                                 description=tr("Regeln, die auf dem Mac nur in einem Programm galten, brauchen das passende Linux-Programm."))
        all_keys = [k for k, _ in cat.apps]
        all_names = [i.get_display_name() for _, i in cat.apps]
        self.app_rows = {}
        for bundle, (name, n) in sorted(apps.items(), key=lambda t: t[1][0].lower()):
            key = bundle.lower()
            if key in btt.APP_NOTES:
                r = Adw.ActionRow(title=esc(name), subtitle=esc(btt.APP_NOTES[key]))
                r.add_prefix(Gtk.Image(icon_name="action-unavailable-symbolic"))
                g.add(r)
                w.app_map[bundle] = None
                continue
            sugg = [s for s in btt.APP_SUGGESTIONS.get(key, []) if cat.lookup(s)]
            keys = [None] + sugg + [k for k in all_keys if k not in sugg]
            labels = [tr("Nicht übernehmen")] + [tr("{name}  ·  Vorschlag").format(name=cat.name(s)) for s in sugg] + \
                     [n_ for k, n_ in zip(all_keys, all_names) if k not in sugg]
            row = Adw.ComboRow(title=esc(name), subtitle=trn("{n} Regel", "{n} Regeln", n).format(n=n),
                               model=Gtk.StringList.new(labels), enable_search=True)
            row.set_selected(1 if sugg else 0)
            self.app_rows[bundle] = (row, keys)
            g.add(row)
        page.add(g)
        tv.set_content(page)
        tv.add_bottom_bar(self._bottom(tr("Weiter"), self._after_apps))
        return Adw.NavigationPage(title=tr("Programme"), child=tv)

    def _after_apps(self):
        for bundle, (row, keys) in self.app_rows.items():
            k = keys[row.get_selected()]
            self.wiz.app_map[bundle] = [k] if k else None
        self.nav.push(self._page_review())

    # ------------------------------------------------------------------ 4
    def _page_review(self):
        w = self.wiz
        self.plan = w.plan()
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        self.choice_widgets = []
        new = [p for p in self.plan if p["kind"] == "new"]
        changed = [p for p in self.plan if p["kind"] == "changed"]

        if new:
            g = Adw.PreferencesGroup(title=tr("Neu ({n})").format(n=len(new)),
                                     description=tr("Vorschläge sind voreingestellt. „Ausgeschaltet“ übernimmt die Regel, "
                                                 "damit du sie später in Ruhe fertig machen kannst."))
            allbtn = Gtk.Button(label=tr("Alle übernehmen"), css_classes=["flat"], valign=Gtk.Align.CENTER)
            g.set_header_suffix(allbtn)
            dds = []
            for p in sorted(new, key=lambda p: ("jdn".index(p["default"]), p["candidate"].app_name)):
                dd = self._row(g, p, CHOICES)
                dds.append(dd)
            allbtn.connect("clicked", lambda *_: [d.set_selected(0) for d in dds])
            page.add(g)
        if changed:
            g = Adw.PreferencesGroup(title=tr("In BetterTouchTool geändert ({n})").format(n=len(changed)),
                                     description=tr("Diese Regeln gibt es schon – im Preset haben sie sich verändert."))
            for p in changed:
                self._row(g, p, CHANGED_CHOICES, changed=True)
            page.add(g)

        info = Adw.PreferencesGroup(title=tr("Außerdem"))
        for kind, title, sub in (("unchanged", tr("Schon vorhanden"), tr("Seit dem letzten Import unverändert – deine Anpassungen bleiben")),
                                 ("skipped_before", tr("Früher bewusst weggelassen"), tr("Wird nicht erneut angeboten")),
                                 ("not_mapped", tr("Programm nicht zugeordnet"), tr("Ohne passendes Linux-Programm")),
                                 ("unsupported", tr("Nicht übertragbar"), tr("Gesten, Touch Bar, Remote"))):
            items = [p for p in self.plan if p["kind"] == kind]
            if not items:
                continue
            ex = Adw.ExpanderRow(title=f"{title} ({len(items)})", subtitle=sub)
            for p in items[:60]:
                c = p["candidate"]
                ex.add_row(Adw.ActionRow(title=esc(c.mac_trigger), subtitle=esc(f"{c.app_name} · {c.action_text}")))
            info.add(ex)
        page.add(info)
        if not new and not changed:
            sp = Adw.StatusPage(icon_name="emblem-ok-symbolic", title=tr("Nichts Neues"),
                                description=tr("Deine Regeln sind auf dem Stand dieser Presets."))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(sp)
            box.append(page)
            tv.set_content(box)
        else:
            tv.set_content(page)
        self.summary = Gtk.Label(xalign=0, css_classes=["dim-label"])
        tv.add_bottom_bar(self._bottom(tr("Importieren"), self._do_import, self.summary))
        self._update_summary()
        return Adw.NavigationPage(title=tr("Regeln prüfen"), child=tv)

    def _row(self, group, p, choices, changed=False):
        c, rule = p["candidate"], p["rule"]
        linux = trigger_text(rule)
        where = tr("überall") if not rule.get("apps") else ", ".join(AppCatalog.get().name(a) for a in rule["apps"])
        acts = "; ".join(describe_action(a) for a in rule["do"][:2])
        sub = f"{c.mac_trigger}  →  {linux}  ·  {where}"
        if not rule["name"].startswith(acts[:30]):
            sub += f"\n{acts}"
        notes = [n for n in p["notes"] if n]
        if changed:
            old = p.get("old") or {}
            sub += tr("\nbisher: {trigger} → ").format(trigger=trigger_text(old)) + \
                   "; ".join(describe_action(a) for a in old.get("do") or [])
        if notes:
            sub += "\n⚠ " + "\n⚠ ".join(notes[:3])
        row = Adw.ActionRow(title=esc(rule["name"]), subtitle=esc(sub), subtitle_lines=6)
        row.add_prefix(Gtk.Label(label=STATUS_ICON[c.status], valign=Gtk.Align.START, margin_top=12))
        if rule.get("hotkey"):
            row.add_suffix(keycaps(rule["hotkey"]))
        elif rule.get("gesture"):
            row.add_suffix(gesture_pill(rule["gesture"]))
        dd = Gtk.DropDown.new_from_strings([x[1] for x in choices])
        dd.set_valign(Gtk.Align.CENTER)
        keys = [x[0] for x in choices]
        dd.set_selected(keys.index(p["default"]) if p["default"] in keys else len(keys) - 1)
        dd.connect("notify::selected", lambda *_: self._update_summary())
        row.add_suffix(dd)
        group.add(row)
        self.choice_widgets.append((p, dd, keys))
        return dd

    def _decisions(self):
        out = []
        for p, dd, keys in self.choice_widgets:
            out.append((p, keys[dd.get_selected()]))
        return out

    def _update_summary(self):
        d = self._decisions()
        j = sum(1 for p, k in d if k == "j" and p["kind"] == "new")
        off = sum(1 for p, k in d if k == "d")
        up = sum(1 for p, k in d if k == "j" and p["kind"] == "changed")
        n = sum(1 for p, k in d if k == "n" and p["kind"] == "new")
        parts = [tr("{n} übernehmen").format(n=j), tr("{n} ausgeschaltet").format(n=off)]
        if up:
            parts.append(tr("{n} aktualisieren").format(n=up))
        parts.append(tr("{n} weglassen").format(n=n))
        self.summary.set_label(" · ".join(parts))
        if hasattr(self, "_bottom_btn"):
            self._bottom_btn.set_sensitive(bool(j or off or up or n))

    # ------------------------------------------------------------------ 5
    def _do_import(self):
        w = self.wiz
        w.accepted, w.updated, w.skipped = [], [], []
        w.unchanged = sum(1 for p in self.plan if p["kind"] == "unchanged")
        w.silent_skips = sum(1 for p in self.plan if p["kind"] == "skipped_before")
        for p, k in self._decisions():
            rule = dict(p["rule"])
            notes = [n for n in p["notes"] if n]
            if p["kind"] == "changed":
                if k == "j":
                    rule["enabled"] = (p.get("old") or {}).get("enabled", True)
                    f, idx = p["prev"]
                    w.updated.append((f, idx, rule))
                continue
            if k == "n":
                w.skipped.append((p["candidate"], "von dir weggelassen"))
                continue
            if k == "d":
                rule["enabled"] = False
                notes = notes or [tr("Vor dem Einschalten prüfen")]
            if notes:
                rule["notes"] = notes
            w.accepted.append(rule)
        for p in self.plan:
            if p["kind"] == "not_mapped":
                w.skipped.append((p["candidate"], tr("Programm weggelassen")))
        try:
            files, report = w.write_results()
        except Exception as e:
            d = Adw.AlertDialog(heading=tr("Import fehlgeschlagen"), body=str(e))
            d.add_response("ok", "OK")
            d.present(self)
            return
        self.report = report
        self.win.reload_store()
        on = sum(1 for r in w.accepted if r.get("enabled", True))
        off = len(w.accepted) - on
        self.nav.push(self._page_done(on, off, len(w.updated)))

    def _page_done(self, on, off, up):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        desc = tr("{on} Regeln aktiv übernommen, {off} ausgeschaltet zum Nacharbeiten").format(on=on, off=off)
        if up:
            desc += tr(", {up} aktualisiert").format(up=up)
        desc += tr(".\nAusgeschaltete Regeln findest du unter „Zu prüfen“.")
        sp = Adw.StatusPage(icon_name="emblem-ok-symbolic", title=tr("Import abgeschlossen"), description=desc)
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        rep = Gtk.Button(label=tr("Bericht öffnen"), css_classes=["pill"])
        rep.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(self.report.as_uri(), None))
        done = Gtk.Button(label=tr("Fertig"), css_classes=["pill", "suggested-action"])
        done.connect("clicked", lambda *_: (self.close(), self.win.show_category("review") if off else None))
        box.append(rep)
        box.append(done)
        sp.set_child(box)
        tv.set_content(sp)
        page = Adw.NavigationPage(title=tr("Fertig"), child=tv, can_pop=False)
        return page
