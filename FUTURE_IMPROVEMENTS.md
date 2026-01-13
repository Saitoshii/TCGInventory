# Future Improvements for Offene Bestellungen

This document lists potential enhancements that could be implemented in future iterations.

## Security & Permissions

### Order Deletion Permissions
**Current:** Any authenticated user can delete any order
**Suggested:** Add permission checks to ensure users can only delete their own orders or require admin role

**Implementation:**
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

### Screen Reader Support for Delete Confirmation
**Current:** Uses basic JavaScript `confirm()` dialog
**Suggested:** Implement accessible modal dialog with proper ARIA attributes

**Implementation:**
- Use Bootstrap modal instead of confirm()
- Add ARIA labels and descriptions
- Keyboard navigation support
- Focus management

**Example:**
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

### Configurable Default-Cards.db Path
**Current:** Path is hardcoded relative to module location
**Suggested:** Make path configurable and check multiple locations

**Implementation:**
```python
# In __init__.py
DEFAULT_CARDS_DB_PATHS = [
    Path(__file__).resolve().parent / "data" / "default-cards.db",  # Module location
    Path.home() / ".tcginventory" / "default-cards.db",  # User home
    Path("/usr/local/share/tcginventory/default-cards.db"),  # System-wide
]

# In order_service.py
def _get_default_cards_db_path():
    """Find the default-cards.db in standard locations."""
    for path in DEFAULT_CARDS_DB_PATHS:
        if path.exists():
            return path
    return None
```

### Error Handling for Missing default-cards.db
**Current:** Silent failure if database doesn't exist
**Suggested:** Log warnings or provide user feedback

**Implementation:**
```python
def _get_image_from_default_db(self, card_name):
    default_db_path = _get_default_cards_db_path()
    
    if not default_db_path:
        # Log warning only once
        if not hasattr(self, '_db_warning_shown'):
            print("Warning: default-cards.db not found. Card images may be missing.")
            self._db_warning_shown = True
        return None
    
    # Continue with normal lookup
```

## User Experience

### Batch Order Operations
**Suggested:** Allow selecting multiple orders and performing batch actions
- Mark multiple orders as sold at once
- Delete multiple orders at once
- Export selected orders

### Order Filtering and Search
**Suggested:** Add filters for orders
- Filter by buyer name
- Filter by date range
- Search by card name
- Filter by status

### Undo Delete
**Suggested:** Soft delete with ability to restore recently deleted orders
- Add `deleted` flag instead of hard delete
- Show deleted orders in a separate "Trash" view
- Allow restore within X days

### Order Notes
**Suggested:** Allow adding notes to orders
- Tracking information
- Special instructions
- Communication history

## Testing

### Integration Tests
**Suggested:** Add browser-based integration tests
- Test full order deletion flow
- Test order synchronization
- Test card matching with real data

### Performance Tests
**Suggested:** Test with large datasets
- Test with 1000+ orders
- Test with 10000+ cards in inventory
- Optimize queries if needed

## Monitoring

### Order Statistics
**Suggested:** Add dashboard with statistics
- Total orders this month
- Average order value
- Most popular cards
- Buyer statistics

### Sync Monitoring
**Suggested:** Track email sync success/failure
- Last successful sync timestamp
- Number of emails processed
- Error logging and alerts

## Implementation Priority

**High Priority:**
1. Order deletion permissions (security)
2. Accessibility improvements (compliance)

**Medium Priority:**
3. Configurable database path (robustness)
4. Error handling improvements (UX)
5. Order filtering (UX)

**Low Priority:**
6. Batch operations (convenience)
7. Undo delete (safety)
8. Order notes (feature)
9. Statistics dashboard (analytics)

## Notes

These improvements are not required for the current implementation to function correctly, but would enhance security, accessibility, and user experience in future versions.
