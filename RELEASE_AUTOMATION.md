# 🚀 Release Automation Setup

This document explains how the automated release system works and how to configure it.

## 🔄 How It Works

The automated release system is triggered when:
1. ✅ All CI/CD tests pass on the `main` branch
2. ✅ Frontend tests pass with coverage requirements
3. ✅ Security checks (Bandit, Safety) pass
4. ✅ Docker build tests pass

When all checks are green, the system automatically:
1. 📋 Generates a semantic version number
2. 🏗️ Builds production Docker images
3. 📦 Creates a release bundle with deployment scripts
4. 🔐 Cryptographically signs the release using Sigstore
5. 🚀 Publishes to GitHub Releases
6. 📊 Associates the release with Sentry for monitoring

## ⚙️ Required GitHub Secrets

Configure these secrets in your GitHub repository settings (`Settings` → `Secrets and variables` → `Actions`):

### Sentry Integration
```bash
SENTRY_AUTH_TOKEN=your_sentry_auth_token_here
SENTRY_ORG=your_sentry_organization_slug  
SENTRY_PROJECT=your_sentry_project_slug
```

### Optional: Slack Notifications
```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK
```

## 🔐 Cryptographic Signing

The release system uses **Sigstore** for keyless cryptographic signing:

- ✅ **No private keys to manage** - uses OIDC identity
- ✅ **Transparent signing** - signatures are publicly verifiable
- ✅ **Tamper-proof** - ensures release integrity
- ✅ **Industry standard** - used by major projects like Kubernetes

### Verifying Signatures

To verify a release signature:

```bash
# Install cosign
curl -O -L "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64"
sudo mv cosign-linux-amd64 /usr/local/bin/cosign
sudo chmod +x /usr/local/bin/cosign

# Verify the release bundle
cosign verify-blob \
  --certificate openmakersuite-v1.2.3.tar.gz.pem \
  --signature openmakersuite-v1.2.3.tar.gz.sig \
  openmakersuite-v1.2.3.tar.gz

# Verify checksums
sha256sum -c openmakersuite-v1.2.3.tar.gz.sha256
```

## 📋 Version Management

### Automatic Versioning

The system automatically detects version bumps based on commit messages:

- **Major** (1.0.0 → 2.0.0): Commits containing "breaking" or "major"
- **Minor** (1.0.0 → 1.1.0): Commits containing "feat", "feature", or "minor"  
- **Patch** (1.0.0 → 1.0.1): All other commits

### Manual Releases

You can manually trigger releases with specific version types:

1. Go to `Actions` → `🚀 Automated Release & Deploy`
2. Click `Run workflow`
3. Select branch and release type (patch/minor/major)

## 📦 Release Artifacts

Each release includes:

### 🐳 Docker Images
```bash
# Multi-architecture support (AMD64, ARM64)
ghcr.io/your-org/openmakersuite/backend:v1.2.3
ghcr.io/your-org/openmakersuite/frontend:v1.2.3
```

### 📁 Release Bundle
- `openmakersuite-v1.2.3.tar.gz` - Complete deployment package
- `deploy.sh` - One-command deployment script
- `docker-compose.yml` - Container orchestration
- `.env.example` - Configuration template

### 🔐 Security Files
- `openmakersuite-v1.2.3.tar.gz.sig` - Sigstore signature
- `openmakersuite-v1.2.3.tar.gz.pem` - Public certificate
- `openmakersuite-v1.2.3.tar.gz.sha256` - Checksums

## 🚀 Deploying Releases

### Quick Deployment

```bash
# Download and extract the latest release
wget https://github.com/your-org/openmakersuite/releases/latest/download/openmakersuite-v1.2.3.tar.gz
tar -xzf openmakersuite-v1.2.3.tar.gz

# Configure environment
cp .env.example .env
nano .env  # Edit your configuration

# Deploy
./deploy.sh
```

### Production Deployment

```bash
# Verify the release signature first (see verification steps above)
cosign verify-blob --certificate *.pem --signature *.sig *.tar.gz

# Then deploy
./deploy.sh
```

## 📊 Sentry Integration Features

### Release Tracking
- 🔄 **Automatic Release Creation**: Every GitHub release creates a Sentry release
- 📈 **Commit Association**: Links commits to releases for better debugging
- 🐛 **Error Tracking**: Associates errors with specific release versions
- 📱 **Performance Monitoring**: Tracks performance across releases

### Setting Up Sentry

1. **Create a Sentry Account**: https://sentry.io/signup/
2. **Create a Project**: Choose "Django" for backend monitoring
3. **Get Auth Token**: 
   - Go to Settings → Account → API → Auth Tokens
   - Create token with `project:releases` scope
4. **Configure Secrets**: Add the three Sentry secrets to GitHub

## 🔧 Troubleshooting

### Release Not Triggering
- ✅ Check that CI workflow completed successfully
- ✅ Verify you're on the `main` branch
- ✅ Ensure there are new commits since last release

### Sentry Integration Failing
- ✅ Verify `SENTRY_AUTH_TOKEN` has correct permissions
- ✅ Check `SENTRY_ORG` and `SENTRY_PROJECT` are correct
- ✅ Ensure Sentry project exists and is accessible

### Docker Image Push Failing
- ✅ Check GitHub Container Registry permissions
- ✅ Verify repository has `packages: write` permission
- ✅ Ensure workflow has proper `GITHUB_TOKEN` access

### Signature Verification Failing
- ✅ Download all signature files (.sig, .pem, .sha256)
- ✅ Use the correct cosign version (v2.2.2+)
- ✅ Ensure files weren't corrupted during download

## 🔄 Workflow Dependencies

The release workflow depends on these other workflows completing successfully:

1. **CI** (`.github/workflows/ci.yml`):
   - Backend tests with coverage
   - Frontend tests with coverage  
   - Security checks (Bandit, Safety)
   - Linting (Black, isort, flake8)

2. **Docker Build** (part of CI):
   - Multi-platform container builds
   - Container registry pushes

## 🎯 Best Practices

### For Developers
1. **Write meaningful commit messages** - they're used for version detection
2. **Ensure all tests pass** before merging to main
3. **Review release notes** - they're auto-generated from commits
4. **Test releases in staging** before production deployment

### For DevOps
1. **Monitor Sentry** for release-related issues
2. **Verify signatures** for production deployments
3. **Keep secrets secure** and rotate periodically
4. **Test the deployment process** regularly

### For Security
1. **Always verify signatures** for production deployments
2. **Use the provided checksums** to verify file integrity
3. **Keep cosign updated** for signature verification
4. **Monitor Sentry** for security-related errors

## 📞 Support

If you encounter issues:

1. **Check the Actions logs** in GitHub for detailed error messages
2. **Verify all secrets** are correctly configured
3. **Test manual release triggers** to isolate issues
4. **Review this documentation** for configuration steps

---

**🔒 Security Note**: This release system uses industry-standard security practices including cryptographic signing, checksum verification, and automated security scanning. Always verify signatures in production environments.
