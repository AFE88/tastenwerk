"""Übersetzungen.

Die Texte im Quellcode sind deutsch. ``_("…")`` schlägt sie in ``locale/<sprache>.po`` nach.
Die .po-Dateien werden direkt gelesen – kein msgfmt nötig. Gibt es für eine Sprache keine
Übersetzung, wird Englisch verwendet (Deutsch bleibt Deutsch).

Neue Sprache: ``locale/en.po`` nach ``locale/<code>.po`` kopieren und die msgstr-Zeilen übersetzen.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
SOURCE_LANG = "de"
LANGUAGES = {"auto": None, "de": "Deutsch", "en": "English"}

_catalog: dict[str, str] = {}
_plural: dict[str, list[str]] = {}
current = SOURCE_LANG


def _configured_language() -> str:
    """Sprache aus der Konfiguration lesen, ohne die ganze Konfiguration zu laden."""
    base = Path(os.environ.get("TASTENWERK_CONFIG_DIR") or
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tastenwerk")
    try:
        text = (base / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return "auto"
    m = re.search(r"^\s+language:\s*['\"]?([A-Za-z_]+)", text, re.M)
    return m.group(1) if m else "auto"


def system_language() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v and v not in ("C", "POSIX", "C.UTF-8"):
            return v.split(":")[0].split("_")[0].split(".")[0].lower()
    return "en"


def _unquote(s: str) -> str:
    return ast.literal_eval(s)


def parse_po(path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    single: dict[str, str] = {}
    plural: dict[str, list[str]] = {}
    entry: dict[str, object] = {}
    key = None

    def flush():
        mid = entry.get("msgid")
        if mid:
            if "msgid_plural" in entry:
                forms = [entry.get(f"msgstr[{i}]", "") for i in range(2)]
                if all(forms):
                    plural[mid] = forms
            elif entry.get("msgstr"):
                single[mid] = entry["msgstr"]
        entry.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            if not line and entry:
                flush()
            continue
        m = re.match(r'^(msgid|msgid_plural|msgstr(?:\[\d\])?|msgctxt)\s+(".*")$', line)
        if m:
            if m.group(1) == "msgid" and "msgid" in entry:
                flush()
            key = m.group(1)
            entry[key] = _unquote(m.group(2))
        elif line.startswith('"') and key:
            entry[key] = entry.get(key, "") + _unquote(line)
    flush()
    return single, plural


def init(lang: str | None = None):
    """Sprache festlegen. None = aus Einstellung bzw. System."""
    global _catalog, _plural, current
    lang = (lang or _configured_language() or "auto").lower()
    if lang == "auto":
        lang = system_language()
    current = lang
    _catalog, _plural = {}, {}
    if lang == SOURCE_LANG:
        return
    for cand in (lang, "en"):
        po = LOCALE_DIR / f"{cand}.po"
        if po.exists():
            _catalog, _plural = parse_po(po)
            current = cand
            return
    current = SOURCE_LANG


def _(text: str) -> str:
    return _catalog.get(text) or text


tr = _


def N_(text: str) -> str:
    """Nur markieren (für Tabellen, die später übersetzt angezeigt werden)."""
    return text


def ngettext(singular: str, plural: str, n: int) -> str:
    forms = _plural.get(singular)
    if forms:
        return forms[0] if n == 1 else forms[1]
    return singular if n == 1 else plural


trn = ngettext

init()
