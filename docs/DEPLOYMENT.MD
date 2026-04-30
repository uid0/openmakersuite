# Deployment Guide for Dallas Makerspace Inventory Management System

This guide explains how to deploy the application to `dallas.openmakersuite.net`.

## Architecture

The production setup uses:
- **Nginx**: Reverse proxy that routes requests
  - `/` → React frontend
  - `/admin/` → Django admin
  - `/api/` → Django REST API
  - `/static/` → Django static files
  - `/media/` → User uploads
- **Backend**: Django + Gunicorn
- **Frontend**: React (built to static files)
- **Database**: PostgreSQL
- **Cache/Queue**: Redis + Celery

## Prerequisites

1. A server with Docker and Docker Compose installed — see
   [`deploy/PREREQUISITES.md`](../deploy/PREREQUISITES.md) for distro-specific
   install steps (Ubuntu/Debian, Fedora/RHEL, Arch) covering Docker, Compose,
   kubectl, Helm, Git, Node, and Python.
2. Domain pointing to your server: `dallas.openmakersuite.net`
3. (Optional) SSL certificate for HTTPS

## Quick Start

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd dms-inventory-management-system
```

### 2. Create environment file

```bash
cp .env.prod.example .env
nano .env  # Edit with your values
```

**Required variables:**
- `SECRET_KEY`: Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
- `POSTGRES_PASSWORD`: Strong database password
- `DOMAIN`: Your domain name

### 3. Deploy

```bash
./deploy.sh
```

The script will:
1. Build Docker images
2. Start the database (only) and wait for it to become healthy
3. **Verify migration state** — run `python manage.py showmigrations --plan --no-color`
   against the target DB using the freshly built backend image. Any pending
   migrations (lines prefixed with `[ ]`) are auto-applied via
   `python manage.py migrate --noinput` **before** the backend container starts
   accepting traffic. If `migrate` fails, the deploy aborts non-zero with a
   clear error — the backend never starts against a partially-migrated DB.
   The pre- and post-migrate `showmigrations` output is appended to
   `deploy.log` for auditability.
4. Start all remaining services
5. Collect static files
6. Optionally create a superuser

### Why the migration gate exists

Previously, migrations ran only *after* the backend container was already up.
If `migrate` silently failed or was skipped, Django would serve requests
against a schema that didn't match the model definitions — inserts would
silently drop data or return 500s (see bead `oms-p2x`, issue `oms-qqn`).
The gate makes it impossible for deployed code to run against an un-migrated
database.

### Simulating a pending migration (manual verification)

```bash
# Revert the last migration record in the DB so it shows as pending again:
docker compose -f docker-compose.prod.yml exec -T db \
    psql -U makerspace -d makerspace_inventory \
    -c "DELETE FROM django_migrations WHERE app='reorder_queue' AND name='0016_fix_order_in_packages_column_type';"

# Re-run deploy.sh — the script should detect the pending migration, log it,
# auto-apply, and then bring the stack up.
./deploy.sh

# Check the audit log:
tail -50 deploy.log
```

### 4. Access your application

- **Frontend**: http://dallas.openmakersuite.net
- **Admin**: http://dallas.openmakersuite.net/admin/
- **API**: http://dallas.openmakersuite.net/api/

## Manual Deployment

If you prefer manual control:

```bash
# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Build and start
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d

# Run migrations
docker-compose -f docker-compose.prod.yml exec backend python manage.py migrate

# Collect static files
docker-compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput

