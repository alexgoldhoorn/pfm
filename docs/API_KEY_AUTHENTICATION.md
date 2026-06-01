# API Key Authentication

The Portfolio Management API now supports API key authentication for securing non-public endpoints.

## Overview

- **Simple `X-API-Key` header validation** against the `api_keys` database table
- **CLI utility** for generating, listing, and managing API keys
- **Secured endpoints** require valid API keys for access
- **Public endpoints** (like health checks and asset listings) remain open

## How It Works

1. **API Key Generation**: Use the CLI utility to generate secure API keys
2. **Key Storage**: Keys are hashed (SHA-256) and stored in the database
3. **Request Authentication**: Include the API key in request headers
4. **Validation**: Middleware validates keys against the database
5. **Access Control**: Protected endpoints require valid API keys

## Database Schema

The system adds an `api_keys` table with the following structure:

```sql
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_name TEXT NOT NULL,                -- Human-readable name
    key_hash TEXT NOT NULL UNIQUE,         -- SHA-256 hash of the key
    key_prefix TEXT NOT NULL,              -- First 8 chars for identification
    is_active BOOLEAN NOT NULL DEFAULT 1,  -- Active/inactive status
    description TEXT,                       -- Optional description
    last_used DATETIME,                     -- Last usage timestamp
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME                     -- Optional expiration
);
```

## CLI Utility

### Creating API Keys

```bash
python portf_server/api_key_cli.py create "My API Key" --description "Development key"
```

Optional parameters:
- `--description`: Add a description for the key
- `--expires-days N`: Set expiration in N days

### Listing API Keys

```bash
python portf_server/api_key_cli.py list
```

Shows all API keys with their status, creation date, and last usage.

### Deactivating API Keys

```bash
python portf_server/api_key_cli.py deactivate KEY_ID
```

Deactivates a key without deleting it.

### Deleting API Keys

```bash
python portf_server/api_key_cli.py delete KEY_ID [--force]
```

Permanently deletes an API key. Use `--force` to skip confirmation.

## Using API Keys

### Request Headers

Include your API key in requests using the `X-API-Key` header:

```bash
curl -H "X-API-Key: YOUR_API_KEY_HERE" http://localhost:8000/api/v1/assets
```

### Alternative: Authorization Header

You can also use the standard Authorization header:

```bash
curl -H "Authorization: Bearer YOUR_API_KEY_HERE" http://localhost:8000/api/v1/assets
```

## Protected Endpoints

The following endpoints require API key authentication:

### Assets
- `POST /api/v1/assets` - Create asset
- `PUT /api/v1/assets/{id}` - Update asset  
- `DELETE /api/v1/assets/{id}` - Delete asset
- `POST /api/v1/assets/{id}/prices` - Add price data

### Public Endpoints (No API Key Required)
- `GET /api/v1/assets` - List assets
- `GET /api/v1/assets/{id}` - Get asset details
- `GET /api/v1/assets/{id}/prices` - Get price history
- `GET /health` - Health check
- `GET /` - API information

## Security Features

1. **Secure Key Generation**: 64-character random keys using cryptographically secure methods
2. **Key Hashing**: Keys are SHA-256 hashed before database storage
3. **Prefix Identification**: Only first 8 characters shown for identification
4. **Usage Tracking**: Last usage timestamp updated on each request
5. **Expiration Support**: Optional key expiration dates
6. **Active/Inactive Status**: Keys can be deactivated without deletion

## Error Responses

When API key authentication fails, the API returns:

```json
{
  "detail": "Valid API key required"
}
```

HTTP Status: `401 Unauthorized`

## Implementation Details

### Middleware Components

1. **APIKeyManager**: Handles key generation, validation, and database operations
2. **APIKeyBearer**: Custom FastAPI security scheme supporting both headers
3. **require_api_key()**: Dependency factory for mandatory authentication
4. **optional_api_key()**: Dependency factory for optional authentication

### Integration

Protected endpoints use the dependency injection pattern:

```python
@router.post("/protected-endpoint")
async def protected_function(
    data: RequestModel,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_assets),
):
    # The api_key_info contains validated key information
    # This endpoint is now protected by API key authentication
    pass
```

### Key Rotation

To rotate an API key:

1. Create a new API key
2. Update your applications to use the new key  
3. Deactivate or delete the old key

## Best Practices

1. **Store Keys Securely**: Never commit API keys to version control
2. **Use Environment Variables**: Store keys in environment variables in production
3. **Regular Rotation**: Rotate keys periodically for security
4. **Monitor Usage**: Check the last_used timestamps to identify unused keys
5. **Set Expiration**: Use expiration dates for temporary keys
6. **Descriptive Names**: Use clear, descriptive names for your API keys

## Example Usage

### Generate a Key
```bash
python portf_server/api_key_cli.py create "Production API Key" --description "Main production key" --expires-days 365
```

### Use in Code (Python)
```python
import requests

api_key = "YOUR_API_KEY_HERE"
headers = {"X-API-Key": api_key}

response = requests.post(
    "http://localhost:8000/api/v1/assets",
    headers=headers,
    json={
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_type": "stock",
        "exchange": "NASDAQ",
        "currency": "USD"
    }
)
```

### Use with curl
```bash
# Create an asset
curl -X POST "http://localhost:8000/api/v1/assets" \
     -H "X-API-Key: YOUR_API_KEY_HERE" \
     -H "Content-Type: application/json" \
     -d '{"symbol":"AAPL","name":"Apple Inc.","asset_type":"stock","exchange":"NASDAQ","currency":"USD"}'
```

## Troubleshooting

### Common Issues

1. **401 Unauthorized**: Check that your API key is correct and active
2. **Key Not Found**: Verify the key exists with `python portf_server/api_key_cli.py list`
3. **Expired Key**: Check if your key has expired and create a new one if needed
4. **Database Error**: Ensure the database exists and has the api_keys table

### Debugging

Enable debug logging to see API key validation details:

```python
import logging
logging.getLogger("portf_server.auth_middleware").setLevel(logging.DEBUG)
```

This will show key validation attempts and results in the application logs.
