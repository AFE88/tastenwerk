"""Tastenkürzel von GNOME/Zorin auslesen, um Überschneidungen zu erkennen."""

from __future__ import annotations

import ast
import re
import subprocess

from .keys import Combo, KeyError_

SCHEMAS = [
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
    "org.gnome.shell.extensions.zorin-tiling-shell",
    "org.gnome.shell.extensions.zorin-taskbar",
]
_MOD = {"super": "super", "primary": "ctrl", "control": "ctrl", "ctrl": "ctrl",
        "alt": "alt", "shift": "shift", "meta": "super", "hyper": "super", "mod4": "super"}


def accel_to_combo(accel: str) -> Combo | None:
    mods = [m.lower() for m in re.findall(r"<([^>]+)>", accel)]
    key = re.sub(r"<[^>]+>", "", accel).strip()
    if not key:
        return None
    try:
        names = [_MOD[m] for m in mods if m in _MOD]
        return Combo.parse("+".join(names + [key]))
    except (KeyError_, KeyError):
        return None


def _gsettings(*args) -> str:
    try:
        return subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def system_shortcuts() -> dict[Combo, str]:
    """Alle belegten Systemkürzel: Kombination -> "schema key"."""
    out: dict[Combo, str] = {}
    installed = set(_gsettings("list-schemas").split())
    for schema in SCHEMAS:
        if schema not in installed:
            continue
        for line in _gsettings("list-recursively", schema).splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            _, key, val = parts
            val = val.strip()
            if val.startswith("@as "):
                val = val[4:]
            accels = []
            if val.startswith("["):
                try:
                    accels = ast.literal_eval(val)
                except (ValueError, SyntaxError):
                    continue
            elif val.startswith("'") and "<" in val:
                accels = [val.strip("'")]
            for a in accels:
                if isinstance(a, str) and a:
                    c = accel_to_combo(a)
                    if c:
                        out.setdefault(c, f"{schema.split('.')[-1]} → {key}")
    # Kürzel von Shell-Erweiterungen mit eigenem Schema (z. B. ClipMaster: Super+Shift+V)
    for schema, key, accel, ext in extension_shortcuts():
        c = accel_to_combo(accel)
        if c:
            out.setdefault(c, f"Erweiterung {ext} → {key}")
    # Eigene Tastenkürzel aus den GNOME-Einstellungen
    paths = _gsettings("get", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings")
    for p in re.findall(r"'([^']+)'", paths):
        schema = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{p}"
        b = _gsettings("get", schema, "binding").strip().strip("'")
        name = _gsettings("get", schema, "name").strip().strip("'")
        c = accel_to_combo(b) if b else None
        if c:
            out.setdefault(c, f"Eigenes GNOME-Kürzel „{name}“")
    return out


def extension_shortcuts() -> list[tuple[str, str, str, str]]:
    """Tastenkürzel eingeschalteter GNOME-Erweiterungen: (schema, key, accel, Name)."""
    import json
    from pathlib import Path
    enabled = re.findall(r"'([^']+)'", _gsettings("get", "org.gnome.shell", "enabled-extensions"))
    out = []
    for uuid in enabled:
        for base in (Path.home() / ".local/share/gnome-shell/extensions", Path("/usr/share/gnome-shell/extensions")):
            d = base / uuid
            sd = d / "schemas"
            if not (sd / "gschemas.compiled").exists():
                continue
            try:
                meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            schema = meta.get("settings-schema")
            if not schema:
                break
            name = meta.get("name", uuid)
            for line in _gsettings("--schemadir", str(sd), "list-recursively", schema).splitlines():
                parts = line.split(" ", 2)
                if len(parts) < 3 or not parts[2].lstrip("@as ").startswith("['<"):
                    continue
                try:
                    accels = ast.literal_eval(parts[2].replace("@as ", ""))
                except (ValueError, SyntaxError):
                    continue
                for a in accels:
                    if isinstance(a, str) and a:
                        out.append((schema, parts[1], a, name))
            break
    return out
