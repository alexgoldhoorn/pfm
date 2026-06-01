# Portfolio Management API - Flow Diagrams

## 1. User Authentication Flow

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant AuthManager
    participant Database

    Note over Client,Database: User Registration
    Client->>API: POST /api/v1/auth/register
    API->>AuthManager: register_user(username, email, password)
    AuthManager->>AuthManager: hash_password(password)
    AuthManager->>Database: INSERT INTO users
    Database-->>AuthManager: user_id
    AuthManager-->>API: UserResponse
    API-->>Client: 201 Created

    Note over Client,Database: User Login
    Client->>API: POST /api/v1/auth/login
    API->>AuthManager: login(username, password)
    AuthManager->>Database: SELECT user by username
    Database-->>AuthManager: user_data
    AuthManager->>AuthManager: verify_password(password, hash)
    AuthManager->>AuthManager: create_session_token()
    AuthManager->>Database: INSERT INTO user_sessions
    AuthManager-->>API: LoginResponse(token, user)
    API-->>Client: 200 OK + JWT Token

    Note over Client,Database: API Key Authentication
    Client->>API: GET /api/v1/assets (X-API-Key: key)
    API->>AuthManager: validate_api_key(key)
    AuthManager->>Database: SELECT api_key by hash
    Database-->>AuthManager: key_data
    AuthManager-->>API: validation_result
    API-->>Client: 200 OK + Data
```

## 2. Asset Management Flow

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Database
    participant PriceService

    Note over Client,PriceService: Create Asset
    Client->>API: POST /api/v1/assets/ (API Key)
    API->>Database: INSERT INTO assets
    Database-->>API: asset_id
    API-->>Client: 201 Created

    Note over Client,PriceService: List Assets with Prices
    Client->>API: GET /api/v1/assets/
    API->>Database: SELECT assets WHERE active=true
    Database-->>API: assets[]
    
    loop For each asset
        API->>Database: SELECT latest price
        Database-->>API: price_data
    end
    
    API-->>Client: 200 OK + Assets with current prices

    Note over Client,PriceService: Add Price Data
    Client->>API: POST /api/v1/assets/1/prices (API Key)
    API->>Database: INSERT INTO prices
    Database-->>API: price_id
    API-->>Client: 201 Created
```

## 3. Transaction Management Flow

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Database
    participant PortfolioEngine

    Note over Client,PortfolioEngine: Create Transaction
    Client->>API: POST /api/v1/transactions/ (API Key)
    API->>Database: SELECT asset_id (validate)
    Database-->>API: asset_exists
    API->>Database: SELECT portfolio_id (validate)
    Database-->>API: portfolio_exists
    API->>Database: INSERT INTO transactions
    Database-->>API: transaction_id
    API->>PortfolioEngine: update_portfolio_positions(portfolio_id)
    API-->>Client: 201 Created

    Note over Client,PortfolioEngine: List Transactions
    Client->>API: GET /api/v1/transactions/?limit=100 (API Key)
    API->>Database: SELECT transactions with asset details
    Database-->>API: transactions[]
    API-->>Client: 200 OK + Transaction list

    Note over Client,PortfolioEngine: Update Transaction
    Client->>API: PUT /api/v1/transactions/123 (API Key)
    API->>Database: SELECT transaction (verify exists)
    Database-->>API: transaction_data
    API->>Database: UPDATE transactions SET ...
    API->>PortfolioEngine: recalculate_positions(portfolio_id)
    API-->>Client: 200 OK
```

## 4. AI/LLM Chat Flow

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant LLMEngine
    participant IntentClassifier
    participant Database
    participant MarketDataService
    participant GeminiAPI

    Note over Client,GeminiAPI: AI Chat Request
    Client->>API: POST /api/v1/llm/chat (API Key)
    API->>IntentClassifier: classify_intent(message)
    IntentClassifier-->>API: intent_type

    alt Portfolio Analysis Intent
        API->>Database: SELECT portfolio positions
        Database-->>API: positions[]
        API->>MarketDataService: get_current_prices(symbols)
        MarketDataService-->>API: prices[]
    else Stock Screening Intent
        API->>MarketDataService: screen_stocks(criteria)
        MarketDataService-->>API: screening_results[]
    else Technical Analysis Intent
        API->>MarketDataService: get_technical_analysis(symbols)
        MarketDataService-->>API: technical_data[]
    end

    API->>LLMEngine: process_chat(message, context)
    LLMEngine->>GeminiAPI: send_prompt_with_context
    GeminiAPI-->>LLMEngine: ai_response
    LLMEngine-->>API: ChatResponse
    API-->>Client: 200 OK + AI Analysis
```

