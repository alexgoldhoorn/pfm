import unittest
from datetime import date
from decimal import Decimal
from portf_manager.tax_calculator import TaxCalculator, TaxLot, TaxTransaction


class MockDBManager:
    def __init__(self):
        self.transactions = [
            {
                "id": 1,
                "transaction_date": "2023-12-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-01-10",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "110",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-03-15",
                "transaction_type": "sell",
                "quantity": "8",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]

    def get_all_transactions(self, user_id):
        return self.transactions

    def get_asset_by_symbol(self, symbol):
        assets = {
            "AAPL": {"name": "Apple Inc."},
            "GOOG": {"name": "Google Inc."},
            "TSLA": {"name": "Tesla Inc."},
            "MSFT": {"name": "Microsoft Corp."},
        }
        return assets.get(symbol, {"name": symbol})

    def get_portfolio(self, portfolio_id):
        return {"name": "Growth Portfolio"}


class TestTaxCalculator(unittest.TestCase):

    def setUp(self):
        self.db_manager = MockDBManager()
        self.calculator = TaxCalculator(self.db_manager)

    def test_tax_report_2025(self):
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        self.assertIn("AAPL", tax_report)
        self.assertEqual(len(tax_report["AAPL"]), 1)
        tx = tax_report["AAPL"][0]
        self.assertEqual(tx.sell_quantity, Decimal("8"))

    def test_error_if_selling_more_than_bought(self):
        transactions = [
            {
                "id": 1,
                "transaction_date": "2024-01-10",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "110",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-03-15",
                "transaction_type": "sell",
                "quantity": "8",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        with self.assertLogs(level="WARNING") as log:
            self.calculator.calculate_tax_report(
                user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
            )
        self.assertIn("Could not match 3 shares for AAPL sell on", "".join(log.output))

    def test_report_only_sold_stocks(self):
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-12-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-03-15",
                "transaction_type": "sell",
                "quantity": "8",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 5,
                "transaction_date": "2024-02-15",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "120",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        self.assertIn("AAPL", tax_report)
        self.assertNotIn("GOOG", tax_report)

    def test_gain_loss_per_sell(self):
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-12-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-03-15",
                "transaction_type": "sell",
                "quantity": "8",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        tx = tax_report["AAPL"][0]
        self.assertAlmostEqual(tx.gain_loss, Decimal("400"))

    def test_summary(self):
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-12-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-03-15",
                "transaction_type": "sell",
                "quantity": "8",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-01-15",
                "transaction_type": "buy",
                "quantity": "6",
                "price": "110",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        summary = self.calculator.generate_tax_summary(tax_report)
        self.assertEqual(summary["total_gain_loss"], Decimal("400"))
        self.assertEqual(
            summary["symbol_summaries"]["AAPL"]["total_gain_loss"], Decimal("400")
        )

    def test_fifo_ordering(self):
        """Test that FIFO ordering is correctly applied."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2023-06-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "200",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "7",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        # Should have 2 transactions: 5 shares at $100 cost, 2 shares at $200 cost
        transactions_list = tax_report["AAPL"]
        self.assertEqual(len(transactions_list), 2)

        # First transaction should be from first lot (5 shares at $100)
        first_tx = transactions_list[0]
        self.assertEqual(first_tx.sell_quantity, Decimal("5"))
        self.assertEqual(first_tx.purchase_price, Decimal("100"))

        # Second transaction should be from second lot (2 shares at $200)
        second_tx = transactions_list[1]
        self.assertEqual(second_tx.sell_quantity, Decimal("2"))
        self.assertEqual(second_tx.purchase_price, Decimal("200"))

    def test_long_term_vs_short_term(self):
        """Test holding period classification."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-12-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "200",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "12",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        transactions_list = tax_report["AAPL"]
        self.assertEqual(len(transactions_list), 2)

        # First transaction (from 2023) should be long term
        first_tx = transactions_list[0]
        self.assertTrue(first_tx.is_long_term)
        self.assertGreaterEqual(first_tx.holding_period_days, 365)

        # Second transaction (from 2024) should be short term
        second_tx = transactions_list[1]
        self.assertFalse(second_tx.is_long_term)
        self.assertLess(second_tx.holding_period_days, 365)

    def test_multiple_symbols(self):
        """Test handling of multiple symbols."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "200",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "5",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 4,
                "transaction_date": "2025-02-15",
                "transaction_type": "sell",
                "quantity": "3",
                "price": "250",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        # Should have both symbols
        self.assertIn("AAPL", tax_report)
        self.assertIn("GOOG", tax_report)

        # Check AAPL transaction
        aapl_tx = tax_report["AAPL"][0]
        self.assertEqual(aapl_tx.sell_quantity, Decimal("5"))
        self.assertEqual(aapl_tx.gain_loss, Decimal("250"))  # (150 - 100) * 5

        # Check GOOG transaction
        goog_tx = tax_report["GOOG"][0]
        self.assertEqual(goog_tx.sell_quantity, Decimal("3"))
        self.assertEqual(goog_tx.gain_loss, Decimal("150"))  # (250 - 200) * 3

    def test_symbol_filtering(self):
        """Test filtering by specific symbols."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "200",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "5",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 4,
                "transaction_date": "2025-02-15",
                "transaction_type": "sell",
                "quantity": "3",
                "price": "250",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            symbols=["AAPL"],
        )

        # Should only have AAPL
        self.assertIn("AAPL", tax_report)
        self.assertNotIn("GOOG", tax_report)

    def test_sells_outside_date_range_ignored(self):
        """Test that sells outside the date range are ignored."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-12-15",
                "transaction_type": "sell",
                "quantity": "5",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "3",
                "price": "160",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        # Should only have the 2025 sell
        self.assertIn("AAPL", tax_report)
        self.assertEqual(len(tax_report["AAPL"]), 1)
        tx = tax_report["AAPL"][0]
        self.assertEqual(tx.sell_quantity, Decimal("3"))
        self.assertEqual(tx.sell_price, Decimal("160"))

    def test_buys_from_any_date_included(self):
        """Test that buys from any date are included for FIFO calculation."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2020-01-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "50",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-01-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "8",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        # Should have 2 transactions: 5 from 2020 lot, 3 from 2024 lot
        transactions_list = tax_report["AAPL"]
        self.assertEqual(len(transactions_list), 2)

        # First should be from 2020 lot (oldest)
        first_tx = transactions_list[0]
        self.assertEqual(first_tx.sell_quantity, Decimal("5"))
        self.assertEqual(first_tx.purchase_price, Decimal("50"))
        self.assertEqual(first_tx.purchase_date, date(2020, 1, 1))

        # Second should be from 2024 lot
        second_tx = transactions_list[1]
        self.assertEqual(second_tx.sell_quantity, Decimal("3"))
        self.assertEqual(second_tx.purchase_price, Decimal("100"))
        self.assertEqual(second_tx.purchase_date, date(2024, 1, 1))

    def test_report_includes_transactions_without_user_id(self):
        """Live rows have user_id NULL; a user_id filter must not drop them."""
        rows = [
            {
                "id": 1,
                "transaction_date": "2025-01-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2025-06-10",
                "transaction_type": "sell",
                "quantity": "10",
                "price": "150",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
        ]

        # Mimic Database.get_all_transactions: user_id is a real SQL filter,
        # and stored rows carry user_id NULL.
        def get_all_transactions(user_id=None):
            if user_id is None:
                return rows
            return [r for r in rows if r.get("user_id") == user_id]

        self.calculator.db_manager.get_all_transactions = get_all_transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        self.assertIn("EXA", tax_report)
        self.assertEqual(len(tax_report["EXA"]), 1)

    def test_prior_year_sells_consume_lots(self):
        """Sells before the report window must still consume FIFO lots."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2023-06-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "200",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            # 2024 sell (outside window) consumes the first lot entirely.
            {
                "id": 3,
                "transaction_date": "2024-03-01",
                "transaction_type": "sell",
                "quantity": "10",
                "price": "300",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            # 2025 sell must therefore match the SECOND lot (cost 200).
            {
                "id": 4,
                "transaction_date": "2025-03-01",
                "transaction_type": "sell",
                "quantity": "10",
                "price": "400",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        # Only the in-window sell is reported...
        self.assertEqual(len(tax_report["EXA"]), 1)
        tx = tax_report["EXA"][0]
        # ...and it must be matched against the second lot, not the
        # already-sold first one.
        self.assertEqual(tx.purchase_date, date(2023, 6, 10))
        self.assertEqual(tx.purchase_price, Decimal("200"))
        self.assertEqual(tx.gain_loss, Decimal("2000"))

    def test_fees_included_in_cost_basis_and_proceeds(self):
        """Purchase fees increase cost basis; sale fees reduce proceeds (IRPF)."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2025-01-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "fees": "10",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2025-06-10",
                "transaction_type": "sell",
                "quantity": "10",
                "price": "150",
                "fees": "5",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        tx = tax_report["EXA"][0]
        # Prices stay gross; the amount fields carry the fee adjustment.
        self.assertEqual(tx.purchase_price, Decimal("100"))
        self.assertEqual(tx.sell_price, Decimal("150"))
        self.assertEqual(tx.purchase_amount, Decimal("1010"))
        self.assertEqual(tx.sell_amount, Decimal("1495"))
        self.assertEqual(tx.gain_loss, Decimal("485"))

    def test_fees_allocated_proportionally_across_lots(self):
        """A sell spanning lots allocates its fee per share sold."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2025-01-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "fees": "10",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2025-02-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "200",
                "fees": "20",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            # Sell 15 with 15 fees → 1/share: 10 from lot 1, 5 from lot 2.
            {
                "id": 3,
                "transaction_date": "2025-06-10",
                "transaction_type": "sell",
                "quantity": "15",
                "price": "300",
                "fees": "15",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        lot1_tx, lot2_tx = tax_report["EXA"]
        # Lot 1: proceeds 10*300-10, cost 10*100+10 (1/share buy fee).
        self.assertEqual(lot1_tx.sell_amount, Decimal("2990"))
        self.assertEqual(lot1_tx.purchase_amount, Decimal("1010"))
        self.assertEqual(lot1_tx.gain_loss, Decimal("1980"))
        # Lot 2: proceeds 5*300-5, cost 5*200+5*2 (2/share buy fee).
        self.assertEqual(lot2_tx.sell_amount, Decimal("1495"))
        self.assertEqual(lot2_tx.purchase_amount, Decimal("1010"))
        self.assertEqual(lot2_tx.gain_loss, Decimal("485"))

    def test_fees_survive_stock_split(self):
        """Per-share purchase fees scale with a split like the price does."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2025-01-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "fees": "10",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            # 2-for-1 split: 20 shares, cost basis still 1010.
            {
                "id": 2,
                "transaction_date": "2025-02-10",
                "transaction_type": "split",
                "quantity": "2",
                "price": "0",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-06-10",
                "transaction_type": "sell",
                "quantity": "20",
                "price": "100",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        tx = tax_report["EXA"][0]
        self.assertEqual(tx.purchase_amount, Decimal("1010"))
        self.assertEqual(tx.sell_amount, Decimal("2000"))
        self.assertEqual(tx.gain_loss, Decimal("990"))

    def test_empty_report_no_sells(self):
        """Test that empty report is returned when no sells in date range."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-01-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "200",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )

        # Should be empty
        self.assertEqual(len(tax_report), 0)

    def test_tax_lot_dataclass(self):
        """Test TaxLot dataclass functionality."""
        lot = TaxLot(
            purchase_date=date(2023, 1, 1),
            quantity=Decimal("10"),
            price=Decimal("100"),
            remaining_quantity=Decimal("8"),
            transaction_id=1,
        )

        self.assertEqual(lot.cost_basis, Decimal("1000"))
        self.assertEqual(lot.remaining_cost_basis, Decimal("800"))

    def test_tax_transaction_dataclass(self):
        """Test TaxTransaction dataclass functionality."""
        tx = TaxTransaction(
            symbol="AAPL",
            asset_name="Apple Inc.",
            sell_date=date(2025, 1, 15),
            sell_quantity=Decimal("5"),
            sell_price=Decimal("150"),
            sell_amount=Decimal("750"),
            purchase_date=date(2023, 1, 1),
            purchase_price=Decimal("100"),
            purchase_amount=Decimal("500"),
            gain_loss=Decimal("250"),
            holding_period_days=379,
            is_long_term=True,
            sell_transaction_id=3,
            buy_transaction_id=1,
            portfolio_name="Test Portfolio",
        )

        self.assertEqual(tx.gain_loss_percentage, Decimal("50"))

    def test_comprehensive_summary_stats(self):
        """Test comprehensive summary statistics."""
        transactions = [
            {
                "id": 1,
                "transaction_date": "2023-01-01",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2024-06-01",
                "transaction_type": "buy",
                "quantity": "5",
                "price": "200",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
            {
                "id": 3,
                "transaction_date": "2025-01-15",
                "transaction_type": "sell",
                "quantity": "5",
                "price": "150",
                "symbol": "AAPL",
                "portfolio_id": 1,
            },
            {
                "id": 4,
                "transaction_date": "2025-02-15",
                "transaction_type": "sell",
                "quantity": "3",
                "price": "180",
                "symbol": "GOOG",
                "portfolio_id": 1,
            },
        ]
        self.calculator.db_manager.get_all_transactions = lambda user_id: transactions
        tax_report = self.calculator.calculate_tax_report(
            user_id=1, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        summary = self.calculator.generate_tax_summary(tax_report)

        # Total gains: AAPL (150-100)*5 = 250, GOOG (180-200)*3 = -60
        self.assertEqual(summary["total_gain_loss"], Decimal("190"))
        self.assertEqual(
            summary["total_long_term_gain_loss"], Decimal("250")
        )  # Only AAPL is long term
        self.assertEqual(
            summary["total_short_term_gain_loss"], Decimal("-60")
        )  # GOOG is short term
        self.assertEqual(summary["total_transactions"], 2)

        # Symbol summaries
        self.assertEqual(
            summary["symbol_summaries"]["AAPL"]["total_gain_loss"], Decimal("250")
        )
        self.assertEqual(
            summary["symbol_summaries"]["AAPL"]["long_term_gain_loss"], Decimal("250")
        )
        self.assertEqual(
            summary["symbol_summaries"]["AAPL"]["short_term_gain_loss"], Decimal("0")
        )
        self.assertEqual(summary["symbol_summaries"]["AAPL"]["transaction_count"], 1)

        self.assertEqual(
            summary["symbol_summaries"]["GOOG"]["total_gain_loss"], Decimal("-60")
        )
        self.assertEqual(
            summary["symbol_summaries"]["GOOG"]["long_term_gain_loss"], Decimal("0")
        )
        self.assertEqual(
            summary["symbol_summaries"]["GOOG"]["short_term_gain_loss"], Decimal("-60")
        )
        self.assertEqual(summary["symbol_summaries"]["GOOG"]["transaction_count"], 1)
