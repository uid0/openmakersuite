# 🐳 DevContainer Authentication Fix

## The Real Issue

You're running in a **DevContainer**, which means:
- Your backend is running **inside the container** (at `/workspace`)
- I was editing files on the **host** (at `/Users/ian/Work/dms/openmakersuite`)
- The workspace is mounted into the container, so changes ARE shared
- BUT the `.env` file already had `DEVELOPMENT_MODE=1` added (I checked)
- So the real issue is: **the backend inside the container needs to be restarted**

## Current Status

✅ `.env` file has `DEVELOPMENT_MODE=1` (verified)
✅ Code changes are in place (mounted from host)
✅ `get_permissions()` methods fixed
❌ **Backend running inside container hasn't restarted**

## How to Fix in DevContainer

### Option 1: Using dev-commands.sh (Recommended)

1. **Stop the backend** (if running):
   - Find the terminal where you ran `./dev-commands.sh run-backend`
   - Press `Ctrl+C`

2. **Restart it**:
   ```bash
   ./dev-commands.sh run-backend
   ```

### Option 2: Manual Restart

1. **Find and kill the backend process**:
   ```bash
   # Inside the DevContainer
   lsof -ti:8000 | xargs kill -9
   ```

2. **Start it again**:
   ```bash
   cd /workspace/backend
   python manage.py runserver 0.0.0.0:8000
   ```

### Option 3: Run the Fix Script (does the checking for you)

```bash
# Inside the DevContainer
./.devcontainer/fix-permissions.sh
```

This will:
- Verify `DEVELOPMENT_MODE=1` is set
- Tell you if backend is running
- Give you instructions to restart

## Testing After Restart

### Inside DevContainer Terminal:
```bash
curl http://localhost:8000/api/reorders/requests/pending/
```

Expected: `HTTP 200` with data (or empty array `[]`)

### In Your Browser (on host):
```javascript
// Open http://localhost:3000 (forwarded from container)
// Browser Console (F12)
fetch('http://localhost:8000/api/reorders/requests/pending/')
  .then(r => r.json())
  .then(console.log)
  .catch(console.error)
```

Expected: Array of pending requests (or empty array)

## DevContainer Port Forwarding

Your `devcontainer.json` forwards ports:
- Port 3000 (Frontend) → Your browser accesses this
- Port 8000 (Backend) → Your browser accesses this

Both are forwarded from the container to your host, so:
- `localhost:8000` on your Mac → Port 8000 inside container
- `localhost:3000` on your Mac → Port 3000 inside container

## Why This Happened

1. ✅ I added `DEVELOPMENT_MODE=1` to the `.env` file on the host
2. ✅ The workspace is mounted, so the container sees the change
3. ✅ I fixed the code (`get_permissions()` methods)
4. ❌ BUT Django loads settings **when it starts**
5. ❌ The backend process **inside the container** is still running with old settings

## DevContainer-Specific Commands

```bash
# Check status
./dev-commands.sh status

# Run backend (this is what you need to restart)
./dev-commands.sh run-backend

# Run frontend
./dev-commands.sh run-frontend

# Test backend
./dev-commands.sh test-backend

# Verify Django sees DEVELOPMENT_MODE
cd /workspace/backend
python manage.py shell -c "from django.conf import settings; print(f'DEVELOPMENT_MODE={settings.DEVELOPMENT_MODE}')"
```

## Summary

**All the code fixes are done and visible to the container.**

You just need to:
1. Stop the backend (Ctrl+C in the terminal where it's running)
2. Start it again (`./dev-commands.sh run-backend`)
3. Test again

The `.env` file is shared between host and container (it's in the mounted workspace), and it already has `DEVELOPMENT_MODE=1`. The backend just needs to reload it.

## Quick Verification

Run this inside the DevContainer to see what Django will load:
```bash
cd /workspace/backend
cat .env | grep DEVELOPMENT_MODE
```

Should show: `DEVELOPMENT_MODE=1`

Then restart the backend and it will work! 🎉

