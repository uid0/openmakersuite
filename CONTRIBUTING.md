# Contributing to Makerspace Inventory Management System

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to this project.

## Code of Conduct

We are committed to providing a welcoming and inclusive environment. Please be respectful and constructive in all interactions.

## How to Contribute

### Reporting Bugs

1. Check if the bug has already been reported in [Issues](https://github.com/yourusername/makerspace-inventory/issues)
2. If not, create a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Screenshots if applicable
   - Environment details (OS, browser, Docker version)

### Suggesting Features

1. Check [existing feature requests](https://github.com/yourusername/makerspace-inventory/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement)
2. Create a new issue with the "enhancement" label
3. Describe the feature and its use case
4. Explain why it would be valuable for makerspaces

### Pull Requests

1. Fork the repository
2. Create a new branch: `git checkout -b feature/your-feature-name`
3. Make your changes following our coding standards
4. Make sure that new data fields have sane defaults.
5. Write or update tests as needed
6. Update documentation if required
7. Commit with clear, descriptive messages
8. Push to your fork and submit a pull request

## Development Setup

See the [README.md](README.md) for basic setup instructions.

### Code Style

**Python (Backend):**
- Follow PEP 8
- Use type hints where applicable
- Maximum line length: 100 characters
- Use meaningful variable and function names

**TypeScript (Frontend):**
- Use TypeScript strict mode
- Follow Airbnb style guide
- Use functional components with hooks
- Meaningful component and variable names

### Testing

**Backend:**
```bash
docker-compose exec backend python manage.py test
```

**Frontend:**
```bash
docker-compose exec frontend npm test
```

Please ensure all tests pass before submitting a PR.

## Project Structure

```
makerspace-inventory/
├── backend/              # Django backend
│   ├── config/          # Django settings
│   ├── inventory/       # Inventory app
│   ├── reorder_queue/   # Reorder queue app
│   └── requirements.txt
├── frontend/            # React frontend
│   ├── public/
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── services/
│       ├── types/
│       └── styles/
├── docker-compose.yml
└── README.md
```

## Commit Message Guidelines

Format: `<type>(<scope>): <subject>`

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

Examples:
- `feat(inventory): add barcode scanning support`
- `fix(api): resolve QR code generation error`
- `docs(readme): update installation instructions`

## Questions?

Feel free to ask questions in [Discussions](https://github.com/yourusername/makerspace-inventory/discussions) or create an issue.

Thank you for contributing! 🎉
