"""Ein Scan-App-Export von A bis Z: lesen, auflösen, einordnen.

Anlass war eine echte Datei — 250 Zeilen, 400 Karten eines Sets, das in
derselben Woche erschienen ist. Gegen die echten Scryfall-Daten lief sie
sauber durch. Drei Dinge wären im Betrieb trotzdem schiefgegangen, und zwar
alle drei **ohne Meldung**:

1. Die App exportiert als ``.txt``. Das Formular bot im Dateidialog nur
   ``.csv`` an.
2. Hat der Zielordner zu wenig freie Plätze (oder wurde keiner gewählt),
   kamen die Karten ohne Lagerplatz in den Bestand. Die Meldung lautete
   trotzdem „Alle Karten aus der Warteschlange übernommen".
3. Sind die Kartendaten älter als das Set, landet **jede** Zeile in
   Needs-Review — mit 250 gleichlautenden Hinweisen und ohne zu sagen, dass
   schlicht das Set fehlt. Danach half nur: jede Zeile einzeln erneut
   versuchen.

Die Beispielzeilen unten stammen aus dem Export und decken ab, was daran
heikel ist: Namen mit Komma und Apostroph, Foil und Normal derselben Karte,
derselbe Name unter zwei Nummern, ein zweites Set, Mengen über eins.
"""

import io
import os
import sqlite3
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory                                               # noqa: E402
from TCGInventory import (auth, build_card_db as bcd,             # noqa: E402
                          lager_manager, order_service, setup_db, web)
import TCGInventory.card_scanner as cs                            # noqa: E402

KOPF = ("List Type,List Name,Collection,Format,Board,Quantity,Card Name,Set Code,"
        "Set Name,Card Number,Condition,Printing,Rarity,Language,Price Bought,"
        "Date Bought,Parent List Type,Parent List Name,"
        "Current Price (tcgplayer_marketsellprice),List Cover Image,"
        "Parent List Cover Image")

#: (Menge, Name wie in der Datei, Set, Setname, Nummer, Printing, Seltenheit,
#:  Preis, Kaufdatum, Marktpreis)
ZEILEN = [
    (1, "Academy Instructor", "TRK", "Star Trek", "137", "Normal", "uncommon", "0.20", "2026-11-10", "0.20"),
    (1, "Assault Drone", "TRK", "Star Trek", "92", "Foil", "common", "0.26", "2026-11-11", "0.26"),
    (2, "Assault Drone", "TRK", "Star Trek", "92", "Normal", "common", "0.22", "2026-11-10", "0.22"),
    (1, '"Bat\'leth"', "TRK", "Star Trek", "94", "Normal", "uncommon", "0.28", "2026-11-10", "0.27"),
    (2, '"Beckett Mariner, Impetuous Ensign"', "TRK", "Star Trek", "138", "Normal", "uncommon", "0.44", "2026-11-10", "0.21"),
    (1, "Arcum Dagsson", "SDS", "Stardates", "7", "Normal", "mythic", "3.38", "2026-11-11", "4.01"),
    (1, '"Quark\'s Bar"', "TRK", "Star Trek", "350", "Normal", "rare", "2.13", "2026-11-11", "2.14"),
    (1, '"Quark\'s Bar"', "TRK", "Star Trek", "292", "Normal", "rare", "0.57", "2026-11-11", "0.48"),
    (4, "Moopsy", "TRK", "Star Trek", "207", "Normal", "common", "0.47", "2026-11-11", "0.40"),
    (2, '"I\'m a Doctor, Not a . . ."', "TRK", "Star Trek", "203", "Normal", "common", "0.25", "2026-11-11", "0.25"),
]


def _export(zeilen=ZEILEN) -> bytes:
    """Die Datei, wie die App sie schreibt — samt ``"sep=,"`` in Zeile eins."""
    text = ['"sep=,"', KOPF]
    for menge, name, s, sname, nr, druck, selten, preis, datum, markt in zeilen:
        text.append(f"Folder,Star Trek,mtg,,,{menge},{name},{s},{sname},{nr},"
                    f"NearMint,{druck},{selten},en,{preis},{datum},,,{markt},193339,")
    return ("\n".join(text) + "\n").encode("utf-8")


