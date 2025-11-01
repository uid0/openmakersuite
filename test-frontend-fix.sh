#!/bin/bash
# Test that the frontend pagination fix works

echo "🧪 Testing Frontend Pagination Fix"
echo "==================================="
echo

# Check if backend is running
if ! curl -s http://localhost:8000/api/ > /dev/null 2>&1; then
    echo "❌ Backend not running. Please restart it first:"
    echo "   ./dev-commands.sh run-backend"
    exit 1
fi

echo "✅ Backend is running"
echo

# Test the fixed pending endpoint
echo "📋 Testing pending requests endpoint..."
PENDING_RESPONSE=$(curl -s http://localhost:8000/api/reorders/requests/pending/)

echo "Raw response:"
echo "$PENDING_RESPONSE"
echo

# Check if it's an array (not paginated)
if echo "$PENDING_RESPONSE" | jq -e '. | type == "array"' > /dev/null 2>&1; then
    echo "✅ Pending endpoint returns array (not paginated)"
    echo "   Length: $(echo "$PENDING_RESPONSE" | jq 'length')"
else
    echo "❌ Pending endpoint still returns paginated response"
    echo "   Expected: []"
    echo "   Got: $(echo "$PENDING_RESPONSE" | jq -r 'keys | join(", ")')"
fi

echo
echo "📋 Testing main requests endpoint (should be paginated)..."
MAIN_RESPONSE=$(curl -s http://localhost:8000/api/reorders/requests/)

if echo "$MAIN_RESPONSE" | jq -e '.results | type == "array"' > /dev/null 2>&1; then
    echo "✅ Main requests endpoint correctly returns paginated response"
    echo "   Results length: $(echo "$MAIN_RESPONSE" | jq '.results | length')"
else
    echo "❌ Main requests endpoint format incorrect"
fi

echo
echo "🎯 Frontend Compatibility Test"
echo "-----------------------------"

# Test what the frontend will actually receive
echo "Frontend getPendingRequests() will receive:"
echo "$PENDING_RESPONSE" | jq .
echo

echo "Frontend listRequests() will receive:"
echo "$MAIN_RESPONSE" | jq -r '.results'

echo
echo "📊 Summary"
echo "----------"
if echo "$PENDING_RESPONSE" | jq -e '. | type == "array"' > /dev/null 2>&1; then
    echo "✅ Frontend should work now!"
    echo "   - Pending endpoint returns array (no .filter error)"
    echo "   - Main endpoint returns paginated (handled correctly)"
else
    echo "❌ Still needs work"
fi

echo
echo "🚀 Next: Restart your backend and test in browser!"
echo "   The 'requests.filter is not a function' error should be gone."

