"""Dialog zum Anlegen und Bearbeiten einer Regel."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..actions import WINDOW_LABELS, describe_action  # noqa: E402
from ..config import WINDOW_OPS, ConfigError  # noqa: E402
from ..keys import Combo, KeyError_, parse_sequence  # noqa: E402
from .widgets import (ACTION_META, ACTION_ORDER, AppPicker, GestureRecorder, KeyRecorder, action_kind,  # noqa: E402
                      app_chip, keycaps, make_tile)

PLACEHOLDERS = [("Datum", "{date}"), ("Uhrzeit", "{time}"), ("Datum + Zeit", "{datetime}"),
                ("Zwischenablage", "{clipboard}"), ("Cursor hier", "{cursor}")]
TEXT_METHODS = [("auto", "Automatisch", "Kurze Texte tippen, lange und mehrzeilige einfügen"),
                ("type", "Tippen", "Zeichen für Zeichen – funktioniert überall"),
                ("paste", "Einfügen", "Über die Zwischenablage – schnell, ideal für lange Texte")]
RUN_OUTPUT = [(None, "Ignorieren"), ("type", "An Cursor einfügen"), ("paste", "Über Zwischenablage einfügen"),
              ("notify", "Als Benachrichtigung")]
AI_INPUT = [("selection", "Markierter Text"), ("line", "Aktuelle Zeile bis zum Cursor"), ("all", "Gesamter Text im Feld")]
WS_CHOICES = [("next", "Nächste"), ("prev", "Vorherige")] + [(str(i), f"Nummer {i}") for i in range(1, 10)]


def _string_list(labels):
    return Gtk.StringList.new([l for l in labels])


class TextArea(Gtk.Box):
    """Mehrzeiliges Textfeld mit Platzhalter-Knöpfen."""

    def __init__(self, text: str, on_change, placeholders=True, mono=True, height=96):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        self.buf = Gtk.TextBuffer(text=text)
        tv = Gtk.TextView(buffer=self.buf, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                          css_classes=["editor-text"] if mono else [], top_margin=8, bottom_margin=8,
                          left_margin=10, right_margin=10)
        frame = Gtk.ScrolledWindow(min_content_height=height, max_content_height=260, propagate_natural_height=True,
                                   hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["card", "textframe"])
        frame.set_child(tv)
        self.append(frame)
        self.tv = tv
        self.buf.connect("changed", lambda *_: on_change())
        if placeholders:
            bar = Gtk.Box(spacing=6)
            bar.append(Gtk.Label(label="Einfügen:", css_classes=["dim-label", "caption"]))
            for label, ph in PLACEHOLDERS:
                b = Gtk.Button(label=label, css_classes=["placeholder-btn"])
                b.set_tooltip_text(ph)
                b.connect("clicked", lambda _b, p=ph: self._insert(p))
                bar.append(b)
            sc = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
            sc.set_child(bar)
            self.append(sc)

    def _insert(self, text):
        self.buf.insert_at_cursor(text)
        self.tv.grab_focus()

    def get_text(self) -> str:
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), True)


class ActionEditor:
    """Eine Aktion als aufklappbare Zeile mit passenden Eingabefeldern."""

    def __init__(self, dialog: "RuleEditor", action: dict, expanded=False):
        self.dlg = dialog
        self.kind = action_kind(action)
        self.a = dict(action)
        title, _desc, icon, style = ACTION_META[self.kind]
        self.row = Adw.ExpanderRow(title=title, expanded=expanded)
        self.row.add_prefix(make_tile(icon, style, 30, 16))
        self.up = Gtk.Button(icon_name="go-up-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER, tooltip_text="Nach oben")
        self.down = Gtk.Button(icon_name="go-down-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER, tooltip_text="Nach unten")
        rm = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER, tooltip_text="Aktion entfernen")
        self.up.connect("clicked", lambda *_: dialog.move_action(self, -1))
        self.down.connect("clicked", lambda *_: dialog.move_action(self, 1))
        rm.connect("clicked", lambda *_: dialog.remove_action(self))
        # ExpanderRow stellt Suffixe in umgekehrter Reihenfolge dar
        for b in (rm, self.down, self.up):
            self.row.add_suffix(b)
        getattr(self, f"_build_{self.kind}")()
        self.refresh_subtitle()

    def changed(self):
        self.refresh_subtitle()
        self.dlg.mark_dirty()

    def refresh_subtitle(self):
        try:
            a = self.get()
            sub = describe_action(a)
            # Typ-Präfix aus der Kurzbeschreibung entfernen, der Titel sagt es schon
            for p in ("Tippen ", "Einfügen ", "Tasten ", "Fenster: ", "Öffnen ", "Befehl ", "Claude: ", "Arbeitsfläche: ", "Hinweis ", "Zwischenablage-"):
                if sub.startswith(p):
                    sub = sub[len(p):]
            self.row.set_subtitle(GLib.markup_escape_text(sub))
        except Exception:
            self.row.set_subtitle("")

    # ---------- Editoren je Typ ----------
    def _build_type(self):
        text = self.a.get("type", self.a.get("paste", ""))
        method = "paste" if "paste" in self.a else self.a.get("method", "auto")
        self.text = TextArea(str(text), self.changed)
        self.row.add_row(self.text)
        self.method = Adw.ComboRow(title="Einfügen als", model=_string_list([m[1] for m in TEXT_METHODS]))
        idx = next((i for i, m in enumerate(TEXT_METHODS) if m[0] == method), 0)
        self.method.set_selected(idx)
        self.method.set_subtitle(TEXT_METHODS[idx][2])
        self.method.connect("notify::selected", lambda *_: (self.method.set_subtitle(TEXT_METHODS[self.method.get_selected()][2]), self.changed()))
        self.row.add_row(self.method)

    def _build_keys(self):
        self.keys = Adw.EntryRow(title="Tasten (nacheinander, mit Leerzeichen getrennt)", text=str(self.a.get("keys", "")))
        rec = KeyRecorder(placeholder="Aufnehmen")
        rec.set_tooltip_text("Kombination aufnehmen und anhängen")

        def add(_r, combo):
            if combo:
                cur = self.keys.get_text().strip()
                self.keys.set_text((cur + " " + combo).strip())
            rec.set_value(None)
        rec.connect("recorded", add)
        self.keys.add_suffix(rec)
        self.keys.connect("changed", lambda *_: (self._validate_keys(), self.changed()))
        self.row.add_row(self.keys)
        self.keys_preview = Adw.ActionRow(title="Vorschau")
        self.row.add_row(self.keys_preview)
        self._validate_keys()

    def _validate_keys(self):
        txt = self.keys.get_text().strip()
        child = getattr(self, "_kp_child", None)
        if child:
            self.keys_preview.remove(child)
        try:
            parse_sequence(txt) if txt else None
            self.keys.remove_css_class("error")
            box = Gtk.Box(spacing=6)
            for part in txt.split():
                box.append(keycaps(part))
            self._kp_child = box
            self.keys_preview.set_subtitle("" if txt else "Noch keine Tasten")
        except KeyError_ as e:
            self.keys.add_css_class("error")
            self._kp_child = Gtk.Label(label=str(e), css_classes=["error"])
        self.keys_preview.add_suffix(self._kp_child)

    def _build_window(self):
        ops = WINDOW_OPS
        self.win = Adw.ComboRow(title="Fenster", model=_string_list([WINDOW_LABELS.get(o, o) for o in ops]))
        cur = self.a.get("window", "left_half")
        self.win.set_selected(ops.index(cur) if cur in ops else 0)
        self.win.connect("notify::selected", lambda *_: self.changed())
        self.row.add_row(self.win)

    def _build_workspace(self):
        self.ws = Adw.ComboRow(title="Wechseln zu", model=_string_list([w[1] for w in WS_CHOICES]))
        cur = str(self.a.get("workspace", "next"))
        self.ws.set_selected(next((i for i, w in enumerate(WS_CHOICES) if w[0] == cur), 0))
        self.ws.connect("notify::selected", lambda *_: self.changed())
        self.row.add_row(self.ws)

    def _build_open(self):
        self.open = Adw.EntryRow(title="Datei, Ordner, Webseite oder Programm", text=str(self.a.get("open", "")))
        btn = Gtk.Button(icon_name="document-open-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                         tooltip_text="Datei auswählen")
        btn.connect("clicked", self._choose_file)
        self.open.add_suffix(btn)
        self.open.connect("changed", lambda *_: self.changed())
        self.row.add_row(self.open)

    def _choose_file(self, *_):
        fd = Gtk.FileDialog(title="Datei oder Ordner wählen")

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f:
                self.open.set_text(f.get_path() or f.get_uri())
        fd.open(self.dlg.get_root(), None, done)

    def _build_run(self):
        self.cmd = Adw.EntryRow(title="Befehl", text=str(self.a.get("run", "")))
        self.cmd.add_css_class("monospace")
        self.cmd.connect("changed", lambda *_: self.changed())
        self.row.add_row(self.cmd)
        self.output = Adw.ComboRow(title="Ausgabe des Befehls", model=_string_list([o[1] for o in RUN_OUTPUT]))
        self.output.set_selected(next((i for i, o in enumerate(RUN_OUTPUT) if o[0] == self.a.get("output")), 0))
        self.output.connect("notify::selected", lambda *_: self.changed())
        self.row.add_row(self.output)

    def _build_delay(self):
        self.delay = Adw.SpinRow.new_with_range(0.05, 30, 0.05)
        self.delay.set_title("Sekunden")
        self.delay.set_digits(2)
        self.delay.set_value(float(self.a.get("delay", 0.3)))
        self.delay.connect("notify::value", lambda *_: self.changed())
        self.row.add_row(self.delay)

    def _build_clipboard(self):
        from ..clipboard_defaults import MAX_N, command
        cmd, n = command(self.a.get("clipboard", "show")) or ("show", 0)
        self.clip = Adw.ComboRow(title="Was passiert",
                                 model=_string_list(["Verlauf zeigen", "Eintrag direkt einfügen", "Verlauf leeren"]))
        self.clip.set_selected({"show": 0, "paste": 1, "clear": 2}[cmd])
        self.clip_nr = Adw.SpinRow.new_with_range(1, MAX_N, 1)
        self.clip_nr.set_title("Welcher Eintrag")
        self.clip_nr.set_value(n or 2)
        self.clip_nr.connect("notify::value", lambda *_: (self._clip_hint(), self.changed()))
        self.clip.connect("notify::selected", lambda *_: (self._clip_hint(), self.changed()))
        self.row.add_row(self.clip)
        self.row.add_row(self.clip_nr)
        self._clip_hint()

    def _clip_hint(self):
        sel = self.clip.get_selected()
        self.clip_nr.set_visible(sel == 1)
        n = int(self.clip_nr.get_value())
        self.clip.set_subtitle({
            0: "Fenster mit Suche, ↵ fügt ein, Strg+P heftet an",
            1: ("1 = zuletzt kopiert, ohne Angeheftete. Nr. 2 wechselt bei jedem Druck "
                "zwischen den letzten beiden Kopien hin und her." if n == 2 else
                "1 = zuletzt kopiert, 2 = davor … Angeheftete zählen nicht mit."),
            2: "Löscht alles außer Angehefteten",
        }[sel])

    def _build_app(self):
        from ..config import APP_IF_ACTIVE
        self.app_key = str(self.a.get("app", "")).strip()
        self.app_row = Adw.ActionRow(title="Programm", activatable=True)
        self.app_icon = Gtk.Image(pixel_size=32)
        self.app_row.add_prefix(self.app_icon)
        pick = Gtk.Button(label="Wählen …", valign=Gtk.Align.CENTER)
        pick.connect("clicked", self._pick_target_app)
        self.app_row.add_suffix(pick)
        self.app_row.set_activatable_widget(pick)
        self.row.add_row(self.app_row)
        self.app_modes = list(APP_IF_ACTIVE)
        self.app_if = Adw.ComboRow(title="Wenn es schon vorne ist", model=_string_list(list(APP_IF_ACTIVE.values())))
        cur = self.a.get("if_active", "next")
        self.app_if.set_selected(self.app_modes.index(cur) if cur in self.app_modes else 0)
        self.app_if.connect("notify::selected", lambda *_: self.changed())
        self.row.add_row(self.app_if)
        self._show_target_app()
        if not self.app_key:
            GLib.idle_add(lambda: (self._pick_target_app(), False)[1])

    def _show_target_app(self):
        from .widgets import AppCatalog
        cat = AppCatalog.get()
        if self.app_key:
            self.app_row.set_title(GLib.markup_escape_text(cat.name(self.app_key)))
            self.app_row.set_subtitle("Läuft es, kommt sein Fenster nach vorne – sonst wird es gestartet")
            self.app_icon.set_from_gicon(cat.icon(self.app_key))
        else:
            self.app_row.set_title("Noch kein Programm gewählt")
            self.app_row.set_subtitle("Gerade geöffnete Programme stehen oben in der Liste")
            self.app_icon.set_from_icon_name("view-app-grid-symbolic")

    def _pick_target_app(self, *_):
        from .widgets import AppPicker

        def picked(key):
            self.app_key = key
            self._show_target_app()
            self.changed()
        AppPicker(picked).present(self.dlg)

    def _build_screenshot(self):
        from ..config import SCREENSHOT_MODES
        self.shot_modes = list(SCREENSHOT_MODES)
        self.shot = Adw.ComboRow(title="Was aufnehmen", model=_string_list(list(SCREENSHOT_MODES.values())))
        cur = str(self.a.get("screenshot", "area"))
        self.shot.set_selected(self.shot_modes.index(cur) if cur in self.shot_modes else 0)
        self.shot.set_subtitle("Öffnet das Bildschirmfoto-Werkzeug von GNOME. Das Bild landet in "
                               "„Bilder/Bildschirmfotos“ und in der Zwischenablage.")
        self.shot.connect("notify::selected", lambda *_: self.changed())
        self.row.add_row(self.shot)

    def _build_lock(self):
        self.row.add_row(Adw.ActionRow(title="Keine Einstellungen nötig", subtitle="Sperrt die Sitzung sofort"))

    def _build_notify(self):
        self.note = Adw.EntryRow(title="Meldung", text=str(self.a.get("notify", "")))
        self.note.connect("changed", lambda *_: self.changed())
        self.row.add_row(self.note)

    def _build_ai(self):
        lbl = Adw.ActionRow(title="Anweisung an Claude",
                            subtitle="Was soll mit dem Text passieren? z. B. „Formuliere als freundliche Mail in der Du-Form“")
        self.row.add_row(lbl)
        self.prompt = TextArea(str(self.a.get("ai", "")), self.changed, placeholders=False, mono=False, height=72)
        self.row.add_row(self.prompt)
        self.ai_in = Adw.ComboRow(title="Welcher Text", model=_string_list([o[1] for o in AI_INPUT]))
        self.ai_in.set_selected(next((i for i, o in enumerate(AI_INPUT) if o[0] == self.a.get("input", "selection")), 0))
        self.ai_in.connect("notify::selected", lambda *_: self.changed())
        self.row.add_row(self.ai_in)

    # ---------- Ergebnis ----------
    def get(self) -> dict:
        k = self.kind
        if k == "type":
            text = self.text.get_text()
            m = TEXT_METHODS[self.method.get_selected()][0]
            if m == "paste":
                return {"paste": text}
            return {"type": text} if m == "auto" else {"type": text, "method": m}
        if k == "keys":
            return {"keys": self.keys.get_text().strip()}
        if k == "window":
            return {"window": WINDOW_OPS[self.win.get_selected()]}
        if k == "workspace":
            return {"workspace": WS_CHOICES[self.ws.get_selected()][0]}
        if k == "open":
            return {"open": self.open.get_text().strip()}
        if k == "run":
            out = RUN_OUTPUT[self.output.get_selected()][0]
            a = {"run": self.cmd.get_text().strip()}
            if out:
                a["output"] = out
            return a
        if k == "delay":
            return {"delay": round(self.delay.get_value(), 2)}
        if k == "lock":
            return {"lock": True}
        if k == "screenshot":
            return {"screenshot": self.shot_modes[self.shot.get_selected()]}
        if k == "app":
            out = {"app": self.app_key}
            mode = self.app_modes[self.app_if.get_selected()]
            if mode != "next":
                out["if_active"] = mode
            return out
        if k == "clipboard":
            sel = self.clip.get_selected()
            if sel == 1:
                return {"clipboard": f"paste {int(self.clip_nr.get_value())}"}
            return {"clipboard": "clear" if sel == 2 else "show"}
        if k == "notify":
            return {"notify": self.note.get_text()}
        if k == "ai":
            a = {"ai": self.prompt.get_text().strip()}
            inp = AI_INPUT[self.ai_in.get_selected()][0]
            if inp != "selection":
                a["input"] = inp
            return a
        return dict(self.a)


class RuleEditor(Adw.Dialog):
    def __init__(self, window, item=None, template: dict | None = None):
        super().__init__(content_width=660, content_height=760)
        self.win = window
        self.store = window.store
        self.item = item
        data = dict(item.data) if item else dict(template or {})
        self.data = data
        self.dirty = False
        self.set_title("Regel bearbeiten" if item else "Neue Regel")
        self.connect("close-attempt", self._on_close_attempt)
        self.connect("closed", lambda *_: self.gest_rec.recording and self.gest_rec.stop())
        self.set_can_close(False)  # Schließen läuft über _on_close_attempt (Rückfrage bei Änderungen)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Abbrechen")
        cancel.connect("clicked", lambda *_: self._on_close_attempt(self))
        self.save_btn = Gtk.Button(label="Speichern" if item else "Anlegen", css_classes=["suggested-action"])
        self.save_btn.connect("clicked", self._save)
        hb.pack_start(cancel)
        hb.pack_end(self.save_btn)
        tv.add_top_bar(hb)

        page = Adw.PreferencesPage()
        self.page = page

        # --- Hinweise vom Import ---
        notes = list(data.get("notes") or [])
        if notes:
            g = Adw.PreferencesGroup(title="Hinweise vom Import",
                                     description="Diese Regel wurde aus BetterTouchTool übernommen. Prüfe die Punkte und schalte sie dann ein.")
            for n in notes:
                r = Adw.ActionRow(title=GLib.markup_escape_text(n), title_lines=3)
                r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                g.add(r)
            done = Gtk.Button(label="Erledigt", valign=Gtk.Align.CENTER, css_classes=["flat"])
            g.set_header_suffix(done)

            def clear(*_):
                self.data.pop("notes", None)
                page.remove(g)
                self.mark_dirty()
            done.connect("clicked", clear)
            page.add(g)
            self.notes_group = g

        # --- Allgemein ---
        g = Adw.PreferencesGroup()
        self.name = Adw.EntryRow(title="Name", text=str(data.get("name", "")))
        self.name.connect("changed", lambda *_: self.mark_dirty())
        self.enabled = Adw.SwitchRow(title="Aktiv", subtitle="Ausgeschaltete Regeln bleiben gespeichert",
                                     active=bool(data.get("enabled", True)))
        self.enabled.connect("notify::active", lambda *_: self.mark_dirty())
        g.add(self.name)
        g.add(self.enabled)
        page.add(g)

        # --- Auslöser ---
        g = Adw.PreferencesGroup(title="Auslöser")
        seg = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        self.t_hot = Gtk.ToggleButton(label="Tastenkürzel")
        self.t_abbr = Gtk.ToggleButton(label="Tastenfolge", group=self.t_hot)
        self.t_gest = Gtk.ToggleButton(label="Geste", group=self.t_hot)
        seg.append(self.t_hot)
        seg.append(self.t_abbr)
        seg.append(self.t_gest)
        g.set_header_suffix(seg)
        self.hot_row = Adw.ActionRow(title="Tastenkürzel", subtitle="Klicken und die gewünschten Tasten drücken")
        self.recorder = KeyRecorder(str(data["hotkey"]) if data.get("hotkey") else None)
        self.recorder.connect("recorded", lambda *_: (self._check_trigger(), self.mark_dirty()))
        self.hot_row.add_suffix(self.recorder)
        self.abbr_row = Adw.EntryRow(title="Getippte Folge, z. B. ##mail",
                                     text=str(data.get("abbreviation") or data.get("abbr") or ""))
        self.abbr_row.connect("changed", lambda *_: (self._check_trigger(), self.mark_dirty()))
        self.gest_row = Adw.ActionRow(title="Touchpad-Geste",
                                      subtitle="Klicken und auf dem Touchpad wischen, oder über ⋯ wählen")
        self.gest_rec = GestureRecorder(data.get("gesture"))
        self.gest_rec.connect("recorded", lambda *_: (self._check_trigger(), self.mark_dirty()))
        self.gest_row.add_suffix(self.gest_rec)
        self.trig_status = Adw.ActionRow(title="", css_classes=["property"])
        self.trig_icon = Gtk.Image()
        self.trig_status.add_prefix(self.trig_icon)
        g.add(self.hot_row)
        g.add(self.abbr_row)
        g.add(self.gest_row)
        g.add(self.trig_status)
        page.add(g)
        is_abbr = bool(data.get("abbreviation") or data.get("abbr"))
        mode = self.t_gest if data.get("gesture") else self.t_abbr if is_abbr else self.t_hot
        if not item and data.get("_mode") == "gesture":
            mode = self.t_gest
        self.data.pop("_mode", None)
        mode.set_active(True)
        for b in (self.t_hot, self.t_abbr, self.t_gest):
            b.connect("toggled", lambda b: b.get_active() and (self._update_trigger_mode(), self.mark_dirty()))
        self._update_trigger_mode()

        # --- Gilt in ---
        g = Adw.PreferencesGroup(title="Gilt in")
        self.apps = list(data.get("apps") or ([data["app"]] if data.get("app") else []))
        self.not_apps = list(data.get("not_apps") or [])
        self.everywhere = Adw.SwitchRow(title="In allen Programmen", active=not self.apps)
        self.everywhere.connect("notify::active", lambda *_: (self._refresh_apps(), self.mark_dirty()))
        g.add(self.everywhere)
        self.apps_row = Adw.ActionRow(title="Nur in")
        self.apps_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                    column_spacing=6, row_spacing=6, valign=Gtk.Align.CENTER, hexpand=True,
                                    margin_top=8, margin_bottom=8)
        add_app = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                             tooltip_text="Programm hinzufügen")
        add_app.connect("clicked", lambda *_: self._pick_app(False))
        self.apps_row.add_suffix(self.apps_box)
        self.apps_row.add_suffix(add_app)
        self.except_row = Adw.ActionRow(title="Außer in")
        self.except_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                      column_spacing=6, row_spacing=6, valign=Gtk.Align.CENTER, hexpand=True,
                                      margin_top=8, margin_bottom=8)
        add_ex = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Ausnahme hinzufügen")
        add_ex.connect("clicked", lambda *_: self._pick_app(True))
        self.except_row.add_suffix(self.except_box)
        self.except_row.add_suffix(add_ex)
        g.add(self.apps_row)
        g.add(self.except_row)
        page.add(g)
        self._refresh_apps()

        # --- Aktionen ---
        self.act_group = Adw.PreferencesGroup(title="Aktionen", description="Laufen nacheinander von oben nach unten ab")
        add = Gtk.MenuButton(label="Hinzufügen", icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                             css_classes=["flat"], always_show_arrow=False)
        add_content = Adw.ButtonContent(icon_name="list-add-symbolic", label="Aktion")
        add.set_child(add_content)
        add.set_popover(self._action_popover())
        self.act_group.set_header_suffix(add)
        self.act_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.act_empty = Adw.ActionRow(title="Noch keine Aktion", subtitle="Über „+ Aktion“ hinzufügen")
        # Zweiter Einstieg direkt unter der letzten Aktion – dort, wo man weitermachen will
        self.add_more = Gtk.MenuButton(css_classes=["flat"], halign=Gtk.Align.START, margin_top=6,
                                       direction=Gtk.ArrowType.UP)
        self.add_more.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Weitere Aktion hinzufügen"))
        self.add_more.set_popover(self._action_popover())
        self.act_group.add(self.act_list)
        self.act_group.add(self.add_more)
        page.add(self.act_group)
        self.editors: list[ActionEditor] = []
        acts = data.get("do", data.get("actions")) or []
        acts = acts if isinstance(acts, list) else [acts]
        for a in acts:
            self._add_editor(a if isinstance(a, dict) else {"keys": str(a)}, expanded=len(acts) == 1)
        self._refresh_actions()

        # --- Löschen ---
        if item:
            g = Adw.PreferencesGroup()
            row = Adw.ActionRow(title="Regel löschen", activatable=True, css_classes=["error"])
            row.add_prefix(Gtk.Image(icon_name="user-trash-symbolic"))
            row.connect("activated", self._delete)
            dup = Adw.ActionRow(title="Als Kopie duplizieren", activatable=True)
            dup.add_prefix(Gtk.Image(icon_name="edit-copy-symbolic"))
            dup.connect("activated", self._duplicate)
            g.add(dup)
            g.add(row)
            page.add(g)

        tv.set_content(page)
        self.set_child(tv)
        self.dirty = False
        if not item:
            GLib.idle_add(lambda: (self.name.grab_focus(), False)[1])

    # ---------- Auslöser ----------
    def _update_trigger_mode(self):
        self.hot_row.set_visible(self.t_hot.get_active())
        self.abbr_row.set_visible(self.t_abbr.get_active())
        self.gest_row.set_visible(self.t_gest.get_active())
        if not self.t_gest.get_active() and self.gest_rec.recording:
            self.gest_rec.stop()
        self._check_trigger()

    def _current_trigger(self) -> dict:
        if self.t_hot.get_active():
            return {"hotkey": self.recorder.value} if self.recorder.value else {}
        if self.t_gest.get_active():
            return {"gesture": self.gest_rec.value} if self.gest_rec.value else {}
        t = self.abbr_row.get_text().strip()
        return {"abbreviation": t} if t else {}

    def _check_trigger(self):
        trig = self._current_trigger()
        icon, text, cls = "dialog-information-symbolic", "", "dim-label"
        if not trig:
            text = "Noch kein Auslöser festgelegt"
        else:
            rule = {**trig, "apps": self._apps_value()}
            others = self.store.same_trigger(rule, exclude=self.item)
            sysmap = self.win.system_shortcuts or {}
            combo = None
            if "hotkey" in trig:
                try:
                    combo = Combo.parse(trig["hotkey"])
                except KeyError_:
                    pass
            gsys = getattr(self.win, "system_gestures", None) or {}
            gest = None
            if "gesture" in trig:
                from ..gestures import Gesture, GestureError
                try:
                    gest = Gesture.parse(trig["gesture"])
                except GestureError:
                    pass
            if gest is not None and gest in gsys:
                icon, cls = "dialog-warning-symbolic", "warning"
                text = f"Wird auch vom System genutzt ({gsys[gest]}) – beides würde ausgelöst"
            elif combo is not None and combo in sysmap:
                icon, cls = "dialog-warning-symbolic", "warning"
                text = f"Von GNOME belegt ({sysmap[combo]}) – wird so nicht ausgelöst"
            elif others:
                icon, cls = "dialog-warning-symbolic", "warning"
                text = f"Schon vergeben an „{others[0].name}“"
            elif combo is not None and not combo.mods and not self._apps_value():
                icon, cls = "dialog-warning-symbolic", "warning"
                text = "Ohne Zusatztaste – überschreibt die Taste in allen Programmen"
            elif "abbreviation" in trig and len(trig["abbreviation"]) < 3:
                icon, cls = "dialog-warning-symbolic", "warning"
                text = "Sehr kurz – löst eventuell beim normalen Schreiben aus"
            else:
                icon, cls = "emblem-ok-symbolic", "success"
                text = "Frei – kann verwendet werden"
        self.trig_icon.set_from_icon_name(icon)
        for c in ("dim-label", "warning", "success"):
            self.trig_icon.remove_css_class(c)
        self.trig_icon.add_css_class(cls)
        self.trig_status.set_title(GLib.markup_escape_text(text))

    # ---------- Programme ----------
    def _apps_value(self):
        if not hasattr(self, "everywhere"):  # während des Aufbaus
            return list(self.data.get("apps") or [])
        return [] if self.everywhere.get_active() else list(self.apps)

    def _refresh_apps(self):
        every = self.everywhere.get_active()
        self.apps_row.set_visible(not every)
        self.except_row.set_visible(every)
        for box, lst, is_ex in ((self.apps_box, self.apps, False), (self.except_box, self.not_apps, True)):
            while (c := box.get_first_child()):
                box.remove(c)
            for a in lst:
                box.append(app_chip(a, lambda wm, ex=is_ex: self._remove_app(wm, ex)))
            if not lst:
                box.append(Gtk.Label(label="Noch kein Programm" if not is_ex else "Keine Ausnahmen",
                                     css_classes=["dim-label"]))
        if not every and not self.apps:
            self.apps_row.set_subtitle("Füge mindestens ein Programm hinzu")
        else:
            self.apps_row.set_subtitle("")
        if hasattr(self, "trig_status"):
            self._check_trigger()

    def _pick_app(self, exception: bool):
        lst = self.not_apps if exception else self.apps

        def picked(key):
            if key not in lst:
                lst.append(key)
                self._refresh_apps()
                self.mark_dirty()
        AppPicker(picked, exclude=lst).present(self)

    def _remove_app(self, wm, exception):
        lst = self.not_apps if exception else self.apps
        if wm in lst:
            lst.remove(wm)
        self._refresh_apps()
        self.mark_dirty()

    # ---------- Aktionen ----------
    def _action_popover(self):
        pop = Gtk.Popover(has_arrow=False)
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        for kind in ACTION_ORDER:
            title, desc, icon, style = ACTION_META[kind]
            row = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=12, margin_top=3, margin_bottom=3, margin_start=4, margin_end=8)
            h.append(make_tile(icon, style, 30, 16))
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            v.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
            v.append(Gtk.Label(label=desc, xalign=0, css_classes=["dim-label", "caption"]))
            h.append(v)
            row.set_child(h)
            row.kind = kind
            box.append(row)

        def act(_b, row):
            pop.popdown()
            defaults = {"type": {"type": ""}, "keys": {"keys": ""}, "window": {"window": "left_half"},
                        "ai": {"ai": ""}, "open": {"open": ""}, "run": {"run": ""},
                        "workspace": {"workspace": "next"}, "delay": {"delay": 0.3}, "lock": {"lock": True},
                        "notify": {"notify": ""}, "clipboard": {"clipboard": "show"},
                        "screenshot": {"screenshot": "area"},
                        "app": {"app": ""}}
            for e in self.editors:
                e.row.set_expanded(False)
            self._add_editor(defaults[row.kind], expanded=True)
            self._refresh_actions()
            self.mark_dirty()
        box.connect("row-activated", act)
        # Die Liste ist lang – scrollbar machen, damit sie auch unten im Dialog ganz bedienbar bleibt
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                max_content_height=380, child=box)
        pop.set_child(sc)
        return pop

    def _add_editor(self, action: dict, expanded=False):
        ed = ActionEditor(self, action, expanded)
        self.editors.append(ed)

    def _refresh_actions(self):
        while (c := self.act_list.get_first_child()):
            self.act_list.remove(c)
        if not self.editors:
            self.act_list.append(self.act_empty)
        self.add_more.set_visible(bool(self.editors))
        for i, ed in enumerate(self.editors):
            self.act_list.append(ed.row)
            ed.up.set_sensitive(i > 0)
            ed.down.set_sensitive(i < len(self.editors) - 1)

    def move_action(self, ed, delta):
        i = self.editors.index(ed)
        j = i + delta
        if 0 <= j < len(self.editors):
            self.editors[i], self.editors[j] = self.editors[j], self.editors[i]
            self._refresh_actions()
            self.mark_dirty()

    def remove_action(self, ed):
        self.editors.remove(ed)
        self._refresh_actions()
        self.mark_dirty()

    # ---------- Speichern / Schließen ----------
    def mark_dirty(self):
        self.dirty = True

    def build(self) -> dict:
        rule = {k: v for k, v in self.data.items() if k not in ("hotkey", "abbreviation", "abbr", "gesture", "apps", "app",
                                                                 "not_apps", "do", "actions", "enabled", "name")}
        name = self.name.get_text().strip()
        trig = self._current_trigger()
        acts = [e.get() for e in self.editors]
        if not name:
            name = describe_action(acts[0]) if acts else "Neue Regel"
        out = {"name": name, **trig}
        if self._apps_value():
            out["apps"] = self._apps_value()
        if self.everywhere.get_active() and self.not_apps:
            out["not_apps"] = list(self.not_apps)
        out["enabled"] = self.enabled.get_active()
        out["do"] = acts
        out.update(rule)
        return out

    def _error(self, text):
        d = Adw.AlertDialog(heading="Noch nicht ganz fertig", body=text)
        d.add_response("ok", "OK")
        d.present(self)

    def _save(self, *_):
        trig = self._current_trigger()
        if not trig:
            return self._error("Lege einen Auslöser fest – ein Tastenkürzel, eine Tastenfolge oder eine Geste.")
        if not self.editors:
            return self._error("Füge mindestens eine Aktion hinzu.")
        if not self.everywhere.get_active() and not self.apps:
            return self._error("Wähle mindestens ein Programm – oder schalte „In allen Programmen“ ein.")
        rule = self.build()
        for a in rule["do"]:
            if "ai" in a and not a["ai"]:
                return self._error("Die Claude-Aktion braucht eine Anweisung.")
            if "keys" in a and not a["keys"]:
                return self._error("Bei „Tasten senden“ fehlen die Tasten.")
        try:
            if self.item:
                self.store.update(self.item, rule)
            else:
                self.store.add(rule)
        except ConfigError as e:
            return self._error(str(e).replace("Regel: ", ""))
        self.dirty = False
        self.win.refresh()
        self.win.toast("Gespeichert" if self.item else f"„{rule['name']}“ angelegt")
        self.force_close()

    def _on_close_attempt(self, *_):
        if not self.dirty:
            self.force_close()
            return
        d = Adw.AlertDialog(heading="Änderungen verwerfen?",
                            body="Du hast Änderungen an dieser Regel noch nicht gespeichert.")
        d.add_response("cancel", "Weiter bearbeiten")
        d.add_response("discard", "Verwerfen")
        d.add_response("save", "Speichern")
        d.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")

        def resp(_d, r):
            if r == "discard":
                self.dirty = False
                self.force_close()
            elif r == "save":
                self._save()
        d.connect("response", resp)
        d.present(self)

    def _delete(self, *_):
        self.dirty = False
        self.force_close()
        self.win.delete_item(self.item)

    def _duplicate(self, *_):
        self.dirty = False
        self.force_close()
        new = self.store.duplicate(self.item)
        self.win.refresh()
        self.win.toast("Kopie angelegt (ausgeschaltet)")
        RuleEditor(self.win, new).present(self.win)
