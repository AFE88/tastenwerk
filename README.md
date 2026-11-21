# Tastenwerk

Tastenkürzel, getippte Tastenfolgen („##mail“), Touchpad-Gesten, Text-Bausteine, Makros und
Fenster-Aktionen für Linux mit X11. Gedacht als Ersatz für BetterTouchTool auf Zorin OS / GNOME. Alles steckt in einem
Programm und einer Konfigurationsdatei.

Die Maus bleibt bei **OpenLogi**. Wenn eine Maustaste dort ein Tastenkürzel sendet, kann
Tastenwerk darauf reagieren. So landen auch Makros auf der Maus.

## Schnellstart

**Tastenwerk** im Programm-Menü öffnen, oder:

```bash
~/LinuxShortcutsTool/bin/tastenwerk
```

In der Oberfläche legst du Regeln an, nimmst Kürzel per Tastendruck auf, importierst
BetterTouchTool-Presets und startest den Hintergrunddienst (Schalter unten links). In den
Einstellungen lässt sich der Autostart einschalten. Ist die Oberfläche geschlossen, reagiert
der Dienst trotzdem weiter.

Falls der Menü-Eintrag fehlt: `bin/tastenwerk install`.

Die Konfiguration liegt in `~/.config/tastenwerk/` und lässt sich auch von Hand bearbeiten.
Änderungen greifen sofort.

| Befehl | Zweck |
|---|---|
| `tastenwerk` / `tastenwerk gui` | Oberfläche öffnen |
| `tastenwerk run` | Hintergrunddienst im Terminal starten |
| `tastenwerk check` | Konfiguration prüfen, auch auf Überschneidungen mit GNOME-Kürzeln |
| `tastenwerk list [--all]` | alle Regeln anzeigen |
| `tastenwerk apps [--wait 3]` | Programmnamen für `apps:` herausfinden |
| `tastenwerk import DATEI…` | BetterTouchTool-Presets übernehmen (Assistent) |
| `tastenwerk reload` / `stop` | laufendes Tastenwerk neu laden / beenden |
| `tastenwerk run --debug` | mit ausführlichem Protokoll starten |

## Regeln

```yaml
rules:
  - name: Fenster links
    hotkey: ctrl+super+left
    do: [{window: left_half}]

  - name: Gruß
    abbreviation: "##mfg"
    do:
      - type: |-
          Viele Grüße
          {cursor}

  - name: Nur in VS Code
    abbreviation: "##api"
    apps: [code]
    do: [{type: "function {cursor}() {{ }}"}]
```

Alle Aktionen und Platzhalter sind in `config.example.yaml` beschrieben. Kurzfassung:
`keys`, `type`, `paste`, `window`, `workspace`, `open`, `run`, `delay`, `lock`, `notify`, `ai`, `clipboard`.

## BetterTouchTool-Import

```bash
bin/tastenwerk import ~/Downloads/*.bttpreset            # Assistent
bin/tastenwerk import ~/Downloads/*.bttpreset --analyse  # nur anschauen, nichts speichern
```

Der Assistent:

1. ordnet jeden BTT-Auslöser ein: ✅ direkt · 🟡 angepasst · 🛠 Handarbeit · ⛔ nicht übertragbar
   (Gesten und Touch Bar landen nur im Bericht),
2. fragt einmal, welche Linux-Taste ⌘ ersetzt, und welche Linux-Programme deine Mac-Programme ersetzen,
3. zeigt jede Regel mit Hinweisen und prüft Überschneidungen mit GNOME-Kürzeln, deinen eigenen Regeln
   und früheren Importen,
4. schreibt nach `~/.config/tastenwerk/imported/btt-<preset>.yaml` und erstellt einen Bericht.

Bei einem **erneuten Import** (z. B. mit neueren Presets) werden unveränderte Regeln still
übersprungen. Was du früher bewusst weggelassen hast, wird nicht noch einmal gefragt.
Geänderte Regeln bietet der Assistent zum Aktualisieren an. Deine eigene `config.yaml` bleibt
dabei unangetastet.

Was der Import erkennt: Tastenkürzel, Tastenfolgen, Touchpad-Wischgesten (3–5 Finger), Text-Bausteine (auch mehrzeilig, mit
Emojis), Datums-Platzhalter, gesendete Tastenkombinationen (⌘→Strg, Mac-Sonderzeichen wie ⌥7 = |),
Fenster-Aktionen, Sperren, Screenshot, Launchpad/Mission Control und ChatGPT-Umformungen (werden
zu Claude).

## Zwischenablage-Verlauf

Tastenwerk merkt sich, was du kopierst (Text und Bilder), und zeigt es auf Knopfdruck mittig über
dem aktiven Fenster an. Standard ist **Super+Shift+V**, wie ⌘⇧V in BTT.

| Taste | Wirkung |
|---|---|
| tippen | sofort suchen (mehrere Wörter = alle müssen vorkommen) |
| ↑ ↓ / ↵ | auswählen / einfügen |
| Strg+1 … 9 | Eintrag 1–9 direkt einfügen |
| ⇧↵ oder Strg+C | nur in die Zwischenablage legen |
| Strg+P | anheften (bleibt immer, steht oben) |
| Entf | Eintrag löschen |
| Tab | nur Angeheftete zeigen |
| Esc | Suche leeren / schließen |

Ohne Fenster geht es mit der Aktion `clipboard: paste 2`: Sie fügt die vorletzte Kopie direkt ein
(1 = zuletzt kopiert, Angeheftete zählen nicht mit). Weil eingefügte Einträge wieder nach oben
rücken, wechselt Nr. 2 bei jedem Druck zwischen den letzten beiden Kopien.

Im Terminal wird automatisch mit Strg+Shift+V eingefügt. Kopien aus Passwort-Managern (Bitwarden,
KeePassXC …) und als Passwort markierte Inhalte werden nie gespeichert. Ebenso wenig, was
Tastenwerk selbst kurz in die Zwischenablage legt. Der Verlauf liegt nur lokal in
`~/.local/share/tastenwerk/clipboard/` (nur für dich lesbar). Einstellungen: Oberfläche →
Einstellungen → Zwischenablage.

## Touchpad-Gesten

```yaml
  - name: Sperren
    gesture: swipe down 4      # swipe up/down/left/right · pinch in/out · 3–5 Finger
    do: [{lock: true}]
```

In der Oberfläche: Auslöser **Geste** wählen, auf „Geste aufnehmen“ klicken und einfach auf dem
Touchpad wischen, oder über ⋯ von Hand wählen. Gesten lassen sich wie Kürzel auf einzelne
Programme beschränken.

Technisch hängt sich Tastenwerk an den **Touchégg**-Dienst, den Zorin mitbringt. Das braucht keine
Root-Rechte und kein Zusatzprogramm. Belegt sind von Haus aus: 3 Finger wischen (GNOME:
Arbeitsflächen/Übersicht) und 4 Finger zusammen-/auseinanderziehen (Touchégg). Die Oberfläche
und `tastenwerk check` warnen, wenn eine Regel eine davon doppelt belegt. **4 und 5 Finger wischen
sind frei.** Klick- und TipTap-Gesten aus BTT gibt es unter Linux nicht.

## Text mit Claude umformen

`ai:` schickt markierten Text (oder mit `input: line` die Zeile vor dem Cursor) an Claude und
ersetzt ihn durch die Antwort. Das läuft über das **Claude-Code-CLI mit deinem Abo**, es ist kein
API-Schlüssel nötig. Einmalig:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude auth login
```

Ohne CLI funktionieren alle anderen Funktionen normal.

## Gut zu wissen

- **Nur X11.** Unter Wayland können Programme keine globalen Kürzel abfangen. Zorin startet mit X11,
  wenn beim Anmelden „Zorin Desktop auf Xorg“ gewählt ist.
- **Belegte Kürzel:** Hat GNOME oder die Zorin-Kachel-Erweiterung ein Kürzel schon belegt (z. B. Super+←),
  kann Tastenwerk es nicht nutzen. `tastenwerk check` zeigt solche Fälle an. Entweder ein anderes
  Kürzel wählen oder es in den GNOME-Einstellungen freigeben.
- **Tastenfolgen** lösen sofort aus, sobald die Folge getippt ist. Ein Mausklick, Pfeiltasten oder
  Strg-Kombinationen setzen den Tippspeicher zurück. `--debug` protokolliert keine getippten Zeichen.

## Aufbau

```
tastenwerk/
  gui/           Oberfläche (GTK 4 + libadwaita): app.py, editor.py, importdlg.py, prefs.py, widgets.py
  store.py       Regeln lesen/schreiben für die Oberfläche
  control.py     Dienst starten/stoppen/pausieren, Autostart, Menü-Eintrag
  daemon.py      Hintergrunddienst, lädt die Konfiguration bei Änderungen neu
  hotkeys.py     globale Kürzel (XGrabKey), Durchreichen an Programme
  gestures.py    Touchpad-Gesten (über den Touchégg-Dienst)
  clipboard.py   Zwischenablage-Verlauf und sein Fenster
  abbrev.py      Tastenfolgen (XRecord)
  actions.py     alle Aktionen (xdotool, Zwischenablage, Befehle, Claude)
  windows.py     Fenster andocken / Monitore (EWMH)
  config.py      Konfiguration, Includes, Prüfung
  gnome.py       GNOME-Kürzel auslesen (Konflikte)
  importer/      BTT-Import (btt.py = Übersetzung, wizard.py = Assistent)
```

Benötigt nur Pakete, die unter Zorin schon installiert sind: `python3-xlib`, `python3-yaml`,
`python3-gi` (GTK 4, libadwaita), `xdotool`.

## Ideen für später

Tray-Symbol mit Menü · Tastenfolgen erst nach
Leerzeichen auslösen (optional) · Fenster auf andere Arbeitsfläche schieben
