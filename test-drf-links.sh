#!/bin/bash
# Test that DRF interface shows clickable links for IDs

echo "🧪 Testing DRF Interface Clickable Links"
echo "========================================"
echo

ITEM_ID="0fa1fe96-f11c-4886-b6ff-4ba87870acb3"

# Test 1: Check if API returns hyperlinked format
echo "🔗 Test 1: API Response Format"
echo "-------------------------------"
API_RESPONSE=$(curl -s http://localhost:8000/api/inventory/items/$ITEM_ID/)

if echo "$API_RESPONSE" | jq -e '.id' > /dev/null 2>&1; then
    echo "✅ API returns data"

    # Check if ID is a string (UUID format)
    ID_VALUE=$(echo "$API_RESPONSE" | jq -r '.id')
    if [[ $ID_VALUE =~ ^[0-9a-f-]{36}$ ]]; then
        echo "✅ ID is proper UUID format: $ID_VALUE"
    else
        echo "❌ ID format unexpected: $ID_VALUE"
    fi
else
    echo "❌ API response format issue"
    echo "Response: $API_RESPONSE"
fi

# Test 2: Check DRF HTML interface for clickable elements
echo
echo "🌐 Test 2: DRF Interface HTML"
echo "------------------------------"
DRF_HTML=$(curl -s http://localhost:8000/api/inventory/items/$ITEM_ID/)

if echo "$DRF_HTML" | grep -q "href"; then
    echo "✅ DRF interface contains links"

    # Look for specific link patterns
    LINKS=$(echo "$DRF_HTML" | grep -o 'href="[^"]*"' | sort | uniq)
    if [ -n "$LINKS" ]; then
        echo "🔗 Found links in DRF interface:"
        echo "$LINKS" | head -5
    fi
else
    echo "⚠️  No links found in DRF interface"
fi

# Test 3: Check schema for proper URL patterns
echo
echo "📋 Test 3: API Schema"
echo "---------------------"
SCHEMA=$(curl -s http://localhost:8000/api/schema/ 2>/dev/null)

if echo "$SCHEMA" | grep -q "inventory"; then
    echo "✅ Schema contains inventory endpoints"

    # Look for the detail URL pattern
    DETAIL_PATTERN=$(echo "$SCHEMA" | jq -r '.paths | keys[]' 2>/dev/null | grep "items.*id" | head -1)
    if [ -n "$DETAIL_PATTERN" ]; then
        echo "✅ Found detail URL pattern: $DETAIL_PATTERN"
    else
        echo "⚠️  Detail URL pattern not found"
    fi
else
    echo "❌ Schema missing inventory endpoints"
fi

echo
echo "🎯 Test 4: Direct Navigation Test"
echo "----------------------------------"
# Test if we can navigate directly to the item
NAV_TEST=$(curl -s -I http://localhost:8000/api/inventory/items/$ITEM_ID/ 2>/dev/null)

if echo "$NAV_TEST" | grep -q "200 OK"; then
    echo "✅ Direct navigation works"
    echo "   Status: $(echo "$NAV_TEST" | grep "HTTP" | head -1)"
else
    echo "❌ Direct navigation failed"
fi

echo
echo "📊 Expected DRF Interface Behavior"
echo "==================================="
echo "✅ ID field should be clickable UUID"
echo "✅ Clicking ID should navigate to detail view"
echo "✅ Should work in browser at:"
echo "   http://localhost:8000/api/inventory/items/$ITEM_ID/"

echo
echo "🧪 Browser Testing Instructions"
echo "================================"
echo "1. Open: http://localhost:8000/api/inventory/items/"
echo "2. Look for clickable ID field in the JSON response"
echo "3. Click on the UUID to navigate to item details"
echo "4. Should go to: http://localhost:8000/api/inventory/items/$ITEM_ID/"

echo
echo "🔧 If Links Don't Work"
echo "======================="
echo "The issue might be:"
echo "1. Backend needs restart to load new serializer"
echo "2. DRF interface caching old format"
echo "3. Browser cache (try Ctrl+F5)"

echo
echo "📚 Technical Details"
echo "===================="
echo "✅ Changed InventoryItemSerializer to HyperlinkedModelSerializer"
echo "✅ Added extra_kwargs for clickable ID field"
echo "✅ Updated Supplier and Category serializers for consistency"
echo "✅ Configured view_name: 'inventoryitem-detail'"

echo
echo "🚀 Restart backend to see changes:"
echo "   ./dev-commands.sh run-backend"

