"""
Tests for the Gemini client and LLM integration.
"""

import pytest
import os
from unittest.mock import patch, MagicMock
import json

from portf_manager.gemini_client import GeminiClient
from portf_manager.llm_client import reset_llm_client
from portf_manager.llm_types import LLMTransaction


class TestGeminiClient:
    """Test suite for GeminiClient class."""

    def setup_method(self):
        """Setup test environment before each test."""
        # Reset singleton so each test gets a fresh LLM client
        reset_llm_client()
        # Mock the API key
        self.api_key = "test_api_key_123"

    def test_client_initialization_with_api_key(self):
        """Test client initialization with API key."""
        client = GeminiClient(api_key=self.api_key)
        assert client.api_key == self.api_key

    @patch.dict(
        os.environ, {"GEMINI_API_KEY": "env_api_key", "PORTF_LLM_PROVIDER": "gemini"}
    )
    def test_client_initialization_from_env(self):
        """Test client initialization from environment variable."""
        client = GeminiClient()
        assert client.api_key == "env_api_key"

    @patch("portf_manager.llm_client.OllamaLLMClient.is_available", return_value=False)
    def test_client_initialization_no_api_key(self, mock_ollama):
        """Test client initialization without any LLM provider raises error."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises((ValueError, RuntimeError)) as exc_info:
                GeminiClient()
            error_msg = str(exc_info.value)
            # Should mention how to set up a provider
            assert "GEMINI_API_KEY" in error_msg or "No LLM provider" in error_msg

    def test_build_extraction_prompt(self):
        """Test building extraction prompt."""
        client = GeminiClient(api_key=self.api_key)
        text = "Test transaction text"

        prompt = client._build_extraction_prompt(text)

        assert "financial transaction parser" in prompt.lower()
        assert "json array" in prompt.lower()
        assert text in prompt
        assert "Spanish" in prompt  # Should support Spanish
        assert "ISIN" in prompt  # Should support ISIN codes

    def test_parse_response_valid_json(self):
        """Test parsing valid JSON response."""
        client = GeminiClient(api_key=self.api_key)

        # Test with array response
        response_text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "AAPL",
                    "asset_name": "Apple Inc.",
                    "quantity": 100.0,
                    "price": 150.0,
                    "date": "2024-01-15",
                    "currency": "USD",
                    "raw_text": "Test transaction",
                }
            ]
        )

        transactions = client._parse_response(response_text, "original text")

        assert len(transactions) == 1
        assert transactions[0].symbol == "AAPL"
        assert transactions[0].quantity == 100.0
        assert transactions[0].price == 150.0

    def test_parse_response_single_object(self):
        """Test parsing single object response."""
        client = GeminiClient(api_key=self.api_key)

        # Test with single object response
        response_text = json.dumps(
            {
                "tx_type": "sell",
                "symbol": "GOOGL",
                "asset_name": "Google",
                "quantity": 50.0,
                "price": 2000.0,
                "date": "2024-01-16",
                "currency": "USD",
                "raw_text": "Test sell transaction",
            }
        )

        transactions = client._parse_response(response_text, "original text")

        assert len(transactions) == 1
        assert transactions[0].symbol == "GOOGL"
        assert transactions[0].tx_type == "sell"

    def test_parse_response_markdown_formatted(self):
        """Test parsing markdown formatted response."""
        client = GeminiClient(api_key=self.api_key)

        # Test with markdown formatting
        response_text = """```json
[{
    "tx_type": "buy",
    "symbol": "AAPL",
    "asset_name": "Apple Inc.",
    "quantity": 100.0,
    "price": 150.0,
    "date": "2024-01-15",
    "currency": "USD",
    "raw_text": "Test transaction"
}]
```"""

        transactions = client._parse_response(response_text, "original text")

        assert len(transactions) == 1
        assert transactions[0].symbol == "AAPL"

    def test_parse_response_invalid_json(self):
        """Test parsing invalid JSON response."""
        client = GeminiClient(api_key=self.api_key)

        # Test with invalid JSON
        response_text = "invalid json response"

        transactions = client._parse_response(response_text, "original text")

        assert len(transactions) == 0

    def test_parse_response_empty_array(self):
        """Test parsing empty array response."""
        client = GeminiClient(api_key=self.api_key)

        # Test with empty array
        response_text = "[]"

        transactions = client._parse_response(response_text, "original text")

        assert len(transactions) == 0

    def test_parse_response_invalid_transaction(self):
        """Test parsing response with invalid transaction data."""
        client = GeminiClient(api_key=self.api_key)

        # Test with invalid transaction (missing required fields)
        response_text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "AAPL",
                    # Missing required fields
                }
            ]
        )

        transactions = client._parse_response(response_text, "original text")

        # Should skip invalid transactions
        assert len(transactions) == 0

    def test_parse_response_mixed_valid_invalid(self):
        """Test parsing response with mix of valid and invalid transactions."""
        client = GeminiClient(api_key=self.api_key)

        # Test with mix of valid and invalid
        response_text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "AAPL",
                    "asset_name": "Apple Inc.",
                    "quantity": 100.0,
                    "price": 150.0,
                    "date": "2024-01-15",
                    "currency": "USD",
                    "raw_text": "Valid transaction",
                },
                {
                    "tx_type": "buy",
                    "symbol": "INVALID",
                    # Missing required fields
                },
            ]
        )

        transactions = client._parse_response(response_text, "original text")

        # Should only include valid transactions
        assert len(transactions) == 1
        assert transactions[0].symbol == "AAPL"

    @patch("google.genai.Client")
    def test_extract_transactions_success(self, mock_client_class):
        """Test successful transaction extraction."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "AAPL",
                    "asset_name": "Apple Inc.",
                    "quantity": 100.0,
                    "price": 150.0,
                    "date": "2024-01-15",
                    "currency": "USD",
                    "raw_text": "Test transaction",
                }
            ]
        )
        mock_client_class.return_value.models.generate_content.return_value = (
            mock_response
        )

        client = GeminiClient(api_key=self.api_key)

        # Extract transactions
        transactions = client.extract_transactions("Test transaction text")

        assert len(transactions) == 1
        assert transactions[0].symbol == "AAPL"
        assert transactions[0].quantity == 100.0

        # Verify the client was called with correct prompt
        mock_client_class.return_value.models.generate_content.assert_called_once()
        call_kwargs = (
            mock_client_class.return_value.models.generate_content.call_args.kwargs
        )
        assert "Test transaction text" in call_kwargs["contents"]

    @patch("google.genai.Client")
    def test_extract_transactions_api_error(self, mock_client_class):
        """Test handling API errors during extraction."""
        mock_client_class.return_value.models.generate_content.side_effect = Exception(
            "API Error"
        )

        client = GeminiClient(api_key=self.api_key)

        # Should return empty list on error
        transactions = client.extract_transactions("Test transaction text")

        assert len(transactions) == 0

    @patch("google.genai.Client")
    def test_extract_transactions_spanish_text(self, mock_client_class):
        """Test extraction with Spanish text."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "US0378331005",
                    "asset_name": "Apple Inc.",
                    "quantity": 14.0,
                    "price": 9.87,
                    "date": "2025-06-17",
                    "currency": "EUR",
                    "raw_text": "Compra de acciones US0378331005",
                }
            ]
        )
        mock_client_class.return_value.models.generate_content.return_value = (
            mock_response
        )

        client = GeminiClient(api_key=self.api_key)

        # Spanish transaction text
        spanish_text = """
        Compra de acciones
        US0378331005
        17/06/2025, 15:07:28
        138,60 €
        14 títulos
        Apple Inc.
        Precio límite: 9,87 €
        """

        transactions = client.extract_transactions(spanish_text)

        assert len(transactions) == 1
        assert transactions[0].symbol == "US0378331005"
        assert transactions[0].currency == "EUR"
        assert transactions[0].quantity == 14.0
        assert transactions[0].price == 9.87

    def test_prompt_contains_spanish_examples(self):
        """Test that prompt contains Spanish examples and mappings."""
        client = GeminiClient(api_key=self.api_key)
        prompt = client._build_extraction_prompt("test text")

        # Check for Spanish language support
        assert "Spanish" in prompt
        assert "Compra" in prompt
        assert "títulos" in prompt
        assert "€" in prompt
        assert "EUR" in prompt
        assert "DD/MM/YYYY" in prompt
        assert "9,87" in prompt

        # Check for ISIN support
        assert "ISIN" in prompt
        assert "US0378331005" in prompt

    def test_prompt_contains_language_mappings(self):
        """Test that prompt contains language mappings."""
        client = GeminiClient(api_key=self.api_key)
        prompt = client._build_extraction_prompt("test text")

        # Check for language mappings
        assert "Compra de acciones" in prompt
        assert "Venta de acciones" in prompt
        assert "Precio límite" in prompt

        # Check for format conversions
        assert "DD/MM/YYYY" in prompt
        assert "YYYY-MM-DD" in prompt
        assert "9,87" in prompt
        assert "9.87" in prompt


class TestLLMTransaction:
    """Test suite for LLMTransaction class."""

    def test_valid_transaction_creation(self):
        """Test creating a valid transaction."""
        transaction = LLMTransaction(
            tx_type="buy",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=150.0,
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        assert transaction.tx_type == "buy"
        assert transaction.symbol == "AAPL"
        assert transaction.asset_name == "Apple Inc."
        assert transaction.quantity == 100.0
        assert transaction.price == 150.0
        assert transaction.date == "2024-01-15"
        assert transaction.currency == "USD"
        assert transaction.raw_text == "Test transaction"

    def test_transaction_validation_success(self):
        """Test successful transaction validation."""
        transaction = LLMTransaction(
            tx_type="buy",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=150.0,
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        # Should return None for valid transaction
        assert transaction.validate() is None

    def test_transaction_validation_missing_fields(self):
        """Test transaction validation with missing fields."""
        transaction = LLMTransaction(
            tx_type="",  # Empty type
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=150.0,
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        validation_error = transaction.validate()
        assert validation_error is not None
        assert "Transaction type is required" in validation_error

    def test_transaction_validation_invalid_type(self):
        """Test transaction validation with invalid type."""
        transaction = LLMTransaction(
            tx_type="invalid_type",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=150.0,
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        validation_error = transaction.validate()
        assert validation_error is not None
        assert (
            "Transaction type must be 'buy', 'sell', 'dividend' or 'interest'"
            in validation_error
        )

    def test_transaction_validation_zero_quantity(self):
        """Test transaction validation with zero quantity."""
        transaction = LLMTransaction(
            tx_type="buy",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=0.0,  # Zero quantity
            price=150.0,
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        validation_error = transaction.validate()
        assert validation_error is not None
        assert "Quantity must be positive" in validation_error

    def test_transaction_validation_negative_price(self):
        """Test transaction validation with negative price."""
        transaction = LLMTransaction(
            tx_type="buy",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=-150.0,  # Negative price
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        validation_error = transaction.validate()
        assert validation_error is not None
        assert "Price must be positive" in validation_error

    def test_transaction_validation_invalid_date(self):
        """Test transaction validation with invalid date."""
        transaction = LLMTransaction(
            tx_type="buy",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=150.0,
            date="invalid-date",  # Invalid date
            currency="USD",
            raw_text="Test transaction",
        )

        validation_error = transaction.validate()
        assert validation_error is not None
        assert "Date must be in ISO-8601 format" in validation_error

    def test_transaction_string_representation(self):
        """Test transaction string representation."""
        transaction = LLMTransaction(
            tx_type="buy",
            symbol="AAPL",
            asset_name="Apple Inc.",
            quantity=100.0,
            price=150.0,
            date="2024-01-15",
            currency="USD",
            raw_text="Test transaction",
        )

        str_repr = str(transaction)
        assert "AAPL" in str_repr
        assert "buy" in str_repr
        assert "100.0" in str_repr
        assert "150.0" in str_repr


class TestGeminiIntegration:
    """Integration tests for Gemini client with real-world scenarios."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.api_key = "test_api_key_123"

    @patch("google.genai.Client")
    def test_extract_multiple_transactions(self, mock_client_class):
        """Test extracting multiple transactions from text."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "AAPL",
                    "asset_name": "Apple Inc.",
                    "quantity": 100.0,
                    "price": 150.0,
                    "date": "2024-01-15",
                    "currency": "USD",
                    "raw_text": "Buy AAPL transaction",
                },
                {
                    "tx_type": "sell",
                    "symbol": "GOOGL",
                    "asset_name": "Google",
                    "quantity": 50.0,
                    "price": 2000.0,
                    "date": "2024-01-16",
                    "currency": "USD",
                    "raw_text": "Sell GOOGL transaction",
                },
            ]
        )
        mock_client_class.return_value.models.generate_content.return_value = (
            mock_response
        )

        client = GeminiClient(api_key=self.api_key)
        transactions = client.extract_transactions("Multiple transactions text")

        assert len(transactions) == 2
        assert transactions[0].symbol == "AAPL"
        assert transactions[0].tx_type == "buy"
        assert transactions[1].symbol == "GOOGL"
        assert transactions[1].tx_type == "sell"

    @patch("google.genai.Client")
    def test_extract_no_transactions(self, mock_client_class):
        """Test extracting from text with no transactions."""
        mock_response = MagicMock()
        mock_response.text = "[]"
        mock_client_class.return_value.models.generate_content.return_value = (
            mock_response
        )

        client = GeminiClient(api_key=self.api_key)
        transactions = client.extract_transactions("No transaction text")

        assert len(transactions) == 0

    @patch("google.genai.Client")
    def test_extract_isin_transactions(self, mock_client_class):
        """Test extracting transactions with ISIN codes."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            [
                {
                    "tx_type": "buy",
                    "symbol": "US0378331005",
                    "asset_name": "Apple Inc.",
                    "quantity": 14.0,
                    "price": 9.87,
                    "date": "2025-06-17",
                    "currency": "EUR",
                    "raw_text": "ISIN transaction",
                }
            ]
        )
        mock_client_class.return_value.models.generate_content.return_value = (
            mock_response
        )

        client = GeminiClient(api_key=self.api_key)
        transactions = client.extract_transactions("ISIN transaction text")

        assert len(transactions) == 1
        assert transactions[0].symbol == "US0378331005"
        assert transactions[0].currency == "EUR"
        assert transactions[0].quantity == 14.0