def _scryfall(zeilen=ZEILEN, nur_sets=None):
    """Kartendaten, in denen genau diese Karten vorkommen."""
    karten, gesehen = [], set()
    for i, (_, name, s, sname, nr, *_rest) in enumerate(zeilen):
        if (s, nr) in gesehen or (nur_sets and s not in nur_sets):
            continue
        gesehen.add((s, nr))
        karten.append({"id": f"{i:08d}-aaaa-bbbb-cccc-dddddddddddd",
                       "name": name.strip('"'), "set": s.lower(), "set_name": sname,
                       "lang": "en", "collector_number": nr,
                       "cardmarket_id": 900000 + i})
    return karten


class _Sofort:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._ziel, self._args = target, args

    def start(self):
        self._ziel(*self._args)


def _leeren():
    web.UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS, web.BULK_DONE, web.BULK_MESSAGE = 0, False, None


@pytest.fixture()
def anlage(tmp_path, monkeypatch):
    """Leeres Lager, Kartendaten mit den Beispielkarten, sofortiger Import."""
    lager = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", lager)
    setup_db.initialize_database()
    karten_db = tmp_path / "default-cards.db"
    bcd.schreibe_datenbank(_scryfall(), karten_db)
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", karten_db)
    monkeypatch.setattr(web.threading, "Thread", _Sofort)
    _leeren()
    yield types.SimpleNamespace(lager=lager, karten_db=karten_db)
    cs.reset_card_database()
    _leeren()


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _ordner(seiten, name="Star Trek"):
    """Ordner anlegen wie in der Oberfläche — das legt auch die Plätze an."""
    _klient().post("/folders/add", data={"name": name, "pages": str(seiten)})
    with sqlite3.connect(web.DB_FILE) as conn:
        return conn.execute("SELECT id FROM folders WHERE name = ?",
                            (name,)).fetchone()[0]


def _lade_hoch(ordner_id="", dateiname="export.txt", inhalt=None):
    return _klient().post("/cards/bulk_add", data={
        "folder_id": str(ordner_id), "cards": "",
        "csv_file": (io.BytesIO(inhalt or _export()), dateiname)},
        content_type="multipart/form-data")


def _bestand():
    with sqlite3.connect(web.DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(z) for z in conn.execute("SELECT * FROM cards ORDER BY id")]


# ---------------------------------------------------------------------------
# Die Datei selbst
# ---------------------------------------------------------------------------

def test_export_wird_vollstaendig_gelesen_und_aufgeloest(anlage):
    _lade_hoch(_ordner(2))
    assert web.NEEDS_REVIEW == []
    assert len(web.UPLOAD_QUEUE) == len(ZEILEN)
    nach_name = {(k["name"], k["collector_number"], k["foil"]): k
                 for k in web.UPLOAD_QUEUE}
    # Namen mit Komma und Apostroph kommen heil an.
    mariner = nach_name[("Beckett Mariner, Impetuous Ensign", "138", False)]
    assert mariner["quantity"] == 2 and mariner["set_code"] == "trk"
    assert ("Bat'leth", "94", False) in nach_name
    assert ("I'm a Doctor, Not a . . .", "203", False) in nach_name
    # Foil und Normal derselben Karte bleiben zwei Zeilen.
    assert nach_name[("Assault Drone", "92", True)]["quantity"] == 1
    assert nach_name[("Assault Drone", "92", False)]["quantity"] == 2
    # Derselbe Name unter zwei Nummern ist zweimal eine eigene Karte.
    assert {k["collector_number"] for k in web.UPLOAD_QUEUE
            if k["name"] == "Quark's Bar"} == {"292", "350"}
    # Das zweite Set der Datei.
    assert nach_name[("Arcum Dagsson", "7", False)]["set_code"] == "sds"


def test_alle_angaben_der_datei_landen_im_bestand(anlage):
    ordner = _ordner(2)
    _lade_hoch(ordner)
    _klient().get("/cards/upload_queue/add_all")
    karten = {(k["name"], k["collector_number"], k["foil"]): k for k in _bestand()}
    moopsy = karten[("Moopsy", "207", 0)]
    assert moopsy["quantity"] == 4
    assert moopsy["condition"] == "NM"                # aus „NearMint"
    assert moopsy["language"] == "en"
    assert moopsy["rarity"] == "common"
    assert moopsy["price"] == 0.47 and moopsy["market_price"] == 0.40
    assert moopsy["date_bought"] == "2026-11-11"
    assert moopsy["folder_id"] == ordner
    assert moopsy["scryfall_id"] and moopsy["cardmarket_id"]
    assert sum(k["quantity"] for k in karten.values()) == sum(z[0] for z in ZEILEN)


