# Google Sheets Export Feature

This document describes the Google Sheets export functionality for the Portfolio Manager, which allows you to export all your portfolio data to Google Sheets with three organized sheets.

## Overview

The Google Sheets export creates a comprehensive export with three sheets:
1. **All Transactions** - Complete transaction history
2. **Tax Report** - Capital gains/losses calculations  
3. **Portfolio Summary** - Overview statistics

## Prerequisites

### 1. Python Requirements
- Python 3.12+ (or 3.9+ depending on your setup)
- Google API libraries (automatically installed from requirements.txt)

### 2. Google Cloud Setup

#### Option A: Service Account (Recommended)
1. **Create a Google Cloud Project**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one

2. **Enable Google Sheets API**
   - Navigate to "APIs & Services" > "Library"
   - Search for "Google Sheets API"
   - Click "Enable"

3. **Create Service Account**
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "Service Account"
   - Fill in the details and click "Create"

4. **Generate Service Account Key**
   - Click on your newly created service account
   - Go to "Keys" tab
   - Click "Add Key" > "Create New Key"
   - Choose "JSON" format and download the file

5. **Set Required Permissions**
   - The service account needs access to create and edit Google Sheets
   - If using an existing spreadsheet, share it with the service account email

#### Option B: OAuth2 (Not Yet Implemented)
OAuth2 user authentication is planned for future releases.

## Configuration

### Environment Variables

Add these variables to your `.env` file:

```bash
# Required: Path to your service account JSON file
GOOGLE_SERVICE_ACCOUNT_FILE=path/to/your/service-account-key.json

# Optional: Specific spreadsheet ID to update
GOOGLE_SPREADSHEET_ID=your_spreadsheet_id_here
```

### Security Notes
- **Never commit** your service account JSON file to version control
- The `.gitignore` should exclude `*.json` files in the project root
- Store the service account file in a secure location outside the project directory

## Usage

### Basic Export
```bash
# Export to a new Google Spreadsheet
portf export-to-sheets

# Export to a specific existing spreadsheet
portf export-to-sheets --spreadsheet-id 1ABC...XYZ

# Always create a new spreadsheet (ignores any configured spreadsheet ID)
portf export-to-sheets --create-new
```

### Command Options

| Option | Description |
|--------|-------------|
| `--spreadsheet-id ID` | Update an existing spreadsheet by ID |
| `--create-new` | Always create a new spreadsheet |

### Finding Spreadsheet ID
The spreadsheet ID can be found in the Google Sheets URL:
```
https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/
```

## Export Structure

### Sheet 1: All Transactions
Contains complete transaction history with columns:
- Transaction ID, Date, Symbol, Asset Name
- Transaction Type, Quantity, Price, Total Amount
- Currency, Portfolio Name, Description

### Sheet 2: Tax Report  
Contains capital gains/losses calculations with columns:
- Symbol, Asset Name, Sell Date, Sell Quantity, Sell Price
- Sell Amount, Purchase Date, Purchase Price, Purchase Amount
- Gain/Loss, Gain/Loss %, Holding Period, Term Type
- Portfolio, Description

### Sheet 3: Portfolio Summary
Contains overview statistics:
- Total Assets, Total Transactions
- Database Version, Asset Types Breakdown
- Report Generation Timestamp

## Examples

### Example 1: First Time Setup
```bash
# 1. Set up environment variable
export GOOGLE_SERVICE_ACCOUNT_FILE="/path/to/service-account.json"

# 2. Create initial export
portf login  # If not already logged in
portf export-to-sheets

# Output:
# 📊 Created new spreadsheet: Portfolio Export 2024-01-15 10:30
# 🔗 URL: https://docs.google.com/spreadsheets/d/1ABC...XYZ/
# 📈 Exporting transactions...
# 💰 Exporting tax report...
# 📋 Exporting portfolio summary...
# ✅ Export completed successfully!
```

### Example 2: Update Existing Spreadsheet
```bash
# Set the spreadsheet ID in .env file or use command line
portf export-to-sheets --spreadsheet-id 1ABC...XYZ

# Output:
# 📊 Exporting to spreadsheet: 1ABC...XYZ
# 📈 Exporting transactions...
# 💰 Exporting tax report...
# 📋 Exporting portfolio summary...  
# ✅ Export completed successfully!
# 🔗 View at: https://docs.google.com/spreadsheets/d/1ABC...XYZ/
```

## Troubleshooting

### Common Issues

#### 1. Authentication Errors
```
❌ Google Sheets export error: Service account file not found
```
**Solution**: Check that `GOOGLE_SERVICE_ACCOUNT_FILE` points to a valid JSON file.

#### 2. Permission Errors  
```
❌ Google Sheets export error: Cannot access spreadsheet
```
**Solutions**:
- Verify the spreadsheet ID is correct
- Share the spreadsheet with your service account email
- Ensure the service account has edit permissions

#### 3. API Not Enabled
```
❌ Google Sheets export error: Google Sheets API has not been used
```
**Solution**: Enable the Google Sheets API in Google Cloud Console.

#### 4. Missing Dependencies
```
Google API libraries not installed
```
**Solution**: Install dependencies:
```bash
pip install google-api-python-client google-auth google-auth-httplib2 google-auth-oauthlib
```

### Debug Mode
Run with debug flag for more detailed error information:
```bash
portf --debug export-to-sheets
```

## Data Privacy & Security

- **Service Account**: Provides secure, automated access without user intervention
- **Scopes**: Only requests Google Sheets access (no other Google services)
- **Data Transfer**: All data transmitted over HTTPS
- **Local Storage**: No Google credentials cached locally

## Integration with Existing Features

The Google Sheets export integrates seamlessly with existing Portfolio Manager features:

- **Authentication**: Uses the same login system
- **Transaction Filtering**: Exports all user transactions based on current auth context  
- **CSV Export**: Reuses existing CSV export logic for data consistency
- **Tax Calculations**: Uses the same tax calculation engine
- **Database Abstraction**: Works with both SQLite and PostgreSQL backends

## Limitations

- **Sheet Size**: Google Sheets has a limit of 10 million cells per spreadsheet
- **Rate Limits**: Google API has rate limits (100 requests per 100 seconds per user)
- **OAuth2**: Not yet implemented (service account only)
- **Formatting**: Exports raw data without advanced formatting

## Future Enhancements

- OAuth2 user authentication support
- Advanced sheet formatting (colors, fonts, charts)
- Incremental updates (only new/changed data)
- Multiple export formats within sheets
- Automated scheduled exports
- Email notifications on export completion

## Support

If you encounter issues:
1. Check this documentation
2. Verify your Google Cloud setup  
3. Test with a minimal example
4. Check the project's issue tracker
5. Ensure all dependencies are installed correctly
