"""Import nur über den Kartennamen: nichts raten, nichts aus dem Netz holen.

Zwei Regeln aus CLAUDE.md, gegen die dieser Weg verstieß:

* **„Nie blind raten … niemals still den ersten Treffer nehmen."** Die
  Namenssuche holte mit ``fetchone()`` irgendeine Ausgabe der Karte — bei
  „Lightning Bolt" eine von über hundert. In der Warteschlange stand sie dann
  mit Set, Nummer und Bild, als wäre sie die gemeinte.
* **„Niemals pro Karte die Scryfall-API abfragen."** Fand die lokale Datenbank
  nichts, ging je Karte eine Anfrage an ``api.scryfall.com``.

Betroffen waren die Texteingabe „ein Name pro Zeile" und JSON-Einträge, die
nur einen Namen tragen. Der CSV-Weg war es nie.

Die Tests arbeiten mit einer **echten** lokalen Kartendatenbank und fangen
jeden Netzzugriff ab — ein Stub an der Stelle der Namenssuche hätte genau den
Fehler versteckt.
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

from TCGInventory import build_card_db as bcd                    # noqa: E402
from TCGInventory import web                                      # noqa: E402
import TCGInventory.card_scanner as cs                            # noqa: E402


def _ausgabe(nummer, name, set_code, collector, lang="en"):
    return {"id": f"{nummer:08d}-aaaa-bbbb-cccc-dddddddddddd", "name": name,
            "set": set_code, "set_name": set_code.upper(), "lang": lang,
            "collector_number": collector, "cardmarket_id": 1000 + nummer}


#: Drei Ausgaben derselben Karte, eine Karte mit nur einer Ausgabe.
KARTEN = [
    _ausgabe(1, "Lightning Bolt", "m11", "146"),
    _ausgabe(2, "Lightning Bolt", "lea", "161"),
    _ausgabe(3, "Lightning Bolt", "m10", "146"),
    _ausgabe(4, "Smaug, Wicked Worm", "tho", "217"),
]


class _Netz:
    """Fängt jeden Aufruf von ``requests.get`` ab und merkt ihn sich."""

    def __init__(self):
        self.aufrufe = []

    def __call__(self, url, *args, **kwargs):
        self.aufrufe.append(url)
        raise cs.requests.RequestException("im Test gibt es kein Netz")


@pytest.fixture()
def netz(monkeypatch):
    netz = _Netz()
    monkeypatch.setattr(cs.requests, "get", netz, raising=False)
    return netz


def _zuruecksetzen():
    web.UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS, web.BULK_DONE, web.BULK_MESSAGE = 0, False, None


@pytest.fixture()
def kartendaten(tmp_path, monkeypatch, netz):
    """Lokale Kartendatenbank mit ``KARTEN``; leere Warteschlangen."""
    db = tmp_path / "default-cards.db"
    bcd.schreibe_datenbank(KARTEN, db)
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _zuruecksetzen()
    yield netz
    cs.reset_card_database()
    _zuruecksetzen()


def _text(*namen, ordner=None):
    web._process_bulk_upload({"cards": "\n".join(namen), "folder_id": ordner},
                             None, None)


def _json(eintraege):
    web._process_bulk_upload({"cards": "", "folder_id": None},
                             json.dumps(eintraege).encode("utf-8"), None)


# ---------------------------------------------------------------------------
# Nicht raten
# ---------------------------------------------------------------------------

def test_name_mit_mehreren_ausgaben_wird_nicht_still_aufgeloest(kartendaten):
    """Der Kern: drei Ausgaben, und keine davon darf einfach genommen werden."""
    _text("Lightning Bolt")
    assert web.UPLOAD_QUEUE == []
    assert len(web.NEEDS_REVIEW) == 1
    eintrag = web.NEEDS_REVIEW[0]
    assert eintrag["name"] == "Lightning Bolt"
    assert "3 Ausgaben" in eintrag["reason"]
    assert eintrag["set_code"] == "" and eintrag["collector_number"] == ""


def test_die_kandidaten_werden_zur_auswahl_mitgegeben(kartendaten):
    _text("Lightning Bolt")
    kandidaten = web.NEEDS_REVIEW[0]["kandidaten"]
    assert [(k["set_code"], k["collector_number"]) for k in kandidaten] == \
        [("lea", "161"), ("m10", "146"), ("m11", "146")]


def test_json_eintrag_nur_mit_namen_wird_ebenso_wenig_geraten(kartendaten):
    _json([{"name": "Lightning Bolt", "quantity": 4, "foil": True}])
    assert web.UPLOAD_QUEUE == []
    eintrag = web.NEEDS_REVIEW[0]
    assert "3 Ausgaben" in eintrag["reason"]
    # Was die Datei sagte, bleibt für die spätere Übernahme erhalten.
    assert eintrag["quantity"] == 4 and eintrag["foil"] is True


def test_name_mit_genau_einer_ausgabe_wird_uebernommen(kartendaten):
    """Hier gibt es nichts zu raten — die Identität steht fest."""
    _text("Smaug, Wicked Worm")
    assert web.NEEDS_REVIEW == []
    karte = web.UPLOAD_QUEUE[0]
    assert (karte["set_code"], karte["collector_number"]) == ("tho", "217")
    assert karte["scryfall_id"] == KARTEN[3]["id"]
    assert karte["name"] == "Smaug, Wicked Worm"


def test_schreibweise_des_namens_ist_egal(kartendaten):
    _text("smaug, wicked WORM")
    assert web.UPLOAD_QUEUE[0]["name"] == "Smaug, Wicked Worm"


# ---------------------------------------------------------------------------
# Nichts aus dem Netz
# ---------------------------------------------------------------------------

def test_unbekannter_name_fragt_nicht_bei_scryfall_nach(kartendaten):
    _text("Gibt Es Nicht")
    assert kartendaten.aufrufe == [], "keine Anfrage je Karte an die Scryfall-API"
    assert web.UPLOAD_QUEUE == []
    assert "Kein Treffer" in web.NEEDS_REVIEW[0]["reason"]


def test_ohne_kartendatenbank_wird_das_gesagt_statt_im_netz_gesucht(
        tmp_path, monkeypatch, netz):
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", tmp_path / "fehlt.db")
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _zuruecksetzen()
    try:
        _text("Lightning Bolt")
        assert netz.aufrufe == []
        assert web.UPLOAD_QUEUE == []
        assert "Kartendatenbank fehlt" in web.NEEDS_REVIEW[0]["reason"]
    finally:
        cs.reset_card_database()
        _zuruecksetzen()


def test_ein_ganzer_import_bleibt_ohne_netzzugriff(kartendaten):
    _text("Lightning Bolt", "Smaug, Wicked Worm", "Gibt Es Nicht")
    _json(["Lightning Bolt", {"name": "Gibt Es Auch Nicht"}])
    assert kartendaten.aufrufe == []


def test_der_ordnername_wird_nicht_mehr_als_set_code_eingetragen(kartendaten,
                                                                 monkeypatch):
    """Früher bekam ein Eintrag ohne Set den Namen des Zielordners als
    Set-Code — zusammen mit der Kartennummer irgendeiner Ausgabe."""
    monkeypatch.setattr(web, "list_folders", lambda: [(3, "Mappe 3", 10)])
    _text("Smaug, Wicked Worm", ordner="3")
    karte = web.UPLOAD_QUEUE[0]
    assert karte["set_code"] == "tho"
    assert karte["folder_id"] == "3"                  # das Ziel bleibt das Ziel


# ---------------------------------------------------------------------------
# Needs-Review: die Ausgabe wählen
# ---------------------------------------------------------------------------

def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def test_seite_bietet_die_ausgaben_zur_auswahl_an(kartendaten):
    _text("Lightning Bolt")
    html = _klient().get("/cards/needs_review").get_data(as_text=True)
    assert "Ausgabe wählen" in html
    for wert in ("lea|161", "m10|146", "m11|146"):
        assert f'value="{wert}"' in html
    assert "3 in der Kartendatenbank" in html


def test_gewaehlte_ausgabe_wird_uebernommen(kartendaten):
    """Auch ohne JavaScript: die Auswahl genügt, die Felder dürfen leer sein."""
    _json([{"name": "Lightning Bolt", "quantity": 4, "foil": True,
            "condition": "NearMint", "language": "German"}])
    antwort = _klient().post("/cards/needs_review/retry/0", data={
        "kandidat": "m10|146", "set_code": "", "collector_number": "",
        "language": "de", "folder_id": ""}, follow_redirects=True)
    assert "aufgelöst" in antwort.get_data(as_text=True)
    assert web.NEEDS_REVIEW == []
    karte = web.UPLOAD_QUEUE[0]
    assert (karte["set_code"], karte["collector_number"]) == ("m10", "146")
    assert karte["scryfall_id"] == KARTEN[2]["id"]
    # Die Angaben aus der Datei haben den Umweg überstanden.
    assert karte["quantity"] == 4 and karte["foil"] is True
    assert karte["condition"] == "NM" and karte["language"] == "de"
    assert "kandidaten" not in karte


def test_von_hand_eingetragenes_hat_vorrang_vor_der_auswahl(kartendaten):
    _text("Lightning Bolt")
    _klient().post("/cards/needs_review/retry/0", data={
        "kandidat": "m10|146", "set_code": "LEA", "collector_number": "161",
        "language": "", "folder_id": ""})
    assert web.UPLOAD_QUEUE[0]["set_code"] == "lea"


def test_ohne_auswahl_und_ohne_angaben_bleibt_der_eintrag_stehen(kartendaten):
    _text("Lightning Bolt")
    _klient().post("/cards/needs_review/retry/0", data={
        "kandidat": "", "set_code": "", "collector_number": "",
        "language": "", "folder_id": ""})
    assert web.UPLOAD_QUEUE == []
    assert len(web.NEEDS_REVIEW) == 1
    assert web.NEEDS_REVIEW[0]["kandidaten"], "die Auswahl bleibt erhalten"


def test_unbekannter_name_laesst_sich_von_hand_aufloesen(kartendaten):
    """Kein Treffer über den Namen — Set und Nummer nachtragen genügt."""
    _text("Blitzschlag")                              # deutscher Kartenname
    eintrag = web.NEEDS_REVIEW[0]
    assert eintrag["kandidaten"] == [] and eintrag["kandidaten_gesamt"] == 0
    _klient().post("/cards/needs_review/retry/0", data={
        "set_code": "m11", "collector_number": "146", "language": "de",
        "folder_id": ""})
    karte = web.UPLOAD_QUEUE[0]
    assert karte["scryfall_id"] == KARTEN[0]["id"] and karte["language"] == "de"


def test_sehr_viele_ausgaben_werden_begrenzt_und_das_wird_gesagt(
        tmp_path, monkeypatch, netz):
    viele = [_ausgabe(100 + i, "Forest", f"s{i:03d}", str(i)) for i in range(200)]
    db = tmp_path / "viele.db"
    bcd.schreibe_datenbank(viele, db)
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _zuruecksetzen()
    try:
        _text("Forest")
        eintrag = web.NEEDS_REVIEW[0]
        assert "200 Ausgaben" in eintrag["reason"]
        assert len(eintrag["kandidaten"]) == web.MAX_KANDIDATEN
        assert eintrag["kandidaten_gesamt"] == 200
        html = _klient().get("/cards/needs_review").get_data(as_text=True)
        assert f"die ersten {web.MAX_KANDIDATEN}" in html
        assert "von Hand eintragen" in html
    finally:
        cs.reset_card_database()
        _zuruecksetzen()


def test_kandidaten_sind_nach_set_und_nummer_sortiert(tmp_path, monkeypatch, netz):
    """Nummern als Zahlen: 2 vor 10, nicht „10" vor „2"."""
    karten = [_ausgabe(1, "Plains", "abc", "10"), _ausgabe(2, "Plains", "abc", "2"),
              _ausgabe(3, "Plains", "aaa", "300")]
    db = tmp_path / "sortiert.db"
    bcd.schreibe_datenbank(karten, db)
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _zuruecksetzen()
    try:
        _text("Plains")
        assert [(k["set_code"], k["collector_number"])
                for k in web.NEEDS_REVIEW[0]["kandidaten"]] == \
            [("aaa", "300"), ("abc", "2"), ("abc", "10")]
    finally:
        cs.reset_card_database()
        _zuruecksetzen()


# ---------------------------------------------------------------------------
# Die Namenssuche selbst gibt es nicht mehr
# ---------------------------------------------------------------------------

def test_die_ratende_namenssuche_ist_entfernt():
    """``fetch_card_info_by_name`` lieferte *eine* Karte zu einem Namen — das
    ist ohne Raten nicht möglich. Wer Ausgaben braucht, bekommt alle."""
    assert not hasattr(cs, "fetch_card_info_by_name")
    assert not hasattr(web, "fetch_card_info_by_name")


def test_alle_ausgaben_eines_namens_kommen_ohne_netz(kartendaten):
    ausgaben = cs.fetch_variants("Lightning Bolt")
    assert sorted(a["set_code"] for a in ausgaben) == ["lea", "m10", "m11"]
    assert cs.fetch_variants("Gibt Es Nicht") == []
    assert kartendaten.aufrufe == []
