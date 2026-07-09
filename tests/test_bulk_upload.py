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

from TCGInventory import web
from TCGInventory.web import _process_bulk_upload, _parse_csv_bytes, UPLOAD_QUEUE

import pytest


def _reset():
    UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS = 0
    web.BULK_DONE = False
    web.BULK_MESSAGE = None


def _echo_identity(set_code, collector_number, language=None):
    """Stub find_by_identity: pretend every (set, number) resolves. Returns no
    canonical name so the parsed CSV name is kept (lets us assert parsing)."""
    return {
        "set_code": set_code,
        "collector_number": collector_number,
        "language": language or "en",
        "scryfall_id": "sc-" + str(collector_number),
        "cardmarket_id": "cm-" + str(collector_number),
        "image_url": "http://img/" + str(collector_number),
    }


def test_bulk_upload_enriches_and_queues(monkeypatch):
    """A resolvable CSV row is enriched (normalized set, canonical IDs) and queued."""
    monkeypatch.setattr(web, "find_by_identity", _echo_identity)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _reset()

    csv_content = "Card Name,Set Code,Card Number\nSample Card,ACR,123\n"
    _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

    assert web.NEEDS_REVIEW == []
    assert len(UPLOAD_QUEUE) == 1
    entry = UPLOAD_QUEUE[0]
    assert entry["collector_number"] == "123"
    assert entry["set_code"] == "acr"            # normalized to Scryfall convention
    assert entry["scryfall_id"] == "sc-123"
    assert entry["cardmarket_id"] == "cm-123"
    _reset()


def test_bulk_upload_unknown_set_goes_to_needs_review(monkeypatch):
    """A row with no Scryfall match is routed to Needs-Review, not imported."""
    monkeypatch.setattr(web, "find_by_identity", lambda *a, **k: None)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _reset()

    csv_content = "Card Name,Set Code,Card Number\nMystery Card,ZZZ,999\n"
    _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

    assert UPLOAD_QUEUE == []
    assert len(web.NEEDS_REVIEW) == 1
    review = web.NEEDS_REVIEW[0]
    assert review["set_code"] == "zzz"
    assert review["collector_number"] == "999"
    assert "Kein Scryfall-Treffer" in review["reason"]
    _reset()


def test_bulk_upload_missing_identity_goes_to_needs_review(monkeypatch):
    """A row without set code / collector number is malformed -> Needs-Review."""
    monkeypatch.setattr(web, "find_by_identity", _echo_identity)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _reset()

    csv_content = "Card Name,Set Code,Card Number\nJust A Name,,\n"
    _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

    assert UPLOAD_QUEUE == []
    assert len(web.NEEDS_REVIEW) == 1
    assert "fehlt" in web.NEEDS_REVIEW[0]["reason"].lower()
    _reset()


def test_bulk_upload_comma_in_name(monkeypatch):
    """Card names containing commas (quoted) are parsed as a single field."""
    monkeypatch.setattr(web, "find_by_identity", _echo_identity)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _reset()

    csv_content = (
        'sep=,\n'
        'Card Name,Set Code,Card Number\n'
        '"Ezio, Brash Novice",ACR,12\n'
    )
    _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

    assert web.NEEDS_REVIEW == []
    assert len(UPLOAD_QUEUE) == 1
    # _echo_identity returns no name, so the parsed CSV name is kept
    assert UPLOAD_QUEUE[0]["name"] == "Ezio, Brash Novice"
    _reset()


@pytest.mark.parametrize("printing,expected", [("Foil", True), ("Normal", False), ("", False)])
def test_bulk_upload_foil_from_printing(monkeypatch, printing, expected):
    """The foil flag is derived from the Dragonshield 'Printing' column."""
    monkeypatch.setattr(web, "find_by_identity", _echo_identity)
    monkeypatch.setattr(web, "list_folders", lambda: [])
    _reset()

    csv_content = (
        "Card Name,Set Code,Card Number,Printing\n"
        f"Bayek of Siwa,ACR,10,{printing}\n"
    )
    _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content.encode())

    assert len(UPLOAD_QUEUE) == 1
    assert UPLOAD_QUEUE[0]["foil"] is expected
    _reset()


# Tests for _parse_csv_bytes helper function

