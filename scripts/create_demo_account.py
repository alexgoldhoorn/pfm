#!/usr/bin/env python3
"""
Demo Account Setup Script for Portfolio Manager

This script creates a demo account with username 'demo' and password 'demo',
along with sample assets, portfolios, and transactions for testing purposes.
"""

import sys
import os
from datetime import datetime, date, timedelta
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent))

from portf_manager.database import Database
from portf_manager.auth import AuthManager
from portf_manager.models import AssetType, TransactionType


def create_demo_account():
    """Create a demo account with sample data."""
    print("🚀 Setting up demo account...")

    # Initialize database and auth manager
    db = Database("portfolio_demo.db")
    auth = AuthManager(db)

    try:
        # Create demo user
        print("👤 Creating demo user...")
        demo_user_id = auth.register_user(
            username="demo",
            email="demo@example.com",
            password="demo",
            full_name="Demo User",
        )
        print(f"✅ Demo user created with ID: {demo_user_id}")

        # Login as demo user
        session = auth.login("demo", "demo")
        print(f"✅ Logged in as: {session.username}")

        # Create sample entities (brokers/platforms)
        print("\n🏢 Creating sample entities...")
        entities = [
            (
                "Interactive Brokers",
                "broker",
                "https://www.interactivebrokers.com",
                "Global online broker",
            ),
            (
                "Fidelity",
                "broker",
                "https://www.fidelity.com",
                "Investment management company",
            ),
            (
                "Charles Schwab",
                "broker",
                "https://www.schwab.com",
                "Financial services company",
            ),
            (
                "TD Ameritrade",
                "broker",
                "https://www.tdameritrade.com",
                "Online broker",
            ),
        ]

        entity_ids = {}
        for name, entity_type, website, description in entities:
            entity_id = db.create_entity(
                name=name,
                entity_type=entity_type,
                user_id=demo_user_id,
                website=website,
                description=description,
            )
            entity_ids[name] = entity_id
            print(f"  ✅ Created entity: {name} (ID: {entity_id})")

        # Create sample portfolios
        print("\n📊 Creating sample portfolios...")
        portfolios = [
            (
                "Main Portfolio",
                "USD",
                "Interactive Brokers",
                "Primary investment portfolio",
            ),
            ("Retirement 401k", "USD", "Fidelity", "401k retirement account"),
            (
                "Tech Growth",
                "USD",
                "Charles Schwab",
                "Technology-focused growth portfolio",
            ),
            (
                "Dividend Income",
                "USD",
                "TD Ameritrade",
                "Dividend-focused income portfolio",
            ),
        ]

        portfolio_ids = {}
        for name, currency, entity_name, description in portfolios:
            portfolio_id = db.create_portfolio(
                name=name,
                base_currency=currency,
                entity_id=entity_ids[entity_name],
                description=description,
                user_id=demo_user_id,
            )
            portfolio_ids[name] = portfolio_id
            print(f"  ✅ Created portfolio: {name} (ID: {portfolio_id})")

        # Create sample assets
        print("\n💰 Creating sample assets...")
        assets = [
            (
                "AAPL",
                "Apple Inc.",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Technology",
            ),
            (
                "GOOGL",
                "Alphabet Inc.",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Technology",
            ),
            (
                "MSFT",
                "Microsoft Corporation",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Technology",
            ),
            (
                "AMZN",
                "Amazon.com Inc.",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Consumer Discretionary",
            ),
            (
                "TSLA",
                "Tesla Inc.",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Consumer Discretionary",
            ),
            (
                "NVDA",
                "NVIDIA Corporation",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Technology",
            ),
            (
                "META",
                "Meta Platforms Inc.",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Technology",
            ),
            (
                "NFLX",
                "Netflix Inc.",
                AssetType.STOCK.value,
                "NASDAQ",
                "USD",
                "Communication Services",
            ),
            (
                "SPY",
                "SPDR S&P 500 ETF Trust",
                AssetType.ETF.value,
                "NYSE",
                "USD",
                "Diversified",
            ),
            (
                "QQQ",
                "Invesco QQQ Trust",
                AssetType.ETF.value,
                "NASDAQ",
                "USD",
                "Technology",
            ),
            (
                "VTI",
                "Vanguard Total Stock Market ETF",
                AssetType.ETF.value,
                "NYSE",
                "USD",
                "Diversified",
            ),
            (
                "BTC-USD",
                "Bitcoin",
                AssetType.CRYPTO.value,
                "Crypto",
                "USD",
                "Cryptocurrency",
            ),
            (
                "ETH-USD",
                "Ethereum",
                AssetType.CRYPTO.value,
                "Crypto",
                "USD",
                "Cryptocurrency",
            ),
            (
                "JNJ",
                "Johnson & Johnson",
                AssetType.STOCK.value,
                "NYSE",
                "USD",
                "Healthcare",
            ),
            (
                "PG",
                "Procter & Gamble",
                AssetType.STOCK.value,
                "NYSE",
                "USD",
                "Consumer Staples",
            ),
        ]

        asset_ids = {}
        for symbol, name, asset_type, exchange, currency, sector in assets:
            asset_id = db.create_asset(
                symbol=symbol,
                name=name,
                asset_type=asset_type,
                exchange=exchange,
                currency=currency,
                sector=sector,
                description=f"{name} - {sector} sector",
            )
            asset_ids[symbol] = asset_id
            print(f"  ✅ Created asset: {symbol} - {name} (ID: {asset_id})")

        # Create sample transactions
        print("\n📈 Creating sample transactions...")

        # Helper function to create transactions
        def create_transaction(
            symbol,
            portfolio_name,
            transaction_type,
            quantity,
            price,
            days_ago,
            description=None,
        ):
            transaction_date = (date.today() - timedelta(days=days_ago)).strftime(
                "%Y-%m-%d"
            )
            total_amount = quantity * price

            return db.create_transaction(
                asset_id=asset_ids[symbol],
                portfolio_id=portfolio_ids[portfolio_name],
                transaction_type=transaction_type,
                quantity=quantity,
                price=price,
                total_amount=total_amount,
                transaction_date=transaction_date,
                fees=0.0,
                description=description,
                user_id=demo_user_id,
            )

        # Sample transactions for different portfolios
        transactions = [
            # Main Portfolio - Mixed investments
            (
                "AAPL",
                "Main Portfolio",
                TransactionType.BUY.value,
                10,
                180.50,
                90,
                "Initial Apple purchase",
            ),
            (
                "AAPL",
                "Main Portfolio",
                TransactionType.BUY.value,
                5,
                185.75,
                60,
                "Additional Apple shares",
            ),
            (
                "GOOGL",
                "Main Portfolio",
                TransactionType.BUY.value,
                3,
                2450.00,
                85,
                "Google investment",
            ),
            (
                "MSFT",
                "Main Portfolio",
                TransactionType.BUY.value,
                8,
                325.80,
                75,
                "Microsoft purchase",
            ),
            (
                "AMZN",
                "Main Portfolio",
                TransactionType.BUY.value,
                2,
                3200.00,
                70,
                "Amazon investment",
            ),
            (
                "SPY",
                "Main Portfolio",
                TransactionType.BUY.value,
                25,
                420.00,
                65,
                "S&P 500 ETF",
            ),
            (
                "AAPL",
                "Main Portfolio",
                TransactionType.DIVIDEND.value,
                15,
                0.85,
                30,
                "Apple quarterly dividend",
            ),
            (
                "MSFT",
                "Main Portfolio",
                TransactionType.DIVIDEND.value,
                8,
                0.62,
                25,
                "Microsoft quarterly dividend",
            ),
            # Tech Growth Portfolio
            (
                "NVDA",
                "Tech Growth",
                TransactionType.BUY.value,
                5,
                450.00,
                80,
                "NVIDIA growth play",
            ),
            (
                "TSLA",
                "Tech Growth",
                TransactionType.BUY.value,
                4,
                850.00,
                75,
                "Tesla investment",
            ),
            (
                "META",
                "Tech Growth",
                TransactionType.BUY.value,
                6,
                280.00,
                70,
                "Meta investment",
            ),
            (
                "NFLX",
                "Tech Growth",
                TransactionType.BUY.value,
                3,
                390.00,
                65,
                "Netflix streaming play",
            ),
            (
                "QQQ",
                "Tech Growth",
                TransactionType.BUY.value,
                15,
                350.00,
                60,
                "NASDAQ-100 ETF",
            ),
            (
                "TSLA",
                "Tech Growth",
                TransactionType.SELL.value,
                1,
                900.00,
                20,
                "Partial Tesla sale",
            ),
            # Retirement 401k
            (
                "VTI",
                "Retirement 401k",
                TransactionType.BUY.value,
                50,
                220.00,
                120,
                "Total market fund",
            ),
            (
                "VTI",
                "Retirement 401k",
                TransactionType.BUY.value,
                25,
                225.00,
                90,
                "Monthly contribution",
            ),
            (
                "VTI",
                "Retirement 401k",
                TransactionType.BUY.value,
                25,
                230.00,
                60,
                "Monthly contribution",
            ),
            (
                "VTI",
                "Retirement 401k",
                TransactionType.BUY.value,
                25,
                235.00,
                30,
                "Monthly contribution",
            ),
            (
                "SPY",
                "Retirement 401k",
                TransactionType.BUY.value,
                30,
                415.00,
                100,
                "S&P 500 allocation",
            ),
            # Dividend Income Portfolio
            (
                "JNJ",
                "Dividend Income",
                TransactionType.BUY.value,
                12,
                165.00,
                95,
                "Johnson & Johnson",
            ),
            (
                "PG",
                "Dividend Income",
                TransactionType.BUY.value,
                10,
                145.00,
                90,
                "Procter & Gamble",
            ),
            (
                "AAPL",
                "Dividend Income",
                TransactionType.BUY.value,
                8,
                175.00,
                85,
                "Apple for dividends",
            ),
            (
                "MSFT",
                "Dividend Income",
                TransactionType.BUY.value,
                6,
                320.00,
                80,
                "Microsoft dividends",
            ),
            (
                "JNJ",
                "Dividend Income",
                TransactionType.DIVIDEND.value,
                12,
                1.06,
                35,
                "J&J quarterly dividend",
            ),
            (
                "PG",
                "Dividend Income",
                TransactionType.DIVIDEND.value,
                10,
                0.87,
                32,
                "P&G quarterly dividend",
            ),
            (
                "AAPL",
                "Dividend Income",
                TransactionType.DIVIDEND.value,
                8,
                0.85,
                30,
                "Apple quarterly dividend",
            ),
            (
                "MSFT",
                "Dividend Income",
                TransactionType.DIVIDEND.value,
                6,
                0.62,
                28,
                "Microsoft quarterly dividend",
            ),
            # Crypto investments
            (
                "BTC-USD",
                "Main Portfolio",
                TransactionType.BUY.value,
                0.5,
                45000.00,
                50,
                "Bitcoin investment",
            ),
            (
                "ETH-USD",
                "Main Portfolio",
                TransactionType.BUY.value,
                2.0,
                3200.00,
                45,
                "Ethereum investment",
            ),
            (
                "BTC-USD",
                "Main Portfolio",
                TransactionType.BUY.value,
                0.25,
                48000.00,
                25,
                "Bitcoin DCA",
            ),
            (
                "ETH-USD",
                "Main Portfolio",
                TransactionType.BUY.value,
                1.0,
                3400.00,
                20,
                "Ethereum DCA",
            ),
        ]

        transaction_count = 0
        for (
            symbol,
            portfolio_name,
            transaction_type,
            quantity,
            price,
            days_ago,
            description,
        ) in transactions:
            transaction_id = create_transaction(
                symbol,
                portfolio_name,
                transaction_type,
                quantity,
                price,
                days_ago,
                description,
            )
            transaction_count += 1
            if transaction_count % 10 == 0:
                print(f"  ✅ Created {transaction_count} transactions...")

        print(f"  ✅ Created {transaction_count} total transactions")

        # Summary
        print("\n📊 Demo Account Summary:")
        print("=" * 50)
        print(f"Username: demo")
        print(f"Password: demo")
        print(f"Database: portfolio_demo.db")
        print(f"User ID: {demo_user_id}")
        print(f"Entities: {len(entities)}")
        print(f"Portfolios: {len(portfolios)}")
        print(f"Assets: {len(assets)}")
        print(f"Transactions: {transaction_count}")
        print("=" * 50)

        print("\n🎉 Demo account setup complete!")
        print("\nTo use the demo account:")
        print("1. Run: python -m portf_manager.cli")
        print("2. Use database: portfolio_demo.db")
        print("3. Login with: demo/demo")
        print("\nExample commands to try:")
        print("- list-assets")
        print("- list-transactions")
        print("- list-portfolios")
        print("- show-portfolio-value")
        print("- filter-transactions --symbol AAPL")
        print("- export-csv --output demo_transactions.csv")

    except Exception as e:
        print(f"❌ Error setting up demo account: {e}")
        return False

    return True


if __name__ == "__main__":
    success = create_demo_account()
    sys.exit(0 if success else 1)
