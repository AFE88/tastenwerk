"""Regeln lesen und schreiben – für die Oberfläche.

Die Hauptkonfiguration und die importierten Dateien werden als rohe YAML-Daten gehalten,
damit jede Regel in ihre Ursprungsdatei zurückgeschrieben wird.
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config as cfgmod
from .keys import Combo, KeyError_
from .i18n import tr, trn  # noqa: E402

MAIN_HEADER = (
    "# Tastenwerk – Konfiguration\n"
    "# Wird von der Oberfläche verwaltet, lässt sich aber auch von Hand bearbeiten.\n"
    "# Alle Optionen sind in config.example.yaml im Programmordner erklärt.\n\n"
)
KEY_ORDER = ["name", "hotkey", "abbreviation", "gesture", "apps", "not_apps", "enabled", "do", "notes", "source"]


class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_rep)


def dump_yaml(data) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=110)


def ordered_rule(r: dict) -> dict:
    out = {k: r[k] for k in KEY_ORDER if k in r and r[k] not in (None, [], "")}
    if out.get("enabled") is True:
        out.pop("enabled")
    for k, v in r.items():
        if k not in out and k not in KEY_ORDER:
            out[k] = v
    return out


@dataclass
class RuleItem:
    data: dict
    file: Path

    @property
    def name(self) -> str:
        return str(self.data.get("name") or tr("Ohne Namen"))

    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled", True))

    @property
    def hotkey(self) -> str | None:
        return self.data.get("hotkey")

    @property
    def abbreviation(self) -> str | None:
        return self.data.get("abbreviation") or self.data.get("abbr")

    @property
    def gesture(self) -> str | None:
        return self.data.get("gesture")

    @property
    def gesture_obj(self):
        from .gestures import Gesture, GestureError
        try:
            return Gesture.parse(str(self.gesture)) if self.gesture else None
        except GestureError:
            return None

    @property
    def apps(self) -> list[str]:
        a = self.data.get("apps") or self.data.get("app") or []
        return [a] if isinstance(a, str) else list(a)

    @property
    def not_apps(self) -> list[str]:
        a = self.data.get("not_apps") or []
        return [a] if isinstance(a, str) else list(a)

    @property
    def actions(self) -> list[dict]:
        a = self.data.get("do", self.data.get("actions")) or []
        a = a if isinstance(a, list) else [a]
        return [x if isinstance(x, dict) else {"keys": str(x)} for x in a]

    @property
    def notes(self) -> list[str]:
        return list(self.data.get("notes") or [])

    @property
    def imported(self) -> bool:
        return bool(self.data.get("source")) or self.file.parent.name == "imported"

    def trigger_key(self) -> str | None:
        try:
            if self.hotkey:
                return "hotkey:" + str(Combo.parse(str(self.hotkey))).lower()
        except KeyError_:
            return None
        if self.gesture:
            g = self.gesture_obj
            return "gesture:" + str(g) if g else None
        if self.abbreviation:
            return "abbr:" + str(self.abbreviation).lower()
        return None


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or cfgmod.CONFIG_FILE)
        self.load()

    # ---------- Laden ----------
    def load(self):
        if not self.path.exists():
            from .importer.wizard import ensure_config
            ensure_config(self.path)
        self.files: dict[Path, dict] = {}
        self.headers: dict[Path, str] = {}
        self.main = self._read(self.path)
        self.files[self.path] = self.main
        self.items: list[RuleItem] = [RuleItem(r, self.path) for r in self.main.get("rules") or [] if isinstance(r, dict)]
        for pattern in self.main.get("include") or []:
            full = pattern if os.path.isabs(pattern) else str(self.path.parent / pattern)
            for inc in sorted(glob.glob(os.path.expanduser(full))):
                p = Path(inc)
                if p.suffix.lower() not in (".yaml", ".yml"):
                    continue
                data = self._read(p)
                self.files[p] = data
                self.items += [RuleItem(r, p) for r in data.get("rules") or [] if isinstance(r, dict)]
        self.mtimes = self._mtimes()

    def _read(self, p: Path) -> dict:
        text = p.read_text(encoding="utf-8")
        header = []
        for line in text.splitlines(keepends=True):
            if line.startswith("#") or not line.strip():
                header.append(line)
            else:
                break
        self.headers[p] = "".join(header)
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise cfgmod.ConfigError(f"{p}: Oberste Ebene muss settings/rules/include enthalten")
        return data

    def _mtimes(self):
        out = {}
        for p in self.files:
            try:
                out[p] = p.stat().st_mtime
            except FileNotFoundError:
                pass
        return out

    def changed_on_disk(self) -> bool:
        imp = self.path.parent / "imported"
        current = set(self.files)
        if imp.is_dir():
            if {p for p in imp.glob("*.yaml")} - current:
                return True
        return self._mtimes() != self.mtimes

    # ---------- Einstellungen ----------
    @property
    def settings(self) -> dict:
        return cfgmod._merge_settings(cfgmod.DEFAULT_SETTINGS, self.main.get("settings") or {})

    def set_setting(self, key: str, value, sub: str | None = None):
        s = self.main.setdefault("settings", {}) or {}
        self.main["settings"] = s
        if sub:
            s.setdefault(sub, {})
            s[sub][key] = value
        else:
            s[key] = value
        self.save(self.path)

    # ---------- Regeln ----------
    def validate(self, rule: dict) -> None:
        cfgmod.parse_rule(rule, tr("Regel"), 0)

    def add(self, rule: dict) -> RuleItem:
        self.validate(rule)
        rule = ordered_rule(rule)
        self.main.setdefault("rules", [])
        if self.main["rules"] is None:
            self.main["rules"] = []
        self.main["rules"].append(rule)
        item = RuleItem(rule, self.path)
        self.items.append(item)
        self.save(self.path)
        return item

    def update(self, item: RuleItem, rule: dict):
        self.validate(rule)
        rule = ordered_rule(rule)
        rules = self.files[item.file]["rules"]
        idx = next(i for i, r in enumerate(rules) if r is item.data)
        rules[idx] = rule
        item.data = rule
        self.save(item.file)

    def set_enabled(self, item: RuleItem, on: bool):
        new = dict(item.data)
        new["enabled"] = on
        self.update(item, new)

    def delete(self, item: RuleItem):
        rules = self.files[item.file]["rules"]
        idx = next(i for i, r in enumerate(rules) if r is item.data)
        del rules[idx]
        self.items.remove(item)
        self.save(item.file)

    def duplicate(self, item: RuleItem) -> RuleItem:
        new = {k: v for k, v in item.data.items() if k != "source"}
        new["name"] = tr("{name} (Kopie)").format(name=item.name)
        new["enabled"] = False
        return self.add(new)

    # ---------- Speichern ----------
    def save(self, path: Path):
        data = self.files[path]
        if path == self.path:
            header = self.headers.get(path) or MAIN_HEADER
            if "verwaltet" not in header:
                header = MAIN_HEADER
            out = {}
            for k in ("settings", "include", "rules"):
                if k in data:
                    out[k] = data[k]
            for k, v in data.items():
                if k not in out:
                    out[k] = v
            out["rules"] = [ordered_rule(r) for r in out.get("rules") or []]
            data = out
        else:
            data = dict(data)
            data["rules"] = [ordered_rule(r) for r in data.get("rules") or []]
            header = self.headers.get(path, "")
        text = header + dump_yaml(data)
        # Vor dem ersten Überschreiben eine Sicherung anlegen
        bak = path.with_suffix(path.suffix + ".bak")
        if path.exists() and not bak.exists():
            shutil.copy2(path, bak)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        self.headers[path] = header
        self.mtimes = self._mtimes()

    # ---------- Prüfungen ----------
    def warnings(self) -> list[str]:
        try:
            return cfgmod.load(self.path).warnings
        except cfgmod.ConfigError as e:
            return [str(e)]

    def same_trigger(self, rule: dict, exclude: RuleItem | None = None) -> list[RuleItem]:
        tmp = RuleItem(rule, self.path)
        key = tmp.trigger_key()
        if not key:
            return []
        apps = {a.lower() for a in tmp.apps}
        out = []
        for it in self.items:
            if it is exclude or not it.enabled or it.trigger_key() != key:
                continue
            other = {a.lower() for a in it.apps}
            if apps == other or (apps & other):
                out.append(it)
        return out
