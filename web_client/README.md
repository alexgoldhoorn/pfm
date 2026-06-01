# Portfolio Management Web Client

A responsive web client for the Portfolio Management API, built with vanilla JavaScript, Bootstrap 5, and Chart.js.

## Features

- **API Key Authentication**: Secure login using API keys
- **Responsive Dashboard**: Portfolio overview with key metrics and interactive charts
- **Asset Management**: View, add, and manage investment assets
- **Transaction Tracking**: Record and view investment transactions
- **Real-time Data**: Automatic data refresh and live updates
- **Mobile Responsive**: Optimized for desktop, tablet, and mobile devices

## Technology Stack

- **HTML5** - Semantic markup
- **CSS3** - Custom styles with CSS Grid and Flexbox
- **JavaScript (ES6+)** - Vanilla JavaScript with modern features
- **Bootstrap 5** - UI components and responsive layout
- **Chart.js** - Interactive portfolio value charts
- **Bootstrap Icons** - Icon library

## Project Structure

```
web_client/
├── index.html          # Main HTML file
├── css/
│   └── styles.css      # Custom CSS styles
├── js/
│   ├── api.js          # API client and HTTP requests
│   ├── auth.js         # Authentication and user management
│   ├── dashboard.js    # Dashboard page functionality
│   ├── assets.js       # Assets page functionality
│   ├── transactions.js # Transactions page functionality
│   └── app.js          # Main application controller
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

**Note**: The frontend now runs on **port 80** (or the port specified by the `WEB_PORT` environment variable). Use `docker compose up -d web` to start the frontend service.

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

The web client needs to be served from a web server due to CORS policies. You can use any of these methods:

#### Option A: Python HTTP Server (Recommended)
```bash
# Navigate to the web_client directory
cd web_client

# Python 3
python -m http.server 8080

# Python 2
python -m SimpleHTTPServer 8080
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
- Enter your API key in the login modal
- The key will be validated against the API server
- Successfully authenticated sessions are remembered

### 2. Dashboard
- View portfolio summary cards (total value, gain/loss, asset count, etc.)
- Interactive chart showing portfolio value over time
- Recent transactions table
- Auto-refresh every 5 minutes

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

If your API server is running on a different host or port, update the `baseURL` in `js/api.js`:

```javascript
// In js/api.js, line ~9
this.baseURL = 'http://your-server:port';
```

### CORS Configuration

The API server is configured to allow requests from:
- `http://localhost:3000`
- `http://localhost:8080`

If you're serving the client from a different port, update the CORS settings in `portf_server/app.py`.

## Features in Detail

### Authentication
- API key-based authentication
- Secure key storage in localStorage
- Automatic session validation
- Password visibility toggle

### Dashboard
- Portfolio value summary cards
- Interactive Chart.js line chart
- Recent transactions table
- Auto-refresh functionality
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
- Toast notifications for user feedback
- Loading spinners and states
- Modal forms with validation
- Keyboard shortcuts (Ctrl+R to refresh, Escape to close modals)
- Responsive tables with mobile-friendly design

## Browser Support

- Chrome 70+
- Firefox 65+
- Safari 12+
- Edge 79+

## Development

### Mock Data

The application includes mock data for development and demonstration:
- Sample transactions
- Portfolio value history
- Asset price data

Real data will be used when the API endpoints are fully implemented.

### Error Handling

The application includes comprehensive error handling:
- Network connectivity issues
- API authentication failures
- Data validation errors
- User-friendly error messages

### Future Enhancements

- Offline support with service workers
- Real-time updates with WebSockets
- Advanced charting options
- Export functionality
- User preferences and settings

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

### Debug Mode

Enable debug logging in the browser console:

```javascript
// In browser console
localStorage.setItem('debug', 'true');
location.reload();
```

## License

This web client is part of the Portfolio Management System and follows the same license as the main project.
