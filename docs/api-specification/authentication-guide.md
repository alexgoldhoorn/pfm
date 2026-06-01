# Portfolio Management API - Authentication & Security Guide

## Authentication Methods Overview

The Portfolio Management API supports two primary authentication methods:

1. **API Key Authentication** - For programmatic access and service-to-service communication
2. **JWT Token Authentication** - For user sessions and web application access

---

## 1. API Key Authentication

### API Key Format
```
Key Length: 64 characters
Character Set: Base64 URL-safe (A-Z, a-z, 0-9, -, _)
Example: YOUR_API_KEY_HERE
```

### Usage
API keys are passed via the `X-API-Key` header:

```bash
curl -H "X-API-Key: YOUR_API_KEY_HERE" \
     https://api.portfolio.com/api/v1/assets
```

### API Key Management

#### Generate New API Key
```bash
# Using the CLI
python -m portf_manager create-api-key --name "Production API Key" --expires-in 365d

# Using the API (requires existing authentication)
curl -X POST https://api.portfolio.com/api/v1/auth/api-keys \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production API Key",
    "permissions": ["read", "write"],
    "expires_at": "2025-12-31T23:59:59Z"
  }'
```

#### List API Keys
```bash
curl -X GET https://api.portfolio.com/api/v1/auth/api-keys \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Response:
```json
{
  "api_keys": [
    {
      "id": 1,
      "name": "Production API Key",
      "key_preview": "y3S7****QoCC",
      "permissions": ["read", "write"],
      "is_active": true,
      "expires_at": "2025-12-31T23:59:59Z",
      "created_at": "2025-01-01T00:00:00Z",
      "last_used": "2025-09-16T12:00:00Z"
    }
  ]
}
```

#### Revoke API Key
```bash
curl -X DELETE https://api.portfolio.com/api/v1/auth/api-keys/1 \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### API Key Security Best Practices

1. **Storage**: Never store API keys in code or version control
2. **Environment Variables**: Use environment variables or secure vaults
3. **Rotation**: Rotate keys regularly (90-day recommended)
4. **Scope**: Use minimal permissions required
5. **Monitoring**: Monitor API key usage for anomalies

---

## 2. JWT Token Authentication

### Login Flow

#### User Registration
```bash
curl -X POST https://api.portfolio.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "email": "john@example.com",
    "password": "SecurePassword123!",
    "full_name": "John Doe"
  }'
```

Response:
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

#### User Login
```bash
curl -X POST https://api.portfolio.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "password": "SecurePassword123!"
  }'
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxLCJ1c2VybmFtZSI6ImpvaG5kb2UiLCJleHAiOjE2OTQ4ODAwMDB9.signature",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": 1,
    "username": "johndoe",
    "email": "john@example.com",
    "full_name": "John Doe",
    "is_active": true
  }
}
```

### Using JWT Tokens

#### Bearer Token Header
```bash
curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
     https://api.portfolio.com/api/v1/auth/me
```

#### Get Current User Info
```bash
curl -X GET https://api.portfolio.com/api/v1/auth/me \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Response:
```json
{
  "id": 1,
  "username": "johndoe",
  "email": "john@example.com",
  "full_name": "John Doe",
  "is_active": true,
  "created_at": "2025-09-16T12:00:00Z",
  "updated_at": "2025-09-16T12:00:00Z"
}
```

### JWT Token Structure

```
Header:
{
  "alg": "HS256",
  "typ": "JWT"
}

Payload:
{
  "user_id": 1,
  "username": "johndoe",
  "exp": 1694880000,
  "iat": 1694876400,
  "iss": "portfolio-api"
}

Signature:
HMACSHA256(
  base64UrlEncode(header) + "." + base64UrlEncode(payload),
  secret
)
```

### Token Expiration & Refresh

#### Token Lifetime
- **Access Token**: 1 hour (3600 seconds)
- **Refresh Token**: 7 days (future enhancement)
- **Session**: Configurable (default: 24 hours)

#### Password Change
```bash
curl -X POST https://api.portfolio.com/api/v1/auth/change-password \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "current_password": "SecurePassword123!",
    "new_password": "NewSecurePassword456!"
  }'
```

#### Logout
```bash
curl -X POST https://api.portfolio.com/api/v1/auth/logout \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

---

## 3. Security Implementation

### Password Security

#### Requirements
- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter  
- At least one number
- At least one special character
- No common passwords (dictionary check)

#### Hashing
- **Algorithm**: bcrypt with salt rounds = 12
- **Storage**: Never store plain text passwords
- **Validation**: Compare hashed values only

### API Security Headers

#### Required Headers
```http
# API Key Authentication
X-API-Key: your-api-key-here

# JWT Authentication  
Authorization: Bearer your-jwt-token-here

# Content Type (for POST/PUT requests)
Content-Type: application/json

# Optional: Request ID for tracking
X-Request-ID: 550e8400-e29b-41d4-a716-446655440000
```

### Rate Limiting (Recommended for Production)

#### Current Implementation
Rate limiting is not currently implemented but recommended for production:

```http
# Rate limit headers (future)
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1694880000
X-RateLimit-Window: 3600
```

