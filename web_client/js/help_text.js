// Centralized help text for the Portfolio Manager web client.
// Loaded BEFORE the app scripts (pfm_core/pages/analytics/features) so its globals are available when pages render.

// Short metric explanations for tooltips (data-bs-toggle="tooltip" title=...).
window.METRIC_HELP = {
  irr: "Money-Weighted IRR: annualised return accounting for the timing and size of your buys/sells. Like the interest rate that makes your cash flows balance.",
  totalReturn: "Total Return: current value + realised gains − amount invested, divided by amount invested. Lifetime, not annualised.",
  periodReturn: "Period Return: change in portfolio value over the selected window, measured from daily snapshots.",
  hhi: "Herfindahl Index (HHI): concentration score 0–10000. Above 2500 = concentrated, below 1500 = well diversified.",
  sharpe: "Sharpe Ratio: return per unit of risk (volatility). Higher is better; >1 is good.",
  volatility: "Volatility: annualised standard deviation of daily returns — how much the portfolio value swings.",
  maxDrawdown: "Max Drawdown: largest peak-to-trough drop in portfolio value over the recorded history.",
  fairValue: "Fair Value: an estimate of intrinsic worth from fundamentals + an LLM analyst. Compare to current price.",
  yieldOnCost: "Yield on Cost: trailing-12-month dividends from a position divided by what you paid for it.",
  feeDrag: "Fee Drag: total fees paid as a percentage of the amount invested — how much costs eat your capital.",
  benchmark: "Benchmark: total return of a market index over the same window, for comparison.",
  netWorth: "Net Worth: total portfolio value in EUR (all currencies converted at current FX rates).",
  invested: "Invested: cost basis of your currently-held positions (what you paid, in EUR).",
  savingsBase: "Savings Base (base del ahorro): the Spanish IRPF income category for investment income — realised capital gains plus dividends. Taxed on a progressive scale (19/21/23/27/28%), separate from your salary.",
  realisedGain: "Realised Gain/Loss: profit or loss actually locked in by selling, using FIFO cost basis. Only realised gains are taxed.",
  unrealisedGain: "Unrealised Gain/Loss: paper profit on positions you still hold (current value − cost basis). Not taxed until you sell.",
  taxEstimate: "Estimated Tax: progressive IRPF savings-base brackets applied to this year's realised gains + dividends. An estimate, not tax advice.",
  taxHarvest: "Tax-Loss Harvesting: positions currently at an unrealised loss. Selling them would realise a loss that offsets taxable gains (watch the 2-month wash-sale rule).",
  snapshots: "Snapshots: a daily record of your portfolio's total value and cost, saved by the price cron. Risk and period-return charts are built from these, so history starts when snapshots began.",
  diversification: "Diversification: your holdings grouped by sector, country, currency and asset type (from Yahoo Finance fundamentals). Concentration (HHI) measures how lopsided the mix is.",
};