class TestParseCsvBytes:
    """Unit tests for the _parse_csv_bytes helper function."""

    def test_basic_csv_parsing(self):
        """Basic CSV parsing without separator directive."""
        csv_content = b"Card Name,Set Code,Card Number\nSample Card,ABC,123\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"
        assert rows[0]["Set Code"] == "ABC"
        assert rows[0]["Card Number"] == "123"

    def test_unquoted_sep_directive(self):
        """CSV with unquoted sep=, directive should be handled."""
        csv_content = b"sep=,\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"

    def test_quoted_sep_directive_double_quotes(self):
        """CSV with quoted "sep=," directive should be handled."""
        csv_content = b'"sep=,"\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n'
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"
        assert rows[0]["Set Code"] == "ABC"

    def test_quoted_sep_directive_single_quotes(self):
        """CSV with single-quoted 'sep=,' directive should be handled."""
        csv_content = b"'sep=,'\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"

    def test_mismatched_quotes_not_recognized(self):
        """CSV with mismatched quotes should not be recognized as sep directive."""
        # Mismatched quotes should not match, so we test the regex directly is not matching
        # When first line is not recognized as sep directive, it becomes header
        csv_content = b"sep=,extra\nCard Name,Set Code\nSample Card,ABC\n"
        rows = _parse_csv_bytes(csv_content)
        # First line "sep=,extra" is not valid sep directive, so it becomes header
        # Result should have 2 rows with malformed header keys
        assert len(rows) == 2
        # Verify "Card Name" is NOT a key (because it's data, not header)
        assert "Card Name" not in rows[0]

    def test_sep_directive_with_semicolon(self):
        """CSV with sep=; directive using semicolon delimiter."""
        csv_content = b"sep=;\nCard Name;Set Code;Card Number\nSample Card;ABC;123\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"
        assert rows[0]["Set Code"] == "ABC"

    def test_sep_directive_case_insensitive(self):
        """sep directive should be case insensitive."""
        csv_content = b"SEP=,\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"

    def test_utf8_bom_handling(self):
        """CSV with UTF-8 BOM should be handled correctly."""
        # UTF-8 BOM is \xef\xbb\xbf
        csv_content = b"\xef\xbb\xbfCard Name,Set Code,Card Number\nSample Card,ABC,123\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"

    def test_utf8_bom_with_sep_directive(self):
        """CSV with UTF-8 BOM and sep directive should be handled."""
        csv_content = b'\xef\xbb\xbf"sep=,"\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n'
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert rows[0]["Card Name"] == "Sample Card"

    def test_latin1_fallback(self):
        """CSV with Latin-1 characters should fall back to latin-1 encoding."""
        # Contains German umlaut that's valid in Latin-1 but invalid in UTF-8
        csv_content = b"Card Name,Set Code\nK\xf6nig,ABC\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 1
        assert "König" in rows[0]["Card Name"]

    def test_empty_csv(self):
        """Empty CSV should return empty list."""
        csv_content = b""
        rows = _parse_csv_bytes(csv_content)
        assert rows == []

    def test_header_only_csv(self):
        """CSV with only header row should return empty list."""
        csv_content = b"Card Name,Set Code,Card Number\n"
        rows = _parse_csv_bytes(csv_content)
        assert rows == []

    def test_multiple_rows(self):
        """CSV with multiple rows should be parsed correctly."""
        csv_content = b"Card Name,Set Code\nCard One,SET1\nCard Two,SET2\nCard Three,SET3\n"
        rows = _parse_csv_bytes(csv_content)
        assert len(rows) == 3
        assert rows[0]["Card Name"] == "Card One"
        assert rows[1]["Card Name"] == "Card Two"
        assert rows[2]["Card Name"] == "Card Three"


class TestBulkUploadWithSepDirective:
    """Integration tests for bulk upload with separator directives."""

    def test_bulk_upload_with_quoted_sep_directive(self, monkeypatch):
        """CSV with quoted sep directive should work in bulk upload."""
        monkeypatch.setattr(web, "find_by_identity", _echo_identity)
        monkeypatch.setattr(web, "list_folders", lambda: [])
        _reset()

        # CSV with quoted sep directive followed by header
        csv_content = b'"sep=,"\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n'
        _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content)

        assert len(UPLOAD_QUEUE) == 1
        assert UPLOAD_QUEUE[0]["collector_number"] == "123"
        _reset()

    def test_bulk_upload_with_bom_and_sep(self, monkeypatch):
        """CSV with BOM and sep directive should work in bulk upload."""
        monkeypatch.setattr(web, "find_by_identity", _echo_identity)
        monkeypatch.setattr(web, "list_folders", lambda: [])
        _reset()

        # CSV with UTF-8 BOM and quoted sep directive
        csv_content = b'\xef\xbb\xbf"sep=,"\nCard Name,Set Code,Card Number\nTest Card,XYZ,456\n'
        _process_bulk_upload({"cards": "", "folder_id": None}, None, csv_content)

        assert len(UPLOAD_QUEUE) == 1
        assert UPLOAD_QUEUE[0]["collector_number"] == "456"
        _reset()


class TestDedupeOnImport:
    """The import commit increments quantity for an identical card in the same folder."""

    def _setup_db(self, tmp_path):
        import TCGInventory
        import TCGInventory.setup_db as setup_db
        import TCGInventory.auth as auth
        from TCGInventory import lager_manager

        db = str(tmp_path / "dedupe.db")
        for mod in (TCGInventory, setup_db, auth, lager_manager):
            mod.DB_FILE = db
        setup_db.initialize_database()
        return db, lager_manager

    def test_same_identity_increments_quantity(self, tmp_path):
        import sqlite3
        db, lager_manager = self._setup_db(tmp_path)

        lager_manager.add_or_increment_card(
            "Sol Ring", "cmr", "de", "MT", 2.0, quantity=1,
            folder_id=None, collector_number="472", foil=False,
        )
        lager_manager.add_or_increment_card(
            "Sol Ring", "cmr", "de", "MT", 2.0, quantity=2,
            folder_id=None, collector_number="472", foil=False,
        )

        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT quantity FROM cards WHERE set_code='cmr' AND collector_number='472' "
                "AND language='de' AND foil=0"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 3

    def test_foil_variant_is_separate(self, tmp_path):
        import sqlite3
        db, lager_manager = self._setup_db(tmp_path)

        lager_manager.add_or_increment_card(
            "Sol Ring", "cmr", "de", "MT", 2.0, quantity=1,
            folder_id=None, collector_number="472", foil=False,
        )
        lager_manager.add_or_increment_card(
            "Sol Ring", "cmr", "de", "MT", 2.0, quantity=1,
            folder_id=None, collector_number="472", foil=True,
        )

        with sqlite3.connect(db) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE set_code='cmr' AND collector_number='472'"
            ).fetchone()[0]
        assert total == 2
