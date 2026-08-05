# Backup nach OneDrive (verschlüsselt)

Nächtliche, **auf dem Pi verschlüsselte** Sicherung aller geschäftskritischen
Daten nach OneDrive. Microsoft sieht zu keinem Zeitpunkt Klartext — auch keine
Datei- oder Ordnernamen.

**Gesichert wird:** `mtg_lager.db` (Inventar, Bestellungen, Buchungsjournal),
weitere Datenbanken aus `data/`, das Beleg-Verzeichnis `data/belege/`
(Rechnungs-PDFs und -Bilder) sowie Konfigurationsdateien ohne Zugangsdaten.

**Nicht gesichert:** `default-cards.db` (Scryfall-Bulkdaten, jederzeit neu
erzeugbar und unnötig groß) und alles, was nach Zugangsdaten aussieht
(`credentials.json`, `token.json`, `rclone.conf`, `*.key`, `*.pem`, `*.pass`).

---

## 1. Einmalige Einrichtung

Diese Schritte macht ein Mensch am Pi — die Anmeldung bei Microsoft läuft über
einen Browser und lässt sich nicht automatisieren. Etwa 20 Minuten einplanen.

### 1.1 rclone installieren

```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
rclone version
```

Erwartete Ausgabe: eine Versionsnummer, z. B. `rclone v1.66.0`.

### 1.2 OneDrive-Remote anlegen

```bash
rclone config
```

Der Assistent fragt der Reihe nach. Antworten:

