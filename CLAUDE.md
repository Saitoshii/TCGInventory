# TCGInventory — Kontext für Claude Code

Dieses Dokument gibt den festen Rahmen für alle Änderungen vor. Bitte bei
jeder Aufgabe beachten und nicht ohne ausdrücklichen Auftrag davon abweichen.

## Was das ist
Flask-Inventarsystem für ein Cardmarket-TCG-Geschäft (Magic: The Gathering).
Server-rendered (Jinja2 + Bootstrap 5 via CDN), SQLite. Betrieb im DACH-Raum.

## Stack & Randbedingungen
- Python 3.11+, Flask, SQLite (`data/*.db`), Jinja2, Bootstrap 5.3 (CDN).
- **Läuft auf einem Raspberry Pi (4 GB).** Deshalb: kein schwergewichtiger
  JS-Build-Step, kein SPA-Rewrite, kein Node-Toolchain (kein Tailwind-Build).
  Server-rendered bleibt. Styling über eine schlanke **eigene CSS-Schicht auf
  Bootstrap**.
- Scryfall-Bulkdaten liegen lokal vor (`default-cards.db` / `build_card_db.py`)
  → für Karten-Anreicherung nutzen, nicht online abfragen.
- **Kein Cardmarket-API-Zugang** derzeit. Bestellungen kommen per Gmail-Mail
  (`email_parser.py`, `gmail_auth.py`).
- Labeldrucker **Niimbot B1 (50×30 mm)** wird später via USB am Pi angebunden.

## Kern-Datenmodell (Tabelle `cards`)
Eine Karte wird eindeutig identifiziert über die Kombination:
`set_code` + `collector_number` + `language` + `foil`.
Anreicherung via Scryfall liefert zusätzlich `scryfall_id` und `cardmarket_id`
(für die spätere API-Nutzung). `storage_code` = physischer Platz (mehrere Karten
pro Platz erlaubt), `folder_id` = Ordner.

## Durchgehender Identitäts-Pfad (NICHT brechen)
```
Dragonshield-CSV
  → Scryfall-Anreicherung (kanonische IDs: scryfall_id, cardmarket_id)
  → Inventarzeile mit Platz (storage_code)
  → Bestell-Mail parsen
  → Match auf (name + set_code + language [+ foil])
  → Platz anzeigen
  → "verkauft" entfernt exakt diese Zeile
```

## Prinzipien (wichtig)
1. **Nie blind raten.** Bei mehrdeutigem Karten-Match oder geparster
   Käuferadresse: die Kandidaten bzw. Werte zur Bestätigung anzeigen, niemals
   still den ersten Treffer nehmen (kein `LIMIT 1` als stille Entscheidung).
2. **Strukturierte Daten behalten**, nicht wegwerfen (Set, Foil, Sprache,
   Zustand). Diese Felder sind die Grundlage für zuverlässiges Matching.
3. **Kleine, reviewbare Änderungen.** Bestehende Tests bleiben grün. Keine
   großflächigen Rewrites ohne ausdrücklichen Auftrag. Gelöschte Inhalte
   auflisten.
4. **UI-Sprache: Deutsch** (Annahme – bei Bedarf zentral änderbar halten).

## Dragonshield-CSV-Format
- Erste Zeile `sep=,` überspringen.
- Spalten: `Folder Name, Quantity, Trade Quantity, Card Name, Set Code,
  Set Name, Card Number, Condition, Printing, Language, Price Bought,
  Date Bought, LOW, MID, MARKET`.
- `Printing` ist `Normal`/`Foil` → daraus das Foil-Flag ableiten
  (kein separates manuelles Angeben mehr).
- **Kartennamen können Kommas enthalten** (z. B. `Ezio, Brash Novice`) →
  zwingend einen echten CSV-Parser (`csv`-Modul) verwenden.
- Set-Codes können sich in Groß-/Kleinschreibung von Scryfall unterscheiden
  (Dragonshield `ACR` vs. Scryfall `acr`) → beim Nachschlagen normalisieren;
  unauflösbare Fälle in eine „Needs-Review"-Liste, nicht raten.

## Repo-Konventionen
- Tests liegen in `tests/` (pytest). Eine `test.py` im Root ist Altlast.
- Dokumentation gehört nach `docs/`. Mehrere überlappende Root-Markdown-Dateien
  werden zu einer `README.md` (Überblick) + `docs/` konsolidiert.
