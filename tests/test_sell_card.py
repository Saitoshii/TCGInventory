import os
import sys
import sqlite3
import types

# Stub heavy dependencies
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import web
from TCGInventory.setup_db import initialize_database
from TCGInventory.lager_manager import add_card


def test_sell_route(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(web, "DB_FILE", str(db))
    monkeypatch.setattr(sys.modules['TCGInventory'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.setup_db'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.lager_manager'], 'DB_FILE', str(db))
    initialize_database()

    add_card("Sample", "SET", "en", "", 0.0, quantity=2)

    with sqlite3.connect(str(db)) as conn:
        card_id = conn.execute("SELECT id FROM cards").fetchone()[0]

    app = web.app
    app.config['TESTING'] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = 'test'
        resp = client.post(f"/cards/{card_id}/sell")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["quantity"] == 1 and not data.get("archived", False)

        resp = client.post(f"/cards/{card_id}/sell")
        data = resp.get_json()
        # Now cards are archived instead of removed
        assert data.get("archived", False)
        assert data["quantity"] == 0

    # Card should still exist but be archived
    with sqlite3.connect(str(db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        status = conn.execute("SELECT status FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
    assert count == 1
    assert status == "archiviert"
