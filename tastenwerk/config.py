"""Konfiguration laden, Includes auflösen und Regeln prüfen."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .clipboard_defaults import DEFAULTS as CLIP_DEFAULTS
from .clipboard_defaults import MAX_N as CLIP_MAX_N, command as clip_command
from .gestures import Gesture, GestureError
from .keys import Combo, KeyError_, parse_sequence
from .i18n import tr, trn  # noqa: E402

CONFIG_DIR = Path(os.environ.get("TASTENWERK_CONFIG_DIR",
                                 Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tastenwerk"))
CONFIG_FILE = CONFIG_DIR / "config.yaml"
IMPORT_DIR = CONFIG_DIR / "imported"

DEFAULT_SETTINGS = {
    "notify": True,
    "tray": True,                   # Symbol in der oberen Leiste
    "language": "auto",             # auto (System) | de | en
    "abbreviation_case_sensitive": False,
    "type_delay_ms": 6,
    "key_delay_ms": 25,
    # Ab dieser Länge oder bei Zeilenumbrüchen wird Text eingefügt statt getippt
    "paste_threshold": 200,
    "restore_clipboard": True,
    "terminal_apps": ["gnome-terminal-server", "kgx", "org.gnome.Console", "tilix", "kitty",
                      "Alacritty", "xfce4-terminal", "konsole", "terminator", "org.wezfurlong.wezterm",
                      "com.mitchellh.ghostty", "ptyxis", "org.gnome.Ptyxis"],
    "ai": {"command": "claude", "model": "haiku", "timeout": 120},
    # Wie weit (in %) eine Touchpad-Geste gewischt sein muss, damit sie zählt
    "gesture_threshold": 20,
    "clipboard": dict(CLIP_DEFAULTS),
}

ACTION_TYPES = {
    "keys": tr("Tasten senden"),
    "type": tr("Text tippen"),
    "paste": tr("Text einfügen (über Zwischenablage)"),
    "run": tr("Befehl ausführen"),
    "open": tr("Datei/URL/Programm öffnen"),
    "window": tr("Fenster-Aktion"),
    "workspace": tr("Arbeitsfläche wechseln"),
    "delay": tr("Warten"),
    "lock": tr("Bildschirm sperren"),
    "notify": tr("Benachrichtigung zeigen"),
    "ai": tr("Markierten Text mit Claude umformen"),
    "clipboard": tr("Zwischenablage-Verlauf"),
    "screenshot": tr("Bildschirmfoto"),
    "app": tr("Programm öffnen oder wechseln"),
}

APP_IF_ACTIVE = {"next": tr("Nächstes Fenster des Programms"), "minimize": tr("Minimieren"), "none": tr("Nichts tun")}

SCREENSHOT_MODES = {"area": tr("Bereich auswählen"), "screen": tr("Ganzer Bildschirm"), "window": tr("Aktuelles Fenster")}

WINDOW_OPS = [
    "left_half", "right_half", "top_half", "bottom_half",
    "left_third", "center_third", "right_third", "left_two_thirds", "right_two_thirds",
    "top_left", "top_right", "bottom_left", "bottom_right",
    "maximize", "unmaximize", "toggle_maximize", "minimize", "minimize_under_cursor",
    "fullscreen", "center", "close", "next_monitor", "next_monitor_maximized", "prev_monitor",
]


class ConfigError(Exception):
    pass


@dataclass
class Rule:
    name: str
    actions: list[dict]
    hotkey: Combo | None = None
    abbreviation: str | None = None
    gesture: Gesture | None = None
    apps: list[str] = field(default_factory=list)
    not_apps: list[str] = field(default_factory=list)
    enabled: bool = True
    source: str | None = None       # z. B. "btt:<uuid>" bei importierten Regeln
    origin: str = ""                # Datei, aus der die Regel stammt

    @property
    def trigger_key(self) -> str:
        if self.hotkey is not None:
            return "hotkey:" + str(self.hotkey).lower()
        if self.gesture is not None:
            return "gesture:" + str(self.gesture)
        return "abbr:" + (self.abbreviation or "")

    @property
    def trigger_text(self) -> str:
        if self.hotkey is not None:
            return str(self.hotkey)
        if self.gesture is not None:
            return self.gesture.label
        return f"„{self.abbreviation}“"

    def matches_app(self, wm_classes: tuple[str, ...]) -> bool:
        classes = {c.lower() for c in wm_classes if c}
        if self.not_apps and classes & {a.lower() for a in self.not_apps}:
            return False
        if not self.apps:
            return True
        return bool(classes & {a.lower() for a in self.apps})


@dataclass
class Config:
    settings: dict
    rules: list[Rule]
    files: list[Path]
    warnings: list[str]

    def enabled_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.enabled]


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _merge_settings(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_settings(out[k], v)
        else:
            out[k] = v
    return out


def normalize_action(a, where: str) -> dict:
    """Eine Aktion prüfen und in die Form {typ: wert, ...} bringen."""
    if isinstance(a, str):
        # Kurzform: reine Tastenfolge
        a = {"keys": a}
    if not isinstance(a, dict) or not a:
        raise ConfigError(f"{where}: Aktion muss ein Eintrag wie 'keys: ctrl+v' sein, nicht {a!r}")
    kinds = [k for k in a if k in ACTION_TYPES]
    if len(kinds) != 1:
        raise ConfigError(f"{where}: Aktion braucht genau einen Typ aus {', '.join(ACTION_TYPES)} – gefunden: {list(a)}")
    kind = kinds[0]
    val = a[kind]
    if kind == "keys":
        try:
            parse_sequence(str(val))
        except KeyError_ as e:
            raise ConfigError(f"{where}: {e}") from None
    elif kind == "window" and val not in WINDOW_OPS:
        raise ConfigError(f"{where}: Unbekannte Fenster-Aktion {val!r}. Möglich: {', '.join(WINDOW_OPS)}")
    elif kind == "delay":
        try:
            float(val)
        except (TypeError, ValueError):
            raise ConfigError(f"{where}: delay braucht Sekunden, z. B. 0.2") from None
    elif kind == "workspace" and not (str(val) in ("next", "prev", "left", "right") or str(val).isdigit()):
        raise ConfigError(f"{where}: workspace kann next, prev oder eine Nummer sein")
    elif kind == "clipboard" and clip_command(val) is None:
        raise ConfigError(f"{where}: clipboard kann 'show' (Verlauf zeigen), 'clear' (leeren) "
                          f"oder 'paste 2' (Eintrag Nr. 2 einfügen, 1–{CLIP_MAX_N}) sein")
    elif kind == "app":
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"{where}: app braucht ein Programm, z. B. 'firefox' oder 'org.gnome.Nautilus'")
        if a.get("if_active", "next") not in APP_IF_ACTIVE:
            raise ConfigError(f"{where}: if_active kann {', '.join(APP_IF_ACTIVE)} sein")
    elif kind == "screenshot" and str(val) not in SCREENSHOT_MODES:
        raise ConfigError(f"{where}: screenshot kann {', '.join(SCREENSHOT_MODES)} sein")
    elif kind == "ai":
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"{where}: ai braucht eine Anweisung als Text")
        if a.get("input", "selection") not in ("selection", "line", "all"):
            raise ConfigError(f"{where}: ai-input kann 'selection', 'line' oder 'all' sein")
    return dict(a)


def parse_rule(raw: dict, origin: str, idx: int) -> Rule:
    name = str(raw.get("name") or f"Regel {idx + 1}")
    where = f"{origin}: „{name}“"
    hk = raw.get("hotkey")
    ab = raw.get("abbreviation", raw.get("abbr"))
    ge = raw.get("gesture")
    if sum(bool(x) for x in (hk, ab, ge)) != 1:
        raise ConfigError(f"{where}: Eine Regel braucht genau eins von 'hotkey', 'abbreviation' oder 'gesture'")
    gesture = None
    if ge:
        try:
            gesture = Gesture.parse(str(ge))
        except GestureError as e:
            raise ConfigError(f"{where}: {e}") from None
    combo = None
    if hk:
        try:
            combo = Combo.parse(str(hk))
        except KeyError_ as e:
            raise ConfigError(f"{where}: {e}") from None
    actions_raw = raw.get("do", raw.get("actions"))
    if actions_raw is None:
        raise ConfigError(f"{where}: 'do' fehlt (was soll passieren?)")
    actions = [normalize_action(a, where) for a in _as_list(actions_raw)]
    return Rule(
        name=name, actions=actions, hotkey=combo,
        abbreviation=str(ab) if ab else None, gesture=gesture,
        apps=[str(x) for x in _as_list(raw.get("apps", raw.get("app")))],
        not_apps=[str(x) for x in _as_list(raw.get("not_apps"))],
        enabled=bool(raw.get("enabled", True)),
        source=raw.get("source"), origin=origin,
    )


def _load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: YAML-Fehler: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: Oberste Ebene muss settings/rules/include enthalten")
    return data


def load(path: Path | None = None) -> Config:
    path = Path(path or CONFIG_FILE)
    if not path.exists():
        raise ConfigError(f"Keine Konfiguration gefunden: {path}\n→ Mit 'tastenwerk init' anlegen.")
    files = [path]
    main = _load_yaml(path)
    settings = _merge_settings(DEFAULT_SETTINGS, main.get("settings") or {})
    raw_rules: list[tuple[dict, str]] = [(r, path.name) for r in _as_list(main.get("rules"))]

    for pattern in _as_list(main.get("include")):
        full = pattern if os.path.isabs(pattern) else str(path.parent / pattern)
        for inc in sorted(glob.glob(os.path.expanduser(full))):
            p = Path(inc)
            if p.suffix.lower() not in (".yaml", ".yml"):
                continue
            files.append(p)
            data = _load_yaml(p)
            if data.get("settings"):
                settings = _merge_settings(settings, data["settings"])
            rel = os.path.relpath(p, path.parent)
            raw_rules += [(r, rel) for r in _as_list(data.get("rules"))]

    rules, warnings = [], []
    for i, (raw, origin) in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise ConfigError(f"{origin}: Regel Nr. {i + 1} ist kein Eintrag")
        rules.append(parse_rule(raw, origin, i))

    cs = settings.get("abbreviation_case_sensitive")
    if not cs:
        for r in rules:
            if r.abbreviation:
                r.abbreviation = r.abbreviation.lower()
    warnings += find_conflicts(rules)
    return Config(settings=settings, rules=rules, files=files, warnings=warnings)


def find_conflicts(rules: list[Rule]) -> list[str]:
    out = []
    seen: dict[str, Rule] = {}
    for r in rules:
        if not r.enabled:
            continue
        for app in (r.apps or ["*"]):
            key = r.trigger_key + "|" + app.lower()
            if key in seen:
                o = seen[key]
                scope = "global" if app == "*" else f"in {app}"
                out.append(f"{r.trigger_text} ist doppelt belegt ({scope}): „{o.name}“ ({o.origin}) und „{r.name}“ ({r.origin}). Es gilt die erste.")
            else:
                seen[key] = r
    abbrs = sorted({r.abbreviation for r in rules if r.enabled and r.abbreviation})
    for a in abbrs:
        for b in abbrs:
            if a != b and b.startswith(a):
                out.append(f"Tastenfolge „{a}“ ist der Anfang von „{b}“ – „{b}“ kann so nie auslösen.")
    return out
