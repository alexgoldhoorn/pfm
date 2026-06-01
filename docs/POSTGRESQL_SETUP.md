# PostgreSQL Setup Guide

Quick reference for setting up PostgreSQL with the Portfolio Manager.

## Quick Start with Docker

```bash
# 1. Copy environment file
cp .env.development .env

# 2. Start PostgreSQL
docker-compose up -d postgres

# 3. Set environment variable
export DATABASE_URL="postgresql://portf_user:portf_password@localhost:5432/portf_db"

# 4. Run the application
python -m portf_manager list-portfolios
```

## Files Created

- `docker-compose.yml` - Docker Compose configuration
- `.env.development` - Environment variables template
- `init.sql` - PostgreSQL initialization script
- `POSTGRESQL_SETUP.md` - This quick reference guide

## Default Configuration

| Setting | Value |
|---------|-------|
| Database | portf_db |
| Username | portf_user |
| Password | portf_password |
| Port | 5432 |
| Host | localhost |

## Common Commands

```bash
# Start PostgreSQL
docker-compose up -d postgres

# Stop PostgreSQL
docker-compose down

# View logs
docker-compose logs postgres

# Connect to database
docker-compose exec postgres psql -U portf_user -d portf_db

# Backup database
docker-compose exec postgres pg_dump -U portf_user portf_db > backup.sql

# Check container status
docker-compose ps
```

## Environment Variables

The application uses these environment variables:

- `DATABASE_URL` - Full PostgreSQL connection string
- `POSTGRES_USER` - Database username
- `POSTGRES_PASSWORD` - Database password
- `POSTGRES_DB` - Database name
- `POSTGRES_PORT` - Database port

## Troubleshooting

### Container won't start
```bash
# Check logs
docker-compose logs postgres

# Restart container
docker-compose restart postgres
```

### Connection refused
```bash
# Check if PostgreSQL is running
docker-compose ps

# Check port is exposed
docker port $(docker-compose ps -q postgres)
```

### Permission denied
```bash
# Reset permissions
docker-compose exec postgres psql -U portf_user -d portf_db -c "GRANT ALL PRIVILEGES ON DATABASE portf_db TO portf_user;"
```

## Manual Installation

For users without Docker, see the main README.md for manual PostgreSQL installation instructions for macOS, Linux, and Windows.
