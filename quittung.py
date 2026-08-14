"""Quittung für den Verkauf vor Ort (Direktverkauf, Flohmarkt).

Der Beileger ist ein Anschreiben für ein Fensterkuvert — mit Empfängeradresse,
Anrede und Dank für die Bestellung. Wer am Flohmarktstand eine Karte über den
Tisch reicht, braucht das nicht: dort zählt, was gekauft wurde, was es gekostet
hat und dass der Betrag bezahlt ist.

Deshalb ein eigenes, kurzes Dokument statt eines beschnittenen Beilegers:

* **A5 quer**, passt zweimal auf ein A4-Blatt und ist mit der Schere zu
  trennen — auf dem Markt hat niemand einen Bondrucker dabei.
* **Keine Adresse.** Wer bar über den Tisch kauft, hinterlässt keine.
* **Keine Anrede, kein Bewertungssatz.** Beides ergibt ohne Cardmarket-Konto
  keinen Sinn.

Schrift, Farben und Absenderangaben kommen aus ``shipping_note`` — es gibt nur
**einen** Ort für das Erscheinungsbild.

Ausdrücklich **keine** Rechnung im steuerlichen Sinn: hier steht keine
Umsatzsteuer, keine Steuernummer und keine Rechnungsnummer im Sinne der
Vorschriften. Was auf so einen Beleg gehört, entscheidet die Steuerberatung,
nicht dieses Programm.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Optional, Sequence

from fpdf import FPDF

from .shipping_note import (
    GOLD, GREY, HAIRLINE, INK, _SANS, _SERIF, _eur, _format_date,
    _position_fields, _register_fonts, bild_seitenverhaeltnis, get_shop_config,
)

# A5 quer — zwei davon auf ein A4-Blatt.
SEITE_B_MM = 210
SEITE_H_MM = 148
RAND_MM = 14
INHALT_B_MM = SEITE_B_MM - 2 * RAND_MM

LOGO_B_MM = 34
KOPF_Y_MM = 12
TABELLE_Y_MM = 46
ZEILE_H_MM = 6.4
SIEGEL_H_MM = 12
SUMMEN_H_MM = 18
#: Unterkante der Tabelle — darunter beginnt der Fuß mit Siegel und Absender.
TABELLE_UNTEN_MM = SEITE_H_MM - 34

#: Rechter Rand für Preise und Summen.
PREIS_RECHTS_X = SEITE_B_MM - RAND_MM
SUMME_LABEL_RECHTS_X = PREIS_RECHTS_X - 26

TEXTE = {
    "titel": "Quittung",
    "nummer": "Beleg {number}",
    "kanal": {"direktverkauf": "Direktverkauf", "flohmarkt": "Flohmarkt"},
    "spalte_menge": "MENGE",
    "spalte_karte": "ARTIKEL",
    "spalte_preis": "PREIS",
    "zwischensumme": "Zwischensumme",
    "versand": "Versand",
    "gesamt": "Gesamt",
    "bezahlt": "Betrag dankend erhalten.",
    "fortsetzung": "Beleg {number} — Fortsetzung",
}


def render_quittung(
    positionen: Sequence,
    beleg_nummer: str,
    kanal: str = "direktverkauf",
    versand: float = 0.0,
    datum=None,
    kaeufer: str = "",
    config: Optional[dict] = None,
    compress: bool = True,
) -> bytes:
    """Eine Quittung als PDF (A5 quer) erzeugen.

    ``positionen`` sind dieselben Angaben wie beim Beileger, damit beide
    Dokumente aus derselben Bestellung entstehen können.
    """
    cfg = config or get_shop_config()
    tag = datum or _date.today()

    pdf = FPDF(orientation="L", unit="mm", format=(SEITE_H_MM, SEITE_B_MM))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    _register_fonts(pdf)

    def text(x, y, s, family=_SERIF, style="", size=10, color=INK, w=0, align="L"):
        pdf.set_xy(x, y)
        pdf.set_font(family, style, size)
        pdf.set_text_color(*color)
        pdf.cell(w or 0, 5, str(s), align=align)

    # --- Kopf: Logo rechts, Titel links ---------------------------------
    logo = cfg.get("logo_path")
    if logo:
        try:
            pdf.image(logo, x=SEITE_B_MM - RAND_MM - LOGO_B_MM, y=KOPF_Y_MM - 4,
                      w=LOGO_B_MM)
        except Exception:
            pass

    text(RAND_MM, KOPF_Y_MM, TEXTE["titel"], _SERIF, "B", 18, INK)
    text(RAND_MM, KOPF_Y_MM + 10,
         TEXTE["nummer"].format(number=beleg_nummer), _SANS, "", 10, GREY)
    zeile = TEXTE["kanal"].get(kanal, kanal)
    if kaeufer.strip():
        zeile += f" · {kaeufer.strip()}"
    text(RAND_MM, KOPF_Y_MM + 16, zeile, _SANS, "", 9, GREY)
    text(RAND_MM, KOPF_Y_MM + 22, _format_date(tag, "de"), _SANS, "", 9, GREY)

    # --- Tabellenkopf ----------------------------------------------------
    y = TABELLE_Y_MM
    text(RAND_MM, y, TEXTE["spalte_menge"], _SANS, "B", 8, GOLD)
    text(RAND_MM + 18, y, TEXTE["spalte_karte"], _SANS, "B", 8, GOLD)
    text(SUMME_LABEL_RECHTS_X - 20, y, TEXTE["spalte_preis"], _SANS, "B", 8, GOLD,
         w=PREIS_RECHTS_X - SUMME_LABEL_RECHTS_X + 20, align="R")
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.2)
    pdf.line(RAND_MM, y + 5, PREIS_RECHTS_X, y + 5)

    # --- Positionen ------------------------------------------------------
    # Auch hier gilt: passt eine Zeile nicht mehr aufs Blatt, kommt ein neues.
    # Sonst stünde sie im PDF und fehlte auf dem Papier — derselbe Fehler wie
    # beim Beileger.
    def neues_blatt() -> float:
        pdf.add_page()
        text(RAND_MM, KOPF_Y_MM, TEXTE["fortsetzung"].format(number=beleg_nummer),
             _SERIF, "", 12, INK)
        pdf.set_draw_color(*HAIRLINE)
        pdf.set_line_width(0.2)
        pdf.line(RAND_MM, KOPF_Y_MM + 8, PREIS_RECHTS_X, KOPF_Y_MM + 8)
        yy = KOPF_Y_MM + 12
        text(RAND_MM, yy, TEXTE["spalte_menge"], _SANS, "B", 8, GOLD)
        text(RAND_MM + 18, yy, TEXTE["spalte_karte"], _SANS, "B", 8, GOLD)
        text(SUMME_LABEL_RECHTS_X - 20, yy, TEXTE["spalte_preis"], _SANS, "B", 8,
             GOLD, w=PREIS_RECHTS_X - SUMME_LABEL_RECHTS_X + 20, align="R")
        pdf.line(RAND_MM, yy + 5, PREIS_RECHTS_X, yy + 5)
        return yy + 8

    y += 8
    zwischensumme = 0.0
    for position in positionen:
        if y + ZEILE_H_MM + 3 > TABELLE_UNTEN_MM:
            y = neues_blatt()

        menge, name, set_name, zustand, einzelpreis, foil = _position_fields(position)
        menge = menge or 1
        einzelpreis = float(einzelpreis or 0)
        zwischensumme += menge * einzelpreis

        text(RAND_MM, y, f"{menge}×", _SANS, "", 9.5, INK)
        # Wie im Beileger ausgeschrieben: die eingebettete Schrift hat kein
        # Sternsymbol, und ein fehlendes Zeichen faellt auf dem Beleg auf.
        beschriftung = f"{name} — Foil" if foil else name
        zusatz = " · ".join(t for t in (set_name, zustand) if t)
        text(RAND_MM + 18, y, beschriftung, _SERIF, "", 10, INK)
        if zusatz:
            text(RAND_MM + 18, y + 4, zusatz, _SANS, "", 7.5, GREY)
        text(SUMME_LABEL_RECHTS_X - 20, y, _eur(menge * einzelpreis), _SANS, "",
             9.5, INK, w=PREIS_RECHTS_X - SUMME_LABEL_RECHTS_X + 20, align="R")
        y += ZEILE_H_MM + (2.5 if zusatz else 0)

    # --- Summen ----------------------------------------------------------
    if y + SUMMEN_H_MM > TABELLE_UNTEN_MM:      # Summen bleiben zusammen
        y = neues_blatt()
    y += 2
    pdf.line(SUMME_LABEL_RECHTS_X - 26, y, PREIS_RECHTS_X, y)
    y += 2

    def summenzeile(label, wert, fett=False):
        stil = "B" if fett else ""
        text(SUMME_LABEL_RECHTS_X - 40, y, label, _SANS, stil, 9.5,
             INK if fett else GREY, w=40, align="R")
        text(SUMME_LABEL_RECHTS_X, y, _eur(wert), _SANS, stil, 9.5, INK,
             w=PREIS_RECHTS_X - SUMME_LABEL_RECHTS_X, align="R")

    versand = float(versand or 0)
    if versand:
        summenzeile(TEXTE["zwischensumme"], zwischensumme)
        y += 5
        summenzeile(TEXTE["versand"], versand)
        y += 6
    summenzeile(TEXTE["gesamt"], zwischensumme + versand, fett=True)

    # --- Fuß: Siegel, Bestätigung, Absender ------------------------------
    fuss_y = SEITE_H_MM - 30
    pdf.set_draw_color(*HAIRLINE)
    pdf.line(RAND_MM, fuss_y, PREIS_RECHTS_X, fuss_y)

    badge = cfg.get("badge_path")
    if badge:
        try:
            breite = SIEGEL_H_MM * bild_seitenverhaeltnis(badge)
            pdf.image(badge, x=PREIS_RECHTS_X - breite, y=fuss_y + 3, h=SIEGEL_H_MM)
        except Exception:
            pass

    text(RAND_MM, fuss_y + 5, TEXTE["bezahlt"], _SERIF, "I", 10, GOLD)
    text(RAND_MM, fuss_y + 14,
         cfg.get("footer_sender_line", cfg["sender_line"]), _SANS, "", 7.5, GREY)

    ausgabe = pdf.output()
    return bytes(ausgabe) if not isinstance(ausgabe, bytes) else ausgabe
