#!/bin/bash
set -e

# 🚀 OpenMakerSuite Release Automation Setup Script
# This script helps configure the automated release system

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}"
    echo "🚀 OpenMakerSuite Release Automation Setup"
    echo "=========================================="
    echo -e "${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

check_prerequisites() {
    print_info "Checking prerequisites..."
    
    # Check if we're in a git repository
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        print_error "Not in a git repository"
        exit 1
    fi
    
    # Check if GitHub CLI is installed
    if ! command -v gh >/dev/null 2>&1; then
        print_warning "GitHub CLI (gh) is not installed"
        print_info "Install it from: https://cli.github.com/"
        print_info "Or run: curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg"
        exit 1
    fi
    
    # Check if logged in to GitHub
    if ! gh auth status >/dev/null 2>&1; then
        print_warning "Not logged in to GitHub CLI"
        print_info "Run: gh auth login"
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

check_workflow_files() {
    print_info "Checking workflow files..."
    
    local missing_files=()
    
    # Check for required workflow files
    if [ ! -f ".github/workflows/ci.yml" ]; then
        missing_files+=(".github/workflows/ci.yml")
    fi
    
    if [ ! -f ".github/workflows/release.yml" ]; then
        missing_files+=(".github/workflows/release.yml")
    fi
    
    if [ ${#missing_files[@]} -gt 0 ]; then
        print_error "Missing workflow files:"
        for file in "${missing_files[@]}"; do
            echo "  - $file"
        done
        print_info "Please ensure all workflow files are committed to your repository"
        exit 1
    fi
    
    print_success "Workflow files found"
}

setup_sentry_secrets() {
    print_info "Setting up Sentry integration..."
    
    echo "To integrate with Sentry for release tracking, you need:"
    echo "1. A Sentry account (https://sentry.io/signup/)"
    echo "2. A Sentry project for your application"
    echo "3. An authentication token with project:releases scope"
    echo ""
    
    read -p "Do you want to configure Sentry secrets now? (y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Get Sentry organization
        read -p "Enter your Sentry organization slug: " SENTRY_ORG
        if [ -z "$SENTRY_ORG" ]; then
            print_warning "Sentry organization not provided, skipping Sentry setup"
            return
        fi
        
        # Get Sentry project
        read -p "Enter your Sentry project slug: " SENTRY_PROJECT
        if [ -z "$SENTRY_PROJECT" ]; then
            print_warning "Sentry project not provided, skipping Sentry setup"
            return
        fi
        
        # Get Sentry auth token
        echo "Create an auth token at: https://sentry.io/settings/account/api/auth-tokens/"
        echo "Ensure it has 'project:releases' scope"
        read -s -p "Enter your Sentry auth token: " SENTRY_AUTH_TOKEN
        echo
        
        if [ -z "$SENTRY_AUTH_TOKEN" ]; then
            print_warning "Sentry auth token not provided, skipping Sentry setup"
            return
        fi
        
        # Set GitHub secrets
        print_info "Setting GitHub secrets..."
        
        if gh secret set SENTRY_ORG --body "$SENTRY_ORG"; then
            print_success "SENTRY_ORG secret set"
        else
            print_error "Failed to set SENTRY_ORG secret"
        fi
        
        if gh secret set SENTRY_PROJECT --body "$SENTRY_PROJECT"; then
            print_success "SENTRY_PROJECT secret set"
        else
            print_error "Failed to set SENTRY_PROJECT secret"
        fi
        
        if gh secret set SENTRY_AUTH_TOKEN --body "$SENTRY_AUTH_TOKEN"; then
            print_success "SENTRY_AUTH_TOKEN secret set"
        else
            print_error "Failed to set SENTRY_AUTH_TOKEN secret"
        fi
        
        print_success "Sentry integration configured!"
    else
        print_warning "Skipping Sentry setup. You can configure it later by running this script again."
    fi
}

setup_slack_notifications() {
    print_info "Setting up Slack notifications (optional)..."
    
    read -p "Do you want to configure Slack notifications? (y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "To set up Slack notifications:"
        echo "1. Create a Slack app: https://api.slack.com/apps"
        echo "2. Add Incoming Webhooks feature"
        echo "3. Create a webhook for your desired channel"
        echo ""
        
        read -p "Enter your Slack webhook URL: " SLACK_WEBHOOK_URL
        
        if [ -z "$SLACK_WEBHOOK_URL" ]; then
            print_warning "Slack webhook URL not provided, skipping Slack setup"
            return
        fi
        
        if gh secret set SLACK_WEBHOOK_URL --body "$SLACK_WEBHOOK_URL"; then
            print_success "SLACK_WEBHOOK_URL secret set"
            print_success "Slack notifications configured!"
        else
            print_error "Failed to set SLACK_WEBHOOK_URL secret"
        fi
    else
        print_warning "Skipping Slack setup"
    fi
}

setup_container_registry() {
    print_info "Checking container registry permissions..."
    
    # GitHub Container Registry is automatically available with GITHUB_TOKEN
    # Just verify the workflow has the right permissions
    
    local workflow_file=".github/workflows/release.yml"
    
    if grep -q "packages: write" "$workflow_file"; then
        print_success "Container registry permissions configured"
    else
        print_warning "Ensure your workflow has 'packages: write' permission"
        print_info "Check the 'permissions:' section in $workflow_file"
    fi
}

test_release_trigger() {
    print_info "Testing release automation..."
    
    echo "The release automation will trigger when:"
    echo "1. ✅ All CI tests pass on main branch"
    echo "2. ✅ There are new commits since the last release"
    echo ""
    
    read -p "Do you want to test with a manual release now? (y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_info "Triggering manual release workflow..."
        
        if gh workflow run release.yml --field release_type=patch; then
            print_success "Release workflow triggered!"
            print_info "Check the Actions tab in your repository to monitor progress"
            print_info "URL: https://github.com/$(gh repo view --json owner,name --jq '.owner.login + "/" + .name')/actions"
        else
            print_error "Failed to trigger release workflow"
            print_info "Make sure all CI checks are passing first"
        fi
    else
        print_info "Skipping manual test. Release will trigger automatically when CI passes."
    fi
}

show_summary() {
    echo ""
    echo -e "${GREEN}🎉 Setup Complete!${NC}"
    echo "==================="
    echo ""
    echo "Your release automation is now configured with:"
    echo "• ✅ Automatic semantic versioning"
    echo "• ✅ Cryptographic signing with Sigstore"
    echo "• ✅ Multi-architecture Docker images"
    echo "• ✅ GitHub Releases with deployment bundles"
    
    if gh secret list | grep -q "SENTRY_"; then
        echo "• ✅ Sentry release tracking"
    fi
    
    if gh secret list | grep -q "SLACK_"; then
        echo "• ✅ Slack notifications"
    fi
    
    echo ""
    echo "Next steps:"
    echo "1. 🧪 Make some commits and push to main branch"
    echo "2. 🔍 Ensure all CI checks pass"
    echo "3. 🚀 Watch your first automated release get created!"
    echo ""
    echo "Documentation:"
    echo "• 📖 Full setup guide: RELEASE_AUTOMATION.md"
    echo "• 🔐 Verification script: scripts/verify-release.sh"
    echo ""
    echo -e "${BLUE}Happy releasing! 🚀${NC}"
}

main() {
    print_header
    
    # Run setup steps
    check_prerequisites
    check_workflow_files
    setup_container_registry
    setup_sentry_secrets
    setup_slack_notifications
    test_release_trigger
    show_summary
}

# Run main function
main "$@"
