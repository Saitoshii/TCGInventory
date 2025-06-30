import sys
import os
import types

# Stub out heavy dependencies used by card_scanner
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import web
from TCGInventory.web import app, UPLOAD_QUEUE


def test_clear_upload_queue():
    app.config['TESTING'] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = 'test'
        UPLOAD_QUEUE.extend([{'name': 'a'}, {'name': 'b'}])
        assert len(UPLOAD_QUEUE) == 2
        resp = client.get('/cards/upload_queue/clear')
        assert resp.status_code == 302
        assert len(UPLOAD_QUEUE) == 0


def test_edit_queue_after_search():
    app.config['TESTING'] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = 'test'
        # Avoid database access in edit_queued_card
        web.list_folders = lambda: []
        UPLOAD_QUEUE.clear()
        UPLOAD_QUEUE.extend([
            {'name': 'Alpha'},
            {'name': 'Beta'},
            {'name': 'Gamma'},
        ])
        resp = client.get('/cards/upload_queue?q=Beta')
        assert resp.status_code == 200
        # The edit link should reference the correct global index (1)
        assert b'/cards/upload_queue/edit/1' in resp.data
        resp = client.get('/cards/upload_queue/edit/1')
        assert resp.status_code == 200
        assert b'value="Beta"' in resp.data
