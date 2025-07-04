from TCGInventory.lager_manager import add_storage_slot, add_card, list_all_cards

# Lagerplatz anlegen
add_storage_slot("O01-S01-P1")

# Karte hinzufügen
add_card(
    name="Lightning Bolt",
    set_code="M11",
    language="Deutsch",
    condition="Near Mint",
    price=1.50,
    quantity=1,
    storage_code="O01-S01-P1",
    cardmarket_id="123456"
)

# Übersicht anzeigen
list_all_cards()
