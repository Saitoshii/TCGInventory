"""Lange Namen auf Beileger und Quittung.

Der gemeldete Fehler: ist ein Name zu lang, sieht der Druck „komisch" aus.
Ursache war ``pdf.cell()`` — es schneidet nichts ab und bricht nichts um. Ein
langer Kartenname lief quer über „Zustand" und „Preis" und über den Blattrand
hinaus, eine lange Adresszeile aus dem Fenster des Kuverts ins Logo. Besonders
betroffen sind Precons, Displays und Zubehör, deren Namen selten kurz sind.

Beim Auslesen des PDFs fällt das nicht auf — der Text ist ja vorhanden, er
steht nur an der falschen Stelle. Geprüft wird deshalb die **Breite** der
gesetzten Zeilen, nicht nur ihr Inhalt.
"""

import os
import sys
import types
from io import BytesIO

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fpdf import FPDF  # noqa: E402

from TCGInventory import quittung  # noqa: E402
from TCGInventory.shipping_note import (  # noqa: E402
    ADDRESS_MIN_FONT_PT, ADDRESS_WIDTH_MM, ADDRESS_ZONE_H_MM, MAX_NAME_ZEILEN,
    RECIPIENT_LINE_H_MM, ROW_H_MM, _CARD_COL_W_MM, _SANS, _SERIF,
    _register_fonts, adresse_einpassen, positionslayout, render_shipping_note,
    seitenanzahl, umbrechen,
)

PRECON = ('Commander: The Lord of the Rings: Tales of Middle-earth: '
          '"Riders of Rohan" Commander Deck')
SET_LANG = "Universes Beyond: The Lord of the Rings: Tales of Middle-earth: Extras"
SPIELMATTE = ("Ultra Pro: Magic: The Gathering - Bloomburrow Stitched Edge "
              "Playmat Special Guest")


def _pdf():
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    _register_fonts(pdf)
    pdf.add_page()
    return pdf


def _lang(anzahl=1):
    return [{"quantity": 1, "name": f"{PRECON} Nr. {i:03d}", "set_name": SET_LANG,
             "condition": "NM", "unit_price": 64.90, "foil": bool(i % 2)}
            for i in range(1, anzahl + 1)]


def _woerter(zeilen):
    return " ".join(zeilen).replace(" ", " ").split()


# ---------------------------------------------------------------------------
# umbrechen()
# ---------------------------------------------------------------------------

def test_kurzer_text_bleibt_eine_zeile():
    pdf = _pdf()
    pdf.set_font(_SERIF, "", 11)
    assert umbrechen(pdf, "Lightning Bolt", 88) == ["Lightning Bolt"]
    assert umbrechen(pdf, "", 88) == []
    assert umbrechen(pdf, None, 88) == []


def test_jede_zeile_passt_in_die_breite_und_kein_wort_geht_verloren():
    pdf = _pdf()
    pdf.set_font(_SERIF, "", 11)
    for name in (PRECON, SPIELMATTE, SET_LANG):
        zeilen = umbrechen(pdf, name, 88)
        assert len(zeilen) > 1, "der Name ist länger als die Spalte"
        for zeile in zeilen:
            assert pdf.get_string_width(zeile) <= 88, zeile
        assert _woerter(zeilen) == name.split()


def test_ein_ueberlanges_wort_wird_getrennt_statt_ueberzulaufen():
    """Ohne Leerzeichen gibt es keine Stelle zum Umbrechen — dann hart."""
    pdf = _pdf()
    pdf.set_font(_SERIF, "", 11)
    wort = "Asmoranomardicadaistinaculdacar" * 4
    zeilen = umbrechen(pdf, wort, 88)
    assert len(zeilen) > 1
    for zeile in zeilen:
        assert pdf.get_string_width(zeile) <= 88
    assert "".join(zeilen) == wort


def test_obergrenze_kuerzt_sichtbar():
    pdf = _pdf()
    pdf.set_font(_SERIF, "", 11)
    zeilen = umbrechen(pdf, " ".join(["Wort"] * 200), 88, max_zeilen=3)
    assert len(zeilen) == 3
    assert zeilen[-1].endswith("…")
    for zeile in zeilen:
        assert pdf.get_string_width(zeile) <= 88


