# Troubleshooting Guide

## Password Authentication Failed Error

**Error message:**
```
psycopg2.OperationalError: connection to server at "db" (172.19.0.4), port 5432 failed:
FATAL: password authentication failed for user "makerspace"
```

### Cause
This happens when the database volume already exists with a different password than what's in your `.env` file.

### Solution 1: Fresh Start (Recommended for new deployments)

**⚠️ WARNING: This deletes all database data**

```bash
# Use the reset script
./reset-and-deploy.sh
```

This will:
1. Stop all containers
2. Remove old volumes
3. Build fresh images
4. Start with your current .env password

### Solution 2: Quick Fix (If you can't lose data)

1. **Check what password the database expects:**
   ```bash
   ./diagnose.sh
   ```

2. **Update your .env file** with the correct password, OR

3. **Manually reset the database:**
   ```bash
   # Stop containers
   docker-compose -f docker-compose.prod.yml down

   # Remove ONLY the database volume
   docker volume rm openmakersuite_postgres_data

   # Start again
   docker-compose -f docker-compose.prod.yml up -d

   # Wait for database
   sleep 10

   # Run migrations
   docker-compose -f docker-compose.prod.yml exec backend python manage.py migrate
   ```

### Solution 3: Diagnose First

Run the diagnostic script to understand what's wrong:

```bash
./diagnose.sh
```

This will check:
- If .env exists and has required variables
- If containers are running
- If database password matches
- Recent error logs

## Other Common Issues

### Issue: Frontend shows blank page

**Check browser console for errors:**
- Press F12 in browser
- Look for API errors (CORS, 404, etc.)

**Verify nginx is routing correctly:**
```bash
# Check nginx logs
docker-compose -f docker-compose.prod.yml logs nginx

# Test API from inside container
docker-compose -f docker-compose.prod.yml exec nginx curl http://backend:8000/api/
```

### Issue: Static files (CSS/JS) not loading

**Collect static files:**
```bash
docker-compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput
```

**Check nginx can access them:**
```bash
docker-compose -f docker-compose.prod.yml exec nginx ls -la /app/staticfiles
```

### Issue: Can't access /admin

**Verify nginx routing:**
```bash
# Check nginx config
docker-compose -f docker-compose.prod.yml exec nginx cat /etc/nginx/conf.d/default.conf
```

**Test backend directly:**
```bash
docker-compose -f docker-compose.prod.yml exec backend curl http://localhost:8000/admin/
```

### Issue: Environment variables not loading

**Check .env format:**
- No spaces around `=`
- No quotes unless needed
- No comments on same line as values

**Good:**
```env
POSTGRES_PASSWORD=mypassword123
DOMAIN=example.com
```

**Bad:**
```env
POSTGRES_PASSWORD = "mypassword123"  # Don't do this
DOMAIN='example.com'  # Quotes not needed
```

**Verify variables are loaded:**
```bash
docker-compose -f docker-compose.prod.yml exec backend env | grep POSTGRES
```

### Issue: Container keeps restarting

**Check logs:**
```bash
docker-compose -f docker-compose.prod.yml logs [service-name]
```

**Common causes:**
- Missing required environment variables
- Database not ready
- Port already in use

**Check container health:**
```bash
docker-compose -f docker-compose.prod.yml ps
```

### Issue: Can't create superuser

**Interactive mode required:**
```bash
docker-compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
```

**Or create via Django shell:**
```bash
docker-compose -f docker-compose.prod.yml exec backend python manage.py shell
```

Then in Python:
```python
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.create_superuser('admin', 'admin@example.com', 'your-password')
```

## Useful Diagnostic Commands

```bash
# View all logs
docker-compose -f docker-compose.prod.yml logs

# Follow logs in real-time
docker-compose -f docker-compose.prod.yml logs -f

# Check specific service
docker-compose -f docker-compose.prod.yml logs backend

# See running processes
docker-compose -f docker-compose.prod.yml top

# Check resource usage
docker stats

# Inspect network
docker network inspect openmakersuite_app-network

# List volumes
docker volume ls | grep openmakersuite

# Inspect volume
docker volume inspect openmakersuite_postgres_data

# Execute commands in container
docker-compose -f docker-compose.prod.yml exec backend bash
docker-compose -f docker-compose.prod.yml exec db psql -U makerspace

# Check database connectivity
docker-compose -f docker-compose.prod.yml exec backend python manage.py dbshell

# Test API endpoint
docker-compose -f docker-compose.prod.yml exec backend python manage.py shell -c "
import requests
response = requests.get('http://backend:8000/api/inventory/items/')
print(response.status_code)
"
```

## Getting Help

1. Run `./diagnose.sh` to collect diagnostic info
2. Check logs: `docker-compose -f docker-compose.prod.yml logs`
3. Verify .env file has all required variables
4. Check [DEPLOYMENT.md](DEPLOYMENT.md) for setup instructions
5. Review nginx configuration in `nginx/conf.d/default.conf`
