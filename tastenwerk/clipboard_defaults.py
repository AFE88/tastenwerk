"""Standardwerte des Zwischenablage-Verlaufs (ohne GTK, für Dienst und Oberfläche)."""

DEFAULTS = {
    "history": True,          # Verlauf mitschreiben
    "max_items": 300,         # angeheftete zählen nicht mit
    "keep_days": 14,          # 0 = unbegrenzt; veraltet, keep_hours hat Vorrang
    "clear_scope": "never",   # Zwischenablage leeren: never | secrets (nur Passwort-Kopien) | all
    "clear_after": 60,        # … nach so vielen Sekunden ohne neue Kopie
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


CLEAR_SCOPES = {"never": "Nie", "secrets": "Nur Passwörter", "all": "Alles"}


def keep_hours(cfg: dict) -> float:
    """Aufbewahrungszeit in Stunden (0 = unbegrenzt). keep_hours hat Vorrang vor dem älteren keep_days."""
    if cfg.get("keep_hours") is not None:
        return float(cfg["keep_hours"])
    return float(cfg.get("keep_days") or 0) * 24
