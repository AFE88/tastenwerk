"""Tastenwerk – Tastenkürzel, Tastenfolgen und Fenster-Aktionen für Linux (X11)."""

from pathlib import Path as _Path

# Version des letzten Release. Bei einem neuen Release hier erhöhen und den Git-Tag v<Version> setzen.
BASE_VERSION = "0.2.0"
APP_NAME = "Tastenwerk"


def _git_version() -> str | None:
    """„0.2.0“ genau auf dem Tag, „0.2.0+3 (a1b2c3d)“ drei Änderungen danach – aus dem Git-Ordner."""
    import subprocess
    root = _Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--long", "--match", "v[0-9]*"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    # v0.2.0-3-ga1b2c3d
    try:
        tag, n, sha = out.rsplit("-", 2)
    except ValueError:
        return None
    ver = tag.lstrip("v")
    return ver if n == "0" else f"{ver}+{n} ({sha.removeprefix('g')})"


__version__ = _git_version() or BASE_VERSION
