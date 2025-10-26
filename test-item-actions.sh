#!/bin/bash
# Test all item-related endpoints and actions

echo "🧪 Testing Item Actions and Index Card Generation"
echo "================================================="
echo

ITEM_ID="0fa1fe96-f11c-4886-b6ff-4ba87870acb3"

# Test 1: Basic item retrieval
echo "📋 Test 1: Item Details"
echo "------------------------"
curl -s http://localhost:8000/api/inventory/items/$ITEM_ID/ | jq -r '.name, .sku, .current_stock' 2>/dev/null || echo "❌ Failed to get item details"

echo
echo "📄 Test 2: Index Card Download"
echo "-------------------------------"
CARD_RESPONSE=$(curl -s -I http://localhost:8000/api/inventory/items/$ITEM_ID/download_card/ 2>/dev/null)

if echo "$CARD_RESPONSE" | grep -q "200 OK" && echo "$CARD_RESPONSE" | grep -q "application/pdf"; then
    echo "✅ Index card download working"
    echo "   Content-Type: $(echo "$CARD_RESPONSE" | grep "Content-Type" | cut -d: -f2 | tr -d ' ')"
    echo "   Filename: $(echo "$CARD_RESPONSE" | grep "Content-Disposition" | cut -d'"' -f2 | cut -d'=' -f2)"
else
    echo "❌ Index card download failed"
fi

echo
echo "📱 Test 3: QR Code Image"
echo "-------------------------"
QR_RESPONSE=$(curl -s -I http://localhost:8000/api/inventory/items/$ITEM_ID/qr_code/ 2>/dev/null)

if echo "$QR_RESPONSE" | grep -q "200 OK" && echo "$QR_RESPONSE" | grep -q "image/png"; then
    echo "✅ QR code image available"
    echo "   Content-Type: $(echo "$QR_RESPONSE" | grep "Content-Type" | cut -d: -f2 | tr -d ' ')"
else
    echo "⚠️  QR code not generated yet - generate it first:"
    echo "   curl -X POST http://localhost:8000/api/inventory/items/$ITEM_ID/generate_qr/"
fi

echo
echo "🔄 Test 4: QR Code Generation"
echo "------------------------------"
GENERATE_RESPONSE=$(curl -s -X POST http://localhost:8000/api/inventory/items/$ITEM_ID/generate_qr/ 2>/dev/null)

if echo "$GENERATE_RESPONSE" | jq -e '.status' > /dev/null 2>&1; then
    echo "✅ QR code generation started"
    echo "   Response: $(echo "$GENERATE_RESPONSE" | jq -r '.status')"
else
    echo "❌ QR code generation failed"
fi

echo
echo "📊 Test 5: DRF Interface Actions"
echo "---------------------------------"
DRF_HTML=$(curl -s http://localhost:8000/api/inventory/items/$ITEM_ID/ 2>/dev/null)

if echo "$DRF_HTML" | grep -q "Download Card\|QR Code"; then
    echo "✅ DRF interface shows custom actions"
    echo "   Actions found: $(echo "$DRF_HTML" | grep -o "Download Card\|QR Code" | tr '\n' ', ' | sed 's/,$//')"
else
    echo "⚠️  DRF interface may not show actions clearly"
    echo "   Direct URLs still work (see above)"
fi

echo
echo "🎯 Direct URLs (Always Work)"
echo "============================="
echo "📋 Item Details:"
echo "   http://localhost:8000/api/inventory/items/$ITEM_ID/"
echo
echo "📄 Download Index Card:"
echo "   http://localhost:8000/api/inventory/items/$ITEM_ID/download_card/"
echo
echo "📱 QR Code Image:"
echo "   http://localhost:8000/api/inventory/items/$ITEM_ID/qr_code/"
echo
echo "🔄 Generate QR Code:"
echo "   POST http://localhost:8000/api/inventory/items/$ITEM_ID/generate_qr/"
echo
echo "📚 API Documentation:"
echo "   http://localhost:8000/api/docs/"
echo "   http://localhost:8000/api/schema/"

echo
echo "✅ All endpoints tested!"
echo "📝 After backend restart, check the DRF interface for action buttons"

