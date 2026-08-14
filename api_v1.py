"""Lese-API für die Buchhaltungssoftware (WP5, API v1).

Diese Schnittstelle ist der **einzige** Weg, auf dem die Buchhaltung an
Inventardaten kommt. Bewusst rein lesend: die Buchhaltung ist führend für
Finanzen, das Inventarsystem für Bestand und Bestellungen — geschrieben wird
hier nichts.

Warum eine API und nicht der direkte Zugriff auf dieselbe SQLite-Datei:

* Zwei Prozesse auf einer Datei führen zu Sperrkonflikten und Wartezeiten.
* Die API ist versionierbar; das Schema darf sich ändern, ohne die Buchhaltung
  zu brechen.
* Zugriffe sind authentifiziert und protokollierbar.

Authentifizierung über einen Bearer-Token aus der Umgebungsvariable
``TCG_API_TOKEN``. Ist sie nicht gesetzt, bleibt die API vollständig
abgeschaltet — lieber keine Schnittstelle als eine offene.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from functools import wraps
from typing import Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

#: Felder, die den buchhaltungsrelevanten Zustand einer Bestellung ausmachen.
#: Nur deren Änderung erzeugt einen neuen ``inhalt_hash`` — eine korrigierte
#: Lieferadresse soll die Buchhaltung nicht beunruhigen.
HASH_FELDER = ("order_number", "status", "date_completed", "amount_gesamt",
               "amount_gesamtwert", "amount_versand", "amount_gebuehren",
               "amount_auszahlung")


def _token() -> str:
    return os.environ.get("TCG_API_TOKEN", "")


def token_noetig(f):
    """Bearer-Token prüfen — zeitkonstant, damit der Vergleich nichts verrät."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        erwartet = _token()
        if not erwartet:
            return jsonify({"fehler": "API ist nicht aktiviert "
                                      "(TCG_API_TOKEN nicht gesetzt)."}), 503
        kopf = request.headers.get("Authorization", "")
        gegeben = kopf[7:] if kopf.startswith("Bearer ") else ""
        if not hmac.compare_digest(gegeben, erwartet):
            return jsonify({"fehler": "Nicht autorisiert."}), 401
        return f(*args, **kwargs)
    return wrapper


