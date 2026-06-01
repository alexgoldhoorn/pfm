#!/usr/bin/env python3
"""
Test setup script for the Portfolio Management Web Client
Creates sample data and starts a local web server
"""

import os
import sys
import subprocess
import requests
import json
import time
import threading

# Add the parent directory to the path to import portf modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def create_sample_data():
    """Create sample assets using the API"""
    api_base = "http://localhost:8000"

    # First, let's check if the API server is running
    try:
        response = requests.get(f"{api_base}/health")
        if response.status_code != 200:
            print("❌ API server is not running. Please start it first:")
            print("   python start_server.py")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ API server is not running. Please start it first:")
        print("   python start_server.py")
        return False

    # Generate an API key
    print("🔑 Generating API key...")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "../portf_server/api_key_cli.py",
                "create",
                "Web Client Test Key",
                "--description",
                "Test key for web client development",
            ],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(__file__),
        )

        if result.returncode != 0:
            print(f"❌ Failed to create API key: {result.stderr}")
            return False

        # Extract API key from output
        output = result.stdout
        api_key = None
        for line in output.split("\n"):
            if "API Key:" in line:
                api_key = line.split("API Key:")[1].strip()
                break

        if not api_key:
            print("❌ Could not extract API key from output")
            return False

        print(f"✅ API Key created: {api_key[:8]}...")

    except Exception as e:
        print(f"❌ Error creating API key: {e}")
        return False

    # Create sample assets
    print("📊 Creating sample assets...")

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    sample_assets = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_type": "stock",
            "exchange": "NASDAQ",
            "currency": "USD",
            "sector": "Technology",
            "description": "Technology company that designs and manufactures consumer electronics",
        },
        {
            "symbol": "GOOGL",
            "name": "Alphabet Inc.",
            "asset_type": "stock",
            "exchange": "NASDAQ",
            "currency": "USD",
            "sector": "Technology",
            "description": "Multinational technology company focusing on search engine technology",
        },
        {
            "symbol": "TSLA",
            "name": "Tesla, Inc.",
            "asset_type": "stock",
            "exchange": "NASDAQ",
            "currency": "USD",
            "sector": "Automotive",
            "description": "Electric vehicle and clean energy company",
        },
        {
            "symbol": "SPY",
            "name": "SPDR S&P 500 ETF Trust",
            "asset_type": "etf",
            "exchange": "NYSE",
            "currency": "USD",
            "sector": "Financial",
            "description": "Exchange-traded fund that tracks the S&P 500 index",
        },
        {
            "symbol": "BTC-USD",
            "name": "Bitcoin",
            "asset_type": "crypto",
            "exchange": "Various",
            "currency": "USD",
            "sector": "Cryptocurrency",
            "description": "Leading cryptocurrency and digital payment system",
        },
    ]

    created_assets = 0
    for asset in sample_assets:
        try:
            response = requests.post(
                f"{api_base}/api/v1/assets", headers=headers, json=asset
            )
            if response.status_code == 201:
                created_assets += 1
                print(f"  ✅ Created: {asset['symbol']}")
            elif response.status_code == 409:
                print(f"  ⚠️  Already exists: {asset['symbol']}")
            else:
                print(f"  ❌ Failed to create {asset['symbol']}: {response.text}")
        except Exception as e:
            print(f"  ❌ Error creating {asset['symbol']}: {e}")

    print(f"📊 Sample data setup complete! Created {created_assets} new assets.")
    print(f"\n🔑 Your API Key: {api_key}")
    print("   Save this key - you'll need it to login to the web client!")

    return True


def start_web_server():
    """Start a simple HTTP server for the web client"""
    print("🌐 Starting web server on http://localhost:8080...")
    print("   Press Ctrl+C to stop the server")

    try:
        # Try Python 3 http.server first
        subprocess.run(
            [sys.executable, "-m", "http.server", "8080"], cwd=os.path.dirname(__file__)
        )
    except KeyboardInterrupt:
        print("\n👋 Web server stopped.")
    except Exception as e:
        print(f"❌ Error starting web server: {e}")
        print("You can manually start the web server using:")
        print("   cd web_client")
        print("   python -m http.server 8080")


def main():
    print("🚀 Portfolio Management Web Client Test Setup")
    print("=" * 50)

    # Check if we're in the right directory
    if not os.path.exists("index.html"):
        print("❌ Please run this script from the web_client directory:")
        print("   cd web_client")
        print("   python test_setup.py")
        return

    print("Setting up test data and starting web server...")
    print()

    # Create sample data
    if create_sample_data():
        print()
        print("🎉 Setup complete! Now starting the web server...")
        print()
        print("📖 Instructions:")
        print("   1. Open your browser to: http://localhost:8080")
        print("   2. Use the API key shown above to login")
        print("   3. Explore the dashboard, assets, and transactions!")
        print()

        # Start web server
        start_web_server()
    else:
        print("❌ Setup failed. Please check the errors above and try again.")


if __name__ == "__main__":
    main()
