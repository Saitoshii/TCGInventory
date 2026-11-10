"""Cardmarket-Produktkatalog lokal vorhalten (Displays, Precons, Zubehör).

Einzelkarten „rasten ein", weil die Scryfall-Daten lokal vorliegen. Für
versiegelte Ware und Zubehör gab es keine solche Quelle — sie mussten von Hand
benannt werden, samt dem Namen, unter dem Cardmarket sie später in der
Bestellmail führt.

Cardmarket stellt seinen Produktkatalog öffentlich zum Herunterladen bereit.
Das ist **kein** API-Zugang: es sind zwei gewöhnliche JSON-Dateien, ohne
Anmeldung abrufbar.

==========================  ==================  ==========================
Datei                       Inhalt              Größenordnung
==========================  ==================  ==========================
``products_nonsingles_1``   Magic, versiegelt   rund 5.000 Einträge, 1 MB
``products_accessories``    Zubehör             rund 20.000 Einträge, 4 MB
==========================  ==================  ==========================

Die Einzelkarten (``products_singles_1``, 20 MB) werden bewusst **nicht**
geladen — für sie ist und bleibt Scryfall die Quelle.

Je Produkt liefert der Katalog eine Nummer (``idProduct``), den Namen und eine
Kategorie. **Keine Sprache und keinen Preis**: die Sprache wählt weiterhin der
Mensch am Artikel, und Preise sind hier ohnehin keine Finanzquelle.

Wie bei den Kartendaten gilt: erst eine temporäre Datenbank aufbauen, dann
atomar tauschen. Bricht der Lauf ab oder liefert Cardmarket etwas
Unbrauchbares, bleibt der bisherige Katalog unangetastet in Betrieb.

Die Dateien sind klein genug, um sie ganz zu lesen. Damit das so bleibt, gibt
es eine Obergrenze (``MAX_BYTES``) — eine unerwartet riesige Antwort wird
abgelehnt, statt den Pi zu füllen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "cardmarket-products.db"

BASIS_URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/"

#: Allgemeine Kennung ohne persönliche Angaben.
USER_AGENT = "TCGInventory/1.0 (Produktkatalog-Aktualisierung)"

#: Obergrenze je Datei. Die größte ist derzeit knapp 4 MB.
MAX_BYTES = 40 * 1024 * 1024

#: Weniger Einträge als das gelten als kaputte Lieferung, nicht als Katalog.
MINDESTANZAHL = 100

#: So oft wird von selbst nachgesehen, ob Cardmarket eine neue Fassung hat.
PRUEFABSTAND = timedelta(hours=24)


class Quelle(NamedTuple):
    schluessel: str          # steht je Produkt in der Datenbank
    datei: str               # Dateiname bei Cardmarket
    item_type: str           # Artikelart im Bestand, wenn die Kategorie nichts anderes sagt
    bezeichnung: str         # für die Anzeige


QUELLEN: Tuple[Quelle, ...] = (
    Quelle("nonsingles", "products_nonsingles_1.json", "display",
           "Versiegelte Magic-Produkte"),
    Quelle("accessories", "products_accessories.json", "zubehoer", "Zubehör"),
)

#: Kategorien aus der Magic-Datei, die keine versiegelte Ware sind.
SONSTIGE_KATEGORIEN = {"Magic Event Tickets", "Magic Lot", "Magic Miscellaneous"}

Fortschritt = Optional[Callable[[str], None]]
#: ``hole(url, etag) -> (status, daten, etag)``; 304 heißt „unverändert".
Holer = Callable[[str, Optional[str]], Tuple[int, Optional[bytes], Optional[str]]]


class KatalogFehler(RuntimeError):
    """Der Katalog ließ sich nicht laden oder ist unbrauchbar."""


# ---------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------
def bereinige_name(name) -> str:
    """Produktname, wie er am Artikel stehen soll.

    Der Katalog schreibt Anführungszeichen doppelt (``""Turtle Power!""``) —
    ein Überbleibsel aus Cardmarkets Export. Gemeint ist ein einzelnes. Dazu
    kommen bei einigen Einträgen Leerzeichen am Rand und doppelte Leerzeichen.
    """
    text = str(name or "")
    while '""' in text:
        text = text.replace('""', '"')
    return " ".join(text.split())


def artikelart(quelle: Quelle, kategorie: str) -> str:
    """Welche Artikelart im Bestand zu einem Katalogeintrag passt."""
    if (kategorie or "").strip() in SONSTIGE_KATEGORIEN:
        return "sonstiges"
    return quelle.item_type


# ---------------------------------------------------------------------------
# Laden und Lesen
# ---------------------------------------------------------------------------
def hole_von_cardmarket(url: str, etag: Optional[str] = None
                        ) -> Tuple[int, Optional[bytes], Optional[str]]:
    """Eine Katalogdatei holen. ``304`` heißt: seit ``etag`` unverändert."""
    import urllib.error
    import urllib.request

    kopf = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if etag:
        kopf["If-None-Match"] = etag
    anfrage = urllib.request.Request(url, headers=kopf)
    try:
        with urllib.request.urlopen(anfrage, timeout=60) as antwort:
            daten = antwort.read(MAX_BYTES + 1)
            if len(daten) > MAX_BYTES:
                raise KatalogFehler(
                    f"Die Katalogdatei ist größer als erwartet "
                    f"(über {MAX_BYTES // 1024 // 1024} MB) — nicht übernommen.")
            return antwort.status, daten, antwort.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, None, etag
        raise KatalogFehler(f"Cardmarket antwortet mit {exc.code} für {url}.") from exc
    except urllib.error.URLError as exc:
        raise KatalogFehler(f"Cardmarket ist nicht erreichbar: {exc.reason}") from exc


def lies_produkte(roh: bytes, quelle: Quelle) -> Tuple[str, List[Tuple]]:
    """Katalogdatei in Zeilen für die Datenbank übersetzen.

    Gibt ``(stand, zeilen)`` zurück. Einträge ohne Nummer oder Namen werden
    übersprungen. Eine Datei, die gar nicht nach Katalog aussieht, ist ein
    Fehler — sie darf den vorhandenen Katalog nicht ersetzen.
    """
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise KatalogFehler(
            f"{quelle.datei}: keine lesbare JSON-Datei ({exc}).") from exc
    produkte = daten.get("products") if isinstance(daten, dict) else None
    if not isinstance(produkte, list):
        raise KatalogFehler(f"{quelle.datei}: die Produktliste fehlt.")

    zeilen: List[Tuple] = []
    for eintrag in produkte:
        if not isinstance(eintrag, dict):
            continue
        nummer = eintrag.get("idProduct")
        name = bereinige_name(eintrag.get("name"))
        if not isinstance(nummer, int) or isinstance(nummer, bool) or not name:
            continue
        kategorie = str(eintrag.get("categoryName") or "").strip()
        zeilen.append((
            nummer, name, name.casefold(), kategorie,
            eintrag.get("idExpansion") if isinstance(eintrag.get("idExpansion"), int) else None,
            str(eintrag.get("dateAdded") or "")[:10],
            quelle.schluessel, artikelart(quelle, kategorie),
        ))
    if len(zeilen) < MINDESTANZAHL:
        raise KatalogFehler(
            f"{quelle.datei}: nur {len(zeilen)} brauchbare Einträge — das ist "
            f"kein vollständiger Katalog.")
    return str(daten.get("createdAt") or ""), zeilen


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------
def _lies_meta(db_path: Path) -> Dict[str, str]:
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            return dict(conn.execute("SELECT schluessel, wert FROM meta"))
    except sqlite3.Error:
        return {}


def _setze_meta(db_path: Path, werte: Dict[str, str]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta "
                     "(schluessel TEXT PRIMARY KEY, wert TEXT)")
        conn.executemany(
            "INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)",
            list(werte.items()))
        conn.commit()


def schreibe_datenbank(zeilen: Iterable[Tuple], meta: Dict[str, str],
                       db_path: Path) -> int:
    """Katalog in eine neue Datei schreiben und atomar gegen die alte tauschen."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".neu")
    if tmp.exists():
        tmp.unlink()
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.execute(
                "CREATE TABLE produkte ("
                " id_product INTEGER PRIMARY KEY,"
                " name TEXT NOT NULL,"
                " suchname TEXT NOT NULL,"
                " kategorie TEXT,"
                " expansion_id INTEGER,"
                " hinzugefuegt TEXT,"
                " quelle TEXT NOT NULL,"
                " item_type TEXT NOT NULL)")
            conn.execute("CREATE TABLE meta (schluessel TEXT PRIMARY KEY, wert TEXT)")
            # Dieselbe Nummer in beiden Dateien wäre ein Fehler bei Cardmarket;
            # die Anwendung soll daran nicht scheitern.
            conn.executemany(
                "INSERT OR IGNORE INTO produkte VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                zeilen)
            conn.execute("CREATE INDEX idx_produkte_suchname ON produkte(suchname)")
            conn.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
            anzahl = conn.execute("SELECT COUNT(*) FROM produkte").fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, db_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return anzahl


def aktualisiere(db_path: Path = DB_PATH, erzwingen: bool = False,
                 fortschritt: Fortschritt = None,
                 hole: Optional[Holer] = None) -> Dict:
    """Den Katalog von Cardmarket holen, wenn es eine neue Fassung gibt.

    Je Datei wird zuerst gefragt, ob sie sich geändert hat (``ETag``). Sind
    beide unverändert, wird nichts geladen und nur der Prüfzeitpunkt vermerkt.
    Hat sich eine geändert, werden beide frisch geholt und der Katalog als
    Ganzes neu geschrieben — bei zusammen unter 5 MB ist das einfacher und
    sicherer, als zwei Hälften unterschiedlichen Alters zusammenzusetzen.

    Scheitert der Lauf, bleibt die bisherige Datenbank stehen.
    """
    hole = hole or hole_von_cardmarket
    db_path = Path(db_path)
    jetzt = datetime.now().isoformat(timespec="seconds")
    meta = _lies_meta(db_path) if db_path.exists() else {}

    def melde(text: str) -> None:
        if fortschritt:
            fortschritt(text)

    if meta and not erzwingen:
        melde("Frage Cardmarket, ob es eine neue Fassung gibt …")
        unveraendert = True
        for quelle in QUELLEN:
            etag = meta.get(f"etag_{quelle.schluessel}")
            if not etag:
                unveraendert = False
                break
            status, _, _ = hole(BASIS_URL + quelle.datei, etag)
            if status != 304:
                unveraendert = False
                break
        if unveraendert:
            _setze_meta(db_path, {"geprueft_am": jetzt})
            melde("Bereits aktuell – kein Download nötig.")
            return {"aktualisiert": False, "anzahl": anzahl_produkte(db_path),
                    "stand": meta.get("stand", "")}

    alle: List[Tuple] = []
    neu_meta: Dict[str, str] = {"geprueft_am": jetzt, "geladen_am": jetzt}
    staende: List[str] = []
    for quelle in QUELLEN:
        melde(f"Lade {quelle.bezeichnung} …")
        status, daten, etag = hole(BASIS_URL + quelle.datei, None)
        if status != 200 or not daten:
            raise KatalogFehler(
                f"{quelle.datei}: unerwartete Antwort ({status}).")
        stand, zeilen = lies_produkte(daten, quelle)
        alle.extend(zeilen)
        neu_meta[f"anzahl_{quelle.schluessel}"] = str(len(zeilen))
        if etag:
            neu_meta[f"etag_{quelle.schluessel}"] = etag
        if stand:
            staende.append(stand)
    # Der Katalog ist so alt wie seine älteste Hälfte.
    neu_meta["stand"] = min(staende) if staende else ""

    anzahl = schreibe_datenbank(alle, neu_meta, db_path)
    melde(f"Fertig – {anzahl} Produkte übernommen.")
    return {"aktualisiert": True, "anzahl": anzahl, "stand": neu_meta["stand"]}


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------
def anzahl_produkte(db_path: Path = DB_PATH) -> int:
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM produkte").fetchone()[0]
    except sqlite3.Error:
        return 0


def info(db_path: Path = DB_PATH) -> Dict:
    """Stand des lokalen Katalogs für die Anzeige."""
    db_path = Path(db_path)
    ergebnis = {"vorhanden": False, "anzahl": 0, "stand": "", "geprueft_am": "",
                "geladen_am": "", "groesse_mb": 0.0, "quellen": []}
    if not db_path.exists():
        return ergebnis
    meta = _lies_meta(db_path)
    ergebnis.update({
        "vorhanden": True,
        "anzahl": anzahl_produkte(db_path),
        "stand": meta.get("stand", ""),
        "geprueft_am": meta.get("geprueft_am", ""),
        "geladen_am": meta.get("geladen_am", ""),
        "groesse_mb": round(db_path.stat().st_size / 1024 / 1024, 1),
        "quellen": [
            {"bezeichnung": q.bezeichnung,
             "anzahl": int(meta.get(f"anzahl_{q.schluessel}", "0") or 0)}
            for q in QUELLEN],
    })
    return ergebnis


def _like(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


def _zeile(z: sqlite3.Row) -> Dict:
    return {"id_product": z["id_product"], "name": z["name"],
            "kategorie": z["kategorie"] or "", "item_type": z["item_type"],
            "hinzugefuegt": z["hinzugefuegt"] or ""}


def suche(text: str, limit: int = 8, db_path: Path = DB_PATH) -> List[Dict]:
    """Produkte, deren Name **alle** Wörter der Eingabe enthält.

    „hobbit play box" findet „The Hobbit Play Booster Box". Die Reihenfolge:

    1. Versiegelte Ware vor Zubehör. Der Katalog enthält viermal so viel
       Zubehör wie Magic-Produkte; ohne diese Regel verdrängten bei
       „lorwyn eclipsed box" acht Deckboxen das gesuchte Booster-Display aus
       der Liste. Wer Zubehör sucht, tippt ohnehin Wörter, die nur dort
       vorkommen („playmat", „sleeves").
    2. Namen, die mit der Eingabe beginnen.
    3. Die neuesten Produkte zuerst — wer etwas einlagert, hat meist
       Aktuelles in der Hand.

    Rein lesend. Fehlt der Katalog, ist das Ergebnis leer.
    """
    woerter = [w for w in bereinige_name(text).casefold().split() if w]
    if not woerter or not Path(db_path).exists():
        return []
    bedingung = " AND ".join(["suchname LIKE ? ESCAPE '\\'"] * len(woerter))
    werte: List = [f"%{_like(w)}%" for w in woerter]
    anfang = _like(" ".join(woerter)) + "%"
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            zeilen = conn.execute(
                f"SELECT * FROM produkte WHERE {bedingung} "
                "ORDER BY (quelle = ?) DESC, "
                "(suchname LIKE ? ESCAPE '\\') DESC, "
                "hinzugefuegt DESC, LENGTH(name), name LIMIT ?",
                werte + [QUELLEN[0].schluessel, anfang, int(limit)]).fetchall()
    except sqlite3.Error:
        return []
    return [_zeile(z) for z in zeilen]


def finde_genau(name: str, db_path: Path = DB_PATH) -> List[Dict]:
    """Alle Katalogeinträge mit genau diesem Namen (Schreibweise egal).

    Fast immer ist das einer. Wenige Namen gibt es doppelt — dann kommen
    mehrere zurück, und der Aufrufer darf sich keinen aussuchen.
    """
    schluessel = bereinige_name(name).casefold()
    if not schluessel or not Path(db_path).exists():
        return []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            zeilen = conn.execute(
                "SELECT * FROM produkte WHERE suchname = ? ORDER BY id_product",
                (schluessel,)).fetchall()
    except sqlite3.Error:
        return []
    return [_zeile(z) for z in zeilen]


# ---------------------------------------------------------------------------
# Von selbst aktuell halten
# ---------------------------------------------------------------------------
def faellig(db_path: Path = DB_PATH, jetzt: Optional[datetime] = None) -> bool:
    """Ist es Zeit, wieder bei Cardmarket nachzusehen?"""
    db_path = Path(db_path)
    if not db_path.exists():
        return True
    zuletzt = _lies_meta(db_path).get("geprueft_am", "")
    try:
        return (jetzt or datetime.now()) - datetime.fromisoformat(zuletzt) >= PRUEFABSTAND
    except ValueError:
        return True


#: Was der letzte selbsttätige Lauf ergeben hat — für die Anzeige.
WAECHTER_STATUS: Dict = {"zuletzt": "", "meldung": "", "fehler": False}


class KatalogWaechter:
    """Hält den Katalog im Hintergrund aktuell.

    Einmal am Tag wird bei Cardmarket nachgefragt; geladen wird nur, wenn es
    eine neue Fassung gibt. Fehler (Pi ohne Netz, Cardmarket nicht erreichbar)
    werden vermerkt und beim nächsten Durchgang erneut versucht — die
    Anwendung läuft mit dem bisherigen Katalog weiter.
    """

    #: Abstand zwischen zwei Blicken auf die Uhr, in Sekunden.
    TAKT = 3600
    #: Wartezeit nach dem Start, damit der Dienst erst einmal hochkommt.
    ANLAUF = 90

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self._stopp = threading.Event()
        self._faden: Optional[threading.Thread] = None

    def durchgang(self) -> Optional[Dict]:
        """Ein Durchgang: prüfen, ob fällig, und gegebenenfalls aktualisieren."""
        if not faellig(self.db_path):
            return None
        WAECHTER_STATUS["zuletzt"] = datetime.now().isoformat(timespec="seconds")
        try:
            ergebnis = aktualisiere(self.db_path)
            WAECHTER_STATUS.update({
                "fehler": False,
                "meldung": (f"{ergebnis['anzahl']} Produkte übernommen."
                            if ergebnis["aktualisiert"] else "Bereits aktuell."),
            })
            return ergebnis
        except Exception as exc:                       # noqa: BLE001
            WAECHTER_STATUS.update({"fehler": True, "meldung": str(exc)})
            print(f"⚠️  Cardmarket-Katalog nicht aktualisiert: {exc}")
            return None

    def _laufe(self) -> None:
        if self._stopp.wait(self.ANLAUF):
            return
        while not self._stopp.is_set():
            self.durchgang()
            self._stopp.wait(self.TAKT)

    def start(self) -> None:
        if self._faden and self._faden.is_alive():
            return
        self._stopp.clear()
        self._faden = threading.Thread(target=self._laufe, daemon=True,
                                       name="cardmarket-katalog")
        self._faden.start()

    def stop(self) -> None:
        self._stopp.set()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        ergebnis = aktualisiere(DB_PATH, erzwingen="--erzwingen" in argv,
                                fortschritt=print)
        if not ergebnis["aktualisiert"]:
            print("Der Katalog war bereits aktuell.")
    except Exception as exc:                          # noqa: BLE001
        print(f"Fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
