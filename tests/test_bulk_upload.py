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
from TCGInventory.web import _process_bulk_upload, UPLOAD_QUEUE


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
