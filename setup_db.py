import sqlite3

# Verbindung zur SQLite-Datenbank (wird erstellt, wenn nicht vorhanden)
conn = sqlite3.connect('mtg_lager.db')
cursor = conn.cursor()

# Tabelle 1: Karten
cursor.execute('''
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    set_code TEXT NOT NULL,
    language TEXT,
    condition TEXT,
    price REAL,
    storage_code TEXT,
    cardmarket_id TEXT,
    status TEXT DEFAULT 'verfügbar',
    date_added TEXT
)
''')

# Tabelle 2: Lagerplätze
cursor.execute('''
CREATE TABLE IF NOT EXISTS storage_slots (
    code TEXT PRIMARY KEY,
    is_occupied INTEGER DEFAULT 0
)
''')

# Tabelle 3: Verkaufslog
cursor.execute('''
CREATE TABLE IF NOT EXISTS sales_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER,
    buyer_name TEXT,
    buyer_address TEXT,
    price REAL,
    date_sold TEXT,
    FOREIGN KEY(card_id) REFERENCES cards(id)
)
''')

conn.commit()
conn.close()

print("✅ Datenbank und Tabellen erfolgreich erstellt.")
