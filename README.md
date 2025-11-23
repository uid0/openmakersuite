# OpenMakerSuite

Open Maker Suite (OMS) solves a problem that I encountered with my time at Dallas Makerspace. It's an inventory management/asset management system that's designed for Makerspaces and collaborative workshops. Use it to track supplies, manage reorders, manage maintenance on equipment, and keep your space running.

## Feature Set

Open Maker Suite isn't just one component or one application. It's a few things:

1. A inventory system that's based on Kanban principles. For each item that exists, a 3x5" index card is created that contains the name of the item, where it should live, and the general amount that we like to keep on hand and how much is re-ordered. When any user of the space (no login to OMS needed) scans a QR code, your team will be prompted that the material needs to be re-ordered. When you click on that request on the admin dashboard, you'll see a list of suppliers that you've used in the past, what their per-unit cost is, as well as their lead time for obtaining the part (all based on historical orders). This gives you the flexibiltiy to spend your Makerspace's money responsibily and give you the most bang for your buck.
1. An asset management system that allows you to specify replaceable parts from inventory items (like 9-volt batteries to an exit alarm) and logs when those items were replaced.
1. A device management system that allows you to enable or disable a device via MQTT, along with disabling a device for safety or maintenance reasons.
1. A QR code reporting system that allows members to report issues with restrooms, lobbies, or equipment.

### Screenshots

<img width="3012" height="1546" alt="2025-11-23_16-41" src="https://github.com/user-attachments/assets/62636aa0-24e5-4480-aac4-666c06b8a424" />



## Features

- **📦 Inventory Management**: Track items, quantities, locations, and suppliers
- **🔄 Automated Reordering**: Smart reorder suggestions based on usage patterns
- **📱 QR Code Integration**: Generate and scan QR codes for quick item access
- **📊 Usage Analytics**: Monitor consumption patterns and optimize stock
- **🏷️ Index Card Generation**: Print physical inventory cards
- **👥 Multi-User Support**: Role-based access control
- **📈 Supplier Management**: Track lead times, costs, and performance
- **🔔 Notifications**: Get alerts for low stock and deliveries
- **📋 Dashboard**: Real-time overview of inventory status

## 🛠️ Technology Stack

### Backend

- **Django**: Python web framework with REST API
- **PostgreSQL**: Primary database
- **Redis**: Caching and background tasks
- **Celery**: Asynchronous task processing
- **Docker**: Containerization

### Frontend

- **React**: Modern JavaScript UI framework
- **TypeScript**: Type-safe JavaScript development
- **React Router**: Client-side routing
- **Axios**: HTTP client for API calls

### Infrastructure

- **Docker Compose**: Local development orchestration
- **GitHub Actions**: CI/CD pipeline
- **Sentry**: Error tracking and performance monitoring

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 18+ (for local frontend development)
- Python 3.11+ (for local backend development)

### 1. Clone and Setup

```bash
git clone https://github.com/your-org/openmakersuite.git
cd openmakersuite
cp .env.example .env
```

### 2. Configure Environment

Edit `.env` with your settings:

```bash
# Database
DATABASE_URL=postgresql://postgres:password@db:5432/makerspace_inventory

# Django
SECRET_KEY=your-secret-key-here
DEBUG=1
ALLOWED_HOSTS=localhost,127.0.0.1

# Redis
REDIS_URL=redis://redis:6379/0
```

### 3. Start Services

```bash
# Development mode with hot reload
docker-compose up -d

# Or use the latest release (production-ready)
./scripts/verify-release.sh latest
./deploy.sh
```

### 4. Initialize Data

```bash
# Create superuser
docker-compose exec backend python manage.py createsuperuser

# Load sample data (optional)
docker-compose exec backend python manage.py loaddata fixtures/sample_data.json
```

### 5. Access the Application

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000/api/
- **Admin Panel**: http://localhost:8000/admin/
- **API Documentation**: http://localhost:8000/api/docs/

## 📱 Usage

### Basic Workflow

1. **Setup Categories & Locations**: Organize your inventory structure
2. **Add Items**: Create inventory items with details and suppliers
3. **Generate QR Codes**: Print QR codes for easy item identification
4. **Scan & Use**: Scan QR codes to log usage and check stock
5. **Automatic Reorders**: System suggests reorders based on usage patterns
6. **Receive Deliveries**: Update stock when supplies arrive

### QR Code Scanning

Access the scanner at `/scan/:itemId` or use the main scanner page:

- **Logged Users**: Manual reorder form with quantity control
- **Anonymous Users**: Automatic reorder submission
- **Real-time Updates**: Instant stock level updates

## 🔧 Development

### Local Development Setup

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
python manage.py migrate
python manage.py runserver

