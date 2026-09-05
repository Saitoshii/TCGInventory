"""Positionszeilen aus der Cardmarket-Bestellmail lesen.

Die Zeilen stammen aus echten Bestellmails:

    1x Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: ... 48,50 EUR
    1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR
    1x Smaug, Wicked Worm (The Hobbit) - M - Englisch - NM 2,99 EUR

Hinter dem Namen stehen bei Einzelkarten drei Teile (Seltenheit, Sprache,
Zustand), bei versiegelter Ware nur einer. Nach der Stelle gelesen wurde aus
"- English" die Seltenheit "English", und die Sprache blieb leer -- eine
englische und eine deutsche Booster Box waeren damit nicht auseinanderzuhalten
gewesen.

Eingeordnet wird deshalb nach Inhalt: Sprachen und Zustaende sind je eine
feste, kurze Liste. Was dort vorkommt, ist eindeutig; alles andere bleibt
Seltenheit. Das ist Nachschlagen, kein Raten.
"""

import os
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory.email_parser import parse_position_line         # noqa: E402

PRECON = ("1x Commander: Magic: The Gathering | Teenage Mutant Ninja "
          "Turtles: ... 48,50 EUR")
DISPLAY = "1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR"
EINZELKARTE = "1x Smaug, Wicked Worm (The Hobbit) - M - Englisch - NM 2,99 EUR"


def test_display_sprache_landet_nicht_in_der_seltenheit():
    """Der gemeldete Fehler: aus "- English" wurde rarity='English'."""
    ergebnis = parse_position_line(DISPLAY)
    assert ergebnis["name"] == "The Hobbit Play Booster Box"
    assert ergebnis["set_name"] == "The Hobbit"
    assert ergebnis["language"] == "en"
    assert ergebnis["rarity"] is None
    assert ergebnis["condition"] is None
    assert ergebnis["unit_price"] == 168.0


def test_einzelkarte_bleibt_unveraendert():
    """Die Reihenfolge Seltenheit/Sprache/Zustand darf nicht kaputtgehen."""
    ergebnis = parse_position_line(EINZELKARTE)
    assert ergebnis["name"] == "Smaug, Wicked Worm"
    assert ergebnis["rarity"] == "M"
    assert ergebnis["language"] == "en"
    assert ergebnis["condition"] == "NM"


def test_precon_wird_als_unsicher_erkannt():
    """Cardmarket hat gekuerzt — daraus darf nichts geraten werden.

    Der volle Name steht nicht in der Mail und laesst sich nicht
    rekonstruieren. Solche Positionen gehoeren in die Handzuordnung.
    """
    ergebnis = parse_position_line(PRECON)
    assert ergebnis["uncertain"] is True
    assert ergebnis["set_name"] is None
    assert ergebnis["unit_price"] == 48.5
    assert ergebnis["name"].endswith("...")


@pytest.mark.parametrize("suffix, sprache, zustand, seltenheit", [
    ("- English", "en", None, None),
    ("- Deutsch", "de", None, None),
    ("- M - Englisch - NM", "en", "NM", "M"),
    ("- NM - Englisch", "en", "NM", None),          # andere Reihenfolge
    ("- Englisch - NM - M", "en", "NM", "M"),       # und noch eine
    ("- Near Mint", None, "NM", None),
])
def test_reihenfolge_der_teile_ist_egal(suffix, sprache, zustand, seltenheit):
    """Eingeordnet wird nach Inhalt, nicht nach Stelle."""
    zeile = f"1x Irgendwas (The Hobbit) {suffix} 1,00 EUR"
    ergebnis = parse_position_line(zeile)
    assert ergebnis["language"] == sprache
    assert ergebnis["condition"] == zustand
    assert ergebnis["rarity"] == seltenheit
