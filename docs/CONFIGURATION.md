# Configuration & Environment Management

This document describes the configuration and environment management system for the Portfolio Management API.

## Overview

The server settings are a `pydantic-settings` model (`portf_server/settings.py`,
class `ServerSettings`, env prefix `PORTF_`). They support:
- Environment variables with the `PORTF_` prefix
- `.env.local` and `.env` dotenv files (read by `ServerSettings` itself and by
  `start_server.py`)
- Command-line argument overrides (`start_server.py --host/--port/...`)

## Configuration Sources (Priority Order)

1. **Command-line arguments** of `start_server.py` (highest priority)
2. **Environment variables** with `PORTF_` prefix
3. **`.env.local`**, then **`.env`** (`ServerSettings.model_config.env_file`)
4. **Built-in defaults** (lowest priority)

Other `.env.*` files are not read by the settings class. `make dev` exports
`.env.development` into the environment before starting, and Docker Compose
loads them through `env_file:` (`.env`, `.env.development`, `.env.local` for
the dev backend; `.env`, `.env.production` for `backend-prod`).
`start_server.py --env=NAME` only sets `PORTF_ENVIRONMENT=NAME`; it does not
load `.env.NAME`.

## Configuration Files

### settings.toml

`settings.toml` in the repo root documents the defaults in TOML form and is
copied into the production image, but **it is not read**: `ServerSettings`
has no `toml_file` configured (the `pydantic-settings[toml]` extra is not
installed). Change settings through environment variables or `.env` files.

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

Only `.env.development` and `.env.example` are in the repo; `.env.staging`
and `.env.production` below are examples of what you would create.

#### .env.development
Development environment settings (excerpt):
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
| `PORTF_SECRET_KEY` | Secret key for cryptographic operations | placeholder; in production the server refuses to start unless it is changed and at least 32 characters long |
| `PORTF_API_KEY_HEADER` | Header carrying the API key | `X-API-Key` |
| `PORTF_CORS_ORIGINS` | Allowed CORS origins (JSON list) | `localhost:3000`, `localhost:8080`, `127.0.0.1:8080` |
| `PORTF_ALLOW_REGISTRATION` | Allow self-service `POST /api/v1/auth/register` | `false` |
| `PORTF_LLM_PROVIDER` / `PORTF_LLM_MODEL` | LLM provider and model (see `docs/features/llm.md`) | auto-detect |
| `PORTF_GEMINI_API_KEY` | Google Gemini API key | *optional* |
| `PORTF_OPENROUTER_API_KEY` | OpenRouter API key | *optional* |
| `PORTF_INDEXA_TOKEN` | Indexa Capital API token for account valuations | *optional* |
| `PORTF_METRICS_ENABLED`, `PORTF_METRICS_TOKEN`, `PORTF_SENTRY_DSN`, `PORTF_SENTRY_TRACES_SAMPLE_RATE`, `PORTF_OTEL_ENDPOINT`, `PORTF_OTEL_SERVICE_NAME` | Observability, all off unless set (see `docs/features/observability.md`) | off |

A few variables are read directly with `os.getenv()` rather than through
`ServerSettings`, so they have no `PORTF_` prefix: `SERVER_API_KEY` (auto-seeded
into `api_keys` at startup and handed out by `/api/v1/auth/login-key`),
`DATABASE_URL` (legacy fallback of `database_factory.get_database_adapter`),
`GOOGLE_SERVICE_ACCOUNT_FILE` / `GOOGLE_SPREADSHEET_ID`, the LLM provider keys
(`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_HOST` /
`OLLAMA_PORT`) and `PFM_BACKUP_DIR`. `PORTF_PUBLIC_VIEW`, `PORTF_LOG_FILE` and
`PORTF_LOG_RETENTION_DAYS` are prefixed but also read directly.

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

- `make test-js` - Run the web client JS tests (Node built-in runner)

### Database Operations
- `make migrate`, `make migrate-fresh`, `make migrate-test` - these targets
  still exist but call `migration_script.py` / `script_migrate_fresh.py` /
  `test_migration.py`, none of which is in the repo, so they exit with "not
  found". Migrations run automatically when the server starts (see
  `portf_manager/database.py`).

### Docker Operations
All compose targets run `WEB_PORT=8080 docker compose --profile dev`; the
compose file expects the external network `portf_net` to exist
(`docker network create portf_net`).
- `make up` - Start all services with docker-compose
- `make up-build` - Start services and rebuild containers
- `make up-backend` / `make up-web` - Rebuild and restart one service
- `make down` - Stop all services
- `make logs` / `make logs-backend` - Show service logs
- `make restart` - Restart all services
- `make status` - Show the `portf_*` containers

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

### Other
- `make requirements` - Export `requirements.txt` from `uv.lock`
- `make config-test*` - call `config_test.py`, which is not in the repo

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

# Or with Docker (make up uses the dev profile; production is the prod profile)
docker compose --profile prod up -d      # or scripts/deploy-prod.sh
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

1. **Missing dependencies**: Run `make install-dev` (`uv sync`); there is no `requirements.txt` in the repo (`make requirements` exports one)
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
`start_server.py` and `ServerSettings` load, without overriding variables
already in the environment:
1. `.env.local`
2. `.env`

`--env=NAME` only sets `PORTF_ENVIRONMENT`; it prints a warning when
`.env.NAME` is missing but never loads that file. Use `make dev`
(`.env.development`) or Docker Compose `env_file:` for per-environment files.
