# CSV-Import

Der Bulk-Import (`/cards/bulk_add`) liest zwei CSV-Layouts über denselben
Codepfad (`dragonshield.py` → `web._process_bulk_upload`). Es gibt keine
Layout-Erkennung: Spalten werden über ihren Namen gelesen und fehlende Spalten
bleiben einfach leer.

## Unterstützte Layouts

**Dragonshield**

```
Folder Name, Quantity, Trade Quantity, Card Name, Set Code, Set Name,
Card Number, Condition, Printing, Language, Price Bought, Date Bought,
LOW, MID, MARKET
```

**Scan-App-Listenexport**

```
List Type, List Name, Collection, Format, Board, Quantity, Card Name, Set Code,
Set Name, Card Number, Condition, Printing, Rarity, Language, Price Bought,
Date Bought, Parent List Type, Parent List Name,
Current Price (<preisquelle>), List Cover Image, Parent List Cover Image
```

Beide benennen die relevanten Felder gleich — deshalb genügt ein Codepfad.

Der Dateiname spielt keine Rolle: manche Scan-Apps speichern denselben Inhalt
als `.txt`. Das Formular bietet im Dateidialog `.csv` und `.txt` an.

Eine führende Separator-Direktive (`sep=,`, auch in Anführungszeichen) und ein
UTF-8-BOM werden übersprungen. Kartennamen dürfen Kommas enthalten
(`"Balin, Loremaster"`); geparst wird immer mit dem `csv`-Modul.

## Spaltenzuordnung

| CSV-Spalte | Feld im Bestand | Anmerkung |
|---|---|---|
| `Card Name` | `name` | wird durch den kanonischen Scryfall-Namen ersetzt, wenn die Anreicherung greift |
| `Set Code` | `set_code` | auf Scryfall-Konvention normalisiert (`HOB` → `hob`), Abweichungen über `SET_CODE_ALIASES` |
| `Card Number` | `collector_number` | Teil der Identität |
| `Language` | `language` | `English`/`en` → `en` |
| `Printing` | `foil` | `Foil` → ja, sonst nein |
| `Condition` | `condition` | auf Cardmarket-Codes normalisiert, siehe unten |
| `Quantity` | `quantity` | |
| `Price Bought` | `price` | Einkaufspreis, deutsches Dezimalkomma erlaubt |
| `MARKET` / `Current Price (…)` | `market_price` | Marktpreis zum Export-Zeitpunkt |
| `Rarity` | `rarity` | nur Scan-App-Layout, Kleinschreibung |
| `Date Bought` | `date_bought` | auf `YYYY-MM-DD` normalisiert; getrennt von `date_added` (Importzeitpunkt) |
| `Set Name` | — | nur zur Anzeige in Needs-Review |
| `Folder Name` / `List Name` | — | **keine** automatische Ordnerzuordnung; der Zielordner wird im Formular gewählt |
| `List Type`, `Collection`, `Format`, `Board`, `Parent List …`, `… Cover Image` | — | ohne Bedeutung für den Bestand |

## Zustände

Der Bestand speichert die Cardmarket-Codes (`lager_manager.CONDITION_VALUES`),
und der Zustandsfilter in der Kartenliste vergleicht exakt. Exporte schreiben
stattdessen die Langformen, die deshalb abgebildet werden:

| Export | Bestand |
|---|---|
| `Mint` | `MT` |
| `NearMint`, `Near Mint` | `NM` |
| `Excellent` | `EX` |
| `Good` | `GD` |
| `LightPlayed`, `Lightly Played` | `LP` |
| `Played` | `PL` |
| `Poor` | `PO` |

Werte ohne anerkannte 1:1-Zuordnung — etwa die fünfstufige TCGplayer-Skala mit
`Moderately Played`, `Heavily Played`, `Damaged` — werden **nicht geraten**,
sondern unverändert übernommen und bleiben in der Warteschlange sichtbar.

## Zwei Preise, nicht einer

`price` ist der Einkaufspreis, `market_price` der Marktpreis zum
Export-Zeitpunkt. Manche Scan-Apps belegen `Price Bought` mit dem aktuellen
Marktpreis vor — dann sind beide Werte identisch und der Einkaufspreis ist in
Wahrheit unbekannt. Die Warteschlange markiert solche Zeilen mit 💶, damit der
Einkaufspreis vor der Übernahme geprüft werden kann; für die Marge wäre er sonst
falsch.

## Lagerplätze: was die Warteschlange braucht

Jeder Eintrag der Warteschlange belegt beim Übernehmen **einen** Platz im
Zielordner — unabhängig von der Menge (drei Exemplare liegen im selben Fach).
Vergeben wird in der Reihenfolge der Datei, also so, wie gescannt wurde. Eine
Mappenseite hat neun Plätze.

