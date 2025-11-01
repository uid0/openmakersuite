#!/bin/bash
set -e

# 🔐 OpenMakerSuite Release Verification Script
# This script helps verify the cryptographic integrity of releases

VERSION=""
DOWNLOAD_DIR="./downloads"
COSIGN_VERSION="v2.2.2"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}"
    echo "🔐 OpenMakerSuite Release Verification"
    echo "======================================"
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

show_usage() {
    echo "Usage: $0 <version>"
    echo ""
    echo "Examples:"
    echo "  $0 v1.2.3                    # Verify specific version"
    echo "  $0 latest                    # Verify latest release"
    echo ""
    echo "Options:"
    echo "  --help, -h                   Show this help message"
    echo "  --download-only              Only download, don't verify"
    echo "  --verify-only                Only verify (files must exist)"
}

install_cosign() {
    if command -v cosign >/dev/null 2>&1; then
        print_success "cosign is already installed"
        return
    fi
    
    print_warning "Installing cosign $COSIGN_VERSION..."
    
    # Detect architecture
    ARCH=$(uname -m)
    case $ARCH in
        x86_64) ARCH="amd64" ;;
        aarch64) ARCH="arm64" ;;
        arm64) ARCH="arm64" ;;
        *) print_error "Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    
    # Detect OS
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    
    # Download and install cosign
    COSIGN_URL="https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-$OS-$ARCH"
    
    curl -sL "$COSIGN_URL" -o cosign
    chmod +x cosign
    sudo mv cosign /usr/local/bin/
    
    print_success "cosign installed successfully"
}

download_release() {
    local version=$1
    local repo_url="https://github.com/your-org/openmakersuite"  # Update this to your actual repo
    
    print_warning "Downloading release $version..."
    
    mkdir -p "$DOWNLOAD_DIR"
    cd "$DOWNLOAD_DIR"
    
    # Get the actual version tag if "latest" was specified
    if [ "$version" = "latest" ]; then
        version=$(curl -s https://api.github.com/repos/your-org/openmakersuite/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')
        print_warning "Latest version is: $version"
    fi
    
    # Download all release files
    local base_url="$repo_url/releases/download/$version"
    local files=(
        "openmakersuite-$version.tar.gz"
        "openmakersuite-$version.tar.gz.sha256"
        "openmakersuite-$version.tar.gz.sig"
        "openmakersuite-$version.tar.gz.pem"
    )
    
    for file in "${files[@]}"; do
        if [ ! -f "$file" ]; then
            print_warning "Downloading $file..."
            if ! curl -sL "$base_url/$file" -o "$file"; then
                print_error "Failed to download $file"
                return 1
            fi
        else
            print_success "$file already exists"
        fi
    done
    
    print_success "All files downloaded successfully"
    cd ..
}

verify_checksums() {
    local version=$1
    
    print_warning "Verifying checksums..."
    
    cd "$DOWNLOAD_DIR"
    
    if [ ! -f "openmakersuite-$version.tar.gz.sha256" ]; then
        print_error "Checksum file not found"
        return 1
    fi
    
    if sha256sum -c "openmakersuite-$version.tar.gz.sha256"; then
        print_success "Checksums verified successfully"
    else
        print_error "Checksum verification failed"
        return 1
    fi
    
    cd ..
}

verify_signature() {
    local version=$1
    
    print_warning "Verifying cryptographic signature..."
    
    cd "$DOWNLOAD_DIR"
    
    local files=(
        "openmakersuite-$version.tar.gz"
        "openmakersuite-$version.tar.gz.sig"
        "openmakersuite-$version.tar.gz.pem"
    )
    
    # Check all required files exist
    for file in "${files[@]}"; do
        if [ ! -f "$file" ]; then
            print_error "Required file not found: $file"
            return 1
        fi
    done
    
    # Verify signature using cosign
    if cosign verify-blob \
        --certificate "openmakersuite-$version.tar.gz.pem" \
        --signature "openmakersuite-$version.tar.gz.sig" \
        "openmakersuite-$version.tar.gz"; then
        print_success "Cryptographic signature verified successfully"
    else
        print_error "Signature verification failed"
        return 1
    fi
    
    cd ..
}

show_release_info() {
    local version=$1
    
    cd "$DOWNLOAD_DIR"
    
    echo -e "\n${BLUE}📦 Release Information${NC}"
    echo "======================"
    echo "Version: $version"
    echo "Files:"
    
    for file in openmakersuite-$version.*; do
        if [ -f "$file" ]; then
            size=$(ls -lh "$file" | awk '{print $5}')
            echo "  📄 $file ($size)"
        fi
    done
    
    echo ""
    echo -e "${GREEN}🎉 Release verification completed successfully!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Extract: tar -xzf openmakersuite-$version.tar.gz"
    echo "  2. Configure: cp .env.example .env && nano .env"
    echo "  3. Deploy: ./deploy.sh"
    
    cd ..
}

main() {
    print_header
    
    # Parse arguments
    DOWNLOAD_ONLY=false
    VERIFY_ONLY=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --help|-h)
                show_usage
                exit 0
                ;;
            --download-only)
                DOWNLOAD_ONLY=true
                shift
                ;;
            --verify-only)
                VERIFY_ONLY=true
                shift
                ;;
            -*)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
            *)
                if [ -z "$VERSION" ]; then
                    VERSION=$1
                else
                    print_error "Multiple versions specified"
                    show_usage
                    exit 1
                fi
                shift
                ;;
        esac
    done
    
    # Validate version argument
    if [ -z "$VERSION" ]; then
        print_error "Version argument is required"
        show_usage
        exit 1
    fi
    
    # Install cosign if needed (unless download-only)
    if [ "$DOWNLOAD_ONLY" = false ]; then
        install_cosign
    fi
    
    # Download files (unless verify-only)
    if [ "$VERIFY_ONLY" = false ]; then
        if ! download_release "$VERSION"; then
            print_error "Download failed"
            exit 1
        fi
    fi
    
    # Verify files (unless download-only)
    if [ "$DOWNLOAD_ONLY" = false ]; then
        if ! verify_checksums "$VERSION"; then
            print_error "Checksum verification failed"
            exit 1
        fi
        
        if ! verify_signature "$VERSION"; then
            print_error "Signature verification failed"
            exit 1
        fi
        
        show_release_info "$VERSION"
    else
        print_success "Download completed. Use --verify-only to verify signatures."
    fi
}

# Run main function with all arguments
main "$@"
