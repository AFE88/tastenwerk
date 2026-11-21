"""Wiederverwendbare Bausteine: Stil, Tastenkappen, Kürzel-Rekorder, Programm-Auswahl."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .. import control  # noqa: E402
from ..apps import Catalog  # noqa: E402
from ..keys import MOD_ORDER, Combo, KeyError_  # noqa: E402
from ..i18n import tr, trn  # noqa: E402

CSS = """
.keycap {
  font-family: "Cantarell", sans-serif;
  font-feature-settings: "tnum";
  font-size: 0.82em;
  font-weight: 700;
  padding: 1px 7px 2px 7px;
  min-width: 14px;
  border-radius: 6px;
  background: alpha(currentColor, 0.09);
  box-shadow: inset 0 -2px alpha(currentColor, 0.16), inset 0 0 0 1px alpha(currentColor, 0.08);
}
.keyplus { opacity: 0.45; font-size: 0.8em; margin: 0 1px; }
.seqpill {
  font-family: monospace;
  font-size: 0.86em;
  font-weight: 700;
  padding: 2px 9px;
  border-radius: 999px;
  color: @accent_color;
  background: alpha(@accent_bg_color, 0.16);
}
.gesturepill {
  font-size: 0.86em;
  font-weight: 700;
  padding: 2px 10px 2px 7px;
  border-radius: 999px;
  color: @purple_4;
  background: alpha(@purple_3, 0.14);
}
.gesturepill image { -gtk-icon-size: 14px; }
.dirbtn { min-width: 44px; min-height: 36px; font-size: 1.15em; font-weight: 700; padding: 0; }
.tile {
  border-radius: 10px;
  min-width: 34px;
  min-height: 34px;
  color: white;
}
.tile-keys   { background: linear-gradient(135deg, #3584e4, #1c71d8); }
.tile-text   { background: linear-gradient(135deg, #2ec27e, #26a269); }
.tile-window { background: linear-gradient(135deg, #9141ac, #813d9c); }
.tile-run    { background: linear-gradient(135deg, #5e5c64, #3d3846); }
.tile-open   { background: linear-gradient(135deg, #e5a50a, #c88800); }
.tile-ai     { background: linear-gradient(135deg, #e66100, #c64600); }
.tile-clip   { background: linear-gradient(135deg, #1c9ab0, #0f7d91); }
.tile-shot   { background: linear-gradient(135deg, #c061cb, #9141ac); }
.tile-lock   { background: linear-gradient(135deg, #e01b24, #c01c28); }
.tile-other  { background: linear-gradient(135deg, #77767b, #5e5c64); }
.dim-tile { opacity: 0.45; }
.recorder { padding: 4px 12px; min-height: 30px; }
.recorder.recording {
  box-shadow: inset 0 0 0 2px @accent_color;
  background: alpha(@accent_bg_color, 0.12);
}
.app-chip {
  padding: 2px 4px 2px 8px;
  border-radius: 999px;
  background: alpha(currentColor, 0.08);
}
.app-chip button { min-height: 20px; min-width: 20px; padding: 0; }
.status-card { padding: 10px 12px; border-radius: 12px; background: alpha(currentColor, 0.05); }
.status-dot { min-width: 9px; min-height: 9px; border-radius: 99px; background: @success_color; }
.status-dot.stopped { background: alpha(currentColor, 0.35); }
.status-dot.paused { background: @warning_color; }
.placeholder-btn { padding: 2px 10px; min-height: 24px; font-size: 0.9em; border-radius: 999px; }
.editor-text { padding: 10px; font-family: monospace; }
.textframe { border-radius: 10px; }
.count { opacity: 0.55; font-feature-settings: "tnum"; font-size: 0.9em; }
.sidebar-heading { font-size: 0.8em; font-weight: 700; opacity: 0.55; margin: 14px 12px 4px 12px; }
.hint-row { color: @warning_color; }
.big-number { font-size: 2.2em; font-weight: 800; }
"""


def install_css():
    prov = Gtk.CssProvider()
    prov.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


# ---------------------------------------------------------------------------
# Tasten hübsch darstellen
# ---------------------------------------------------------------------------
MOD_LABEL = {"ctrl": tr("Strg"), "alt": tr("Alt"), "shift": tr("⇧ Umschalt"), "super": "Super", "altgr": "AltGr"}
KEY_LABEL = {
    "Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "Return": tr("Enter"), "KP_Enter": tr("Enter"),
    "BackSpace": "⌫", "Delete": tr("Entf"), "Escape": tr("Esc"), "Tab": "Tab", "space": tr("Leertaste"),
    "Prior": tr("Bild ↑"), "Next": tr("Bild ↓"), "Home": "Pos1", "End": tr("Ende"), "Insert": tr("Einfg"),
    "Print": tr("Druck"), "numbersign": "#", "less": "<", "greater": ">", "plus": "+", "minus": "-",
    "period": ".", "comma": ",", "ssharp": "ß", "adiaeresis": "Ä", "odiaeresis": "Ö", "udiaeresis": "Ü",
    "dead_circumflex": "^", "dead_acute": "´", "asciicircum": "^", "Super_L": "Super",
    "Control_L": tr("Strg"), "Alt_L": tr("Alt"), "Shift_L": tr("Umschalt"), "XF86AudioNext": "⏭", "XF86AudioPrev": "⏮",
    "XF86AudioPlay": "⏯", "XF86Tools": "F13", "Menu": tr("Menü"), "Caps_Lock": tr("Feststell"),
}


def key_label(name: str) -> str:
    if name in KEY_LABEL:
        return KEY_LABEL[name]
    if name.startswith("KP_"):
        return "Num " + name[3:]
    if len(name) == 1:
        return name.upper()
    return name


def combo_parts(text: str) -> list[str]:
    try:
        c = Combo.parse(text)
    except KeyError_:
        return [text]
    return [MOD_LABEL[m] for m in MOD_ORDER if m in c.mods] + [key_label(c.key)]


def keycaps(text: str, dim: bool = False) -> Gtk.Widget:
    """„ctrl+super+left“ als Reihe kleiner Tastenkappen."""
    box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
    parts = combo_parts(text)
    for i, p in enumerate(parts):
        if i:
            box.append(Gtk.Label(label="+", css_classes=["keyplus"]))
        box.append(Gtk.Label(label=p, css_classes=["keycap"]))
    if dim:
        box.add_css_class("dim-label")
    return box


def sequence_keycaps(text: str) -> Gtk.Widget:
    """Mehrere Kombinationen nacheinander („tab tab ctrl+v“)."""
    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    for part in text.split():
        box.append(keycaps(part))
    return box


def gesture_pill(text: str, dim: bool = False) -> Gtk.Widget:
    """„swipe up 4“ → ein Etikett mit Touchpad-Symbol: [▭ 4 Finger ↑]."""
    from ..gestures import ARROW, Gesture, GestureError
    try:
        g = Gesture.parse(text)
        label, tip = tr("{n} Finger {arrow}").format(n=g.fingers, arrow=ARROW[g.direction]), g.label
    except GestureError:
        label, tip = str(text), None
    box = Gtk.Box(spacing=5, css_classes=["gesturepill"], valign=Gtk.Align.CENTER, tooltip_text=tip)
    box.append(Gtk.Image(icon_name="input-touchpad-symbolic"))
    box.append(Gtk.Label(label=label))
    if dim:
        box.add_css_class("dim-label")
    return box


def trigger_widget(item_data: dict, dim: bool = False) -> Gtk.Widget:
    hk = item_data.get("hotkey")
    if hk:
        return keycaps(str(hk), dim)
    if item_data.get("gesture"):
        return gesture_pill(str(item_data["gesture"]), dim)
    ab = item_data.get("abbreviation") or item_data.get("abbr") or "?"
    lbl = Gtk.Label(label=str(ab), css_classes=["seqpill"], valign=Gtk.Align.CENTER)
    if dim:
        lbl.add_css_class("dim-label")
    return lbl


# ---------------------------------------------------------------------------
# Aktionstypen
# ---------------------------------------------------------------------------
ACTION_META = {
    # typ: (Titel, Beschreibung, Icon, Kachel-Stil)
    "type": (tr("Text einfügen"), tr("Text-Baustein tippen oder einfügen"), "insert-text-symbolic", "tile-text"),
    "keys": (tr("Tasten senden"), tr("Eine oder mehrere Tastenkombinationen"), "input-keyboard-symbolic", "tile-keys"),
    "window": (tr("Fenster anordnen"), tr("Andocken, maximieren, Monitor wechseln"), "view-dual-symbolic", "tile-window"),
    "ai": (tr("Mit Claude umformen"), tr("Markierten Text umschreiben lassen"), "starred-symbolic", "tile-ai"),
    "open": (tr("Öffnen"), tr("Datei, Ordner, Webseite oder Programm"), "folder-open-symbolic", "tile-open"),
    "run": (tr("Befehl ausführen"), tr("Shell-Befehl, optional mit Ausgabe"), "utilities-terminal-symbolic", "tile-run"),
    "workspace": (tr("Arbeitsfläche wechseln"), tr("Nächste, vorherige oder bestimmte"), "view-paged-symbolic", "tile-window"),
    "delay": (tr("Warten"), tr("Pause zwischen Aktionen"), "alarm-symbolic", "tile-other"),
    "clipboard": (tr("Zwischenablage-Verlauf"), tr("Frühere Kopien suchen und einfügen"), "edit-paste-symbolic", "tile-clip"),
    "app": (tr("Programm öffnen / wechseln"), tr("Läuft es, nach vorne holen – sonst starten"),
            "view-app-grid-symbolic", "tile-open"),
    "screenshot": (tr("Bildschirmfoto"), tr("Bereich, Bildschirm oder Fenster aufnehmen"),
                   "accessories-screenshot-symbolic", "tile-shot"),
    "lock": (tr("Bildschirm sperren"), tr("Sitzung sperren"), "system-lock-screen-symbolic", "tile-lock"),
    "notify": (tr("Benachrichtigung"), tr("Kurze Meldung anzeigen"), "preferences-system-notifications-symbolic", "tile-other"),
}
ACTION_ORDER = ["type", "keys", "app", "window", "clipboard", "screenshot", "ai", "open", "run", "workspace", "delay", "lock", "notify"]


def action_kind(a: dict) -> str:
    for k in a:
        if k in ACTION_META:
            return k
        if k == "paste":
            return "type"
    return "notify"


def action_tile(actions: list[dict], dim: bool = False) -> Gtk.Widget:
    kind = action_kind(actions[0]) if actions else "notify"
    _, _, icon, style = ACTION_META[kind]
    return make_tile(icon, style, 34, 18, dim)


def make_tile(icon: str, style: str, size: int = 34, icon_size: int = 18, dim: bool = False) -> Gtk.Widget:
    """Farbige, quadratische Kachel mit Symbol (dehnt sich nicht aus)."""
    img = Gtk.Image(icon_name=icon, pixel_size=icon_size, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    tile = Gtk.CenterBox(css_classes=["tile", style], valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                         hexpand=False, vexpand=False)
    tile.set_size_request(size, size)
    tile.set_center_widget(img)
    if dim:
        tile.add_css_class("dim-tile")
    return tile


# ---------------------------------------------------------------------------
# Programme (WM_CLASS ↔ App-Infos mit Symbol und Namen)
# ---------------------------------------------------------------------------
class AppCatalog(Catalog):
    """App-Katalog der Oberfläche: wie ``apps.Catalog``, dazu die gerade offenen Programme."""
    _inst = None

    def open_windows(self) -> list[str]:
        try:
            from ..x11 import X11
            x = X11()
            seen = []
            for w in x.client_list():
                cls = x.wm_class(w)
                if cls and cls[-1] not in seen and cls[-1] != control.APP_ID:
                    seen.append(cls[-1])
            return seen
        except Exception:
            return []


def app_image(wm: str, size: int = 16) -> Gtk.Image:
    img = Gtk.Image(gicon=AppCatalog.get().icon(wm), pixel_size=size)
    return img


class AppPicker(Adw.Dialog):
    """Dialog zur Auswahl eines Programms (offene Fenster zuerst, dann alle installierten)."""

    def __init__(self, on_pick, exclude=()):
        super().__init__(title=tr("Programm wählen"), content_width=420, content_height=560)
        self.on_pick = on_pick
        cat = AppCatalog.get()
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        self.search = Gtk.SearchEntry(placeholder_text=tr("Programm suchen"), hexpand=True)
        sbar = Gtk.Box(margin_start=12, margin_end=12, margin_bottom=6)
        sbar.append(self.search)
        tv.add_top_bar(sbar)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=6, margin_bottom=18,
                      margin_start=12, margin_end=12)
        self.rows = []
        excl = {e.lower() for e in exclude}

        def section(title, entries):
            if not entries:
                return
            g = Adw.PreferencesGroup(title=title)
            for key, info in entries:
                if key.lower() in excl:
                    continue
                name = info.get_display_name() if info else key
                row = Adw.ActionRow(title=GLib.markup_escape_text(name), activatable=True,
                                    subtitle=GLib.markup_escape_text(key))
                img = Gtk.Image(gicon=(info.get_icon() if info and info.get_icon() else
                                       Gio.ThemedIcon.new("application-x-executable-symbolic")), pixel_size=32)
                row.add_prefix(img)
                row.connect("activated", lambda _r, k=key: self._pick(k))
                g.add(row)
                self.rows.append((row, (name + " " + key).casefold()))
            box.append(g)

        open_w = [(w, cat.lookup(w)) for w in cat.open_windows()]
        section(tr("Gerade geöffnet"), open_w)
        section(tr("Alle Programme"), [(k, i) for k, i in cat.apps])
        manual = Adw.PreferencesGroup(title=tr("Nicht in der Liste?"),
                                      description=tr("Programmnamen (WM_CLASS) direkt eintragen, z. B. „code“"))
        self.manual = Adw.EntryRow(title=tr("Programmname"))
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                         tooltip_text=tr("Hinzufügen"))
        add.connect("clicked", lambda *_: self.manual.get_text().strip() and self._pick(self.manual.get_text().strip()))
        self.manual.add_suffix(add)
        self.manual.connect("entry-activated", lambda *_: self.manual.get_text().strip() and self._pick(self.manual.get_text().strip()))
        manual.add(self.manual)
        box.append(manual)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(child=box, maximum_size=520)
        sc.set_child(clamp)
        tv.set_content(sc)
        self.set_child(tv)
        self.search.connect("search-changed", self._filter)

    def _filter(self, *_):
        q = self.search.get_text().casefold().strip()
        for row, text in self.rows:
            row.set_visible(not q or q in text)

    def _pick(self, key):
        self.on_pick(key)
        self.close()


def app_chip(wm: str, on_remove) -> Gtk.Widget:
    box = Gtk.Box(spacing=6, css_classes=["app-chip"], valign=Gtk.Align.CENTER)
    box.append(app_image(wm, 16))
    box.append(Gtk.Label(label=AppCatalog.get().name(wm), ellipsize=Pango.EllipsizeMode.END, max_width_chars=22))
    btn = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"],
                     tooltip_text=tr("Entfernen"), valign=Gtk.Align.CENTER)
    btn.connect("clicked", lambda *_: on_remove(wm))
    box.append(btn)
    return box


# ---------------------------------------------------------------------------
# Kürzel-Rekorder
# ---------------------------------------------------------------------------
MOD_KEYVALS = {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Super_L", "Super_R",
               "Meta_L", "Meta_R", "ISO_Level3_Shift", "Hyper_L", "Hyper_R", "Caps_Lock", "Num_Lock"}


def event_to_combo(widget: Gtk.Widget, keyval: int, keycode: int, state: Gdk.ModifierType) -> tuple[str | None, list[str]]:
    """Tastendruck -> (Kombination oder None bei reinen Modifikatoren, gedrückte Modifikatoren)."""
    mods = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        mods.append("ctrl")
    if state & Gdk.ModifierType.ALT_MASK:
        mods.append("alt")
    if state & Gdk.ModifierType.SHIFT_MASK:
        mods.append("shift")
    if state & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK):
        mods.append("super")
    name = Gdk.keyval_name(keyval) or ""
    if name in MOD_KEYVALS:
        extra = {"Control": "ctrl", "Shift": "shift", "Alt": "alt", "Super": "super", "Meta": "super"}
        for k, v in extra.items():
            if name.startswith(k) and v not in mods:
                mods.append(v)
        return None, mods
    # Taste ohne Umschalt-Ebene bestimmen (Umschalt+3 soll „shift+3“ sein, nicht „§“)
    base = keyval
    try:
        ok, keys, vals = widget.get_display().map_keycode(keycode)
        if ok:
            for k, v in zip(keys, vals):
                if k.level == 0 and k.group == 0:
                    base = v
                    break
    except Exception:
        pass
    bname = Gdk.keyval_name(Gdk.keyval_to_lower(base)) or name
    try:
        combo = str(Combo.parse("+".join(mods + [bname])))
    except KeyError_:
        return None, mods
    return combo, mods


class KeyRecorder(Gtk.Button):
    """Schaltfläche, die nach dem Klick die nächste Tastenkombination aufnimmt."""

    __gsignals__ = {"recorded": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, value: str | None = None, placeholder=tr("Kürzel aufnehmen"), allow_bare=True):
        super().__init__(css_classes=["recorder"], valign=Gtk.Align.CENTER)
        self.value = value
        self.placeholder = placeholder
        self.allow_bare = allow_bare
        self.recording = False
        self._paused = False
        ctl = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._on_key)
        self.add_controller(ctl)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.recording and self.stop(cancel=True))
        self.add_controller(focus)
        self.connect("clicked", lambda *_: self.stop(cancel=True) if self.recording else self.start())
        self._show()

    def _show(self, mods=None):
        if self.recording:
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(icon_name="media-record-symbolic"))
            if mods:
                box.append(keycaps("+".join(mods + ["…"])))
            else:
                box.append(Gtk.Label(label=tr("Tasten drücken …")))
            self.set_child(box)
            self.set_tooltip_text(tr("Esc bricht ab · Rücktaste löscht"))
        elif self.value:
            self.set_child(keycaps(self.value))
            self.set_tooltip_text(tr("Klicken, um neu aufzunehmen"))
        else:
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(icon_name="input-keyboard-symbolic"))
            box.append(Gtk.Label(label=self.placeholder))
            self.set_child(box)
            self.set_tooltip_text(None)

    def start(self):
        self.recording = True
        self.add_css_class("recording")
        self.grab_focus()
        # Laufenden Hintergrunddienst pausieren, sonst schnappt er bekannte Kürzel weg
        self._paused = control.pause()
        self._show()

    def stop(self, cancel=False):
        self.recording = False
        self.remove_css_class("recording")
        if self._paused:
            control.resume()
            self._paused = False
        self._show()

    def _on_key(self, _ctl, keyval, keycode, state):
        if not self.recording:
            return False
        name = Gdk.keyval_name(keyval)
        plain = not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
                              Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.SHIFT_MASK))
        if name == "Escape" and plain:
            self.stop(cancel=True)
            return True
        if name == "BackSpace" and plain:
            self.value = None
            self.stop()
            self.emit("recorded", "")
            return True
        combo, mods = event_to_combo(self, keyval, keycode, state)
        if combo is None:
            self._show(mods)
            return True
        self.value = combo
        self.stop()
        self.emit("recorded", combo)
        return True

    def set_value(self, v):
        self.value = v or None
        self._show()


class GestureRecorder(Gtk.Box):
    """Geste aufnehmen (einfach auf dem Touchpad ausführen) oder von Hand wählen."""

    __gsignals__ = {"recorded": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, value: str | None = None):
        super().__init__(spacing=6, valign=Gtk.Align.CENTER)
        from ..gestures import Gesture, GestureError
        try:
            self.value = str(Gesture.parse(value)) if value else None
        except GestureError:
            self.value = None
        self.recording = False
        self._paused = False
        self._listener = None
        self._timeout = None
        self.btn = Gtk.Button(css_classes=["recorder"], valign=Gtk.Align.CENTER)
        self.btn.connect("clicked", lambda *_: self.stop() if self.recording else self.start())
        self.append(self.btn)
        self.menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER,
                                   css_classes=["flat"], tooltip_text=tr("Geste von Hand wählen"))
        self.menu.set_popover(self._picker())
        self.append(self.menu)
        self._show()

    # --- Anzeige ---
    def _show(self):
        if self.recording:
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(icon_name="media-record-symbolic"))
            box.append(Gtk.Label(label=tr("Jetzt auf dem Touchpad wischen …")))
            self.btn.set_child(box)
            self.btn.set_tooltip_text(tr("Klicken bricht ab"))
        elif self.value:
            self.btn.set_child(gesture_pill(self.value))
            self.btn.set_tooltip_text(tr("Klicken und Geste neu ausführen"))
        else:
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(icon_name="input-touchpad-symbolic"))
            box.append(Gtk.Label(label=tr("Geste aufnehmen")))
            self.btn.set_child(box)
            self.btn.set_tooltip_text(tr("Klicken und die Geste auf dem Touchpad ausführen"))

    # --- Aufnehmen über Touchégg ---
    def start(self):
        from ..gestures import GestureListener, all_gestures
        self._listener = GestureListener(self._on_gesture)
        self._listener.set_gestures(all_gestures())
        if self._listener.conn is None:
            self._listener.close()
            self._listener = None
            toast(self, tr("Touchpad-Gesten nicht erreichbar – bitte von Hand wählen (⋯)"))
            self.menu.popup()
            return
        self.recording = True
        self.btn.add_css_class("recording")
        self._paused = control.pause()   # sonst löst die gerade belegte Geste aus
        self._timeout = GLib.timeout_add_seconds(15, lambda: (self.stop(), False)[1])
        self._show()

    def stop(self):
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = None
        if self._listener:
            self._listener.close()
            self._listener = None
        if self._paused:
            control.resume()
            self._paused = False
        self.recording = False
        self.btn.remove_css_class("recording")
        self._show()

    def _on_gesture(self, g):
        self.value = str(g)
        GLib.idle_add(self._finish)

    def _finish(self):
        self.stop()
        self.emit("recorded", self.value or "")
        return False

    # --- Von Hand ---
    def _picker(self) -> Gtk.Popover:
        from ..gestures import ARROW, DIR_LABEL
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=tr("Finger"), xalign=0, css_classes=["heading"]))
        fb = Gtk.Box(css_classes=["linked"], homogeneous=True)
        self._fbtn = {}
        first = None
        for n in (3, 4, 5):
            b = Gtk.ToggleButton(label=str(n), group=first)
            first = first or b
            fb.append(b)
            self._fbtn[n] = b
        try:
            cur = int(str(self.value).split()[-1]) if self.value else 4
        except ValueError:
            cur = 4
        self._fbtn.get(cur, self._fbtn[4]).set_active(True)
        box.append(fb)
        box.append(Gtk.Label(label=tr("Wischen"), xalign=0, css_classes=["heading"]))
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, halign=Gtk.Align.CENTER)
        pos = {"up": (1, 0), "left": (0, 1), "right": (2, 1), "down": (1, 2)}

        def pick(_b, kind, d):
            n = next(k for k, b in self._fbtn.items() if b.get_active())
            self.value = f"{kind} {d} {n}"
            pop.popdown()
            self._show()
            self.emit("recorded", self.value)
        for d, (x, y) in pos.items():
            b = Gtk.Button(label=ARROW[d], css_classes=["dirbtn"], tooltip_text=DIR_LABEL[d])
            b.connect("clicked", pick, "swipe", d)
            grid.attach(b, x, y, 1, 1)
        grid.attach(Gtk.Image(icon_name="input-touchpad-symbolic", pixel_size=20, css_classes=["dim-label"]),
                    1, 1, 1, 1)
        box.append(grid)
        box.append(Gtk.Label(label=tr("Zusammenziehen"), xalign=0, css_classes=["heading"]))
        pb = Gtk.Box(spacing=6, homogeneous=True)
        for d, text in (("in", tr("→← zusammen")), ("out", tr("←→ auseinander"))):
            b = Gtk.Button(label=text)
            b.connect("clicked", pick, "pinch", d)
            pb.append(b)
        box.append(pb)
        pop.set_child(box)
        return pop

    def set_value(self, v):
        self.value = v or None
        self._show()


def toast(widget: Gtk.Widget, text: str, button: str | None = None, on_button=None, timeout=3):
    root = widget.get_root()
    overlay = getattr(root, "toast_overlay", None)
    if overlay is None:
        return
    t = Adw.Toast(title=text, timeout=timeout)
    if button:
        t.set_button_label(button)
        t.connect("button-clicked", lambda *_: on_button())
    overlay.add_toast(t)
