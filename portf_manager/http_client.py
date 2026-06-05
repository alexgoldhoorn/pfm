"""
HTTP Client for Portfolio Manager Server Mode.

Provides HTTP-based communication with the Portfolio Manager server when running in server mode.
Uses httpx for async HTTP operations and provides the same interface as local operations.
"""

import httpx
import json
from typing import Dict, List, Optional, Any
from datetime import date, datetime

from .config import PortfolioConfig


class PortfolioHTTPClient:
    """HTTP client for Portfolio Manager server operations."""

    def __init__(self, config: PortfolioConfig):
        """
        Initialize HTTP client with server configuration.

        Args:
            config: Portfolio configuration with server details
        """
        self.config = config
        self.base_url = config.server_url.rstrip("/")
        self.api_key = config.api_key
        self.headers = {"Content-Type": "application/json", "X-API-Key": self.api_key}

        # Create httpx client with timeout and retry settings
        self.client = httpx.Client(
            timeout=30.0, headers=self.headers, follow_redirects=True
        )

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close client."""
        self.client.close()

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        Make HTTP request to server.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (will be prefixed with base_url)
            **kwargs: Additional arguments passed to httpx

        Returns:
            Dict: Response JSON data

        Raises:
            RuntimeError: If request fails or server returns error
        """
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()

            # Handle empty responses
            if not response.content:
                return {}

            return response.json()

        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            try:
                error_data = e.response.json()
                error_detail = error_data.get(
                    "detail", error_data.get("message", str(error_data))
                )
            except (json.JSONDecodeError, AttributeError):
                error_detail = e.response.text or str(e)

            raise RuntimeError(
                f"Server error ({e.response.status_code}): {error_detail}"
            )

        except httpx.RequestError as e:
            raise RuntimeError(f"Connection error: {e}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

    # Asset operations
    def create_asset(
        self,
        symbol: str,
        name: str,
        asset_type: str,
        exchange: Optional[str] = None,
        currency: str = "USD",
        sector: Optional[str] = None,
        description: Optional[str] = None,
    ) -> int:
        """Create a new asset on the server."""
        data = {
            "symbol": symbol.upper(),
            "name": name,
            "asset_type": asset_type,
            "exchange": exchange,
            "currency": currency.upper(),
            "sector": sector,
            "description": description,
        }

        response = self._make_request("POST", "/api/v1/assets", json=data)
        return response.get("id")

    def get_asset_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get asset by symbol from server."""
        try:
            response = self._make_request(
                "GET", f"/api/v1/assets?symbol={symbol.upper()}"
            )
            assets = (
                response if isinstance(response, list) else response.get("assets", [])
            )

            for asset in assets:
                if asset.get("symbol") == symbol.upper():
                    return asset
            return None

        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def get_asset(self, asset_id: int) -> Optional[Dict[str, Any]]:
        """Get asset by ID from server."""
        try:
            return self._make_request("GET", f"/api/v1/assets/{asset_id}")
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def get_all_assets(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all assets from server."""
        params = {"active_only": active_only}
        response = self._make_request("GET", "/api/v1/assets", params=params)
        return response if isinstance(response, list) else response.get("assets", [])

    def update_asset(self, asset_id: int, **kwargs) -> bool:
        """Update asset on server."""
        try:
            self._make_request("PUT", f"/api/v1/assets/{asset_id}", json=kwargs)
            return True
        except RuntimeError:
            return False

    def delete_asset(self, asset_id: int) -> bool:
        """Delete an asset from the server (soft delete)."""
        try:
            self._make_request("DELETE", f"/api/v1/assets/{asset_id}")
            return True
        except RuntimeError:
            return False

    # Transaction operations
    def create_transaction(
        self,
        asset_id: int,
        transaction_type: str,
        quantity: float,
        price: float,
        total_amount: float,
        transaction_date: str,
        portfolio_id: Optional[int] = None,
        description: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Create a new transaction on the server."""
        data = {
            "asset_id": asset_id,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "price": price,
            "total_amount": total_amount,
            "transaction_date": transaction_date,
            "portfolio_id": portfolio_id,
            "description": description,
            "user_id": user_id,
        }

        response = self._make_request("POST", "/api/v1/transactions", json=data)
        return response.get("id")

    def get_all_transactions(
        self, user_id: Optional[int] = None, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get all transactions from server."""
        params = {}
        if user_id is not None:
            params["user_id"] = user_id
        if limit is not None:
            params["limit"] = limit

        response = self._make_request("GET", "/api/v1/transactions", params=params)
        return (
            response if isinstance(response, list) else response.get("transactions", [])
        )

    def get_transactions_by_asset(self, asset_id: int) -> List[Dict[str, Any]]:
        """Get transactions for specific asset from server."""
        params = {"asset_id": asset_id}
        response = self._make_request("GET", "/api/v1/transactions", params=params)
        return (
            response if isinstance(response, list) else response.get("transactions", [])
        )

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete a transaction from the server."""
        try:
            self._make_request("DELETE", f"/api/v1/transactions/{transaction_id}")
            return True
        except RuntimeError:
            return False

    def update_transaction(self, transaction_id: int, **kwargs) -> bool:
        """Update a transaction on the server."""
        if not kwargs:
            return False

        # Filter out None values and prepare update data
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return False

        try:
            self._make_request(
                "PUT", f"/api/v1/transactions/{transaction_id}", json=update_data
            )
            return True
        except RuntimeError:
            return False

    def get_transaction(self, transaction_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific transaction by ID from server."""
        try:
            return self._make_request("GET", f"/api/v1/transactions/{transaction_id}")
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    # Portfolio operations
    def create_portfolio(
        self,
        name: str,
        base_currency: str = "USD",
        entity_id: Optional[int] = None,
        description: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Create a new portfolio on the server."""
        data = {
            "name": name,
            "base_currency": base_currency,
            "entity_id": entity_id,
            "description": description,
            "user_id": user_id,
        }

        response = self._make_request("POST", "/api/v1/portfolios", json=data)
        return response.get("id")

    def get_portfolio_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get portfolio by name from server."""
        try:
            response = self._make_request("GET", f"/api/v1/portfolios?name={name}")
            portfolios = (
                response
                if isinstance(response, list)
                else response.get("portfolios", [])
            )

            for portfolio in portfolios:
                if portfolio.get("name") == name:
                    return portfolio
            return None

        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict[str, Any]]:
        """Get portfolio by ID from server."""
        try:
            return self._make_request("GET", f"/api/v1/portfolios/{portfolio_id}")
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def get_all_portfolios(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all portfolios from server."""
        params = {}
        if user_id is not None:
            params["user_id"] = user_id

        response = self._make_request("GET", "/api/v1/portfolios", params=params)
        return (
            response if isinstance(response, list) else response.get("portfolios", [])
        )

    # Entity operations
    def create_entity(
        self,
        name: str,
        entity_type: str,
        user_id: Optional[int] = None,
        website: Optional[str] = None,
        description: Optional[str] = None,
    ) -> int:
        """Create a new entity on the server."""
        data = {
            "name": name,
            "entity_type": entity_type,
            "user_id": user_id,
            "website": website,
            "description": description,
        }

        response = self._make_request("POST", "/api/v1/entities", json=data)
        return response.get("id")

    def get_entity_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get entity by name from server."""
        try:
            response = self._make_request("GET", f"/api/v1/entities?name={name}")
            entities = (
                response if isinstance(response, list) else response.get("entities", [])
            )

            for entity in entities:
                if entity.get("name") == name:
                    return entity
            return None

        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def get_all_entities(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all entities from server."""
        params = {}
        if user_id is not None:
            params["user_id"] = user_id

        response = self._make_request("GET", "/api/v1/entities", params=params)
        return response if isinstance(response, list) else response.get("entities", [])

    # Price operations
    def get_latest_price(self, asset_id: int) -> Optional[Dict[str, Any]]:
        """Get latest price for asset from server."""
        try:
            return self._make_request("GET", f"/api/v1/assets/{asset_id}/prices/latest")
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def insert_price_record(
        self,
        symbol: str,
        price: float,
        fetched_ts: datetime,
        source: str = "yfinance",
        price_type: str = "close",
        price_date: Optional[str] = None,
    ) -> int:
        """Insert a price record via the REST API (server-mode equivalent of db.insert_price_record)."""
        if price_date is None:
            price_date = date.today().isoformat()

        asset = self.get_asset_by_symbol(symbol)
        if not asset:
            raise ValueError(f"Asset not found: {symbol}")

        result = self._make_request(
            "POST",
            f"/api/v1/assets/{asset['id']}/prices",
            json={
                "price": price,
                "price_date": price_date,
                "price_type": price_type,
                "source": source,
            },
        )
        return result.get("id", 0)

    def record_price_update_run(
        self,
        started_at: str,
        duration_seconds: float,
        updated_count: int,
        skipped_count: int,
        error_count: int,
        skipped_symbols: Optional[list] = None,
        error_symbols: Optional[list] = None,
        api_errors: Optional[list] = None,
        source: str = "cron",
    ) -> int:
        """Record a price-update run via the REST API (server-mode equivalent
        of db.record_price_update_run)."""
        result = self._make_request(
            "POST",
            "/api/v1/analytics/update-runs",
            json={
                "started_at": started_at,
                "duration_seconds": duration_seconds,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "skipped_symbols": skipped_symbols or [],
                "error_symbols": error_symbols or [],
                "api_errors": api_errors or [],
                "source": source,
            },
        )
        return result.get("id", 0)

    # LLM chat operations
    def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        symbols: Optional[list[str]] = None,
        live: bool = True,
        search: bool = False,
    ) -> Dict[str, Any]:
        """Send a chat message to the server-side LLM chat endpoint."""
        payload = {
            "message": message,
            "session_id": session_id,
            "symbols": symbols,
            "live": live,
            "search": search,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._make_request("POST", "/api/v1/llm/chat", json=payload)


def create_http_client(config: PortfolioConfig) -> PortfolioHTTPClient:
    """
    Create HTTP client instance for server mode operations.

    Args:
        config: Portfolio configuration

    Returns:
        PortfolioHTTPClient: Configured HTTP client
    """
    return PortfolioHTTPClient(config)
