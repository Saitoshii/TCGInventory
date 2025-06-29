import os
import sys
import json
import types
from pathlib import Path

# Stub out heavy dependencies used by card_scanner
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import TCGInventory.card_scanner as cs  # noqa: E402


def test_find_variant(tmp_path):
    card_data = [
        {
            "id": "xyz",
            "name": "Sample Card",
            "set": "ABC",
            "lang": "en",
            "collector_number": "007",
            "image_uris": {"normal": "url"},
        }
    ]
    json_file = tmp_path / "cards.json"
    json_file.write_text(json.dumps(card_data))

    cs.DEFAULT_CARDS_PATH = json_file
    cs._DB_CONN = None
    cs._CARDS_BY_ID.clear()
    cs._CARDS_BY_NAME.clear()

    variant = cs.find_variant("Sample Card", "ABC")
    assert variant
    assert variant["collector_number"] == "007"

