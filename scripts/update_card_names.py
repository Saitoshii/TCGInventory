"""Download card names from Scryfall and store them locally."""
import requests
from pathlib import Path

URL = "https://api.scryfall.com/catalog/card-names"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "card_names.txt"

def main() -> None:
    try:
        resp = requests.get(URL, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        print(f"Failed to download names: {exc}")
        return
    data = resp.json()
    names = data.get("data", [])
    OUTPUT.write_text("\n".join(names), encoding="utf-8")
    print(f"Saved {len(names)} names to {OUTPUT}")

if __name__ == "__main__":
    main()
