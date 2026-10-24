# Portfolio Management Web Client

A responsive web client for the Portfolio Management API, built with vanilla JavaScript, Bootstrap 5, and Chart.js.

## Features

- **Login**: password, account creation or API key (login modal tabs)
- **Responsive Dashboard**: Portfolio overview with key metrics and interactive charts
- **Asset Management**: View, add, and manage investment assets
- **Transaction Tracking**: Record and view investment transactions
- **Mobile Responsive**: Optimized for desktop, tablet, and mobile devices
- Plus analytics, research, net worth, goals, Wealth Simulator, spending, budget,
  watchlist, action items, AI chat, import/export and diagnostics pages — the full
  page-by-page reference is `docs/features/web-client.md`

## Technology Stack

- **HTML5** - Semantic markup
- **CSS3** - Custom styles with CSS Grid and Flexbox
- **JavaScript (ES6+)** - Vanilla JavaScript with modern features
- **Bootstrap 5** - UI components and responsive layout
- **Chart.js** (+ `chartjs-chart-matrix`) - Interactive charts; many dashboard charts are hand-drawn SVG
- **Marked** - Markdown rendering for chat and research
- **Bootstrap Icons** - Icon library

All libraries load from jsDelivr; there is no build step or `npm install`.

## Project Structure

```
web_client/
├── index.html          # Single-page app shell: every page's markup + modals
├── public.html         # Read-only public summary page
├── manifest.json, sw.js, icons/   # PWA manifest, service worker (push), icons
├── css/
│   └── styles.css      # Custom CSS styles (incl. --viz-* colour tokens)
├── js/                 # Loaded by index.html in this order, one global scope
│   ├── help_text.js    # METRIC_HELP / PAGE_HELP tooltip and help-modal text
│   ├── pfm_core.js     # Prefs, Fmt, esc, API client, modal manager, shared chart helpers, notify/confirmDialog
│   ├── pfm_pages.js    # Page manager: dashboard, transactions, assets, help/resources
│   ├── pfm_analytics.js # Analytics, net worth and chart code
│   ├── pfm_features.js # Navigation + auth managers, all other pages, DOMContentLoaded bootstrap
│   └── tests/          # Node built-in test runner (`make test-js`), DOM-free
├── nginx.conf          # Container nginx config: SPA fallback, /api/ proxy, CSP
├── Dockerfile, docker-build.sh, DOCKER.md
└── README.md           # This file
```

## Setup Instructions

### Docker Deployment (Recommended)

The easiest way to run the frontend is using Docker Compose:

```bash
# Start the web frontend (runs on port 80 by default)
docker compose up -d web

# Access the application at:
# http://localhost
```

**Note**: The frontend runs on **port 80** (or the port specified by the `WEB_PORT` environment variable, e.g. `WEB_PORT=8080 docker compose up -d web`). The container's nginx proxies `/api/` to the backend container (`portf_backend_dev:8000` on the `portf_net` network), so the browser only talks to one origin. Web files are baked into the image: rebuild after every edit (`docker compose build web`).

### Development Setup

### 1. Server Setup

Ensure your Portfolio Management API server is running on `http://localhost:8000`. You can start it using:

```bash
# From the project root
python start_server.py
```

### 2. API Key Generation

Generate an API key using the CLI utility:

```bash
# From the project root
python portf_server/api_key_cli.py create "Web Client Key" --description "API key for web client"
```

### 3. Serve the Web Client

The web client needs to be served from a web server due to CORS policies. Without the nginx `/api/` proxy the browser calls the API directly, so set `window.PORTF_API_BASE = 'http://localhost:8000'` (see Configuration) and serve from a port in the API's CORS list (`8080` or `3000`). You can use any of these methods:

#### Option A: Python HTTP Server (Recommended)
```bash
# Navigate to the web_client directory
cd web_client

python -m http.server 8080
```

#### Option B: Node.js HTTP Server
```bash
# Install http-server globally
npm install -g http-server

# Navigate to web_client directory
cd web_client

# Start server
http-server -p 8080
```

