"""Scryfall-Bulkdaten in die lokale Kartendatenbank überführen.

Die Bulkdatei ist mehrere hundert MB groß. Sie wird deshalb **streamend**
verarbeitet: das JSON-Array wird Objekt für Objekt gelesen und blockweise in
SQLite geschrieben. Der Speicherbedarf bleibt dabei bei wenigen MB — unabhängig
von der Dateigröße. So läuft der Aufbau auch auf dem Raspberry Pi (4 GB).

Zwei Wege:

* ``aktualisiere_von_scryfall()`` lädt die Bulkdatei direkt von Scryfall und
  schreibt sie im Vorbeifließen in die Datenbank. Die Roh-JSON landet nie auf
  der SD-Karte.
* ``import_cards(json_path, db_path)`` verarbeitet eine bereits vorhandene
  Datei — etwa wenn sie manuell auf den Pi kopiert wurde.

Beide bauen zuerst eine temporäre Datei auf und tauschen sie erst am Ende
atomar gegen die bisherige aus. Bricht der Lauf ab, bleibt die alte
Datenbank unangetastet nutzbar.

Nicht importiert werden Karten mit ``digital: true`` (nur Arena/MTGO, physisch
nicht existent). Die Bildadresse wird **nicht** gespeichert, sondern bei Bedarf
aus der Scryfall-ID abgeleitet (siehe ``card_scanner.image_url_for``).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, Optional, TextIO, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"
JSON_PATH = DATA_DIR / "default-cards.json"
DB_PATH = DATA_DIR / "default-cards.db"

#: Welche Bulkdatei verwendet wird. ``default_cards`` enthält jede Ausgabe
#: einmal, bevorzugt auf Englisch.
BULK_TYP = "default_cards"
BULK_INDEX_URL = "https://api.scryfall.com/bulk-data"

#: Scryfall bittet um eine aussagekräftige Kennung.
USER_AGENT = "TCGInventory/1.0 (Kartendaten-Aktualisierung)"

#: Wie viele Zeilen je Schreibvorgang gebündelt werden.
BLOCKGROESSE = 5000

#: Wie viele Zeichen je Lesevorgang aus dem Datenstrom geholt werden.
LESEBLOCK = 1 << 20          # 1 MiB

Fortschritt = Optional[Callable[[int, str], None]]


# ---------------------------------------------------------------------------
# Streamendes Lesen des JSON-Arrays
# ---------------------------------------------------------------------------
def iter_json_array(stream: TextIO, leseblock: int = LESEBLOCK) -> Iterator[Dict]:
    """Die Objekte eines JSON-Arrays einzeln liefern.

    Es wird immer nur ein Leseblock plus das gerade betrachtete Objekt im
    Speicher gehalten — die Datei wird nie vollständig geladen. Ohne
    Zusatzbibliothek, nur mit dem ``json``-Modul der Standardbibliothek.
    """
    decoder = json.JSONDecoder()
    puffer = ""
    pos = 0

    def nachladen() -> bool:
        """Nächsten Block anhängen und den bereits verarbeiteten Teil verwerfen."""
        nonlocal puffer, pos
        block = stream.read(leseblock)
        if not block:
            return False
        if pos:
            puffer = puffer[pos:]
            pos = 0
        puffer += block
        return True

    # Bis zur öffnenden Klammer vorspulen.
    while True:
        while pos < len(puffer) and puffer[pos].isspace():
            pos += 1
        if pos < len(puffer):
            if puffer[pos] != "[":
                raise ValueError("Die Datei enthält kein JSON-Array.")
            pos += 1
            break
        if not nachladen():
            raise ValueError("Die Datei ist leer.")

    while True:
        # Trennzeichen und Leerraum überspringen.
        while True:
            while pos < len(puffer) and (puffer[pos].isspace() or puffer[pos] == ","):
                pos += 1
            if pos < len(puffer):
                break
            if not nachladen():
                return
        if puffer[pos] == "]":
            return
        # Ein Objekt lesen; reicht der Puffer nicht, nachladen und erneut versuchen.
        while True:
            try:
                objekt, ende = decoder.raw_decode(puffer, pos)
            except ValueError:
                if not nachladen():
                    raise
                continue
            pos = ende
            yield objekt
            break


def iter_json_lines(stream: TextIO) -> Iterator[Dict]:
    """Objekte aus einer JSON-Lines-Datei liefern (eine Karte je Zeile).

    Scryfall stellt die Bulkdaten inzwischen als ``.jsonl.gz`` bereit. Das lässt
    sich Zeile für Zeile lesen — es ist nie mehr als eine Karte im Speicher.
    """
    for zeile in stream:
        zeile = zeile.strip().rstrip(",")
        if not zeile or zeile in ("[", "]"):
            continue
        yield json.loads(zeile)


def iter_karten(stream: TextIO, jsonl: bool = False) -> Iterator[Dict]:
    """Passenden Leser wählen: JSON Lines oder klassisches JSON-Array."""
    return iter_json_lines(stream) if jsonl else iter_json_array(stream)


# ---------------------------------------------------------------------------
# Umwandlung einer Scryfall-Karte in eine Datenbankzeile
# ---------------------------------------------------------------------------
SPALTEN = ("id", "name", "set_code", "set_name", "lang", "collector_number",
           "cardmarket_id")


def zeile_aus_karte(card: Dict) -> Optional[Tuple]:
    """Eine Zeile bilden — oder ``None``, wenn die Karte nicht gebraucht wird.

    Übersprungen werden rein digitale Ausgaben (Arena/MTGO): sie existieren
    physisch nicht und können daher nie im Bestand liegen.
    """
    if card.get("digital"):
        return None
    kennung = card.get("id")
    if not kennung:
        return None
    return (
        kennung,
        card.get("name"),
        card.get("set"),
        card.get("set_name", ""),
        card.get("lang"),
        card.get("collector_number", ""),
        str(card.get("cardmarket_id") or ""),
    )


# ---------------------------------------------------------------------------
# Aufbau der Datenbank
# ---------------------------------------------------------------------------
def _lege_tabelle_an(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            name TEXT,
            set_code TEXT,
            set_name TEXT,
            lang TEXT,
            collector_number TEXT,
            cardmarket_id TEXT
        )
        """
    )


