# DevContainer Setup for OpenMakerSuite

This devcontainer configuration provides a consistent development environment that matches the CI/CD pipeline, eliminating platform-specific differences between macOS, Windows, and Linux development.

## 🎯 Benefits

- **Consistent Environment**: Matches exactly with CI (Ubuntu 22.04, Python 3.11, Node.js 18)
- **Zero Setup**: All dependencies and tools pre-installed
- **Cross-Platform**: Works identically on macOS, Windows, and Linux
- **Isolated**: No conflicts with your host system
- **VS Code Integration**: Optimized extensions and settings included

## 📋 Prerequisites

1. **Docker Desktop**: Install from [docker.com](https://docs.docker.com/desktop/)
2. **VS Code**: Install from [code.visualstudio.com](https://code.visualstudio.com/)
3. **Dev Containers Extension**: Install from VS Code marketplace

## 🚀 Getting Started

### Option 1: VS Code Command Palette
1. Open VS Code
2. Press `Cmd/Ctrl + Shift + P`
3. Type "Dev Containers: Clone Repository in Container Volume"
4. Enter the repository URL
5. Wait for the container to build and setup

### Option 2: Existing Repository
1. Open the project in VS Code
2. Press `Cmd/Ctrl + Shift + P`
3. Type "Dev Containers: Reopen in Container"
4. Wait for the container to build and setup

### Option 3: Command Line
```bash
# Clone the repository
git clone https://github.com/your-org/openmakersuite.git
cd openmakersuite

# Open in VS Code
code .

# VS Code will prompt to reopen in container
```

## 🛠️ What's Included

### Development Tools
- **Python 3.11** with pip
- **Node.js 18** with npm
- **Git** with full history
- **PostgreSQL client** for database operations
- **Redis CLI** for cache operations

### Python Tools
- `flake8` - Code linting
- `black` - Code formatting  
- `isort` - Import sorting
- `pytest` - Testing framework
- `bandit` - Security scanning
- `ipython` - Enhanced REPL

### VS Code Extensions
- Python language support
- TypeScript/JavaScript support  
- ESLint integration
- Prettier formatting
- Docker tools
- Git tools (GitLens)
- Testing support

## 📝 Common Commands

### Running Tests (CI-Compatible)
```bash
# Backend tests (matches CI exactly)
cd backend
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
black --check --diff .
isort --check-only --diff .
pytest --cov --cov-report=xml --cov-fail-under=80

# Frontend tests (matches CI exactly)  
cd frontend
npm run lint
npm run test:ci
```

### Development Servers
```bash
# Start backend development server
cd backend
python manage.py runserver 0.0.0.0:8000

# Start frontend development server
cd frontend  
npm start
```

### Database Operations
```bash
# Run migrations
cd backend
python manage.py migrate

# Create superuser
./create_superuser.sh

# Access database directly
psql -h db -U makerspace -d makerspace_inventory
```

## 🌐 Port Forwarding

The devcontainer automatically forwards these ports:

- **3000** - Frontend (React)
- **8000** - Backend (Django) 
- **5432** - PostgreSQL (for external tools)
- **6379** - Redis (for external tools)

Access your application at:
- Frontend: http://localhost:3000
- Backend: http://localhost:8000

## 🔧 Troubleshooting

### Container Won't Start
1. Ensure Docker Desktop is running
2. Check Docker has sufficient resources (4GB+ RAM recommended)
3. Try rebuilding: `Cmd/Ctrl + Shift + P` → "Dev Containers: Rebuild Container"

### Services Not Ready
The setup script waits for PostgreSQL and Redis, but you might need to wait longer:
```bash
# Check service status
docker compose ps

# Check logs
docker compose logs db
docker compose logs redis
```

### Permission Issues
```bash
# Fix file permissions if needed
sudo chown -R vscode:vscode /workspace
```

### Tests Failing Differently Than CI
The devcontainer environment should match CI exactly. If you see differences:

1. Check Node.js version: `node --version` (should be 18.x)
2. Check Python version: `python --version` (should be 3.11.x)
3. Check environment variables: `env | grep CI`
4. Rebuild container to ensure clean state

## 🎛️ Customization

### Adding Extensions
Edit `.devcontainer/devcontainer.json` and add to the extensions array:
```json
"customizations": {
  "vscode": {
    "extensions": [
      "your-extension-id"
    ]
  }
}
```

### Environment Variables
Edit `.devcontainer/docker-compose.dev.yml` to add environment variables:
```yaml
services:
  devcontainer:
    environment:
      - YOUR_VAR=value
```

### Additional Dependencies
Edit `.devcontainer/requirements.dev.txt` for Python packages or modify the Dockerfile for system packages.

## 🔄 Updates

To update the devcontainer:
1. Pull latest changes: `git pull`
2. Rebuild container: `Cmd/Ctrl + Shift + P` → "Dev Containers: Rebuild Container"

## 💡 Tips

- Use the integrated terminal for all commands
- Git history is preserved in the container
- Extensions and settings are automatically configured
- The setup script runs automatically on first container creation
- Use `Cmd/Ctrl + Shift + P` to access Dev Container commands
- Container volumes persist data between rebuilds