def test_plaetze_werden_in_der_reihenfolge_der_datei_vergeben(anlage):
    """Die Datei ist so sortiert, wie die Karten gescannt in der Mappe liegen."""
    _lade_hoch(_ordner(2))
    _klient().get("/cards/upload_queue/add_all")
    plaetze = [k["storage_code"] for k in _bestand()]
    assert plaetze[:3] == ["O01-S01-P1", "O01-S01-P2", "O01-S01-P3"]
    assert plaetze[9] == "O01-S02-P1"                 # zehnte Zeile, zweite Seite
    assert len(set(plaetze)) == len(plaetze)


# ---------------------------------------------------------------------------
# 1) Die App exportiert als .txt
# ---------------------------------------------------------------------------

def test_dateidialog_bietet_auch_txt_an(anlage):
    html = _klient().get("/cards/bulk_add").get_data(as_text=True)
    feld = html[html.index('name="csv_file"'):]
    feld = feld[:feld.index(">")]
    assert ".txt" in feld and ".csv" in feld


@pytest.mark.parametrize("dateiname", ["export.txt", "export.csv", "Star Trek.TXT"])
def test_der_dateiname_spielt_fuer_den_import_keine_rolle(anlage, dateiname):
    _lade_hoch(_ordner(2), dateiname=dateiname)
    assert len(web.UPLOAD_QUEUE) == len(ZEILEN)


# ---------------------------------------------------------------------------
# 2) Zu wenig Platz darf nicht still bleiben
# ---------------------------------------------------------------------------

def test_warteschlange_zeigt_bedarf_und_freie_plaetze(anlage):
    _lade_hoch(_ordner(2))                            # 18 Plätze für 10 Zeilen
    html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    assert "10 Plätze gebraucht" in html and "18 frei" in html
    assert "bekämen keinen Lagerplatz" not in html


def test_warteschlange_warnt_wenn_der_ordner_zu_klein_ist(anlage):
    ordner = _ordner(1)                               # 9 Plätze für 10 Zeilen
    _lade_hoch(ordner)
    html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    assert "10 Plätze gebraucht" in html and "9 frei" in html
    assert "1 Eintrag bekäme keinen Lagerplatz" in html
    assert f"/folders/edit/{ordner}" in html          # dort lassen sich Seiten ergänzen


def test_warteschlange_warnt_wenn_kein_zielordner_gewaehlt_wurde(anlage):
    _lade_hoch("")
    html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    assert "Kein Zielordner" in html
    assert "10 Einträge bekämen keinen Lagerplatz" in html


def test_bedarf_zaehlt_nicht_was_schon_einen_platz_hat(anlage):
    """Eine zweite Lieferung derselben Karten erhöht nur die Menge."""
    ordner = _ordner(2)
    _lade_hoch(ordner)
    _klient().get("/cards/upload_queue/add_all")
    _lade_hoch(ordner)
    html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    assert "0 Plätze gebraucht" in html
    assert "bekäme" not in html


def test_uebernahme_meldet_karten_ohne_lagerplatz(anlage):
    """Vorher hieß es nur „Alle Karten übernommen" — auch wenn die Mehrzahl
    ohne Platz im Bestand stand."""
    _lade_hoch(_ordner(1))
    antwort = _klient().get("/cards/upload_queue/add_all", follow_redirects=True)
    text = antwort.get_data(as_text=True)
    ohne = [k for k in _bestand() if not k["storage_code"]]
    assert len(ohne) == 1                             # die zehnte Zeile
    assert "ohne Lagerplatz" in text
    assert f"1 Eintrag mit {ohne[0]['quantity']} Karte" in text


def test_uebernahme_ohne_engpass_meldet_nichts_weiter(anlage):
    _lade_hoch(_ordner(2))
    text = _klient().get("/cards/upload_queue/add_all",
                         follow_redirects=True).get_data(as_text=True)
    assert "ohne Lagerplatz" not in text
    assert all(k["storage_code"] for k in _bestand())


