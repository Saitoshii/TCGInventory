# Inventur: Bestand im System und im Regal

Bei einer Inventur stand mehr im System als im Regal. Die Prüfung im November
2026 hat die Wege gefunden, auf denen das passieren konnte. Alle sind behoben
und mit Tests abgesichert. Ihre **Folgen** stecken aber noch in den Daten, die
vorher entstanden sind — dafür gibt es die Seite **System → Bestand prüfen**.

## Was zu viel Bestand erzeugte

| Weg | Was passierte | Jetzt |
|---|---|---|
| Position ohne Zuordnung | „Versendet / Verkauft" zog sie nicht ab; das stand erst in der Meldung danach, im selben blauen Kasten wie jede Bestätigung | Hinweis **vor** dem Knopf, Abschließen nur mit Häkchen; Warnung in Gelb |
| Bestand reichte nicht | Es hieß trotzdem „abgezogen" | Meldung „Spock (1 von 3)"; die Abzugsmenge steht an der Position (`order_items.abgezogen`) |
| Offene Bestellung älter als 30 Tage | Verschwand aus der Liste — samt dem Knopf, der abgezogen hätte | Die Liste nennt ihre Zahl; „Ältere anzeigen" (`/orders?alle=1`) |
| Bestellung gelöscht statt abgeschlossen | Bestand blieb, ohne Hinweis | Rückfrage sagt „Bestand wird NICHT verändert"; Protokoll; verkaufte lassen sich nicht löschen |
| Set-Code beim Bearbeiten überschrieben | Aus `trk` wurde der Ordnername „Star Trek" — Bestellungen fanden die Karte nicht mehr, der nächste Import legte sie ein zweites Mal an | Eigenes Feld für den Set-Code; ohne Feld bleibt er unverändert |
| „Erneut auflösen" zweimal gleichzeitig | Nach dem Kartendaten-Update und per Knopf „Alle erneut versuchen" kamen dieselben Einträge zweimal in die Warteschlange | Warteschlange und Needs-Review nur unter einer Sperre |
| Zweiter Klick auf „Hochladen" | Zweiter, gleichzeitiger Import derselben Datei | Knopf sperrt sich; der Server startet keinen zweiten Lauf |
| Dieselbe Datei noch einmal | Alle Mengen wurden still erhöht | Datei wird erkannt (Tabelle `importe`); zweites Einlesen nur mit Häkchen |
| Warteschlange | Nicht zu sehen, was schon im Bestand liegt | „schon im Bestand: 2 → 4", „doppelt", Knopf „Nur neue übernehmen" |
| Doppelklick auf „Speichern" | Artikel doppelt angelegt bzw. Menge verdoppelt | Einmalige Formular-Kennung; Knöpfe sperren sich nach dem Absenden |
| Zwei Mails zur selben Bestellung | Zwei Bestellungen; zweimal „Verkauft" zog doppelt ab | Gleiche Bestellnummer wird nicht noch einmal angelegt |

Weitere Fehler, die den Betrieb störten: Mails ohne erkannte Positionen wurden
verworfen (jetzt: Bestellung mit Hinweis), nach abgelaufener Anmeldung führte
„Versendet / Verkauft" auf „Method Not Allowed", ohne dass die Bestellung
abgeschlossen war (jetzt: Rücksprung mit Warnung), ein Umzug auf einen anderen
Lagerplatz ließ den alten belegt, Plätze ab P10 aus der kurzzeitigen
18-Plätze-Fassung wurden vergeben (jetzt beim Start entfernt, soweit frei),
und Foil ließ sich in der Warteschlange nicht abwählen.

## Nach dem Update: einmal aufräumen

1. **System → Bestand prüfen** öffnen.
2. **Ältere offene Bestellungen** (Hinweis oben): verschickte mit „Versendet /
   Verkauft" abschließen, stornierte löschen.
3. **Verkauft, aber nicht abgezogen**: je Position die verkaufte Karte wählen
   und „Ausbuchen" — oder „Erledigt", wenn die Ware nie im Bestand war.
   Positionen, die vor diesem Update mit Karte abgeschlossen wurden, stehen
   dort nicht: ob damals abgezogen wurde, lässt sich nicht mehr feststellen.
4. **Set-Code fehlt oder ist falsch**: „Alle … wiederherstellen". Grundlage ist
   die Scryfall-ID an der Karte; ohne sie bleibt nur „Bearbeiten".
5. **Dieselbe Karte mehrfach im selben Ordner**: erscheint oft erst nach
   Schritt 4. Im Ordner nachzählen, die Menge an einer Zeile berichtigen, die
   andere über „Bearbeiten → Aus dem Bestand entfernen" löschen.
6. **Karten auf Plätzen, die es nicht gibt**: Karte im Ordner suchen und ihren
   echten Platz eintragen.

Geändert wird dabei nur, was angeklickt wird; jede Änderung steht im
Audit-Log.

## Danach: Inventur berichtigen

- **Menge berichtigen**: Karte → „Bearbeiten" → Menge. Der Set-Code bleibt
  dabei erhalten.
- **Karte ist gar nicht da**: „Bearbeiten" → „Aus dem Bestand entfernen"
  (steht im Audit-Log als `delete`, nicht als Verkauf).
- **Verkauft ohne Bestellung** (Flohmarkt, Direktkontakt): „Verkauft" in der
  Kartenliste, mit Beleg über „Direktverkauf".

## Technisches

- `order_items.abgezogen` — beim Abschließen tatsächlich abgezogene Menge;
  `NULL` bei vor diesem Update abgeschlossenen Bestellungen.
- `order_items.bestand_geklaert` — auf der Prüfseite erledigt.
- `importe` — Fingerabdruck (SHA-256, unabhängig vom Zeilenende) jeder
  eingelesenen Datei.
- Abschließen läuft in einer Transaktion: erst der Status (nur wenn noch
  offen), dann die Abzüge (`lager_manager.buche_aus`).
- Beim Start (`setup_db._bereinige_plaetze`): freie Plätze ab P10 ohne Karte
  werden entfernt, die Belegung wird aus den Karten abgeleitet.
- Logik der Prüfseite: `bestandspruefung.py`.
