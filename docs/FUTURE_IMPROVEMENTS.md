# Future Improvements & Suggestions

This document collects potential enhancements for future iterations. It
consolidates the former `FUTURE_IMPROVEMENTS.md` (order-focused, English) and
`VERBESSERUNGSVORSCHLAEGE.md` (general, German) root files. None of these are
required for the current system to function.

---

# Part A — Open Orders (English)

Potential enhancements for the "Offene Bestellungen" feature.

## Security & permissions

### Order deletion permissions
**Current:** any authenticated user can delete any order.
**Suggested:** ensure users can only delete their own orders, or require an
admin role.

```python
# In web.py delete_order endpoint
@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id: int):
    # Verify order belongs to current user or user is admin
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        # Add user_id column to orders table and check ownership
        # Or check if current user is admin
        pass
```

## Accessibility

### Screen-reader support for delete confirmation
**Current:** uses a basic JavaScript `confirm()` dialog.
**Suggested:** an accessible modal dialog with proper ARIA attributes — use a
Bootstrap modal instead of `confirm()`, add ARIA labels/descriptions, keyboard
navigation and focus management.

```html
<div class="modal" role="dialog" aria-labelledby="deleteModalLabel">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 id="deleteModalLabel">Bestellung löschen</h5>
      </div>
      <div class="modal-body">
        Diese Bestellung wirklich löschen? Dies kann nicht rückgängig gemacht werden.
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Abbrechen</button>
        <form method="post" action="...">
          <button type="submit" class="btn btn-danger">Löschen</button>
        </form>
      </div>
    </div>
  </div>
</div>
```

## Robustness

### Configurable `default-cards.db` path
**Current:** the path is hardcoded relative to the module location.
**Suggested:** make it configurable and check multiple locations.

```python
# In __init__.py
DEFAULT_CARDS_DB_PATHS = [
    Path(__file__).resolve().parent / "data" / "default-cards.db",  # Module location
    Path.home() / ".tcginventory" / "default-cards.db",             # User home
    Path("/usr/local/share/tcginventory/default-cards.db"),         # System-wide
]

# In order_service.py
def _get_default_cards_db_path():
    """Find the default-cards.db in standard locations."""
    for path in DEFAULT_CARDS_DB_PATHS:
        if path.exists():
            return path
    return None
```

### Error handling for a missing `default-cards.db`
**Current:** silent failure if the database does not exist.
**Suggested:** log a warning or provide user feedback (e.g. warn once per
process when the DB is not found).

## User experience

- **Batch order operations:** mark multiple orders as sold, delete multiple
  orders, export selected orders.
- **Order filtering & search:** by buyer name, date range, card name, status.
- **Undo delete:** soft delete with a `deleted` flag, a "Trash" view, and
  restore within X days.
- **Order notes:** tracking information, special instructions, communication
  history.

## Testing

- **Integration tests:** browser-based tests of the full order deletion flow,
  order synchronization, and card matching with real data.
- **Performance tests:** 1000+ orders, 10000+ cards; optimize queries as needed.

## Monitoring

- **Order statistics:** total orders this month, average order value, most
  popular cards, buyer statistics.
- **Sync monitoring:** last successful sync timestamp, number of emails
  processed, error logging and alerts.

## Priority

- **High:** order-deletion permissions (security); accessibility improvements.
- **Medium:** configurable DB path (robustness); error handling; order
  filtering.
- **Low:** batch operations; undo delete; order notes; statistics dashboard.

---

# Part B — Allgemeine Verbesserungsvorschläge (Deutsch)

## Bereits implementiert: Upload-Funktion für `default-cards.db`

- Weboberfläche zum Hochladen der `default-cards.db` Datei.
- Route `/upload_database` mit Authentifizierung.
- Validierung: Dateiname muss exakt `default-cards.db` sein, muss eine gültige
  SQLite-Datenbank sein und eine `cards`-Tabelle enthalten.
- Sichere Temporärdatei-Methode zur Vermeidung von Race Conditions; Validierung
  vor dem Überschreiben.
- Maximale Dateigröße: 500 MB.
- Erreichbar über den "Upload DB" Button in der Navigation.
- Unit-Tests für die Datenbankvalidierung.

