#!/bin/bash
# Comprehensive test of all public-facing API endpoints

echo "🧪 Testing All Public API Endpoints"
echo "===================================="
echo

# Check if backend is running
if ! curl -s http://localhost:8000/api/ > /dev/null 2>&1; then
    echo "❌ Backend not running. Please start it first:"
    echo "   cd backend && python manage.py runserver"
    exit 1
fi

echo "✅ Backend is running"
echo

# Test counter
PASSED=0
FAILED=0

test_endpoint() {
    local METHOD=$1
    local URL=$2
    local DATA=$3
    local EXPECTED_STATUS=$4
    local DESCRIPTION=$5
    
    echo -n "Testing: $DESCRIPTION... "
    
    if [ -z "$DATA" ]; then
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X "$METHOD" "$URL")
    else
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            -X "$METHOD" "$URL" \
            -H "Content-Type: application/json" \
            -d "$DATA")
    fi
    
    if [ "$STATUS" = "$EXPECTED_STATUS" ]; then
        echo "✅ (HTTP $STATUS)"
        ((PASSED++))
    else
        echo "❌ (Expected $EXPECTED_STATUS, got $STATUS)"
        ((FAILED++))
    fi
}

echo "📋 Testing Inventory Endpoints"
echo "------------------------------"
test_endpoint "GET" "http://localhost:8000/api/inventory/categories/" "" "200" "List categories"
test_endpoint "GET" "http://localhost:8000/api/inventory/suppliers/" "" "200" "List suppliers"
test_endpoint "GET" "http://localhost:8000/api/inventory/items/" "" "200" "List items"
test_endpoint "GET" "http://localhost:8000/api/inventory/items/low_stock/" "" "200" "Get low stock items"

echo
echo "📦 Testing Reorder Queue Endpoints"
echo "-----------------------------------"
test_endpoint "GET" "http://localhost:8000/api/reorders/requests/" "" "200" "List reorder requests"
test_endpoint "GET" "http://localhost:8000/api/reorders/requests/pending/" "" "200" "Get pending requests"

echo
echo "📝 Testing Write Operations (with DEVELOPMENT_MODE)"
echo "----------------------------------------------------"

# Create a test category
TIMESTAMP=$(date +%s)
test_endpoint "POST" "http://localhost:8000/api/inventory/categories/" \
    "{\"name\": \"Test Category $TIMESTAMP\", \"description\": \"Test\"}" \
    "201" "Create category"

echo
echo "📊 Test Summary"
echo "==============="
echo "✅ Passed: $PASSED"
echo "❌ Failed: $FAILED"
echo

if [ $FAILED -eq 0 ]; then
    echo "🎉 All tests passed!"
    echo
    echo "🔐 Authentication Status:"
    if grep -q "DEVELOPMENT_MODE=1" backend/.env 2>/dev/null; then
        echo "   ✅ DEVELOPMENT_MODE=1 (all endpoints accessible)"
    else
        echo "   🔒 DEVELOPMENT_MODE not enabled"
    fi
    echo
    echo "📝 Note: Admin-only endpoints (approve, mark_ordered, etc.) still require authentication"
    exit 0
else
    echo "⚠️  Some tests failed. Check your backend/.env file:"
    echo "   - Ensure DEVELOPMENT_MODE=1 is set"
    echo "   - Restart the backend after making changes"
    echo
    echo "Current backend/.env DEVELOPMENT_MODE:"
    grep "DEVELOPMENT_MODE" backend/.env 2>/dev/null || echo "   Not found"
    exit 1
fi

