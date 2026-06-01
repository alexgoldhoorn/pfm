# Portfolio Management API - Complete Endpoint Documentation

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
Most endpoints require API Key authentication via the `X-API-Key` header.

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
  "description": "Purchase of AAPL shares",
  "user_id": 1
}
```

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
  "live": true,
  "search": true
}
```

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

**Authentication:** Required (User Authentication)

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

---

## User Management

### 1. Register User
**POST** `/api/v1/auth/register`

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
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
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

## Portfolios

### 1. List Portfolios
**GET** `/api/v1/portfolios/`

*Currently under construction*

### 2. Create Portfolio
**POST** `/api/v1/portfolios/`

*Currently under construction*

---

## Entities (Brokers)

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
  "version": "1.0.0",
  "environment": "development"
}
```

---

### 2. API Root
**GET** `/`

**Response (200):**
```json
{
  "message": "Welcome to Portfolio Management API",
  "version": "1.0.0",
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

*Not currently implemented but recommended for production*

---

## Asset Types Supported

- `stock` - Individual stocks
- `bond` - Government and corporate bonds  
- `crypto` - Cryptocurrencies
- `etf` - Exchange-traded funds
- `mutual_fund` - Mutual funds
- `commodity` - Commodities
- `cash` - Cash positions

---

## Price Types Supported

- `open` - Opening price
- `high` - High price
- `low` - Low price  
- `close` - Closing price (default)
- `adjusted_close` - Adjusted closing price

---

## Next Steps for Web Application

This API provides a solid foundation for building a comprehensive portfolio management web application. Key integration points include:

1. **Real-time Data:** WebSocket endpoints for live price updates
2. **File Uploads:** CSV import for bulk transaction loading
3. **Charting:** Price history and portfolio performance visualization
4. **Mobile Optimization:** Responsive design for mobile trading
5. **Advanced Analytics:** Integration with the AI/LLM chat for investment advice

