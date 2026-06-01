#!/usr/bin/env python3

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from portf_manager.config import PortfolioConfig
from portf_manager.cli import PortfolioManagerCLI, print_interactive_help


def test_console():
    print("🚀 Portfolio Manager Test Console")
    print("Testing basic functionality...")

    # Test CLI initialization
    config = PortfolioConfig(db_path="test_portfolio.db")
    cli = PortfolioManagerCLI(config)
    print("✅ CLI initialized successfully")

    # Test help function
    print("\n📚 Testing help function:")
    print_interactive_help()
    print("✅ Help function works")

    # Test paste function (without actual LLM call)
    print("\n🔍 Testing paste function availability:")
    if hasattr(cli, "paste_transaction_interactive"):
        print("✅ paste_transaction_interactive method exists")
    else:
        print("❌ paste_transaction_interactive method missing")

    # Test basic commands
    print("\n📋 Testing basic commands:")
    try:
        cli.list_assets()
        print("✅ list_assets works")
    except Exception as e:
        print(f"❌ list_assets failed: {e}")

    try:
        cli.list_portfolios()
        print("✅ list_portfolios works")
    except Exception as e:
        print(f"❌ list_portfolios failed: {e}")

    try:
        cli.list_transactions()
        print("✅ list_transactions works")
    except Exception as e:
        print(f"❌ list_transactions failed: {e}")


if __name__ == "__main__":
    test_console()