def test_foil_zusatz_wird_nicht_vom_strich_getrennt():
    """„— Foil" gehört zusammen: kein Strich am Zeilenende, kein einsames „Foil"."""
    pdf = _pdf()
    zeile = positionslayout(pdf, [{"quantity": 1, "foil": True, "set_name": "",
                                   "name": "Asmoranomardicadaistinaculdacar (V.2)",
                                   "condition": "NM", "unit_price": 1}])[0]
    assert not any(z.endswith("—") for z in zeile["name_zeilen"])
    assert any("— Foil" in z for z in zeile["name_zeilen"])


# ---------------------------------------------------------------------------
# Beileger
# ---------------------------------------------------------------------------

def test_positionen_bleiben_in_der_kartenspalte():
    pdf = _pdf()
    for zeile in positionslayout(pdf, _lang(3)):
        pdf.set_font(_SERIF, "", 11)
        assert 1 < len(zeile["name_zeilen"]) <= MAX_NAME_ZEILEN
        for stueck in zeile["name_zeilen"]:
            assert pdf.get_string_width(stueck) <= _CARD_COL_W_MM, stueck
        pdf.set_font(_SERIF, "I", 8.5)
        for stueck in zeile["set_zeilen"]:
            assert pdf.get_string_width(stueck) <= _CARD_COL_W_MM, stueck
        assert zeile["hoehe"] > ROW_H_MM, "die Zeile muss mit dem Text wachsen"


def test_kurze_namen_behalten_die_bisherige_zeilenhoehe():
    """Für gewöhnliche Karten ändert sich am Blatt nichts."""
    pdf = _pdf()
    layout = positionslayout(pdf, [{"quantity": 2, "name": "Lightning Bolt",
                                    "set_name": "Magic 2011", "condition": "NM",
                                    "unit_price": 1.2, "foil": True}])
    assert layout[0]["hoehe"] == ROW_H_MM
    assert len(layout[0]["name_zeilen"]) == 1


def test_langer_name_steht_vollstaendig_auf_dem_beileger():
    pypdf = pytest.importorskip("pypdf")
    roh = render_shipping_note(
        recipient_lines=["Max Mustermann", "Musterweg 1", "01159 Dresden"],
        order_number="LANG-1", buyer_name="kaeufer",
        positions=[{"quantity": 1, "name": PRECON, "set_name": SET_LANG,
                    "condition": "", "unit_price": 64.90}],
        totals={"shipping": 5.49})
    text = pypdf.PdfReader(BytesIO(roh)).pages[0].extract_text()
    flach = " ".join(text.split())
    for wort in PRECON.split():
        assert wort in flach, wort
    assert "64,90" in flach and "70,39" in flach


def test_seitenzahl_stimmt_auch_bei_hohen_zeilen():
    """Hohe Zeilen füllen ein Blatt schneller. Die Vorausberechnung muss das
    wissen — sonst steht „Seite 1 von 2" auf einem Beileger mit drei Blättern."""
    pypdf = pytest.importorskip("pypdf")
    for anzahl in (1, 5, 6, 7, 12, 20, 33):
        positionen = _lang(anzahl)
        roh = render_shipping_note(
            recipient_lines=["Max Mustermann", "01159 Dresden"],
            order_number="LANG-2", buyer_name="kaeufer", positions=positionen)
        seiten = pypdf.PdfReader(BytesIO(roh)).pages
        hoehen = [z["hoehe"] for z in positionslayout(_pdf(), positionen)]
        assert seitenanzahl(hoehen) == len(seiten), f"bei {anzahl} Positionen"
        if len(seiten) > 1:
            for nr, seite in enumerate(seiten, start=1):
                assert f"Seite {nr} von {len(seiten)}" in seite.extract_text()
        # Jede Position genau einmal, keine verloren.
        alles = " ".join(" ".join(s.extract_text() for s in seiten).split())
        for i in range(1, anzahl + 1):
            assert alles.count(f"Nr. {i:03d}") == 1, f"Position {i}"


def test_seitenanzahl_nimmt_weiterhin_eine_anzahl():
    """Die alte Aufrufform bleibt gültig: n Zeilen in der üblichen Höhe."""
    assert seitenanzahl(3) == seitenanzahl([ROW_H_MM] * 3) == 1
    assert seitenanzahl(40) == seitenanzahl([ROW_H_MM] * 40)
    assert seitenanzahl(0) == 1


