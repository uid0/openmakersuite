# 🎯 Index Card Generation Testing Guide

## Current Status

✅ **Authentication & CORS fixed** - Frontend can communicate with backend
✅ **Pagination fixed** - Admin dashboard loads without errors
❌ **Backend needs restart** - To pick up the fixes

## Step-by-Step Testing Process

### 1. 🔄 Restart Backend (Required)

**In your DevContainer terminal:**

```bash
# Find the terminal running the backend
# Press Ctrl+C to stop it

# Restart with updated settings
./dev-commands.sh run-backend
```

**Verify it worked:**
```bash
curl http://localhost:8000/api/reorders/requests/pending/
# Should return: [] (not {"detail": "Authentication..."})
```

### 2. 📦 Create Test Data

**Option A: Use the automated script**
```bash
./TEST_DATA_SETUP.sh
```

**Option B: Create manually (in DevContainer)**
```bash
# Create category
curl -X POST http://localhost:8000/api/inventory/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Electronics", "description": "Electronic components"}'

# Create supplier
curl -X POST http://localhost:8000/api/inventory/suppliers/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Amazon", "supplier_type": "amazon", "website": "https://amazon.com"}'

# Create item
curl -X POST http://localhost:8000/api/inventory/items/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Arduino Uno R3",
    "description": "Microcontroller board",
    "sku": "ARDUINO-UNO",
    "category": 1,
    "location": "Electronics Lab",
    "supplier": 1,
    "reorder_quantity": 5,
    "current_stock": 10,
    "minimum_stock": 3,
    "supplier_sku": "B008GRTSV6",
    "unit_cost": "24.95"
  }'
```

### 3. 🎨 Test Index Card Generation

#### Option A: Direct API Test
```bash
# Get the item ID from the creation response
ITEM_ID="your-item-id-here"

# Test PDF generation
curl -I http://localhost:8000/api/inventory/items/$ITEM_ID/download_card/
# Should return: HTTP 200 with Content-Type: application/pdf

# Actually download the PDF
curl -o arduino-card.pdf http://localhost:8000/api/inventory/items/$ITEM_ID/download_card/
```

#### Option B: Browser Testing
1. **Open:** http://localhost:3000/admin
2. **Navigate to:** Items list (you may need to create items through the API first)
3. **Click:** Download card for any item
4. **Expected:** PDF downloads automatically

### 4. 📱 Test QR Code Generation

```bash
# Generate QR code for the item
curl -X POST http://localhost:8000/api/inventory/items/$ITEM_ID/generate_qr/
# Should return: {"status": "QR code generation started"}
```

### 5. 🔍 Test QR Code Scanning

1. **Open:** http://localhost:3000/scan/YOUR_ITEM_ID
2. **Expected:** Shows item details, allows usage logging
3. **Test usage logging:**
   ```bash
   curl -X POST http://localhost:8000/api/inventory/items/$ITEM_ID/log_usage/ \
     -H "Content-Type: application/json" \
     -d '{"quantity": 1, "notes": "Testing usage logging"}'
   ```

## What the Index Card Should Contain

The generated PDF should include:
- ✅ **Item image/thumbnail** (if available)
- ✅ **Item name and description**
- ✅ **SKU and location**
- ✅ **Reorder quantity and current stock**
- ✅ **QR code** for easy scanning
- ✅ **Supplier information** (if available)
- ✅ **Professional formatting** (3x5" card size)

## API Endpoints to Test

### Inventory Management
```bash
# List all items
curl http://localhost:8000/api/inventory/items/

# Get item details
curl http://localhost:8000/api/inventory/items/YOUR_ITEM_ID/

# Get low stock items
curl http://localhost:8000/api/inventory/items/low_stock/

# Download card
curl -o card.pdf http://localhost:8000/api/inventory/items/YOUR_ITEM_ID/download_card/

# Generate QR code
curl -X POST http://localhost:8000/api/inventory/items/YOUR_ITEM_ID/generate_qr/

# Log usage
curl -X POST http://localhost:8000/api/inventory/items/YOUR_ITEM_ID/log_usage/ \
  -H "Content-Type: application/json" \
  -d '{"quantity": 1, "notes": "Test usage"}'
```

### Reorder Management
```bash
# Get pending requests (should return [])
curl http://localhost:8000/api/reorders/requests/pending/

# Create a reorder request (test the scan page workflow)
curl -X POST http://localhost:8000/api/reorders/requests/ \
  -H "Content-Type: application/json" \
  -d '{
    "item": "YOUR_ITEM_ID",
    "quantity": 5,
    "requested_by": "Test User",
    "priority": "normal"
  }'
```

## Expected Results

### ✅ Authentication
- No 401 errors in browser console
- API requests work without authentication

### ✅ Admin Dashboard
- Loads without "requests.filter is not a function" error
- Shows empty state initially
- Filter buttons work correctly

### ✅ Index Cards
- PDF downloads successfully
- Contains all expected information
- Properly formatted for printing

### ✅ QR Codes
- Generate without errors
- Can be scanned to access item details
- Usage logging works

## Troubleshooting

**If you still get 401 errors:**
```bash
# Check if DEVELOPMENT_MODE is active
curl -s http://localhost:8000/api/reorders/requests/pending/ | jq .

# Should return: [] (not {"detail": "Authentication..."})
# If still getting auth errors, restart backend again
```

**If items don't appear in admin dashboard:**
- Check if items were created successfully
- Verify API returns data: `curl http://localhost:8000/api/inventory/items/`

**If index card download fails:**
- Check item exists: `curl http://localhost:8000/api/inventory/items/YOUR_ITEM_ID/`
- Check permissions are working: Should return 200, not 401

## Complete Testing Workflow

1. ✅ **Backend restarted** with fixes
2. ✅ **Test data created** (category, supplier, item)
3. ✅ **Admin dashboard loads** without errors
4. ✅ **Index card generates** and downloads
5. ✅ **QR code generates** successfully
6. ✅ **Scan page works** for the item
7. ✅ **Usage logging works** via scan page

## Files Created

🧪 `TEST_DATA_SETUP.sh` - Automated test data creation
📚 `INDEX_CARD_TESTING.md` - This comprehensive testing guide

Ready to test once you restart the backend! 🎉

