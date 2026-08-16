# Offene Bestellungen (Open Orders) — Implementation Notes

This document describes how the "Offene Bestellungen" (Open Orders) feature is
implemented. It consolidates the former `IMPLEMENTATION_SUMMARY.md` root file.
For end-user Gmail setup see [GMAIL_SETUP.md](GMAIL_SETUP.md).

## 1. Card matching with `default-cards.db`

**File:** `order_service.py`

- `_find_card_info()` searches both the user's inventory **and**
  `default-cards.db`.
- `_get_image_from_default_db()` fetches images from the Scryfall-derived
  database.
- Matching logic:
  1. Check the user's inventory for an exact match.
  2. If found but no image, fall back to `default-cards.db` for the image.
  3. If not found in inventory, try a partial match.
  4. Finally, attempt to get an image from `default-cards.db` even if the card
     is not in inventory.

**Benefit:** card images display even for cards not yet in the user's inventory,
as long as they exist in `default-cards.db`.

## 2. Order deletion with CASCADE

**File:** `web.py`

- Route `/orders/<int:order_id>/delete` (POST).
- Enables `PRAGMA foreign_keys = ON` so CASCADE deletion works.
- Deletes an order and all associated items in one atomic operation.
- Flash message confirms deletion; redirects back to the orders list.

**File:** `templates/orders.html`

- Delete button (`×`) next to each order header.
- Inline form with a JavaScript `confirm()` dialog.
- German confirmation message: "Diese Bestellung wirklich löschen? Dies kann
  nicht rückgängig gemacht werden."
- Styled with Bootstrap `btn-sm btn-danger`.

**File:** `setup_db.py`

- `order_items` already has an `ON DELETE CASCADE` foreign key.
- The `email_date` column already exists and is migrated.

## 3. Email date display

- **`web.py`:** the query uses `COALESCE(o.email_date, o.date_received)` to
  prefer the email date, falling back to `date_received` when `email_date` is
  NULL. Orders are sorted by email date (newest first).
- **`order_service.py`:** `_save_order()` stores both `date_received` and
  `email_date`, using the email date parsed from the message when available.

## 4. Buyer name parsing

**File:** `email_parser.py`

- Extracts the buyer name from the email subject line
  (e.g. "für KohlkopfKlaus: Bitte versenden").
- Falls back to body patterns (e.g. "KohlkopfKlaus hat Bestellung").
- Multiple pattern matches for German and English formats.
- Ignores email signatures such as "Das Cardmarket-Team".
- Uses "Unknown Buyer" as a last-resort fallback.

## 5. Card name cleaning

**File:** `email_parser.py`

`_clean_card_name()` removes:

- Price suffixes (e.g. "0,02 EUR", "1.50 EUR")
- Set information in parentheses (e.g. "(Magic: The Gathering | ...)")
- Language markers (e.g. "(EN)", "(DE)")
- Condition markers (e.g. "[NM]", "[EX]")
- Extra whitespace and punctuation

## 6. Direktverkauf — Bestellungen ohne Cardmarket

**Dateien:** `direktverkauf.py`, `quittung.py`, `templates/direktverkauf_*.html`

Nicht jeder Verkauf läuft über Cardmarket. Auf dem Flohmarkt oder von Hand zu
Hand entsteht keine Bestellmail — der Verkauf soll trotzdem denselben Weg
gehen: Bestellung, Beleg, Lagerplatz frei, Umsatz in der Buchhaltung.

**Kein zweiter Bestellweg.** Ein Direktverkauf landet in denselben Tabellen
(`orders`, `order_items`), nur mit `quelle = 'manuell'`. Alles Nachgelagerte —
Beileger, API, Buchhaltung — funktioniert dadurch unverändert.

### Zwei neue Spalten an `orders`

| Spalte | Werte | Zweck |
|---|---|---|
| `quelle` | `cardmarket`, `manuell` | woher die Bestellung stammt |
| `verkaufskanal` | `cardmarket`, `direktverkauf`, `flohmarkt` | entscheidet in der Buchhaltung über Geldkonto und Gebühr |

Beide sind nicht-zerstörend ergänzt; vorhandene Zeilen bekommen über den
Vorgabewert `cardmarket`.

### Zwei Einstiege

* **Bestellungen → „＋ Direktverkauf"** für den vollständigen Vorgang
* **Kartenübersicht → „Verkauft"** fragt nach: mit Beleg (Formular) oder nur
  ausbuchen wie bisher

### Zwei Belege

| | Format | Adresse | wofür |
|---|---|---|---|
| Beileger (`shipping_note.py`) | A4 | nötig | Versand, Fensterkuvert |
| Quittung (`quittung.py`) | A5 quer | keine | Verkauf vor Ort |

Die Quittung ist ein eigenes Dokument, kein beschnittener Beileger: ohne
Anrede, ohne Bewertungssatz, ohne Adressfeld. Schrift, Farben und Absender
kommen aus `shipping_note` — es gibt nur **einen** Ort fürs Erscheinungsbild.

Ausdrücklich **keine** Rechnung im steuerlichen Sinn.

### Regeln, die der Code durchsetzt