def test_einzelne_uebernahme_meldet_den_fehlenden_platz(anlage):
    _lade_hoch("")                                    # kein Ordner, also kein Platz
    text = _klient().get("/cards/upload_queue/add/0",
                         follow_redirects=True).get_data(as_text=True)
    assert "ohne Lagerplatz" in text


# ---------------------------------------------------------------------------
# 3) Kartendaten älter als das Set
# ---------------------------------------------------------------------------

@pytest.fixture()
def alte_kartendaten(anlage, monkeypatch):
    """Die Kartendatenbank kennt das neue Set noch nicht."""
    alt = anlage.karten_db.with_name("alt.db")
    bcd.schreibe_datenbank(_scryfall(nur_sets={"SDS"}), alt)
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", alt)
    return anlage


def test_unbekanntes_set_wird_als_solches_benannt(alte_kartendaten):
    _lade_hoch(_ordner(2))
    trk = [e for e in web.NEEDS_REVIEW if e["set_code"] == "trk"]
    assert len(trk) == 9 and len(web.UPLOAD_QUEUE) == 1          # nur SDS löst auf
    for eintrag in trk:
        assert "Kein Scryfall-Treffer" in eintrag["reason"]
        assert "Set 'trk' ist in der lokalen Kartendatenbank unbekannt" in eintrag["reason"]


def test_meldung_nach_dem_import_nennt_die_ursache(alte_kartendaten):
    _lade_hoch(_ordner(2))
    assert "TRK" in web.BULK_MESSAGE
    assert "Kartendaten" in web.BULK_MESSAGE


def test_bekanntes_set_mit_falscher_nummer_bleibt_ein_einzelfall(anlage):
    zeilen = [(1, "Gibt Es Nicht", "TRK", "Star Trek", "9999", "Normal", "common",
               "0.10", "2026-11-11", "0.10")]
    _lade_hoch(_ordner(1), inhalt=_export(zeilen))
    grund = web.NEEDS_REVIEW[0]["reason"]
    assert "Kein Scryfall-Treffer für Set 'trk' Nr. 9999" in grund
    assert "unbekannt" not in grund
    assert "Kartendaten" not in (web.BULK_MESSAGE or "")


def test_alle_erneut_versuchen_nach_dem_aktualisieren(alte_kartendaten, monkeypatch):
    """Kartendaten aktualisiert — ein Klick statt neun (oder 249)."""
    ordner = _ordner(2)
    _lade_hoch(ordner)
    assert len(web.NEEDS_REVIEW) == 9

    cs.reset_card_database()                          # wie nach „Kartendaten aktualisieren"
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", alte_kartendaten.karten_db)
    antwort = _klient().post("/cards/needs_review/retry_all", follow_redirects=True)
    assert "9 aufgelöst" in antwort.get_data(as_text=True)
    assert web.NEEDS_REVIEW == []
    assert len(web.UPLOAD_QUEUE) == len(ZEILEN)
    mariner = [k for k in web.UPLOAD_QUEUE if k["collector_number"] == "138"][0]
    assert mariner["quantity"] == 2 and mariner["rarity"] == "uncommon"
    assert mariner["market_price"] == 0.21 and str(mariner["folder_id"]) == str(ordner)


def test_alle_erneut_versuchen_laesst_ungeloestes_stehen(alte_kartendaten):
    _lade_hoch(_ordner(2))
    antwort = _klient().post("/cards/needs_review/retry_all", follow_redirects=True)
    assert "0 aufgelöst" in antwort.get_data(as_text=True)
    assert len(web.NEEDS_REVIEW) == 9


def test_seite_bietet_alle_erneut_versuchen_an(alte_kartendaten):
    _lade_hoch(_ordner(2))
    html = _klient().get("/cards/needs_review").get_data(as_text=True)
    assert "/cards/needs_review/retry_all" in html
    assert "Kartendaten" in html


# ---------------------------------------------------------------------------
# Die Suche nach Set und Nummer nutzt den Index — mit demselben Ergebnis
# ---------------------------------------------------------------------------

