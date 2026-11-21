#!/usr/bin/env python3
"""Sammelt alle tr("…")/trn("…", "…", n)-Texte und aktualisiert locale/*.po.

Aufruf im Projektordner:  python3 tools/extract_messages.py
Neue Texte erscheinen in jeder .po-Datei mit leerem msgstr; vorhandene Übersetzungen bleiben.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tastenwerk.i18n import parse_po  # noqa: E402


def collect():
    msgs = {}      # msgid -> (plural | None, [Fundstellen])
    for f in sorted((ROOT / "tastenwerk").rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("tr", "trn") and n.args:
                args = n.args
                if not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args[:2 if n.func.id == "trn" else 1]):
                    continue
                mid = args[0].value
                plural = args[1].value if n.func.id == "trn" else None
                ref = f"{f.relative_to(ROOT)}:{n.lineno}"
                if mid in msgs:
                    msgs[mid][1].append(ref)
                else:
                    msgs[mid] = (plural, [ref])
    return msgs


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def write_po(path, msgs, lang):
    old_single, old_plural = parse_po(path) if path.exists() else ({}, {})
    out = [f'# Tastenwerk – Übersetzung ({lang})', 'msgid ""', 'msgstr ""',
           q("Content-Type: text/plain; charset=UTF-8\n"), q(f"Language: {lang}\n"), ""]
    missing = 0
    for mid, (plural, refs) in msgs.items():
        out.append("#: " + " ".join(refs[:4]))
        out.append("msgid " + q(mid))
        if plural is not None:
            forms = old_plural.get(mid, ["", ""])
            out.append("msgid_plural " + q(plural))
            out.append("msgstr[0] " + q(forms[0]))
            out.append("msgstr[1] " + q(forms[1]))
            missing += not all(forms)
        else:
            t = old_single.get(mid, "")
            out.append("msgstr " + q(t))
            missing += not t
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    return missing


if __name__ == "__main__":
    msgs = collect()
    (ROOT / "locale").mkdir(exist_ok=True)
    pos = sorted((ROOT / "locale").glob("*.po")) or [ROOT / "locale" / "en.po"]
    for po in pos:
        miss = write_po(po, msgs, po.stem)
        print(f"{po.relative_to(ROOT)}: {len(msgs)} Texte, davon {miss} noch nicht übersetzt")
