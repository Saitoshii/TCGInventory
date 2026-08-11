"""Sortierung der Ordner-Übersicht.

Nach einem Bulk-Import fiel auf, dass die Reihenfolge im Ordner an einigen
Stellen nicht stimmt. Die Ursache war nicht ein Fehler, sondern vier: SQLite
vergleicht Text zeichenweise, kennt keinen zweiten Sortierschlüssel und
sortiert NULL nach oben.
"""

import os
import sqlite3
import sys
import types

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import sortierung  # noqa: E402


def _sortiere(karten, schluessel):
    return [k[0] for k in sorted(karten, key=schluessel)]


# =========================================================================
# Lagerplätze
# =========================================================================

def test_zahl_wird_als_zahl_verglichen():
    """S10 gehört hinter S2, nicht dazwischen."""
    plaetze = ["O01-S10-P1", "O01-S02-P1", "O01-S01-P1", "O01-S09-P9"]
    assert sorted(plaetze, key=sortierung.platz) == [
        "O01-S01-P1", "O01-S02-P1", "O01-S09-P9", "O01-S10-P1"]


def test_platz_ohne_fuehrende_null_landet_richtig():
    """Von Hand angelegte Plätze sind nicht immer aufgefüllt."""
    plaetze = ["O01-S10-P1", "O1-S3-P4", "O01-S02-P1"]
    assert sorted(plaetze, key=sortierung.platz) == [
        "O01-S02-P1", "O1-S3-P4", "O01-S10-P1"]


def test_karten_ohne_platz_stehen_am_ende():
    """Displays und Zubehör haben keinen Platz — sie gehören nach unten."""
    plaetze = [None, "O01-S01-P1", "", "   ", "O01-S02-P1"]
    sortiert = sorted(plaetze, key=sortierung.platz)
    assert sortiert[:2] == ["O01-S01-P1", "O01-S02-P1"]
    assert all(not (p or "").strip() for p in sortiert[2:])


def test_zweistellige_ordner():
    plaetze = ["O10-S01-P1", "O2-S01-P1", "O01-S01-P1"]
    assert sorted(plaetze, key=sortierung.platz) == [
        "O01-S01-P1", "O2-S01-P1", "O10-S01-P1"]


# =========================================================================
# Namen
# =========================================================================

def test_grossschreibung_zaehlt_nicht():
    """Binär sortiert stünde 'brainstorm' hinter 'Zombie Ogre'."""
    namen = ["Zombie Ogre", "brainstorm", "Ancestral Recall"]
    assert sorted(namen, key=sortierung.alphabet) == [
        "Ancestral Recall", "brainstorm", "Zombie Ogre"]


def test_sonderzeichen_stehen_beim_grundbuchstaben():
    namen = ["Zombie Ogre", "Æther Vial", "Jötun Grunt", "Márton Stromgald",
             "Ancestral Recall"]
    assert sorted(namen, key=sortierung.alphabet) == [
        "Æther Vial", "Ancestral Recall", "Jötun Grunt", "Márton Stromgald",
        "Zombie Ogre"]


def test_umlaute_und_scharfes_s():
    namen = ["Kartenhüllen", "Kartenhalter", "Straße", "Strasse"]
    sortiert = sorted(namen, key=sortierung.alphabet)
    assert sortiert[0] == "Kartenhalter" and sortiert[1] == "Kartenhüllen"
    # ß und ss werden gleich behandelt; die Reihenfolge untereinander ist
    # dann stabil, aber beide stehen hinter 'Karten…'.
    assert set(sortiert[2:]) == {"Straße", "Strasse"}


def test_leerer_name_bricht_nicht():
    assert sortierung.alphabet(None) == ""
    assert sortierung.alphabet("") == ""


# =========================================================================
# Sammlernummern
# =========================================================================

def test_nummern_ohne_fuehrende_null():
    nummern = ["100", "2", "10", "1", "20", "9"]
    assert sorted(nummern, key=sortierung.nummer) == [
        "1", "2", "9", "10", "20", "100"]


def test_nummern_mit_buchstabenzusatz():
    nummern = ["281b", "281a", "281", "28"]
    assert sorted(nummern, key=sortierung.nummer) == [
        "28", "281", "281a", "281b"]


def test_ohne_nummer_ans_ende():
    nummern = [None, "012", "003"]
    assert sorted(nummern, key=sortierung.nummer) == ["003", "012", None]


# =========================================================================
# Zusammenspiel: so sieht die Ordner-Übersicht aus
# =========================================================================

#: Spaltenreihenfolge wie in list_folders_view.
SPALTEN = "id name set_code quantity storage_code collector_number foil bild".split()
N, P, NR = 1, 4, 5


def _karten():
    """Ein Ordner, wie er nach einem Bulk-Import aussieht."""
    roh = [
        ("Zombie Ogre",       "O01-S01-P1", "045"),
        ("Ancestral Recall",  "O01-S01-P1", "012"),   # drei Karten,
        ("Mox Pearl",         "O01-S01-P1", "003"),   # ein Platz
        ("Birds of Paradise", "O01-S01-P2", "128"),
        ("Counterspell",      "O01-S02-P1", "050"),
        ("Lightning Bolt",    "O01-S10-P1", "161"),
        ("Æther Vial",        "O01-S02-P2", "049"),
        ("brainstorm",        "O01-S02-P3", "051"),
        ("Serra Angel",       "O1-S3-P4",   "021"),
        ("Booster Display",   None,         None),
    ]
    return [(i, name, "tst", 1, platz, nr, 0, None)
            for i, (name, platz, nr) in enumerate(roh, start=1)]


