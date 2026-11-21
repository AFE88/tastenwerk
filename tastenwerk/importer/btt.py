"""BetterTouchTool-Presets (.json / .bttpreset) lesen und in Tastenwerk-Regeln übersetzen.

Die Übersetzung liefert für jeden BTT-Auslöser einen *Kandidaten* mit Status:
  ok          – lässt sich 1:1 übernehmen
  adapted     – übernehmbar, aber sinngemäß angepasst (Hinweise beachten)
  manual      – braucht Handarbeit (wird auf Wunsch deaktiviert als Vorlage übernommen)
  unsupported – gibt es unter Linux so nicht (Gesten, Touch Bar …)
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Tastatur: macOS-Keycodes beschreiben die *Position* einer Taste. Wir übersetzen
# sie in X11-Keycodes derselben Position und fragen dann die aktuelle Linux-
# Tastaturbelegung, wie die Taste heißt (z. B. Mac „Z“-Position = deutsches „y“).
# ---------------------------------------------------------------------------
MAC_TO_X = {
    0: 38, 1: 39, 2: 40, 3: 41, 4: 43, 5: 42, 6: 52, 7: 53, 8: 54, 9: 55,
    10: 49,   # ISO-Taste links neben der 1 (^)
    50: 94,   # ISO-Taste neben linker Umschalttaste (<>)
    11: 56, 12: 24, 13: 25, 14: 26, 15: 27, 16: 29, 17: 28, 18: 10, 19: 11, 20: 12,
    21: 13, 22: 15, 23: 14, 24: 21, 25: 18, 26: 16, 27: 20, 28: 17, 29: 19, 30: 35,
    31: 32, 32: 30, 33: 34, 34: 31, 35: 33, 36: 36, 37: 46, 38: 44, 39: 48, 40: 45,
    41: 47, 42: 51, 43: 59, 44: 61, 45: 57, 46: 58, 47: 60, 48: 23, 49: 65, 51: 22,
    53: 9,
    65: 91, 67: 63, 69: 86, 71: 77, 75: 106, 76: 104, 78: 82, 81: 125,
    82: 90, 83: 87, 84: 88, 85: 89, 86: 83, 87: 84, 88: 85, 89: 79, 91: 80, 92: 81,
    122: 67, 120: 68, 99: 69, 118: 70, 96: 71, 97: 72, 98: 73, 100: 74, 101: 75,
    109: 76, 103: 95, 111: 96, 105: 191, 107: 192, 113: 193, 106: 194, 64: 195,
    79: 196, 80: 197, 90: 198,
    114: 118, 115: 110, 116: 112, 117: 119, 119: 115, 121: 117,
    123: 113, 124: 114, 125: 116, 126: 111,
}
# Fallback-Namen (deutsche PC-Belegung), falls keine X-Verbindung besteht
X_FALLBACK = {
    38: "a", 39: "s", 40: "d", 41: "f", 43: "h", 42: "g", 52: "y", 53: "x", 54: "c", 55: "v",
    49: "dead_circumflex", 94: "less", 56: "b", 24: "q", 25: "w", 26: "e", 27: "r", 29: "z",
    28: "t", 10: "1", 11: "2", 12: "3", 13: "4", 15: "6", 14: "5", 21: "dead_acute", 18: "9",
    16: "7", 20: "ssharp", 17: "8", 19: "0", 35: "plus", 32: "o", 30: "u", 34: "udiaeresis",
    31: "i", 33: "p", 36: "Return", 46: "l", 44: "j", 48: "adiaeresis", 45: "k",
    47: "odiaeresis", 51: "numbersign", 59: "comma", 61: "minus", 57: "n", 58: "m",
    60: "period", 23: "Tab", 65: "space", 22: "BackSpace", 9: "Escape",
    91: "KP_Decimal", 63: "KP_Multiply", 86: "KP_Add", 77: "Num_Lock", 106: "KP_Divide",
    104: "KP_Enter", 82: "KP_Subtract", 125: "KP_Equal", 90: "KP_0", 87: "KP_1", 88: "KP_2",
    89: "KP_3", 83: "KP_4", 84: "KP_5", 85: "KP_6", 79: "KP_7", 80: "KP_8", 81: "KP_9",
    67: "F1", 68: "F2", 69: "F3", 70: "F4", 71: "F5", 72: "F6", 73: "F7", 74: "F8", 75: "F9",
    76: "F10", 95: "F11", 96: "F12", 191: "F13", 192: "F14", 193: "F15", 194: "F16",
    195: "F17", 196: "F18", 197: "F19", 198: "F20",
    118: "Insert", 110: "Home", 112: "Prior", 119: "Delete", 115: "End", 117: "Next",
    113: "Left", 114: "Right", 116: "Down", 111: "Up",
}
MAC_MOD_KEYS = {55: "cmd", 54: "cmd", 56: "shift", 60: "shift", 58: "opt", 61: "opt",
                59: "ctrl", 62: "ctrl", 63: "fn", 57: None}
FLAG_SHIFT, FLAG_CTRL, FLAG_OPT, FLAG_CMD, FLAG_FN = 1 << 17, 1 << 18, 1 << 19, 1 << 20, 1 << 23

# Deutsche Mac-Belegung: Wahltaste + Taste erzeugt Sonderzeichen
MAC_DE_OPT_CHARS = {
    (frozenset({"opt"}), 23): "[", (frozenset({"opt"}), 22): "]", (frozenset({"opt"}), 26): "|",
    (frozenset({"opt"}), 28): "{", (frozenset({"opt"}), 25): "}", (frozenset({"opt"}), 37): "@",
    (frozenset({"opt"}), 45): "~", (frozenset({"opt"}), 14): "€",
    (frozenset({"opt", "shift"}), 26): "\\",
}

MAC_MOD_LABEL = {"ctrl": "⌃", "opt": "⌥", "shift": "⇧", "cmd": "⌘", "fn": "fn"}
MAC_KEY_LABEL = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V", 10: "^",
    50: "<", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T", 18: "1", 19: "2",
    20: "3", 21: "4", 22: "6", 23: "5", 24: "´", 25: "9", 26: "7", 27: "ß", 28: "8", 29: "0",
    30: "+", 31: "O", 32: "U", 33: "Ü", 34: "I", 35: "P", 36: "↩", 37: "L", 38: "J", 39: "Ä",
    40: "K", 41: "Ö", 42: "#", 43: ",", 44: "-", 45: "N", 46: "M", 47: ".", 48: "⇥", 49: "Leertaste",
    51: "⌫", 53: "esc", 76: "⌤", 117: "⌦", 115: "↖", 119: "↘", 116: "⇞", 121: "⇟",
    123: "←", 124: "→", 125: "↓", 126: "↑", 122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5",
    97: "F6", 98: "F7", 100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12", 105: "F13",
    107: "F14", 113: "F15", 106: "F16", 64: "F17", 79: "F18", 80: "F19",
}


def mac_label(mods: set[str], keycode: int | None) -> str:
    order = ["ctrl", "opt", "shift", "cmd", "fn"]
    s = "".join(MAC_MOD_LABEL[m] for m in order if m in mods)
    if keycode is not None and keycode >= 0:
        s += MAC_KEY_LABEL.get(keycode, f"Taste{keycode}")
    return s


def send_label(codes_str) -> str:
    """„56,55,21“ -> „⇧⌘4“"""
    codes = [int(x) for x in str(codes_str).split(",") if x.strip().lstrip("-").isdigit()]
    mods = {MAC_MOD_KEYS[c] for c in codes if c in MAC_MOD_KEYS and MAC_MOD_KEYS[c]}
    main = [c for c in codes if c not in MAC_MOD_KEYS]
    if not main:
        return mac_label(mods, None)
    return " ".join(mac_label(mods, c) for c in main)


def flags_to_mods(flags: int | None) -> set[str]:
    if not flags or flags < 0:
        return set()
    out = set()
    if flags & FLAG_SHIFT:
        out.add("shift")
    if flags & FLAG_CTRL:
        out.add("ctrl")
    if flags & FLAG_OPT:
        out.add("opt")
    if flags & FLAG_CMD:
        out.add("cmd")
    if flags & FLAG_FN:
        out.add("fn")
    return out


class KeyResolver:
    """Wandelt eine macOS-Tastenposition in den Linux-Tastennamen der aktuellen Belegung."""

    def __init__(self):
        self.d = None
        try:
            from Xlib import display
            self.d = display.Display()
        except Exception:
            pass

    def name(self, mac_keycode: int) -> str | None:
        xkc = MAC_TO_X.get(mac_keycode)
        if xkc is None:
            return None
        if self.d is not None:
            from ..keys import keysym_to_name
            ks = self.d.keycode_to_keysym(xkc, 0)
            if ks:
                n = keysym_to_name(ks)
                if n:
                    return n
        return X_FALLBACK.get(xkc)


# ---------------------------------------------------------------------------
# Texte: BTT speichert eingefügte Texte als NSAttributedString (typedstream, base64).
# ---------------------------------------------------------------------------
def decode_attributed(b64: str) -> str | None:
    try:
        s = b64.replace("_", "/").replace("-", "+")
        raw = base64.b64decode(s + "=" * (-len(s) % 4))
    except (ValueError, TypeError):
        return None
    i = raw.find(b"NSString")
    if i < 0:
        return None
    j = raw.find(b"+", i + 8)
    if j < 0 or j + 2 > len(raw):
        return None
    b = raw[j + 1]
    if b == 0x81:
        n, start = int.from_bytes(raw[j + 2:j + 4], "little"), j + 4
    elif b == 0x82:
        n, start = int.from_bytes(raw[j + 2:j + 6], "little"), j + 6
    else:
        n, start = b, j + 2
    try:
        return raw[start:start + n].decode("utf-8")
    except UnicodeDecodeError:
        return raw[start:start + n].decode("utf-8", "replace")


_ICU_TO_STRFTIME = [
    ("yyyy", "%Y"), ("yy", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"), ("M", "%-m"),
    ("dd", "%d"), ("d", "%-d"), ("EEEE", "%A"), ("EEE", "%a"), ("HH", "%H"), ("H", "%-H"),
    ("hh", "%I"), ("h", "%-I"), ("mm", "%M"), ("ss", "%S"), ("a", "%p"),
]


def icu_to_strftime(fmt: str) -> str:
    out, i = "", 0
    while i < len(fmt):
        for icu, st in _ICU_TO_STRFTIME:
            if fmt.startswith(icu, i):
                out += st
                i += len(icu)
                break
        else:
            out += "%%" if fmt[i] == "%" else fmt[i]
            i += 1
    return out


def convert_btt_text(text: str) -> tuple[str, list[str]]:
    """BTT-Platzhalter in Tastenwerk-Platzhalter übersetzen. Gibt (Text, Hinweise) zurück."""
    notes = []
    text = text.replace("{", "{{").replace("}", "}}")

    def repl(m):
        inner = m.group(1)
        if inner.startswith("@dateformat:"):
            fmt = inner[len("@dateformat:"):]
            if "REPLACE_THIS" in fmt:
                notes.append("Datumsformat war in BTT nie ausgefüllt – bitte festlegen")
                return "{date}"
            return "{date:" + icu_to_strftime(fmt) + "}"
        if inner in ("clipboard_contents", "@clipboard"):
            return "{clipboard}"
        notes.append(f"BTT-Variable „{inner}“ gibt es nicht – bitte anpassen")
        return f"[{inner}]"

    text = re.sub(r"\(BTT\)(.*?)\(BTT\)", repl, text)
    text = re.sub(r"\{\{(clipboard_contents|BTTClipboardContent)\}\}", "{clipboard}", text)
    return text, notes


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------
APP_SUGGESTIONS = {
    "com.google.chrome": ["google-chrome", "chromium", "zen", "firefox", "brave-browser"],
    "company.thebrowser.browser": ["zen", "firefox", "google-chrome"],
    "com.apple.safari": ["zen", "firefox", "google-chrome"],
    "org.mozilla.firefox": ["firefox", "zen"],
    "com.microsoft.vscode": ["code", "codium", "code-oss"],
    "com.apple.terminal": ["gnome-terminal-server", "ptyxis", "kgx"],
    "com.googlecode.iterm2": ["gnome-terminal-server", "ptyxis", "kgx"],
    "com.apple.finder": ["org.gnome.nautilus"],
    "com.microsoft.word": ["libreoffice-writer"],
    "com.microsoft.powerpoint": ["libreoffice-impress"],
    "com.microsoft.excel": ["libreoffice-calc"],
    "com.microsoft.outlook": ["thunderbird", "betterbird", "evolution"],
    "com.microsoft.onenote.mac": ["md.obsidian.obsidian", "obsidian"],
    "com.agilebits.onepassword7": ["bitwarden", "1password"],
    "com.1password.1password": ["bitwarden", "1password"],
    "com.microsoft.rdc.macos": ["org.remmina.remmina", "remmina", "xfreerdp"],
    "com.citrix.receiver.icaviewer.mac": ["wfica"],
    "md.obsidian": ["md.obsidian.obsidian", "obsidian"],
    "com.tinyspeck.slackmacgap": ["slack"],
    "com.spotify.client": ["spotify"],
}
APP_NOTES = {
    "com.logi.optionsplus": "Maus-Einstellungen – übernimmt unter Linux OpenLogi",
    "com.logitech.manager.daemon": "Maus-Einstellungen – übernimmt unter Linux OpenLogi",
}
APP_LAUNCH = {
    "finder.app": ("open", "~"),
    "activity monitor.app": ("run", "gnome-system-monitor"),
    "terminal.app": ("run", "gnome-terminal"),
    "system preferences.app": ("run", "gnome-control-center"),
    "system settings.app": ("run", "gnome-control-center"),
    "textedit.app": ("run", "gnome-text-editor"),
    "calculator.app": ("run", "gnome-calculator"),
    "visual studio code.app": ("run", "code"),
    "google chrome.app": ("run", "google-chrome"),
    "obsidian.app": ("run", "obsidian"),
}


# ---------------------------------------------------------------------------
# Kandidaten
# ---------------------------------------------------------------------------
STATUS_ORDER = {"ok": 0, "adapted": 1, "manual": 2, "unsupported": 3}
STATUS_LABEL = {"ok": "✅ direkt", "adapted": "🟡 angepasst", "manual": "🛠 Handarbeit", "unsupported": "⛔ nicht übertragbar"}


@dataclass
class Candidate:
    source: str                     # "btt:<uuid>"
    app_name: str                   # BTT-Programmname („Global“ = überall)
    bundle: str
    kind: str                       # hotkey | abbr | other
    mac_trigger: str                # Anzeige wie auf dem Mac
    btt_enabled: bool
    status: str = "ok"
    notes: list[str] = field(default_factory=list)
    hotkey_mac: tuple | None = None  # (mods, keycode) – wird erst mit der Cmd-Wahl übersetzt
    abbreviation: str | None = None
    gesture: str | None = None       # „swipe down 4“
    actions: list[dict] = field(default_factory=list)
    action_text: str = ""            # Beschreibung der BTT-Aktion(en)
    name: str = ""

    def worsen(self, status: str, note: str | None = None):
        if STATUS_ORDER[status] > STATUS_ORDER[self.status]:
            self.status = status
        if note and note not in self.notes:
            self.notes.append(note)

    @property
    def is_global(self) -> bool:
        return self.bundle in ("BT.G", "") or self.app_name.lower() == "global"


@dataclass
class MapOptions:
    trigger_cmd: str = "super"      # was ⌘ in Auslösern wird: super | ctrl | alt


def trigger_combo(c: Candidate, opts: MapOptions, keys: KeyResolver) -> tuple[str | None, list[str]]:
    """Mac-Auslöser in eine Linux-Kombination wie ``super+shift+v`` übersetzen."""
    if not c.hotkey_mac:
        return None, []
    mods, kc = c.hotkey_mac
    notes = []
    key = keys.name(kc)
    if not key:
        return None, [f"Taste {mac_label(set(), kc)} gibt es auf der PC-Tastatur nicht"]
    out = []
    for m in ("ctrl", "opt", "shift", "cmd"):
        if m not in mods:
            continue
        if m == "cmd":
            out.append(opts.trigger_cmd)
        elif m == "opt":
            out.append("alt")
        else:
            out.append(m)
    if "fn" in mods and not (kc in (123, 124, 125, 126) or 96 <= kc <= 122 or kc in (64, 79, 80, 90)):
        notes.append("fn-Taste entfällt")
    if kc in (105, 107, 113, 106, 64, 79, 80, 90):
        notes.append(f"{MAC_KEY_LABEL.get(kc, 'F13+')} gibt es auf PC-Tastaturen meist nicht – "
                     "z. B. per OpenLogi auf eine Maustaste legen oder anderen Auslöser wählen")
    # doppelte Modifikatoren (⌘ → ctrl bei vorhandenem ⌃) zusammenfassen
    seen = []
    for m in out:
        if m not in seen:
            seen.append(m)
    if len(seen) < len(out):
        notes.append("⌃ und ⌘ fallen auf dieselbe Taste – Kombination wurde zusammengefasst")
    return "+".join(seen + [key]), notes


def _send_shortcut(codes_str: str, keys: KeyResolver) -> tuple[dict | None, str, list[str]]:
    """BTTShortcutToSend (Mac-Keycodes) -> (Aktion, Status, Hinweise)."""
    codes = [int(x) for x in str(codes_str).split(",") if x.strip().lstrip("-").isdigit()]
    mods, main = set(), []
    for c in codes:
        if c in MAC_MOD_KEYS:
            if MAC_MOD_KEYS[c]:
                mods.add(MAC_MOD_KEYS[c])
        else:
            main.append(c)
    mods.discard("fn")
    if not main:
        return None, "manual", ["Sendet nur Modifikator-Tasten"]
    if len(main) == 1:
        kc = main[0]
        fm = frozenset(mods)
        # Sonderfälle mit eigener Linux-Entsprechung
        if fm == {"cmd"} and kc == 48:
            return {"keys": "alt+Tab"}, "adapted", ["⌘⇥ (Programm wechseln) → Alt+Tab"]
        if fm == {"ctrl", "cmd"} and kc == 12:
            return {"lock": True}, "adapted", ["⌃⌘Q (Sperren) → Bildschirm sperren"]
        if fm == {"shift", "cmd"} and kc in (20, 21, 23):  # ⇧⌘3 / ⇧⌘4 / ⇧⌘5
            mode = "screen" if kc == 20 else "area"
            what = "ganzer Bildschirm" if mode == "screen" else "Bereich"
            return {"screenshot": mode}, "adapted", [f"Mac-Bildschirmfoto {mac_label(set(mods), kc)} → GNOME ({what})"]
        if fm == {"opt", "cmd"} and kc == 53:
            return {"run": "gnome-system-monitor"}, "adapted", ["⌥⌘esc (Sofort beenden) → Systemüberwachung"]
        if (fm, kc) in MAC_DE_OPT_CHARS:
            ch = MAC_DE_OPT_CHARS[(fm, kc)]
            return {"type": ch.replace("{", "{{").replace("}", "}}")}, "adapted", [f"{mac_label(set(mods), kc)} erzeugte auf dem Mac „{ch}“ → tippt jetzt „{ch}“"]
        if "opt" in mods and not ({"cmd", "ctrl"} & mods) and kc not in (48, 36, 49, 51, 53, 117, 123, 124, 125, 126):
            return None, "manual", [f"{mac_label(set(mods), kc)} erzeugt auf dem Mac ein Sonderzeichen – bitte das Zeichen eintragen"]
    keys_out, notes, status = [], [], "ok"
    for kc in main:
        name = "Return" if kc == 76 else keys.name(kc)
        if not name:
            return None, "manual", [f"Taste {mac_label(set(), kc)} unbekannt"]
        parts = []
        for m in ("ctrl", "opt", "shift", "cmd"):
            if m in mods:
                parts.append({"ctrl": "ctrl", "opt": "alt", "shift": "shift", "cmd": "ctrl"}[m])
        parts = list(dict.fromkeys(parts))
        keys_out.append("+".join(parts + [name]))
    if "cmd" in mods:
        status = "adapted"
        notes.append("⌘ in gesendeten Kürzeln → Strg (z. B. ⌘V → Strg+V)")
    if "ctrl" in mods and "cmd" in mods:
        notes.append("⌃ und ⌘ werden beide zu Strg – bitte prüfen")
    return {"keys": " ".join(keys_out)}, status, notes


def _launch(path: str) -> tuple[dict | None, str, list[str]]:
    p = unquote(path.replace("file://", "")).rstrip("/")
    base = Path(p).name.lower()
    if base in APP_LAUNCH:
        kind, val = APP_LAUNCH[base]
        return {kind: val}, "adapted", [f"{Path(p).name} → {val}"]
    if p.lower().endswith(".app"):
        return None, "manual", [f"Mac-Programm „{Path(p).stem}“ – bitte Linux-Programm eintragen"]
    if p.startswith("/Users/"):
        return {"open": p}, "manual", [f"Datei lag auf dem Mac ({Path(p).name}) – Pfad anpassen"]
    return {"open": p}, "adapted", ["Pfad prüfen"]


def convert_action(t: dict, keys: KeyResolver) -> tuple[list[dict], str, list[str], str]:
    """Eine BTT-Aktion übersetzen -> (Aktionen, Status, Hinweise, Beschreibung)."""
    at = t.get("BTTPredefinedActionType", -1)
    name = t.get("BTTPredefinedActionName") or ""
    lname = name.lower()
    data = t.get("BTTAdditionalActionData") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}

    if at in (-1, None) or name == "No Action":
        if t.get("BTTShortcutToSend"):
            a, st, notes = _send_shortcut(t["BTTShortcutToSend"], keys)
            desc = "Sende " + send_label(t["BTTShortcutToSend"])
            return ([a] if a else []), st, notes, desc
        return [], "ok", [], ""

    if at == 129 or "delay" in lname:
        return [{"delay": float(t.get("BTTDelayNextActionBy") or 0.2)}], "ok", [], name

    # Text tippen / einfügen
    text = t.get("BTTStringToType")
    if text is None and data.get("BTTActionTextToPaste"):
        text = decode_attributed(data["BTTActionTextToPaste"])
        if text is None:
            return [], "manual", ["Text konnte nicht gelesen werden"], name
    if text is not None:
        conv, notes = convert_btt_text(text)
        st = "manual" if any("bitte" in n for n in notes) else "ok"
        left = t.get("BTTMoveCursorLeftBy")
        try:
            left = int(left)
        except (TypeError, ValueError):
            left = 0
        if left > 0:
            # Cursor-Marke setzen (Zählung ohne Platzhalter grob angenähert)
            conv = conv[:-left] + "{cursor}" + conv[-left:]
        if "=lorem(" in conv or "=rand(" in conv:
            notes.append("=lorem() ist eine Word-Funktion – in LibreOffice gibt es das nicht")
            st = "manual"
        return [{"type": conv}], st, notes, name

    if "chatgpt" in lname or "BTTChatGPTTransformerSystemPrompt" in data or "BTTChatGPTTransformerUserPrompt" in data:
        user = (data.get("BTTChatGPTTransformerUserPrompt") or "").strip()
        system = (data.get("BTTChatGPTTransformerSystemPrompt") or "").strip()
        instr = user or system or "Verbessere den Text."
        if user and system and "helpful assistant" not in system.lower():
            instr = system + "\n\n" + user
        return [{"ai": instr}], "adapted", ["ChatGPT → Claude über dein Abo (Claude-CLI nötig)"], name
    if "java script" in lname or "javascript" in lname:
        js = data.get("BTTClipboardTransformerJS", "")
        return [], "manual", ["JavaScript-Umwandlung gibt es nicht – lässt sich als Befehl oder mit Claude lösen"
                              + (f" (Code beginnt: {js.strip()[:60]!r})" if js else "")], name

    window_map = {19: "left_half", 20: "right_half", 21: "maximize", 106: "minimize_under_cursor",
                  112: "fullscreen", 48: "next_monitor_maximized"}
    if at in window_map:
        return [{"window": window_map[at]}], "ok", [], name
    if "linke hälfte" in lname or "window left" in lname:
        return [{"window": "left_half"}], "ok", [], name
    if "rechte hälfte" in lname or "window right" in lname:
        return [{"window": "right_half"}], "ok", [], name
    if at == 158 or "lock screen" in lname or "sperren" in lname:
        return [{"lock": True}], "ok", [], name
    if at == 115 or "launchpad" in lname:
        return [{"keys": "super+a"}], "adapted", ["Launchpad → GNOME-Programmübersicht (Super+A)"], name
    if at == 5 or "mission control" in lname:
        return [{"keys": "super"}], "adapted", ["Mission Control → GNOME-Aktivitäten (Super)"], name
    if at in (169, 263) or "screenshot" in lname:
        return [{"screenshot": "area"}], "adapted", ["Screenshot → GNOME-Bildschirmfoto (Bereich)"], name
    if at == 203 or "clipboard" in lname or "zwischenablage" in lname:
        return [{"clipboard": "show"}], "ok", [], name
    if at in (26, 27) or lname in ("nächstes", "vorheriges", "next", "previous"):
        k = "XF86AudioNext" if (at == 27 or lname in ("nächstes", "next")) else "XF86AudioPrev"
        return [{"keys": k}], "adapted", [f"„{name}“ als Medientaste übernommen – bitte prüfen"], name
    if re.search(r"desktop nach links|space left|move left a space", lname):
        return [{"workspace": "prev"}], "adapted", ["Mac-Desktop → GNOME-Arbeitsfläche"], name
    if re.search(r"desktop nach rechts|space right|move right a space", lname):
        return [{"workspace": "next"}], "adapted", ["Mac-Desktop → GNOME-Arbeitsfläche"], name
    if t.get("BTTLaunchPath"):
        a, st, notes = _launch(t["BTTLaunchPath"])
        return ([a] if a else []), st, notes, name
    url = t.get("BTTOpenURL")
    if url:
        if url.startswith("btt://"):
            return [], "manual", ["Ruft andere BTT-Aktionen auf – bitte direkt eintragen"], name
        return [{"open": url}], "ok", [], name
    script = t.get("BTTShellTaskActionScript") or t.get("BTTTerminalCommand")
    if script:
        return [{"run": script}], "adapted", ["macOS-Befehl – bitte prüfen, ob er unter Linux läuft"], name
    if t.get("BTTInlineAppleScript") or t.get("BTTAppleScriptString") or "apple script" in lname:
        return [], "manual", ["AppleScript gibt es unter Linux nicht"], name
    return [], "manual", [f"Aktion „{name}“ hat noch keine Entsprechung"], name


UNSUPPORTED_CLASSES = {
    "BTTTriggerTypeTouchpadAll": "Diese Touchpad-Geste kennt Linux nicht (nur Wischen/Zusammenziehen mit 3–5 Fingern)",
    "BTTTriggerTypeMagicMouse": "Magic-Mouse-Geste (Maus übernimmt OpenLogi)",
    "BTTTriggerTypeNormalMouse": "Maustaste (übernimmt OpenLogi)",
    "BTTTriggerTypeTouchBar": "Touch Bar gibt es nicht",
    "BTTTriggerTypeBTTRemote": "BTT-Remote-App gibt es nicht",
    "BTTTriggerTypeSiriRemote": "Siri Remote gibt es nicht",
    "BTTTriggerTypeNotchBar": "Notch-Leiste gibt es nicht",
    "BTTTriggerTypeMenubar": "Menüleisten-Eintrag (später eventuell als Tray-Menü)",
    "BTTTriggerTypeDrawing": "Zeichengeste",
    "BTTTriggerTypeOtherTriggers": "Sonder-Auslöser (z. B. benannte Trigger)",
    "BTTTriggerTypeFloatingMenu": "Schwebendes Menü",
}


# Bekannte BTT-Gesten-IDs, falls die Beschreibung fehlt (Rest wird aus dem Text gelesen)
BTT_GESTURE_IDS = {107: "swipe down 4", 128: "swipe left 5"}
_BTT_DIR_WORDS = [("up", ("up", "oben", "hoch")), ("down", ("down", "unten", "runter")),
                  ("left", ("left", "links")), ("right", ("right", "rechts")),
                  ("in", ("pinch in", "zusammen", "pinch-in")), ("out", ("pinch out", "auseinander", "pinch-out"))]


def btt_gesture(t: dict) -> str | None:
    """BTT-Touchpad-Auslöser → Tastenwerk-Geste („swipe down 4“), wenn übertragbar."""
    desc = str(t.get("BTTTriggerTypeDescription") or "").lower()
    if desc:
        if any(w in desc for w in ("click", "klick", "tap", "force", "tip", "rotate", "dreh", "corner", "ecke",
                                   "edge", "rand", "double", "doppel", "move", "beweg", "drag", "zieh ")):
            return None
        m = re.search(r"(\d)\s*finger", desc)
        fingers = int(m.group(1)) if m else None
        kind = "pinch" if ("pinch" in desc or "zusammen" in desc or "auseinander" in desc) else \
            "swipe" if ("swipe" in desc or "wisch" in desc) else None
        direction = None
        for d, words in _BTT_DIR_WORDS:
            if any(w in desc for w in words):
                direction = d
                if d in ("in", "out"):
                    kind = "pinch"
                break
        if kind == "pinch" and direction not in ("in", "out"):
            direction = "out" if "out" in desc or "auseinander" in desc else "in"
        if fingers in (3, 4, 5) and kind and direction:
            return f"{kind} {direction} {fingers}"
        return None
    return BTT_GESTURE_IDS.get(t.get("BTTTriggerType"))


def _uid(t: dict, app: str) -> str:
    if t.get("BTTUUID"):
        return "btt:" + t["BTTUUID"]
    h = hashlib.sha1((app + json.dumps(t, sort_keys=True, default=str)).encode()).hexdigest()[:16]
    return "btt:h" + h


def load_preset(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def candidates(preset: dict, keys: KeyResolver) -> list[Candidate]:
    out = []
    for app in preset.get("BTTPresetContent", []) or []:
        app_name = app.get("BTTAppName") or "?"
        bundle = app.get("BTTAppBundleIdentifier") or ""
        for t in app.get("BTTTriggers", []) or []:
            out += _convert_trigger(t, app_name, bundle, keys)
    return out


def _convert_trigger(t: dict, app_name: str, bundle: str, keys: KeyResolver) -> list[Candidate]:
    cls = t.get("BTTTriggerClass", "")
    enabled = t.get("BTTEnabled", 1) not in (0, False) and t.get("BTTEnabled2", 1) not in (0, False)
    c = Candidate(source=_uid(t, app_name), app_name=app_name, bundle=bundle, kind="other",
                  mac_trigger="", btt_enabled=enabled)
    if cls == "BTTTriggerTypeKeyboardShortcut":
        kc = t.get("BTTShortcutKeyCode")
        mods = flags_to_mods(t.get("BTTShortcutModifierKeys"))
        c.kind = "hotkey"
        c.mac_trigger = mac_label(mods, kc)
        if kc is None or kc < 0:
            c.worsen("unsupported", "Auslöser nur aus Modifikator-Tasten")
        else:
            c.hotkey_mac = (frozenset(mods), kc)
    elif cls == "BTTTriggerTypeKeySequence":
        seq = t.get("BTTKeySequence") or {}
        chars = [k for k in (seq.get("BTTKeySequenceDownKeys") or []) if k.get("BTTKEYCode") not in MAC_MOD_KEYS]
        text = "".join((k.get("BTTKEYCharacter") or "").strip() for k in chars)
        c.kind = "abbr"
        c.mac_trigger = f"„{text}“"
        if not text:
            c.worsen("manual", "Tastenfolge konnte nicht gelesen werden")
        c.abbreviation = text.lower()
        dele = seq.get("BTTCharactersToDeleteAfterwards")
        if dele == 0:
            c.notes.append("In BTT blieb die Tastenfolge stehen – Tastenwerk löscht sie")
        elif dele is not None and int(dele) != len(text):
            c.notes.append(f"BTT löschte {dele} Zeichen, Tastenwerk löscht die {len(text)} getippten")
    elif cls == "BTTTriggerTypeTouchpadAll" and btt_gesture(t):
        from ..gestures import Gesture
        c.kind = "gesture"
        c.gesture = btt_gesture(t)
        c.mac_trigger = t.get("BTTTriggerTypeDescription") or Gesture.parse(c.gesture).label
    else:
        desc = t.get("BTTTriggerTypeDescription") or cls.replace("BTTTriggerType", "")
        c.mac_trigger = desc
        c.worsen("unsupported", UNSUPPORTED_CLASSES.get(cls, f"Auslöser-Art „{desc}“ gibt es nicht"))
        acts, _, _, adesc = convert_action(t, keys)
        c.action_text = adesc or "–"
        c.name = f"{desc}"
        return [c]

    steps = [t] + sorted(t.get("BTTAdditionalActions") or [], key=lambda a: a.get("BTTOrder", 0))
    descs = []
    for step in steps:
        acts, st, notes, desc = convert_action(step, keys)
        if desc:
            descs.append(desc)
        c.actions += acts
        if st != "ok" or notes:
            c.worsen(st)
            for n in notes:
                if n not in c.notes:
                    c.notes.append(n)
    # Aufeinanderfolgende Tasten-Aktionen zusammenfassen
    merged: list[dict] = []
    for a in c.actions:
        if merged and set(a) == {"keys"} and set(merged[-1]) == {"keys"}:
            merged[-1] = {"keys": merged[-1]["keys"] + " " + a["keys"]}
        else:
            merged.append(a)
    c.actions = merged
    if c.kind == "abbr" and any("ai" in a for a in c.actions):
        for a in c.actions:
            if "ai" in a:
                a["input"] = "line"
        c.worsen("adapted", "Nimmt den Text der aktuellen Zeile vor der Tastenfolge – passt das zu deinem Ablauf?")
    c.action_text = " → ".join(descs) or "–"
    if not c.actions and c.status != "unsupported":
        c.worsen("manual", "Keine übertragbare Aktion")
    if not enabled:
        c.notes.append("War in BTT ausgeschaltet")
    # Eigener Name aus BTT, sonst später aus der Aktion erzeugt
    label = t.get("BTTTriggerName") or t.get("BTTGestureNotes")
    c.name = label.strip()[:80] if isinstance(label, str) and label.strip() else ""
    return [c]
