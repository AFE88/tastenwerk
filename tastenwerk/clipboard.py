"""Zwischenablage-Verlauf: im Dienst mitschreiben und als schnelles Fenster zum Einfügen zeigen.

Alles läuft im GTK-3-Hauptthread des Dienstes. Deshalb erscheint das Fenster ohne Startverzögerung.
Der Verlauf liegt nur auf diesem Rechner (~/.local/share/tastenwerk/clipboard, nur für dich lesbar).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, Pango  # noqa: E402

from .mainthread import GLib, Gdk, Gtk  # noqa: E402

log = logging.getLogger("tastenwerk")

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tastenwerk" / "clipboard"
HISTORY_FILE = DATA_DIR / "history.json"
IMG_DIR = DATA_DIR / "images"

from .clipboard_defaults import DEFAULTS  # noqa: E402
MAX_TEXT = 500_000
MAX_IMAGE_PIXELS = 40_000_000
SECRET_TARGETS = {"x-kde-passwordManagerHint", "application/x-kde-passwordManagerHint"}


def settings_of(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("clipboard") or {})}


def _app_name(wm: tuple[str, ...]) -> str:
    if not wm:
        return ""
    for cand in wm:
        for did in (cand, cand.lower()):
            try:
                info = Gio.DesktopAppInfo.new(did + ".desktop")
            except TypeError:
                info = None
            if info:
                return info.get_name()
    aliases = {"gnome-terminal-server": "Terminal", "zen": "Zen", "org.gnome.nautilus": "Dateien"}
    return aliases.get(wm[0].lower(), wm[-1])


def relative_time(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return "gerade eben"
    if d < 3600:
        return f"vor {int(d // 60)} Min."
    if d < 86400 and datetime.fromtimestamp(ts).date() == datetime.now().date():
        return f"vor {int(d // 3600)} Std."
    if d < 2 * 86400:
        return "gestern"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")


# =============================================================================
# Verlauf
# =============================================================================
class History:
    def __init__(self, settings_getter):
        self._settings = settings_getter
        self.items: list[dict] = []
        self.listeners: list = []
        self._block = 0
        self._quiet_until = 0.0
        self._x = None
        self._gen = 0            # zählt fremde Kopien – für das automatische Leeren
        self.load()

    @property
    def cfg(self) -> dict:
        return settings_of(self._settings())

    # ---------- Speichern ----------
    def load(self):
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            self.items = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("kind")]
        except FileNotFoundError:
            self.items = []
        except (OSError, ValueError) as e:
            log.warning("Zwischenablage-Verlauf nicht lesbar (%s) – starte leer", e)
            self.items = []
        self.prune(save=False)
        self._changed(save=False)

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(DATA_DIR, 0o700)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": self.items}, f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)

    def _changed(self, save=True):
        if save:
            try:
                self.save()
            except OSError as e:
                log.warning("Zwischenablage-Verlauf nicht speicherbar: %s", e)
        for fn in list(self.listeners):
            fn()

    def prune(self, save=True):
        cfg = self.cfg
        from .clipboard_defaults import keep_hours
        hours = keep_hours(cfg)
        limit = time.time() - hours * 3600 if hours > 0 else None
        kept, n = [], 0
        self.items.sort(key=lambda i: i.get("time", 0), reverse=True)
        for it in self.items:
            if it.get("pinned"):
                kept.append(it)
                continue
            if limit and it.get("time", 0) < limit:
                continue
            n += 1
            if n > int(cfg.get("max_items") or 300):
                continue
            kept.append(it)
        removed = [i for i in self.items if i not in kept]
        self.items = kept
        for it in removed:
            self._drop_file(it)
        if removed and save:
            self._changed()
        return bool(removed)

    def _drop_file(self, it):
        if it.get("kind") == "image" and it.get("file"):
            if not any(o is not it and o.get("file") == it["file"] for o in self.items):
                with contextlib.suppress(OSError):
                    (IMG_DIR / it["file"]).unlink()

    # ---------- Eigene Zugriffe ausblenden ----------
    @contextlib.contextmanager
    def quiet(self, after: float = 0.6):
        """Solange Tastenwerk selbst die Zwischenablage benutzt, nichts mitschreiben."""
        self._block += 1
        try:
            yield
        finally:
            self._block -= 1
            self._quiet_until = time.monotonic() + after

    def is_quiet(self) -> bool:
        return self._block > 0 or time.monotonic() < self._quiet_until

    # ---------- Mitschreiben ----------
    def start(self):
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.connect("owner-change", self._on_owner_change)
        # Abgelaufenes auch dann entfernen, wenn gerade nichts kopiert wird
        GLib.timeout_add_seconds(300, lambda: (self.prune(), True)[1])

    def _active_app(self) -> tuple[str, ...]:
        try:
            if self._x is None:
                from .x11 import X11
                self._x = X11()
            return self._x.active_app()
        except Exception:
            return ()

    def _on_owner_change(self, cb, _event):
        if self.is_quiet():
            return
        cfg = self.cfg
        self._gen += 1
        recording = bool(cfg.get("history", True))
        if not recording and cfg.get("clear_scope", "never") == "never":
            return
        app = self._active_app()
        excluded = {a.lower() for a in cfg.get("exclude_apps") or []}
        from_secret_app = bool({c.lower() for c in app} & excluded)
        cb.request_targets(self._got_targets, (app, self._gen, from_secret_app))

    def _got_targets(self, cb, *args):
        atoms = next((a for a in args if isinstance(a, list)), [])
        app, gen, from_secret_app = args[-1] if args and isinstance(args[-1], tuple) else ((), self._gen, False)
        names = {a.name() for a in atoms if a is not None}
        secret = from_secret_app or bool(names & SECRET_TARGETS)
        self._schedule_clear(gen, secret)
        if secret:
            log.debug("Passwort-Kopie – nicht gespeichert")
            return
        if not self.cfg.get("history", True):
            return
        has_text = bool(names & {"UTF8_STRING", "STRING", "TEXT", "text/plain", "text/plain;charset=utf-8"})
        has_image = any(n.startswith("image/") for n in names)
        if has_image and not has_text and self.cfg.get("images", True):
            cb.request_image(self._got_image, app)
        elif has_text or not names:
            cb.request_text(self._got_text, app)

    # ---------- Automatisch leeren ----------
    def _schedule_clear(self, gen: int, secret: bool):
        cfg = self.cfg
        scope = cfg.get("clear_scope", "never")
        if scope == "all" or (scope == "secrets" and secret):
            secs = max(5, int(cfg.get("clear_after") or 60))
            GLib.timeout_add_seconds(secs, self._clear_now, gen, secs)

    def _clear_now(self, gen: int, secs: int):
        if gen != self._gen:
            return False   # inzwischen wurde etwas Neues kopiert – das bekommt seinen eigenen Zeitgeber
        with self.quiet():
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_text("", 0)
            cb.clear()
        log.info("Zwischenablage nach %d s geleert", secs)
        return False

    def _got_text(self, _cb, text, app):
        if not text or not text.strip() or len(text) > MAX_TEXT:
            return
        h = hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()
        self._add({"kind": "text", "text": text, "hash": h}, app)

    def _got_image(self, _cb, pixbuf, app):
        if pixbuf is None or pixbuf.get_width() * pixbuf.get_height() > MAX_IMAGE_PIXELS:
            return
        raw = pixbuf.get_pixels() if hasattr(pixbuf, "get_pixels") else b""
        h = hashlib.sha1(bytes(raw) + f"{pixbuf.get_width()}x{pixbuf.get_height()}".encode()).hexdigest()
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(IMG_DIR, 0o700)
        fname = h[:20] + ".png"
        path = IMG_DIR / fname
        if not path.exists():
            try:
                pixbuf.savev(str(path), "png", [], [])
                os.chmod(path, 0o600)
            except GLib.Error as e:
                log.warning("Bild aus der Zwischenablage nicht speicherbar: %s", e.message)
                return
        self._add({"kind": "image", "file": fname, "hash": h,
                   "width": pixbuf.get_width(), "height": pixbuf.get_height()}, app)

    def _add(self, item: dict, app: tuple[str, ...]):
        now = time.time()
        for it in self.items:
            if it.get("hash") == item["hash"]:
                it["time"] = now
                it["app"] = _app_name(app) or it.get("app", "")
                self._changed()
                return
        item.update({"id": uuid.uuid4().hex[:12], "time": now, "app": _app_name(app), "pinned": False})
        self.items.insert(0, item)
        self.prune(save=False)
        self._changed()

    # ---------- Bearbeiten ----------
    def get(self, item_id: str) -> dict | None:
        return next((i for i in self.items if i.get("id") == item_id), None)

    def touch(self, item_id: str):
        it = self.get(item_id)
        if it and self.cfg.get("move_to_top", True):
            it["time"] = time.time()
            self._changed()

    def toggle_pin(self, item_id: str):
        it = self.get(item_id)
        if it:
            it["pinned"] = not it.get("pinned")
            self._changed()

    def delete(self, item_id: str):
        it = self.get(item_id)
        if it:
            self.items.remove(it)
            self._drop_file(it)
            self._changed()

    def clear(self, keep_pinned: bool = True):
        removed = [i for i in self.items if not (keep_pinned and i.get("pinned"))]
        self.items = [i for i in self.items if keep_pinned and i.get("pinned")]
        for it in removed:
            self._drop_file(it)
        self._changed()

    def ordered(self) -> list[dict]:
        pinned = [i for i in self.items if i.get("pinned")]
        rest = sorted((i for i in self.items if not i.get("pinned")), key=lambda i: i.get("time", 0), reverse=True)
        return pinned + rest

    def set_clipboard(self, it: dict):
        """Eintrag in die Zwischenablage legen (Hauptthread)."""
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if it["kind"] == "text":
            cb.set_text(it["text"], -1)
        else:
            pb = GdkPixbuf.Pixbuf.new_from_file(str(IMG_DIR / it["file"]))
            cb.set_image(pb)
        cb.store()


# =============================================================================
# Fenster
# =============================================================================
CSS = b"""
.tw-clip {
  background-color: @theme_base_color;
  border-radius: 14px;
  border: 1px solid alpha(@theme_fg_color, 0.14);
  box-shadow: 0 6px 24px alpha(black, 0.35);
}
.tw-clip-head { padding: 12px 12px 8px 12px; }
.tw-clip-head entry { min-height: 34px; border-radius: 9px; }
.tw-clip list { background: transparent; }
.tw-clip row { border-radius: 9px; margin: 1px 8px; padding: 6px 8px; }
.tw-clip row:selected { background-color: alpha(@theme_selected_bg_color, 0.85); }
.tw-clip row:selected label { color: @theme_selected_fg_color; }
.tw-clip .num {
  font-size: 0.78em; font-weight: bold; min-width: 18px; min-height: 18px;
  border-radius: 6px; background-color: alpha(@theme_fg_color, 0.08);
}
.tw-clip row:selected .num { background-color: alpha(white, 0.22); }
.tw-clip .meta { font-size: 0.8em; opacity: 0.62; }
.tw-clip .section { font-size: 0.78em; font-weight: bold; opacity: 0.55; margin: 8px 18px 2px 18px; }
.tw-clip .rowbtn { opacity: 0; min-width: 26px; min-height: 26px; padding: 0; }
.tw-clip row:hover .rowbtn, .tw-clip row:selected .rowbtn, .tw-clip .rowbtn.on { opacity: 1; }
.tw-clip .rowbtn.on { color: #e5a50a; }
.tw-clip .foot { font-size: 0.78em; opacity: 0.55; padding: 7px 14px 10px 14px; }
.tw-clip .empty { opacity: 0.55; margin: 40px 20px; }
.tw-clip .thumb { border-radius: 6px; }
.tw-clip .filter { min-height: 34px; min-width: 34px; border-radius: 9px; }
"""

WIDTH, HEIGHT = 460, 540


class ClipPopup:
    def __init__(self, history: History, on_choose):
        self.history = history
        self.on_choose = on_choose       # fn(item, mode, prev_window_id)  mode: paste | copy
        self.win = None
        self.prev_xid = None
        self._shown_at = 0.0
        self.only_pinned = False
        self._pix_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        history.listeners.append(self._on_history_changed)

    # ---------- Aufbau ----------
    def _build(self):
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        w = Gtk.Window(title="Zwischenablage")
        w.set_wmclass("tastenwerk-clipboard", "Tastenwerk")
        w.set_decorated(False)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_keep_above(True)
        w.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        w.set_default_size(WIDTH, HEIGHT)
        w.set_resizable(False)
        screen = w.get_screen()
        vis = screen.get_rgba_visual()
        if vis is not None and screen.is_composited():
            w.set_visual(vis)
            w.set_app_paintable(True)
        w.connect("draw", self._clear_bg)
        w.connect("key-press-event", self._on_key)
        w.connect("focus-out-event", self._on_focus_out)
        w.connect("delete-event", lambda *_: (self.hide(), True)[1])

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=10)
        root.get_style_context().add_class("tw-clip")
        head = Gtk.Box(spacing=6)
        head.get_style_context().add_class("tw-clip-head")
        self.search = Gtk.SearchEntry(placeholder_text="Verlauf durchsuchen …", hexpand=True)
        self.search.connect("search-changed", lambda *_: self._fill())
        head.pack_start(self.search, True, True, 0)
        self.pin_filter = Gtk.ToggleButton(tooltip_text="Nur Angeheftete (Tab)")
        self.pin_filter.add(Gtk.Image.new_from_icon_name("starred-symbolic", Gtk.IconSize.BUTTON))
        self.pin_filter.get_style_context().add_class("filter")
        self.pin_filter.set_can_focus(False)
        self.pin_filter.connect("toggled", lambda b: (setattr(self, "only_pinned", b.get_active()), self._fill()))
        head.pack_start(self.pin_filter, False, False, 0)
        root.pack_start(head, False, False, 0)

        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, activate_on_single_click=True)
        self.list.set_can_focus(False)
        self.list.set_header_func(self._header)
        self.list.connect("row-activated", lambda _l, row: self._choose(row, "paste"))
        self.scroller.add(self.list)
        self.empty = Gtk.Label(label="", justify=Gtk.Justification.CENTER, wrap=True)
        self.empty.get_style_context().add_class("empty")
        self.stack = Gtk.Stack()
        self.stack.add_named(self.scroller, "list")
        self.stack.add_named(self.empty, "empty")
        root.pack_start(self.stack, True, True, 0)
        foot = Gtk.Label(label="↵ Einfügen   ⇧↵ Nur kopieren   Strg+1–9   Strg+P Anheften   Entf Löschen",
                         xalign=0, ellipsize=Pango.EllipsizeMode.END)
        foot.get_style_context().add_class("foot")
        root.pack_start(foot, False, False, 0)
        w.add(root)
        self.win = w

    @staticmethod
    def _clear_bg(widget, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(0)  # CLEAR
        cr.paint()
        cr.set_operator(2)  # OVER
        return False

    def _header(self, row, before):
        want = None
        if not self.search.get_text() and not self.only_pinned:
            pinned = row.item.get("pinned")
            if before is None:
                want = "Angeheftet" if pinned else None
            elif before.item.get("pinned") and not pinned:
                want = "Verlauf"
        if want:
            lbl = Gtk.Label(label=want.upper(), xalign=0)
            lbl.get_style_context().add_class("section")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def _thumb(self, it) -> GdkPixbuf.Pixbuf | None:
        key = it["file"]
        if key not in self._pix_cache:
            try:
                self._pix_cache[key] = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(IMG_DIR / key), 300, 84, True)
            except GLib.Error:
                self._pix_cache[key] = None
        return self._pix_cache[key]

    def _row(self, it: dict, index: int) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.item = it
        box = Gtk.Box(spacing=10)
        num = Gtk.Label(label=str(index) if index <= 9 else "", valign=Gtk.Align.START, margin_top=2)
        if index <= 9:
            num.get_style_context().add_class("num")
        else:
            num.set_size_request(18, -1)
        box.pack_start(num, False, False, 0)
        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        if it["kind"] == "image":
            pb = self._thumb(it)
            img = Gtk.Image.new_from_pixbuf(pb) if pb else Gtk.Image.new_from_icon_name("image-missing", 6)
            img.set_halign(Gtk.Align.START)
            img.get_style_context().add_class("thumb")
            mid.pack_start(img, False, False, 0)
            meta = f"Bild {it.get('width', '?')}×{it.get('height', '?')}"
        else:
            text = it["text"]
            flat = " ".join(text.split())
            lbl = Gtk.Label(label=flat[:400], xalign=0, lines=2, wrap=True, ellipsize=Pango.EllipsizeMode.END,
                            wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=40)
            mid.pack_start(lbl, False, False, 0)
            nl = text.count("\n")
            meta = f"{len(text)} Zeichen" + (f" · {nl + 1} Zeilen" if nl else "")
            row.set_tooltip_text(text[:1500] + ("…" if len(text) > 1500 else ""))
        parts = [p for p in (it.get("app"), relative_time(it.get("time", 0)), meta) if p]
        m = Gtk.Label(label=" · ".join(parts), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        m.get_style_context().add_class("meta")
        mid.pack_start(m, False, False, 0)
        box.pack_start(mid, True, True, 0)
        pin = Gtk.Button(relief=Gtk.ReliefStyle.NONE, valign=Gtk.Align.CENTER,
                         tooltip_text="Lösen (Strg+P)" if it.get("pinned") else "Anheften (Strg+P)")
        pin.add(Gtk.Image.new_from_icon_name("starred-symbolic" if it.get("pinned") else "non-starred-symbolic",
                                             Gtk.IconSize.BUTTON))
        pin.get_style_context().add_class("rowbtn")
        if it.get("pinned"):
            pin.get_style_context().add_class("on")
        pin.set_can_focus(False)
        pin.connect("clicked", lambda *_: self.history.toggle_pin(it["id"]))
        dele = Gtk.Button(relief=Gtk.ReliefStyle.NONE, valign=Gtk.Align.CENTER, tooltip_text="Löschen (Entf)")
        dele.add(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON))
        dele.get_style_context().add_class("rowbtn")
        dele.set_can_focus(False)
        dele.connect("clicked", lambda *_: self.history.delete(it["id"]))
        box.pack_start(pin, False, False, 0)
        box.pack_start(dele, False, False, 0)
        row.add(box)
        return row

    def _visible(self) -> list[dict]:
        q = self.search.get_text().casefold().strip() if self.search else ""
        out = []
        for it in self.history.ordered():
            if self.only_pinned and not it.get("pinned"):
                continue
            if q:
                hay = (it.get("text") or ("bild image " + it.get("app", ""))).casefold()
                if not all(w in hay for w in q.split()):
                    continue
            out.append(it)
        return out

    def _fill(self, keep_id: str | None = None):
        if self.win is None:
            return
        for r in self.list.get_children():
            self.list.remove(r)
        items = self._visible()[:200]
        for i, it in enumerate(items, 1):
            self.list.add(self._row(it, i))
        self.list.show_all()
        if items:
            self.stack.set_visible_child_name("list")
            target = None
            if keep_id:
                target = next((r for r in self.list.get_children() if r.item["id"] == keep_id), None)
            self._select(target or self.list.get_row_at_index(0))
        else:
            if self.search.get_text():
                self.empty.set_text("Nichts gefunden")
            elif self.only_pinned:
                self.empty.set_text("Noch nichts angeheftet\nStrg+P heftet den markierten Eintrag an")
            elif not self.history.cfg.get("history", True):
                self.empty.set_text("Der Verlauf ist ausgeschaltet\n(Tastenwerk → Einstellungen → Zwischenablage)")
            else:
                self.empty.set_text("Noch leer\nAlles, was du kopierst, erscheint hier")
            self.stack.set_visible_child_name("empty")

    def _select(self, row):
        if row is None:
            return
        self.list.select_row(row)
        adj = self.scroller.get_vadjustment()
        alloc = row.get_allocation()
        if alloc.height > 1:
            if alloc.y < adj.get_value():
                adj.set_value(alloc.y)
            elif alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
                adj.set_value(alloc.y + alloc.height - adj.get_page_size())

    def _on_history_changed(self):
        if self.win is not None and self.win.get_visible():
            sel = self.list.get_selected_row()
            self._fill(keep_id=sel.item["id"] if sel else None)

    # ---------- Anzeigen ----------
    def show(self, prev_xid: int | None):
        if self.win is None:
            self._build()
        self.prev_xid = prev_xid
        self.search.set_text("")
        self.only_pinned = False
        self.pin_filter.set_active(False)
        self._pix_cache.clear()
        self._fill()
        self._place()
        self.win.show_all()
        self._shown_at = time.monotonic()
        self.search.grab_focus()
        self._focus_tries = 0
        self._focus()
        GLib.timeout_add(60, self._focus_retry)
        log.info("Zwischenablage-Verlauf geöffnet")

    def _focus_retry(self):
        """Bis zu ~1 s lang nachfassen, falls GNOME den Fokus nicht sofort hergibt."""
        if self.win is None or not self.win.get_visible() or self.win.is_active():
            return False
        self._focus_tries += 1
        if self._focus_tries > 12:
            log.warning("Zwischenablage-Verlauf: Fokus wurde verweigert")
            return False
        self._focus()
        return True

    def bring_to_front(self):
        """Schon offen, aber nicht vorne: wieder platzieren und Fokus holen."""
        self._place()
        self.win.present()
        self._shown_at = time.monotonic()
        self._focus_tries = 0
        self._focus()
        GLib.timeout_add(60, self._focus_retry)

    def is_front(self) -> bool:
        return self.visible() and self.win.is_active()

    def _activate_x(self, xid: int):
        """_NET_ACTIVE_WINDOW mit Quelle „Pager“ – das befolgt GNOME immer."""
        try:
            from Xlib import X, display as xdisplay
            from Xlib.protocol import event as xevent
            if getattr(self, "_xd", None) is None:
                self._xd = xdisplay.Display()
            d = self._xd
            root = d.screen().root
            ev = xevent.ClientMessage(window=d.create_resource_object("window", xid),
                                      client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
                                      data=(32, [2, X.CurrentTime, 0, 0, 0]))
            root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            d.flush()
        except Exception as e:
            log.debug("Aktivieren über _NET_ACTIVE_WINDOW fehlgeschlagen: %s", e)

    def _target_rect(self):
        """Rahmen des Fensters, aus dem der Aufruf kam (x, y, w, h) – oder None."""
        if not self.prev_xid:
            return None
        try:
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11
            gw = GdkX11.X11Window.foreign_new_for_display(Gdk.Display.get_default(), self.prev_xid)
            if gw is None:
                return None
            r = gw.get_frame_extents()
            if r.width < 50 or r.height < 50:
                return None
            return r.x, r.y, r.width, r.height
        except Exception:
            return None

    def _place(self):
        """Mittig über dem aktiven Fenster öffnen (sonst an der Maus), immer ganz sichtbar."""
        display = Gdk.Display.get_default()
        rect = self._target_rect()
        if rect:
            x, y, w, h = rect
            cx, cy = x + w // 2, y + h // 2
            px, py = cx - WIDTH // 2, cy - HEIGHT // 2
        else:
            _screen, cx, cy = display.get_default_seat().get_pointer().get_position()
            px, py = cx - WIDTH // 2, cy - 30
        wa = display.get_monitor_at_point(cx, cy).get_workarea()
        px = min(max(wa.x + 8, px), wa.x + wa.width - WIDTH - 8)
        py = min(max(wa.y + 8, py), wa.y + wa.height - HEIGHT - 8)
        self.win.move(px, py)

    def _focus(self):
        if self.win is None or not self.win.get_visible():
            return
        gw = self.win.get_window()
        if gw is None:
            return
        try:
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11
            ts = GdkX11.x11_get_server_time(gw)
        except (ValueError, ImportError, TypeError):
            ts = Gtk.get_current_event_time()
        self.win.present_with_time(ts)
        gw.focus(ts)
        try:
            self._activate_x(gw.get_xid())
        except AttributeError:
            pass

    def hide(self):
        if self.win is not None:
            self.win.hide()

    def visible(self) -> bool:
        return self.win is not None and self.win.get_visible()

    def _on_focus_out(self, *_):
        # Kurz nach dem Öffnen kann der Fokus noch wechseln – dann nicht gleich schließen
        if time.monotonic() - self._shown_at > 0.6:
            GLib.timeout_add(150, self._maybe_hide)
        return False

    def _maybe_hide(self):
        if self.win is not None and self.win.get_visible() and not self.win.is_active():
            log.info("Zwischenablage-Verlauf geschlossen (anderes Fenster angeklickt)")
            self.hide()
        return False

    # ---------- Bedienung ----------
    def _choose(self, row, mode):
        if row is None:
            return
        it = row.item
        self.hide()
        self.on_choose(it, mode, self.prev_xid)

    def _move(self, delta):
        rows = self.list.get_children()
        if not rows:
            return
        cur = self.list.get_selected_row()
        i = rows.index(cur) if cur in rows else -1
        i = max(0, min(len(rows) - 1, i + delta))
        self._select(rows[i])

    def _on_key(self, _w, ev):
        k = Gdk.keyval_name(ev.keyval) or ""
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(ev.state & Gdk.ModifierType.MOD1_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        sel = self.list.get_selected_row()
        if k == "Escape":
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.hide()
            return True
        if k in ("Down", "KP_Down"):
            self._move(1)
            return True
        if k in ("Up", "KP_Up"):
            self._move(-1)
            return True
        if k in ("Page_Down", "KP_Page_Down"):
            self._move(6)
            return True
        if k in ("Page_Up", "KP_Page_Up"):
            self._move(-6)
            return True
        if k in ("Return", "KP_Enter"):
            self._choose(sel, "copy" if shift else "paste")
            return True
        if (ctrl or alt) and k in "123456789" and len(k) == 1:
            row = self.list.get_row_at_index(int(k) - 1)
            if row is not None:
                self._choose(row, "paste")
            return True
        if k == "Tab" or k == "ISO_Left_Tab":
            self.pin_filter.set_active(not self.pin_filter.get_active())
            return True
        if ctrl and k.lower() == "p" and sel is not None:
            self.history.toggle_pin(sel.item["id"])
            return True
        if sel is not None and (k == "Delete" and (ctrl or shift or not self.search.get_text())):
            rows = self.list.get_children()
            idx = rows.index(sel)
            nxt = rows[idx + 1] if idx + 1 < len(rows) else rows[idx - 1] if idx > 0 else None
            keep = nxt.item["id"] if nxt is not None else None
            self.history.delete(sel.item["id"])
            if keep:
                self._fill(keep_id=keep)
            return True
        if ctrl and k.lower() == "c" and sel is not None and not self.search.get_text():
            self._choose(sel, "copy")
            return True
        # Alles andere geht ins Suchfeld
        if not self.search.has_focus():
            self.search.grab_focus_without_selecting()
        return False
