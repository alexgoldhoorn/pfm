# Portfolio Management FastAPI Server

This is the FastAPI server implementation for the portfolio management system, providing a comprehensive REST API for portfolio tracking, transaction management, and financial analysis.

## Features

### 🔐 Authentication
- User registration and login
- JWT-like session token authentication
- Password management
- User profile management

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
- Transaction extraction from broker statements
- Natural language processing for financial documents
- Multi-language support (English, Spanish, etc.)

### 📋 Tax Reporting
- FIFO-based tax calculations
- Capital gains/loss reporting
- Tax period analysis

## API Documentation

Once the server is running, you can access:
- **Interactive API Documentation**: http://localhost:8000/docs
- **ReDoc Documentation**: http://localhost:8000/redoc
- **OpenAPI Schema**: http://localhost:8000/openapi.json

## Quick Start

### 1. Install Dependencies

```bash
pip install fastapi uvicorn pydantic email-validator python-multipart
```

### 2. Start the Server

**Simple startup:**
```bash
python ../start_server.py
```

**Development mode with auto-reload:**
```bash
python ../start_server.py --reload
```

**Custom host/port:**
```bash
python ../start_server.py --host 0.0.0.0 --port 8080
```

**Or use uvicorn directly:**
```bash
uvicorn portf_server.app:app --host localhost --port 8000 --reload
```

### 3. Test the API

```bash
# Health check
curl http://localhost:8000/health

# Get all sectors
curl http://localhost:8000/api/v1/sectors/

# Check LLM service
curl http://localhost:8000/api/v1/llm/health
```

## API Endpoints

### Core Endpoints
- `GET /` - Welcome message and API info
- `GET /health` - Health check endpoint

### Authentication (`/api/v1/auth`)
- `POST /register` - Register new user
- `POST /login` - User login
- `POST /logout` - User logout
- `GET /me` - Get current user info
- `POST /change-password` - Change user password

### Assets (`/api/v1/assets`)
- `GET /` - List all assets
- `POST /` - Create new asset
- `GET /{asset_id}` - Get specific asset
- `PUT /{asset_id}` - Update asset
- `DELETE /{asset_id}` - Delete asset (soft delete)
- `POST /{asset_id}/prices` - Add price data
- `GET /{asset_id}/prices` - Get price history

### Transactions (`/api/v1/transactions`)
- *Under construction* - Basic endpoints scaffolded

### Portfolios (`/api/v1/portfolios`)
- *Under construction* - Basic endpoints scaffolded

### Entities (`/api/v1/entities`)
- *Under construction* - Basic endpoints scaffolded

### Sectors (`/api/v1/sectors`)
- `GET /` - Get all available sectors
- `GET /{symbol}` - Get sector for specific asset symbol

### LLM (`/api/v1/llm`)
- `POST /extract-transactions` - Extract transactions from broker statements
- `GET /health` - Check LLM service availability

### Tax (`/api/v1/tax`)
- *Under construction* - Basic endpoints scaffolded

## Architecture

### Project Structure
```
portf_server/
├── __init__.py
├── app.py                  # Main FastAPI application
├── dependencies.py         # Dependency injection
├── README.md              # This file
├── routers/               # API route handlers
│   ├── __init__.py
│   ├── auth.py           # Authentication routes
│   ├── assets.py         # Asset management routes
│   ├── transactions.py   # Transaction routes (basic)
│   ├── portfolios.py     # Portfolio routes (basic)
│   ├── entities.py       # Entity routes (basic)
│   ├── sectors.py        # Sector analysis routes
│   ├── llm.py           # LLM/AI routes
│   └── tax.py           # Tax reporting routes (basic)
└── schemas/              # Pydantic models
    ├── __init__.py
    ├── auth.py          # Authentication schemas
    └── assets.py        # Asset schemas
```

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
- **Database Layer**: Uses `portf_manager.database.Database` for data persistence
- **Authentication**: Leverages `portf_manager.auth.AuthManager` for user management
- **Domain Models**: Integrates with `portf_manager.models` for business entities
- **AI Features**: Uses `portf_manager.gemini_client` for LLM-powered features
- **Tax Calculations**: Utilizes `portf_manager.tax_calculator` for tax reporting

## Configuration

### Environment Variables
- `GEMINI_API_KEY`: Required for LLM-powered transaction extraction

### Database
- SQLite database (`portfolio.db`) is created automatically
- Database migrations handled automatically on startup

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

- Session-based authentication with tokens
- CORS configuration for frontend integration
- Input validation via Pydantic schemas
- SQL injection protection through parameterized queries

## Status

### ✅ Completed
- FastAPI server scaffolding
- Authentication system (registration, login, logout)
- Asset management (full CRUD with price data)
- Sectors functionality
- LLM transaction extraction
- OpenAPI documentation
- Error handling and validation
- Database integration
- Health check endpoints

### 🔄 In Progress / To Do
- Complete transaction management endpoints
- Complete portfolio management endpoints  
- Complete entity management endpoints
- Complete tax reporting endpoints
- Add comprehensive tests
- Add rate limiting
- Add caching
- Add logging configuration
- Add deployment configuration

## Testing

Start the server and visit http://localhost:8000/docs to test all endpoints interactively using the auto-generated Swagger UI.
