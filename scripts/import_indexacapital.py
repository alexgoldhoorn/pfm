#!/usr/bin/env python3
"""
IndexaCapital CSV Importer

This script allows you to import IndexaCapital CSV transaction data
directly into your portfolio database without the complexity of the CLI.

Usage:
    python import_indexacapital.py [csv_file]

If no file is provided, you can paste the CSV content directly.
"""

import sys
import os
from pathlib import Path

# Add the current directory to path so we can import our modules
sys.path.append(str(Path(__file__).parent))

from portf_manager.parsers.indexacapital_csv_parser import parse_indexacapital_csv
from portf_manager.database import Database
from portf_manager.models import Asset, Transaction
import sqlite3
from datetime import datetime


def import_from_csv_content(csv_content: str, db_path: str = "portfolio.db") -> None:
    """Import transactions from CSV content."""

    print("🔄 Parsing IndexaCapital CSV...")
    result = parse_indexacapital_csv(csv_content)

    if not result.importable:
        print("❌ No importable transactions found.")
        if result.skipped:
            print("📋 Skipped entries:")
            for tx_type, reason in result.skipped:
                print(f"   • {tx_type}: {reason}")
        return

    print(f"📊 Processing Summary:")
    print(f"   🔥 Importable: {len(result.importable)} transactions")
    print(f"   📋 Skipped: {len(result.skipped)} entries")

    if result.skipped:
        print("\n📋 Skipped entries:")
        for tx_type, reason in result.skipped:
            print(f"   • {tx_type}: {reason}")

    print(f"\n✅ Found {len(result.importable)} importable transactions:")

    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    imported_count = 0
    for i, tx in enumerate(result.importable, 1):
        print(f"\n📋 Transaction {i}:")
        print(f"   Symbol: {tx.symbol}")
        print(f"   Name: {tx.asset_name}")
        print(f"   Type: {tx.tx_type}")
        print(f"   Quantity: {tx.quantity}")
        print(f"   Price: {tx.price:.4f} EUR")
        print(f"   Total: {tx.quantity * tx.price:.2f} EUR")
        print(f"   Date: {tx.date}")

        # Ask for confirmation
        while True:
            response = (
                input(f"\n❓ Import this transaction? [y/N/s=skip all]: ")
                .strip()
                .lower()
            )
            if response in ["y", "yes"]:
                try:
                    # Check if asset exists, if not create it
                    cursor.execute(
                        "SELECT id FROM assets WHERE symbol = ?", (tx.symbol,)
                    )
                    asset_row = cursor.fetchone()

                    if not asset_row:
                        print(f"📝 Creating new asset: {tx.symbol}")
                        cursor.execute(
                            """
                            INSERT INTO assets (symbol, name, asset_type, exchange, currency) 
                            VALUES (?, ?, ?, ?, ?)
                        """,
                            (tx.symbol, tx.asset_name, "etf", "UNKNOWN", "EUR"),
                        )
                        asset_id = cursor.lastrowid
                    else:
                        asset_id = asset_row[0]

                    # Insert transaction
                    cursor.execute(
                        """
                        INSERT INTO transactions 
                        (asset_id, portfolio_id, user_id, transaction_type, quantity, price, 
                         total_amount, fees, transaction_date, description, created_at, updated_at)
                        VALUES (?, 1, 1, ?, ?, ?, ?, 0.0, ?, ?, ?, ?)
                    """,
                        (
                            asset_id,
                            tx.tx_type,
                            tx.quantity,
                            tx.price,
                            tx.quantity * tx.price,
                            tx.date,
                            f"Imported from IndexaCapital: {tx.raw_text[:100]}...",
                            datetime.now(),
                            datetime.now(),
                        ),
                    )

                    conn.commit()
                    imported_count += 1
                    print("✅ Transaction imported successfully!")
                    break

                except Exception as e:
                    print(f"❌ Failed to import transaction: {e}")
                    break

            elif response in ["n", "no", ""]:
                print("⏭️  Skipped.")
                break

            elif response in ["s", "skip"]:
                print("⏭️  Skipping all remaining transactions.")
                conn.close()
                print(
                    f"\n🎉 Import complete! Imported {imported_count}/{len(result.importable)} transactions."
                )
                return

            else:
                print("❓ Please enter y, n, or s")

    conn.close()
    print(
        f"\n🎉 Import complete! Imported {imported_count}/{len(result.importable)} transactions."
    )


def main():
    """Main function."""
    print("🏦 IndexaCapital CSV Importer")
    print("=" * 50)

    # Check if CSV file was provided as argument
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
        if not os.path.exists(csv_file):
            print(f"❌ File not found: {csv_file}")
            sys.exit(1)

        print(f"📁 Reading from file: {csv_file}")
        with open(csv_file, "r", encoding="utf-8") as f:
            csv_content = f.read()
    else:
        # Interactive mode - paste CSV content
        print("📋 Paste your IndexaCapital CSV content below.")
        print("💡 Format: semicolon-separated with columns like:")
        print(
            '   DD/MM/YYYY;YYYY-MM-DD;"Asset Name";ISIN;SUSCRIPCIÓN;quantity;"amount €";"fees €";"more fees €"'
        )
        print("\n🔚 Type 'END' on a new line when finished:")
        print()

        lines = []
        while True:
            try:
                line = input()
                if line.strip().upper() == "END":
                    break
                lines.append(line)
            except KeyboardInterrupt:
                print("\n\n❌ Import cancelled.")
                sys.exit(0)

        csv_content = "\n".join(lines)

    if not csv_content.strip():
        print("❌ No CSV content provided.")
        sys.exit(1)

    # Import the transactions
    try:
        import_from_csv_content(csv_content)
    except Exception as e:
        print(f"❌ Error during import: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