## Allgemeine Empfehlungen

### 1. Sicherheit und Authentifizierung
- ✅ Session-Timeout ist bereits implementiert (15 Minuten).
- HTTPS für die Weboberfläche aktivieren, besonders bei Netzwerkzugriff.
- Umgebungsvariable `FLASK_SECRET_KEY` setzen (nicht den Standard verwenden).
- Passwort-Komplexitätsanforderungen bei der Registrierung hinzufügen.

### 2. Performance-Optimierungen
- Indizes für häufig durchsuchte Felder (`collector_number`, `storage_code`)
  hinzufügen. *(Hinweis: Indizes auf `storage_code`, `name`, `scryfall_id`,
  `cardmarket_id` und `(set_code, collector_number, language, foil)` wurden
  inzwischen im Schema-Fundament ergänzt — siehe `setup_db.py`.)*
- Pagination für die Kartenliste bei großen Beständen (umgesetzt in der
  Cards-Ansicht).
- Caching für häufige Datenbankabfragen (z. B. Ordnerliste).

### 3. Benutzerfreundlichkeit
- ✅ Navigation ist bereits übersichtlich.
- Dark-Mode-Option hinzufügen.
- Bestätigungsdialoge vor dem Löschen von Karten/Ordnern.
- "Letzte Änderungen"-Ansicht für schnellen Überblick.

### 4. Backup und Wiederherstellung
- Automatische Backups der Hauptdatenbank (`mtg_lager.db`).
- Export/Import-Funktion für die komplette Datenbank (nicht nur CSV).
- Backup vor Upload einer neuen `default-cards.db` erstellen.

### 5. Mobile Optimierung
- Responsive Design für kleine Bildschirme verbessern.
- Touch-freundlichere Buttons und Formularfelder.
- Barcode-Scanner direkt in der Web-App nutzen (WebRTC).

### 6. Monitoring und Logging
- Strukturiertes Logging für Fehler und wichtige Aktionen.
- Speicherplatz-Warnung, wenn der Raspberry Pi voll wird.
- Upload-Historie für `default-cards.db` (wann, von wem).

### 7. Erweiterte Funktionen
- Bulk-Edit-Funktion für mehrere Karten gleichzeitig.
- Preishistorie und -trends tracken.
- Automatische Preisaktualisierung von Cardmarket.
- Dashboard mit Statistiken (Gesamtwert, Anzahl Karten, etc.) — inzwischen
  teilweise umgesetzt, siehe [FEATURES.md](FEATURES.md).

### 8. Deployment auf Raspberry Pi
- Systemd-Service-Datei für automatischen Start erstellen.
- Nginx als Reverse Proxy für bessere Performance.
- Gunicorn oder uWSGI statt Flask Development Server.
- Log-Rotation konfigurieren.

### 9. Tests und Qualität
- ✅ Basis-Tests sind vorhanden.
- Mehr Integration-Tests für die Weboberfläche.
- E2E-Tests mit Playwright oder Selenium.
- CI/CD-Pipeline mit GitHub Actions.

### 10. Dokumentation
- ✅ README und WEB_INTERFACE.md sind gut dokumentiert.
- Screenshots in der Dokumentation.
- Video-Tutorial für neue Benutzer.
- API-Dokumentation, falls externe Zugriffe geplant sind.

## Sofort umsetzbare Verbesserungen

### Systemd-Service (für automatischen Start)
`/etc/systemd/system/tcginventory.service`:

```ini
[Unit]
Description=TCG Inventory Web Application
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/TCGInventory
Environment="FLASK_SECRET_KEY=<generiere-einen-sicheren-key>"
Environment="FLASK_RUN_HOST=0.0.0.0"
Environment="FLASK_RUN_PORT=5000"
ExecStart=/usr/bin/python3 -m TCGInventory.web
Restart=always

[Install]
WantedBy=multi-user.target
```

Aktivieren mit:

```bash
sudo systemctl enable tcginventory
sudo systemctl start tcginventory
```

### Nginx Reverse Proxy
`/etc/nginx/sites-available/tcginventory`:

```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
