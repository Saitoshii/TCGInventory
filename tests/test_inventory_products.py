"""WP4a: selling frees the slot and refills logically (no archiving),
products (display/accessory) as stock, and the one-time archive cleanup."""

import os
import sys
import types
import sqlite3

# Stub heavy optional dependencies (scanner) so importing web is cheap.
sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory  # noqa: E402
from TCGInventory import web, setup_db, lager_manager  # noqa: E402


def _use_db(tmp_path):
    db = str(tmp_path / "inv.db")
    for mod in (TCGInventory, web, setup_db, lager_manager):
        mod.DB_FILE = db
    setup_db.initialize_database()
    return db


def _slot_occupied(db, code):
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT is_occupied FROM storage_slots WHERE code = ?", (code,)
        ).fetchone()
    return row[0] if row else None


def _card_id(db, name):
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT id FROM cards WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def test_selling_out_frees_slot_and_next_card_refills_it(tmp_path):
    db = _use_db(tmp_path)
    fid = lager_manager.add_folder("Bloomburrow")
    lager_manager.create_binder(fid, 1)  # O01-S01-P1 .. P9

    # First card takes the lowest free slot.
    lager_manager.add_card("Alpha", "BLB", "en", "NM", 1.0, quantity=1, folder_id=fid)
    first = _card_id(db, "Alpha")
    with sqlite3.connect(db) as conn:
        slot = conn.execute("SELECT storage_code FROM cards WHERE id = ?", (first,)).fetchone()[0]
    assert slot == "O01-S01-P1"
    assert _slot_occupied(db, slot) == 1

    # Sell it out -> row removed, slot freed (no archiving).
    lager_manager.sell_card(first)
    assert _card_id(db, "Alpha") is None
    assert _slot_occupied(db, slot) == 0

    # The next card added to the folder refills the freed lowest slot.
    lager_manager.add_card("Beta", "BLB", "en", "NM", 1.0, quantity=1, folder_id=fid)
    with sqlite3.connect(db) as conn:
        beta_slot = conn.execute(
            "SELECT storage_code FROM cards WHERE name = 'Beta'"
        ).fetchone()[0]
    assert beta_slot == "O01-S01-P1"
    assert _slot_occupied(db, beta_slot) == 1


def test_shared_slot_stays_occupied_until_last_card_leaves(tmp_path):
    db = _use_db(tmp_path)
    lager_manager.add_storage_slot("SHARED-1")
    # Two different cards on the same physical slot (allowed per CLAUDE.md).
    lager_manager.add_card("One", "SET", "en", "", 1.0, quantity=1, storage_code="SHARED-1")
    lager_manager.add_card("Two", "SET", "en", "", 1.0, quantity=1, storage_code="SHARED-1")
    assert _slot_occupied(db, "SHARED-1") == 1

    lager_manager.sell_card(_card_id(db, "One"))
    # Still occupied because "Two" remains on it.
    assert _slot_occupied(db, "SHARED-1") == 1

    lager_manager.sell_card(_card_id(db, "Two"))
    assert _slot_occupied(db, "SHARED-1") == 0


def test_product_has_no_binder_slot_and_sells_by_quantity(tmp_path):
    db = _use_db(tmp_path)
    lager_manager.add_card(
        "Bloomburrow Booster Box", "", "", "", 129.0, quantity=2,
        item_type="display", location_hint="Regal A",
    )
    pid = _card_id(db, "Bloomburrow Booster Box")
    with sqlite3.connect(db) as conn:
        storage, hint, itype = conn.execute(
            "SELECT storage_code, location_hint, item_type FROM cards WHERE id = ?", (pid,)
        ).fetchone()
    assert (storage in (None, "")) and hint == "Regal A" and itype == "display"

    lager_manager.sell_card(pid)          # 2 -> 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT quantity FROM cards WHERE id = ?", (pid,)).fetchone()[0] == 1
    lager_manager.sell_card(pid)          # 1 -> 0 -> removed
    assert _card_id(db, "Bloomburrow Booster Box") is None


def test_product_is_offered_as_order_candidate_by_name(tmp_path):
    db = _use_db(tmp_path)
    lager_manager.add_card(
        "Bloomburrow Booster Box", "", "", "", 129.0, quantity=1,
        item_type="display", location_hint="Regal A",
    )
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = web._order_item_candidates(conn.cursor(), "Bloomburrow Booster Box")
    assert any(r["name"] == "Bloomburrow Booster Box" for r in rows)


def test_manual_add_tops_up_identical_card_on_existing_slot(tmp_path):
    db = _use_db(tmp_path)
    fid = lager_manager.add_folder("TLA")  # folder name doubles as set_code
    lager_manager.create_binder(fid, 1)

    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    form = {
        "item_type": "card", "name": "Rumble Arena", "folder_id": str(fid),
        "collector_number": "42", "language": "en", "condition": "NM",
        "price": "1.0", "quantity": "1", "page": "", "slot": "",
    }
    client.post("/cards/add", data=dict(form))
    # Same identity, even a different condition, must top up (condition ignored).
    client.post("/cards/add", data=dict(form, condition="LP"))

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT quantity, storage_code FROM cards WHERE name = 'Rumble Arena'"
        ).fetchall()
        occupied = conn.execute(
            "SELECT COUNT(*) FROM storage_slots WHERE is_occupied = 1"
        ).fetchone()[0]
    assert len(rows) == 1           # one row, not two
    assert rows[0][0] == 2          # quantity topped up on the existing card
    assert occupied == 1            # only one slot consumed


def test_cleanup_removes_archived_rows_and_frees_slots(tmp_path):
    db = _use_db(tmp_path)
    lager_manager.add_storage_slot("O01-S01-P1")
    # Simulate a legacy archived row still holding its slot.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, quantity, storage_code, status, item_type) "
            "VALUES ('OldSold', 'SET', 0, 'O01-S01-P1', 'archiviert', 'card')"
        )
        conn.execute("UPDATE storage_slots SET is_occupied = 1 WHERE code = 'O01-S01-P1'")
        conn.commit()

    assert lager_manager.count_archived() == 1
    removed, freed = lager_manager.cleanup_archived()
    assert removed == 1 and freed == 1
    assert _card_id(db, "OldSold") is None
    assert _slot_occupied(db, "O01-S01-P1") == 0
    assert lager_manager.count_archived() == 0


def test_cleanup_route_reports_and_clears(tmp_path):
    db = _use_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, quantity, status, item_type) "
            "VALUES ('Ghost', 'SET', 0, 'archiviert', 'card')"
        )
        conn.commit()

    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    body = client.get("/system/inventar-aufraeumen").get_data(as_text=True)
    assert "1" in body  # preview shows one archived row

    resp = client.post("/system/inventar-aufraeumen")
    assert resp.status_code == 302
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cards WHERE name = 'Ghost'").fetchone()[0] == 0