def _lege_indizes_an(conn: sqlite3.Connection) -> None:
    """Indizes erst nach dem Befüllen — das ist deutlich schneller."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON cards(name)")
    # Identitätssuche des Dragonshield-Imports (find_by_identity).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identity ON cards(set_code, collector_number, lang)"
    )
    # Auflösung Set-Name -> Set-Code beim Bestell-Matching.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_set_name ON cards(set_name)")


def schreibe_datenbank(karten: Iterable[Dict], db_path: Path,
                       fortschritt: Fortschritt = None,
                       vor_tausch: Optional[Callable[[], None]] = None) -> int:
    """Karten in eine **neue** Datenbank schreiben und diese atomar einsetzen.

    Der Aufbau läuft in ``<ziel>.neu``; erst danach wird die Datei an ihren
    Platz geschoben. Schlägt etwas fehl, bleibt die bisherige Datenbank
    unverändert in Betrieb. Rückgabe: Anzahl importierter Karten.

    ``vor_tausch`` wird unmittelbar vor dem Austausch aufgerufen. Die Anwendung
    hängt hier das Schließen ihrer zwischengespeicherten Verbindung ein: sonst
    läse sie anschließend weiter aus der alten Datei (und unter Windows
    scheiterte der Austausch an der offenen Datei).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_name(db_path.name + ".neu")
    if temp_path.exists():
        temp_path.unlink()

    anzahl = 0
    conn = sqlite3.connect(temp_path)
    try:
        # Die temporäre Datei wird bei einem Fehler ohnehin verworfen –
        # deshalb ist hier kein Journal nötig, was den Aufbau stark beschleunigt.
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        _lege_tabelle_an(conn)

        block = []
        einfuegen = (f"INSERT OR REPLACE INTO cards ({', '.join(SPALTEN)}) "
                     f"VALUES ({', '.join('?' * len(SPALTEN))})")
        for card in karten:
            zeile = zeile_aus_karte(card)
            if zeile is None:
                continue
            block.append(zeile)
            if len(block) >= BLOCKGROESSE:
                conn.executemany(einfuegen, block)
                anzahl += len(block)
                block.clear()
                if fortschritt:
                    fortschritt(anzahl, f"{anzahl} Karten verarbeitet …")
        if block:
            conn.executemany(einfuegen, block)
            anzahl += len(block)

        if anzahl == 0:
            raise ValueError("Keine Karten gefunden – Datenbank wird nicht ersetzt.")

        if fortschritt:
            fortschritt(anzahl, "Indizes werden angelegt …")
        _lege_indizes_an(conn)
        conn.commit()
    except BaseException:
        conn.close()
        temp_path.unlink(missing_ok=True)
        raise
    conn.close()

    if vor_tausch:
        vor_tausch()                        # offene Leseverbindung schließen
    os.replace(temp_path, db_path)          # atomarer Tausch
    if fortschritt:
        fortschritt(anzahl, f"Fertig – {anzahl} Karten.")
    return anzahl


