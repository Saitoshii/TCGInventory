"""Import einer Scan-App-Listen-CSV (zusätzliches Spaltenlayout).

Der Export einer Scan-App hat mehr Spalten als der Dragonshield-Export, benennt
die relevanten Felder aber gleich (``Card Name``, ``Set Code``, ``Card Number``,
``Condition``, ``Printing``, ``Language``, ``Price Bought``, ``Date Bought``).
Zusätzlich liefert er ``Rarity`` und ``Current Price (<quelle>)``.

Getestet wird, dass beide Layouts denselben Pfad nehmen, die Zusatzfelder
erhalten bleiben und der Zustand auf die Cardmarket-Codes normalisiert wird.
"""

import os
import sys
import types

# Stub out heavy dependencies used by card_scanner
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest  # noqa: E402

from TCGInventory import web  # noqa: E402
from TCGInventory.web import _process_bulk_upload, UPLOAD_QUEUE  # noqa: E402
from TCGInventory.dragonshield import (  # noqa: E402
    extract_row,
    normalize_condition,
    normalize_date,
)

# Kopfzeile des Scan-App-Exports (inkl. der Separator-Direktive von Excel).
SCANAPP_HEADER = (
    'sep=,\n'
    'List Type,List Name,Collection,Format,Board,Quantity,Card Name,Set Code,'
    'Set Name,Card Number,Condition,Printing,Rarity,Language,Price Bought,'
    'Date Bought,Parent List Type,Parent List Name,'
    'Current Price (cardmarket_avgsellprice),List Cover Image,'
    'Parent List Cover Image\n'
)


def _reset():
    UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS = 0
    web.BULK_DONE = False
    web.BULK_MESSAGE = None


def _echo_identity(set_code, collector_number, language=None):
    """Stub für find_by_identity: jede (Set, Nummer) löst auf."""
    return {
        "set_code": set_code,
        "collector_number": collector_number,
        "language": language or "en",
        "scryfall_id": "sc-" + str(collector_number),
        "cardmarket_id": "cm-" + str(collector_number),
        "image_url": "http://img/" + str(collector_number),
    }