| Frage | Eingabe |
|---|---|
| `e/n/d/r/c/s/q>` | `n` (New remote) |
| `name>` | `onedrive` |
| `Storage>` | `onedrive` (bzw. die Nummer von „Microsoft OneDrive") |
| `client_id>` | leer lassen, **Enter** |
| `client_secret>` | leer lassen, **Enter** |
| `Edit advanced config?` | `n` |
| `Use web browser to automatically authenticate?` | `y` |

Jetzt öffnet sich ein Browser: **mit dem Microsoft-Konto anmelden** und den
Zugriff bestätigen.

> **Pi ohne Desktop/Browser?** Dann bei der Frage `y/n` mit `n` antworten.
> rclone zeigt einen Befehl (`rclone authorize "onedrive"`), den man auf einem
> Rechner **mit** Browser ausführt; das Ergebnis (ein langer Token-Text) wird
> zurück in die Pi-Konsole kopiert.

Danach:

| Frage | Eingabe |
|---|---|
| `Your choice>` (Kontotyp) | `1` für OneDrive Personal bzw. Business laut Konto |
| Laufwerksauswahl | die angebotene Standard-ID bestätigen |
| `Yes this is OK` | `y` |

Test:

```bash
rclone lsd onedrive:
```

Zeigt die Ordner im OneDrive → die Verbindung steht.

### 1.3 Verschlüsseltes Remote darüber legen

Im selben `rclone config` mit `n` ein **zweites** Remote anlegen:

| Frage | Eingabe |
|---|---|
| `name>` | `onedrive-crypt` |
| `Storage>` | `crypt` |
| `remote>` | `onedrive:TCGInventory-Backup` |
| `filename_encryption>` | `1` (standard) — **wichtig**, verschlüsselt Dateinamen |
| `directory_name_encryption>` | `1` (true) — **wichtig**, verschlüsselt Ordnernamen |
| `Password or pass phrase for encryption` | `y` (own password) → **Passwort 1** eingeben |
| `Password or pass phrase for salt` | `y` (own password) → **Passwort 2** eingeben |
| `Edit advanced config?` | `n` |
| `Yes this is OK` | `y` |

Beide Passwörter selbst vergeben (jeweils lang und zufällig, z. B. aus dem
Passwort-Manager). **Nicht** die von rclone generierten Vorschläge verwenden,
ohne sie zu notieren.

### 1.4 rclone-Konfiguration mit Passwort schützen

Immer noch in `rclone config`: `s` (Set configuration password) → `a` (Add
password) → ein **drittes** Passwort vergeben (Konfigurationspasswort). Dann `q`
zum Beenden.

Dieses dritte Passwort legt das Skript in einer geschützten Datei ab:

```bash
install -m 600 /dev/null ~/.config/rclone/config.pass
nano ~/.config/rclone/config.pass          # nur das Passwort, eine Zeile
chmod 600 ~/.config/rclone/rclone.conf
```

Prüfen, dass beide Dateien `-rw-------` zeigen:

```bash
ls -l ~/.config/rclone/
```

---

## 2. ⚠️ Passwörter — bitte sehr ernst nehmen

> **Ohne die beiden crypt-Passwörter (Passwort 1 und Passwort 2) sind alle
> Sicherungen unwiederbringlich verloren.**
>
> Microsoft kann sie **nicht** wiederherstellen. Der Support kann nicht helfen.
> Es gibt keine Hintertür. Die Daten in OneDrive sind dann für immer
> unlesbarer Zufallsdatensalat.

Deshalb, verbindlich für die GbR:

- [ ] **Beide Gesellschafter** besitzen Passwort 1 und Passwort 2.
- [ ] Beide Passwörter liegen in einem **Passwort-Manager**.
- [ ] Zusätzlich existiert ein **Ausdruck auf Papier** an einem sicheren Ort
      (z. B. Ordner mit den Geschäftsunterlagen, nicht neben dem Pi).
- [ ] Das Konfigurationspasswort (Passwort 3) ist ebenfalls notiert — es
      schützt nur die rclone-Datei auf dem Pi und ist bei einem Neuaufbau
      nötig, aber nicht zum Entschlüsseln der Sicherungen.

Ein Backup, dessen Passwort nur einer kennt, ist bei Krankheit oder Ausfall
dieser Person wertlos.

---

## 3. Timer installieren

Die Umgebungsdatei anlegen (enthält nur Pfade, keine Passwörter):

```bash
sudo install -m 600 /dev/null /etc/tcginventory-backup.env
sudo nano /etc/tcginventory-backup.env
```

Inhalt nach dem Muster in `deploy/tcginventory-backup.env.example`, mindestens:

```
TCG_BACKUP_REMOTE=onedrive-crypt:
TCG_RCLONE_CONFIG=/home/pi/.config/rclone/rclone.conf
TCG_RCLONE_CONFIG_PASS_FILE=/home/pi/.config/rclone/config.pass
```

Units installieren und aktivieren:

```bash
sudo cp deploy/tcginventory-backup.service /etc/systemd/system/
sudo cp deploy/tcginventory-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tcginventory-backup.timer
```

Kontrolle:

```bash
systemctl list-timers tcginventory-backup.timer     # nächster Lauf
sudo systemctl start tcginventory-backup.service    # sofort einmal testen
journalctl -u tcginventory-backup.service -n 50     # Protokoll ansehen
```

Danach in der Weboberfläche unter **System → Backup** nachsehen: dort stehen
Zeitpunkt, Ergebnis, Größe und Prüfsumme des letzten Laufs.

**Uhrzeit ändern** (Standard 03:30):

```bash
sudo systemctl edit tcginventory-backup.timer
# [Timer]
# OnCalendar=
# OnCalendar=*-*-* 02:15:00
sudo systemctl daemon-reload
```

`Persistent=true` ist gesetzt: War der Pi nachts aus, wird der Lauf nach dem
Einschalten nachgeholt.

---

## 4. Wiederherstellung

Verfügbare Stände anzeigen:

```bash
cd ~/TCGInventory
set -a; . /etc/tcginventory-backup.env; set +a
./scripts/restore.sh liste
```

Einen Stand holen, prüfen und entpacken — **immer in ein separates
Verzeichnis**, nie direkt über die laufenden Daten:

```bash
./scripts/restore.sh hole tcginventory_2026-05-04_0330.tar.gz --ziel /tmp/restore
```

Das Skript

1. lädt den Stand herunter (rclone entschlüsselt dabei automatisch),
2. vergleicht die **SHA-256-Prüfsumme** mit der Begleitdatei,
3. entpackt nach `/tmp/restore/inhalt/`,
4. führt `PRAGMA integrity_check` auf jeder Datenbank aus,
5. gibt aus, welche Schritte manuell folgen.

Produktivsetzen (bewusst manuell):

```bash
sudo systemctl stop tcginventory.service
mv ~/TCGInventory/mtg_lager.db ~/mtg_lager.db.vor-restore     # Sicherheitsnetz
cp /tmp/restore/inhalt/db/mtg_lager.db ~/TCGInventory/mtg_lager.db
cp -r /tmp/restore/inhalt/data/belege ~/TCGInventory/data/
sudo systemctl start tcginventory.service
```

Anschließend in der Weboberfläche stichprobenartig prüfen: Bestand, offene
Bestellungen, Buchungsjournal.

**`default-cards.db` fehlt im Backup** — das ist Absicht. Sie wird bei Bedarf
neu erzeugt:

```bash
python3 build_card_db.py
```

---

## 5. Wiederherstellungstest (bitte einmal wirklich durchspielen)

> **Ein Backup, das nie zurückgespielt wurde, gilt als ungetestet** — und damit
> als nicht vorhanden. Der Test dauert 15 Minuten und ist die einzige Art,
> sicher zu wissen, dass die Sicherung im Ernstfall trägt.

Checkliste — einmal nach der Einrichtung und danach halbjährlich:

- [ ] `./scripts/restore.sh liste` zeigt mindestens einen Stand.
- [ ] Ein Stand lässt sich nach `/tmp/restore` holen.
- [ ] Die Ausgabe meldet **„Prüfsumme (SHA-256): OK"**.
- [ ] Die Ausgabe meldet **„integrity_check mtg_lager.db: OK"**.
- [ ] Unter `/tmp/restore/inhalt/data/belege/` liegen die Belegdateien.
- [ ] Ein Beleg-PDF lässt sich öffnen und ist lesbar.
- [ ] Das Live-Verzeichnis ist unverändert (der Test hat nichts überschrieben).
- [ ] Der Test wurde **von beiden Gesellschaftern** mindestens einmal
      eigenständig durchgeführt — jeder muss es im Ernstfall allein können.
- [ ] Datum des letzten erfolgreichen Tests notiert: ________________

Zusätzlich regelmäßig (dauert Sekunden):

- [ ] In der Weboberfläche unter **System → Backup** steht ein grüner Hinweis.
      Erscheint dort eine rote Warnung, ist seit über 48 Stunden kein Backup
      mehr durchgelaufen — dann `journalctl -u tcginventory-backup.service`
      ansehen.

---

## 6. Aufbewahrung

In der Cloud verbleiben automatisch:

- die **letzten 7 Stände** (nicht „die letzten 7 Kalendertage" — fällt ein Lauf
  aus, bleiben trotzdem sieben Stände erhalten),
- zusätzlich der **erste Stand jedes Monats** für die letzten 12 Monate.

Der Monatsstand überlebt die Tagesrotation, damit die Aufbewahrungspflicht für
Geschäftsunterlagen nicht an der 7-Tage-Rotation scheitert. Ältere Stände werden
beim nächsten Lauf entfernt. Anpassbar über `TCG_BACKUP_KEEP_DAILY` und
`TCG_BACKUP_KEEP_MONTHLY`.

---

## 7. Fehlersuche

| Symptom | Ursache / Abhilfe |
|---|---|
| `Kein Backup-Ziel gesetzt` | `TCG_BACKUP_REMOTE` fehlt in `/etc/tcginventory-backup.env` |
| `failed to load config file` | falsches Konfigurationspasswort in `config.pass` |
| `integrity_check fehlgeschlagen` | Datenbank ist beschädigt — **es wurde nichts hochgeladen**, alten Stand zurückspielen |
| Rote Warnung in der Oberfläche | `systemctl status tcginventory-backup.timer`, dann `journalctl -u tcginventory-backup.service -n 50` |
| Upload bricht ab | Netzwerk/Token: `rclone lsd onedrive:` testen, ggf. `rclone config reconnect onedrive:` |

Das Skript beendet sich bei jedem Fehler mit einem Exit-Code ≠ 0 und schreibt
Log **und** Status-Datei — ein stiller Fehlschlag ist ausgeschlossen.
