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

## Nicht auflösbare Zeilen

Eine Zeile ohne Set-Code oder Kartennummer und eine Zeile ohne Treffer in der
lokalen Scryfall-Datenbank landen unter
[Needs-Review](../templates/needs_review.html) — nie still im Bestand. Dort
werden Set-Code, Kartennummer und Sprache korrigiert und die Anreicherung erneut
versucht.

## Doppelte Zeilen

Identische Identitäten (`set_code` + `collector_number` + `language` + `foil`)
im selben Ordner werden beim Übernehmen zusammengeführt: die Menge wird erhöht,
statt eine zweite Zeile anzulegen (`add_or_increment_card`). Foil und Normal
derselben Karte bleiben getrennt.
