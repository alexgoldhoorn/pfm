# Demo Account

A self-contained, **fictional** demo portfolio for showing off the product,
taking screenshots, and exploring the analytics — without exposing any real
holdings. Everything here is synthetic sample data.

## Quick start (full web UI)

```bash
# 1. Generate the demo database (writes ./portfolio_demo.db, fetches live prices)
python scripts/create_demo_account.py --fresh

# 2. Bring up the isolated demo stack (backend + web UI on its own network)
docker compose -f docker-compose.demo.yml up -d

# 3. Open the UI
open http://localhost:8081
```

Log in via the **API key** tab with:

```
demo-key-0000000000000000000000000000000000000000000000000000000000
```

…or the **Password** tab with `demo` / `demo`.

Tear down when finished:

```bash
docker compose -f docker-compose.demo.yml down
```

The demo stack uses its own containers (`portf_demo_backend`, `portf_demo_web`),
its own bridge network, and port **8081** — it never touches the live dev/prod
stack or the real `portfolio.db`.

## What's in the demo

| Area            | Contents |
|-----------------|----------|
| **Login**       | `demo` / `demo` + a fixed demo API key |
| **Portfolios**  | Degiro, Trade Republic, Indexa Capital, Coinbase (EUR base) |
| **Assets (10)** | AAPL, MSFT, NVDA, GOOGL, JNJ (US), LLOY.L (UK/GBX), IWDA.AS, VWCE.DE (EUR ETFs), BTC-EUR, ETH-EUR |
| **Transactions**| 18 buys (spread over ~2 yrs), 2 sells (realised gains for the FIFO tax report), 8 cash dividends |
| **Bookings**    | 6 deposits funding the brokers over time |
| **Snapshots**   | ~366 daily net-worth points → the chart, drawdown / volatility / Sharpe |
| **Watchlist**   | AMZN, TSLA with buy-below targets |
| **Goal**        | A FIRE target (€500k) with monthly contribution + projection |
| **Rebalancing** | Allocation targets: ETF 45% / Stock 40% / Crypto 15% |

The asset symbols are real tickers, so the daily price fetch and EUR/FX
conversion populate live current value and P&L. All quantities, prices, dates,
and amounts are made up.

## Refreshing prices

`create_demo_account.py` fetches current prices on creation. To refresh later
without rebuilding the DB, regenerate (cheap) or fetch directly:

```bash
python scripts/create_demo_account.py --fresh   # rebuild + refetch prices
```

## CLI against the demo DB

The CLI accepts `--db-path`, so you can drive the demo data from the terminal
too (local mode, no server):

```bash
python -m portf_manager.cli --db-path portfolio_demo.db list-portfolios
python -m portf_manager.cli --db-path portfolio_demo.db list-transactions
```

## Notes

- `portfolio_demo.db` is gitignored — it's a generated artifact, regenerate it
  any time with the script.
- The demo is fully isolated from real data; deleting `portfolio_demo.db`
  removes it entirely.
