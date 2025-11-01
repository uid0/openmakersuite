# DevContainer Development Environment

This devcontainer provides a consistent development environment that matches your CI/CD pipeline exactly.

## 🚀 Quick Start

### Prerequisites
- Docker Desktop (running)
- VS Code with "Dev Containers" extension

### Getting Started
1. **Open this project in VS Code**
2. **Click "Reopen in Container"** when prompted (or use `Cmd/Ctrl+Shift+P` → "Dev Containers: Reopen in Container")
3. **Wait for setup** (first time ~5-7 minutes, subsequent ~30 seconds)
4. **The setup script will automatically:**
   - Install all Python and Node.js dependencies
   - Set up SQLite database and Redis
   - Run database migrations
   - Create helper scripts

### Development Commands
Once inside the container, use these helper commands:

```bash
# Start development servers
./dev-commands.sh run-backend      # Django on :8000
./dev-commands.sh run-frontend     # React on :3000

# Run tests
./dev-commands.sh test-backend     # Backend tests
./dev-commands.sh test-frontend    # Frontend tests  
./dev-commands.sh test-all         # All tests

# Code quality
./dev-commands.sh lint-backend     # Backend linting
./dev-commands.sh lint-frontend    # Frontend linting

# Database
./dev-commands.sh migrate          # Run migrations
./dev-commands.sh shell            # Django shell
```

### Manual Commands
You can also run commands manually:

```bash
# Backend
cd backend
python manage.py runserver 0.0.0.0:8000
python -m pytest
flake8 . && black --check . && isort --check .

# Frontend  
cd frontend
npm start
npm test
npm run lint
```

## 🌐 Accessing Services

The devcontainer automatically forwards ports:

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **Django Admin**: http://localhost:8000/admin

**Database & Redis:** Run inside the container (no external access needed):
- SQLite database: `/workspace/backend/db.sqlite3`
- Redis: Available via `redis-cli` inside container

## 🛠️ Environment Details

**Development-Optimized Setup:**
- Ubuntu Linux (same as GitHub Actions)
- Python 3.11 with pip and development tools
- Node.js 18 with npm
- SQLite database (development-friendly)
- Redis 7 (via system service)

**Pre-installed Tools:**
- All Python dependencies (production + development)
- All Node.js dependencies
- Git, GitHub CLI
- VS Code extensions for Python, JavaScript, Docker
- Redis server (system service)

**Environment Variables:**
- `CI=true` (matches CI behavior)
- `DEBUG=1` (Django debug mode)
- `DATABASE_URL=sqlite:///db.sqlite3` (development database)
- `REDIS_URL=redis://localhost:6379/0` (local Redis)

## 🔧 Troubleshooting

### Container Won't Start
- ✅ **Docker Desktop running?** Check Docker Desktop is open and running
- ✅ **Dev Containers extension?** Install from VS Code marketplace
- ✅ **Rebuild container:** `Cmd/Ctrl+Shift+P` → "Dev Containers: Rebuild Container"
- ✅ **Check logs:** `Cmd/Ctrl+Shift+P` → "Dev Containers: Show Container Log"

### Setup Script Issues
If the automatic setup doesn't work, try:

```bash
# Rebuild the container
Cmd/Ctrl+Shift+P → "Dev Containers: Rebuild Container"

# Or run commands manually inside the container
cd backend && pip install -r requirements.txt -r requirements-dev.txt
cd ../frontend && npm install
cd ../backend && python manage.py migrate
```

### Services Not Ready
```bash
# Check database
ls -la backend/db.sqlite3

# Check Redis
redis-cli ping

# Check environment status
./dev-commands.sh status

# Restart Redis if needed
sudo service redis-server restart
```

### Port Conflicts
- **Check what's using ports:** `lsof -i :3000,8000`
- **Stop conflicting services:** Close other development servers
- **Only need ports 3000 and 8000** (SQLite and Redis run inside container)

### Performance Issues
- **Docker Desktop settings:** Allocate 4GB+ RAM, 2+ CPU cores
- **Windows:** Ensure WSL 2 is enabled and Docker is using WSL 2
- **macOS:** Increase Docker Desktop memory allocation

### Missing Commands
If `dev-commands.sh` isn't available, use these directly:

```bash
# Backend
cd backend
python manage.py runserver 0.0.0.0:8000  # Start server
python -m pytest                          # Run tests
python manage.py migrate                  # Database migrations
python manage.py shell                    # Django shell

# Frontend
cd frontend
npm start    # Development server
npm test     # Run tests
npm run lint # Code linting
```

### Still Having Issues?
1. **Try manual setup:** Run `./setup-dev.sh` from project root
2. **Check environment:** `./dev-commands.sh status` (if available)
3. **Restart Docker Desktop** and try "Rebuild Container" again
4. **Check VS Code output:** View → Output → Dev Containers

## 📂 Project Structure

```
/workspace/
├── backend/          # Django backend
├── frontend/         # React frontend
├── dev-commands.sh   # Helper scripts (created by setup)
├── verify-setup.sh   # Verification script (created by setup)
└── docker-compose.yml
```

## 🎯 Benefits

✅ **Consistency**: Identical to CI/CD environment  
✅ **Zero Setup**: Everything pre-installed and configured  
✅ **Team Sync**: Everyone gets the same environment  
✅ **Isolation**: No conflicts with your host system  
✅ **Fast**: Hot reloading, volume mounts, cached dependencies