// Per-page help: what's shown, where data comes from, how it's computed.
// `body` is HTML rendered inside a Bootstrap modal.
window.PAGE_HELP = {
  dashboard: {
    title: "Dashboard",
    body: `
      <p>Your portfolio at a glance: total value, amount invested, return and open positions.</p>
      <ul class="mb-2">
        <li><strong>Portfolio Value</strong> and <strong>Invested</strong> are shown in EUR. Foreign-currency holdings are converted at live FX rates.</li>
        <li><strong>Return</strong> defaults to lifetime (cost-basis) return. Switching to YTD / 1Y uses daily snapshots, so it only covers the period since snapshots began.</li>
        <li><strong>Top Positions</strong> and the <strong>allocation donut</strong> reflect current open positions by EUR value.</li>
      </ul>
      <p class="text-muted small mb-0">Prices come from Yahoo Finance and are refreshed daily at 20:00 UTC.</p>`
  },
  analytics: {
    title: "Analytics",
    body: `
      <p>Seven tabs covering every angle of portfolio analysis. All values in EUR, prices from Yahoo Finance refreshed daily at 20:00 UTC.</p>
      <ul class="mb-2 small">
        <li><strong>Performance</strong>: total return (FIFO cost basis), money-weighted IRR, and period return vs a market benchmark over a selectable window (YTD / 1Y / 3Y / All). Period return needs daily snapshots.</li>
        <li><strong>Dividends</strong>: monthly bar chart of dividend income, trailing-12-month total, projected annual income, yield on cost per position, and an upcoming dividend calendar.</li>
        <li><strong>Gain / Loss</strong>: unrealised winners and losers leaderboard for your open positions, plus a year-by-year realised gains summary with a tax-report CSV export.</li>
        <li><strong>Tax</strong>: Spanish IRPF savings-base estimate — FIFO realised gains + dividends + interest taxed on the progressive 19/21/23/27/28% brackets. Also shows tax-loss harvesting candidates (open losses, with 2-month wash-sale flag). An estimate, not tax advice.</li>
        <li><strong>Risk</strong>: maximum drawdown, annualised volatility, and Sharpe ratio — computed from daily snapshots (needs at least 3).</li>
        <li><strong>Fees</strong>: total fees and withholding tax paid per broker, plus fee drag % of amount invested.</li>
        <li><strong>Diversification</strong>: sector, country, currency and asset-type breakdown with Herfindahl concentration index (HHI). Fetches fundamentals from Yahoo Finance — can be slow.</li>
      </ul>
      <p class="text-muted small mb-0">Hover any metric label for its definition. Net Worth Over Time chart uses daily snapshots; history starts from the first recorded snapshot.</p>`
  },
  holdings: {
    title: "Holdings",
    body: `
      <p>Your current open positions with cost basis, live price and profit/loss, all in EUR.</p>
      <ul class="mb-2">
        <li><strong>Avg Price</strong> is your FIFO cost basis; <strong>Current Price</strong> is the latest Yahoo Finance quote.</li>
        <li><strong>P/L</strong> is unrealised gain/loss on positions you still hold.</li>
        <li><strong>Research</strong> opens an LLM-generated fair-value analysis from fundamentals — informational, not advice.</li>
        <li><strong>Rebalancing</strong> compares your current allocation against your target percentages and suggests buys/sells to close the drift.</li>
      </ul>
      <p class="text-muted small mb-0">Prices from Yahoo Finance, refreshed daily at 20:00 UTC; converted to EUR at live FX rates.</p>`
  },
  watchlist: {
    title: "Watchlist",
    body: `
      <p>Securities you are considering but do not own yet.</p>
      <ul class="mb-2">
        <li>Set a <strong>Buy below</strong> price; <strong>Distance to Buy</strong> shows how far the current price is from your target.</li>
        <li>Prices are fetched live from Yahoo Finance when the page loads, so it may take a few seconds.</li>
      </ul>
      <p class="text-muted small mb-0">This is a tracking aid, not a recommendation to buy.</p>`
  },
  goals: {
    title: "Goals",
    body: `
      <p>Set financial targets and check whether your current net worth plus monthly contributions keep you on track.</p>
      <ul class="mb-2">
        <li>Each goal projects forward from your current net worth using your <strong>monthly contribution</strong> and an assumed annual <strong>return %</strong> (compounded).</li>
        <li>Progress compares the projected value at the target date against the target amount.</li>
      </ul>
      <p class="text-muted small mb-0">Assumed returns are your inputs — actual results will vary.</p>`
  },
  forecast: {
    title: "Wealth Simulator",
    body: `
      <p>Projects your future net worth using a Geometric Brownian Motion (GBM) model per asset class, plus deterministic mortgage amortization.</p>

      <h6 class="fw-semibold mt-2 mb-1">Inputs</h6>
      <ul class="small mb-2">
        <li><strong>Stocks / ETFs</strong> value is auto-populated from your live holdings when the page loads. Hit the refresh icon <i class="bi bi-arrow-clockwise"></i> to reload it.</li>
        <li><strong>Load from Net Worth</strong> pre-fills Cash, Bonds and Mortgage amounts from your Net Worth page (manual assets + liabilities).</li>
        <li><strong>Use my history</strong> sets the Stocks annual return and volatility from your own portfolio's recorded snapshot history, replacing the defaults.</li>
        <li><strong>Annual return %</strong> per asset class is your assumed long-run real return, e.g. 8% for stocks, 4% for bonds, 1.5% for cash.</li>
        <li><strong>Volatility %</strong> (stocks only) controls how wide the confidence band is. Default 16%; your historical figure may differ.</li>
        <li><strong>Monthly contribution</strong> is added to the liquid pool each month before compounding.</li>
      </ul>

      <h6 class="fw-semibold mt-2 mb-1">Chart</h6>
      <ul class="small mb-2">
        <li>The <strong>mean</strong> line is deterministic compound growth. The <strong>shaded band</strong> is the GBM confidence interval, widening over time as uncertainty compounds.</li>
        <li><strong>95% interval</strong> (default): ~1 in 20 outcomes falls outside the band. <strong>99%</strong> is wider but still not exhaustive — extreme events aren't modelled.</li>
        <li>Net Worth = cash + stocks + bonds − remaining mortgage balance at each year.</li>
      </ul>

      <p class="text-muted small mb-0">A statistical projection, not financial advice. See the <em>Methodology</em> panel on the page for the exact GBM formula.</p>`
  },
  transactions: {
    title: "Transactions",
    body: `
      <p>Your full transaction history — buys, sells and dividends across all brokers.</p>
      <ul class="mb-2">
        <li>Filter by portfolio, type and date range.</li>
        <li>Import from a broker file or pasted text (AI extraction), or add transactions manually.</li>
        <li>Export everything as CSV or in Portfolio Dividend Tracker (PDT) XLSX format.</li>
      </ul>
      <p class="text-muted small mb-0">Cost basis for tax and P/L uses FIFO from these records.</p>`
  },
  assets: {
    title: "Assets",
    body: `
      <p>The catalogue of securities and funds you track.</p>
      <ul class="mb-2">
        <li>Each asset has a symbol, type, exchange and currency.</li>
        <li><strong>Current Price</strong> comes from Yahoo Finance, refreshed daily at 20:00 UTC.</li>
      </ul>
      <p class="text-muted small mb-0">Assets are created automatically when you import transactions, or added manually here.</p>`
  },
  portfolios: {
    title: "Portfolios",
    body: `
      <p>Broker and account groups used to organise your transactions.</p>
      <ul class="mb-2">
        <li>Each import automatically creates a matching portfolio if one does not exist.</li>
        <li>Filter transactions and analytics by portfolio elsewhere in the app.</li>
      </ul>`
  },
  importexport: {
    title: "Import / Export",
    body: `
      <p>Bring data in and out, manage cash bookings, back up your database and sync with Google Sheets.</p>
      <ul class="mb-2">
        <li><strong>File import</strong> supports IndexaCapital, MyInvestor, Mintos, Coinbase CSV and PDT XLSX; <strong>text import</strong> uses AI extraction for any broker statement.</li>
        <li><strong>Bookings</strong> are cash deposits and withdrawals (no asset).</li>
        <li><strong>Backup</strong>: <em>Download DB backup</em> saves a full SQLite snapshot of your data. <em>Restore DB backup</em> replaces the live database from a .db or .db.gz file — a pre-restore snapshot is auto-saved if <code>PFM_BACKUP_DIR</code> is set.</li>
        <li><strong>Google Sheets sync</strong> pulls from / pushes to a spreadsheet in PDT format shared with the service account.</li>
      </ul>`
  },
  networth: {
    title: "Net Worth",
    body: `
      <p>Your complete financial picture: brokerage investments plus off-brokerage assets and liabilities, all converted to EUR.</p>
      <ul class="mb-2">
        <li><strong>Investments</strong> are auto-calculated from your portfolio positions. <strong>Fixed Deposits</strong> tracks active term deposits; maturing one posts an interest transaction automatically.</li>
        <li>Add <strong>manual assets</strong> (cash, property, pension) and <strong>liabilities</strong> (mortgage, loans) to complete the picture.</li>
        <li>FIRE goals project from total net worth, not just the brokerage value.</li>
      </ul>
      <p class="text-muted small mb-0">All amounts converted to EUR at live FX rates.</p>`
  },
  research: {
    title: "Research",
    body: `
      <p>Deep-dive on any ticker: fundamentals, your position, a thesis, and an LLM valuation backed by live news.</p>
      <ul class="mb-2">
        <li><strong>Workbench</strong>: search any symbol → see Yahoo Finance fundamentals (P/E, yield, sector), your cost basis and P/L, set fair/buy/sell prices, write a thesis and get an AI analysis with news citations.</li>
        <li><strong>Save</strong> a research note to version your thinking over time. Saved price targets are pushed to your alerts so you get notified when a price is crossed.</li>
        <li><strong>Compare</strong> tab shows the latest saved note per symbol with current price vs fair value and upside.</li>
      </ul>
      <p class="text-muted small mb-0">LLM analysis is informational, not financial advice. Fundamentals from Yahoo Finance.</p>`
  },
  diagnostics: {
    title: "Diagnostics",
    body: `
      <p>Two tabs: <strong>Price Health</strong> monitors live price data; <strong>Data Quality</strong> runs automated checks on your transaction records.</p>

      <h6 class="fw-semibold mt-3 mb-1">Price Health tab</h6>
      <ul class="mb-2 small">
        <li><strong>Freshness</strong>: hours since the last price refresh. <em>Fresh</em> = under 30 h; <em>Aging</em> = 30–48 h or one or more stale holdings; <em>Very stale</em> = over 48 h.</li>
        <li><strong>Stale &amp; unpriced holdings</strong>: auto-priced assets whose latest stored price is older than 4 days, or that have never been priced. Assets with auto-price disabled (manual prices) are excluded.</li>
        <li><strong>Update history</strong>: each daily run (20:00 UTC) logs duration, updated/skipped/error counts and the list of skipped symbols.</li>
      </ul>

      <h6 class="fw-semibold mt-3 mb-1">Data Quality tab</h6>

      <p class="small mb-1"><strong>Cash &amp; Position Reconciliation</strong><br>
      For each portfolio, computes:<br>
      <code>Implied Cash = Deposits − Withdrawals − Buy costs + Sell proceeds + Dividends + Interest</code><br>
      <em>Invested Value</em> = held shares × latest stored price (EUR). <em>Total Accounted</em> = Implied Cash + Invested Value.
      Compare <em>Implied Cash</em> against your broker's actual cash balance to spot missing transactions.</p>

      <p class="small mb-1"><strong>Possible Duplicate Transactions</strong><br>
      Groups transactions by portfolio / asset / type, then flags pairs where:
      <ul class="small mb-1">
        <li><strong>LIKELY</strong> (red): same calendar date <em>and</em> quantity within ±1% <em>and</em> price within ±1%.</li>
        <li><strong>POSSIBLE</strong> (yellow): dates within ±3 days <em>and</em> quantity within ±5% <em>and</em> price within ±5%.</li>
      </ul>
      Use <em>Delete older</em> to remove the earlier of the two, or pick a specific row from the dropdown. Dismiss findings that are intentional (e.g. two separate partial fills on the same day).</p>

      <p class="small mb-1"><strong>Suspicious Patterns</strong><br>
      Five automated checks run chronologically across all transactions:
      <ul class="small mb-0">
        <li><strong>zero_price</strong> (warning): a buy or sell recorded with price = 0 (splits and dividends are exempt).</li>
        <li><strong>zero_qty</strong> (warning): a buy or sell with quantity = 0.</li>
        <li><strong>negative_position</strong> (warning): a sell that would push the running held quantity below zero — usually a missing buy.</li>
        <li><strong>dividend_before_buy</strong> (info): a dividend recorded before the first buy for that asset — may indicate an import ordering issue.</li>
        <li><strong>price_outlier</strong> (warning): a buy/sell price that is more than 5× or less than 0.2× the median price for that asset (requires at least 3 price data points). Common cause: GBX/GBP unit confusion.</li>
      </ul></p>
      <p class="text-muted small mb-0">Dismissals are stored in your browser (localStorage) and survive page reloads.</p>`
  },
  chat: {
    title: "Chat",
    body: `
      <p>Ask questions about your portfolio in natural language, or paste a broker statement to extract transactions.</p>
      <ul class="mb-2">
        <li>Answers are generated by an LLM using your portfolio data — informational, not financial advice.</li>
        <li><strong>Extract &amp; Import</strong> pulls buy/sell transactions out of pasted text for you to review before saving.</li>
      </ul>`
  },
};
