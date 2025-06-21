import os

from colorama import init, Fore, Style

init(autoreset=True)

from TCGInventory.lager_manager import (
    add_card,
    create_binder,
    update_card,
    delete_card,
    list_all_cards,
    add_folder,
)
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE



def initialize_if_needed() -> None:
    """Create the database if it does not yet exist."""
    if not os.path.exists(DB_FILE):
        initialize_database()
        print(Fore.GREEN + "ℹ️  Datenbank initialisiert.")

def show_menu():
    print(Fore.CYAN + "\n🎴 MTG Lagerverwaltung")
    print(Fore.YELLOW + "1. Karte hinzufügen")
    print(Fore.YELLOW + "2. Alle Karten anzeigen")
    print(Fore.YELLOW + "3. Karte bearbeiten")
    print(Fore.YELLOW + "4. Karte löschen")
    print(Fore.YELLOW + "5. Ordner anlegen")
    print(Fore.YELLOW + "0. Beenden")


def _get_float(prompt: str) -> float:
    """Prompt the user for a float until valid input is given."""
    while True:
        try:
            return float(input(prompt))
        except ValueError:
            print(Fore.RED + "Bitte eine gültige Zahl eingeben.")


def _get_int(prompt: str) -> int:
    """Prompt the user for an integer until valid input is given."""
    while True:
        try:
            return int(input(prompt))
        except ValueError:
            print(Fore.RED + "Bitte eine gültige Ganzzahl eingeben.")

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
                quantity = _get_int("Anzahl: ")
                add_card(
                    name,
                    set_code,
                    language,
                    condition,
                    price,
                    quantity,
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
                confirm = input("Sicher löschen (j/n)? ").strip().lower()
                if confirm == "j":
                    delete_card(card_id)
                else:
                    print(Fore.YELLOW + "Löschen abgebrochen.")

            elif choice == "5":
                name = input("Set-Code für den Ordner: ")
                pages = _get_int("Anzahl Seiten: ")
                folder_id = add_folder(name)
                if folder_id is not None:
                    create_binder(folder_id, pages)

            elif choice == "0":
                print(Fore.YELLOW + "👋 Programm beendet.")
                break

            else:
                print(Fore.RED + "❌ Ungültige Eingabe!")
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n👋 Programm beendet.")

if __name__ == "__main__":
    run()
