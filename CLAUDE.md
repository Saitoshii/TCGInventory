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
  → für Karten-Anreicherung **immer** die lokale Datenbank nutzen, **niemals**
  pro Karte die Scryfall-API abfragen.
  Die Bulkdatei selbst darf periodisch von Scryfall geladen werden (Seite
  „Kartendaten"): sie wird streamend verarbeitet, die Rohdatei wird nicht
  gespeichert, und die Datenbank wird erst am Ende atomar getauscht.
  Die rohe Bulk-JSON nie vollständig in den Speicher laden — das sprengt den Pi.
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

## Zwei Wege zu einer Bestellung
Neben der Cardmarket-Mail gibt es den **Direktverkauf** (Flohmarkt, von Hand
zu Hand): `direktverkauf.py` legt ihn in **denselben** Tabellen an, nur mit
`quelle = 'manuell'` und einem `verkaufskanal`. Kein zweiter Bestellweg —
Beileger, API und Buchhaltung laufen unverändert weiter. Details:
`docs/ORDERS.md`, Abschnitt 6.

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
- Zustände kommen als Langform (`NearMint`) und werden auf die
  Cardmarket-Codes (`NM`) normalisiert — der Bestand und der Zustandsfilter
  arbeiten mit den Codes. Skalen ohne 1:1-Zuordnung nicht umdeuten.

## Weiteres CSV-Layout (Scan-App-Listenexport)
Neben Dragonshield wird der längere Listenexport von Scan-Apps gelesen
(`List Type, List Name, …, Rarity, …, Current Price (<quelle>), …`). Beide
Layouts benennen die relevanten Spalten gleich und teilen sich **einen**
Codepfad (`dragonshield.py`) — kein zweiter Parser, keine Layout-Erkennung.
Zusätzlich übernommen werden `rarity`, `date_bought` (Kaufdatum, getrennt von
`date_added`) und `market_price` (Marktpreis beim Export, getrennt vom
Einkaufspreis `price`). Details: `docs/IMPORT.md`.

## Repo-Konventionen
- Tests liegen in `tests/` (pytest). Eine `test.py` im Root ist Altlast.
- Dokumentation gehört nach `docs/`. Mehrere überlappende Root-Markdown-Dateien
  werden zu einer `README.md` (Überblick) + `docs/` konsolidiert.
