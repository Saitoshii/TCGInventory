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
    rename_folder,
    export_inventory_csv,
)
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE
from TCGInventory.auth import (
    init_user_db,
    user_exists,
    register_user,
    verify_user,
)
from getpass import getpass
import time



def initialize_if_needed() -> None:
    """Create the database if it does not yet exist."""
    if not os.path.exists(DB_FILE):
        initialize_database()
        print(Fore.GREEN + "ℹ️  Datenbank initialisiert.")


def authenticate() -> bool:
    """Handle user registration on first run and verify login."""
    init_user_db()
    if not user_exists():
        print(Fore.CYAN + "=== Registrierung ===")
        username = input("Benutzername: ")
        while True:
            pw1 = getpass("Passwort: ")
            pw2 = getpass("Passwort wiederholen: ")
            if pw1 == pw2:
                break
            print(Fore.RED + "Passwörter stimmen nicht überein.")
        register_user(username, pw1)
        print(Fore.GREEN + "Benutzer angelegt.")
        return True
    else:
        for _ in range(3):
            username = input("Benutzername: ")
            pw = getpass("Passwort: ")
            if verify_user(username, pw):
                return True
            print(Fore.RED + "Falsche Anmeldedaten.")
        return False

def show_menu():
    print(Fore.CYAN + "\n🎴 MTG Lagerverwaltung")
    print(Fore.YELLOW + "1. Karte hinzufügen")
    print(Fore.YELLOW + "2. Alle Karten anzeigen")
    print(Fore.YELLOW + "3. Karte bearbeiten")
    print(Fore.YELLOW + "4. Karte löschen")
    print(Fore.YELLOW + "5. Ordner anlegen")
    print(Fore.YELLOW + "6. Ordner umbenennen")
    print(Fore.YELLOW + "7. Karten exportieren")
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
    if not authenticate():
        print(Fore.RED + "Login fehlgeschlagen.")
        return
    last_activity = time.time()
    try:
        while True:
            if time.time() - last_activity > 900:
                print(Fore.YELLOW + "\n⏰ Sitzung abgelaufen. Bitte erneut anmelden.")
                if not authenticate():
                    print(Fore.RED + "Login fehlgeschlagen.")
                    break
                last_activity = time.time()
                continue

            show_menu()
            choice = input("➤ Auswahl: ")
            last_activity = time.time()

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

            elif choice == "6":
                folder_id = _get_int("Ordner-ID zum Umbenennen: ")
                new_name = input("Neuer Name: ")
                rename_folder(folder_id, new_name)

            elif choice == "7":
                folder = input("Ordnername für Export (leer = alle): ").strip() or None
                path = (
                    input("Dateiname für CSV-Export [inventory.csv]: ").strip()
                    or "inventory.csv"
                )
                export_inventory_csv(path, folder)

            elif choice == "0":
                print(Fore.YELLOW + "👋 Programm beendet.")
                break

            else:
                print(Fore.RED + "❌ Ungültige Eingabe!")
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n👋 Programm beendet.")

if __name__ == "__main__":
    run()