* **Preise werden nicht geraten.** Der Einkaufspreis ist nicht der
  Verkaufspreis; jede Position bekommt ihren Preis von Hand.
* **Alles oder nichts.** Erst wird jede Position geprüft, dann geschrieben,
  dann ausgebucht. Reicht bei einer Position der Bestand nicht, entsteht weder
  eine Bestellung noch verschwindet eine Karte.
* **Status sofort `sold`.** Der Verkauf ist gelaufen; stünde er als „offen" in
  der Liste, könnte ein zweiter Klick auf „verkauft" dieselbe Karte noch
  einmal ausbuchen. Gedruckt wird von der Abschlussseite `/orders/<id>/beleg`.
* **Gescheitertes Ausbuchen wird gemeldet**, nicht verschluckt: sonst stünde
  ein Verkauf im System, während die Karte weiter im Regal liegt.
* Eigene Nummernfolge `DV-JJJJ-NNNN`, damit man auf dem Papier die Herkunft
  sieht.

### Weg in die Buchhaltung

`api_v1` liefert `verkaufskanal` und `quelle` mit. Die Buchhaltung entscheidet
daraus:

| Kanal | Geldkonto | Plattformgebühr |
|---|---|---|
| `cardmarket` | Cardmarket | ja |
| `flohmarkt`, `direktverkauf` | Barkasse | keine |

⚠️ **Nicht doppelt erfassen:** Wer Direktverkäufe einzeln erfasst, darf für
dieselben Verkäufe keinen Flohmarkt-Tagesabschluss in der Buchhaltung buchen.
Die Oberfläche warnt an drei Stellen, und die Prüfliste der Auswertung meldet
es, falls es doch passiert.

## 7. Nachdrucken verkaufter Bestellungen

Die Übersicht unter `/orders` zeigt bewusst nur **offene** Bestellungen. Wer
auf „Versendet / Verkauft" drückt, verliert damit den Weg zum Beileger — die
Angaben selbst bleiben aber vollständig: `mark_order_sold` setzt nur den Status
und zieht die Karten ab, `order_items` wird nicht angerührt.

`/orders/verkauft` schließt diese Lücke:

- verkaufte Bestellungen, neueste zuerst, **ohne** die Frist
  `ORDER_CUTOFF_DAYS` der offenen Liste — hier geht es gerade um
  Zurückliegendes;
- Suche nach Bestellnummer oder Käufer, seitenweise
  (`VERKAUFTE_JE_SEITE = 25`);
- Beileger und Quittung drucken, Sprache umschalten;
- die Adresse bestätigen, falls das vor dem Verkauf vergessen wurde — **ohne
  bestätigte Adresse wird auch hier kein Beileger gedruckt**.

Die Seite ändert **nichts am Bestand**: kein „Verkauft", kein Löschen, keine
Kartenzuordnung. Ein Test hält den Bestand vor und nach dem Aufruf gegen.

Die Formulare für Adresse und Sprache kommen von beiden Seiten. Wohin danach
weitergeleitet wird, entscheidet ein Formularfeld `zurueck` mit dem festen Wert
`verkauft` — keine freie URL, damit sich die Weiterleitung nicht auf eine
fremde Adresse biegen lässt.

## Tests

- **`test_verkaufte_bestellungen.py`** — Archiv findet Verkauftes und nur
  Verkauftes, Nachdruck von Beileger und Quittung, Suche, Blättern,
  unsinnige Seitenzahlen, Adresse bestätigen mit Rückweg, Bestand unverändert.
- **`test_direktverkauf.py`** — Anlegen, Bestandsführung, Preisprüfung,
  Formular, Quittung, Kanal durch die API, gescheitertes Ausbuchen.
- **`test_order_matching.py`** — `test_find_card_in_inventory`,
  `test_find_card_partial_match`, `test_find_card_not_in_inventory`,
  `test_get_image_from_default_db`.
- **`test_order_deletion.py`** — `test_order_deletion_cascade`,
  `test_email_date_column`.
- **`test_email_parser.py`** — buyer-name parsing (subject and body), card item
  parsing in various formats, card-name cleaning, German and English formats.
- **`test_schema_migration.py`** — existing schema tests continue to pass.

## UI summary

`templates/orders.html` shows:

- Buyer name from the parsed email (e.g. "KohlkopfKlaus")
- Email date instead of insertion time
- Card images from inventory or `default-cards.db`
- Storage locations (e.g. "O01-S01-P1") or "Nicht im Lager"
- Delete button (`×`) with a confirmation dialog
- The existing "Verkauft" button

## Security considerations

- The delete action requires a POST request (prevents CSRF via GET).
- A confirmation dialog guards against accidental deletion.
- Foreign-key CASCADE is properly configured with `PRAGMA foreign_keys = ON`.
- Parameterized queries — no SQL-injection vulnerabilities.

## Performance considerations

- Image lookup from `default-cards.db` is efficient (indexed on name).
- Partial matching uses `LIKE` with proper indexing.
- CASCADE deletion is atomic (single transaction).
- No N+1 query issues.

## Backward compatibility

- Existing orders without `email_date` fall back to `date_received`.
- Existing functionality (mark sold, sync) is unchanged.
- Database migrations are idempotent.
- No breaking changes to the API or data structures.