def test_identitaet_wird_unabhaengig_von_der_schreibweise_gefunden(anlage):
    for code in ("trk", "TRK", "Trk"):
        treffer = cs.find_by_identity(code, "137")
        assert treffer and treffer["name"] == "Academy Instructor"
    assert cs.find_by_identity("trk", "99999") is None
    assert cs.find_by_identity("zzz", "137") is None


def test_anders_erzeugte_datenbank_mit_grossen_set_codes_wird_weiter_gelesen(
        anlage, monkeypatch):
    """Scryfall schreibt Set-Codes klein. Für Datenbanken, die es anders
    halten, bleibt der frühere, tolerante Vergleich als zweiter Versuch."""
    gross = anlage.karten_db.with_name("gross.db")
    bcd.schreibe_datenbank(_scryfall(), gross)
    with sqlite3.connect(gross) as conn:
        conn.execute("UPDATE cards SET set_code = upper(set_code)")
        conn.commit()
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", gross)
    assert cs.find_by_identity("trk", "137")["name"] == "Academy Instructor"
    assert cs.set_bekannt("trk") is True and cs.set_bekannt("zzz") is False


def test_die_suche_liest_nicht_mehr_die_ganze_tabelle(anlage):
    """Der direkte Vergleich muss den Index treffen — sonst kostet jede Zeile
    eines Imports einen Durchlauf durch über 100.000 Karten."""
    cs._load_card_database()
    plan = " ".join(str(z[3]) for z in cs._DB_CONN.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM cards WHERE set_code = ? "
        "AND collector_number = ?", ("trk", "137")))
    assert "SEARCH" in plan and "idx_identity" in plan


def test_set_bekannt_merkt_sich_die_antwort_bis_zum_tausch(alte_kartendaten,
                                                           monkeypatch):
    assert cs.set_bekannt("trk") is False
    assert cs._SET_BEKANNT == {"trk": False}
    # Nach „Kartendaten aktualisieren" darf die alte Antwort nicht kleben.
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", alte_kartendaten.karten_db)
    assert cs.set_bekannt("TRK") is True


# ---------------------------------------------------------------------------
# 4) Kartendaten aus der Preview-Zeit: das Set ist bekannt, aber unvollständig
#
# Der Fall aus dem Betrieb: „Star Trek" erschien am 13.11. Bis kurz davor hatte
# Scryfall nur die vorgestellten Karten. Kartendaten aus dieser Zeit kannten
# das Set — 78 Zeilen wurden erkannt, 172 nicht, jede mit „Kein Treffer für
# Set 'trk' Nr. …". Das sah aus wie 172 Einzelfehler.
# ---------------------------------------------------------------------------

@pytest.fixture()
def preview_kartendaten(anlage, monkeypatch):
    """Nur drei Karten des Sets sind schon bekannt, mit Stand in der Datenbank."""
    preview = [k for k in _scryfall()
               if k["set"] != "trk" or k["collector_number"] in ("137", "92", "94")]
    pfad = anlage.karten_db.with_name("preview.db")
    bcd.schreibe_datenbank(preview, pfad)
    bcd._merke_stand(pfad, "2026-10-28T10:00:00.000+00:00")
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", pfad)
    return anlage


def test_preview_daten_werden_als_ursache_benannt(preview_kartendaten):
    _lade_hoch(_ordner(2))
    fehlend = [e for e in web.NEEDS_REVIEW if e["set_code"] == "trk"]
    assert len(fehlend) == 5                          # 350, 292, 138, 207, 203
    assert "Karten fehlen in den Kartendaten: TRK (5 Zeilen)" in web.BULK_MESSAGE
    assert "28.10.2026" in web.BULK_MESSAGE
    assert "Preview" in web.BULK_MESSAGE


def test_einzelne_falsche_nummer_loest_den_hinweis_nicht_aus(anlage):
    zeilen = [(1, "Gibt Es Nicht", "TRK", "Star Trek", "9999", "Normal",
               "common", "0.10", "2026-11-11", "0.10")]
    _lade_hoch(_ordner(1), inhalt=_export(zeilen))
    assert "Karten fehlen in den Kartendaten" not in (web.BULK_MESSAGE or "")