## 5. Tax Reporting Flow

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant TaxCalculator
    participant Database
    participant PDFGenerator

    Note over Client,PDFGenerator: Generate Tax Report
    Client->>API: GET /api/v1/tax/report?start_date=2024-01-01&end_date=2024-12-31&format=pdf
    API->>TaxCalculator: calculate_tax_report(user_id, date_range)
    TaxCalculator->>Database: SELECT buy transactions
    Database-->>TaxCalculator: buy_transactions[]
    TaxCalculator->>Database: SELECT sell transactions
    Database-->>TaxCalculator: sell_transactions[]
    TaxCalculator->>TaxCalculator: apply_fifo_matching()
    TaxCalculator->>TaxCalculator: calculate_gains_losses()
    TaxCalculator-->>API: tax_report_data

    alt CSV Format
        API->>API: generate_csv(tax_report)
        API-->>Client: CSV Download
    else PDF Format
        API->>PDFGenerator: create_pdf_report(tax_report)
        PDFGenerator-->>API: pdf_buffer
        API-->>Client: PDF Download
    end
```

## 6. Data Flow Architecture

```mermaid
graph TB
    Client[Web App Client]
    API[FastAPI Server]
    Auth[Auth Middleware]
    DB[(SQLite/PostgreSQL)]
    Cache[Redis Cache]
    LLM[Gemini LLM]
    Market[Market Data APIs]
    FileStorage[File Storage]

    Client -->|HTTP/HTTPS| API
    API --> Auth
    Auth --> DB
    API --> DB
    API --> Cache
    API --> LLM
    API --> Market
    API --> FileStorage

    subgraph "External Services"
        Market --> Yahoo[Yahoo Finance]
        Market --> Alpha[Alpha Vantage]
        LLM --> Gemini[Google Gemini]
    end

    subgraph "Data Layer"
        DB --> Users[Users Table]
        DB --> Assets[Assets Table]
        DB --> Transactions[Transactions Table]
        DB --> Prices[Prices Table]
        DB --> Portfolios[Portfolios Table]
    end
```

## 7. Error Handling Flow

```mermaid
graph TD
    Request[Incoming Request]
    Auth{API Key Valid?}
    Resource{Resource Exists?}
    Permission{Has Permission?}
    Process[Process Request]
    Success[200 OK Response]
    
    Error401[401 Unauthorized]
    Error404[404 Not Found]
    Error403[403 Forbidden]
    Error500[500 Server Error]
    
    Request --> Auth
    Auth -->|No| Error401
    Auth -->|Yes| Resource
    Resource -->|No| Error404
    Resource -->|Yes| Permission
    Permission -->|No| Error403
    Permission -->|Yes| Process
    Process -->|Success| Success
    Process -->|Exception| Error500
```

## 8. Real-time Data Flow (Future Enhancement)

```mermaid
sequenceDiagram
    participant Client
    participant WebSocket
    participant API
    participant PriceStream
    participant MarketData

    Note over Client,MarketData: WebSocket Connection
    Client->>WebSocket: Connect to wss://api/ws
    WebSocket->>API: validate_connection(token)
    API-->>WebSocket: connection_approved
    WebSocket-->>Client: Connected

    Note over Client,MarketData: Subscribe to Symbols
    Client->>WebSocket: subscribe(['AAPL', 'MSFT'])
    WebSocket->>PriceStream: add_subscriptions(symbols)
    
    Note over Client,MarketData: Real-time Updates
    MarketData->>PriceStream: price_update(AAPL, $150.25)
    PriceStream->>WebSocket: broadcast_update
    WebSocket->>Client: price_update_message
    Client->>Client: update_portfolio_display
```

## Key Integration Points for Web Application

### 1. Authentication Strategy
- **JWT Tokens** for session-based auth
- **API Keys** for service-to-service communication
- **Refresh Token** rotation for security
- **Role-based** access control (future)

### 2. Real-time Data Requirements
- **WebSocket** connections for live price updates
- **Server-Sent Events** for portfolio alerts
- **Polling fallback** for unsupported browsers

### 3. Caching Strategy
- **Browser caching** for static assets
- **Application caching** for API responses
- **Redis caching** for frequently accessed data
- **CDN caching** for global performance

### 4. Error Recovery Patterns
- **Retry logic** with exponential backoff
- **Circuit breaker** for external API calls  
- **Graceful degradation** when services are unavailable
- **User-friendly** error messages

### 5. Performance Optimization
- **Lazy loading** for large data sets
- **Virtual scrolling** for transaction lists
- **Debounced search** for asset lookup
- **Optimistic updates** for better UX

### 6. Security Measures
- **HTTPS** everywhere
- **CSRF protection** with tokens
- **XSS protection** with Content Security Policy
- **Input validation** on all endpoints
- **Rate limiting** to prevent abuse

This flow documentation provides the foundation for building a robust, scalable web application that integrates seamlessly with the Portfolio Management API.

