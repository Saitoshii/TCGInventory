"""JSON-Upload im Bulk-Import.

Der gemeldete Fehler: jede JSON-Datei mit mindestens einem Eintrag wurde mit
„Invalid JSON file" abgewiesen — und zwar auch dann, wenn sie völlig in Ordnung
war. In ``bulk_add_view`` stand ein alter Block, der die Datei schon in der
Anfrage verarbeiten wollte und dabei Namen benutzte, die es dort nie gab
(``folder_id``, ``set_code``). Der ``NameError`` wurde abgefangen und als
kaputte Datei gemeldet. Weil der Block den Datenstrom bereits gelesen hatte,
bekam auch der eigentliche Hintergrundlauf nichts mehr.

Die Tests gehen deshalb durch die **Route** und nicht nur durch
``_process_bulk_upload`` — genau dort saß der Fehler.
"""

import io
import json
import os
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import web                                      # noqa: E402


class _Sofort:
    """Ersatz für ``threading.Thread``: läuft beim Start gleich durch."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._ziel, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._ziel(*self._args, **self._kwargs)


def _identitaet(set_code, collector_number, language=None):
    """Wie die lokale Scryfall-Datenbank: jede (Set, Nummer) löst auf."""
    return {
        "name": f"Karte {set_code.upper()} {collector_number}",
        "set_code": set_code, "collector_number": collector_number,
        "language": language or "en",
        "scryfall_id": f"sc-{set_code}-{collector_number}",
        "cardmarket_id": f"cm-{collector_number}",
        "image_url": f"http://img/{collector_number}",
    }


@pytest.fixture()
def umgebung(monkeypatch):
    """Leere Warteschlangen, kein Netz, Hintergrundlauf läuft sofort."""
    web.UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS, web.BULK_DONE, web.BULK_MESSAGE = 0, False, None
    aufrufe = {"name": [], "variante": []}

    def nach_name(name):
        aufrufe["name"].append(name)
        return {"name": name, "set_code": "m11", "language": "en",
                "cardmarket_id": "cm-1", "collector_number": "146",
                "scryfall_id": "sc-name", "image_url": "http://img/name"}

    def variante(name, set_code, collector_number=None):
        aufrufe["variante"].append((name, set_code, collector_number))
        return None

    monkeypatch.setattr(web, "fetch_card_info_by_name", nach_name)
    monkeypatch.setattr(web, "find_variant", variante, raising=False)
    monkeypatch.setattr(web, "find_by_identity", _identitaet)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    monkeypatch.setattr(web.threading, "Thread", _Sofort)
    yield aufrufe
    web.UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS, web.BULK_DONE, web.BULK_MESSAGE = 0, False, None


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _lade_hoch(inhalt, csv=None, karten=""):
    """Die Datei so schicken, wie es das Formular tut."""
    roh = inhalt if isinstance(inhalt, bytes) else json.dumps(inhalt).encode("utf-8")
    daten = {"folder_id": "", "cards": karten,
             "json_file": (io.BytesIO(roh), "karten.json")}
    if csv is not None:
        daten["csv_file"] = (io.BytesIO(csv.encode("utf-8")), "karten.csv")
    antwort = _klient().post("/cards/bulk_add", data=daten,
                             content_type="multipart/form-data")
    return antwort, antwort.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Der Fehler selbst
# ---------------------------------------------------------------------------

def test_liste_von_namen_landet_in_der_warteschlange(umgebung):
    antwort, seite = _lade_hoch(["Lightning Bolt", "Sol Ring"])
    assert antwort.status_code == 200
    assert "Invalid JSON" not in seite
    assert [k["name"] for k in web.UPLOAD_QUEUE] == ["Lightning Bolt", "Sol Ring"]


def test_objekte_mit_namen_landen_in_der_warteschlange(umgebung):
    _, seite = _lade_hoch([{"name": "Lightning Bolt"}])
    assert "Invalid JSON" not in seite
    assert [k["name"] for k in web.UPLOAD_QUEUE] == ["Lightning Bolt"]


# ---------------------------------------------------------------------------
# Einträge mit Identität gehen den Weg einer CSV-Zeile
# ---------------------------------------------------------------------------

def test_set_und_nummer_werden_ueber_die_identitaet_aufgeloest(umgebung):
    """Kein zweiter Codepfad: Set, Nummer und Sprache wie beim CSV-Import."""
    _lade_hoch([{"name": "Ezio, Brash Novice", "set_code": "ACR",
                 "collector_number": "56", "language": "English",
                 "condition": "NearMint", "quantity": 3, "foil": True}])
    assert web.NEEDS_REVIEW == []
    assert len(web.UPLOAD_QUEUE) == 1
    karte = web.UPLOAD_QUEUE[0]
    assert karte["set_code"] == "acr"                 # auf Scryfall-Schreibweise
    assert karte["collector_number"] == "56"
    assert karte["scryfall_id"] == "sc-acr-56"
    assert karte["language"] == "en"
    assert karte["condition"] == "NM"
    assert karte["quantity"] == 3
    assert karte["foil"] is True


def test_mit_identitaet_wird_nicht_ueber_den_namen_gesucht(umgebung):
    """Die Suche nach dem bloßen Namen nimmt irgendeine Ausgabe. Wer Set und
    Nummer angibt, hat eine bestimmte gemeint."""
    _lade_hoch([{"name": "Lightning Bolt", "set_code": "m11",
                 "collector_number": "146"}])
    assert umgebung["name"] == []
    assert web.UPLOAD_QUEUE[0]["scryfall_id"] == "sc-m11-146"


def test_csv_spaltennamen_gelten_auch_in_json(umgebung):
    """Wer eine CSV-Zeile als Objekt schreibt, bekommt dasselbe Ergebnis."""
    _lade_hoch([{"Card Name": "Sol Ring", "Set Code": "CMR", "Card Number": "472",
                 "Printing": "Foil", "Quantity": "2"}])
    karte = web.UPLOAD_QUEUE[0]
    assert (karte["set_code"], karte["collector_number"]) == ("cmr", "472")
    assert karte["foil"] is True and karte["quantity"] == 2


def test_set_ohne_nummer_geht_in_needs_review(umgebung):
    """Ein Set hat oft mehrere Ausgaben derselben Karte. Früher wurde hier die
    erste genommen — jetzt entscheidet der Mensch."""
    _lade_hoch([{"name": "Lightning Bolt", "set_code": "m11"}])
    assert web.UPLOAD_QUEUE == []
    assert len(web.NEEDS_REVIEW) == 1
    assert "Kartennummer fehlt" in web.NEEDS_REVIEW[0]["reason"]
    assert umgebung["name"] == [], "auch dann nicht über den Namen raten"


def test_unbekannte_identitaet_geht_in_needs_review(umgebung, monkeypatch):
    monkeypatch.setattr(web, "find_by_identity", lambda *a, **k: None)
    _lade_hoch([{"name": "Mystery", "set_code": "zzz", "collector_number": "9"}])
    assert web.UPLOAD_QUEUE == []
    assert "Kein Scryfall-Treffer" in web.NEEDS_REVIEW[0]["reason"]


# ---------------------------------------------------------------------------
# Einträge nur mit Namen: mitgegebene Angaben bleiben erhalten
# ---------------------------------------------------------------------------

def test_angaben_neben_dem_namen_werden_nicht_verworfen(umgebung):
    _lade_hoch([{"name": "Lightning Bolt", "quantity": 4, "foil": "yes",
                 "language": "German", "condition": "Excellent", "price": "1,50"}])
    karte = web.UPLOAD_QUEUE[0]
    assert karte["name"] == "Lightning Bolt"
    assert karte["quantity"] == 4
    assert karte["foil"] is True
    assert karte["language"] == "de"
    assert karte["condition"] == "EX"
    assert karte["price"] == 1.5


@pytest.mark.parametrize("foil,erwartet", [
    (True, True), ("true", True), ("Foil", True), (1, True), ("ja", True),
    (False, False), ("", False), ("Normal", False), (0, False), (None, False),
])
def test_foil_schreibweisen(umgebung, foil, erwartet):
    _lade_hoch([{"name": "Lightning Bolt", "foil": foil}])
    assert web.UPLOAD_QUEUE[0]["foil"] is erwartet


def test_name_ohne_weitere_angaben_bekommt_die_ueblichen_vorgaben(umgebung):
    _lade_hoch(["Lightning Bolt"])
    karte = web.UPLOAD_QUEUE[0]
    assert (karte["quantity"], karte["foil"], karte["condition"]) == (1, False, "")
    assert karte["language"] == "en"                  # aus der Kartensuche


# ---------------------------------------------------------------------------
# Was nicht verarbeitet werden kann, wird gesagt
# ---------------------------------------------------------------------------

def test_unlesbare_datei_wird_gemeldet_und_reisst_nichts_mit(umgebung):
    """Die kaputte JSON-Datei darf die CSV im selben Upload nicht verhindern."""
    _lade_hoch(b"{ das ist kein JSON",
               csv="Card Name,Set Code,Card Number\nSol Ring,CMR,472\n",
               karten="Lightning Bolt")
    assert "JSON-Datei ist nicht lesbar" in web.BULK_MESSAGE
    assert sorted(k["collector_number"] for k in web.UPLOAD_QUEUE) == ["146", "472"]


def test_objekt_statt_liste_wird_gemeldet(umgebung):
    _lade_hoch({"name": "Lightning Bolt"})
    assert "muss eine Liste enthalten" in web.BULK_MESSAGE
    assert web.UPLOAD_QUEUE == []


def test_eintraege_ohne_namen_werden_gezaehlt_statt_verschwiegen(umgebung):
    _lade_hoch(["Lightning Bolt", "", 42, None, {"quantity": 3}, ["Liste"],
                {"name": "  "}])
    assert [k["name"] for k in web.UPLOAD_QUEUE] == ["Lightning Bolt"]
    assert "6 JSON-Eintrag/Einträge ohne Namen übersprungen" in web.BULK_MESSAGE


def test_leere_liste_ist_kein_fehler(umgebung):
    _lade_hoch([])
    assert web.BULK_MESSAGE == "Keine Karten hinzugefügt."


def test_datei_mit_bom_wird_gelesen(umgebung):
    _lade_hoch(b"\xef\xbb\xbf" + json.dumps(["Sol Ring"]).encode("utf-8"))
    assert [k["name"] for k in web.UPLOAD_QUEUE] == ["Sol Ring"]


def test_umlaute_im_namen(umgebung):
    _lade_hoch(json.dumps(["Jötun Grunt"], ensure_ascii=False).encode("utf-8"))
    assert [k["name"] for k in web.UPLOAD_QUEUE] == ["Jötun Grunt"]


def test_lesefehler_der_csv_wird_nicht_mehr_ueberschrieben(umgebung, monkeypatch):
    """Derselbe Mangel an derselben Stelle: die Abschlussmeldung überschrieb
    jeden Hinweis, der vorher gesetzt wurde."""
    def kaputt(_):
        raise ValueError("CSV parsing failed: Zeile 3")
    monkeypatch.setattr(web, "_parse_csv_bytes", kaputt)
    _lade_hoch(["Sol Ring"], csv="egal")
    assert "CSV parsing failed: Zeile 3" in web.BULK_MESSAGE
    assert "Warteschlange" in web.BULK_MESSAGE         # die JSON kam trotzdem an


def test_meldung_erreicht_die_oberflaeche(umgebung):
    """Die Fortschrittsabfrage reicht die Meldung an die nächste Seite weiter."""
    _lade_hoch({"keine": "Liste"})
    klient = _klient()
    assert klient.get("/cards/bulk_add/progress").get_json()["done"] is True
    seite = klient.get("/cards/upload_queue").get_data(as_text=True)
    assert "muss eine Liste enthalten" in seite