def import_cards(json_path: Path = JSON_PATH, db_path: Path = DB_PATH,
                 fortschritt: Fortschritt = None,
                 vor_tausch: Optional[Callable[[], None]] = None) -> int:
    """Eine lokal vorliegende Bulkdatei streamend importieren.

    Erkennt ``.json``, ``.jsonl`` und die gepackten Varianten ``.gz``.
    """
    import gzip

    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(json_path)
    name = json_path.name.lower()
    jsonl = ".jsonl" in name
    oeffner = gzip.open if name.endswith(".gz") else open
    with oeffner(json_path, "rt", encoding="utf-8") as f:
        return schreibe_datenbank(iter_karten(f, jsonl), db_path, fortschritt,
                                  vor_tausch)


# ---------------------------------------------------------------------------
# Direkter Bezug von Scryfall
# ---------------------------------------------------------------------------
def bulk_info(typ: str = BULK_TYP) -> Dict:
    """Metadaten der Bulkdatei holen (``download_uri``, ``updated_at``, Größe).

    Das ist **kein** Abfragen einzelner Karten, sondern der von Scryfall
    vorgesehene Weg, an die lokalen Bulkdaten zu kommen.
    """
    import urllib.request

    anfrage = urllib.request.Request(
        BULK_INDEX_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        daten = json.loads(antwort.read().decode("utf-8"))
    for eintrag in daten.get("data", []):
        if eintrag.get("type") == typ:
            return eintrag
    raise RuntimeError(f"Bulkdatei '{typ}' nicht gefunden.")


def _gespeicherter_stand(db_path: Path) -> Optional[str]:
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            zeile = conn.execute(
                "SELECT wert FROM meta WHERE schluessel = 'updated_at'").fetchone()
            return zeile[0] if zeile else None
    except sqlite3.Error:
        return None


def _merke_stand(db_path: Path, stand: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (schluessel TEXT PRIMARY KEY, wert TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES ('updated_at', ?)",
                     (stand,))
        conn.commit()


def aktualisiere_von_scryfall(db_path: Path = DB_PATH, fortschritt: Fortschritt = None,
                              erzwingen: bool = False,
                              vor_tausch: Optional[Callable[[], None]] = None) -> Dict:
    """Bulkdatei direkt von Scryfall streamen und die Datenbank neu aufbauen.

    Die Datei wird nicht zwischengespeichert, sondern im Vorbeifließen
    verarbeitet. Hat sich seit dem letzten Lauf nichts geändert, wird ohne
    Download abgebrochen (``erzwingen=True`` umgeht das).
    """
    import gzip
    import io
    import urllib.request

    db_path = Path(db_path)
    if fortschritt:
        fortschritt(0, "Frage Scryfall nach der aktuellen Bulkdatei …")
    info = bulk_info()
    stand = info.get("updated_at", "")
    vorher = _gespeicherter_stand(db_path)
    if not erzwingen and vorher and vorher == stand and db_path.exists():
        if fortschritt:
            fortschritt(0, "Bereits aktuell – kein Download nötig.")
        return {"aktualisiert": False, "anzahl": 0, "stand": stand}

    # Scryfall liefert die Bulkdaten als gzip-gepacktes JSON Lines
    # (``jsonl_download_uri``, rund 80 MB). Ältere/andere Fassungen der
    # Schnittstelle bieten ein JSON-Array unter ``download_uri`` — beides wird
    # unterstützt, damit ein Wechsel bei Scryfall nichts kaputt macht.
    url = info.get("jsonl_download_uri") or info.get("download_uri")
    if not url:
        raise RuntimeError("Scryfall nennt keine Download-Adresse für die Bulkdatei.")
    jsonl = ".jsonl" in url
    gepackt = url.endswith(".gz")

    if fortschritt:
        groesse = info.get("compressed_size") or info.get("size") or 0
        hinweis = f" ({groesse / 1024 / 1024:.0f} MB)" if groesse else ""
        fortschritt(0, f"Lade Kartendaten{hinweis} …")

    anfrage = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(anfrage, timeout=300) as antwort:
        roh = gzip.GzipFile(fileobj=antwort) if gepackt else antwort
        strom = io.TextIOWrapper(roh, encoding="utf-8")
        anzahl = schreibe_datenbank(iter_karten(strom, jsonl), db_path,
                                    fortschritt, vor_tausch)

    _merke_stand(db_path, stand)
    return {"aktualisiert": True, "anzahl": anzahl, "stand": stand}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    def zeige(_n, text):
        print(text)
    try:
        if "--datei" in argv:
            pfad = Path(argv[argv.index("--datei") + 1])
            anzahl = import_cards(pfad, DB_PATH, zeige)
            print(f"{anzahl} Karten nach {DB_PATH} geschrieben.")
        else:
            ergebnis = aktualisiere_von_scryfall(
                DB_PATH, zeige, erzwingen="--erzwingen" in argv)
            if not ergebnis["aktualisiert"]:
                print("Kartendaten waren bereits aktuell.")
            else:
                print(f"{ergebnis['anzahl']} Karten nach {DB_PATH} geschrieben.")
    except Exception as exc:                      # noqa: BLE001
        print(f"Fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