Die Warteschlange zeigt je Zielordner, wie viele Plätze gebraucht werden und
wie viele frei sind (`web._platzbedarf`). Was im selben Ordner schon liegt
(gleiche Identität), erhöht nur die Menge und zählt nicht.

Reicht der Platz nicht, oder wurde **kein Zielordner** gewählt, kommen die
überzähligen Einträge **ohne Lagerplatz** in den Bestand. Das bleibt möglich —
ein voller Ordner soll den Import nicht abbrechen —, ist aber nicht mehr still:

- vorher: Warnung in der Warteschlange mit der Zahl der fehlenden Seiten und
  dem Weg zu „Ordner bearbeiten"; „Add All" fragt dann nach;
- nachher: „… Einträge mit … Karten ohne Lagerplatz übernommen".

Früher hieß es in beiden Fällen nur „Alle Karten aus der Warteschlange
übernommen", und es fiel erst auf, wenn eine Bestellung „Kein Lagerort" zeigte.

## Neues Set, ältere Kartendaten

Die Kartendaten werden von Hand aktualisiert (*System → Kartendaten*). Ist ein
Set neuer als die Daten, findet **keine** Zeile dieses Sets einen Treffer.
Das ist etwas anderes als eine einzelne falsche Kartennummer, und es wird
auch so benannt (`web._grund_ohne_treffer`):

| Grund in Needs-Review | Bedeutung | Abhilfe |
|---|---|---|
| „Set 'trk' ist in der lokalen Kartendatenbank unbekannt" | die Datenbank kennt das ganze Set nicht | Kartendaten aktualisieren |
| „die lokale Kartendatenbank fehlt" | es gibt noch gar keine | Kartendaten laden |
| „Kein Scryfall-Treffer für Set 'trk' Nr. 9999" | das Set ist bekannt, diese Nummer nicht | Zeile korrigieren |

Nach dem Import nennt die Meldung die unbekannten Sets mit der Zahl der
betroffenen Zeilen, und Needs-Review zeigt oben einen Hinweis mit dem Weg zu
den Kartendaten. Danach genügt **„Alle erneut versuchen"**
(`/cards/needs_review/retry_all`): jeder Eintrag mit Set-Code und Nummer wird
noch einmal aufgelöst, mit allen Angaben aus der Datei. Was weiterhin keinen
Treffer hat, bleibt stehen; Namen mit mehreren Ausgaben bleiben unberührt.

### Kartendaten aus der Preview-Zeit

Tückischer als ein unbekanntes Set ist ein **halb** bekanntes. Scryfall nimmt
während der Preview-Zeit nur die vorgestellten Karten auf, den Rest eines Sets
erst kurz vor dem Erscheinen. Kartendaten aus dieser Zeit kennen das Set, aber
nur zum Teil. So geschehen mit „Star Trek" (erschienen 13.11.2026): 78 von
250 Zeilen wurden erkannt, 172 nicht — jede mit „Kein Treffer für Set 'trk'
Nr. …", was wie 172 Einzelfehler aussah.

Fehlen aus einem bekannten Set mindestens `HAEUFUNG_JE_SET` (3) Karten, nennt
die Meldung nach dem Import das Set und den Stand der Kartendaten, und
Needs-Review zeigt den Knopf **„Kartendaten aktualisieren und danach erneut
versuchen"**.

**Nach jeder Aktualisierung der Kartendaten** — von Hand, über diesen Knopf
oder nachts — werden die offenen Einträge in Needs-Review von selbst erneut
aufgelöst (`web._needs_review_erneut_aufloesen`, dieselbe Regel wie „Alle
erneut versuchen").

**Nachts zwischen 3 und 6 Uhr** sieht `web.KartendatenWaechter` einmal nach,
ob Scryfall eine neuere Fassung hat; nur dann wird geladen. Er startet mit der
Anwendung, wie der Bestelleingang und der Cardmarket-Katalog.

Bleibt das Set auch nach dem Aktualisieren unbekannt, weicht der Set-Code der
Datei von Scryfall ab (siehe `SET_CODE_ALIASES` in `dragonshield.py`).

Die Suche nach Set und Nummer (`card_scanner.find_by_identity`) nutzt den
Index der Kartendatenbank. Der frühere Vergleich über `lower(set_code)` las
für jede Zeile die ganze Tabelle; für anders erzeugte Datenbanken mit groß
geschriebenen Set-Codes bleibt er als zweiter Versuch erhalten.

## Nicht auflösbare Zeilen

Eine Zeile ohne Set-Code oder Kartennummer, eine Zeile ohne Treffer in der
lokalen Scryfall-Datenbank und ein bloßer Name mit mehreren Ausgaben landen unter
[Needs-Review](../templates/needs_review.html) — nie still im Bestand. Dort
werden Set-Code, Kartennummer und Sprache korrigiert und die Anreicherung erneut
versucht.

## JSON-Datei

Neben CSV nimmt der Bulk-Import eine JSON-Datei an. Sie enthält eine **Liste**;
jeder Eintrag ist ein Kartenname oder ein Objekt.

```json
[
  "Lightning Bolt",
  {"name": "Sol Ring", "quantity": 2, "foil": true},
  {"name": "Ezio, Brash Novice", "set_code": "ACR", "collector_number": "56",
   "language": "en", "condition": "NM", "quantity": 1}
]
```

Es gibt dafür keinen eigenen Codepfad. `web._json_eintrag` ordnet jeden
Eintrag ein:

| Eintrag | Weg |
|---|---|
| nennt `set_code`/`set` **oder** `collector_number`/`card_number` | wie eine CSV-Zeile: `extract_row` → `find_by_identity`. Fehlt eines von beiden oder gibt es keinen Scryfall-Treffer → Needs-Review |
| nur ein Name (Zeichenkette oder Objekt mit `name`) | derselbe Weg wie die Texteingabe „ein Name pro Zeile" (siehe „Nur ein Name"); `quantity`, `language`, `condition`, `foil` und `price` bleiben erhalten |
| weder Zeichenkette noch Objekt, oder ohne Namen | übersprungen und in der Meldung gezählt |

