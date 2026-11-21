"""Tastenkombinationen lesen, normalisieren und für xdotool aufbereiten.

Eine Kombination wird in der Konfiguration so geschrieben: ``ctrl+alt+left``,
``super+shift+v``, ``f13``.  Mehrere Tasten nacheinander werden mit Leerzeichen
getrennt: ``tab tab ctrl+v``.
"""

from __future__ import annotations

from Xlib import X, XK

# Multimedia-/Sondertasten (XF86…) und weitere Gruppen nachladen
for _grp in ("xf86", "latin2", "latin3", "latin4", "greek", "technical", "special", "publishing", "currency"):
    try:
        XK.load_keysym_group(_grp)
    except (ImportError, AttributeError):
        pass


def string_to_keysym(name: str) -> int:
    """Wie XK.string_to_keysym, versteht aber auch die X11-Schreibweise „XF86AudioNext“."""
    ks = XK.string_to_keysym(name)
    if not ks and name.startswith("XF86") and not name.startswith("XF86_"):
        ks = XK.string_to_keysym("XF86_" + name[4:])
    if not ks and len(name) > 1 and name[0] == "U":
        try:
            ks = 0x01000000 + int(name[1:], 16)
        except ValueError:
            pass
    return ks

# Modifikator-Namen (inkl. deutscher Varianten) -> kanonischer Name
MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "strg": "ctrl", "ctl": "ctrl",
    "alt": "alt", "opt": "alt", "option": "alt", "mod1": "alt",
    "shift": "shift", "umschalt": "shift",
    "super": "super", "win": "super", "windows": "super", "meta": "super", "mod4": "super",
    "altgr": "altgr", "mod5": "altgr",
}
MOD_ORDER = ["ctrl", "alt", "shift", "super", "altgr"]
MOD_MASKS = {
    "ctrl": X.ControlMask,
    "alt": X.Mod1Mask,
    "shift": X.ShiftMask,
    "super": X.Mod4Mask,
    "altgr": X.Mod5Mask,
}
RELEVANT_MASK = X.ControlMask | X.Mod1Mask | X.ShiftMask | X.Mod4Mask | X.Mod5Mask
# Für xdotool
MOD_XDOTOOL = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "super": "super", "altgr": "ISO_Level3_Shift"}

# Tasten-Namen -> X11-Keysym-Name
KEY_ALIASES = {
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "links": "Left", "rechts": "Right", "hoch": "Up", "runter": "Down",
    "enter": "Return", "return": "Return", "eingabe": "Return",
    "esc": "Escape", "escape": "Escape",
    "del": "Delete", "delete": "Delete", "entf": "Delete",
    "backspace": "BackSpace", "bksp": "BackSpace", "rücktaste": "BackSpace",
    "space": "space", "leertaste": "space", "leer": "space",
    "tab": "Tab", "tabulator": "Tab",
    "pgup": "Prior", "pageup": "Prior", "bildauf": "Prior", "prior": "Prior",
    "pgdown": "Next", "pagedown": "Next", "pgdn": "Next", "bildab": "Next",
    "home": "Home", "pos1": "Home", "end": "End", "ende": "End",
    "ins": "Insert", "insert": "Insert", "einfg": "Insert",
    "print": "Print", "druck": "Print", "menu": "Menu",
    "capslock": "Caps_Lock",
    "#": "numbersign", "<": "less", ">": "greater", "-": "minus", "+": "plus",
    ".": "period", ",": "comma", ";": "semicolon", ":": "colon", "/": "slash",
    "\\": "backslash", "^": "dead_circumflex", "´": "dead_acute", "`": "dead_grave",
    "ß": "ssharp", "ä": "adiaeresis", "ö": "odiaeresis", "ü": "udiaeresis",
    "'": "apostrophe", "=": "equal", "*": "asterisk", "?": "question", "!": "exclam",
    "[": "bracketleft", "]": "bracketright", "{": "braceleft", "}": "braceright",
    "|": "bar", "@": "at", "~": "asciitilde", "€": "EuroSign", "§": "section",
    "kp_enter": "KP_Enter",
}


class KeyError_(ValueError):
    pass


_NAME_BY_KEYSYM: dict[int, str] = {}


