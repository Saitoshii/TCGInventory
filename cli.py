import os

from TCGInventory.lager_manager import (
    add_card,
    create_binder,
    update_card,
    delete_card,
    list_all_cards,
)
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE



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
    print("5. Ordner anlegen")
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
    try:
        while True:
            show_menu()
            choice = input("➤ Auswahl: ")

            if choice == "1":
                name = input("Kartennamen: ")
                set_code = input("Set-Code (z. B. M21): ")
                language = input("Sprache: ")
                condition = input("Zustand (z. B. Near Mint): ")
                price = _get_float("Preis (€): ")
                add_card(
                    name,
                    set_code,
                    language,
                    condition,
                    price,
                )

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
                set_code = input("Set-Code für den Ordner: ")
                pages = _get_int("Anzahl Seiten: ")
                create_binder(set_code, pages)

            elif choice == "0":
                print("👋 Programm beendet.")
                break

            else:
                print("❌ Ungültige Eingabe!")
    except KeyboardInterrupt:
        print("\n👋 Programm beendet.")

if __name__ == "__main__":
    run()
