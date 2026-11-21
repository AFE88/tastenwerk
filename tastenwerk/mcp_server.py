"""MCP-Server: Claude (Code/Desktop) kann Tastenwerk-Regeln lesen, prüfen und anlegen.

Start:  tastenwerk mcp   (spricht JSON-RPC 2.0 über stdin/stdout, ohne Zusatzpakete)

Bewusst NICHT enthalten: Zwischenablage-Verlauf (enthält Passwörter), Fenstertitel,
Tasten senden oder Aktionen auslösen. Der Server ändert nur die Konfiguration.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

from . import __version__, config as cfgmod, control
from .store import Store, RuleItem, dump_yaml

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """Tastenwerk is a keyboard shortcut / text expansion / window management tool for Linux (X11),
similar to BetterTouchTool. Rules live in ~/.config/tastenwerk/config.yaml.
Workflow: call rule_reference once for the syntax, list_rules to see what exists, check_trigger before
picking a new hotkey, list_apps for real program names (don't guess them), then add_rule / update_rule. Changes are applied immediately (the running service
reloads). Rule ids are positions and can shift after add/delete - always use ids from the latest list_rules.
Ask the user before deleting rules or overwriting an existing shortcut."""

_RULE_SCHEMA = {
    "type": "object",
    "description": "A rule exactly as in config.yaml, e.g. {\"name\": \"Obsidian\", \"hotkey\": \"super+o\", "
                   "\"do\": [{\"app\": \"obsidian\"}]}. Exactly one trigger: hotkey, abbreviation or gesture. "
                   "Optional: apps, not_apps, enabled, notes. See rule_reference.",
    "additionalProperties": True,
}

TOOLS = [
    {
        "name": "rule_reference",
        "title": "Rule syntax",
        "description": "Syntax of Tastenwerk rules: triggers, all action types, window operations, "
                       "placeholders. Read this before writing rules.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_rules",
        "title": "List rules",
        "description": "List the configured rules with id, trigger, apps, state and a readable description "
                       "of their actions. Optional text filter on name, trigger or action.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Only rules containing this text (case-insensitive)"},
            "include_disabled": {"type": "boolean", "default": True},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_rule",
        "title": "Show rule",
        "description": "The full YAML of one rule.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "check_trigger",
        "title": "Check trigger",
        "description": "Is a hotkey, abbreviation or gesture still free? Reports other Tastenwerk rules, "
                       "GNOME / extension shortcuts and abbreviation prefix clashes.",
        "inputSchema": {"type": "object", "properties": {
            "hotkey": {"type": "string", "description": "e.g. super+alt+o"},
            "abbreviation": {"type": "string", "description": "e.g. ##mail"},
            "gesture": {"type": "string", "description": "e.g. swipe up 4"},
            "apps": {"type": "array", "items": {"type": "string"},
                     "description": "Only check for these apps (WM_CLASS)"},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "validate_rule",
        "title": "Validate rule",
        "description": "Dry run: check a rule for errors and conflicts without saving it.",
        "inputSchema": {"type": "object", "properties": {"rule": _RULE_SCHEMA}, "required": ["rule"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_rule",
        "title": "Add rule",
        "description": "Validate and save a new rule, then reload the running service.",
        "inputSchema": {"type": "object", "properties": {"rule": _RULE_SCHEMA}, "required": ["rule"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "update_rule",
        "title": "Change rule",
        "description": "Replace a rule completely with a new version (get_rule first, change, send back). "
                       "'expected_name' guards against ids that shifted.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "integer"}, "expected_name": {"type": "string"}, "rule": _RULE_SCHEMA,
        }, "required": ["id", "expected_name", "rule"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "set_rule_enabled",
        "title": "Switch rule on/off",
        "description": "Switch a rule on or off without deleting it.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "integer"}, "expected_name": {"type": "string"}, "enabled": {"type": "boolean"},
        }, "required": ["id", "expected_name", "enabled"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "delete_rule",
        "title": "Delete rule",
        "description": "Delete a rule. Returns its YAML so it can be restored with add_rule. "
                       "Prefer set_rule_enabled(false) unless the user really wants it gone.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "integer"}, "expected_name": {"type": "string"},
        }, "required": ["id", "expected_name"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "status",
        "title": "Status",
        "description": "Version, whether the service runs or is paused, config file, rule counts and "
                       "configuration warnings.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_log",
        "title": "Read log",
        "description": "Last lines of the Tastenwerk log - which rules fired, errors, why a shortcut "
                       "did not trigger. Contains no typed text or clipboard contents.",
        "inputSchema": {"type": "object", "properties": {
            "lines": {"type": "integer", "default": 60, "minimum": 1, "maximum": 400},
            "contains": {"type": "string", "description": "Only lines containing this text"},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_apps",
        "title": "Open programs",
        "description": "Program names (WM_CLASS) of the open windows, for 'apps:' / 'not_apps:' and the "
                       "'app' action. Window titles are deliberately not included.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]


# True, wenn mit --config eine andere Datei als die des Dienstes bearbeitet wird
OTHER_CONFIG = False


class ToolError(Exception):
    """Fehler, den das Modell sehen und korrigieren soll (isError statt JSON-RPC-Fehler)."""


# ---------------------------------------------------------------- Hilfen

def _store() -> Store:
    # Bei jedem Aufruf frisch laden – die Oberfläche kann die Datei zwischendurch ändern.
    return Store(cfgmod.CONFIG_FILE)


def _trigger(it: RuleItem) -> str:
    if it.hotkey:
        return f"hotkey {it.hotkey}"
    if it.abbreviation:
        return f"abbreviation {it.abbreviation}"
    if it.gesture:
        return f"gesture {it.gesture}"
    return "?"


def _summary(i: int, it: RuleItem, root: Path) -> dict:
    from .actions import describe_action
    out = {"id": i, "name": it.name, "trigger": _trigger(it), "enabled": it.enabled,
           "actions": [describe_action(a) for a in it.actions]}
    if it.apps:
        out["apps"] = it.apps
    if it.not_apps:
        out["not_apps"] = it.not_apps
    if it.file != root:
        out["file"] = str(it.file.relative_to(root.parent)) if it.file.is_relative_to(root.parent) else str(it.file)
    return out


def _item(store: Store, rid, expected_name: str | None = None) -> RuleItem:
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        raise ToolError("id must be a number from list_rules") from None
    if not 1 <= rid <= len(store.items):
        raise ToolError(f"No rule with id {rid} (there are {len(store.items)}). Call list_rules again.")
    it = store.items[rid - 1]
    if expected_name is not None and it.name != expected_name:
        raise ToolError(f"Rule {rid} is now „{it.name}“, not „{expected_name}“ - ids shifted, call list_rules again.")
    return it


def _rule_arg(args: dict) -> dict:
    rule = args.get("rule")
    if isinstance(rule, str):
        # Manche Clients schicken das Objekt als Text
        try:
            import yaml
            rule = yaml.safe_load(rule)
        except Exception:
            raise ToolError("rule must be an object") from None
    if not isinstance(rule, dict) or not rule:
        raise ToolError("rule must be an object like {name, hotkey, do: [...]}")
    return rule


def _validate(store: Store, rule: dict) -> None:
    try:
        store.validate(rule)
    except cfgmod.ConfigError as e:
        raise ToolError(f"Invalid rule: {e}") from None


def _conflicts(store: Store, rule: dict, exclude: RuleItem | None = None) -> list[str]:
    """Überschneidungen mit anderen Regeln, GNOME-Kürzeln und Tastenfolgen-Anfängen."""
    out = []
    probe = dict(rule)
    probe.setdefault("enabled", True)
    for it in store.same_trigger(probe, exclude=exclude):
        i = store.items.index(it) + 1
        scope = ", ".join(it.apps) if it.apps else "global"
        out.append(f"Same trigger as rule {i} „{it.name}“ ({scope})")
    hk = rule.get("hotkey")
    if hk:
        from .keys import Combo, KeyError_
        try:
            combo = Combo.parse(str(hk))
        except KeyError_ as e:
            return out + [f"Invalid hotkey: {e}"]
        try:
            from .gnome import system_shortcuts
            sysmap = system_shortcuts()
        except Exception:   # gsettings fehlt o. ä.
            sysmap = {}
        if combo in sysmap:
            out.append(f"{combo} is also a GNOME shortcut: {sysmap[combo]} - GNOME may swallow it")
    ab = rule.get("abbreviation") or rule.get("abbr")
    if ab:
        cs = store.settings.get("abbreviation_case_sensitive")
        a = str(ab) if cs else str(ab).lower()
        for i, it in enumerate(store.items, 1):
            if it is exclude or not it.enabled or not it.abbreviation:
                continue
            b = str(it.abbreviation) if cs else str(it.abbreviation).lower()
            if a != b and b.startswith(a):
                out.append(f"„{a}“ is the start of „{b}“ (rule {i} „{it.name}“) - „{b}“ could never fire")
            elif a != b and a.startswith(b):
                out.append(f"„{b}“ (rule {i} „{it.name}“) is the start of „{a}“ - „{a}“ could never fire")
    ge = rule.get("gesture")
    if ge:
        try:
            from .gestures import Gesture, system_gestures
            g = Gesture.parse(str(ge))
            used = system_gestures()
            if g in used:
                out.append(f"Gesture „{g}“ also triggers: {used[g]}")
        except Exception:
            pass
    return out


def _reload() -> str:
    if OTHER_CONFIG:
        return "Saved (separate config file - the running service was not reloaded)."
    if not control.running_pid():
        return "Saved. Tastenwerk is not running - it will use the rule on next start (tastenwerk run / GUI)."
    return "Saved and applied (service reloaded)." if control.reload() else "Saved, but the reload signal failed."


def _reference() -> str:
    text = (control.PROJECT / "config.example.yaml").read_text(encoding="utf-8")
    m = re.search(r"^#\s*Auslöser.*?(?=^# ─{10,})", text, re.S | re.M)
    block = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in (m.group(0) if m else "").splitlines())
    return (
        "Rule format (YAML / JSON object). Comments are German.\n\n" + block.strip() + "\n\n"
        "Key names: ctrl alt shift super altgr + any X keysym (a, F5, Return, Escape, Tab, space, Left, "
        "Prior/Next = Page Up/Down, Print, XF86AudioPlay …). Several key presses: \"tab tab ctrl+v\".\n"
        "Window operations: " + ", ".join(cfgmod.WINDOW_OPS) + "\n"
        "Action types: " + ", ".join(cfgmod.ACTION_TYPES) + "\n\n"
        "Example:\n" + dump_yaml({"name": "Dateien holen", "hotkey": "super+alt+e",
                                   "do": [{"app": "org.gnome.Nautilus", "if_active": "minimize"}]})
    )


# ---------------------------------------------------------------- Werkzeuge

def tool_rule_reference(args):
    return _reference()


def tool_list_rules(args):
    store = _store()
    q = str(args.get("query") or "").lower()
    with_off = args.get("include_disabled", True)
    rules = []
    for i, it in enumerate(store.items, 1):
        if not with_off and not it.enabled:
            continue
        s = _summary(i, it, store.path)
        if q and q not in json.dumps(s, ensure_ascii=False).lower():
            continue
        rules.append(s)
    return {"config": str(store.path), "count": len(rules), "total": len(store.items), "rules": rules}


def tool_get_rule(args):
    store = _store()
    it = _item(store, args.get("id"))
    return f"# id {args['id']} – {it.file}\n" + dump_yaml(it.data)


def tool_check_trigger(args):
    given = [k for k in ("hotkey", "abbreviation", "gesture") if args.get(k)]
    if len(given) != 1:
        raise ToolError("Give exactly one of hotkey, abbreviation, gesture")
    store = _store()
    probe = {given[0]: args[given[0]], "do": [{"notify": "test"}]}
    if args.get("apps"):
        probe["apps"] = args["apps"]
    _validate(store, probe)
    found = _conflicts(store, probe)
    return {"free": not found, "conflicts": found}


def tool_validate_rule(args):
    store = _store()
    rule = _rule_arg(args)
    _validate(store, rule)
    return {"valid": True, "conflicts": _conflicts(store, rule)}


def tool_add_rule(args):
    store = _store()
    rule = _rule_arg(args)
    _validate(store, rule)
    warn = _conflicts(store, rule)
    store.add(rule)
    return {"id": len(store.items), "result": _reload(), "warnings": warn}


def tool_update_rule(args):
    store = _store()
    it = _item(store, args.get("id"), args.get("expected_name"))
    rule = _rule_arg(args)
    _validate(store, rule)
    warn = _conflicts(store, rule, exclude=it)
    store.update(it, rule)
    return {"id": int(args["id"]), "result": _reload(), "warnings": warn}


def tool_set_rule_enabled(args):
    store = _store()
    it = _item(store, args.get("id"), args.get("expected_name"))
    on = bool(args.get("enabled"))
    warn = _conflicts(store, it.data, exclude=it) if on else []
    store.set_enabled(it, on)
    return {"id": int(args["id"]), "enabled": on, "result": _reload(), "warnings": warn}


def tool_delete_rule(args):
    store = _store()
    it = _item(store, args.get("id"), args.get("expected_name"))
    old = dump_yaml(it.data)
    store.delete(it)
    return {"deleted": it.name, "result": _reload(), "restore_with_add_rule": old}


def tool_status(args):
    pid = control.running_pid()
    out = {"version": __version__, "running": bool(pid),
           "paused": control.PAUSED_FILE.exists(), "config": str(cfgmod.CONFIG_FILE)}
    try:
        cfg = cfgmod.load(cfgmod.CONFIG_FILE)
        out.update(rules=len(cfg.rules), enabled=len(cfg.enabled_rules()),
                   files=[str(p) for p in cfg.files], warnings=cfg.warnings)
    except cfgmod.ConfigError as e:
        out["config_error"] = str(e)
    return out


def tool_read_log(args):
    n = max(1, min(int(args.get("lines") or 60), 400))
    needle = str(args.get("contains") or "")
    try:
        lines = control.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return "No log yet."
    if needle:
        lines = [l for l in lines if needle.lower() in l.lower()]
    return "\n".join(lines[-n:]) or "(no matching lines)"


def tool_list_apps(args):
    try:
        from .x11 import X11
        x = X11()
        apps = sorted({tuple(c) for w in x.client_list() if (c := x.wm_class(w))})
    except Exception as e:
        raise ToolError(f"No X11 display available: {e}") from None
    return {"apps": [{"instance": c[0], "class": c[-1]} for c in apps],
            "hint": "Use either name in apps:/not_apps:; for the 'app' action the desktop id or class works best."}


HANDLERS = {t["name"]: globals()["tool_" + t["name"]] for t in TOOLS}


# ---------------------------------------------------------------- Protokoll

def _content(result) -> dict:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
    out = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(result, dict):
        out["structuredContent"] = result
    return out


def handle(msg: dict) -> dict | None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:          # Benachrichtigung (z. B. notifications/initialized)
        return None

    def ok(res):
        return {"jsonrpc": "2.0", "id": mid, "result": res}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({
            "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "tastenwerk", "title": "Tastenwerk", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        fn = HANDLERS.get(name)
        if not fn:
            return err(-32602, f"Unknown tool: {name}")
        try:
            return ok(_content(fn(params.get("arguments") or {})))
        except ToolError as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
        except Exception as e:  # nie abstürzen, Fehler ans Modell geben
            traceback.print_exc(file=sys.stderr)
            return ok({"content": [{"type": "text", "text": f"Internal error: {e}"}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return err(-32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            out = [r for m in batch if isinstance(m, dict) and (r := handle(m)) is not None]
            resp = out if isinstance(msg, list) else (out[0] if out else None)
        if resp:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