class TestConditionNormalization:
    """Zustände aus dem Export werden auf die Cardmarket-Codes abgebildet."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("NearMint", "NM"),
            ("Near Mint", "NM"),
            ("nearmint", "NM"),
            ("NM", "NM"),
            ("Mint", "MT"),
            ("Excellent", "EX"),
            ("Good", "GD"),
            ("LightPlayed", "LP"),
            ("Lightly Played", "LP"),
            ("Played", "PL"),
            ("Poor", "PO"),
            ("", ""),
        ],
    )
    def test_known_conditions(self, raw, expected):
        assert normalize_condition(raw) == expected

    def test_unknown_condition_is_kept_verbatim(self):
        """Nicht eindeutig abbildbare Skalen werden nicht geraten."""
        assert normalize_condition("Moderately Played") == "Moderately Played"
        assert normalize_condition("Heavily Played") == "Heavily Played"


class TestDateNormalization:
    def test_iso_date(self):
        assert normalize_date("2026-08-09") == "2026-08-09"

    def test_iso_datetime_is_truncated(self):
        assert normalize_date("2026-08-09T14:33:00Z") == "2026-08-09"

    def test_german_date(self):
        assert normalize_date("9.8.2026") == "2026-08-09"

    def test_unknown_format_is_kept(self):
        assert normalize_date("irgendwann") == "irgendwann"


class TestExtractRow:
    """extract_row liest die Zusatzspalten, wenn sie da sind."""

    def _row(self, **overrides):
        row = {
            "list_type": "Folder",
            "list_name": "hobbit",
            "collection": "mtg",
            "quantity": "4",
            "card_name": "Attercop",
            "set_code": "HOB",
            "set_name": "The Hobbit",
            "card_number": "116",
            "condition": "NearMint",
            "printing": "Normal",
            "rarity": "common",
            "language": "en",
            "price_bought": "0.24",
            "date_bought": "2026-08-09",
            "current_price_(cardmarket_avgsellprice)": "0.31",
        }
        row.update(overrides)
        return row

    def test_all_fields_extracted(self):
        fields, error = extract_row(self._row())
        assert error is None
        assert fields["name"] == "Attercop"
        assert fields["set_code"] == "hob"          # auf Scryfall normalisiert
        assert fields["set_name"] == "The Hobbit"
        assert fields["collector_number"] == "116"
        assert fields["language"] == "en"
        assert fields["foil"] is False
        assert fields["condition"] == "NM"
        assert fields["condition_raw"] == "NearMint"
        assert fields["quantity"] == 4
        assert fields["price"] == 0.24
        assert fields["market_price"] == 0.31
        assert fields["rarity"] == "common"
        assert fields["date_bought"] == "2026-08-09"
        assert fields["source_list"] == "hobbit"

    def test_foil_from_printing(self):
        fields, error = extract_row(self._row(printing="Foil"))
        assert error is None
        assert fields["foil"] is True

    def test_market_price_column_variant(self):
        """Die Preisquelle steht im Spaltennamen und ist konfigurierbar."""
        row = self._row()
        del row["current_price_(cardmarket_avgsellprice)"]
        row["current_price_(cardmarket_trendprice)"] = "1.50"
        fields, error = extract_row(row)
        assert error is None
        assert fields["market_price"] == 1.50

    def test_dragonshield_layout_still_works(self):
        """Der Dragonshield-Export hat keine Rarity-Spalte — kein Fehler."""
        fields, error = extract_row(
            {
                "folder_name": "Ordner 1",
                "quantity": "1",
                "card_name": "Ezio, Brash Novice",
                "set_code": "ACR",
                "card_number": "12",
                "condition": "NearMint",
                "printing": "Foil",
                "language": "English",
                "price_bought": "1,50",     # deutsches Dezimalkomma
                "date_bought": "9.8.2026",
                "market": "2.00",
            }
        )
        assert error is None
        assert fields["name"] == "Ezio, Brash Novice"
        assert fields["set_code"] == "acr"
        assert fields["language"] == "en"
        assert fields["condition"] == "NM"
        assert fields["rarity"] == ""
        assert fields["price"] == 1.50
        assert fields["market_price"] == 2.00
        assert fields["date_bought"] == "2026-08-09"

    def test_missing_market_price_is_none(self):
        row = self._row()
        row["current_price_(cardmarket_avgsellprice)"] = ""
        fields, error = extract_row(row)
        assert error is None
        assert fields["market_price"] is None


class TestScanAppBulkUpload:
    """Ende-zu-Ende über _process_bulk_upload mit dem echten Spaltenlayout."""

    def test_row_is_enriched_and_queued_with_extras(self, monkeypatch):
        monkeypatch.setattr(web, "find_by_identity", _echo_identity)
        monkeypatch.setattr(web, "list_folders", lambda: [])
        _reset()

        csv_content = SCANAPP_HEADER + (
            "Folder,hobbit,mtg,,,4,Attercop,HOB,The Hobbit,116,NearMint,Normal,"
            "common,en,0.24,2026-08-09,,,0.31,176784,\n"
        )
        _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

        assert web.NEEDS_REVIEW == []
        assert len(UPLOAD_QUEUE) == 1
        entry = UPLOAD_QUEUE[0]
        assert entry["name"] == "Attercop"
        assert entry["set_code"] == "hob"
        assert entry["collector_number"] == "116"
        assert entry["quantity"] == 4
        assert entry["condition"] == "NM"
        assert entry["rarity"] == "common"
        assert entry["date_bought"] == "2026-08-09"
        assert entry["market_price"] == 0.31
        assert entry["price"] == 0.24
        _reset()

    def test_comma_in_name_with_full_layout(self, monkeypatch):
        """Kartennamen mit Komma bleiben ein Feld (echter CSV-Parser)."""
        monkeypatch.setattr(web, "find_by_identity", _echo_identity)
        monkeypatch.setattr(web, "list_folders", lambda: [])
        _reset()

        csv_content = SCANAPP_HEADER + (
            'Folder,hobbit,mtg,,,1,"Balin, Loremaster",HOB,The Hobbit,87,NearMint,'
            'Normal,rare,en,0.30,2026-08-09,,,0.30,176784,\n'
        )
        _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

        assert len(UPLOAD_QUEUE) == 1
        assert UPLOAD_QUEUE[0]["name"] == "Balin, Loremaster"
        _reset()

    def test_double_faced_name_and_foil_variants_stay_separate(self, monkeypatch):
        """Foil und Normal derselben Karte sind zwei Zeilen und bleiben getrennt."""
        monkeypatch.setattr(web, "find_by_identity", _echo_identity)
        monkeypatch.setattr(web, "list_folders", lambda: [])
        _reset()

        csv_content = SCANAPP_HEADER + (
            "Folder,hobbit,mtg,,,1,Along the Crooked Way,HOB,The Hobbit,60,NearMint,"
            "Foil,rare,en,0.73,2026-08-09,,,0.73,176784,\n"
            "Folder,hobbit,mtg,,,1,Along the Crooked Way,HOB,The Hobbit,60,NearMint,"
            "Normal,rare,en,3.48,2026-08-09,,,3.48,176784,\n"
            "Folder,hobbit,mtg,,,1,An Unexpected Party // At the Door,HOB,The Hobbit,29,"
            "NearMint,Normal,rare,en,1.79,2026-08-09,,,1.79,176784,\n"
        )
        _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

        assert len(UPLOAD_QUEUE) == 3
        assert UPLOAD_QUEUE[0]["foil"] is True
        assert UPLOAD_QUEUE[1]["foil"] is False
        assert UPLOAD_QUEUE[2]["name"] == "An Unexpected Party // At the Door"
        _reset()

    def test_unknown_set_still_goes_to_needs_review(self, monkeypatch):
        """Auch im neuen Layout wird nichts ohne Scryfall-Identität importiert."""
        monkeypatch.setattr(web, "find_by_identity", lambda *a, **k: None)
        monkeypatch.setattr(web, "list_folders", lambda: [])
        _reset()

        csv_content = SCANAPP_HEADER + (
            "Folder,hobbit,mtg,,,1,Mystery Card,ZZZ,Nowhere,999,NearMint,Normal,"
            "rare,en,1.00,2026-08-09,,,1.00,176784,\n"
        )
        _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

        assert UPLOAD_QUEUE == []
        assert len(web.NEEDS_REVIEW) == 1
        review = web.NEEDS_REVIEW[0]
        assert review["rarity"] == "rare"
        assert review["date_bought"] == "2026-08-09"
        assert "Kein Scryfall-Treffer" in review["reason"]
        _reset()


class TestExtrasArePersisted:
    """Die Zusatzfelder landen auch wirklich in der Datenbank."""

    def _setup_db(self, tmp_path):
        import TCGInventory
        import TCGInventory.setup_db as setup_db
        import TCGInventory.auth as auth
        from TCGInventory import lager_manager

        db = str(tmp_path / "scanapp.db")
        for mod in (TCGInventory, setup_db, auth, lager_manager):
            mod.DB_FILE = db
        setup_db.initialize_database()
        return db, lager_manager

    def test_add_card_stores_rarity_date_and_market_price(self, tmp_path):
        import sqlite3

        db, lager_manager = self._setup_db(tmp_path)
        lager_manager.add_or_increment_card(
            "Attercop", "hob", "en", "NM", 0.24, quantity=4,
            folder_id=None, collector_number="116", foil=False,
            rarity="common", date_bought="2026-08-09", market_price=0.31,
        )

        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT rarity, date_bought, market_price, price, condition "
                "FROM cards WHERE collector_number='116'"
            ).fetchone()
        assert row == ("common", "2026-08-09", 0.31, 0.24, "NM")

    def test_legacy_positional_call_still_works(self, tmp_path):
        """Bestehende Aufrufer ohne die neuen Felder bleiben unverändert gültig."""
        import sqlite3

        db, lager_manager = self._setup_db(tmp_path)
        lager_manager.add_or_increment_card(
            "Sol Ring", "cmr", "de", "MT", 2.0, 1, None, "", None, "472",
            "", "", False, "card", "",
        )

        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT rarity, date_bought, market_price FROM cards "
                "WHERE collector_number='472'"
            ).fetchone()
        assert row == ("", "", None)