def test_needs_review_bietet_aktualisieren_und_erneut_versuchen(preview_kartendaten):
    _lade_hoch(_ordner(2))
    html = _klient().get("/cards/needs_review").get_data(as_text=True)
    assert "Karten fehlen in den Kartendaten" in html and "TRK (5 Einträge)" in html
    assert "28.10.2026" in html
    assert "Kartendaten aktualisieren und danach erneut versuchen" in html
    assert 'name="zurueck" value="needs_review"' in html


def test_nach_der_aktualisierung_wird_needs_review_von_selbst_aufgeloest(
        preview_kartendaten, monkeypatch):
    """Der eigentliche Ausweg: Kartendaten neu, und die offenen Einträge gehen
    ohne weiteren Klick in die Warteschlange — mit allen Angaben der Datei."""
    ordner = _ordner(2)
    _lade_hoch(ordner)
    assert len(web.NEEDS_REVIEW) == 5

    vollstaendig = preview_kartendaten.karten_db

    def aktualisiere(fortschritt=None, erzwingen=False, vor_tausch=None):
        if vor_tausch:
            vor_tausch()
        monkeypatch.setattr(cs, "DEFAULT_DB_PATH", vollstaendig)
        return {"aktualisiert": True, "anzahl": 10, "stand": "2026-11-13"}

    monkeypatch.setattr(web.build_card_db, "aktualisiere_von_scryfall", aktualisiere)
    web.CARDDATA_STATUS.update({"laeuft": False, "fertig": False, "fehler": None})
    antwort = _klient().post("/system/kartendaten/aktualisieren",
                             data={"zurueck": "needs_review"})
    assert antwort.headers["Location"].endswith("/cards/needs_review")

    assert web.NEEDS_REVIEW == []
    assert len(web.UPLOAD_QUEUE) == len(ZEILEN)
    assert "5 aufgelöst" in web.CARDDATA_STATUS["meldung"]
    moopsy = [k for k in web.UPLOAD_QUEUE if k["collector_number"] == "207"][0]
    assert moopsy["quantity"] == 4 and moopsy["market_price"] == 0.40
    assert str(moopsy["folder_id"]) == str(ordner)


def test_namen_mit_mehreren_ausgaben_bleiben_beim_nachziehen_stehen(
        anlage, monkeypatch):
    web.NEEDS_REVIEW.append({"name": "Lightning Bolt", "set_code": "",
                             "collector_number": "", "reason": "3 Ausgaben"})
    monkeypatch.setattr(web.build_card_db, "aktualisiere_von_scryfall",
                        lambda **k: {"aktualisiert": False, "anzahl": 0, "stand": ""})
    web._aktualisiere_kartendaten(False)
    assert len(web.NEEDS_REVIEW) == 1
    assert "1 weiterhin offen" in web.CARDDATA_STATUS["meldung"]


# ---------------------------------------------------------------------------
# Nächtliche Aktualisierung
# ---------------------------------------------------------------------------

from datetime import datetime as _dt                              # noqa: E402


@pytest.mark.parametrize("uhrzeit,zuletzt,erwartet", [
    ("2026-11-13 03:10", None, True),
    ("2026-11-13 05:59", "2026-11-12", True),
    ("2026-11-13 04:00", "2026-11-13", False),        # heute schon gelaufen
    ("2026-11-13 02:59", None, False),                # zu früh
    ("2026-11-13 06:00", None, False),                # zu spät — tagsüber nicht
    ("2026-11-13 14:00", None, False),
])
def test_kartendaten_werden_nachts_einmal_geprueft(uhrzeit, zuletzt, erwartet):
    jetzt = _dt.strptime(uhrzeit, "%Y-%m-%d %H:%M")
    assert web._kartendaten_faellig(jetzt, zuletzt) is erwartet


def test_waechter_startet_nichts_waehrend_einer_laufenden_aktualisierung(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(web, "_aktualisiere_kartendaten", aufrufe.append)
    monkeypatch.setitem(web.CARDDATA_STATUS, "laeuft", True)
    waechter = web.KartendatenWaechter()
    assert waechter.durchgang(_dt(2026, 11, 13, 3, 30)) is False
    monkeypatch.setitem(web.CARDDATA_STATUS, "laeuft", False)
    assert waechter.durchgang(_dt(2026, 11, 13, 3, 30)) is True
    assert waechter.durchgang(_dt(2026, 11, 13, 4, 30)) is False   # nur einmal je Nacht
    assert aufrufe == [False]
