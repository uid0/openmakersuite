# Development Authentication Guide

## Quick Start (Session Authentication)

Since the backend has both JWT and Session authentication enabled, the easiest way for development is to use Django's built-in admin authentication:

### Step 1: Create a Superuser

```bash
cd backend
python manage.py createsuperuser
```

Follow the prompts to create your admin account.

### Step 2: Log In via Django Admin

1. Start the backend: `python manage.py runserver`
2. Navigate to: `http://localhost:8000/admin`
3. Log in with your superuser credentials
4. Keep this browser tab open

### Step 3: Use the Frontend

With the Django admin session active in your browser, all frontend requests will now be authenticated automatically via the session cookie.

1. Start the frontend: `cd frontend && npm start`
2. Navigate to: `http://localhost:3000`
3. All API calls will now work with your authenticated session!

---

## Alternative: Use JWT Tokens (For Production-like Testing)

If you want to test JWT authentication:

### 1. Get a Token

```bash
curl -X POST http://localhost:8000/api/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "your-username", "password": "your-password"}'
```

### 2. Store in Browser

Open browser console and run:
```javascript
localStorage.setItem('token', 'your-access-token-here');
```

### 3. Refresh the Page

The frontend will now include the JWT token in all requests.

---

## Development-Only: Allow Unauthenticated Access

For rapid development without authentication, you can temporarily allow all access:

**backend/config/settings.py:**
```python
# Add this near the top
DEVELOPMENT_MODE = config("DEVELOPMENT_MODE", default=False, cast=bool)

# Update REST_FRAMEWORK
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.AllowAny" if DEVELOPMENT_MODE 
        else "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    # ... rest of config
}
```

Then in your `.env`:
```
DEVELOPMENT_MODE=1
```

⚠️ **Warning**: Never enable `DEVELOPMENT_MODE` in production!

---

## Current Permission Setup

- **Inventory Items** (Categories, Items, Suppliers, Locations): `IsAuthenticatedOrReadOnly`
  - Anyone can READ
  - Authentication required for CREATE/UPDATE/DELETE
  
- **Reorder Requests**: `IsAuthenticated` (most actions)
  - Authentication required for all operations

- **Index Cards**: `IsAuthenticatedOrReadOnly`
  - Anyone can READ
  - Authentication required for CREATE/UPDATE/DELETE