Die Schlüssel werden wie CSV-Spalten gelesen (`Card Name` = `card_name` =
`name`), die Werte genauso normalisiert (Sprache, Zustand, Set-Code). `foil`
darf `true`, `1`, `"yes"`, `"ja"` oder `"Foil"` sein und entspricht
`"printing": "Foil"`.

**Ein Set ohne Kartennummer wird nicht geraten.** Früher nahm der JSON-Weg in
diesem Fall die erste Ausgabe des Namens im Set; jetzt geht die Zeile in
Needs-Review, wie beim CSV-Import.

Ist die Datei nicht lesbar oder keine Liste, steht das in der Meldung nach dem
Import. Eine CSV-Datei und die Texteingabe im selben Upload werden trotzdem
verarbeitet.

## Nur ein Name

Die Texteingabe „Karten (ein Name pro Zeile)" und JSON-Einträge ohne Set und
Nummer tragen nur einen Kartennamen. Ein Name ist keine Identität — die meisten
Karten gibt es in vielen Ausgaben. `web._ueber_den_namen` sucht deshalb
**ausschließlich in der lokalen Kartendatenbank** (`card_scanner.fetch_variants`)
und entscheidet nach der Zahl der Treffer:

| Treffer | Ergebnis |
|---|---|
| genau eine Ausgabe | Warteschlange, mit Set-Code, Nummer und IDs dieser Ausgabe |
| mehrere Ausgaben | Needs-Review: „Der Name hat N Ausgaben – bitte eine wählen". Die Ausgaben stehen dort als Liste (Set-Code · Nummer · Sprache, sortiert); die Wahl füllt Set-Code und Kartennummer und zeigt das Bild |
| keine | Needs-Review: „Kein Treffer …" — Set-Code und Nummer von Hand nachtragen. Das trifft auch übersetzte Namen („Blitzschlag"): die Datenbank kennt die Karte unter ihrem englischen Namen |
| Kartendatenbank fehlt | Needs-Review mit dem Hinweis, sie unter *System → Kartendaten* zu laden |

Mengen, Sprache, Zustand, Foil und Preis aus der Datei bleiben über den Umweg
Needs-Review erhalten.

Zwei Dinge gibt es auf diesem Weg nicht mehr:

- **Keine stille Wahl der ersten Ausgabe.** Früher lieferte
  `fetch_card_info_by_name` die erste Zeile, die die Datenbank zurückgab; sie
  stand dann mit Set, Nummer und Bild in der Warteschlange, als wäre sie die
  gemeinte. Die Funktion ist entfernt.
- **Keine Anfrage an die Scryfall-API.** Ohne lokalen Treffer ging je Karte
  eine Anfrage an `api.scryfall.com/cards/named`.

Der Name des Zielordners wird nicht mehr als Set-Code eingetragen. Der Ordner
ist nur das Ziel; der Set-Code kommt immer von der Karte.

In Needs-Review werden höchstens `MAX_KANDIDATEN` (150) Ausgaben gezeigt; die
Seite sagt, wenn es mehr gibt. Von Hand eingetragene Werte haben Vorrang vor
der Auswahl.

## Doppelte Zeilen

Identische Identitäten (`set_code` + `collector_number` + `language` + `foil`)
im selben Ordner werden beim Übernehmen zusammengeführt: die Menge wird erhöht,
statt eine zweite Zeile anzulegen (`add_or_increment_card`). Foil und Normal
derselben Karte bleiben getrennt.
