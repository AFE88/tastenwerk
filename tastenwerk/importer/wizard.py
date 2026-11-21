"""Interaktiver Assistent: BTT-Presets prüfen, auswählen und in Tastenwerk zusammenführen.

Ablauf
  1. Presets lesen und jeden Auslöser einordnen (direkt / angepasst / Handarbeit / nicht übertragbar)
  2. Einmalige Grundfragen: Wohin mit der ⌘-Taste? Welche Linux-Programme ersetzen die Mac-Programme?
  3. Jede übertragbare Regel einzeln bestätigen – mit Hinweisen und Konflikt-Prüfung
     (GNOME-Kürzel, eigene Regeln, früher importierte Regeln)
  4. Ergebnis nach ~/.config/tastenwerk/imported/btt-<preset>.yaml schreiben, plus Bericht

Beim erneuten Import (z. B. mit neueren Presets) werden unveränderte Regeln still übersprungen,
früher bewusst weggelassene nicht noch einmal gefragt und geänderte zum Aktualisieren angeboten.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

from .. import config as cfgmod
from ..actions import describe_action
from ..keys import Combo, KeyError_
from . import btt

# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty()


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def bold(s): return _c("1", s)
def dim(s): return _c("2", s)
def yellow(s): return _c("33", s)
def green(s): return _c("32", s)
def red(s): return _c("31", s)
def cyan(s): return _c("36", s)


def hr():
    print(dim("─" * 72))


class Abort(Exception):
    pass


class Asker:
    def __init__(self, assume_defaults: bool):
        self.auto = assume_defaults or not sys.stdin.isatty()

    def ask(self, prompt: str, choices: dict[str, str], default: str) -> str:
        legend = "  ".join(f"[{k.upper() if k == default else k}] {v}" for k, v in choices.items())
        if self.auto:
            return default
        while True:
            print(legend)
            try:
                ans = input(f"{prompt} › ").strip().lower()
            except EOFError:
                raise Abort() from None
            if not ans:
                return default
            if ans in choices:
                return ans
            print(red("Bitte eine der Optionen wählen."))

    def text(self, prompt: str, default: str = "") -> str:
        if self.auto:
            return default
        try:
            ans = input(f"{prompt}{f' [{default}]' if default else ''} › ").strip()
        except EOFError:
            raise Abort() from None
        return ans or default


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def installed_apps() -> dict[str, str]:
    """WM_CLASS-artige Namen installierter/geöffneter Programme -> Anzeigename."""
    out: dict[str, str] = {}
    dirs = ["/usr/share/applications", os.path.expanduser("~/.local/share/applications"),
            "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
            "/var/lib/snapd/desktop/applications"]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".desktop"):
                continue
            name, wmc, nodisplay = f[:-8], None, False
            try:
                for line in open(os.path.join(d, f), encoding="utf-8", errors="replace"):
                    if line.startswith("Name=") and name == f[:-8]:
                        name = line[5:].strip()
                    elif line.startswith("StartupWMClass="):
                        wmc = line.split("=", 1)[1].strip()
                    elif line.strip() == "NoDisplay=true":
                        nodisplay = True
                    elif line.startswith("[") and "Desktop Entry" not in line:
                        break
            except OSError:
                continue
            if nodisplay:
                continue
            for key in filter(None, (wmc, f[:-8])):
                out.setdefault(key.lower(), name)
    try:
        from ..x11 import X11
        x = X11()
        for w in x.client_list():
            for cls in x.wm_class(w):
                out.setdefault(cls.lower(), cls)
    except Exception:
        pass
    return out


def open_window_classes() -> list[str]:
    try:
        from ..x11 import X11
        x = X11()
        seen = []
        for w in x.client_list():
            cls = x.wm_class(w)
            if cls and cls[-1] not in seen:
                seen.append(cls[-1])
        return seen
    except Exception:
        return []


GNOME_EQUIV = {
    # Fenster-Aktion -> GNOME-Schlüssel, die dasselbe tun
    "left_half": ("toggle-tiled-left", "tile-left"),
    "right_half": ("toggle-tiled-right", "tile-right"),
    "maximize": ("maximize", "toggle-maximized"),
    "fullscreen": ("toggle-fullscreen", "toggle-maximized"),
    "minimize": ("minimize",),
    "minimize_under_cursor": ("minimize",),
}


def same_as_gnome(actions: list[dict], gnome_desc: str) -> bool:
    if len(actions) != 1:
        return False
    a = actions[0]
    if "window" in a:
        return any(gnome_desc.endswith(k) for k in GNOME_EQUIV.get(a["window"], ()))
    if "lock" in a:
        return gnome_desc.endswith("screensaver")
    if a.get("keys") == "Print":
        return "screenshot" in gnome_desc
    return False


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9äöüÄÖÜß_-]+", "-", s.strip().lower()).strip("-")
    return s or "preset"


class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_rep)


def dump_yaml(data) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=110)


def trigger_key(r: dict) -> str:
    """Auslöser einer Regel-Rohform als Schlüssel wie in config.Rule.trigger_key."""
    if r.get("hotkey"):
        return "hotkey:" + str(Combo.parse(r["hotkey"])).lower()
    if r.get("gesture"):
        from ..gestures import Gesture
        return "gesture:" + str(Gesture.parse(r["gesture"]))
    return "abbr:" + str(r.get("abbreviation", "")).lower()


def trigger_text(r: dict) -> str:
    if r.get("hotkey"):
        return str(r["hotkey"])
    if r.get("gesture"):
        from ..gestures import Gesture, GestureError
        try:
            return Gesture.parse(r["gesture"]).label
        except GestureError:
            return str(r["gesture"])
    return f"„{r.get('abbreviation')}“"


def rule_signature(r: dict) -> tuple:
    if r.get("hotkey"):
        trig = ("h", str(Combo.parse(r["hotkey"])).lower())
    elif r.get("gesture"):
        trig = ("g", str(r["gesture"]))
    else:
        trig = ("a", str(r.get("abbreviation", "")).lower())
    return trig, tuple(sorted(a.lower() for a in (r.get("apps") or []))), yaml.safe_dump(r.get("do"), allow_unicode=True)


def source_hash(rule: dict) -> str:
    """Fingerabdruck der Regel, wie sie aus BTT übersetzt wurde (ohne spätere Änderungen)."""
    import hashlib
    try:
        sig = rule_signature(rule)
    except KeyError_:
        sig = repr(rule)
    return hashlib.sha1(repr(sig).encode()).hexdigest()[:12]


def parse_trigger_input(text: str) -> tuple[str, str]:
    """Benutzereingabe -> ("hotkey", "super+x") oder ("abbreviation", "##x")."""
    t = text.strip()
    if "+" in t[1:] or (len(t) > 1 and t[0].isalpha() and " " not in t):
        try:
            return "hotkey", str(Combo.parse(t))
        except KeyError_:
            if "+" in t[1:]:
                raise
    return "abbreviation", t.lower()


# ---------------------------------------------------------------------------
# Assistent
# ---------------------------------------------------------------------------
class Wizard:
    def __init__(self, paths: list[Path], config_path: Path, analyse_only=False, assume_defaults=False):
        self.paths = paths
        self.config_path = Path(config_path)
        self.import_dir = self.config_path.parent / "imported"
        self.analyse_only = analyse_only
        self.q = Asker(assume_defaults)
        self.keys = btt.KeyResolver()
        self.opts = btt.MapOptions()
        self.app_map: dict[str, list[str] | None] = {}

    # ---------- 1. Einlesen ----------
    def load(self):
        self.presets = []
        cands: list[btt.Candidate] = []
        for p in self.paths:
            data = btt.load_preset(p)
            name = data.get("BTTPresetName") or p.stem
            self.presets.append((p, name))
            for c in btt.candidates(data, self.keys):
                c.notes = list(c.notes)
                c.preset = name
                c.file = p
                cands.append(c)
        # Doppelte (gleiche Quelle oder gleicher Inhalt in mehreren Dateien) zusammenfassen.
        # Spätere Dateien gelten als neuer.
        by_key: dict = {}
        for c in cands:
            key_src = c.source
            key_content = (c.bundle, c.mac_trigger, repr(c.actions), c.kind)
            prev = by_key.get(key_src) or by_key.get(key_content)
            if prev is not None:
                cands_idx = cands.index(prev)
                cands[cands_idx] = None  # type: ignore
            by_key[key_src] = c
            by_key[key_content] = c
        self.cands = [c for c in cands if c is not None]
        self.preset_name = self.presets[-1][1]

    def overview(self):
        print()
        print(bold("BetterTouchTool-Import"))
        hr()
        for p, name in self.presets:
            print(f"  {p.name}  {dim('(Preset „' + name + '“)')}")
        counts = {s: 0 for s in btt.STATUS_ORDER}
        for c in self.cands:
            counts[c.status] += 1
        print()
        print(f"  {len(self.cands)} Auslöser gefunden:")
        for s, n in counts.items():
            if n:
                print(f"    {btt.STATUS_LABEL[s]:24} {n:3}")
        apps: dict[str, int] = {}
        for c in self.cands:
            if c.status != "unsupported":
                apps[c.app_name] = apps.get(c.app_name, 0) + 1
        print()
        print("  Übertragbar nach Programm: " + ", ".join(f"{a} ({n})" for a, n in sorted(apps.items(), key=lambda t: -t[1])))
        uns = [c for c in self.cands if c.status == "unsupported"]
        if uns:
            reasons: dict[str, int] = {}
            for c in uns:
                reasons[c.notes[0] if c.notes else "?"] = reasons.get(c.notes[0] if c.notes else "?", 0) + 1
            print()
            print("  Nicht übertragbar (wird nur im Bericht aufgeführt):")
            for r, n in reasons.items():
                print(f"    {n:3} × {r}")
        print()

    # ---------- 2. Grundfragen ----------
    def ask_cmd(self):
        uses_cmd = any(c.hotkey_mac and "cmd" in c.hotkey_mac[0] for c in self.cands if c.status != "unsupported")
        if not uses_cmd:
            return
        hr()
        print(bold("Frage 1: Wohin wandert die ⌘-Taste?"))
        print("Auf dem Mac hast du Kürzel mit ⌘ ausgelöst. Unter Linux gibt es ⌘ nicht.")
        print("  super – Windows-Taste. Kollidiert am wenigsten mit Programm-Kürzeln (empfohlen)")
        print("  ctrl  – Strg. Fühlt sich wie Linux an, überschreibt aber oft Programm-Kürzel")
        print("  alt   – gleiche Position wie ⌘ auf der Mac-Tastatur")
        a = self.q.ask("⌘ wird zu", {"s": "super", "c": "ctrl", "a": "alt"}, "s")
        self.opts.trigger_cmd = {"s": "super", "c": "ctrl", "a": "alt"}[a]
        print(green(f"→ ⌘ wird zu {self.opts.trigger_cmd}"))
        print(dim("  (In gesendeten Kürzeln wird ⌘ immer zu Strg – z. B. ⌘V → Strg+V.)"))
        print()

    def ask_apps(self):
        apps: dict[str, tuple[str, int]] = {}
        for c in self.cands:
            if c.status != "unsupported" and not c.is_global:
                name, n = apps.get(c.bundle, (c.app_name, 0))
                apps[c.bundle] = (name, n + 1)
        if not apps:
            return
        hr()
        print(bold("Frage 2: Welche Linux-Programme ersetzen deine Mac-Programme?"))
        print("Regeln, die nur in einem bestimmten Programm galten, brauchen das passende Linux-Programm.")
        print(dim("Eingabe: Enter = Vorschlag übernehmen · eigenen Namen tippen · '-' = Regeln dieses Programms weglassen · '?' = offene Fenster zeigen"))
        print()
        inst = installed_apps()
        for bundle, (name, n) in sorted(apps.items(), key=lambda t: t[1][0].lower()):
            key = bundle.lower()
            if key in btt.APP_NOTES:
                print(f"  {bold(name)} ({n} Regeln): {btt.APP_NOTES[key]} → weggelassen")
                self.app_map[bundle] = None
                continue
            sugg = [s for s in btt.APP_SUGGESTIONS.get(key, []) if s.lower() in inst]
            default = sugg[0] if sugg else "-"
            hint = f"Vorschlag: {default} ({inst.get(default.lower(), '')})" if sugg else "kein passendes Programm gefunden"
            while True:
                ans = self.q.text(f"  {bold(name)} ({n} Regeln) – {hint}", default)
                if ans == "?":
                    print("    Offene Fenster: " + ", ".join(open_window_classes()))
                    continue
                break
            if self.q.auto:
                print(f"  {bold(name)} ({n} Regeln) – {hint}")
            if ans == "-":
                self.app_map[bundle] = None
                print(dim("    → Regeln dieses Programms werden weggelassen"))
            else:
                names = [a.strip() for a in ans.split(",") if a.strip()]
                self.app_map[bundle] = names
                unknown = [a for a in names if a.lower() not in inst]
                if unknown:
                    print(yellow(f"    ⚠ {', '.join(unknown)} ist nicht installiert/geöffnet – Regel greift erst, wenn es das Programm gibt"))
        print()

    # ---------- 3. Bestehendes laden ----------
    def load_existing(self):
        self.own_rules: list[cfgmod.Rule] = []
        if self.config_path.exists():
            try:
                cfg = cfgmod.load(self.config_path)
                self.own_rules = [r for r in cfg.rules if not str(r.origin).startswith("imported")]
            except cfgmod.ConfigError as e:
                print(yellow(f"⚠ Deine Konfiguration hat einen Fehler, Konflikte mit eigenen Regeln werden nicht geprüft:\n  {e}"))
        self.imported: dict[Path, dict] = {}
        self.by_source: dict[str, tuple[Path, int]] = {}
        self.skipped_before: dict[str, str] = {}
        if self.import_dir.is_dir():
            for f in sorted(self.import_dir.glob("*.yaml")):
                try:
                    data = yaml.safe_load(open(f, encoding="utf-8")) or {}
                except yaml.YAMLError:
                    continue
                self.imported[f] = data
                for i, r in enumerate(data.get("rules") or []):
                    if r.get("source"):
                        self.by_source[r["source"]] = (f, i)
                for s in data.get("skipped") or []:
                    self.skipped_before[s.get("source")] = s.get("name", "")
        from ..gnome import system_shortcuts
        from ..gestures import system_gestures
        self.system = system_shortcuts()
        self.system_gestures = system_gestures()

    # ---------- 4. Regeln prüfen ----------
    def build_rule(self, c: btt.Candidate) -> tuple[dict | None, list[str]]:
        notes = list(c.notes)
        label = c.name or ("; ".join(describe_action(a) for a in c.actions[:2]) if c.actions else c.action_text)
        rule: dict = {"name": f"{c.app_name}: {label}" if not c.is_global else label}
        if c.kind == "hotkey":
            combo, n = btt.trigger_combo(c, self.opts, self.keys)
            notes += n
            if not combo:
                return None, notes
            rule["hotkey"] = combo
        elif c.kind == "gesture":
            rule["gesture"] = c.gesture
        else:
            rule["abbreviation"] = c.abbreviation
        if not c.is_global:
            apps = self.app_map.get(c.bundle)
            if not apps:
                return None, notes
            rule["apps"] = apps
        rule["do"] = c.actions or [{"notify": f"Noch nicht umgesetzt: {c.action_text}"}]
        rule["source"] = c.source
        rule["source_hash"] = source_hash(rule)
        return rule, notes

    def _compare_previous(self, c, rule):
        """-> (Datei, Index, alte Regel, unverändert?) oder None"""
        prev = self.by_source.get(c.source)
        if not prev:
            return None
        f, idx = prev
        old = self.imported[f]["rules"][idx]
        if old.get("source_hash"):
            same = old["source_hash"] == rule["source_hash"]
        else:
            try:
                same = rule_signature(old) == rule_signature(rule)
            except KeyError_:
                same = False
        return f, idx, old, same

    def default_choice(self, c, notes: list[str], confl: list[str], hard: str | None) -> str:
        """j = übernehmen, d = deaktiviert, n = weglassen, k = anderer Auslöser nötig"""
        default = "j"
        if c.status == "manual":
            default = "d"
        if hard or not c.btt_enabled:
            default = "n"
        if any("ohne Zusatztaste" in n for n in confl) and default == "j":
            default = "d"
        if any("von GNOME belegt" in n for n in confl):
            default = "k"
        return default

    def plan(self) -> list[dict]:
        """Alle Kandidaten mit Vorschlag – ohne Rückfragen (für die Oberfläche).

        Jeder Eintrag: candidate, rule, notes, kind (new|changed|unchanged|skipped_before|
        not_mapped|unsupported), default (j|d|n), prev (Datei, Index) bei changed."""
        out, accepted = [], []
        todo = sorted(self.cands, key=lambda c: (c.status == "unsupported", not c.is_global,
                                                  c.app_name.lower(), btt.STATUS_ORDER[c.status]))
        for c in todo:
            item = {"candidate": c, "rule": None, "notes": list(c.notes), "default": "n", "prev": None}
            if c.status == "unsupported":
                item["kind"] = "unsupported"
                out.append(item)
                continue
            if c.source in self.skipped_before:
                item["kind"] = "skipped_before"
                out.append(item)
                continue
            rule, notes = self.build_rule(c)
            item["notes"] = notes
            if rule is None:
                item["kind"] = "not_mapped"
                out.append(item)
                continue
            item["rule"] = rule
            cmp = self._compare_previous(c, rule)
            if cmp:
                f, idx, old, same = cmp
                item["kind"] = "unchanged" if same else "changed"
                item["prev"] = (f, idx)
                item["old"] = old
                item["default"] = "n" if same else "j"
                out.append(item)
                continue
            confl, hard = self.conflicts(rule, accepted)
            item["notes"] = notes + confl + ([hard] if hard else [])
            d = self.default_choice(c, item["notes"], confl, hard)
            item["default"] = "d" if d == "k" else d
            item["kind"] = "new"
            if item["default"] in ("j", "d"):
                accepted.append(rule)
            out.append(item)
        return out

    def conflicts(self, rule: dict, pending: list[dict]) -> tuple[list[str], str | None]:
        """Gibt (Konflikt-Hinweise, harter Konflikt-Grund) zurück."""
        out, hard = [], None
        apps = {a.lower() for a in rule.get("apps") or []}
        if rule.get("hotkey"):
            combo = Combo.parse(rule["hotkey"])
            g = self.system.get(combo)
            if g:
                if same_as_gnome(rule["do"], g):
                    hard = f"GNOME macht mit {combo} bereits dasselbe ({g})"
                else:
                    out.append(f"{combo} ist von GNOME belegt ({g}) – Tastenwerk kann es dann nicht nutzen")
            if not combo.mods and not rule.get("apps") and not re.fullmatch(r"F(1[3-9]|2[0-9])|XF86.*|Print|Pause", combo.key):
                out.append(f"{combo} ohne Zusatztaste – überschreibt die Taste in allen Programmen (z. B. F3 = Suchen)")
            if str(combo).lower() in ("ctrl+shift+v", "ctrl+shift+c") and not rule.get("apps"):
                out.append(f"{combo} ist im Terminal Einfügen/Kopieren – global belegt fehlt es dort")
            tkey = "hotkey:" + str(combo).lower()
        else:
            tkey = trigger_key(rule)
            if rule.get("gesture"):
                from ..gestures import Gesture
                used = getattr(self, "system_gestures", {}).get(Gesture.parse(rule["gesture"]))
                if used:
                    out.append(f"Diese Geste nutzt auch das System ({used}) – beides würde ausgelöst")
        for r in self.own_rules:
            if r.trigger_key == tkey and r.enabled:
                rapps = {a.lower() for a in r.apps}
                if rapps == apps or (rapps & apps):
                    hard = hard or f"Deine eigene Regel „{r.name}“ nutzt {r.trigger_text} schon"
                else:
                    out.append(f"Eigene Regel „{r.name}“ nutzt denselben Auslöser ({'global' if not rapps else ', '.join(r.apps)})")
        for p in pending:
            same = trigger_key(p) == tkey
            if same and p.get("enabled", True):
                papps = {a.lower() for a in p.get("apps") or []}
                if papps == apps or (papps & apps):
                    hard = hard or f"In diesem Import schon vergeben an „{p['name']}“"
        return out, hard

    def show(self, i: int, n: int, c: btt.Candidate, rule: dict, notes: list[str]):
        hr()
        trig = trigger_text(rule)
        where = "überall" if not rule.get("apps") else ", ".join(rule["apps"])
        print(f"{dim(f'[{i}/{n}]')} {bold(rule['name'])}   {btt.STATUS_LABEL[c.status]}")
        print(f"  Mac:    {c.mac_trigger}  {dim('in ' + c.app_name)}")
        print(f"  Linux:  {cyan(trig)}  {dim('in ' + where)}")
        for a in rule["do"]:
            print(f"  tut:    {describe_action(a)}")
        for nt in notes:
            print(yellow(f"  ⚠ {nt}"))

    def review(self):
        todo = [c for c in self.cands if c.status != "unsupported"]
        todo.sort(key=lambda c: (not c.is_global, c.app_name.lower(), btt.STATUS_ORDER[c.status]))
        self.accepted: list[dict] = []
        self.updated: list[tuple[Path, int, dict]] = []
        self.skipped: list[tuple[btt.Candidate, str]] = []
        self.unchanged = 0
        self.silent_skips = 0
        accept_rest = False
        print(bold("Frage 3: Was willst du übernehmen?"))
        print(dim("Optionen: j = übernehmen · n = weglassen · d = deaktiviert übernehmen (später fertig machen)"))
        print(dim("          k = anderen Auslöser wählen · a = alle restlichen mit Vorschlag · q = abbrechen, nichts speichern"))
        print()
        for i, c in enumerate(todo, 1):
            if c.source in self.skipped_before:
                self.silent_skips += 1
                continue
            rule, notes = self.build_rule(c)
            if rule is None:
                self.skipped.append((c, "Programm weggelassen" if not c.is_global else "; ".join(notes) or "nicht übersetzbar"))
                continue
            # Schon früher importiert?
            cmp = self._compare_previous(c, rule)
            if cmp:
                f, idx, old, same = cmp
                if same:
                    self.unchanged += 1
                    continue
                self.show(i, len(todo), c, rule, notes + ["Wurde früher schon importiert und hat sich geändert"])
                print(dim(f"  bisher: {trigger_text(old)} → " + "; ".join(describe_action(a) for a in old.get('do') or [])))
                ans = "e" if accept_rest else self.q.ask("Aktualisieren?", {"e": "ersetzen", "b": "bisherige behalten", "q": "abbrechen"}, "e")
                if ans == "q":
                    raise Abort()
                if ans == "e":
                    rule["enabled"] = old.get("enabled", True)
                    self.updated.append((f, idx, rule))
                continue


            confl, hard = self.conflicts(rule, self.accepted)
            notes += confl
            if hard:
                notes.append(hard)
            default = self.default_choice(c, notes, confl, hard)
            self.show(i, len(todo), c, rule, notes)
            if accept_rest:
                ans = default if default != "k" else "d"
            else:
                ans = self.q.ask("Übernehmen?", {"j": "ja", "n": "nein", "d": "deaktiviert", "k": "anderer Auslöser",
                                                  "a": "alle restlichen", "q": "abbrechen"}, default)
            if ans == "q":
                raise Abort()
            if ans == "a":
                accept_rest = True
                ans = default if default != "k" else "d"
            if ans == "k":
                while True:
                    t = self.q.text("  Neuer Auslöser (z. B. super+alt+v oder ##mail)")
                    if not t:
                        ans = "d"
                        break
                    try:
                        kind, val = parse_trigger_input(t)
                    except KeyError_ as e:
                        print(red(f"  {e}"))
                        continue
                    rule.pop("hotkey", None)
                    rule.pop("abbreviation", None)
                    rule.pop("gesture", None)
                    rule[kind] = val
                    more, hard2 = self.conflicts(rule, self.accepted)
                    for m in more + ([hard2] if hard2 else []):
                        print(yellow(f"  ⚠ {m}"))
                    if hard2 or any("von GNOME belegt" in m for m in more):
                        continue
                    ans = "j"
                    break
            if ans == "n":
                self.skipped.append((c, "von dir weggelassen"))
                continue
            if ans == "d":
                rule["enabled"] = False
                notes = notes or ["Vor dem Einschalten prüfen"]
            if notes:
                rule["notes"] = notes
            self.accepted.append(rule)
            print(green("  ✓ übernommen" + (" (deaktiviert)" if rule.get("enabled") is False else "")))

    # ---------- 5. Schreiben ----------
    def summary_and_write(self):
        hr()
        on = [r for r in self.accepted if r.get("enabled", True)]
        off = [r for r in self.accepted if r.get("enabled") is False]
        print(bold("Zusammenfassung"))
        print(f"  neu übernommen: {len(on)}   deaktiviert (Handarbeit): {len(off)}   aktualisiert: {len(self.updated)}")
        print(f"  unverändert seit letztem Import: {self.unchanged}   früher weggelassen: {self.silent_skips}   jetzt weggelassen: {len(self.skipped)}")
        new_skips = [(c, why) for c, why in self.skipped if why == "von dir weggelassen"]
        if not (self.accepted or self.updated or new_skips):
            print(green("Nichts Neues – deine Konfiguration ist auf dem Stand dieser Presets."))
            return
        if self.analyse_only:
            print(dim("(Nur Analyse – es wurde nichts gespeichert.)"))
            return
        ans = self.q.ask("Speichern?", {"j": "ja", "n": "nein"}, "j")
        if ans != "j":
            print("Nichts gespeichert.")
            return
        files, report = self.write_results()
        for f in files:
            print(green(f"✓ gespeichert: {f}"))
        print(green(f"✓ Bericht: {report}"))
        try:
            cfgmod.load(self.config_path)
            print(green("✓ Konfiguration ist gültig. Ein laufendes Tastenwerk lädt sie automatisch neu."))
        except cfgmod.ConfigError as e:
            print(red(f"⚠ Konfiguration hat jetzt einen Fehler: {e}"))

    def write_results(self) -> tuple[list[Path], Path]:
        """self.accepted / self.updated / self.skipped speichern. -> (Dateien, Bericht)"""
        ensure_config(self.config_path)
        self.import_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        target = self.import_dir / f"btt-{slug(self.preset_name)}.yaml"

        # Aktualisierungen in bestehende Dateien schreiben
        touched = {}
        for f, idx, rule in self.updated:
            self.imported[f]["rules"][idx] = rule
            touched[f] = self.imported[f]
        data = self.imported.get(target) or {"rules": [], "skipped": []}
        data.setdefault("rules", [])
        data.setdefault("skipped", [])
        data["rules"] += self.accepted
        data["imported_from"] = sorted(set(data.get("imported_from") or []) | {p.name for p, _ in self.presets})
        known = {s.get("source") for s in data["skipped"]}
        for c, why in self.skipped:
            if why == "von dir weggelassen" and c.source not in known:
                data["skipped"].append({"source": c.source, "name": c.name, "reason": why})
        touched[target] = data

        for f, d in touched.items():
            if f.exists():
                shutil.copy2(f, f.with_suffix(f".yaml.bak"))
            header = (f"# Von Tastenwerk aus BetterTouchTool importiert – zuletzt {stamp}\n"
                      f"# Quellen: {', '.join(d.get('imported_from') or [p.name for p, _ in self.presets])}\n"
                      "# Du kannst hier frei ändern. Beim nächsten Import werden Änderungen erkannt.\n"
                      "# 'skipped' merkt sich, was du bewusst weggelassen hast (wird nicht erneut gefragt).\n\n")
            f.write_text(header + dump_yaml(d), encoding="utf-8")
        report = self.write_report(target.with_name(f"{target.stem}-bericht-{dt.datetime.now():%Y%m%d-%H%M%S}.md"), stamp)
        return list(touched), report

    def write_report(self, path: Path, stamp: str) -> Path:
        lines = [f"# BTT-Import-Bericht ({stamp})", "",
                 "Quellen: " + ", ".join(f"`{p.name}`" for p, _ in self.presets), "",
                 f"⌘ in Auslösern → **{self.opts.trigger_cmd}**", ""]

        def rl(r):
            t = trigger_text(r)
            apps = f" (nur {', '.join(r['apps'])})" if r.get("apps") else ""
            notes = "".join(f"\n  - ⚠ {n}" for n in r.get("notes") or [])
            return f"- **{t}**{apps} – {r['name']}: " + "; ".join(describe_action(a) for a in r["do"]) + notes

        on = [r for r in self.accepted if r.get("enabled", True)]
        off = [r for r in self.accepted if r.get("enabled") is False]
        if on:
            lines += ["## Übernommen", ""] + [rl(r) for r in on] + [""]
        if off:
            lines += ["## Deaktiviert übernommen – brauchen noch Handarbeit", "",
                      "In der Import-Datei `enabled: true` setzen, wenn fertig.", ""] + [rl(r) for r in off] + [""]
        if self.updated:
            lines += ["## Aktualisiert", ""] + [rl(r) for _, _, r in self.updated] + [""]
        if self.skipped:
            lines += ["## Weggelassen", ""] + [f"- {c.mac_trigger} in {c.app_name}: {c.action_text} – {why}" for c, why in self.skipped] + [""]
        uns = [c for c in self.cands if c.status == "unsupported"]
        if uns:
            lines += ["## Nicht übertragbar", ""] + [f"- {c.mac_trigger} in {c.app_name}: {c.action_text} – {'; '.join(c.notes)}" for c in uns] + [""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def run(self):
        try:
            self.load()
            self.overview()
            self.load_existing()
            self.ask_cmd()
            self.ask_apps()
            self.review()
            self.summary_and_write()
        except (Abort, KeyboardInterrupt):
            print()
            print(yellow("Abgebrochen – es wurde nichts gespeichert."))
            return 1
        return 0


def ensure_config(path: Path):
    """Legt die Konfiguration aus der Vorlage an, falls es noch keine gibt."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    example = Path(__file__).resolve().parent.parent.parent / "config.example.yaml"
    shutil.copy(example, path)
    return True
