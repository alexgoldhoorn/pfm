# Configuration & Environment Management

This document describes the configuration and environment management system for the Portfolio Management API.

## Overview

The application uses a layered configuration system that supports:
- TOML configuration files (`settings.toml`)
- Environment-specific `.env` files
- Environment variables with prefixes
- Command-line argument overrides

## Configuration Sources (Priority Order)

1. **Command-line arguments** (highest priority)
2. **Environment variables** with `PORTF_` prefix
3. **Environment-specific .env files** (`.env.development`, `.env.staging`, `.env.production`)
4. **Default .env file** (`.env`)
5. **TOML configuration file** (`settings.toml`)
6. **Built-in defaults** (lowest priority)

## Configuration Files

### settings.toml

The main configuration file in TOML format:

```toml
[server]
host = "localhost"
port = 8000
debug = false
reload = false
workers = 1
log_level = "info"

[database]
database_url = "sqlite:///portfolio.db"

[app]
title = "Portfolio Management API"
description = "A comprehensive API for portfolio management"
version = "1.0.0"
environment = "development"

[security]
secret_key = "your-secret-key-change-in-production"
api_key_header = "X-API-Key"

[cors]
origins = [
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:8080"
]
```

### Environment Files

#### .env.development
Development environment settings:
```bash
PORTF_ENVIRONMENT=development
PORTF_DEBUG=true
PORTF_RELOAD=true
PORTF_DATABASE_URL=postgresql://portf_user:portf_password@localhost:5432/portf_db
```

#### .env.staging
Staging environment settings:
```bash
PORTF_ENVIRONMENT=staging
PORTF_DEBUG=false
PORTF_HOST=0.0.0.0
PORTF_WORKERS=2
PORTF_DATABASE_URL=postgresql://portf_user:portf_password@localhost:5432/portf_staging_db
```

#### .env.production
Production environment settings:
```bash
PORTF_ENVIRONMENT=production
PORTF_DEBUG=false
PORTF_HOST=0.0.0.0
PORTF_WORKERS=4
# PORTF_DATABASE_URL=postgresql://user:password@prod-db:5432/portf_production_db
# PORTF_SECRET_KEY=your-production-secret-key
```

## Environment Variables

All configuration options can be set via environment variables with the `PORTF_` prefix:

| Variable | Description | Default |
|----------|-------------|---------|
| `PORTF_HOST` | Server host | `localhost` |
| `PORTF_PORT` | Server port | `8000` |
| `PORTF_DEBUG` | Debug mode | `false` |
| `PORTF_RELOAD` | Auto-reload for development | `false` |
| `PORTF_WORKERS` | Number of worker processes | `1` |
| `PORTF_LOG_LEVEL` | Log level | `info` |
| `PORTF_DATABASE_URL` | Database connection URL | `sqlite:///portfolio.db` |
| `PORTF_ENVIRONMENT` | Application environment | `development` |
| `PORTF_SECRET_KEY` | Secret key for cryptographic operations | *required in production* |
| `PORTF_GEMINI_API_KEY` | Google Gemini API key | *optional* |

## Starting the Server

### Using Python directly

```bash
# Use default configuration
python start_server.py

# Override specific settings
python start_server.py --host=0.0.0.0 --port=8080 --reload

# Use specific environment
python start_server.py --env=staging

# Development with auto-reload
python start_server.py --env=development --reload
```

### Using Makefile

```bash
# Start development server
make dev

# Start with staging configuration
make dev-staging

# Start production server
make server
```

## Makefile Targets

### Development
- `make dev` - Start development server with auto-reload
- `make dev-staging` - Start development server with staging config

### Testing
- `make test` - Run all tests
- `make test-coverage` - Run tests with coverage report
- `make test-unit` - Run unit tests only
- `make test-integration` - Run integration tests only

### Database Operations
- `make migrate` - Run database migrations
- `make migrate-fresh` - Run fresh database migration (reset)
- `make migrate-test` - Test database migrations

### Docker Operations
- `make up` - Start all services with docker-compose
- `make up-build` - Start services and rebuild containers
- `make down` - Stop all services
- `make logs` - Show service logs
- `make restart` - Restart all services

### Code Quality
- `make install` - Install dependencies
- `make install-dev` - Install development dependencies
- `make lint` - Run linting checks
- `make format` - Format code with black and isort
- `make type-check` - Run type checking with mypy
- `make check` - Run all code quality checks

### Database Utilities
- `make db-shell` - Open database shell (requires running PostgreSQL)
- `make db-backup` - Create database backup

### Cleanup
- `make clean` - Clean up temporary files and caches
- `make clean-docker` - Clean up Docker containers and volumes

## Environment-Specific Usage

### Development
```bash
# Using environment file
make dev

# Using environment variables
PORTF_DEBUG=true PORTF_RELOAD=true python start_server.py

# Using command line
python start_server.py --env=development --reload --log-level=debug
```

### Staging
```bash
# Load staging configuration
make dev-staging

# Or directly
python start_server.py --env=staging
```

### Production
```bash
# Load production configuration
make server

# Or with Docker
make up
```

## Database Configuration

### SQLite (Development)
```bash
PORTF_DATABASE_URL=sqlite:///portfolio.db
```

### PostgreSQL (Production/Staging)
```bash
PORTF_DATABASE_URL=postgresql://username:password@host:port/database
```

### Using Docker Compose
The application includes a `docker-compose.yml` file for running PostgreSQL:

```bash
# Start PostgreSQL service
make up

# The database will be available at localhost:5432
# Connection details are in .env.development
```

## Security Considerations

### Production Deployment
1. **Never commit sensitive values** to version control
2. **Use secure secret management** for production secrets
3. **Set strong SECRET_KEY** for cryptographic operations
4. **Use environment variables** for sensitive configuration
5. **Limit CORS origins** to trusted domains only

### Example Production Environment Setup
```bash
# Set via secure environment variable management
export PORTF_SECRET_KEY="your-very-secure-secret-key"
export PORTF_DATABASE_URL="postgresql://user:pass@secure-db:5432/proddb"
export PORTF_GEMINI_API_KEY="your-production-gemini-key"

# Start production server
python start_server.py --env=production
```

## Troubleshooting

### Common Issues

1. **Missing dependencies**: Run `make install` or `pip install -r requirements.txt`
2. **Database connection errors**: Check `PORTF_DATABASE_URL` and ensure database server is running
3. **Configuration not loading**: Verify environment variable names have `PORTF_` prefix
4. **Port already in use**: Change `PORTF_PORT` or use `--port` argument

### Debug Configuration
To see the current configuration being used:

```bash
# The server logs will show:
# - Environment being used
# - Database URL (sanitized)
# - Port and host information
python start_server.py --log-level=debug
```

### Environment File Loading
Environment files are loaded in this order:
1. `.env.{environment}` (if `--env` specified)
2. `.env` (default)

To verify which file is being loaded, check the server startup logs.
