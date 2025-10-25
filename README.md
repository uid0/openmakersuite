# Makerspace Inventory Management System

An open-source inventory management system designed specifically for makerspaces. This application helps manage inventory with QR code-enabled shelf labels, automated reordering workflows, and supplier integration.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![Django](https://img.shields.io/badge/django-4.2-green.svg)
![React](https://img.shields.io/badge/react-18.2-blue.svg)
![CI](https://github.com/[username]/dms-inventory-management-system/workflows/CI/badge.svg)
![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)

## Features

### 🏷️ Index Card Generation
- Generate printable 3x5" index cards with:
  - Product image
  - Item description
  - Reorder quantity
  - QR code for easy scanning
- Laminate and hang from shelves for quick identification

### 📱 QR Code Scanning
- Users scan QR codes with their phones
- View product details, average lead time, and current stock
- Submit reorder requests directly from scan page
- No login required for basic users

### 📊 Admin Dashboard
- Queue management for reorder requests
- Approve, order, and track deliveries
- Group requests by supplier for bulk ordering
- Generate shopping cart links for HD Supply, Grainger, and Amazon
- View estimated costs and lead times

### 📈 Usage Tracking & Analytics
- Log item usage over time
- Calculate average lead times based on historical data
- Predict reorder timing based on usage patterns
- Track low-stock items automatically

## Technology Stack

**Backend:**
- Django 4.2 + Django REST Framework
- PostgreSQL database
- Redis for caching and task queue
- Celery for async tasks
- ReportLab for PDF generation
- QRCode for QR generation

**Frontend:**
- React 18 with TypeScript
- React Router for navigation
- Axios for API calls
- Responsive mobile-first design

**Infrastructure:**
- Docker & Docker Compose
- Nginx (production)
- Supports self-hosting

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Git

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/makerspace-inventory.git
   cd makerspace-inventory
   ```

2. **Copy environment variables:**
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env` file:**
   - Change `POSTGRES_PASSWORD` to a secure password
   - Change `SECRET_KEY` to a random secret key
   - Update `ALLOWED_HOSTS` for production

4. **Start the application:**
   ```bash
   docker-compose up -d
   ```

5. **Run migrations:**
   ```bash
   docker-compose exec backend python manage.py migrate
   ```

6. **Create a superuser:**
   ```bash
   docker-compose exec backend python manage.py createsuperuser
   ```

7. **Access the application:**
   - Frontend: http://localhost:3000
   - Django Admin: http://localhost:8000/admin
   - API Documentation: http://localhost:8000/api/docs/

## Usage Guide

### Adding Inventory Items

1. Log in to Django Admin at http://localhost:8000/admin
2. Navigate to "Inventory Items" and click "Add Inventory Item"
3. Fill in the required fields:
   - Name, description, SKU
   - Upload product image
   - Set location, reorder quantity, minimum stock
   - Add supplier information
   - Save the item

### Generating QR Codes & Index Cards

**Via Django Admin:**
1. Open an inventory item
2. The QR code will be auto-generated on save
3. Click "Download Card" to get the 3x5" PDF
4. Print and laminate the card

**Via API:**
```bash
# Generate QR code
curl -X POST http://localhost:8000/api/inventory/items/{item-id}/generate_qr/

# Download index card PDF
curl http://localhost:8000/api/inventory/items/{item-id}/download_card/ --output card.pdf
```

### User Workflow (Scanning & Reordering)

1. User scans QR code on shelf label with phone camera
2. Browser opens to scan page showing item details
3. User optionally enters their name and notes
4. User clicks "Request Reorder" button
5. Request enters admin queue for approval

### Admin Workflow

1. Log in to admin dashboard at http://localhost:3000/admin
2. Review pending reorder requests
3. Approve or cancel requests
4. Click "View by Supplier" to group by supplier
5. Use supplier URLs to build carts on HD Supply, Grainger, or Amazon
6. Mark requests as "Ordered" with order number
7. When items arrive, mark as "Received" (auto-updates inventory)

## API Documentation

Full API documentation is available at http://localhost:8000/api/docs/ when the server is running.

### Key Endpoints

**Inventory:**
- `GET /api/inventory/items/` - List all items
- `GET /api/inventory/items/{id}/` - Get item details
- `GET /api/inventory/items/low_stock/` - Get low stock items
- `POST /api/inventory/items/{id}/generate_qr/` - Generate QR code
- `GET /api/inventory/items/{id}/download_card/` - Download PDF card

**Reorder Requests:**
- `GET /api/reorders/requests/` - List all requests
- `POST /api/reorders/requests/` - Create reorder request (public)
- `GET /api/reorders/requests/pending/` - Get pending requests
- `GET /api/reorders/requests/by_supplier/` - Group by supplier
- `POST /api/reorders/requests/{id}/approve/` - Approve request
- `POST /api/reorders/requests/{id}/mark_ordered/` - Mark as ordered
- `POST /api/reorders/requests/{id}/mark_received/` - Mark as received

## Configuration

### Environment Variables

See [.env.example](.env.example) for all available configuration options.

Key variables:
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `SECRET_KEY` - Django secret key
- `DEBUG` - Enable debug mode (development only)
- `ALLOWED_HOSTS` - Comma-separated list of allowed hosts

### Supplier Integration

The system supports three main suppliers out of the box:

- **HD Supply** - Set `supplier_type` to `hdsupply`
- **Grainger** - Set `supplier_type` to `grainger`
- **Amazon** - Set `supplier_type` to `amazon`

Add the supplier's product URL to each item for direct linking.

## Development

### Running Locally Without Docker

**Backend:**
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

**Frontend:**
```bash
cd frontend
npm install
npm start
```

### Running Tests

This project has comprehensive test coverage (80%+ backend, 70%+ frontend).

```bash
# Run all tests with coverage
make test

# Backend tests only
make test-backend

# Frontend tests only
make test-frontend

# Generate full coverage reports
make coverage

# Or manually:
docker-compose exec backend pytest --cov
docker-compose exec frontend npm run test:coverage
```

See [TESTING.md](TESTING.md) for detailed testing documentation.

### Database Migrations

```bash
# Create new migration
docker-compose exec backend python manage.py makemigrations

# Apply migrations
docker-compose exec backend python manage.py migrate
```

## Production Deployment

For production deployment:

1. Set `DEBUG=0` in `.env`
2. Configure proper `SECRET_KEY` and `POSTGRES_PASSWORD`
3. Update `ALLOWED_HOSTS` with your domain
4. Use a reverse proxy (Nginx/Caddy) for HTTPS
5. Set up automated backups for PostgreSQL
6. Consider using managed services for PostgreSQL and Redis

Example nginx configuration is provided in `nginx.conf.example` (to be created).

## CI/CD & Code Quality

This project uses GitHub Actions for continuous integration and deployment.

### Automated Testing

Every push and pull request triggers:
- **Backend Tests**: pytest with 95% coverage requirement
- **Frontend Tests**: Jest with 70% coverage requirement
- **Code Quality**: flake8, black, isort checks
- **Security Scans**: bandit, safety, gitleaks
- **Docker Build**: Full integration test

### Running Tests Locally

```bash
# Run all tests
make test

# Run backend tests only
make test-backend

# Run frontend tests only
make test-frontend

# Run code quality checks
make quality-backend

# Run security checks
make security-backend

# Run full CI suite locally
make ci-test
```

### Code Quality Tools

**Backend:**
- `flake8`: Linting and style checks
- `black`: Code formatting
- `isort`: Import sorting
- `bandit`: Security scanning
- `safety`: Dependency vulnerability checks

**Run all quality checks:**
```bash
make format-backend  # Auto-format code
make isort-backend   # Sort imports
make lint-backend    # Check for issues
```

### Pre-commit Workflow

Before committing:
```bash
make pre-commit  # Runs formatting, linting, and tests
```

**Pre-commit Hooks Available:**
- Automatic code formatting (black, isort)
- Linting (flake8)
- Security scanning (bandit)
- File cleanups (trailing whitespace, line endings)
- Django best practices (django-upgrade)

See [PRE_COMMIT_GUIDE.md](PRE_COMMIT_GUIDE.md) for detailed setup and usage.
See [CI_CD.md](CI_CD.md) for CI/CD documentation.

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Write tests for your changes (TDD encouraged)
4. Run `make pre-commit` to ensure code quality
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

**Contribution Guidelines:**
- All tests must pass
- Coverage must not decrease
- Follow PEP 8 style guide (enforced by flake8)
- Use type hints in Python code
- Write comprehensive docstrings

## License

This project is licensed under the AGPL - see the [LICENSE](LICENSE) file for details.

## Roadmap

- [ ] Barcode scanning support
- [ ] Email notifications for low stock
- [ ] Analytics dashboard with usage graphs
- [ ] Mobile app (React Native)
- [ ] Multi-location support
- [ ] Automated reordering based on usage patterns
- [ ] Integration with accounting software
- [ ] Bulk import/export functionality

## Support

- **Issues:** [GitHub Issues](https://github.com/yourusername/makerspace-inventory/issues)
- **Discussions:** [GitHub Discussions](https://github.com/yourusername/makerspace-inventory/discussions)
- **Email:** support@yourdomain.com

## Acknowledgments

Built with love for the maker community. Special thanks to all contributors and makerspaces who provided feedback.

---

**Made for Makerspaces, by Makerspaces** 🛠️