def _db() -> sqlite3.Connection:
    from TCGInventory import DB_FILE
    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def inhalt_hash(bestellung: Dict) -> str:
    """Fingerabdruck der buchhaltungsrelevanten Felder.

    Die Buchhaltung erkennt daran, ob sich eine bereits importierte Bestellung
    im Quellsystem verändert hat — ohne alle Felder vergleichen zu müssen.
    """
    daten = {k: bestellung.get(k) for k in HASH_FELDER}
    roh = json.dumps(daten, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def _positionen(conn: sqlite3.Connection, order_id: int) -> List[Dict]:
    zeilen = conn.execute(
        "SELECT card_name, quantity, unit_price, set_name, set_code, language, "
        "condition, foil, card_id, match_status FROM order_items "
        "WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()
    return [
        {
            "name": z["card_name"],
            "menge": z["quantity"],
            "einzelpreis_cent": _cent(z["unit_price"]),
            "set_name": z["set_name"],
            "set_code": z["set_code"],
            "sprache": z["language"],
            "zustand": z["condition"],
            "foil": bool(z["foil"]),
            "inventar_karte_id": z["card_id"],
            "zuordnung": z["match_status"],
        }
        for z in zeilen
    ]


def _spalte(zeile: sqlite3.Row, name: str):
    """Wert einer Spalte, die es vielleicht noch nicht gibt.

    Ältere Datenbanken kennen ``quelle`` und ``verkaufskanal`` noch nicht.
    Ein Absturz der API wäre dafür die falsche Antwort — die Buchhaltung
    bekommt dann den Vorgabewert und bucht wie bisher.
    """
    try:
        return zeile[name]
    except (IndexError, KeyError):
        return None


def _cent(wert) -> Optional[int]:
    """Euro-Wert aus der Inventardatenbank in Cent — ohne Gleitkommafehler."""
    if wert is None:
        return None
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(wert)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _bestellung(zeile: sqlite3.Row, conn: sqlite3.Connection,
                mit_positionen: bool = True) -> Dict:
    """Eine Bestellung in die API-Form bringen.

    Bewusst **ohne** Adresse und ohne Käufernamen im Klartext über das
    Käuferkürzel hinaus: die Buchhaltung braucht für die EÜR keine
    personenbezogenen Kundendaten (Datensparsamkeit).
    """
    daten = {
        "inventar_id": zeile["id"],
        "bestellnummer": zeile["order_number"],
        "kaeufer": zeile["buyer_name"],          # Cardmarket-Pseudonym
        "status": zeile["status"],
        "bestelldatum": (zeile["email_date"] or zeile["date_received"] or "")[:10],
        "versanddatum": (zeile["date_completed"] or "")[:10] or None,
        "betraege_cent": {
            "gesamt": _cent(zeile["amount_gesamt"]),
            "warenwert": _cent(zeile["amount_gesamtwert"]),
            "versand": _cent(zeile["amount_versand"]),
            "gebuehren": _cent(zeile["amount_gebuehren"]),
            "auszahlung": _cent(zeile["amount_auszahlung"]),
        },
        # Nicht jeder Verkauf läuft über Cardmarket. Bei einem Direktverkauf
        # fällt keine Plattformgebühr an und das Geld landet nicht auf dem
        # Cardmarket-Konto — die Buchhaltung muss die Fälle unterscheiden
        # können, deshalb steht hier der echte Kanal statt einer Konstante.
        "verkaufskanal": _spalte(zeile, "verkaufskanal") or "cardmarket",
        "quelle": _spalte(zeile, "quelle") or "cardmarket",
    }
    daten["inhalt_hash"] = inhalt_hash(dict(zeile))
    if mit_positionen:
        daten["positionen"] = _positionen(conn, zeile["id"])
    return daten


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------
@api_v1.route("/health")
def health():
    """Erreichbarkeit und Datenbankzustand — ohne Token, ohne Daten."""
    try:
        with _db() as conn:
            conn.execute("SELECT 1 FROM orders LIMIT 1")
        return jsonify({"status": "ok", "api": "v1"})
    except sqlite3.Error as exc:
        return jsonify({"status": "fehler", "meldung": str(exc)}), 503


@api_v1.route("/orders")
@token_noetig
def orders():
    """Versendete Bestellungen ab einem Stichtag.

    Parameter: ``ab`` (JJJJ-MM-TT), ``status`` (Standard ``sold``),
    ``limit`` (Standard 200, höchstens 1000).
    """
    ab = (request.args.get("ab") or "").strip()
    status = (request.args.get("status") or "sold").strip()
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
    except ValueError:
        limit = 200

    sql = ("SELECT * FROM orders WHERE status = ? "
           "AND substr(COALESCE(date_completed, email_date, date_received), 1, 10) >= ?")
    werte = [status, ab or "0000-01-01"]
    sql += " ORDER BY COALESCE(date_completed, email_date, date_received) DESC LIMIT ?"
    werte.append(limit)

    with _db() as conn:
        zeilen = conn.execute(sql, werte).fetchall()
        daten = [_bestellung(z, conn) for z in zeilen]
    return jsonify({"anzahl": len(daten), "bestellungen": daten})


@api_v1.route("/orders/<int:order_id>")
@token_noetig
def order_detail(order_id: int):
    with _db() as conn:
        zeile = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not zeile:
            return jsonify({"fehler": "Bestellung nicht gefunden."}), 404
        return jsonify(_bestellung(zeile, conn))


@api_v1.route("/stock/summary")
@token_noetig
def stock_summary():
    """Bestandskennzahlen — für Controlling, nicht für Buchungen."""
    with _db() as conn:
        zeile = conn.execute(
            "SELECT COUNT(*) AS positionen, COALESCE(SUM(quantity), 0) AS stueck "
            "FROM cards WHERE item_type = 'card'").fetchone()
        produkte = conn.execute(
            "SELECT COUNT(*) AS positionen, COALESCE(SUM(quantity), 0) AS stueck "
            "FROM cards WHERE item_type <> 'card'").fetchone()
    return jsonify({
        "karten": {"positionen": zeile["positionen"], "stueck": zeile["stueck"]},
        "produkte": {"positionen": produkte["positionen"], "stueck": produkte["stueck"]},
    })
