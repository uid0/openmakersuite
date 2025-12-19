#!/bin/bash
#
# Script to resolve merge conflicts when merging with main
# Specifically handles the backend/coverage.xml conflict
#

set -e  # Exit on error

echo "🔍 Checking current branch..."
CURRENT_BRANCH=$(git branch --show-current)
echo "   Current branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" = "main" ]; then
    echo "❌ Error: You are on the main branch. Please switch to your feature branch first."
    exit 1
fi

echo ""
echo "📥 Fetching latest changes from origin..."
git fetch origin main

echo ""
echo "🔄 Attempting to merge origin/main into $CURRENT_BRANCH..."
# Merge might fail with conflicts, which is expected - don't exit on error
set +e
git merge origin/main --no-commit --no-ff
MERGE_STATUS=$?
set -e

if [ $MERGE_STATUS -eq 0 ]; then
    echo "✅ Merge completed successfully with no conflicts!"
    echo "   You can now commit the merge:"
    echo "   git commit -m 'Merge origin/main into $CURRENT_BRANCH'"
    exit 0
fi

echo ""
echo "⚠️  Merge conflicts detected. Resolving..."

# Check if coverage.xml is the conflict
if git status | grep -q "backend/coverage.xml"; then
    echo "   Found conflict in backend/coverage.xml (generated file, should be ignored)"
    echo "   Removing backend/coverage.xml..."
    git rm backend/coverage.xml 2>/dev/null || true
    echo "   ✅ Removed backend/coverage.xml"
fi

# Check for other conflicts
CONFLICTS=$(git diff --name-only --diff-filter=U)
if [ -z "$CONFLICTS" ]; then
    echo ""
    echo "✅ All conflicts resolved!"
    echo ""
    echo "📝 Changes to be committed:"
    git status --short
    echo ""
    echo "💡 To complete the merge, run:"
    echo "   git commit -m 'Merge origin/main into $CURRENT_BRANCH'"
else
    echo ""
    echo "⚠️  Remaining conflicts that need manual resolution:"
    echo "$CONFLICTS"
    echo ""
    echo "💡 After resolving conflicts, run:"
    echo "   git add <resolved-files>"
    echo "   git commit -m 'Merge origin/main into $CURRENT_BRANCH'"
    exit 1
fi
