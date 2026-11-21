"""Standardwerte des Zwischenablage-Verlaufs (ohne GTK, für Dienst und Oberfläche)."""

DEFAULTS = {
    "history": True,          # Verlauf mitschreiben
    "max_items": 300,         # angeheftete zählen nicht mit
    "keep_days": 14,          # 0 = unbegrenzt (BTT-Standard: 14)
    "images": True,
    "move_to_top": True,      # Eingefügtes rückt nach oben
    "exclude_apps": ["bitwarden", "keepassxc", "1password", "seahorse"],
}


MAX_N = 20


def command(val) -> tuple[str, int] | None:
    """'show' | 'clear' | 'paste N' → ('show', 0) / ('clear', 0) / ('paste', N); sonst None."""
    parts = str(val).strip().lower().split()
    if parts in (["show"], ["clear"]):
        return parts[0], 0
    if len(parts) == 2 and parts[0] == "paste" and parts[1].isdigit() and 1 <= int(parts[1]) <= MAX_N:
        return "paste", int(parts[1])
    return None
