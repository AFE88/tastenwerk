"""Kommandozeile: tastenwerk run | check | list | apps | import | init | reload | autostart"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__
from . import config as cfgmod

PROJECT = Path(__file__).resolve().parent.parent
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
LOCK = RUNTIME / "tastenwerk.lock"
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "tastenwerk.desktop"


def _x11_check():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        print("⚠ Wayland-Sitzung erkannt. Tastenwerk braucht X11 (beim Anmelden „Zorin Desktop auf Xorg“ wählen).")
    if not os.environ.get("DISPLAY"):
        sys.exit("Kein DISPLAY – Tastenwerk muss in der grafischen Sitzung laufen.")


def running_pid() -> int | None:
    try:
        pid = int(LOCK.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def cmd_run(args):
    _x11_check()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"Tastenwerk läuft bereits (PID {running_pid()}). Neu laden: tastenwerk reload")
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    from .daemon import Daemon
    path = Path(args.config) if args.config else cfgmod.CONFIG_FILE
    if not path.exists():
        from .importer.wizard import ensure_config
        ensure_config(path)
        print(f"Neue Konfiguration angelegt: {path}")
    try:
        Daemon(path).run()
    except cfgmod.ConfigError as e:
        sys.exit(f"Konfiguration fehlerhaft:\n{e}")


def cmd_check(args):
    path = Path(args.config) if args.config else cfgmod.CONFIG_FILE
    try:
        cfg = cfgmod.load(path)
    except cfgmod.ConfigError as e:
        print(f"✗ {e}")
        return 1
    rules = cfg.enabled_rules()
    print(f"✓ {path} ist gültig – {len(rules)} aktive von {len(cfg.rules)} Regeln aus {len(cfg.files)} Datei(en)")
    for w in cfg.warnings:
        print(f"⚠ {w}")
    if os.environ.get("DISPLAY"):
        from .gnome import system_shortcuts
        system = system_shortcuts()
        for r in rules:
            if r.hotkey and r.hotkey in system:
                print(f"⚠ {r.hotkey} („{r.name}“) ist von GNOME belegt: {system[r.hotkey]}")
        if any(r.gesture for r in rules):
            from .gestures import system_gestures, touchegg_available
            used = system_gestures()
            for r in rules:
                if r.gesture and r.gesture in used:
                    print(f"⚠ Geste „{r.gesture.label}“ („{r.name}“) löst auch aus: {used[r.gesture]}")
            if not touchegg_available():
                print("⚠ Touchégg-Dienst nicht erreichbar – Touchpad-Gesten funktionieren nicht")
    from .ai import find_claude
    if any("ai" in a for r in rules for a in r.actions):
        exe = find_claude(cfg.settings.get("ai", {}).get("command", "claude"))
        print(f"{'✓' if exe else '⚠'} Claude-CLI: {exe or 'nicht gefunden – KI-Aktionen funktionieren nicht'}")
    pid = running_pid()
    print(f"{'✓ läuft (PID ' + str(pid) + ')' if pid else '○ läuft gerade nicht'}")
    return 0


def cmd_list(args):
    from .actions import describe_action
    cfg = cfgmod.load(Path(args.config) if args.config else None)
    for r in cfg.rules:
        if not r.enabled and not args.all:
            continue
        state = "" if r.enabled else "  (aus)"
        apps = f"  [{', '.join(r.apps)}]" if r.apps else ""
        print(f"{r.trigger_text:24} {r.name}{apps}{state}")
        for a in r.actions:
            print(f"{'':24}   → {describe_action(a)}")
    return 0


def cmd_apps(args):
    _x11_check()
    from .x11 import X11
    x = X11()
    print("Offene Fenster (für 'apps:' den ersten oder zweiten Namen verwenden):")
    for w in x.client_list():
        cls = x.wm_class(w)
        if cls:
            print(f"  {cls[0]:28} {cls[-1]:28} {x.title(w)[:50]}")
    if args.wait:
        for i in range(args.wait, 0, -1):
            print(f"\rKlick jetzt in das gewünschte Fenster … {i} ", end="", flush=True)
            time.sleep(1)
        cls = x.active_app()
        print(f"\nAktives Fenster: {' / '.join(cls) or '?'}")
    return 0


def cmd_init(args):
    from .importer.wizard import ensure_config
    path = cfgmod.CONFIG_FILE
    if ensure_config(path):
        print(f"✓ Konfiguration angelegt: {path}")
    else:
        print(f"Konfiguration gibt es schon: {path}")
    return 0


def cmd_import(args):
    from .importer.wizard import Wizard
    paths = [Path(p).expanduser() for p in args.files]
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit("Nicht gefunden: " + ", ".join(map(str, missing)))
    paths.sort(key=lambda p: p.stat().st_mtime)  # ältere zuerst, neuere gewinnen
    cfg = Path(args.config) if args.config else cfgmod.CONFIG_FILE
    return Wizard(paths, cfg, analyse_only=args.analyse, assume_defaults=args.yes).run()


def cmd_gui(args):
    _x11_check()
    from . import control
    if not control.DESKTOP_FILE.exists():
        control.install_launcher()
    from .gui.app import main as gui_main
    return gui_main()


def cmd_install(args):
    from . import control
    control.install_launcher()
    print(f"✓ Tastenwerk steht jetzt im Programm-Menü ({control.DESKTOP_FILE})")
    return 0


def cmd_reload(args):
    pid = running_pid()
    if not pid:
        print("Tastenwerk läuft nicht.")
        return 1
    os.kill(pid, signal.SIGHUP)
    print("✓ neu geladen")
    return 0


def cmd_stop(args):
    pid = running_pid()
    if not pid:
        print("Tastenwerk läuft nicht.")
        return 1
    os.kill(pid, signal.SIGTERM)
    print("✓ beendet")
    return 0


def cmd_autostart(args):
    from . import control
    control.set_autostart(args.state == "on")
    print(f"✓ Autostart {args.state == 'on' and 'an' or 'aus'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="tastenwerk", description=f"{APP_NAME} {__version__} – Tastenkürzel für Linux (X11)")
    p.add_argument("--config", help="andere Konfigurationsdatei verwenden")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("run", help="im Vordergrund starten")
    s.add_argument("--debug", action="store_true")
    s.set_defaults(fn=cmd_run)
    sub.add_parser("gui", help="Oberfläche öffnen").set_defaults(fn=cmd_gui)
    sub.add_parser("install", help="Eintrag im Programm-Menü anlegen").set_defaults(fn=cmd_install)
    sub.add_parser("check", help="Konfiguration prüfen").set_defaults(fn=cmd_check)
    s = sub.add_parser("list", help="Regeln auflisten")
    s.add_argument("--all", action="store_true", help="auch ausgeschaltete")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("apps", help="Programmnamen (WM_CLASS) für 'apps:' anzeigen")
    s.add_argument("--wait", type=int, default=0, metavar="SEK", help="danach das aktive Fenster anzeigen")
    s.set_defaults(fn=cmd_apps)
    sub.add_parser("init", help="Konfiguration aus Vorlage anlegen").set_defaults(fn=cmd_init)
    s = sub.add_parser("import", help="BetterTouchTool-Presets importieren (Assistent)")
    s.add_argument("files", nargs="+", help=".bttpreset oder .json")
    s.add_argument("--analyse", action="store_true", help="nur zeigen, nichts speichern")
    s.add_argument("--yes", action="store_true", help="alle Vorschläge ohne Rückfrage übernehmen")
    s.set_defaults(fn=cmd_import)
    sub.add_parser("reload", help="laufendes Tastenwerk neu laden").set_defaults(fn=cmd_reload)
    sub.add_parser("stop", help="laufendes Tastenwerk beenden").set_defaults(fn=cmd_stop)
    s = sub.add_parser("autostart", help="beim Anmelden starten")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn=cmd_autostart)
    args = p.parse_args(argv)
    if not getattr(args, "fn", None):
        args.fn = cmd_gui
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
