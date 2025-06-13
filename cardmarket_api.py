"""Small wrapper around the Cardmarket API.

This module provides a minimal client to communicate with the Cardmarket
web service.  The real API requires OAuth1 authentication.  The necessary
credentials are expected via the environment variables ``MKM_APP_TOKEN``,
``MKM_APP_SECRET``, ``MKM_TOKEN`` and ``MKM_TOKEN_SECRET``.

Only a very small subset of functionality is implemented here as the actual API
access is not available yet.  The helper methods will print status messages so
the behaviour can be verified without valid credentials.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List

import requests
from requests_oauthlib import OAuth1
from fpdf import FPDF


class CardmarketClient:
    """Helper class for Cardmarket operations."""

    BASE_URL = "https://api.cardmarket.com/ws/v2.0/output.json"

    def __init__(self, app_token: str, app_secret: str, token: str, token_secret: str) -> None:
        self.auth = OAuth1(app_token, app_secret, token, token_secret)

    @classmethod
    def from_env(cls) -> "CardmarketClient":
        """Create a client using credentials stored in environment variables."""
        return cls(
            os.environ.get("MKM_APP_TOKEN", ""),
            os.environ.get("MKM_APP_SECRET", ""),
            os.environ.get("MKM_TOKEN", ""),
            os.environ.get("MKM_TOKEN_SECRET", ""),
        )

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------
    def upload_card(self, card: Dict) -> None:
        """Upload card data to Cardmarket (simplified placeholder)."""
        if not card.get("cardmarket_id"):
            print("⚠️  Keine Cardmarket-ID vorhanden. Upload übersprungen.")
            return

        url = f"{self.BASE_URL}/stock"
        data = {
            "idProduct": card["cardmarket_id"],
            "count": 1,
            "price": card.get("price", 0),
            "language": card.get("language", "en"),
            "condition": card.get("condition", "NM"),
        }
        try:
            resp = requests.post(url, auth=self.auth, data=data)
            if resp.status_code == 200:
                print(
                    f"🔗 Karte '{card['name']}' zu Cardmarket hochgeladen."  # type: ignore[index]
                )
            else:
                print(
                    f"❌ Upload fehlgeschlagen ({resp.status_code}): {resp.text}"
                )
        except requests.RequestException as exc:
            print(f"❌ Netzwerkfehler beim Upload: {exc}")

    def update_price(self, article_id: int, new_price: float) -> None:
        """Update the price of an existing article."""
        url = f"{self.BASE_URL}/stock/article/{article_id}"
        try:
            resp = requests.put(url, auth=self.auth, data={"price": new_price})
            if resp.status_code == 200:
                print(f"💰 Preis für Artikel {article_id} aktualisiert.")
            else:
                print(
                    f"❌ Preisaktualisierung fehlgeschlagen ({resp.status_code}): {resp.text}"
                )
        except requests.RequestException as exc:
            print(f"❌ Netzwerkfehler beim Aktualisieren des Preises: {exc}")

    # ------------------------------------------------------------------
    # Sales helpers
    # ------------------------------------------------------------------
    def fetch_sales(self) -> List[Dict]:
        """Retrieve the current sales from Cardmarket."""
        url = f"{self.BASE_URL}/orders?sales=1"
        try:
            resp = requests.get(url, auth=self.auth)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"❌ Fehler beim Abrufen der Verkäufe: {exc}")
            return []

        data = resp.json()
        return data.get("order", [])

    def sales_to_pdf(self, sales: Iterable[Dict], path: str) -> None:
        """Generate a simple PDF from sales information."""
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)

        pdf.cell(0, 10, "Verkäufe", ln=True)
        pdf.ln(4)

        headers = ["ID", "Käufer", "Adresse", "Preis"]
        pdf.set_font_size(10)
        pdf.cell(20, 10, headers[0])
        pdf.cell(50, 10, headers[1])
        pdf.cell(80, 10, headers[2])
        pdf.cell(20, 10, headers[3], ln=True)

        for sale in sales:
            pdf.cell(20, 10, str(sale.get("idOrder", "")))
            pdf.cell(50, 10, str(sale.get("buyer", {}).get("name", "")))
            pdf.cell(80, 10, str(sale.get("buyer", {}).get("address", "")))
            pdf.cell(20, 10, str(sale.get("price", "")), ln=True)

        pdf.output(path)
        print(f"📄 Verkaufsübersicht gespeichert unter: {path}")


# Convenience instance for module level functions ------------------------------
_CLIENT = CardmarketClient.from_env()


def upload_card(card: Dict) -> None:
    """Backward compatible wrapper around ``CardmarketClient.upload_card``."""
    _CLIENT.upload_card(card)

