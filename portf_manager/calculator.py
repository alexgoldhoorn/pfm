import pandas as pd
import numpy as np
from typing import List
from decimal import Decimal
from .models import Transaction, TransactionType

# Constants
TRADING_DAYS = 252

# Utility Functions


def calculate_portfolio_value(prices: pd.DataFrame, shares: pd.Series) -> float:
    """
    Calculate the total portfolio value based on the asset prices and the number of shares held.
    """
    return (prices.iloc[-1] * shares).sum()


def calculate_allocation(prices: pd.DataFrame, shares: pd.Series) -> pd.Series:
    """
    Calculate the allocation percentage of each asset in the portfolio.
    """
    total_value = calculate_portfolio_value(prices, shares)
    return (prices.iloc[-1] * shares) / total_value


def calculate_pnl(prices: pd.DataFrame, shares: pd.Series) -> pd.Series:
    """
    Calculate the profit and loss (PnL) for each asset since the first price observation.
    """
    start_value = prices.iloc[0] * shares
    end_value = prices.iloc[-1] * shares
    return end_value - start_value


def calculate_volatility(prices: pd.DataFrame) -> pd.Series:
    """
    Calculate annualized volatility for each asset.
    """
    log_returns = np.log(prices / prices.shift(1))
    return log_returns.std() * np.sqrt(TRADING_DAYS)


def calculate_beta(prices: pd.DataFrame, benchmark: pd.Series) -> pd.Series:
    """
    Calculate the beta of each asset with respect to a benchmark index.
    """
    log_returns = np.log(prices / prices.shift(1))
    benchmark_returns = np.log(benchmark / benchmark.shift(1))
    covariance = log_returns.apply(lambda col: col.cov(benchmark_returns))
    benchmark_variance = benchmark_returns.var()
    return covariance / benchmark_variance


def calculate_sharpe_ratio(
    prices: pd.DataFrame, risk_free_rate: float = 0.01
) -> pd.Series:
    """
    Calculate the Sharpe ratio for each asset.
    """
    excess_returns = np.log(prices / prices.shift(1)) - risk_free_rate / TRADING_DAYS
    return (excess_returns.mean() / excess_returns.std()) * np.sqrt(TRADING_DAYS)


def calculate_max_drawdown(prices: pd.DataFrame) -> pd.Series:
    """
    Calculate the maximum drawdown for each asset.
    """
    cumulative_returns = (prices / prices.iloc[0]).cumprod()
    peaks = cumulative_returns.cummax()
    drawdowns = cumulative_returns / peaks - 1
    return drawdowns.min()


def calculate_var(prices: pd.DataFrame, confidence_level: float = 0.95) -> pd.Series:
    """
    Calculate the Value at Risk (VaR) for each asset.
    """
    log_returns = np.log(prices / prices.shift(1))
    return log_returns.quantile(1 - confidence_level)


def calculate_alpha(
    prices: pd.DataFrame, benchmark: pd.Series, risk_free_rate: float = 0.01
) -> pd.Series:
    """
    Calculate the alpha of each asset with respect to a benchmark index.
    """
    beta = calculate_beta(prices, benchmark)
    excess_returns = np.log(prices / prices.shift(1)) - risk_free_rate / TRADING_DAYS
    benchmark_excess = (
        np.log(benchmark / benchmark.shift(1)) - risk_free_rate / TRADING_DAYS
    )
    return excess_returns.mean() - beta * benchmark_excess.mean()


# Position and Cost Basis Calculation


def calculate_asset_positions(transactions: List[Transaction]) -> pd.Series:
    """
    Calculate the current position for each asset from transaction history.
    """
    position = {}

    for transaction in transactions:
        asset_id = transaction.asset_id

        if transaction.transaction_type == TransactionType.BUY:
            position[asset_id] = (
                position.get(asset_id, Decimal("0")) + transaction.quantity
            )
        elif transaction.transaction_type == TransactionType.SELL:
            position[asset_id] = (
                position.get(asset_id, Decimal("0")) - transaction.quantity
            )
        elif transaction.transaction_type == TransactionType.SPLIT:
            position[asset_id] = (
                position.get(asset_id, Decimal("0")) * transaction.quantity
            )

    return pd.Series(position)


def calculate_total_cost_basis(transactions: List[Transaction]) -> pd.Series:
    """
    Calculate the cost basis for each asset from transaction history.
    """
    cost_basis = {}

    for transaction in transactions:
        asset_id = transaction.asset_id

        if transaction.transaction_type == TransactionType.BUY:
            if asset_id not in cost_basis:
                cost_basis[asset_id] = Decimal("0")
            # Add cost including any fees
            cost_basis[asset_id] += (
                transaction.quantity * transaction.price + transaction.fees
            )

    return pd.Series(cost_basis, dtype="float")
