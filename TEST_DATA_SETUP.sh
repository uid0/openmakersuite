#!/bin/bash
# Create test data and test index card generation

echo "🚀 OpenMakerSuite - Test Data & Index Card Generation"
echo "====================================================="
echo

# Check if backend is running with correct settings
echo "🔍 Checking backend status..."
if ! curl -s http://localhost:8000/api/reorders/requests/pending/ > /dev/null 2>&1; then
    echo "❌ Backend not running or not accessible"
    echo "   Please restart: ./dev-commands.sh run-backend"
    exit 1
fi

# Test that we're getting array response (not 401)
PENDING_TEST=$(curl -s http://localhost:8000/api/reorders/requests/pending/)
if echo "$PENDING_TEST" | jq -e 'type == "array"' > /dev/null 2>&1; then
    echo "✅ Backend running with correct settings"
else
    echo "❌ Backend still returning authentication errors"
    echo "   Please restart: ./dev-commands.sh run-backend"
    exit 1
fi

echo
echo "📦 Creating Test Data"
echo "====================="

# Create a category
echo "📂 Creating category 'Electronics'..."
CATEGORY_RESPONSE=$(curl -s -X POST http://localhost:8000/api/inventory/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Electronics", "description": "Electronic components and devices"}')

CATEGORY_ID=$(echo "$CATEGORY_RESPONSE" | jq -r '.id // empty')
if [ -z "$CATEGORY_ID" ]; then
    echo "❌ Failed to create category: $CATEGORY_RESPONSE"
    exit 1
fi
echo "✅ Created category with ID: $CATEGORY_ID"

# Create a supplier
echo "🏪 Creating supplier 'Amazon'..."
SUPPLIER_RESPONSE=$(curl -s -X POST http://localhost:8000/api/inventory/suppliers/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Amazon", "supplier_type": "amazon", "website": "https://amazon.com", "notes": "Online retailer"}')

SUPPLIER_ID=$(echo "$SUPPLIER_RESPONSE" | jq -r '.id // empty')
if [ -z "$SUPPLIER_ID" ]; then
    echo "❌ Failed to create supplier: $SUPPLIER_RESPONSE"
    exit 1
fi
echo "✅ Created supplier with ID: $SUPPLIER_ID"

# Create a location (this will be auto-created by the API)
echo "📍 Using location 'Electronics Lab' (will be auto-created)..."

# Create an item
echo "📦 Creating test item 'Arduino Uno R3'..."
ITEM_RESPONSE=$(curl -s -X POST http://localhost:8000/api/inventory/items/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Arduino Uno R3",
    "description": "Popular microcontroller board for electronics projects",
    "sku": "ARDUINO-UNO-R3",
    "category": "'$CATEGORY_ID'",
    "location": "Electronics Lab",
    "supplier": "'$SUPPLIER_ID'",
    "reorder_quantity": 5,
    "current_stock": 10,
    "minimum_stock": 3,
    "supplier_sku": "B008GRTSV6",
    "supplier_url": "https://amazon.com/dp/B008GRTSV6",
    "unit_cost": "24.95",
    "average_lead_time": 7,
    "is_active": true,
    "notes": "Great for beginners and prototyping"
  }')

ITEM_ID=$(echo "$ITEM_RESPONSE" | jq -r '.id // empty')
if [ -z "$ITEM_ID" ]; then
    echo "❌ Failed to create item: $ITEM_RESPONSE"
    exit 1
fi
echo "✅ Created item with ID: $ITEM_ID"

echo
echo "🎯 Testing Index Card Generation"
echo "=================================="

# Test index card download
echo "📄 Testing index card download..."
CARD_RESPONSE=$(curl -s -I http://localhost:8000/api/inventory/items/$ITEM_ID/download_card/)

if echo "$CARD_RESPONSE" | grep -q "200 OK"; then
    echo "✅ Index card download endpoint accessible"
    echo "   Status: $(echo "$CARD_RESPONSE" | grep "HTTP" | head -1)"
    echo "   Content-Type: $(echo "$CARD_RESPONSE" | grep "Content-Type" | head -1)"
else
    echo "❌ Index card download failed"
    echo "   Response: $(echo "$CARD_RESPONSE" | grep "HTTP" | head -1)"
fi

# Test QR code generation
echo "📱 Testing QR code generation..."
QR_RESPONSE=$(curl -s -X POST http://localhost:8000/api/inventory/items/$ITEM_ID/generate_qr/)

if echo "$QR_RESPONSE" | jq -e '.status' > /dev/null 2>&1; then
    echo "✅ QR code generation started successfully"
    echo "   Response: $(echo "$QR_RESPONSE" | jq -r '.status')"
else
    echo "❌ QR code generation failed: $QR_RESPONSE"
fi

echo
echo "📊 Test Data Summary"
echo "===================="
echo "Category ID: $CATEGORY_ID"
echo "Supplier ID: $SUPPLIER_ID"
echo "Item ID: $ITEM_ID"
echo "Item SKU: ARDUINO-UNO-R3"

echo
echo "🧪 Manual Testing in Browser"
echo "=============================="
echo "1. Open: http://localhost:3000/admin"
echo "2. You should see the Admin Dashboard without errors"
echo "3. Test item card download:"
echo "   Visit: http://localhost:8000/api/inventory/items/$ITEM_ID/download_card/"
echo "   Should download a PDF file"
echo
echo "📱 Test QR Code Scanning:"
echo "1. Open: http://localhost:3000"
echo "2. Go to: http://localhost:3000/scan/$ITEM_ID"
echo "3. Should show item details and allow usage logging"

echo
echo "✅ Test data creation complete!"
echo "📝 Next: Restart your backend and test the index card generation in the browser"
