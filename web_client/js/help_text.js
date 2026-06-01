// Centralized help text for the Portfolio Manager web client.
// Loaded BEFORE portfolio_debug.js so the globals are available when pages render.

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
      <p>Deeper performance, risk and cost analysis of your portfolio.</p>
      <ul class="mb-2">
        <li><strong>Performance</strong> compares your total/period return against a market benchmark over the selected window.</li>
        <li><strong>Net Worth Over Time</strong> and the period returns are built from daily snapshots, so history starts when snapshots began accumulating.</li>
        <li><strong>Tax Estimate</strong> applies the Spanish IRPF savings-base brackets to FIFO realised gains. It is an estimate, not tax advice.</li>
        <li><strong>Diversification</strong>, <strong>Risk</strong> (volatility, drawdown, Sharpe) and <strong>Fees</strong> summarise concentration, swings and costs.</li>
      </ul>
      <p class="text-muted small mb-0">All values in EUR. Prices from Yahoo Finance, refreshed daily at 20:00 UTC. Hover any metric label for its definition.</p>`
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
      <ul class="mb-2">
        <li>The <strong>mean</strong> path is compound growth; the <strong>confidence bands</strong> widen over time with each asset's volatility.</li>
        <li>Your current stocks/ETF value is pre-filled from your live holdings; add cash, bonds and a mortgage to model net worth.</li>
        <li>Net Worth = liquid assets (cash + stocks + bonds) − remaining mortgage at each year.</li>
      </ul>
      <p class="text-muted small mb-0">A statistical projection, not a guarantee. See the Methodology panel for the formulas.</p>`
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
      <p>Bring data in and out, manage cash bookings and sync with Google Sheets.</p>
      <ul class="mb-2">
        <li><strong>File import</strong> supports IndexaCapital and Coinbase CSV and PDT XLSX; <strong>text import</strong> uses AI extraction for any broker statement.</li>
        <li><strong>Bookings</strong> are cash deposits and withdrawals (no asset).</li>
        <li><strong>Google Sheets sync</strong> pulls from / pushes to a spreadsheet in PDT format shared with the service account.</li>
      </ul>`
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
