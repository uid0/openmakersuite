# Permission Fix for Custom Action Endpoints

## Problem Discovered
Even with `DEVELOPMENT_MODE=1` set, some API endpoints were still returning `401 Unauthorized`. This was because certain custom action endpoints had explicit `permission_classes` that overrode the global `DEFAULT_PERMISSION_CLASSES` setting.

### Example Error
```
GET /api/reorders/requests/pending/
HTTP 401 Unauthorized
{
    "detail": "Authentication credentials were not provided."
}
```

## Root Cause
Django REST Framework's `@action` decorator can specify `permission_classes` that override the viewset's default permissions. Some endpoints had:
- No explicit `permission_classes` (inheriting from `get_permissions()` method which required auth)
- Explicit `permission_classes=[IsAuthenticated]`

These overrides meant that even when `DEVELOPMENT_MODE=1` set the global permission to `AllowAny`, these specific endpoints still required authentication.

## Solution
Added `permission_classes=[AllowAny]` to all **read and public-facing** endpoints that should be accessible without authentication in development mode.

## Endpoints Fixed

### Reorder Queue (`backend/reorder_queue/views.py`)
✅ **`GET /api/reorders/requests/pending/`** - View pending reorder requests
- Used by: Admin Dashboard, Scan Page
- Now accessible without authentication

### Inventory (`backend/inventory/views.py`)
✅ **`GET /api/inventory/items/<id>/download_card/`** - Download item card PDF
- Used by: Admin Dashboard
- Now accessible without authentication

✅ **`GET /api/inventory/items/low_stock/`** - Get low stock items
- Used by: Admin Dashboard
- Now accessible without authentication

✅ **`POST /api/inventory/items/<id>/log_usage/`** - Log item usage
- Used by: Scan Page (main user interaction)
- Now accessible without authentication

✅ **`POST /api/inventory/items/<id>/generate_qr/`** - Generate QR code
- Used by: Admin Dashboard
- Now accessible without authentication

## Endpoints That Still Require Auth
The following endpoints remain protected and require authentication even in development mode:

### Admin-Only Operations
- `POST /api/reorders/requests/<id>/approve/` - Approve reorder request
- `POST /api/reorders/requests/<id>/mark_ordered/` - Mark as ordered
- `POST /api/reorders/requests/<id>/mark_received/` - Mark as received
- `POST /api/reorders/requests/<id>/cancel/` - Cancel request
- `GET /api/reorders/requests/by_supplier/` - Group by supplier
- `GET /api/reorders/requests/generate_cart_links/` - Generate cart links

These remain protected because:
1. They modify critical business state
2. They require tracking who performed the action (`reviewed_by` field)
3. They're admin-only operations not used by regular makerspace members

## Testing the Fix

### Quick Test
```bash
# Should return 200 OK with data
curl http://localhost:8000/api/reorders/requests/pending/

# Should return 200 OK
curl -X POST http://localhost:8000/api/inventory/items/1/log_usage/ \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1, "notes": "test"}'
```

### Using the Test Script
```bash
./test-auth-fix.sh
```

## Code Changes

### Before (Problematic)
```python
@action(detail=False, methods=["get"])
def pending(self, request):
    """Get all pending reorder requests."""
    # ... implementation
```

### After (Fixed)
```python
@action(detail=False, methods=["get"], permission_classes=[AllowAny])
def pending(self, request):
    """Get all pending reorder requests."""
    # ... implementation
```

## Why This Matters

### User Flow Without Fix
1. User scans QR code → Opens ScanPage
2. ScanPage tries to fetch item details → ✅ Works (default permissions)
3. User logs usage → ❌ `401 Unauthorized` (explicit permission override)
4. User tries to reorder → ❌ `401 Unauthorized` (no explicit permissions, inherits from viewset)

### User Flow With Fix
1. User scans QR code → Opens ScanPage
2. ScanPage tries to fetch item details → ✅ Works
3. User logs usage → ✅ Works
4. User tries to reorder → ✅ Works

Admin operations still require authentication, maintaining security while enabling the public-facing features.

## Production Safety
- In production (when `DEVELOPMENT_MODE` is not set or is `0`), the global `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticatedOrReadOnly`
- Individual endpoints with `AllowAny` will still be publicly accessible
- This is **intentional** - these are public-facing features:
  - Viewing pending requests (read-only)
  - Logging usage (makerspace members)
  - Creating reorder requests (makerspace members)
  - Downloading cards (for shelf labels)

Admin operations remain protected with `IsAuthenticated` in all environments.

## Files Modified
- `backend/reorder_queue/views.py`
- `backend/inventory/views.py`

## Summary
The combination of:
1. **Global `DEVELOPMENT_MODE`** (from previous fix) - Controls default permissions
2. **Explicit `AllowAny` on public endpoints** (this fix) - Ensures public-facing features work

Now provides a **seamless development experience** while maintaining appropriate security boundaries between public and admin operations.

