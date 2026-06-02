import json
from typing import List, Optional
import logging

from .llm_client import LLMClient, get_llm_client
from .llm_types import LLMTransaction

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    LLM-powered client for extracting transaction data from broker statements
    and general chat. Uses the provider-agnostic LLMClient abstraction.

    Name kept as GeminiClient for backward compatibility, but it works
    with any configured LLM provider (Gemini, Ollama, etc.).
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        api_key: Optional[str] = None,
    ):
        """
        Initialize the client.

        Args:
            llm: Optional LLMClient instance. If not provided, uses get_llm_client() factory.
            api_key: Optional API key (backward compat). If given, forces Gemini provider.
        """
        if api_key:
            # Backward compatibility: explicit api_key forces Gemini
            from .llm_client import GeminiLLMClient

            self.llm = GeminiLLMClient(api_key=api_key)
            self.api_key = api_key
        else:
            self.llm = llm or get_llm_client()
            self.api_key = getattr(self.llm, "api_key", None)

    def extract_transactions(self, text: str) -> List[LLMTransaction]:
        """
        Extract transaction data from broker statement text using Gemini API.

        Args:
            text: Raw broker statement text

        Returns:
            List of LLMTransaction objects extracted from the text
        """
        try:
            prompt = self._build_extraction_prompt(text)
            response_text = self.llm.generate(prompt)

            # Parse the JSON response
            transactions = self._parse_response(response_text, text)
            return transactions

        except Exception as e:
            logger.error(f"Error extracting transactions: {str(e)}")
            return []

    def _build_extraction_prompt(self, text: str) -> str:
        """
        Build the prompt for transaction extraction with in-context examples.

        Args:
            text: Raw broker statement text

        Returns:
            Formatted prompt string
        """
        return f"""
You are a financial transaction parser. Extract all buy/sell transactions from the following broker statement text and return them as a JSON array.

The text can be in any language (English, Spanish, etc.) and may be unstructured or formatted as complex broker statements. Look for key transaction information scattered throughout the text.

Each transaction should be formatted as a JSON object with these exact fields:
- tx_type: "buy" or "sell"
- symbol: Stock symbol/ticker (e.g., "AAPL", "GOOGL") or ISIN code
- asset_name: Full company or fund name (e.g., "Apple Inc.", "Apple Inc.")
- quantity: Number of shares/units as a float
- price: Price per share/unit as a float (use "Precio" / "Precio límite" for Spanish, or the gross price per share)
- date: Date in ISO-8601 format (YYYY-MM-DD)
- currency: Currency code (e.g., "USD", "EUR")
- fees: Total transaction costs as a float — sum ALL fee/commission/expense fields (e.g., "Comisiones" + "Gastos", or "Commission" + "Fees"). Default 0.0 if none.
- raw_text: A relevant excerpt from the original text containing this transaction

EXAMPLES:

Input: "2024-01-15 BUY 100 AAPL Apple Inc. @ $150.25 USD, Commission $3.50"
Output: {{
  "tx_type": "buy",
  "symbol": "AAPL",
  "asset_name": "Apple Inc.",
  "quantity": 100.0,
  "price": 150.25,
  "date": "2024-01-15",
  "currency": "USD",
  "fees": 3.50,
  "raw_text": "2024-01-15 BUY 100 AAPL Apple Inc. @ $150.25 USD, Commission $3.50"
}}

Input: "01/20/2024 SELL 50 GOOGL Alphabet Inc. $2,750.00 USD"
Output: {{
  "tx_type": "sell",
  "symbol": "GOOGL",
  "asset_name": "Alphabet Inc.",
  "quantity": 50.0,
  "price": 2750.00,
  "date": "2024-01-20",
  "currency": "USD",
  "raw_text": "01/20/2024 SELL 50 GOOGL Alphabet Inc. $2,750.00 USD"
}}

Input: Spanish broker statement with scattered information:
"Compra de acciones
US0378331005
17/06/2025, 15:07:28
138,60 €
14 títulos
Apple Inc.
Precio límite: 9,87 €
Títulos: 14
Importe bruto en divisa: 138,21 €"
Output: {{
  "tx_type": "buy",
  "symbol": "US0378331005",
  "asset_name": "Apple Inc.",
  "quantity": 14.0,
  "price": 9.87,
  "date": "2025-06-17",
  "currency": "EUR",
  "raw_text": "Compra de acciones US0378331005 17/06/2025, 15:07:28 138,60 € 14 títulos Apple Inc. Precio límite: 9,87 €"
}}

LANGUAGE MAPPING (Spanish to English):
- "Compra" / "Compra de acciones" → "buy"
- "Venta" / "Venta de acciones" → "sell"
- "títulos" / "acciones" → shares/units
- "€" → "EUR"
- "$" → "USD"
- Date formats: "DD/MM/YYYY" → "YYYY-MM-DD"
- Decimal separator: "9,87" → 9.87
- "Precio límite" / "Precio Bruto" / "Precio" → price per share (gross unit price)
- "Comisiones" + "Gastos" → fees (add them together)
- "Tasas e Impuestos" → include in fees if non-zero
- "Número de títulos/Participaciones" → quantity

COMMON FIELDS TO LOOK FOR:
- Transaction type: "Compra", "Venta", "Buy", "Sell", "Purchase", "Sale", "COMPRA", "VENTA"
- Asset identifier: ISIN codes (e.g., "US0378331005"), ticker symbols (e.g., "AAPL")
- Asset name: Full company/fund name anywhere in the text
- Quantity: Look for "títulos", "Número de títulos", "shares", "units"
- Price: "Precio límite", "Precio Bruto", "Precio", "Price", "@" — use the per-share price, not the total
- Fees: "Comisiones", "Gastos", "Commission", "Fees", "Brokerage" — SUM all fee fields
- Date: Various formats like "DD/MM/YYYY", "YYYY-MM-DD", "MM/DD/YYYY", "Fecha Operación"
- Currency: "EUR", "USD", "€", "$", "Divisa"

IMPORTANT:
- Return ONLY a valid JSON array of transaction objects
- Do not include any other text or explanations
- If no transactions are found, return an empty array: []
- Ensure all numeric values are proper numbers (not strings)
- Convert dates to ISO-8601 format (YYYY-MM-DD)
- Convert decimal commas to dots (e.g., "9,87" → 9.87)
- Map transaction types to "buy" or "sell" regardless of language
- Use ISIN codes as symbols when stock tickers are not available
- Extract meaningful excerpts for raw_text, not the entire input

Now extract transactions from this broker statement:

{text}
"""

    def _parse_response(
        self, response_text: str, original_text: str
    ) -> List[LLMTransaction]:
        """
        Parse the JSON response from Gemini and convert to LLMTransaction objects.

        Args:
            response_text: Raw response text from Gemini
            original_text: Original input text for error context

        Returns:
            List of LLMTransaction objects
        """
        transactions = []

        try:
            # Clean the response text to extract just the JSON
            response_text = response_text.strip()

            # Handle cases where the response might have markdown formatting
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                json_lines = []
                in_json = False

                for line in lines:
                    if line.strip().startswith("```"):
                        in_json = not in_json
                        continue
                    if in_json:
                        json_lines.append(line)

                response_text = "\n".join(json_lines)

            # Parse the JSON
            transaction_data = json.loads(response_text)

            # Handle both single object and array responses
            if isinstance(transaction_data, dict):
                transaction_data = [transaction_data]

            # Convert each dict to LLMTransaction
            for tx_dict in transaction_data:
                try:
                    transaction = LLMTransaction(
                        tx_type=tx_dict.get("tx_type", "").lower(),
                        symbol=tx_dict.get("symbol", ""),
                        asset_name=tx_dict.get("asset_name", ""),
                        quantity=float(tx_dict.get("quantity", 0)),
                        price=float(tx_dict.get("price", 0)),
                        date=tx_dict.get("date", ""),
                        currency=tx_dict.get("currency", ""),
                        fees=float(tx_dict.get("fees", 0)),
                        raw_text=tx_dict.get("raw_text", ""),
                    )

                    # Validate the transaction
                    validation_error = transaction.validate()
                    if validation_error:
                        logger.warning(
                            f"Transaction validation failed: {validation_error}"
                        )
                        continue

                    transactions.append(transaction)

                except (ValueError, TypeError) as e:
                    logger.warning(
                        f"Error converting transaction dict to LLMTransaction: {e}"
                    )
                    continue

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.error(f"Response text: {response_text}")

        except Exception as e:
            logger.error(f"Unexpected error parsing response: {e}")

        return transactions

    def extract_bookings(self, text: str) -> List[dict]:
        """Extract cash deposits / withdrawals (bookings) from statement text.

        Bookings are cash transfers into or out of a broker account — distinct
        from buy/sell trades. Returns a list of dicts with keys: ``date``,
        ``action`` ("Deposit" | "Withdrawal"), ``amount`` (float), ``currency``,
        and optional ``broker``. Returns [] on any failure.
        """
        prompt = f"""
You extract CASH MOVEMENTS (deposits and withdrawals) from a broker or bank
statement. These are transfers of money INTO or OUT OF a brokerage/cash
account — NOT purchases or sales of shares/ETFs/crypto. Ignore any buy/sell
trades, dividends, and fees.

Return ONLY a JSON array. Each object has these exact fields:
- date: ISO-8601 date (YYYY-MM-DD)
- action: "Deposit" (money in) or "Withdrawal" (money out)
- amount: positive number (float), the cash amount
- currency: currency code (e.g. "EUR", "USD"); default "EUR"
- broker: account/broker name if stated, else null

LANGUAGE MAPPING:
- "Ingreso" / "Transferencia recibida" / "Aportación" / "Deposit" / "Storting" -> "Deposit"
- "Retirada" / "Reintegro" / "Transferencia enviada" / "Withdrawal" / "Opname" -> "Withdrawal"
- "€"->"EUR", "$"->"USD"; decimal comma "1.234,56" -> 1234.56

EXAMPLE
Input: "10/01/2026 Ingreso por transferencia 2.000,00 € Degiro"
Output: [{{"date": "2026-01-10", "action": "Deposit", "amount": 2000.0, "currency": "EUR", "broker": "Degiro"}}]

Rules:
- Return ONLY the JSON array, no prose.
- If there are no cash movements, return [].
- amount is always positive; the direction is in "action".

Now extract cash movements from this text:

{text}
"""
        try:
            response_text = self.llm.generate(prompt).strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(
                    ln for ln in lines if not ln.strip().startswith("```")
                )
            data = json.loads(response_text)
            bookings = []
            for item in data if isinstance(data, list) else []:
                action = str(item.get("action", "")).strip().capitalize()
                if action not in ("Deposit", "Withdrawal"):
                    continue
                try:
                    amount = abs(float(item.get("amount")))
                except (TypeError, ValueError):
                    continue
                if not item.get("date") or amount <= 0:
                    continue
                bookings.append(
                    {
                        "broker": item.get("broker") or None,
                        "date": str(item["date"])[:10],
                        "action": action,
                        "amount": amount,
                        "currency": (item.get("currency") or "EUR").upper()[:3],
                    }
                )
            return bookings
        except Exception as e:
            logger.error(f"Error extracting bookings: {str(e)}")
            return []

    def chat(self, prompt: str) -> str:
        """
        Generate a chat response using the configured LLM.

        Args:
            prompt: The input prompt/question

        Returns:
            str: Generated response

        Raises:
            RuntimeError: If generation fails
        """
        try:
            return self.llm.generate(prompt)
        except Exception as e:
            raise RuntimeError(f"Chat generation failed: {str(e)}")