def keysym_to_name(ks: int) -> str | None:
    """Keysym-Nummer -> Keysym-Name (z. B. 0xff09 -> "Tab", 0x23 -> "numbersign")."""
    if not _NAME_BY_KEYSYM:
        from Xlib import keysymdef
        for mod in keysymdef.__all__:
            XK.load_keysym_group(mod)
        for attr in dir(XK):
            if attr.startswith("XK_"):
                val = getattr(XK, attr)
                if isinstance(val, int):
                    # Kleinbuchstaben und erste Namen bevorzugen
                    _NAME_BY_KEYSYM.setdefault(val, attr[3:])
    if 0x41 <= ks <= 0x5A:
        ks += 0x20
    name = _NAME_BY_KEYSYM.get(ks)
    if name and name.startswith("XF86_"):
        name = "XF86" + name[5:]
    if name is None and ks & 0xFF000000 == 0x01000000:
        name = "U%04X" % (ks & 0xFFFFFF)
    return name


def keysym_name(name: str) -> str:
    """Tasten-Namen in einen gültigen X11-Keysym-Namen verwandeln."""
    raw = name.strip()
    low = raw.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    if raw in KEY_ALIASES:
        return KEY_ALIASES[raw]
    if len(low) >= 2 and low[0] == "f" and low[1:].isdigit():
        return "F" + low[1:]
    if low.startswith("kp_") or low.startswith("kp"):
        rest = raw[3:] if low.startswith("kp_") else raw[2:]
        cand = "KP_" + (rest.capitalize() if not rest.isdigit() else rest)
        if string_to_keysym(cand):
            return cand
    if low.startswith("xf86"):
        for cand in (raw, "XF86" + raw[4:]):
            if string_to_keysym(cand):
                return "XF86" + cand[4:].lstrip("_")
    for cand in (raw, low, raw.capitalize(), raw.upper()):
        if string_to_keysym(cand):
            return cand if len(cand) > 1 else cand.lower()
    if len(raw) == 1:
        cp = ord(raw.lower())
        if cp < 0x100:
            return keysym_to_name(cp) or raw
        return "U%04X" % cp
    raise KeyError_(f"Unbekannte Taste: {name!r}")


class Combo:
    """Eine Tastenkombination wie ctrl+alt+left."""

    __slots__ = ("mods", "key")

    def __init__(self, mods: frozenset[str], key: str):
        self.mods = mods
        self.key = key

    @classmethod
    def parse(cls, text: str) -> "Combo":
        text = text.strip()
        if not text:
            raise KeyError_("Leere Tastenkombination")
        # "ctrl++" erlauben (Plus-Taste)
        parts = text.split("+")
        if text.endswith("++"):
            parts = parts[:-2] + ["+"]
        parts = [p for p in parts if p != ""] or ["+"]
        *mod_parts, key_part = parts
        mods = set()
        for m in mod_parts:
            canon = MOD_ALIASES.get(m.strip().lower())
            if not canon:
                raise KeyError_(f"Unbekannter Modifikator {m!r} in {text!r}")
            mods.add(canon)
        # Ein einzelner Modifikator als Taste (z. B. "super") ist erlaubt
        if key_part.strip().lower() in MOD_ALIASES and not mod_parts:
            canon = MOD_ALIASES[key_part.strip().lower()]
            key = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L",
                   "super": "Super_L", "altgr": "ISO_Level3_Shift"}[canon]
            return cls(frozenset(), key)
        return cls(frozenset(mods), keysym_name(key_part))

    @property
    def mask(self) -> int:
        m = 0
        for mod in self.mods:
            m |= MOD_MASKS[mod]
        return m

    def __str__(self) -> str:
        mods = [m for m in MOD_ORDER if m in self.mods]
        return "+".join(mods + [self.key])

    def xdotool(self) -> str:
        mods = [MOD_XDOTOOL[m] for m in MOD_ORDER if m in self.mods]
        return "+".join(mods + [self.key])

    def __eq__(self, other):
        return isinstance(other, Combo) and self.mods == other.mods and self.key.lower() == other.key.lower()

    def __hash__(self):
        return hash((self.mods, self.key.lower()))

    def __repr__(self):
        return f"Combo({self})"


def parse_sequence(text: str) -> list[Combo]:
    """``"tab tab ctrl+v"`` -> drei Kombinationen."""
    return [Combo.parse(p) for p in text.split()]


def mods_from_state(state: int) -> frozenset[str]:
    return frozenset(name for name, mask in MOD_MASKS.items() if state & mask)


def keysym_to_char(ks: int) -> str | None:
    """Keysym in ein druckbares Zeichen umwandeln (oder None)."""
    if 0x20 <= ks <= 0x7E or 0xA0 <= ks <= 0xFF:
        return chr(ks)
    if ks & 0xFF000000 == 0x01000000:
        return chr(ks & 0x00FFFFFF)
    if ks == 0x20AC:  # EuroSign
        return "€"
    if 0xFFB0 <= ks <= 0xFFB9:  # KP_0..KP_9
        return chr(ord("0") + ks - 0xFFB0)
    return None