# Create superuser
docker-compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
```

## SSL/HTTPS Setup

### Using Let's Encrypt (Recommended)

1. Install certbot on your server:
```bash
apt-get install certbot
```

2. Get certificate:
```bash
certbot certonly --standalone -d dallas.openmakersuite.net
```

3. Copy certificates to nginx directory:
```bash
mkdir -p nginx/ssl
cp /etc/letsencrypt/live/dallas.openmakersuite.net/fullchain.pem nginx/ssl/cert.pem
cp /etc/letsencrypt/live/dallas.openmakersuite.net/privkey.pem nginx/ssl/key.pem
```

4. Uncomment HTTPS server block in `nginx/conf.d/default.conf`

5. Restart nginx:
```bash
docker-compose -f docker-compose.prod.yml restart nginx
```

## Troubleshooting

### Issue: Can't access /admin

**Problem**: Both frontend and backend have `/admin` routes

**Solution**: Nginx routes `/admin/` to Django backend, React router won't match it

### Issue: Static files not loading

**Problem**: Static files return 404

**Solution**:
```bash
# Ensure static files are collected
docker-compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput

# Check volumes
docker-compose -f docker-compose.prod.yml exec nginx ls -la /app/staticfiles
docker-compose -f docker-compose.prod.yml exec nginx ls -la /app/frontend
```

### Issue: .env not being loaded

**Problem**: Environment variables not available

**Solution**:
- Ensure `.env` exists in project root
- Check `env_file:` section in docker-compose.prod.yml
- Restart containers after changing .env

### Issue: Database connection failed

**Problem**: Backend can't connect to PostgreSQL

**Solution**:
```bash
# Check if database is running
docker-compose -f docker-compose.prod.yml ps

# View database logs
docker-compose -f docker-compose.prod.yml logs db

# Verify environment variables
docker-compose -f docker-compose.prod.yml exec backend env | grep DATABASE
```

## Useful Commands

```bash
# View logs
docker-compose -f docker-compose.prod.yml logs -f [service]

# Restart a service
docker-compose -f docker-compose.prod.yml restart [service]

# Run Django management command
docker-compose -f docker-compose.prod.yml exec backend python manage.py [command]

# Access Django shell
docker-compose -f docker-compose.prod.yml exec backend python manage.py shell

# Access database
docker-compose -f docker-compose.prod.yml exec db psql -U makerspace -d makerspace_inventory

# Backup database (quick form — see deploy/BACKUP_RESTORE.md for the full
# playbook covering media, secrets, and Kubernetes)
docker-compose -f docker-compose.prod.yml exec db pg_dump -U makerspace makerspace_inventory > backup.sql

# Restore database
cat backup.sql | docker-compose -f docker-compose.prod.yml exec -T db psql -U makerspace makerspace_inventory
```

## Updating the Application

```bash
# Preferred: use deploy.sh, which includes the pre-deploy migration gate.
./deploy.sh

# Manual equivalent (no migration gate — only use if deploy.sh is unavailable):
git pull
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d db
# ...wait for db healthy, then:
docker-compose -f docker-compose.prod.yml run --rm --no-deps \
    -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py showmigrations --plan --no-color
docker-compose -f docker-compose.prod.yml run --rm --no-deps \
    -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py migrate --noinput
docker-compose -f docker-compose.prod.yml up -d
docker-compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput
```

## Monitoring

### Health Checks

Check service health:
```bash
docker-compose -f docker-compose.prod.yml ps
```

### Logs

View real-time logs:
```bash
# All services
docker-compose -f docker-compose.prod.yml logs -f

# Specific service
docker-compose -f docker-compose.prod.yml logs -f backend
docker-compose -f docker-compose.prod.yml logs -f nginx
```

### Resource Usage

```bash
docker stats
```

## Security Checklist

- [ ] Change `SECRET_KEY` in `.env`
- [ ] Use strong `POSTGRES_PASSWORD`
- [ ] Set `DEBUG=0` in production
- [ ] Configure HTTPS with SSL certificate
- [ ] Set proper `ALLOWED_HOSTS`
- [ ] Configure firewall (allow only 80, 443)
- [ ] Regular backups of database and media (see [`deploy/BACKUP_RESTORE.md`](../deploy/BACKUP_RESTORE.md))
- [ ] Keep Docker images updated
- [ ] Review nginx access logs regularly

## Support

For issues or questions:
1. Check logs: `docker-compose -f docker-compose.prod.yml logs`
2. Verify configuration in `.env`
3. Review this guide's troubleshooting section
4. Check GitHub issues
