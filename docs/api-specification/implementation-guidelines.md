# Portfolio Management Web Application - Implementation Guidelines & Best Practices

## 📋 Table of Contents

1. [Coding Standards](#coding-standards)
2. [Accessibility (WCAG 2.1 AA)](#accessibility-wcag-21-aa)
3. [Performance Optimization](#performance-optimization)
4. [Security Best Practices](#security-best-practices)
5. [Versioning Strategy](#versioning-strategy)
6. [API Consumption Patterns](#api-consumption-patterns)
7. [Testing Guidelines](#testing-guidelines)
8. [Error Handling](#error-handling)
9. [Code Review Checklist](#code-review-checklist)

---

## 🎯 Coding Standards

### ESLint Configuration

```json
// .eslintrc.json
{
  "extends": [
    "eslint:recommended",
    "@typescript-eslint/recommended",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
    "plugin:jsx-a11y/recommended",
    "plugin:import/errors",
    "plugin:import/warnings",
    "plugin:import/typescript"
  ],
  "rules": {
    "react/react-in-jsx-scope": "off",
    "react/prop-types": "off",
    "@typescript-eslint/explicit-function-return-type": "warn",
    "@typescript-eslint/no-unused-vars": "error",
    "import/order": [
      "error",
      {
        "groups": [
          "builtin",
          "external",
          "internal",
          "parent",
          "sibling",
          "index"
        ],
        "newlines-between": "always"
      }
    ],
    "prefer-const": "error",
    "no-var": "error",
    "no-console": "warn"
  }
}
```

### Prettier Configuration

```json
// .prettierrc
{
  "semi": false,
  "trailingComma": "es5",
  "singleQuote": true,
  "printWidth": 100,
  "tabWidth": 2,
  "useTabs": false,
  "bracketSpacing": true,
  "arrowParens": "avoid"
}
```

### TypeScript Standards

#### Interface vs Type
```typescript
// Use interfaces for object shapes that might be extended
interface User {
  id: number
  username: string
  email: string
}

interface AdminUser extends User {
  permissions: string[]
}

// Use types for unions, primitives, and computed types
type Status = 'loading' | 'success' | 'error'
type UserWithStatus = User & { status: Status }
```

#### Naming Conventions
```typescript
// Constants: SCREAMING_SNAKE_CASE
const API_BASE_URL = 'https://api.portfolio.com'

// Types/Interfaces: PascalCase
interface PortfolioData {
  id: number
  name: string
}

// Variables/Functions: camelCase
const portfolioData = await fetchPortfolio()

// Components: PascalCase
const PortfolioCard: React.FC<Props> = ({ data }) => {
  return <div>{data.name}</div>
}

// Files: kebab-case for components, camelCase for utilities
// portfolio-card.tsx
// apiClient.ts
```

#### Function Components
```typescript
// Preferred: Arrow function with explicit return type
const PortfolioCard: React.FC<PortfolioCardProps> = ({ portfolio, onUpdate }) => {
  const [isLoading, setIsLoading] = useState<boolean>(false)

  const handleUpdate = useCallback(async () => {
    setIsLoading(true)
    try {
      await onUpdate(portfolio.id)
    } catch (error) {
      console.error('Update failed:', error)
    } finally {
      setIsLoading(false)
    }
  }, [portfolio.id, onUpdate])

  return (
    <div className="portfolio-card">
      <h3>{portfolio.name}</h3>
      <button onClick={handleUpdate} disabled={isLoading}>
        {isLoading ? 'Updating...' : 'Update'}
      </button>
    </div>
  )
}
```

---

## ♿ Accessibility (WCAG 2.1 AA)

### Semantic HTML
```tsx
// Good: Semantic structure
const Dashboard: React.FC = () => (
  <main role="main">
    <header>
      <h1>Portfolio Dashboard</h1>
    </header>
    <nav aria-label="Main navigation">
      <ul>
        <li><a href="/portfolio">Portfolio</a></li>
        <li><a href="/transactions">Transactions</a></li>
      </ul>
    </nav>
    <section aria-labelledby="portfolio-summary">
      <h2 id="portfolio-summary">Portfolio Summary</h2>
      {/* Content */}
    </section>
  </main>
)
```

### Form Accessibility
```tsx
const LoginForm: React.FC = () => {
  return (
    <form aria-labelledby="login-title">
      <h2 id="login-title">Sign In</h2>
      
      <div className="form-group">
        <label htmlFor="email">Email Address</label>
        <input
          id="email"
          type="email"
          required
          aria-describedby="email-error"
          aria-invalid={hasEmailError ? 'true' : 'false'}
        />
        {hasEmailError && (
          <div id="email-error" role="alert" className="error-message">
            Please enter a valid email address
          </div>
        )}
      </div>

      <div className="form-group">
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          required
          aria-describedby="password-help"
        />
        <div id="password-help" className="help-text">
          Must be at least 8 characters long
        </div>
      </div>

      <button type="submit" aria-describedby="login-status">
        Sign In
      </button>
    </form>
  )
}
```

### Color & Contrast
```css
/* Ensure minimum contrast ratios */
:root {
  --text-primary: #1a1a1a; /* 21:1 contrast on white */
  --text-secondary: #4a4a4a; /* 9.74:1 contrast on white */
  --link-color: #0056b3; /* 7.46:1 contrast on white */
  --error-color: #d63384; /* 5.47:1 contrast on white */
  --success-color: #198754; /* 4.56:1 contrast on white */
}

/* Never rely on color alone */
.status-indicator {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.status-success::before {
  content: '✓';
  color: var(--success-color);
}

.status-error::before {
  content: '✗';
  color: var(--error-color);
}
```

### Focus Management
```tsx
const Modal: React.FC<ModalProps> = ({ isOpen, onClose, children }) => {
  const modalRef = useRef<HTMLDivElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement as HTMLElement
      modalRef.current?.focus()
    } else {
      previousFocusRef.current?.focus()
    }
  }, [isOpen])

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      onClose()
    }
  }

  if (!isOpen) return null

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      ref={modalRef}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      <div className="modal-content">
        <button
          className="modal-close"
          onClick={onClose}
          aria-label="Close modal"
        >
          ×
        </button>
        {children}
      </div>
    </div>
  )
}
```

---

## ⚡ Performance Optimization

### Code Splitting
```tsx
// Route-level splitting
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Portfolio = lazy(() => import('./pages/Portfolio'))

// Component-level splitting for large components
const HeavyChart = lazy(() => import('./components/HeavyChart'))

// Dynamic imports for utilities
const formatCurrency = async (amount: number) => {
  const { format } = await import('./utils/currency')
  return format(amount)
}
```

### Image Optimization
```tsx
// Responsive images with lazy loading
const AssetImage: React.FC<{ symbol: string; alt: string }> = ({ symbol, alt }) => (
  <img
    src={`/api/assets/${symbol}/logo`}
    alt={alt}
    loading="lazy"
    decoding="async"
    sizes="(max-width: 768px) 50px, 100px"
    className="asset-logo"
  />
)

// WebP with fallback
const OptimizedImage: React.FC<ImageProps> = ({ src, alt, ...props }) => (
  <picture>
    <source srcSet={`${src}.webp`} type="image/webp" />
    <source srcSet={`${src}.jpg`} type="image/jpeg" />
    <img src={`${src}.jpg`} alt={alt} {...props} />
  </picture>
)
```

### Memoization Strategies
```tsx
// Expensive calculations
const PortfolioSummary: React.FC<{ transactions: Transaction[] }> = ({ transactions }) => {
  const portfolioMetrics = useMemo(() => {
    return calculatePortfolioMetrics(transactions)
  }, [transactions])

  return <div>{/* Render metrics */}</div>
}

// Component memoization
const TransactionRow = memo<TransactionRowProps>(({ transaction }) => (
  <tr>
    <td>{transaction.symbol}</td>
    <td>{transaction.quantity}</td>
    <td>{formatCurrency(transaction.price)}</td>
  </tr>
))

// Callback memoization
const TransactionList: React.FC<Props> = ({ transactions, onUpdate }) => {
  const handleUpdate = useCallback(
    (id: number) => {
      onUpdate(id)
    },
    [onUpdate]
  )

  return (
    <div>
      {transactions.map(transaction => (
        <TransactionRow
          key={transaction.id}
          transaction={transaction}
          onUpdate={handleUpdate}
        />
      ))}
    </div>
  )
}
```

### Virtual Scrolling for Large Lists
```tsx
import { FixedSizeList as List } from 'react-window'

const VirtualTransactionList: React.FC<{ transactions: Transaction[] }> = ({
  transactions,
}) => {
  const Row = ({ index, style }: { index: number; style: CSSProperties }) => (
    <div style={style}>
      <TransactionRow transaction={transactions[index]} />
    </div>
  )

  return (
    <List
      height={600}
      itemCount={transactions.length}
      itemSize={60}
      width="100%"
    >
      {Row}
    </List>
  )
}
```

---

## 🔒 Security Best Practices

### Content Security Policy
```typescript
// CSP configuration
const cspDirectives = {
  defaultSrc: ["'self'"],
  scriptSrc: ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net"],
  styleSrc: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
  fontSrc: ["'self'", "https://fonts.gstatic.com"],
  imgSrc: ["'self'", "data:", "https:"],
  connectSrc: ["'self'", "https://api.portfolio.com", "wss://api.portfolio.com"],
  objectSrc: ["'none'"],
  baseSrc: ["'self'"],
}
```

### Input Sanitization
```typescript
import DOMPurify from 'dompurify'

// Client-side sanitization
const sanitizeInput = (input: string): string => {
  return DOMPurify.sanitize(input.trim())
}

// Validation schemas using Zod
import { z } from 'zod'

const TransactionSchema = z.object({
  symbol: z.string().min(1).max(10).regex(/^[A-Z]+$/),
  quantity: z.number().positive(),
  price: z.number().positive(),
  date: z.string().datetime(),
})

const validateTransaction = (data: unknown) => {
  try {
    return TransactionSchema.parse(data)
  } catch (error) {
    throw new ValidationError('Invalid transaction data')
  }
}
```

### XSS Prevention
```tsx
// Safe HTML rendering
const SafeHTML: React.FC<{ content: string }> = ({ content }) => {
  const sanitizedContent = useMemo(
    () => DOMPurify.sanitize(content),
    [content]
  )

  return <div dangerouslySetInnerHTML={{ __html: sanitizedContent }} />
}

// Escape user input in URLs
const buildAssetUrl = (symbol: string): string => {
  const encodedSymbol = encodeURIComponent(symbol)
  return `/assets/${encodedSymbol}`
}
```

---

## 📦 Versioning Strategy

### Semantic Versioning (SemVer)
```
MAJOR.MINOR.PATCH

MAJOR: Breaking changes
MINOR: New features (backward compatible)
PATCH: Bug fixes (backward compatible)

Examples:
1.0.0 → 1.0.1 (bug fix)
1.0.1 → 1.1.0 (new feature)
1.1.0 → 2.0.0 (breaking change)
```

### API Versioning
```typescript
// API client with version support
class ApiClient {
  constructor(
    private baseURL: string,
    private version: string = 'v1'
  ) {}

  private buildUrl(endpoint: string): string {
    return `${this.baseURL}/api/${this.version}${endpoint}`
  }
}

// Version-specific endpoints
const apiV1 = new ApiClient('https://api.portfolio.com', 'v1')
const apiV2 = new ApiClient('https://api.portfolio.com', 'v2')
```

### Release Management
```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Create Release
        uses: actions/create-release@v1
        with:
          tag_name: ${{ github.ref }}
          release_name: Release ${{ github.ref }}
          draft: false
          prerelease: false
```

---

## 🌐 API Consumption Patterns

### Retry Logic with Exponential Backoff
```typescript
interface RetryConfig {
  maxRetries: number
  baseDelay: number
  maxDelay: number
  backoffFactor: number
}

const defaultRetryConfig: RetryConfig = {
  maxRetries: 3,
  baseDelay: 1000, // 1 second
  maxDelay: 10000, // 10 seconds
  backoffFactor: 2,
}

async function withRetry<T>(
  fn: () => Promise<T>,
  config: RetryConfig = defaultRetryConfig
): Promise<T> {
  let lastError: Error
  
  for (let attempt = 0; attempt <= config.maxRetries; attempt++) {
    try {
      return await fn()
    } catch (error) {
      lastError = error as Error
      
      if (attempt === config.maxRetries) {
        throw lastError
      }
      
      if (!isRetryableError(error)) {
        throw lastError
      }
      
      const delay = Math.min(
        config.baseDelay * Math.pow(config.backoffFactor, attempt),
        config.maxDelay
      )
      
      await new Promise(resolve => setTimeout(resolve, delay))
    }
  }
  
  throw lastError!
}

const isRetryableError = (error: any): boolean => {
  return (
    error.status >= 500 || // Server errors
    error.status === 429 || // Rate limit
    error.code === 'NETWORK_ERROR'
  )
}
```

### Circuit Breaker Pattern
```typescript
enum CircuitState {
  CLOSED = 'CLOSED',
  OPEN = 'OPEN',
  HALF_OPEN = 'HALF_OPEN',
}

class CircuitBreaker {
  private state = CircuitState.CLOSED
  private failures = 0
  private lastFailureTime = 0

  constructor(
    private failureThreshold: number = 5,
    private recoveryTimeout: number = 60000 // 1 minute
  ) {}

  async call<T>(fn: () => Promise<T>): Promise<T> {
    if (this.state === CircuitState.OPEN) {
      if (Date.now() - this.lastFailureTime > this.recoveryTimeout) {
        this.state = CircuitState.HALF_OPEN
      } else {
        throw new Error('Circuit breaker is OPEN')
      }
    }

    try {
      const result = await fn()
      this.onSuccess()
      return result
    } catch (error) {
      this.onFailure()
      throw error
    }
  }

  private onSuccess(): void {
    this.failures = 0
    this.state = CircuitState.CLOSED
  }

  private onFailure(): void {
    this.failures++
    this.lastFailureTime = Date.now()
    
    if (this.failures >= this.failureThreshold) {
      this.state = CircuitState.OPEN
    }
  }
}
```

### Request Deduplication
```typescript
class RequestDeduplicator {
  private pendingRequests = new Map<string, Promise<any>>()

  async request<T>(key: string, fn: () => Promise<T>): Promise<T> {
    if (this.pendingRequests.has(key)) {
      return this.pendingRequests.get(key)
    }

    const promise = fn()
      .finally(() => {
        this.pendingRequests.delete(key)
      })

    this.pendingRequests.set(key, promise)
    return promise
  }
}

// Usage with React Query
const useAsset = (symbol: string) => {
  return useQuery({
    queryKey: ['asset', symbol],
    queryFn: () => deduplicator.request(`asset-${symbol}`, () =>
      apiClient.get(`/assets/${symbol}`)
    ),
  })
}
```

---

## 🧪 Testing Guidelines

### Unit Testing with Vitest
```typescript
// utils/currency.test.ts
import { describe, it, expect } from 'vitest'
import { formatCurrency } from './currency'

describe('formatCurrency', () => {
  it('formats positive numbers correctly', () => {
    expect(formatCurrency(1234.56)).toBe('$1,234.56')
  })

  it('formats negative numbers correctly', () => {
    expect(formatCurrency(-1234.56)).toBe('-$1,234.56')
  })

  it('handles zero correctly', () => {
    expect(formatCurrency(0)).toBe('$0.00')
  })
})
```

### Component Testing
```tsx
// components/PortfolioCard.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { PortfolioCard } from './PortfolioCard'

const mockPortfolio = {
  id: 1,
  name: 'Test Portfolio',
  value: 10000,
}

describe('PortfolioCard', () => {
  it('renders portfolio information', () => {
    render(<PortfolioCard portfolio={mockPortfolio} />)
    
    expect(screen.getByText('Test Portfolio')).toBeInTheDocument()
    expect(screen.getByText('$10,000.00')).toBeInTheDocument()
  })

  it('calls onUpdate when update button is clicked', async () => {
    const mockOnUpdate = vi.fn()
    render(<PortfolioCard portfolio={mockPortfolio} onUpdate={mockOnUpdate} />)
    
    fireEvent.click(screen.getByText('Update'))
    
    await waitFor(() => {
      expect(mockOnUpdate).toHaveBeenCalledWith(1)
    })
  })
})
```

### E2E Testing with Playwright
```typescript
// tests/e2e/login.spec.ts
import { test, expect } from '@playwright/test'

test.describe('Login Flow', () => {
  test('successful login redirects to dashboard', async ({ page }) => {
    await page.goto('/login')
    
    await page.fill('[data-testid="email-input"]', 'user@example.com')
    await page.fill('[data-testid="password-input"]', 'password123')
    await page.click('[data-testid="login-button"]')
    
    await expect(page).toHaveURL('/dashboard')
    await expect(page.locator('h1')).toContainText('Portfolio Dashboard')
  })

  test('invalid credentials show error message', async ({ page }) => {
    await page.goto('/login')
    
    await page.fill('[data-testid="email-input"]', 'invalid@example.com')
    await page.fill('[data-testid="password-input"]', 'wrongpassword')
    await page.click('[data-testid="login-button"]')
    
    await expect(page.locator('[data-testid="error-message"]'))
      .toContainText('Invalid credentials')
  })
})
```

---

## 🚨 Error Handling

### Error Boundaries
```tsx
interface ErrorBoundaryState {
  hasError: boolean
  error?: Error
}

class ErrorBoundary extends Component<
  PropsWithChildren<{}>,
  ErrorBoundaryState
> {
  constructor(props: PropsWithChildren<{}>) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Error caught by boundary:', error, errorInfo)
    // Send to error reporting service
    reportError(error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-fallback">
          <h2>Something went wrong</h2>
          <p>We're sorry, but something unexpected happened.</p>
          <button onClick={() => window.location.reload()}>
            Reload Page
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
```

### Global Error Handling
```typescript
// Error reporting service
class ErrorReporter {
  static report(error: Error, context?: Record<string, any>) {
    const errorData = {
      message: error.message,
      stack: error.stack,
      timestamp: new Date().toISOString(),
      url: window.location.href,
      userAgent: navigator.userAgent,
      context,
    }

    // Send to logging service
    fetch('/api/errors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(errorData),
    }).catch(() => {
      // Fallback: log to console
      console.error('Failed to report error:', errorData)
    })
  }
}

// Global error handlers
window.addEventListener('error', event => {
  ErrorReporter.report(event.error)
})

window.addEventListener('unhandledrejection', event => {
  ErrorReporter.report(new Error(event.reason))
})
```

---

## ✅ Code Review Checklist

### Before Submitting PR
- [ ] All tests pass locally
- [ ] ESLint and Prettier checks pass
- [ ] TypeScript compilation succeeds
- [ ] No console.log statements (use proper logging)
- [ ] Accessibility requirements met
- [ ] Performance considerations addressed
- [ ] Security vulnerabilities checked
- [ ] Documentation updated if needed

### Reviewer Checklist
- [ ] Code follows established patterns
- [ ] Function and component names are descriptive
- [ ] Error handling is appropriate
- [ ] No hardcoded values or magic numbers
- [ ] Props and state properly typed
- [ ] Side effects properly handled with useEffect
- [ ] Memory leaks prevented (cleanup in useEffect)
- [ ] Loading and error states handled
- [ ] Accessibility attributes present
- [ ] Performance optimizations considered

### Security Review
- [ ] User inputs properly validated and sanitized
- [ ] No sensitive data in client-side code
- [ ] Authentication properly implemented
- [ ] API calls use proper error handling
- [ ] HTTPS enforced for external requests
- [ ] XSS vulnerabilities addressed

This comprehensive guide ensures consistent, maintainable, secure, and accessible code across the entire Portfolio Management web application.

