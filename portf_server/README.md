# Portfolio Management FastAPI Server

This is the FastAPI server implementation for the portfolio management system, providing a comprehensive REST API for portfolio tracking, transaction management, and financial analysis.

## Features

### 🔐 Authentication
- API key authentication (`X-API-Key` header) on every data router; see
  `docs/API_KEY_AUTHENTICATION.md`
- Username/password login that hands the web client the shared
  `SERVER_API_KEY` (`POST /api/v1/auth/login-key`)
- Session-token login (`/login`, `/me`, `/logout`), password management
- Self-service registration only with `PORTF_ALLOW_REGISTRATION=true`

### 💰 Asset Management
- Create, read, update, delete assets (CRUD operations)
- Support for multiple asset types (stocks, bonds, crypto, ETFs, etc.)
- Price history tracking
- Real-time price data integration

### 📊 Transaction Management
- Transaction recording (buy, sell, dividend, split, transfers)
- Portfolio-based transaction organization
- Transaction validation and error handling

### 🗂️ Portfolio Management
- Multiple portfolio support
- Portfolio performance analytics
- Asset allocation analysis
- Unrealized gain/loss calculations

### 🏢 Entity Management
- Broker and platform management
- Multi-entity portfolio organization

### 📈 Sector Analysis
- GICS sector classification
- Asset sector mapping
- Sector-based portfolio analysis

### 🤖 AI-Powered Features
- Transaction, booking and deposit extraction from broker statements
- Portfolio chat with tools (`/api/v1/llm/chat`, persistent chat sessions)
- Provider-agnostic LLM client (Anthropic in production; Ollama, Gemini,
  OpenRouter) — see `docs/features/llm.md`

### 📋 Tax Reporting
- FIFO-based tax calculations
- Capital gains/loss reporting, Spanish IRPF report and PDF
- Wash-sale (antiaplicación) check

### 📈 And more
Analytics, fund look-through, net worth and balance sheet, spending and
budgets, research, rebalancing, the next-euro allocator, action items and the
PDT sync — each documented in `docs/features/`.

## API Documentation

Once the server is running, you can access:
- **Interactive API Documentation**: http://localhost:8000/docs
- **ReDoc Documentation**: http://localhost:8000/redoc
- **OpenAPI Schema**: http://localhost:8000/openapi.json

## Quick Start

### 1. Install Dependencies

```bash
uv sync            # from the repo root; dependencies live in pyproject.toml / uv.lock
```

### 2. Start the Server

All commands run from the repo root.

**Simple startup:**
```bash
uv run python start_server.py
```

**Development mode with auto-reload:**
```bash
uv run python start_server.py --reload      # or: make dev
```

**Custom host/port:**
```bash
uv run python start_server.py --host 0.0.0.0 --port 8080
```

**Or use uvicorn directly:**
```bash
uv run uvicorn portf_server.app:app --host localhost --port 8000 --reload
```

In Docker the dev backend (`portf_backend_dev`) runs `uvicorn --reload` on the
bind-mounted checkout; see `docker-compose.yml` and `docs/CONFIGURATION.md`.

### 3. Test the API

```bash
# Health check (no key needed)
curl http://localhost:8000/health

# Everything under /api/v1 needs an API key (SERVER_API_KEY, or one made with
# portf_server/api_key_cli.py create ...)
curl -H "X-API-Key: $SERVER_API_KEY" http://localhost:8000/api/v1/sectors/
curl -H "X-API-Key: $SERVER_API_KEY" http://localhost:8000/api/v1/portfolios/holdings
```

## API Endpoints

### Core Endpoints
- `GET /` - Welcome message and API info
- `GET /health` - Health check endpoint

### Authentication (`/api/v1/auth`, no API key required)
- `POST /login-key` - Username/password → the shared `SERVER_API_KEY`
- `POST /change-password-key` - Change password, authenticated by API key
- `POST /register` - Register new user (403 unless `PORTF_ALLOW_REGISTRATION=true`)
- `POST /login`, `POST /logout`, `GET /me`, `POST /change-password` - session-token flow

### Public (`/api/v1/public`, no API key required)
- `GET /summary` - %-only summary, 404 unless `PORTF_PUBLIC_VIEW=true`

### Assets (`/api/v1/assets`)
- `GET /` - List all assets (with latest price)
- `POST /` - Create new asset
- `GET /{asset_id}` - Get specific asset
- `PUT /{asset_id}` - Update asset (also `ticker`, `auto_price`)
- `DELETE /{asset_id}` - Delete asset (soft delete)
- `POST /{asset_id}/prices` - Add price data
- `GET /{asset_id}/prices` - Get price history
- `POST /resolve-tickers` - ISIN → ticker resolution

### Other data routers (all behind the API key)
Each prefix maps to `portf_server/routers/<name>.py`; the full per-feature
reference is in `docs/features/`:

| Prefix | Area | Doc |
|---|---|---|
| `/api/v1/transactions`, `/bookings`, `/portfolios`, `/entities`, `/sectors` | Core ledger, brokers | `networth-portfolios.md` |
| `/api/v1/import`, `/export`, `/sync` | File import/export, DB backup, PDT sync | `imports-exports.md` |
| `/api/v1/llm` | Extraction, chat, chat sessions | `llm.md` |
| `/api/v1/tax`, `/analytics` | Tax report, performance, risk, fees, data quality, price updates | `analytics.md` |
| `/api/v1/fund-profiles` | Fund look-through weights | `analytics.md` |
| `/api/v1/research`, `/rebalance`, `/watchlist` | Research, Portfolio Health, rebalance planner | `research-rebalance.md` |
| `/api/v1/networth`, `/goals`, `/deposits`, `/retirement` | Net worth, balance sheet, loans, retirement | `networth-portfolios.md` |
| `/api/v1/spending`, `/budgets` | Bank accounts, categories, budgets | `spending.md`, `budgeting.md` |
| `/api/v1/allocate` | Next-euro allocator | `allocator.md` |
| `/api/v1/action-items` | Detected issues | `action-items.md` |
| `/api/v1/market` | Quotes, FX, fundamentals | `prices-market.md` |
| `/api/v1/reports` | PDF portfolio report | `analytics.md` |
| `/api/v1/system` | `GET /logs`, `POST /restore` | `llm.md`, `docs/BACKUP.md` |
| `/api/v1/notifications` | Web push (`vapid-key` is public) | `web-client.md` |
| `/metrics` | Prometheus metrics, off unless `PORTF_METRICS_ENABLED` | `observability.md` |

## Architecture

### Project Structure
```
portf_server/
├── __init__.py
├── app.py                  # Main FastAPI application (lifespan, router mounting)
├── settings.py             # ServerSettings (pydantic-settings, PORTF_ prefix)
├── dependencies.py         # Dependency injection (db, auth, require_api_key_dep)
├── auth_middleware.py      # APIKeyManager, APIKeyBearer
├── api_key_cli.py          # create / list / deactivate / delete API keys
├── chat_tools.py           # The chat's tool definitions (call services directly)
├── observability.py        # Metrics, Sentry, OTel — all off unless configured
├── README.md               # This file
├── routers/                # One module per API area (see the prefix table above)
│   ├── auth.py, public.py, assets.py, transactions.py, portfolios.py, ...
│   └── system.py           # carries its own /api/v1/system prefix
└── schemas/                # Pydantic models shared by several routers
    ├── auth.py             # Authentication schemas
    └── assets.py           # Asset schemas (asset_type enum — keep in sync with models.py)
```

Most routers define their request/response models inline.

### Key Components

1. **FastAPI Application** (`app.py`)
   - Application lifecycle management
   - CORS configuration
   - Global exception handling
   - Router mounting
   - Dependency injection setup

2. **Dependencies** (`dependencies.py`)
   - Database connection management
   - Authentication middleware
   - User session handling

3. **Routers** (`routers/`)
   - Domain-specific route handlers
   - Business logic integration
   - Request/response validation
   - Error handling with proper HTTP status codes

4. **Schemas** (`schemas/`)
   - Pydantic models for request/response validation
   - Type safety and automatic documentation
   - Input sanitization

## Business Logic Integration

The FastAPI server integrates with the existing business logic in `portf_manager/`:
- **Database Layer**: `portf_manager.database_factory.get_database_adapter(settings.database_url)` — SQLite by default, PostgreSQL for `postgresql://` URLs (core tables only)
- **Authentication**: Leverages `portf_manager.auth.AuthManager` for user management
- **Domain Models**: Integrates with `portf_manager.models` for business entities
- **AI Features**: `portf_manager.llm_client.get_llm_client(tier=...)` — provider-agnostic, never a model slug
- **Tax Calculations**: Utilizes `portf_manager.tax_calculator` for tax reporting
- **Services**: `portf_manager/services/` holds the analytics, spending, budgeting, net-worth and allocator logic the routers call

## Configuration

### Environment Variables
All server settings use the `PORTF_` prefix (`portf_server/settings.py`); the
full list is in `docs/CONFIGURATION.md`. The ones you need first:
- `SERVER_API_KEY`: seeded into `api_keys` at startup and returned by `/auth/login-key`
- `PORTF_SECRET_KEY`: must be changed and ≥ 32 characters in production
- `PORTF_DATABASE_URL`: default `sqlite:///portfolio.db`
- LLM: `PORTF_LLM_PROVIDER` / `PORTF_LLM_MODEL` and the provider key
  (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, or an Ollama
  host) — see `docs/features/llm.md`

### Database
- SQLite database (`portfolio.db`) is created automatically
- Database migrations handled automatically on startup (schema version 35)

## Development

### Adding New Endpoints
1. Create/update schemas in `schemas/`
2. Implement route handlers in appropriate router file
3. Add business logic integration
4. Test the endpoints

### Error Handling
- All endpoints use proper HTTP status codes
- Business logic errors are wrapped in `HTTPException`
- Global exception handler catches unhandled errors
- Detailed error messages for development, generic for production

## Security

- API key authentication on every data router (`X-API-Key`), see `docs/API_KEY_AUTHENTICATION.md`
- Production refuses to start with the placeholder or a short `PORTF_SECRET_KEY`
- CORS configuration for frontend integration (`PORTF_CORS_ORIGINS`)
- Input validation via Pydantic schemas
- SQL injection protection through parameterized queries

## Status

The server is feature-complete for the areas listed above; `PROJECT_STATUS.md`
tracks what changed recently and what is pending. Rate limiting is not
implemented; caching uses the `kv_cache` table (`portf_manager/cache.py`);
logging is configured by `portf_manager.event_log.configure_logging()` and
persisted to `app_logs`.

## Testing

```bash
uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e   # unit (incl. tests/unit/test_api_routers.py)
uv run pytest tests/integration tests/e2e                              # spawns the real app on a temp DB
```

See `docs/TESTING.md`. For manual exploration start the server and visit
http://localhost:8000/docs (Swagger UI; authorize with your API key).