# ---------------------------------------------------------------------------
# Anschrift
# ---------------------------------------------------------------------------

def test_gewoehnliche_anschrift_bleibt_unveraendert():
    zeilen = ["Max Mustermann", "Musterweg 1", "01159 Dresden"]
    groesse, abstand, aus = adresse_einpassen(_pdf(), zeilen)
    assert (groesse, abstand, aus) == (11.0, RECIPIENT_LINE_H_MM, zeilen)


def test_lange_adresszeile_bleibt_im_fenster():
    pdf = _pdf()
    zeilen = ["Maximilian-Alexander von Hohenzollern-Sigmaringen-Schwarzenberg",
              "c/o Wohngemeinschaft Sonnenschein im Hinterhaus, 3. Etage links",
              "Bürgermeister-Smidt-Straße 123a", "27568 Bremerhaven"]
    groesse, abstand, aus = adresse_einpassen(pdf, zeilen)
    assert groesse >= ADDRESS_MIN_FONT_PT, "die Anschrift muss lesbar bleiben"
    pdf.set_font(_SANS, "", groesse)
    for zeile in aus:
        assert pdf.get_string_width(zeile) <= ADDRESS_WIDTH_MM, zeile
    assert _woerter(aus) == " ".join(zeilen).split()
    assert len(aus) * abstand <= ADDRESS_ZONE_H_MM


def test_knapp_zu_breite_zeile_wird_eingepasst_statt_umbrochen():
    """Ein einzelnes übriges Wort in einer eigenen Zeile sähe aus wie eine
    weitere Adresszeile. Fehlt nur wenig, wird die Schrift etwas kleiner."""
    pdf = _pdf()
    pdf.set_font(_SANS, "", 11)
    zeile = "Musterstraße"
    while pdf.get_string_width(zeile + " 1") <= ADDRESS_WIDTH_MM:
        zeile += " 1"
    zeile += " 1"                                   # jetzt knapp zu breit
    groesse, _, aus = adresse_einpassen(pdf, ["Max Mustermann", zeile])
    assert aus == ["Max Mustermann", zeile]
    assert ADDRESS_MIN_FONT_PT <= groesse < 11


def test_sehr_viele_adresszeilen_ruecken_zusammen():
    zeilen = [f"Adresszeile {i}" for i in range(1, 11)]
    _, abstand, aus = adresse_einpassen(_pdf(), zeilen)
    assert len(aus) == 10
    assert len(aus) * abstand <= ADDRESS_ZONE_H_MM + 1e-6


# ---------------------------------------------------------------------------
# Quittung
# ---------------------------------------------------------------------------

def test_quittung_bricht_lange_artikel_um():
    pypdf = pytest.importorskip("pypdf")
    roh = quittung.render_quittung(
        positionen=[{"quantity": 1, "name": PRECON, "set_name": SET_LANG,
                     "condition": "NM", "unit_price": 64.90, "foil": True},
                    {"quantity": 1, "name": SPIELMATTE, "set_name": "Playmats",
                     "condition": "", "unit_price": 29.99}],
        beleg_nummer="DV-2026-0007", kanal="flohmarkt", datum="2026-11-09")
    seiten = pypdf.PdfReader(BytesIO(roh)).pages
    flach = " ".join(" ".join(s.extract_text() for s in seiten).split())
    for wort in PRECON.split() + SPIELMATTE.split():
        assert wort in flach, wort
    assert "94,89" in flach                       # Summe unverändert richtig

    pdf = _pdf()
    pdf.set_font(_SERIF, "", 10)
    for name in (PRECON + " — Foil", SPIELMATTE):
        for zeile in umbrechen(pdf, name, quittung.ARTIKEL_B_MM):
            assert pdf.get_string_width(zeile) <= quittung.ARTIKEL_B_MM


def test_artikelspalte_der_quittung_endet_vor_dem_preis():
    """Die Breite ist so gewählt, dass rechts Platz für den Preis bleibt."""
    rechts = quittung.ARTIKEL_X + quittung.ARTIKEL_B_MM
    assert rechts <= quittung.PREIS_RECHTS_X - 26