def test_ordner_reihenfolge_platz_dann_name():
    """Die Voreinstellung: wie im Binder, innerhalb eines Platzes alphabetisch."""
    sortiert = sorted(_karten(), key=lambda k: (sortierung.platz(k[P]),
                                                sortierung.alphabet(k[N])))
    assert [k[N] for k in sortiert] == [
        "Ancestral Recall", "Mox Pearl", "Zombie Ogre",   # alle auf S01-P1
        "Birds of Paradise",
        "Counterspell", "Æther Vial", "brainstorm",
        "Serra Angel",                                    # O1-S3 zwischen S02 und S10
        "Lightning Bolt",
        "Booster Display",                                # ohne Platz ans Ende
    ]


def test_gleicher_platz_ist_alphabetisch():
    """Der eigentliche Auslöser: drei Karten auf einem Platz, ungeordnet."""
    sortiert = sorted(_karten(), key=lambda k: (sortierung.platz(k[P]),
                                                sortierung.alphabet(k[N])))
    auf_p1 = [k[N] for k in sortiert if k[P] == "O01-S01-P1"]
    assert auf_p1 == sorted(auf_p1)


def test_reihenfolge_ist_stabil_und_wiederholbar():
    """Zweimal sortieren ergibt zweimal dasselbe."""
    def schluessel(k):
        return (sortierung.platz(k[P]), sortierung.alphabet(k[N]))
    einmal = sorted(_karten(), key=schluessel)
    assert sorted(einmal, key=schluessel) == einmal


# =========================================================================
# Die Seite selbst
# =========================================================================

def _ordner_datenbank(pfad):
    """Ein Ordner mit Karten, wie er nach einem Bulk-Import aussieht."""
    conn = sqlite3.connect(pfad)
    conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT, "
                 "pages INTEGER)")
    conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "name TEXT, set_code TEXT, quantity INTEGER, storage_code TEXT, "
                 "collector_number TEXT, foil INTEGER, image_url TEXT, "
                 "folder_id INTEGER)")
    conn.execute("INSERT INTO folders (id, name, pages) VALUES (1, 'Binder 1', 9)")
    for _, name, platz, nr in [(0, n, p, x) for n, p, x in [
            ("Zombie Ogre", "O01-S01-P1", "045"),
            ("Ancestral Recall", "O01-S01-P1", "012"),
            ("Mox Pearl", "O01-S01-P1", "003"),
            ("Lightning Bolt", "O01-S10-P1", "161"),
            ("Counterspell", "O01-S02-P1", "050"),
            ("Booster Display", None, None)]]:
        conn.execute(
            "INSERT INTO cards (name, set_code, quantity, storage_code, "
            "collector_number, foil, image_url, folder_id) "
            "VALUES (?, 'tst', 1, ?, ?, 0, NULL, 1)", (name, platz, nr))
    conn.commit()
    conn.close()


def _reihenfolge(seite, namen):
    """Positionen der Namen im gerenderten HTML."""
    return [seite.index(n) for n in namen]


def test_ordnerseite_zeigt_platz_dann_name(tmp_path, monkeypatch):
    """Der Test, der den gemeldeten Fehler gefunden hätte."""
    from TCGInventory import lager_manager, web
    db = str(tmp_path / "t.db")
    _ordner_datenbank(db)
    monkeypatch.setattr(web, "DB_FILE", db)
    monkeypatch.setattr(lager_manager, "DB_FILE", db)

    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    seite = client.get("/folders").get_data(as_text=True)

    # Drei Karten auf demselben Platz: alphabetisch.
    stellen = _reihenfolge(seite, ["Ancestral Recall", "Mox Pearl", "Zombie Ogre"])
    assert stellen == sorted(stellen)
    # S10 gehört hinter S02, nicht dazwischen.
    assert seite.index("Counterspell") < seite.index("Lightning Bolt")
    # Ohne Platz ans Ende.
    assert seite.index("Lightning Bolt") < seite.index("Booster Display")


def test_ordnerseite_voreinstellung_ist_der_lagerort(tmp_path, monkeypatch):
    from TCGInventory import lager_manager, web
    db = str(tmp_path / "t.db")
    _ordner_datenbank(db)
    monkeypatch.setattr(web, "DB_FILE", db)
    monkeypatch.setattr(lager_manager, "DB_FILE", db)

    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    namen = ["Ancestral Recall", "Mox Pearl", "Zombie Ogre", "Counterspell",
             "Lightning Bolt", "Booster Display"]
    ohne_angabe = client.get("/folders").get_data(as_text=True)
    nach_lagerort = client.get("/folders?sort=storage").get_data(as_text=True)
    nach_name = client.get("/folders?sort=name").get_data(as_text=True)

    assert _reihenfolge(ohne_angabe, namen) == _reihenfolge(nach_lagerort, namen)
    # Gegenprobe: nach Name sortiert sieht die Seite wirklich anders aus.
    assert _reihenfolge(nach_name, namen) != _reihenfolge(nach_lagerort, namen)


def test_die_datenbank_wird_nicht_angefasst(tmp_path):
    """Sortiert wird beim Anzeigen — Plätze bleiben, wie sie sind."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, name TEXT, "
                 "storage_code TEXT)")
    conn.executemany("INSERT INTO cards (name, storage_code) VALUES (?, ?)",
                     [("Zombie Ogre", "O1-S3-P4"), ("brainstorm", "O01-S02-P3")])
    conn.commit()

    vorher = conn.execute("SELECT id, storage_code FROM cards ORDER BY id").fetchall()
    zeilen = conn.execute("SELECT id, name, storage_code FROM cards").fetchall()
    sorted(zeilen, key=lambda z: sortierung.platz(z[2]))
    nachher = conn.execute("SELECT id, storage_code FROM cards ORDER BY id").fetchall()
    conn.close()

    assert vorher == nachher