#### Recommended Limits
- **Authentication endpoints**: 5 requests per minute
- **Read operations**: 1000 requests per hour
- **Write operations**: 100 requests per hour
- **AI/LLM endpoints**: 10 requests per minute

---

## 4. Error Responses

### Authentication Errors

#### 401 Unauthorized
```json
{
  "detail": "Invalid API key"
}
```

```json
{
  "detail": "Token has expired"
}
```

#### 403 Forbidden
```json
{
  "detail": "Insufficient permissions for this operation"
}
```

#### 429 Too Many Requests
```json
{
  "detail": "Rate limit exceeded. Try again in 60 seconds.",
  "retry_after": 60
}
```

---

## 5. Web Application Integration

### Authentication Flow Implementation

#### React Context Setup
```typescript
// contexts/AuthContext.tsx
interface AuthContextType {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  login: (credentials: LoginCredentials) => Promise<void>
  logout: () => void
  isLoading: boolean
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(
    localStorage.getItem('portfolio_token')
  )
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (token) {
      validateToken(token)
        .then(userData => {
          setUser(userData)
          setIsLoading(false)
        })
        .catch(() => {
          localStorage.removeItem('portfolio_token')
          setToken(null)
          setIsLoading(false)
        })
    } else {
      setIsLoading(false)
    }
  }, [token])

  const login = async (credentials: LoginCredentials) => {
    const response = await authService.login(credentials)
    setToken(response.access_token)
    setUser(response.user)
    localStorage.setItem('portfolio_token', response.access_token)
  }

  const logout = () => {
    setToken(null)
    setUser(null)
    localStorage.removeItem('portfolio_token')
  }

  return (
    <AuthContext.Provider value={{
      user,
      token,
      isAuthenticated: !!user,
      login,
      logout,
      isLoading
    }}>
      {children}
    </AuthContext.Provider>
  )
}
```

#### API Client with Token Interceptor
```typescript
// services/apiClient.ts
class ApiClient {
  private baseURL: string
  private token: string | null = null

  constructor(baseURL: string) {
    this.baseURL = baseURL
    this.token = localStorage.getItem('portfolio_token')
  }

  setAuthToken(token: string | null) {
    this.token = token
    if (token) {
      localStorage.setItem('portfolio_token', token)
    } else {
      localStorage.removeItem('portfolio_token')
    }
  }

  private async makeRequest<T>(
    method: string,
    endpoint: string,
    data?: any
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }

    if (this.token) {
      headers.Authorization = `Bearer ${this.token}`
    }

    const response = await fetch(url, {
      method,
      headers,
      body: data ? JSON.stringify(data) : undefined,
    })

    if (response.status === 401) {
      // Token expired, redirect to login
      this.setAuthToken(null)
      window.location.href = '/login'
      throw new Error('Authentication required')
    }

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Request failed')
    }

    return response.json()
  }

  async get<T>(endpoint: string): Promise<T> {
    return this.makeRequest<T>('GET', endpoint)
  }

  async post<T>(endpoint: string, data: any): Promise<T> {
    return this.makeRequest<T>('POST', endpoint, data)
  }
}

export const apiClient = new ApiClient('https://api.portfolio.com')
```

#### Protected Route Component
```typescript
// components/ProtectedRoute.tsx
interface ProtectedRouteProps {
  children: React.ReactNode
  requiredPermissions?: string[]
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ 
  children, 
  requiredPermissions = [] 
}) => {
  const { isAuthenticated, user, isLoading } = useAuth()

  if (isLoading) {
    return <LoadingSpinner />
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // Check permissions if specified
  if (requiredPermissions.length > 0 && user) {
    const hasPermissions = requiredPermissions.every(permission =>
      user.permissions?.includes(permission)
    )
    
    if (!hasPermissions) {
      return <AccessDenied />
    }
  }

  return <>{children}</>
}
```

---

## 6. Security Best Practices

### HTTPS Everywhere
```typescript
// Enforce HTTPS in production
if (process.env.NODE_ENV === 'production' && location.protocol !== 'https:') {
  location.replace(`https:${location.href.substring(location.protocol.length)}`)
}
```

### Content Security Policy
```http
Content-Security-Policy: default-src 'self'; 
  script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; 
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; 
  font-src 'self' https://fonts.gstatic.com; 
  img-src 'self' data: https:; 
  connect-src 'self' https://api.portfolio.com wss://api.portfolio.com;
```

### CORS Configuration
```python
# FastAPI CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://portfolio.com",
        "https://*.portfolio.com",
        "http://localhost:3000"  # Development only
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
```

### Input Validation & Sanitization
```typescript
// Client-side validation
const validateInput = (input: string): string => {
  return DOMPurify.sanitize(input.trim())
}

// API request validation
const validateApiRequest = (data: any): boolean => {
  // Implement schema validation
  return isValidSchema(data)
}
```

### Audit Logging
```python
# Server-side audit logging
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start_time = time.time()
    
    # Log request
    logger.info(f"Request: {request.method} {request.url}")
    
    response = await call_next(request)
    
    # Log response
    process_time = time.time() - start_time
    logger.info(f"Response: {response.status_code} ({process_time:.3f}s)")
    
    return response
```

This authentication guide provides comprehensive coverage of security implementation for both API and web application integration, following industry best practices for authentication, authorization, and secure communication.

