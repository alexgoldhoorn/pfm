# New API Endpoints: LLM Transaction Parsing & Tax Reporting

This document describes the newly implemented LLM and Tax endpoints in the Portfolio Management API.

## LLM Transaction Parsing Endpoint

### POST `/api/v1/llm/parse`

Parses raw broker statement text into structured transaction objects using AI.

**Request Body:**
```json
{
  "text": "AAPL BUY 100 shares at $150.00 on 2024-01-15\nGOOGL SELL 50 shares at $2800.00 on 2024-01-20"
}
```

**Response:**
```json
{
  "transactions": [
    {
      "tx_type": "buy",
      "symbol": "AAPL",
      "asset_name": "Apple Inc.",
      "quantity": 100.0,
      "price": 150.00,
      "date": "2024-01-15",
      "currency": "USD",
      "raw_text": "AAPL BUY 100 shares at $150.00 on 2024-01-15"
    },
    {
      "tx_type": "sell", 
      "symbol": "GOOGL",
      "asset_name": "Alphabet Inc.",
      "quantity": 50.0,
      "price": 2800.00,
      "date": "2024-01-20",
      "currency": "USD",
      "raw_text": "GOOGL SELL 50 shares at $2800.00 on 2024-01-20"
    }
  ],
  "count": 2
}
```

**Features:**
- Uses Google Gemini AI for intelligent parsing
- Validates parsed transactions for completeness
- Maintains raw text for traceability
- Handles multiple transaction formats
- Skips invalid/incomplete transactions

## Tax Report Generation Endpoint

### GET `/api/v1/tax/report`

Generates comprehensive tax reports in CSV or PDF format using FIFO capital gains methodology.

**Query Parameters:**
- `start_date` (required): Start date for tax report (YYYY-MM-DD)
- `end_date` (required): End date for tax report (YYYY-MM-DD)  
- `symbols` (optional): Comma-separated list of symbols to filter by
- `format` (optional): Output format - "csv" or "pdf" (default: "csv")

**Example Request:**
```
GET /api/v1/tax/report?start_date=2024-01-01&end_date=2024-12-31&symbols=AAPL,GOOGL&format=csv
```

**Response:**
- **CSV Format**: Returns CSV file with detailed tax transactions and summary
- **PDF Format**: Returns formatted PDF report with tables and summaries

**CSV Content Includes:**
- Individual tax transactions with buy/sell details
- Capital gains/losses calculations
- Holding period classification (short-term vs long-term)
- Summary totals by symbol and overall
- FIFO cost basis methodology

**PDF Content Includes:**
- Professional formatted report with title
- Summary table with key metrics
- Detailed transaction table with color coding
- Proper formatting for tax filing purposes

**Features:**
- FIFO (First In First Out) cost basis calculation
- Automatic long-term vs short-term classification
- Symbol filtering capability
- Date range filtering
- Comprehensive summary statistics
- Professional PDF formatting with ReportLab
- CSV export for spreadsheet analysis

## Implementation Details

### Authentication
Both endpoints require user authentication via Bearer token or API key.

### Error Handling
- **400 Bad Request**: Invalid parameters or date ranges
- **401 Unauthorized**: Missing or invalid authentication
- **404 Not Found**: No transactions found for criteria
- **500 Internal Server Error**: Processing failures
- **501 Not Implemented**: PDF generation when ReportLab not available

### Dependencies Added
- `reportlab`: For PDF generation functionality

### Technical Notes
- LLM parsing uses existing GeminiClient and LLMTransaction types
- Tax calculations use existing TaxCalculator and TaxReportExporter
- Both endpoints leverage existing database and authentication infrastructure
- PDF generation gracefully falls back with informative error messages

## Usage Examples

### Parse Transaction Text
```bash
curl -X POST "http://localhost:8000/api/v1/llm/parse" \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"text": "AAPL BUY 100 shares at $150.00 on 2024-01-15"}'
```

### Generate Tax Report (CSV)
```bash
curl -X GET "http://localhost:8000/api/v1/tax/report?start_date=2024-01-01&end_date=2024-12-31&format=csv" \
  -H "Authorization: Bearer your-token" \
  -o tax_report.csv
```

### Generate Tax Report (PDF)
```bash  
curl -X GET "http://localhost:8000/api/v1/tax/report?start_date=2024-01-01&end_date=2024-12-31&format=pdf" \
  -H "Authorization: Bearer your-token" \
  -o tax_report.pdf
```

## Testing

Run the included test script to verify endpoint functionality:

```bash
python test_new_endpoints.py
```

This validates model definitions, router setup, and endpoint availability.
