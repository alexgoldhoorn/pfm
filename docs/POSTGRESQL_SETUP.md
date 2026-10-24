# PostgreSQL Setup Guide

Quick reference for setting up PostgreSQL with the Portfolio Manager.

## Quick Start with Docker

```bash
# 1. Copy environment file
cp .env.development .env

# 2. Start PostgreSQL (the service is behind the dev/prod profiles and the
#    compose file uses the external network portf_net)
docker network create portf_net 2>/dev/null || true
docker compose --profile dev up -d postgres

# 3. Set environment variable (the server reads PORTF_DATABASE_URL;
#    DATABASE_URL is only a fallback of database_factory)
export PORTF_DATABASE_URL="postgresql://portf_user:portf_password@localhost:5432/portf_db"

# 4. Run the API server against it
uv run python start_server.py
```

The CLI in local mode (`python -m portf_manager ...`) always opens the SQLite
file given by `--db-path` / `PORTF_DB_PATH`; to use PostgreSQL from the CLI,
run it in server mode (`--server http://localhost:8000 --api-key ...`) against
the server above.

> **Scope of the PostgreSQL adapter.** `portf_manager/database_pg.py` is at its
> own schema version 3 and implements the core tables only (users, entities,
> portfolios, assets, transactions, prices, portfolio_config, api_keys). The
> SQLite adapter is at version 35 with spending, budgets, fund profiles, loans
> and more; those features are not available on PostgreSQL.

## Files Created

- `docker-compose.yml` - Docker Compose configuration
- `.env.development` - Environment variables template
- `init.sql` - PostgreSQL initialization script
- `docs/POSTGRESQL_SETUP.md` - This quick reference guide

## Default Configuration

| Setting | Value |
|---------|-------|
| Database | portf_db |
| Username | portf_user |
| Password | portf_password |
| Port | 5432 (published on 127.0.0.1 only) |
| Host | localhost |

## Common Commands

```bash
# Start PostgreSQL
docker compose --profile dev up -d postgres

# Stop PostgreSQL
docker compose --profile dev down

# View logs
docker compose --profile dev logs postgres

# Connect to database (also: make db-shell)
docker compose --profile dev exec postgres psql -U portf_user -d portf_db

# Backup database (also: make db-backup)
docker compose --profile dev exec postgres pg_dump -U portf_user portf_db > backup.sql

# Check container status
docker compose --profile dev ps
```

## Environment Variables

The application uses these environment variables:

- `PORTF_DATABASE_URL` - Connection URL read by the server settings (default `sqlite:///portfolio.db`)
- `DATABASE_URL` - Legacy fallback read by `database_factory.get_database_adapter()` when no URL is passed
- `POSTGRES_USER` - Database username
- `POSTGRES_PASSWORD` - Database password
- `POSTGRES_DB` - Database name
- `POSTGRES_PORT` - Database port

## Troubleshooting

### Container won't start
```bash
# Check logs
docker compose --profile dev logs postgres

# Restart container
docker compose --profile dev restart postgres
```

### Connection refused
```bash
# Check if PostgreSQL is running
docker compose --profile dev ps

# Check port is exposed
docker port portf_postgres
```

### Permission denied
```bash
# Reset permissions
docker compose --profile dev exec postgres psql -U portf_user -d portf_db -c "GRANT ALL PRIVILEGES ON DATABASE portf_db TO portf_user;"
```

## Manual Installation

For users without Docker, see the main README.md for manual PostgreSQL installation instructions for macOS, Linux, and Windows.
