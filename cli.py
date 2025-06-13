import os

from TCGInventory.lager_manager import (
    add_card,
    add_storage_slot,
    update_card,
    delete_card,
    list_all_cards,
)
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE
from TCGInventory.card_scanner import scan_and_queue, SCANNER_QUEUE
from TCGInventory.cardmarket_api import upload_card, CardmarketClient

MKM_CLIENT = CardmarketClient.from_env()



def initialize_if_needed() -> None:
    """Create the database if it does not yet exist."""
    if not os.path.exists(DB_FILE):
        initialize_database()
        print("ℹ️  Datenbank initialisiert.")

def show_menu():
    print("\n🎴 MTG Lagerverwaltung")
    print("1. Karte hinzufügen")
    print("2. Alle Karten anzeigen")
    print("3. Karte bearbeiten")
    print("4. Karte löschen")
    print("5. Lagerplatz hinzufügen")
    print("6. Karte scannen (Bild)")
    print("7. Karte aus Queue hochladen")
    print("8. Preis auf Cardmarket aktualisieren")
    print("9. Verkäufe abrufen und als PDF speichern")
    print("0. Beenden")


def _get_float(prompt: str) -> float:
    """Prompt the user for a float until valid input is given."""
    while True:
        try:
            return float(input(prompt))
        except ValueError:
            print("Bitte eine gültige Zahl eingeben.")


def _get_int(prompt: str) -> int:
    """Prompt the user for an integer until valid input is given."""
    while True:
        try:
            return int(input(prompt))
        except ValueError:
            print("Bitte eine gültige Ganzzahl eingeben.")

def run():
    initialize_if_needed()
    while True:
        show_menu()
        choice = input("➤ Auswahl: ")

        if choice == "1":
            name = input("Kartennamen: ")
            set_code = input("Set-Code (z. B. M21): ")
            language = input("Sprache: ")
            condition = input("Zustand (z. B. Near Mint): ")
            price = _get_float("Preis (€): ")
            storage_code = input("Lagercode (z. B. O01-S01-H01): ")
            cardmarket_id = input("Cardmarket-ID (optional): ")
            add_card(name, set_code, language, condition, price, storage_code, cardmarket_id)

        elif choice == "2":
            list_all_cards()

        elif choice == "3":
            card_id = _get_int("Karten-ID zum Bearbeiten: ")
            field = input("Welches Feld bearbeiten? (z. B. price): ")
            if field == "price":
                value = _get_float("Neuer Wert: ")
            else:
                value = input("Neuer Wert: ")
            update_card(card_id, **{field: value})

        elif choice == "4":
            card_id = _get_int("Karten-ID zum Löschen: ")
            delete_card(card_id)

        elif choice == "5":
            code = input("Neuer Lagerplatz-Code (z. B. O02-S04-H09): ")
            add_storage_slot(code)

        elif choice == "6":
            path = input("Pfad zum Kartenbild: ")
            scan_and_queue(path)

        elif choice == "7":
            if SCANNER_QUEUE.empty():
                print("⚠️ Keine Karten in der Queue.")
            else:
                card = SCANNER_QUEUE.get()
                upload_card(card)

        elif choice == "8":
            article_id = _get_int("Cardmarket Artikel-ID: ")
            new_price = _get_float("Neuer Preis (€): ")
            MKM_CLIENT.update_price(article_id, new_price)

        elif choice == "9":
            sales = MKM_CLIENT.fetch_sales()
            if not sales:
                print("Keine Verkäufe gefunden.")
            else:
                path = input("PDF-Datei speichern unter: ")
                MKM_CLIENT.sales_to_pdf(sales, path)

        elif choice == "0":
            print("👋 Programm beendet.")
            break

        else:
            print("❌ Ungültige Eingabe!")

if __name__ == "__main__":
    run()
