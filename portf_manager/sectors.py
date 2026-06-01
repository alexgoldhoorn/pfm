from typing import Dict, List

# GICS sector mapping dictionary
GICS_SECTOR_MAP: Dict[str, str] = {
    "AAPL": "Information Technology",
    "GOOGL": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "MSFT": "Information Technology",
    "JPM": "Financials",
    # Add more tickers and their respective sectors here
}


def resolve_sector(asset_symbol: str) -> str:
    """
    Resolve the GICS sector for a given asset symbol.

    :param asset_symbol: The symbol of the asset to resolve the sector for.
    :return: The sector name if found, else 'Unknown Sector'.
    """
    return GICS_SECTOR_MAP.get(asset_symbol, "Unknown Sector")


def list_all_sectors() -> List[str]:
    """
    List all unique GICS sectors.

    :return: A list of unique sectors.
    """
    return list(set(GICS_SECTOR_MAP.values()))
