"""Hintergrunddienst steuern: Status, Start, Stopp, Pause, Autostart, Claude-Status."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT / "bin" / "tastenwerk"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
LOCK = RUNTIME / "tastenwerk.lock"
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
AUTOSTART = XDG_CONFIG / "autostart" / "tastenwerk.desktop"
LOG_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "tastenwerk"
LOG_FILE = LOG_DIR / "tastenwerk.log"
APP_ID = "de.tastenwerk.Tastenwerk"
CLIP_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tastenwerk" / "clipboard"
CLIP_FILE = CLIP_DIR / "history.json"
DESKTOP_FILE = Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop"
ICON_SRC = PROJECT / "data" / "tastenwerk.svg"
ICON_DST = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def running_pid() -> int | None:
    try:
        pid = int(LOCK.read_text().strip())
        os.kill(pid, 0)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None
    # PID könnte wiederverwendet sein – prüfen, ob es wirklich Tastenwerk ist
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        return None
    return pid if "tastenwerk" in cmd and " run" in cmd else None


def start() -> bool:
    if running_pid():
        return True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a", encoding="utf-8")
    subprocess.Popen([str(LAUNCHER), "run"], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(40):
        time.sleep(0.05)
        if running_pid():
            return True
    return False


def stop() -> bool:
    pid = running_pid()
    if not pid:
        return True
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        time.sleep(0.05)
        if not running_pid():
            return True
    return False


def _signal(sig) -> bool:
    pid = running_pid()
    if pid:
        os.kill(pid, sig)
        return True
    return False


def reload() -> bool:
    return _signal(signal.SIGHUP)


def pause() -> bool:
    return _signal(signal.SIGUSR1)


def resume() -> bool:
    return _signal(signal.SIGUSR2)


def autostart_enabled() -> bool:
    return AUTOSTART.exists()


def set_autostart(on: bool):
    if not on:
        if AUTOSTART.exists():
            AUTOSTART.unlink()
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text(
        "[Desktop Entry]\nType=Application\nName=Tastenwerk (Hintergrund)\n"
        "Comment=Tastenkürzel, Tastenfolgen und Fenster-Aktionen\n"
        f"Exec={LAUNCHER} run\nIcon={APP_ID}\nNoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3\nTerminal=false\n", encoding="utf-8")


def install_launcher() -> bool:
    """Eintrag im Programm-Menü anlegen (einmalig). True, wenn neu angelegt."""
    new = not DESKTOP_FILE.exists()
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ICON_SRC.exists():
        ICON_DST.parent.mkdir(parents=True, exist_ok=True)
        ICON_DST.write_bytes(ICON_SRC.read_bytes())
    DESKTOP_FILE.write_text(
        "[Desktop Entry]\nType=Application\nName=Tastenwerk\n"
        "GenericName=Tastenkürzel & Makros\n"
        "Comment=Tastenkürzel, Tastenfolgen, Text-Bausteine und Fenster-Aktionen\n"
        f"Exec={LAUNCHER} gui\nIcon={APP_ID}\nTerminal=false\n"
        "Categories=Utility;GTK;\nKeywords=Shortcut;Kürzel;Hotkey;Makro;BetterTouchTool;Text;\n"
        f"StartupWMClass={APP_ID}\nStartupNotify=true\n", encoding="utf-8")
    subprocess.run(["update-desktop-database", str(DESKTOP_FILE.parent)], capture_output=True)
    subprocess.run(["gtk-update-icon-cache", "-q", "-t", str(ICON_DST.parents[3])], capture_output=True)
    return new


def claude_status(command: str = "claude") -> dict:
    """{"found": bool, "path": str|None, "logged_in": bool, "method": str}"""
    from .ai import find_claude
    exe = find_claude(command)
    if not exe:
        return {"found": False, "path": None, "logged_in": False, "method": ""}
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError):
        data = {}
    return {"found": True, "path": exe, "logged_in": bool(data.get("loggedIn")),
            "method": data.get("authMethod", ""), "email": data.get("email") or data.get("account", "")}


def clipboard_stats() -> tuple[int, int]:
    """(Einträge, davon angeheftet) im Zwischenablage-Verlauf."""
    try:
        items = json.loads(CLIP_FILE.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return 0, 0
    return len(items), sum(1 for i in items if i.get("pinned"))


def clipboard_clear(keep_pinned: bool = True) -> int:
    """Verlauf leeren (ohne GTK 3 – auch aus der Oberfläche nutzbar). Gibt die Zahl gelöschter Einträge zurück."""
    try:
        data = json.loads(CLIP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    items = data.get("items", [])
    keep = [i for i in items if keep_pinned and i.get("pinned")]
    files = {i.get("file") for i in keep if i.get("file")}
    for i in items:
        if i not in keep and i.get("file") and i["file"] not in files:
            try:
                (CLIP_DIR / "images" / i["file"]).unlink()
            except OSError:
                pass
    data["items"] = keep
    tmp = CLIP_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CLIP_FILE)
    reload()   # laufender Dienst liest den Verlauf neu
    return len(items) - len(keep)
