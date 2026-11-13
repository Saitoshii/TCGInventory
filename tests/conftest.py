"""Gemeinsame Vorkehrungen für alle Tests.

Kein Test geht ins Netz. Seit der Import die Kartendaten bei fehlenden Karten
selbst aktualisiert, konnte ein Test über diesen Weg die echten Scryfall-Daten
herunterladen — und in den echten Datenordner schreiben. Hier wird jeder
Netzzugriff abgelehnt; Tests, die ihn brauchen, ersetzen ihn selbst (ihr
``monkeypatch`` greift nach diesem).
"""

import urllib.request

import pytest


class NetzImTestGesperrt(OSError):
    pass


def _gesperrt(*args, **kwargs):
    raise NetzImTestGesperrt("Netzzugriff ist in Tests gesperrt.")


@pytest.fixture(autouse=True)
def _kein_netz(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _gesperrt)
    try:
        import requests
    except ImportError:                       # pragma: no cover
        return
    monkeypatch.setattr(requests, "get", _gesperrt)
    monkeypatch.setattr(requests, "post", _gesperrt)
