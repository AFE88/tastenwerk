"""Text mit Claude umformen – über das Claude-Code-CLI, also mit dem normalen Abo (kein API-Schlüssel)."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile

PROMPT_TEMPLATE = """{instruction}

Hier ist der Text:

<text>
{text}
</text>

Antworte ausschließlich mit dem fertigen Ergebnistext – ohne Einleitung, ohne Erklärung, ohne Anführungszeichen oder Code-Blöcke drumherum."""


def find_claude(command: str = "claude") -> str | None:
    """Claude-CLI suchen: zuerst im PATH, sonst die von der Desktop-App mitgelieferte Version."""
    if os.path.isabs(command) and os.access(command, os.X_OK):
        return command
    found = shutil.which(command)
    if found:
        return found
    for cand in (os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude")):
        if os.access(cand, os.X_OK):
            return cand
    bundled = glob.glob(os.path.expanduser("~/.config/Claude/claude-code/*/*/claude"))
    bundled = [b for b in bundled if os.access(b, os.X_OK)]
    if bundled:
        return max(bundled, key=os.path.getmtime)
    return None


def transform(text: str, instruction: str, settings: dict) -> str:
    ai = settings.get("ai") or {}
    exe = find_claude(ai.get("command", "claude"))
    if not exe:
        raise RuntimeError("Claude-CLI nicht gefunden. Installieren mit: curl -fsSL https://claude.ai/install.sh | bash")
    cmd = [exe, "-p", "--output-format", "text"]
    if ai.get("model"):
        cmd += ["--model", str(ai["model"])]
    prompt = PROMPT_TEMPLATE.format(instruction=instruction.strip(), text=text)
    # In einem leeren Ordner starten, damit keine Projekt-Dateien als Kontext geladen werden
    with tempfile.TemporaryDirectory(prefix="tastenwerk-ai-") as tmp:
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tmp,
                             timeout=float(ai.get("timeout", 120)))
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip().splitlines()
        raise RuntimeError("Claude-Fehler: " + (msg[-1] if msg else f"Exit {res.returncode}"))
    out = res.stdout.strip()
    if not out:
        raise RuntimeError("Claude hat nichts zurückgegeben")
    return out
