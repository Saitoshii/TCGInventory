"""Der Kopfbereich der Belege: das Logo darf nichts überdecken.

Auf den Folgeseiten eines großen Beilegers lief die goldene Linie mitten durch
das Logo, und die Spaltenüberschrift „PREIS" stand dahinter. Ursache war, dass
im Code nur die **Breite** des Logos stand (30 mm). Wie hoch ein Bild wird,
entscheidet bei ``pdf.image(..., w=…)`` aber das Seitenverhältnis der Datei —
und das Logo ist nahezu quadratisch, wurde also 30 mm hoch.

Die Prüfungen hier arbeiten deshalb bewusst auf zwei Ebenen:

* rechnerisch, damit die Konstanten zueinander passen, egal welches Bild
  hinterlegt wird;
* am fertigen PDF, damit auch wirklich ankommt, was gerechnet wurde.
"""

import os
import re
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import shipping_note as sn  # noqa: E402

PUNKT_JE_MM = 72 / 25.4


def _positionen(anzahl):
    return [
        {"quantity": 1, "name": f"Karte {i:03d}", "set_name": "The Hobbit",
         "condition": "NM", "unit_price": 0.10}
        for i in range(anzahl)
    ]


def _bilder_je_seite(pdf_bytes):
    """Alle Bildplatzierungen als ``{seite: [(links, oben, breite, hoehe)]}``.

    fpdf2 schreibt jedes Bild als ``q b 0 0 h x y cm /I… Do Q`` in den
    Inhaltsstrom einer Seite, mit dem Ursprung **unten links** und in Punkt.
    Hier wird beides in Millimeter von oben umgerechnet, weil das Layout so
    beschrieben ist.
    """
    text = pdf_bytes.decode("latin-1")
    stroeme = re.findall(r"stream\r?\n(.*?)\r?\nendstream", text, re.S)
    # Nur Seiteninhalte, keine Schrift- oder Bilddaten.
    seiten = [s for s in stroeme if (" cm /I" in s) or ("BT" in s and "Tf" in s)]
    gefunden = {}
    for nummer, strom in enumerate(seiten, start=1):
        for treffer in re.finditer(
            r"q ([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) cm /I", strom
        ):
            breite, hoehe, links, unten = (
                float(g) / PUNKT_JE_MM for g in treffer.groups()
            )
            oben = sn.PAGE_H_MM - unten - hoehe
            gefunden.setdefault(nummer, []).append((links, oben, breite, hoehe))
    return gefunden


# ---------------------------------------------------------------------------
# Rechnerisch: die Maße halten beide Grenzen ein
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("verhaeltnis", [0.25, 0.5, 0.75, 0.997, 1.0, 1.5, 3.0, 8.0])
def test_bildmasse_ueberschreitet_keine_der_beiden_grenzen(tmp_path, monkeypatch,
                                                          verhaeltnis):
    monkeypatch.setattr(sn, "bild_seitenverhaeltnis", lambda _p: verhaeltnis)
    breite, hoehe = sn.bildmasse("egal.png", 26, 18)
    assert breite <= 26 + 1e-9, "zu breit"
    assert hoehe <= 18 + 1e-9, "zu hoch — genau das war der Fehler"
    # Das Bild wird nicht verzerrt.
    assert breite / hoehe == pytest.approx(verhaeltnis)
    # Und es wird so groß wie möglich: eine der beiden Grenzen ist erreicht.
    assert breite == pytest.approx(26) or hoehe == pytest.approx(18)


def test_bildmasse_faengt_unbrauchbares_verhaeltnis_ab(monkeypatch):
    """Ein kaputtes Bild darf kein Maß von 0 oder eine Division ergeben."""
    monkeypatch.setattr(sn, "bild_seitenverhaeltnis", lambda _p: 0.0)
    breite, hoehe = sn.bildmasse("kaputt.png", 26, 18)
    assert breite > 0 and hoehe > 0


def test_folgeseiten_logo_bleibt_ueber_linie_und_tabelle():
    """Die Konstanten müssen zueinander passen — unabhängig vom Bild."""
    unterkante = sn.CONT_LOGO_TOP_MM + sn.CONT_LOGO_MAX_H_MM
    assert unterkante < sn.CONT_RULE_MM, (
        "Das Logo reicht bis zur goldenen Linie oder darüber hinaus"
    )
    assert unterkante < sn.CONT_TABLE_TOP_MM, (
        "Das Logo reicht in die Tabelle hinein"
    )
    assert sn.CONT_RULE_MM < sn.CONT_TABLE_TOP_MM


