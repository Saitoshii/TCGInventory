# Gmail Order Ingestion Setup

This guide explains how to configure and use the Gmail-based order ingestion feature for automatically importing Cardmarket orders.

## Overview

The Gmail order ingestion feature automatically fetches and processes "Bitte versenden" (shipping notification) emails from Cardmarket. Orders are parsed and displayed in the "Offene Bestellungen" (Open Orders) tab with card images and storage locations.

## Prerequisites

1. A Gmail account that receives Cardmarket order notifications
2. Google Cloud Console access to create OAuth credentials

## Step 1: Create Gmail OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Gmail API:
   - Go to "APIs & Services" > "Library"
   - Search for "Gmail API"
   - Click "Enable"
4. Create OAuth 2.0 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - If prompted, configure the OAuth consent screen first:
     - User Type: External (or Internal if using Google Workspace)
     - Add your email as a test user
     - Scopes: Add `gmail.readonly` and `gmail.modify`
   - Application type: "Desktop app"
   - Name: "TCG Inventory Gmail Integration"
   - Click "Create"
5. Download the credentials:
   - Click the download icon next to your new OAuth 2.0 Client ID
   - Note the Client ID and Client Secret from the JSON file

## Step 2: Configure Environment Variables

Set the following environment variables with your OAuth credentials:

```bash
export GMAIL_CLIENT_ID="your-client-id-here.apps.googleusercontent.com"
export GMAIL_CLIENT_SECRET="your-client-secret-here"
```

For persistent configuration, add these to your shell profile (`.bashrc`, `.zshrc`, etc.) or create a `.env` file.

## Step 3: Initial OAuth Authentication

The first time the application tries to access Gmail, it will open a browser window for OAuth consent:

1. Start the web application:
   ```bash
   python web.py
   ```

2. Navigate to the "Offene Bestellungen" tab and click "Jetzt synchronisieren" (Sync Now)

3. A browser window will open asking you to:
   - Select your Google account
   - Review the permissions requested
   - Click "Allow"

4. After authorization, a token will be saved to `TCGInventory/data/token.pickle`

5. Future requests will use this token automatically (it refreshes when needed)

**Note:** If you see a warning "This app isn't verified", click "Advanced" and then "Go to TCG Inventory Gmail Integration (unsafe)". This is normal for personal OAuth apps.

## Step 4: Using the Order Ingestion Feature

### Automatic Polling

The application automatically polls Gmail for new orders:
- **Operating Hours:** 11:00 AM - 10:00 PM (server time)
- **Poll Interval:** Every 10-15 minutes
- **Email Filter:** `from:noreply@cardmarket.com subject:"Bitte versenden"`

Orders are automatically marked as processed (via a Gmail label or read status) to prevent duplicates.

### Manual Synchronization

You can manually trigger a sync at any time:
1. Navigate to "Offene Bestellungen"
2. Click "Jetzt synchronisieren"

### Managing Orders

In the "Offene Bestellungen" tab:
- View all open orders with buyer names and order dates
- See card images and storage locations for each item
- Click "Verkauft" to mark an order as sold (removes it from the open orders list)

### Enable/Disable Polling

Use the toggle button in the "Offene Bestellungen" tab to:
- **Enable:** Resume automatic polling during operating hours
- **Disable:** Stop automatic polling (manual sync still works)

## How It Works

1. **Email Fetching:** The service queries Gmail for unprocessed emails matching the Cardmarket filter
2. **Parsing:** Email content is parsed to extract:
   - **Buyer name:** Extracted from the email subject line (e.g., "Bestellung 1250416803 für KohlkopfKlaus: Bitte versenden") or from the email body (e.g., "KohlkopfKlaus hat Bestellung ... bezahlt")
   - **Email date:** The actual date/time the email was sent (from email headers), used for display instead of the ingestion time
   - **Card items:** Quantity + name in formats like "1x Lightning Bolt" or "1x Airbending Lesson (Magic: The Gathering | Avatar: The Last Airbe... 0,02 EUR)"
   - Card names are automatically cleaned to remove price suffixes (e.g., "0,02 EUR") and set information (e.g., "(Magic: The Gathering | ...)")
3. **Card Matching:** For each card, the system searches the inventory database to find:
   - Card image URL (from Scryfall)
   - Storage location code
   - Matching is done with the cleaned card name for better accuracy
4. **Database Storage:** Orders are saved with status "open" and the email date for accurate timestamp display
5. **Email Marking:** Processed emails are labeled "processed-tcg" to avoid re-import

## Troubleshooting

### "Gmail authentication failed"
- Verify `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` are set correctly
- Delete `TCGInventory/data/token.pickle` and re-authenticate

### No orders appear after sync
- Check that your Gmail account actually has Cardmarket emails
- Verify the emails have subject "Bitte versenden"
- Check that emails are not already labeled "processed-tcg"

### Orders missing card images/locations
- This is normal if the card is not in your inventory database
- The order will still appear, just without image/location info

### Token expired errors
- The token should auto-refresh, but if issues persist:
  - Delete `TCGInventory/data/token.pickle`
  - Restart the app to re-authenticate

## Security Notes

- The `token.pickle` file contains sensitive credentials - keep it secure
- Do not commit `token.pickle` to version control (it's in `.gitignore`)
- OAuth credentials should be stored as environment variables, not in code
- The app only requests necessary Gmail permissions (read and label modification)

## Email Format Support

The parser supports various email formats:
- **Quantity formats:** `1x`, `2 x`, `3×`, `1 Stück`
- **Language variations:** English and German
- **Buyer name extraction:**
  - From subject: "Bestellung 1250416803 für KohlkopfKlaus: Bitte versenden" → extracts "KohlkopfKlaus"
  - From body: "KohlkopfKlaus hat Bestellung ... bezahlt" → extracts "KohlkopfKlaus"
  - Fallback patterns: "Käufer:", "Buyer:", "Bestellung von:", "Order from:"
- **Card name cleaning:** Automatically removes price suffixes (e.g., "0,02 EUR", "1,50 EUR") and set information (e.g., "(Magic: The Gathering | Avatar: The Last Airbe...")
- **Date handling:** Uses the email's actual sent date rather than the import time for accurate order timestamps

If your Cardmarket emails have a different format, please open an issue with an example (anonymized).
