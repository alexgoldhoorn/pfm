# Portfolio Management API - Complete Endpoint Documentation

> **Scope note (2026-10-23).** This file documents the core endpoints the API
> started with (assets, transactions, portfolios, LLM, tax, auth) and was checked
> against the code on that date. The API has since grown to ~30 routers under
> `/api/v1/` (`import`, `export`, `bookings`, `spending`, `sync`, `rebalance`,
> `research`, `analytics`, `fund-profiles`, `reports`, `watchlist`, `goals`,
> `budgets`, `networth`, `allocate`, `retirement`, `deposits`, `market`,
> `action-items`, `notifications`, `system`, `public`, `sectors`). Those are
> documented per area in `docs/features/*.md`; the live OpenAPI schema at
> `/docs` is always complete.

## API Overview

**Base URL:** `http://127.0.0.1:8000`  
**API Version:** v1  
**Authentication:** API Key (X-API-Key header)

## Table of Contents

1. [Authentication](#authentication)
2. [Assets Management](#assets-management)
3. [Transactions](#transactions)
4. [Portfolios](#portfolios)  
5. [Entities (Brokers)](#entities-brokers)
6. [LLM/AI Services](#llmai-services)
7. [Tax Reporting](#tax-reporting)
8. [User Management](#user-management)
9. [Health & System](#health--system)

---

## Authentication

### API Key Requirements
Every data router is protected at include-time by an API key (`portf_server/app.py`,
`_PROTECTED`); only `/api/v1/auth/*` and `/api/v1/public/*` are outside it. The key
goes in the `X-API-Key` header (an `Authorization: Bearer <api key>` header is
accepted as a fallback). Keys are created with
`python -m portf_server.api_key_cli create NAME` or seeded from the
`SERVER_API_KEY` environment variable at startup; there is no HTTP endpoint for
key management and keys carry no per-key permissions.

```bash
curl -H "X-API-Key: YOUR_API_KEY_HERE" http://127.0.0.1:8000/api/v1/assets
```

---

## Assets Management

### 1. Create Asset
**POST** `/api/v1/assets/`

Creates a new asset in the system.

**Request Body:**
```json
{
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "asset_type": "stock",
  "exchange": "NASDAQ",
  "currency": "USD",
  "sector": "Technology",
  "description": "Apple Inc. designs, manufactures, and markets consumer electronics"
}
```

**Response (201):**
```json
{
  "id": 1,
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "asset_type": "stock",
  "exchange": "NASDAQ",
  "currency": "USD",
  "sector": "Technology",
  "description": "Apple Inc. designs, manufactures, and markets consumer electronics",
  "is_active": true,
  "created_at": "2025-09-16T12:00:00Z",
  "updated_at": "2025-09-16T12:00:00Z"
}
```

**Error Responses:**
- `409` - Asset with symbol already exists
- `400` - Invalid request data
- `500` - Internal server error

---

### 2. List Assets
**GET** `/api/v1/assets/`

Retrieves all assets with optional filtering.

**Query Parameters:**
- `active_only` (boolean, default: true) - Only return active assets
- `asset_type` (string, optional) - Filter by asset type
- `sector` (string, optional) - Filter by sector

**Response (200):**
```json
[
  {
    "id": 1,
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "asset_type": "stock",
    "exchange": "NASDAQ",
    "currency": "USD",
    "sector": "Technology",
    "description": "Apple Inc.",
    "is_active": true,
    "current_price": 150.25,
    "price_date": "2025-09-16"
  }
]
```

---

### 3. Get Asset by ID
**GET** `/api/v1/assets/{asset_id}`

Retrieves a specific asset with current price.

**Path Parameters:**
- `asset_id` (integer) - Asset ID

**Response (200):**
```json
{
  "id": 1,
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "asset_type": "stock",
  "exchange": "NASDAQ",
  "currency": "USD",
  "sector": "Technology",
  "is_active": true,
  "current_price": 150.25,
  "price_date": "2025-09-16"
}
```

**Error Responses:**
- `404` - Asset not found

---

### 4. Update Asset
**PUT** `/api/v1/assets/{asset_id}`

Updates an existing asset.

**Path Parameters:**
- `asset_id` (integer) - Asset ID

**Request Body (partial update allowed):**
```json
{
  "name": "Updated Apple Inc.",
  "sector": "Consumer Electronics",
  "is_active": false
}
```

**Authentication:** Required (API Key)

---

### 5. Delete Asset
**DELETE** `/api/v1/assets/{asset_id}`

Soft deletes an asset (sets is_active to false).

**Path Parameters:**
- `asset_id` (integer) - Asset ID

**Authentication:** Required (API Key)

**Response:** `204 No Content`

---

### 6. Add Price Data
**POST** `/api/v1/assets/{asset_id}/prices`

Adds price data for an asset.

**Path Parameters:**
- `asset_id` (integer) - Asset ID

**Request Body:**
```json
{
  "price": 150.25,
  "price_date": "2025-09-16",
  "price_type": "close",
  "volume": 50000000,
  "source": "yahoo_finance"
}
```

**Authentication:** Required (API Key)

---

### 7. Get Price History
**GET** `/api/v1/assets/{asset_id}/prices`

Retrieves price history for an asset.

**Path Parameters:**
- `asset_id` (integer) - Asset ID

**Query Parameters:**
- `start_date` (date, optional) - Start date (YYYY-MM-DD)
- `end_date` (date, optional) - End date (YYYY-MM-DD)
- `price_type` (string, default: "close") - Price type

**Response (200):**
```json
[
  {
    "id": 1,
    "asset_id": 1,
    "price": 150.25,
    "price_date": "2025-09-16",
    "price_type": "close",
    "volume": 50000000,
    "source": "yahoo_finance",
    "created_at": "2025-09-16T12:00:00Z"
  }
]
```

---

## Transactions

### 1. List Transactions
**GET** `/api/v1/transactions/`

Retrieves all transactions with optional filtering.

**Query Parameters:**
- `limit` (integer, default: 100) - Number of transactions to return
- `symbol` (string, optional) - Filter by asset symbol
- `portfolio_id` (integer, optional) - Filter by portfolio

**Authentication:** Required (API Key)

**Response (200):**
```json
[
  {
    "id": 1,
    "asset_id": 1,
    "portfolio_id": 1,
    "transaction_type": "buy",
    "quantity": 100,
    "price": 150.25,
    "total_amount": 15025.00,
    "fees": 9.99,
    "transaction_date": "2025-09-16",
    "description": "Purchase of AAPL shares",
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "currency": "USD"
  }
]
```

---

### 2. Create Transaction
**POST** `/api/v1/transactions/`

Creates a new transaction.

**Request Body:**
```json
{
  "asset_id": 1,
  "transaction_type": "buy",
  "quantity": 100,
  "price": 150.25,
  "total_amount": 15025.00,
  "transaction_date": "2025-09-16",
  "portfolio_id": 1,
  "fees": 9.99,
  "tax": 0,
  "currency": "USD",
  "description": "Purchase of AAPL shares"
}
```

`fees`, `tax`, `currency`, `description`, `portfolio_id` and `user_id` are optional;
`currency` defaults to the asset's currency when omitted. Transaction rows store
`user_id` NULL in practice (see CLAUDE.md, "Spanish tax").

**Authentication:** Required (API Key)

**Response (201):**
```json
{
  "message": "Transaction created successfully",
  "id": 1,
  "asset_id": 1,
  "transaction_type": "buy",
  "quantity": 100,
  "price": 150.25,
  "transaction_date": "2025-09-16"
}
```

---

### 3. Get Transaction by ID
**GET** `/api/v1/transactions/{transaction_id}`

Retrieves a specific transaction.

**Path Parameters:**
- `transaction_id` (integer) - Transaction ID

**Authentication:** Required (API Key)

---

### 4. Update Transaction
**PUT** `/api/v1/transactions/{transaction_id}`

Updates an existing transaction.

**Path Parameters:**
- `transaction_id` (integer) - Transaction ID

**Request Body (partial update):**
```json
{
  "quantity": 120,
  "price": 149.50,
  "description": "Updated purchase"
}
```

**Authentication:** Required (API Key)

---

### 5. Delete Transaction
**DELETE** `/api/v1/transactions/{transaction_id}`

Deletes a transaction permanently.

**Path Parameters:**
- `transaction_id` (integer) - Transaction ID

**Authentication:** Required (API Key)

**Response (200):**
```json
{
  "message": "Transaction deleted successfully",
  "id": 1
}
```

**Error Responses:**
- `404` - Transaction not found

---

## LLM/AI Services

### 1. Extract Transactions from Text
**POST** `/api/v1/llm/extract-transactions`

Extracts transactions from broker statement text using AI.

**Request Body:**
```json
{
  "text": "Date: 2025-09-16\nBought 100 shares of AAPL at $150.25\nTotal: $15,025.00\nFees: $9.99"
}
```

**Authentication:** Required (API Key)

**Response (200):**
```json
{
  "transactions": [
    {
      "tx_type": "buy",
      "symbol": "AAPL",
      "asset_name": "Apple Inc.",
      "quantity": 100,
      "price": 150.25,
      "date": "2025-09-16",
      "currency": "USD",
      "raw_text": "Bought 100 shares of AAPL at $150.25"
    }
  ],
  "count": 1
}
```

---

### 2. AI Chat with Portfolio Context
**POST** `/api/v1/llm/chat`

Interactive AI chat with portfolio awareness and stock analysis.

**Request Body:**
```json
{
  "message": "How is my portfolio performing? Should I buy more AAPL?",
  "session_id": "user123_session",
  "symbols": ["AAPL", "MSFT"],
  "live": true
}
```

`session_id`, `symbols` and `live` are optional (there is no `search` field; web
search grounding is decided server-side by the provider). Sessions are persisted
and managed via `GET`/`POST /api/v1/llm/chat/sessions`,
`PATCH`/`DELETE /api/v1/llm/chat/sessions/{session_id}` and
`GET /api/v1/llm/chat/sessions/{session_id}/messages` — see `docs/features/llm.md`.

**Authentication:** Required (API Key)

**Response (200):**
```json
{
  "session_id": "user123_session",
  "answer": "Based on your current portfolio holdings of AAPL (100 shares at $150.25 avg cost), your position is showing a 5% gain. Given the recent technical indicators showing RSI at 65 and positive earnings momentum, a moderate addition to your AAPL position could be beneficial. However, consider diversification across other tech stocks like MSFT which is showing strong fundamentals...",
  "context_summary": {
    "analysis_type": "portfolio",
    "data_sources": ["portfolio", "technical_analysis", "market_data"],
    "portfolios": 1,
    "positions": 5,
    "technical_symbols": 2
  },
  "recommendations": [
    {
      "symbol": "AAPL",
      "action": "buy",
      "confidence": 0.75,
      "target_price": 160.00,
      "reasoning": "Strong fundamentals and technical momentum"
    }
  ],
  "warnings": []
}
```

**Features:**
- **Smart Intent Detection:** Automatically detects if you're asking about:
  - Portfolio analysis
  - Stock screening ("find good dividend stocks")  
  - Technical analysis ("should I buy AAPL?")
  - Fundamental analysis ("is MSFT overvalued?")
  - General market questions

- **Integrated Data Sources:**
  - Your actual portfolio positions
  - Real-time market data
  - Technical analysis (RSI, MACD, moving averages)
  - Fundamental analysis (P/E ratios, financial health)
  - Stock screening capabilities
  - Web search for news and events

---

## Tax Reporting

### 1. Generate Tax Report
**GET** `/api/v1/tax/report`

Generates tax reports for capital gains/losses using FIFO methodology.

**Query Parameters:**
- `start_date` (date, required) - Start date (YYYY-MM-DD)
- `end_date` (date, required) - End date (YYYY-MM-DD)
- `symbols` (string, optional) - Comma-separated symbols
- `format` (string, default: "csv") - Output format ("csv" or "pdf")

**Authentication:** Required — both the API key (`X-API-Key`, router-level) **and** a
Bearer session token from `POST /api/v1/auth/login` (`get_current_user_id`).
The web client does not use this endpoint; it reads the JSON report from
`GET /api/v1/analytics/tax-report` (API key only) — see `docs/features/analytics.md`.

**Response:**
- CSV or PDF file download
- Content-Type: `text/csv` or `application/pdf`
- Content-Disposition: `attachment; filename=tax_report_2025.csv`

**Features:**
- FIFO cost basis calculation
- Long-term vs short-term capital gains classification
- Detailed transaction matching
- Summary statistics
- Professional PDF formatting (requires reportlab)

### 2. Tax Endpoint Index
**GET** `/api/v1/tax/`

Returns a small JSON description of the tax endpoints, methodology (FIFO) and formats.

---

## User Management

These endpoints are **not** behind the API-key dependency. Login issues an **opaque
session token** (`secrets.token_urlsafe`, held in the server process and in
`.portf_session`), not a JWT; passwords are hashed with PBKDF2-HMAC. The token is
sent as `Authorization: Bearer <token>` to `/me`, `/logout`, `/change-password` and
`/tax/report`. The web client instead calls `/login-key` (below) and then uses the
API key for everything.

### 1. Register User
**POST** `/api/v1/auth/register`

Self-service registration is **disabled by default** and returns `403` unless
`PORTF_ALLOW_REGISTRATION=true` (a registered user can exchange their password for
the shared server API key via `/login-key`).

**Request Body:**
```json
{
  "username": "johndoe",
  "email": "john@example.com",
  "password": "securepassword123",
  "full_name": "John Doe"
}
```

**Response (201):**
```json
{
  "id": 1,
  "username": "johndoe",
  "email": "john@example.com",
  "full_name": "John Doe",
  "is_active": true,
  "created_at": "2025-09-16T12:00:00Z"
}
```

---

### 2. User Login
**POST** `/api/v1/auth/login`

**Request Body:**
```json
{
  "username": "johndoe",
  "password": "securepassword123"
}
```

**Response (200):**
```json
{
  "access_token": "<opaque session token>",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "johndoe",
    "email": "john@example.com",
    "full_name": "John Doe",
    "is_active": true
  }
}
```

---

### 3. Get Current User
**GET** `/api/v1/auth/me`

**Authentication:** Required (Bearer Token)

---

### 4. Change Password
**POST** `/api/v1/auth/change-password`

**Request Body:**
```json
{
  "current_password": "oldpassword",
  "new_password": "newsecurepassword123"
}
```

**Authentication:** Required (Bearer Token)

---

### 5. Logout
**POST** `/api/v1/auth/logout`

**Authentication:** Required (Bearer Token)

---

### 6. Login for API Key (web client)
**POST** `/api/v1/auth/login-key`

Validates username/password and returns the configured `SERVER_API_KEY` so a browser
can use `X-API-Key` for all data calls. `500` if `SERVER_API_KEY` is not set.
Login endpoints are rate-limited to 5 attempts per minute per client+username (`429`).

**Request Body:**
```json
{
  "username": "johndoe",
  "password": "securepassword123"
}
```

**Response (200):**
```json
{
  "api_key": "YOUR_API_KEY_HERE",
  "username": "johndoe"
}
```

---

### 7. Change Password (web client)
**POST** `/api/v1/auth/change-password-key`

Password change without a Bearer session: the current password is the authorisation.

**Request Body:**
```json
{
  "username": "johndoe",
  "current_password": "oldpassword",
  "new_password": "newsecurepassword123"
}
```

---

## Portfolios

A portfolio is a broker account; a bank account is also a `portfolios` row with
`account_type='bank'` (create-only). Full detail: `docs/features/networth-portfolios.md`.

### 1. List Portfolios
**GET** `/api/v1/portfolios/`

### 2. Create Portfolio
**POST** `/api/v1/portfolios/` — `201`

### 3. Update Portfolio
**PUT** `/api/v1/portfolios/{portfolio_id}`

### 4. Delete Portfolio
**DELETE** `/api/v1/portfolios/{portfolio_id}` — `204`

### 5. Clear a Portfolio's Transactions
**DELETE** `/api/v1/portfolios/{portfolio_id}/transactions`

### 6. Portfolio Values and Holdings
**GET** `/api/v1/portfolios/values`, **GET** `/api/v1/portfolios/holdings`

Current value per portfolio and current positions (from `compute_positions`, see
CLAUDE.md "Positions & corporate actions").

**Authentication:** Required (API Key) for all of the above.

---

## Entities (Brokers)

Still placeholders as of 2026-10-23: both endpoints return
`{"message": "... under construction"}` and touch no data (`portf_server/routers/entities.py`).
Brokers are represented by portfolios in practice.

### 1. List Entities
**GET** `/api/v1/entities/`

*Currently under construction*

### 2. Create Entity
**POST** `/api/v1/entities/`

*Currently under construction*

---

## Health & System

### 1. Health Check
**GET** `/health`

**Response (200):**
```json
{
  "status": "healthy",
  "message": "Portfolio Management API is running",
  "version": "2.5.9",
  "environment": "development"
}
```

`version` is `Settings.version` in `portf_server/settings.py`; the value above is the
one at the time of writing. `/health`, `/`, `/docs` and `/openapi.json` need no API key.

---

### 2. API Root
**GET** `/`

**Response (200):**
```json
{
  "message": "Welcome to Portfolio Management API",
  "version": "2.5.9",
  "environment": "development",
  "documentation": "/docs",
  "openapi": "/openapi.json"
}
```

---

## Error Handling

All endpoints return consistent error responses:

**4xx Client Errors:**
```json
{
  "detail": "Asset not found"
}
```

**5xx Server Errors:**
```json
{
  "error": "Internal Server Error",
  "message": "An unexpected error occurred. Please try again later."
}
```

---

## Rate Limiting

Only the credential endpoints (`/auth/login`, `/auth/login-key`,
`/auth/change-password-key`) are rate-limited: 5 attempts per minute per client IP +
username, in-process, answering `429`. Data endpoints have no rate limit.

---

## Asset Types Supported

- `stock` - Individual stocks
- `bond` - Government and corporate bonds  
- `crypto` - Cryptocurrencies
- `etf` - Exchange-traded funds
- `index` - Market indices (benchmarks, not holdings)
- `mutual_fund` - Mutual funds
- `commodity` - Commodities
- `cash` - Cash positions

A new value must be added to both `portf_server/schemas/assets.py` and
`portf_manager/models.py` (see CLAUDE.md).

---

## Transaction Types Supported

- `buy`, `sell`
- `dividend`, `interest`
- `split` - stock split; the ratio is stored in `quantity` (2-for-1 = `2.0`)
- `transfer_in`, `transfer_out`

---

## Price Types Supported

- `open` - Opening price
- `high` - High price
- `low` - Low price  
- `close` - Closing price (default)
- `adjusted_close` - Adjusted closing price

---

## Web Application

The web client exists: `web_client/` is a vanilla JS + Bootstrap 5 single-page app
with no build step, served by nginx and talking to this API with `X-API-Key`
(see CLAUDE.md "Web Client" and `docs/features/web-client.md`). File import lives
under `/api/v1/import` (`docs/features/imports-exports.md`); there are no WebSocket
endpoints — prices are refreshed by a daily cron (`docs/features/prices-market.md`).

