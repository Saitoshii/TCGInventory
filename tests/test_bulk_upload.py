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


def test_bulk_upload_preserves_collector_number(monkeypatch):
    """CSV uploads should keep the provided collector number if no variant is found."""
    monkeypatch.setattr(web, "fetch_card_info_by_name", lambda name: {
        "name": name,
        "set_code": "ABC",
        "collector_number": "007",
    })
    monkeypatch.setattr(web, "find_variant", lambda *a, **k: None)
    monkeypatch.setattr(web, "list_folders", lambda: [])

    UPLOAD_QUEUE.clear()
    web.BULK_PROGRESS = 0
    web.BULK_DONE = False
    web.BULK_MESSAGE = None
    form_data = {"cards": "", "folder_id": None}
    csv_content = "Card Name,Set Code,Card Number\nSample Card,ABC,123\n"
    _process_bulk_upload(form_data, None, csv_content.encode())

    assert len(UPLOAD_QUEUE) == 1
    assert UPLOAD_QUEUE[0]["collector_number"] == "123"
    UPLOAD_QUEUE.clear()


def test_bulk_upload_keeps_number_if_variant_found(monkeypatch):
    """Provided collector numbers should not be overwritten by variant data."""
    monkeypatch.setattr(web, "fetch_card_info_by_name", lambda name: {
        "name": name,
        "set_code": "ABC",
        "collector_number": "0123",
    })

    monkeypatch.setattr(
        web,
        "find_variant",
        lambda *a, **k: {
            "name": "Sample Card",
            "set_code": "ABC",
            "collector_number": "0123",
        },
    )
    monkeypatch.setattr(web, "list_folders", lambda: [])

    UPLOAD_QUEUE.clear()

    web.BULK_PROGRESS = 0
    web.BULK_DONE = False
    web.BULK_MESSAGE = None


    form_data = {"cards": "", "folder_id": None}
    csv_content = "Card Name,Set Code,Card Number\nSample Card,ABC,123\n"
    _process_bulk_upload(form_data, None, csv_content.encode())

    assert len(UPLOAD_QUEUE) == 1
    assert UPLOAD_QUEUE[0]["collector_number"] == "123"
    UPLOAD_QUEUE.clear()


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
        monkeypatch.setattr(web, "fetch_card_info_by_name", lambda name: {
            "name": name,
            "set_code": "ABC",
            "collector_number": "007",
        })
        monkeypatch.setattr(web, "find_variant", lambda *a, **k: None)
        monkeypatch.setattr(web, "list_folders", lambda: [])

        UPLOAD_QUEUE.clear()
        web.BULK_PROGRESS = 0
        web.BULK_DONE = False
        web.BULK_MESSAGE = None
        form_data = {"cards": "", "folder_id": None}
        # CSV with quoted sep directive followed by header
        csv_content = b'"sep=,"\nCard Name,Set Code,Card Number\nSample Card,ABC,123\n'
        _process_bulk_upload(form_data, None, csv_content)

        assert len(UPLOAD_QUEUE) == 1
        assert UPLOAD_QUEUE[0]["collector_number"] == "123"
        UPLOAD_QUEUE.clear()

    def test_bulk_upload_with_bom_and_sep(self, monkeypatch):
        """CSV with BOM and sep directive should work in bulk upload."""
        monkeypatch.setattr(web, "fetch_card_info_by_name", lambda name: {
            "name": name,
            "set_code": "ABC",
            "collector_number": "007",
        })
        monkeypatch.setattr(web, "find_variant", lambda *a, **k: None)
        monkeypatch.setattr(web, "list_folders", lambda: [])

        UPLOAD_QUEUE.clear()
        web.BULK_PROGRESS = 0
        web.BULK_DONE = False
        web.BULK_MESSAGE = None
        form_data = {"cards": "", "folder_id": None}
        # CSV with UTF-8 BOM and quoted sep directive
        csv_content = b'\xef\xbb\xbf"sep=,"\nCard Name,Set Code,Card Number\nTest Card,XYZ,456\n'
        _process_bulk_upload(form_data, None, csv_content)

        assert len(UPLOAD_QUEUE) == 1
        assert UPLOAD_QUEUE[0]["collector_number"] == "456"
        UPLOAD_QUEUE.clear()