#### Option C: PHP Development Server
```bash
# Navigate to web_client directory
cd web_client

# Start PHP server
php -S localhost:8080
```

### 4. Access the Application

Open your web browser and navigate to:
```
http://localhost:8080
```

## Usage

### 1. Login
- The login modal has three tabs: Password, Create account, API key
- Credentials are validated against the API server
- Successfully authenticated sessions are remembered

### 2. Dashboard
- KPI cards (total value, invested, cash, return, open positions), attention strip, risk and progress rows
- Portfolio value history chart, allocation donut, top positions, Wealth Simulator preview
- Bank accounts, spending and budget cards; recent transactions table
- Use the Refresh button (or "Refresh prices") to reload; there is no timed auto-refresh

### 3. Assets Management
- View all assets in a searchable, filterable table
- Add new assets using the "Add Asset" button
- Filter by asset type (stock, bond, ETF, crypto, commodity)
- Search by symbol or name

### 4. Transactions
- View all transactions with filtering options
- Add new transactions using the "Add Transaction" button
- Filter by transaction type and date range
- Automatic total value calculation

## Configuration

### API Server URL

By default the client calls the API on its own origin (`baseURL` is `''` in
`createAPIClient()`, `js/pfm_core.js`), which works behind the nginx `/api/` proxy. When
serving the files some other way, set the base URL before the app scripts load:

```html
<script>window.PORTF_API_BASE = 'http://your-server:8000';</script>
```

### CORS Configuration

The API server's default CORS list (`cors_origins` in `portf_server/settings.py`) allows:
- `http://localhost:3000`
- `http://localhost:8080`

If you're serving the client from a different origin, set `PORTF_CORS_ORIGINS` (a JSON list) or change the default in `portf_server/settings.py`.

## Features in Detail

### Authentication
- Password or API key-based authentication (`X-API-Key` header)
- Key storage in localStorage
- Automatic session validation
- Password visibility toggle

### Dashboard
- Portfolio value summary cards
- SVG and Chart.js charts (shared helpers in `pfm_core.js`)
- Recent transactions table
- Responsive design

### Asset Management
- Create new assets with full details
- Search and filter functionality
- Asset type badges
- Action buttons for future CRUD operations

### Transaction Management
- Add transactions with asset selection
- Automatic total value calculation
- Transaction type badges
- Date range filtering

### UI/UX Features
- Toast notifications for user feedback (`notify()`), confirmation dialogs (`confirmDialog()`)
- Loading spinners and states
- Modal forms with validation
- Keyboard: Ctrl/Cmd+Enter sends a chat message; Escape closes modals (Bootstrap default)
- Dark/light theme, privacy blur, sortable/filterable tables with persisted state
- Responsive tables with mobile-friendly design

## Browser Support

- Chrome 70+
- Firefox 65+
- Safari 12+
- Edge 79+

## Development

### Tests

Pure helpers are `window.`-exported and tested without a DOM by the Node built-in
test runner: `make test-js` (files in `js/tests/*.test.mjs`). Also check new pages at
390px width for horizontal overflow.

### Error Handling

The application includes comprehensive error handling:
- Network connectivity issues
- API authentication failures
- Data validation errors
- User-friendly error messages

### Future Enhancements

- Offline support (the current `sw.js` is a network pass-through used for push notifications)
- Real-time updates with WebSockets

## Troubleshooting

### Common Issues

1. **CORS Errors**
   - Ensure you're serving the web client from a web server
   - Check that your server URL is correct
   - Verify CORS settings in the API server

2. **API Key Authentication Fails**
   - Verify the API key is correct
   - Ensure the API server is running
   - Check the server logs for authentication errors

3. **Charts Not Loading**
   - Check browser console for JavaScript errors
   - Ensure Chart.js is loading correctly
   - Verify chart data format

4. **Mobile Display Issues**
   - Clear browser cache
   - Test in different browsers
   - Check responsive CSS rules

5. **Changes not showing up in Docker**
   - The image bakes the files in; rebuild with `docker compose build web` and restart the `web` service

## License

This web client is part of the Portfolio Management System and follows the same license as the main project.