# Frontend
cd frontend
npm install
npm start
```

### Code Quality

The project includes comprehensive linting and testing:

```bash
# Backend
cd backend
black .                    # Code formatting
isort .                    # Import sorting
flake8 .                   # Style checking
bandit -r .               # Security scanning
pytest --cov             # Tests with coverage

# Frontend
cd frontend
npm run lint              # ESLint checking
npm test                  # Jest tests
npm run test:coverage     # Coverage report
```

### Database Migrations

```bash
# Create new migration
docker-compose exec backend python manage.py makemigrations

# Apply migrations
docker-compose exec backend python manage.py migrate
```

## 🧪 Testing

### Automated Testing

GitHub Actions runs comprehensive tests on every push:

- ✅ Backend unit & integration tests (pytest)
- ✅ Frontend component tests (Jest/React Testing Library)
- ✅ Code quality checks (Black, isort, flake8, ESLint)
- ✅ Security scanning (Bandit, Safety, Gitleaks)
- ✅ Docker build verification
- ✅ Coverage reporting (Codecov)

### Manual Testing

```bash
# Run specific test suites
docker-compose exec backend pytest inventory/tests/
docker-compose exec backend pytest reorder_queue/tests/

# Frontend tests
cd frontend && npm test

# Integration tests with test database
docker-compose -f docker-compose.test.yml up --build
```

## 🔐 Security

Security is a top priority with multiple layers of protection:

- **🔒 Authentication**: JWT-based authentication system
- **🛡️ Permissions**: Role-based access control (RBAC)
- **🔍 Scanning**: Automated security vulnerability scanning
- **🔐 Secrets**: No hardcoded credentials or API keys
- **📊 Monitoring**: Sentry integration for security issue tracking
- **✅ Validation**: Input validation and sanitization
- **🔒 HTTPS**: SSL/TLS encryption in production

### Security Scanning

```bash
# Backend security checks
bandit -r backend/
safety check --json

# Frontend dependency audit
cd frontend && npm audit

# Git secrets scanning
gitleaks detect
```

## 📦 Deployment

### Production Deployment

1. **Download Latest Release**:

   ```bash
   ./scripts/verify-release.sh latest
   ```

2. **Configure Environment**:

   ```bash
   cp .env.example .env
   # Edit .env with production settings
   ```

3. **Deploy**:
   ```bash
   ./deploy.sh
   ```

### Environment Variables

Key production environment variables:

```bash
# Security
SECRET_KEY=your-production-secret-key
DEBUG=0
ALLOWED_HOSTS=yourdomain.com

# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Monitoring
SENTRY_DSN=https://your-sentry-dsn
SENTRY_ENVIRONMENT=production

# Email (optional)
EMAIL_HOST=smtp.yourdomain.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
```

### Reverse Proxy Setup

Example Nginx configuration:

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
    }

    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
    }
}
```

## 📖 Documentation

- **🌐 [API Documentation](docs/api.md)**: REST API reference

## 🤝 Contributing

We welcome contributions! Please read our [Contributing Guidelines](CONTRIBUTING.md):

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Development Workflow

1. **Issues**: Check existing issues or create new ones
2. **Testing**: Ensure all tests pass locally
3. **Code Quality**: Run linting and formatting tools
4. **Documentation**: Update docs for new features
5. **Review**: Respond to code review feedback

## 📊 Monitoring & Analytics

### Sentry Integration

OpenMakerSuite integrates with Sentry for:

- **🐛 Error Tracking**: Automatic error capture and reporting
- **📈 Performance Monitoring**: API response times and database queries
- **📱 Release Tracking**: Associate errors with specific releases
- **🔔 Alerting**: Real-time notifications for critical issues

### Metrics Dashboard

The admin dashboard provides:

- 📊 Inventory levels and trends
- 📈 Usage patterns and analytics
- 🔄 Reorder queue status
- 👥 User activity logs
- 📦 Supplier performance metrics

## 🛟 Support

### Getting Help

- **📖 Documentation**: Check the docs folder for detailed guides
- **🐛 Issues**: Report bugs on GitHub Issues
- **💬 Discussions**: Join GitHub Discussions for questions
- **📧 Security**: Email security issues to security@yourdomain.com

### Community

- **🎯 Roadmap**: See planned features in GitHub Projects
- **🎉 Releases**: Follow release notes for updates
- **⭐ Star**: Star the repo if you find it useful!

## 📄 License

This project is licensed under the AGPL License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Django** community for the excellent web framework
- **React** team for the powerful UI library
- **Sigstore** for keyless cryptographic signing
- **PostgreSQL** for robust database functionality
- **All contributors** who help improve OpenMakerSuite
- **Hive13** Cincinnati's Premier Makerspace
- **Dallas Makerspace** The biggest makerspace in terms of membership count in the US, where I'm currently the COO.

---

**Built with ❤️ my makers, for the maker community**

_OpenMakerSuite helps makerspaces focus on creating instead of managing inventory._