def test_erste_seite_logo_bleibt_ueber_dem_betreff():
    unterkante = sn.LOGO_TOP_MM + sn.LOGO_MAX_H_MM
    assert unterkante < sn.SUBJECT_TOP_MM


# ---------------------------------------------------------------------------
# Am fertigen PDF: was gezeichnet wurde, nicht was gedacht war
# ---------------------------------------------------------------------------
def test_kein_bild_ragt_in_den_kopf_der_folgeseite():
    pdf = sn.render_shipping_note(
        ["Max Muster", "Weg 1", "24983 Handewitt"], "1294428289",
        _positionen(40), buyer_name="tester", compress=False,
    )
    bilder = _bilder_je_seite(pdf)
    assert bilder, "keine Bildplatzierung gefunden — Prüfung liefe ins Leere"

    folgeseiten = [n for n in bilder if n > 1]
    assert folgeseiten, "erwartet wurde ein mehrseitiger Beileger mit Logo"

    for seite in folgeseiten:
        for links, oben, breite, hoehe in bilder[seite]:
            if oben > sn.CONT_TABLE_TOP_MM:
                continue                      # das Siegel im Fuß der letzten Seite
            assert oben + hoehe <= sn.CONT_RULE_MM, (
                f"Seite {seite}: Logo reicht bis {oben + hoehe:.1f} mm und "
                f"damit in die Linie bei {sn.CONT_RULE_MM} mm"
            )
            # Und es bleibt im rechten Rand, läuft also nicht über das Blatt.
            assert links + breite <= sn.PAGE_W_MM - sn.PAGE_MARGIN_MM + 1e-6


def test_logo_der_ersten_seite_bleibt_unveraendert():
    """Seite 1 war in Ordnung und soll sich durch die Korrektur nicht ändern."""
    pdf = sn.render_shipping_note(
        ["Max Muster", "Weg 1", "24983 Handewitt"], "1294428289",
        _positionen(40), buyer_name="tester", compress=False,
    )
    erste = _bilder_je_seite(pdf)[1]
    assert len(erste) == 1
    links, oben, breite, hoehe = erste[0]
    assert oben == pytest.approx(sn.LOGO_TOP_MM, abs=0.1)
    assert breite == pytest.approx(sn.LOGO_WIDTH_MM, abs=0.1)
    assert links + breite == pytest.approx(sn.PAGE_W_MM - sn.PAGE_MARGIN_MM, abs=0.1)


def test_hochformatiges_logo_sprengt_den_kopf_nicht(monkeypatch):
    """Ein anderes Logo (SHOP_LOGO) darf das Layout nicht zerlegen.

    Die Höhe wird begrenzt, nicht die Breite — deshalb bleibt auch ein
    schmales, hohes Bild über der Linie.
    """
    monkeypatch.setattr(sn, "bild_seitenverhaeltnis", lambda _p: 0.35)
    pdf = sn.render_shipping_note(
        ["Max Muster", "Weg 1", "24983 Handewitt"], "1294428289",
        _positionen(40), buyer_name="tester", compress=False,
    )
    bilder = _bilder_je_seite(pdf)
    for seite, eintraege in bilder.items():
        for links, oben, breite, hoehe in eintraege:
            if seite == 1:
                assert oben + hoehe < sn.SUBJECT_TOP_MM
            elif oben <= sn.CONT_TABLE_TOP_MM:
                assert oben + hoehe <= sn.CONT_RULE_MM


def test_quittung_logo_bleibt_ueber_der_tabelle(monkeypatch):
    from TCGInventory import quittung as q

    monkeypatch.setattr(sn, "bild_seitenverhaeltnis", lambda _p: 0.35)
    pdf = q.render_quittung(_positionen(4), "Q-1", compress=False)
    text = pdf.decode("latin-1")
    treffer = re.findall(r"q ([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) cm /I", text)
    assert treffer, "keine Bildplatzierung in der Quittung gefunden"
    for breite, hoehe, links, unten in treffer:
        hoehe_mm = float(hoehe) / PUNKT_JE_MM
        oben = q.SEITE_H_MM - float(unten) / PUNKT_JE_MM - hoehe_mm
        if oben > q.TABELLE_Y_MM:
            continue                          # Siegel im Fuß
        assert oben + hoehe_mm < q.TABELLE_Y_MM, (
            "Das Logo der Quittung reicht in die Tabelle"
        )
