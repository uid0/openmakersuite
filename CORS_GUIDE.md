# CORS Configuration Guide

## What is CORS?

CORS (Cross-Origin Resource Sharing) is a browser security feature that prevents JavaScript running on one domain (e.g., `http://localhost:3000`) from making requests to another domain (e.g., `http://localhost:8000`) unless the server explicitly allows it.

## The Problem

When your React frontend (port 3000) tries to call your Django backend (port 8000), the browser blocks the request unless the backend explicitly allows cross-origin requests from port 3000.

### Symptoms of CORS Issues

In the **browser console** (not terminal), you'll see errors like:
```
Access to fetch at 'http://localhost:8000/api/...' from origin 'http://localhost:3000' 
has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present 
on the requested resource.
```

**Important**: CORS errors only appear in the browser console, not in curl or terminal requests!

## Current Configuration

### Production-Safe CORS (Default)
```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ALLOW_CREDENTIALS = True
```

This allows:
- Requests from `http://localhost:3000` ✅
- Requests from `http://127.0.0.1:3000` ✅
- Session cookies to be sent ✅
- All other origins blocked ❌

### Development Mode CORS (DEVELOPMENT_MODE=1)
```python
if DEVELOPMENT_MODE:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOW_CREDENTIALS = True
```

This allows:
- Requests from ANY origin ✅
- Useful for testing with different ports/IPs
- Only active when `DEVELOPMENT_MODE=1`

## How to Check for CORS Issues

### 1. Check Browser Console (Not Terminal!)

Open your browser's Developer Tools (F12) and look at the Console tab. CORS errors will appear there.

### 2. Check Network Tab

1. Open Developer Tools → Network tab
2. Make a request from your frontend
3. Click on the failed request
4. Check the "Headers" section
5. Look for `Access-Control-Allow-Origin` in the Response Headers

**Expected Response Headers:**
```
Access-Control-Allow-Origin: http://localhost:3000
Access-Control-Allow-Credentials: true
```

### 3. Test with curl (Will NOT Show CORS Errors!)

```bash
# This will work even if CORS is misconfigured!
curl -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: content-type" \
     -X OPTIONS \
     -v \
     http://localhost:8000/api/reorders/requests/pending/
```

Look for these headers in the response:
- `Access-Control-Allow-Origin`
- `Access-Control-Allow-Methods`
- `Access-Control-Allow-Headers`

## Common CORS Issues and Solutions

### Issue 1: Wrong Origin

**Symptom:** CORS error saying origin is not allowed

**Solution:** Check if you're accessing the frontend via the exact URL in `CORS_ALLOWED_ORIGINS`:
- ✅ `http://localhost:3000` (no trailing slash)
- ❌ `http://localhost:3000/` (has trailing slash)
- ❌ `https://localhost:3000` (https vs http)

### Issue 2: Missing Preflight Response

**Symptom:** OPTIONS request fails before the actual request

**Solution:** Already configured! The middleware handles OPTIONS automatically.

### Issue 3: Credentials Not Allowed

**Symptom:** Error about credentials when sending cookies/auth

**Solution:** Already configured with `CORS_ALLOW_CREDENTIALS = True`

### Issue 4: Custom Headers Blocked

**Symptom:** Request works in curl but not browser

**Solution:** Already configured with common headers including `authorization`

## Troubleshooting Steps

### Step 1: Verify corsheaders is Installed

```bash
cd backend
pip show django-cors-headers
```

Should show version 4.x or higher.

### Step 2: Check Middleware Order

The CORS middleware should be early in the middleware list (it is):
```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # ← Should be here
    "django.contrib.sessions.middleware.SessionMiddleware",
    # ... rest
]
```

### Step 3: Restart Backend

CORS settings require a backend restart to take effect:
```bash
cd backend
# Stop with Ctrl+C
python manage.py runserver
```

### Step 4: Check Browser Console

Make a request from the frontend and check for CORS-specific error messages.

### Step 5: Use Development Mode

If still having issues, enable development mode for maximum CORS permissiveness:
```bash
# In backend/.env
DEVELOPMENT_MODE=1
```

This sets `CORS_ALLOW_ALL_ORIGINS = True`.

## Testing CORS Configuration

### Test 1: Browser Test (Most Accurate)

```javascript
// In browser console (F12) on http://localhost:3000
fetch('http://localhost:8000/api/reorders/requests/pending/')
  .then(r => r.json())
  .then(console.log)
  .catch(console.error)
```

If you see CORS error → CORS is misconfigured  
If you see 401 error → CORS is working, but auth is failing  
If you see data → Everything works! 🎉

### Test 2: Network Tab Test

1. Open http://localhost:3000 in browser
2. Open DevTools → Network tab
3. Navigate to a page that makes API requests
4. Look at the request headers and response headers
5. Verify `Access-Control-Allow-Origin` is present

### Test 3: Preflight Test (curl)

```bash
curl -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: content-type" \
     -X OPTIONS \
     -v \
     http://localhost:8000/api/inventory/items/
```

Look for these in the response:
```
< Access-Control-Allow-Origin: http://localhost:3000
< Access-Control-Allow-Methods: DELETE, GET, OPTIONS, PATCH, POST, PUT
< Access-Control-Allow-Headers: accept, accept-encoding, authorization, content-type, ...
```

## Current Configuration Summary

### With DEVELOPMENT_MODE=0 (Production)
- ✅ Requests from `localhost:3000` allowed
- ✅ Requests from `127.0.0.1:3000` allowed
- ❌ Requests from other origins blocked
- ✅ Credentials allowed
- ✅ All standard HTTP methods allowed
- ✅ Common headers allowed

### With DEVELOPMENT_MODE=1 (Development)
- ✅ Requests from ALL origins allowed
- ✅ Credentials allowed
- ✅ All standard HTTP methods allowed
- ✅ Common headers allowed

This configuration is **production-safe** (restrictive by default) while being **development-friendly** (permissive with DEVELOPMENT_MODE=1).

## If CORS Still Doesn't Work

1. **Clear browser cache** - Old CORS headers can be cached
2. **Try incognito/private window** - Eliminates cache issues
3. **Check if backend is actually running** - `curl http://localhost:8000/api/`
4. **Verify frontend URL** - Make sure you're accessing from `localhost:3000`
5. **Check for proxy** - Some setups use nginx/proxy that might interfere
6. **Look at actual error message** - Browser console error is very specific

## Files Modified
- `backend/config/settings.py` - Enhanced CORS configuration

## References
- [django-cors-headers docs](https://github.com/adamchainz/django-cors-headers)
- [MDN CORS Guide](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS)

