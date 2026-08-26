// Centralized help text for the Portfolio Manager web client.
// Loaded BEFORE the app scripts (pfm_core/pages/analytics/features) so its globals are available when pages render.

// Short metric explanations for tooltips (data-bs-toggle="tooltip" title=...).
window.METRIC_HELP = {
  irr: "Money-Weighted IRR: annualised return accounting for the timing and size of your buys/sells. Like the interest rate that makes your cash flows balance.",
  totalReturn: "Total Return: current value + realised gains − amount invested, divided by amount invested. Lifetime, not annualised.",
  periodReturn: "Period Return: change in portfolio value over the selected window, measured from daily snapshots.",
  hhi: "Herfindahl Index (HHI): concentration score 0–10000. Above 2500 = concentrated, below 1500 = well diversified.",
  sharpe: "Sharpe Ratio: return per unit of risk (volatility). Higher is better; >1 is good.",
  cagr:          "CAGR: Compound Annual Growth Rate — average annual growth since inception assuming constant compounding. Formula: (end/start)^(1/years) − 1. Unlike IRR it ignores contribution timing.",
  annualizedGain:"Annualized Gain: average annual profit in euros — CAGR × invested capital. A rough sense of how much the portfolio earns per year.",
  inception:     "Inception Date: date of your first transaction — the start point for CAGR.",
  sortino:       "Sortino Ratio: like Sharpe but only penalises downside volatility (negative-return days). Ignores upside swings that inflate Sharpe's denominator. >1 is good.",
  calmar:        "Calmar Ratio: annualised return ÷ max drawdown. >1 means your annual gain exceeds your worst peak-to-trough drop.",
  beta:          "Beta: sensitivity to the benchmark. 1.0 = moves with the market; >1 amplifies swings; <1 is more stable. Computed from daily snapshot returns.",
  alpha:         "Alpha: annualised excess return above what Beta predicts (CAPM, rf=0). Positive = outperformance beyond market exposure.",
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
  pnl: "P/L (Profit & Loss): current value minus cost basis, in euros. Unrealised while you still hold the position.",
  pnlPct: "P/L % (Profit & Loss): current value minus cost basis, as a percentage of cost basis.",
};

// Per-page help: what's shown, where data comes from, how it's computed.
// `body` is HTML rendered inside a Bootstrap modal.
window.PAGE_HELP = {
  dashboard: {
    title: "Dashboard",
    body: `
      <p>Your portfolio at a glance: total value, amount invested, cash, return and open positions.</p>
      <ul class="mb-2">
        <li><strong>Portfolio Value</strong> and <strong>Invested</strong> are shown in EUR. Foreign-currency holdings are converted at live FX rates.</li>
        <li><strong>Cash</strong> combines uninvested cash in your brokerage accounts (deposits/withdrawals/sells/dividends/interest not yet reinvested) with your bank account balances into one figure — not part of Portfolio Value. See the Bank Accounts card for the per-account breakdown.</li>
        <li><strong>Return</strong> defaults to lifetime (cost-basis) return. Switching to YTD / 1Y uses daily snapshots, so it only covers the period since snapshots began.</li>
        <li><strong>Top Positions</strong> and the <strong>allocation donut</strong> reflect current open positions by EUR value, plus a Cash slice.</li>
        <li>The <strong>action items</strong> banner (when shown) summarizes open items from the Action Items page — stale imports, data-quality issues, price-update errors, stale research, off-track goals, and the Net Worth setup checklist. Watchlist/price-target alerts are shown separately, in the alerts banner above it.</li>
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
      <p class="text-muted small mb-0">Hover any metric label for its definition. Net Worth Over Time chart uses daily snapshots; history starts from the first recorded snapshot.</p>
      <hr class="my-3">
      <h6 class="fw-semibold">Metrics Explained</h6>
      <p class="fw-semibold mb-1">Return metrics</p>
      <ul class="mb-2">
        <li><strong>Total Return</strong> — lifetime (current + realised − invested) / invested. Simple, no time-weighting.</li>
        <li><strong>CAGR</strong> — annualised version of Total Return. Best for headline comparisons between portfolios.</li>
        <li><strong>IRR (MWRR)</strong> — accounts for contribution timing. Use when you invest irregularly.</li>
        <li><strong>TWR (Period Return)</strong> — strips out deposits/withdrawals. Best for comparing to a benchmark.</li>
        <li><strong>Alpha</strong> — excess return above what market exposure (Beta) predicts.</li>
      </ul>
      <p class="fw-semibold mb-1">Risk metrics</p>
      <ul class="mb-2">
        <li><strong>Volatility</strong> — annualised std dev of daily returns. Higher = bumpier ride.</li>
        <li><strong>Sharpe</strong> — return per unit of total volatility. Penalises all swings equally.</li>
        <li><strong>Sortino</strong> — like Sharpe but only penalises losses. Better for portfolios with positive skew.</li>
        <li><strong>Max Drawdown</strong> — worst peak-to-trough drop in portfolio history.</li>
        <li><strong>Calmar</strong> — CAGR ÷ drawdown. Combines return and worst-case loss in one number.</li>
        <li><strong>Beta</strong> — market sensitivity. Not good or bad by itself; depends on your goals.</li>
      </ul>`
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
        <li>The "Monthly €" field is pre-filled with your current net Monthly Cash Flow (income − expenses, from the Net Worth page) when adding a new goal — still yours to edit, and different goals can assume different contributions.</li>
        <li>Click the <i class="bi bi-info-circle"></i> next to a goal's current/target figures to see the breakdown (brokerage + fixed deposits + manual assets − liabilities) behind the current net-worth number — useful when it's lower (or more negative) than you expect.</li>
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
        <li><strong>Load from Net Worth</strong> pre-fills Cash, Bonds and Mortgage amounts from your Net Worth page, plus <strong>Monthly contribution</strong> and the mortgage <strong>Monthly Payment</strong> from your Monthly Cash Flow entries (all still manually editable afterwards).</li>
        <li><strong>Use my history</strong> sets the Stocks annual return and volatility from your own portfolio's recorded snapshot history, replacing the defaults.</li>
        <li><strong>Annual return %</strong> per asset class is your assumed long-run real return, e.g. 8% for stocks, 4% for bonds, 1.5% for cash.</li>
        <li><strong>Volatility %</strong> (stocks only) controls how wide the confidence band is. Default 16%; your historical figure may differ.</li>
        <li><strong>Monthly contribution</strong> (under Stocks / ETFs) is added to the stocks bucket each month before compounding — it does not apply to cash or bonds.</li>
        <li><strong>Goals</strong> — tick any goal to overlay its target on the chart: a dashed line at the target amount, plus a marker at the target date if it falls within the years shown. A target far outside the chart's natural range (e.g. a much larger goal than the current projection) is shown as a small chip above the chart instead of a line, so it doesn't flatten everything else.</li>
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
      <p>Your current open positions with cost basis, live price and profit/loss, plus the full catalogue of securities and funds you track — all in EUR.</p>
      <ul class="mb-2">
        <li>Each asset has a symbol, type, exchange and currency. Assets are created automatically when you import transactions, or added manually here.</li>
        <li><strong>Avg Price</strong> is your FIFO cost basis; <strong>Current Price</strong> is the latest Yahoo Finance quote, refreshed daily at 20:00 UTC.</li>
        <li><strong>P/L</strong> is unrealised gain/loss on positions you still hold. Quantity, cost and P/L are blank for assets you don't currently hold — tick <strong>"Show assets with no holding"</strong> to include them in the table.</li>
        <li><strong>Research</strong> opens an LLM-generated fair-value analysis from fundamentals — informational, not advice.</li>
        <li><strong>Rebalancing</strong> compares your current allocation against your target percentages and suggests buys/sells to close the drift.</li>
      </ul>
      <p class="text-muted small mb-0">Prices from Yahoo Finance, refreshed daily at 20:00 UTC; converted to EUR at live FX rates.</p>`
  },
  portfolios: {
    title: "Portfolios",
    body: `
      <p>Broker and account groups used to organise your transactions.</p>
      <ul class="mb-2">
        <li>Each import automatically creates a matching portfolio if one does not exist.</li>
        <li>You can also create an account directly here with the <strong>Add Portfolio</strong> button — pick <strong>Brokerage</strong> for an investment account, or <strong>Bank</strong> to set up a checking/savings account for use with <strong>Spending</strong> tracking before importing anything. The account type can't be changed later, so choose carefully at creation time.</li>
        <li>Filter transactions and analytics by portfolio elsewhere in the app.</li>
      </ul>`
  },
  importexport: {
    title: "Import / Export",
    body: `
      <p>Bring data in and out, manage cash bookings, back up your database and sync with Google Sheets.</p>
      <ul class="mb-2">
        <li><strong>File import</strong> supports IndexaCapital, MyInvestor, Mintos, Coinbase CSV and PDT XLSX; <strong>text import</strong> uses AI extraction for any broker statement.</li>
        <li><strong>Import Bank Statement</strong> lets you bring in a bank/checking account CSV from this page too — the same import flow as the <strong>Spending</strong> page, just accessible here alongside your other imports.</li>
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
        <li><strong>Investments</strong> are auto-calculated from your portfolio positions. <strong>Fixed Deposits</strong> tracks active term deposits — only <em>active</em> ones count toward net worth, so click "Mark as Matured" once a deposit's term ends (this doesn't happen automatically) to record the interest and stop it inflating a stale total.</li>
        <li>Add <strong>manual assets</strong> (cash, property, pension) and <strong>liabilities</strong> (mortgage, loans) to complete the picture. If you carry a mortgage, add your home's value too — otherwise the debt is counted with nothing offsetting it, which can make net worth look far more negative than reality. For a home's value, use a current market estimate (cadastral value or a comparable local listing), not the original purchase price — it's just a rough offset, not used for anything else.</li>
        <li>For balances that change often (a bank account, say), click the € amount in the table to edit it in place — no need to delete and re-add. Bank/cash rows show when they were last updated, flagged in amber past 30 days.</li>
        <li>The <strong>Setup checklist</strong> card flags common gaps — home value, bank accounts, income, mortgage/loan payments, other recurring expenses — and overdue-but-still-active deposits; the "Run setup wizard" button walks through filling them in one at a time.</li>
        <li><strong>Monthly Cash Flow</strong> tracks rough recurring income (salary, other) vs expenses (mortgage payment, loan, rest) for your own reference — it does not currently feed Goals or Forecast projections.</li>
        <li>FIRE goals project from total net worth, not just the brokerage value.</li>
        <li>If you import bank statements on the <strong>Spending</strong> page, an "Actual" comparison appears here for the last 30 days — read-only, doesn't change the manual figures above.</li>
      </ul>
      <p class="text-muted small mb-0">All amounts converted to EUR at live FX rates.</p>`
  },
  spending: {
    title: "Spending",
    body: `
      <p>Categorized day-to-day spending imported from your bank/checking accounts — separate from the investment transactions tracked elsewhere in the app.</p>
      <ul class="mb-2">
        <li><strong>Import a statement</strong> (CSV: date, description, amount) via the button top-right, or from the <strong>Import Bank Statement</strong> card on the Import/Export page — both open the same import flow. If any imported rows look like duplicates of what's already saved, a <strong>Skip / Add anyway / Overwrite existing</strong> selector appears so you decide how to handle them before saving. Each row is matched against your saved <strong>category rules</strong> (a keyword in the description, e.g. "MERCADONA" → Groceries); anything left uncategorized can be resolved with the <strong>"Suggest categories (AI)"</strong> button — review and edit before saving. Accepting a suggestion creates a new rule automatically, so future imports for that merchant auto-categorize.</li>
        <li><strong>Transfers</strong> between your own accounts (e.g. checking → savings, or checking → a brokerage account already tracked here) are detected automatically by matching an outflow in one account to an inflow of the same amount within a few days in another — shown separately, not counted as spending. Use "Re-scan transfers" if you import a matching account's statement later.</li>
        <li>Click a row's category to change it by hand at any time.</li>
        <li>Select one or more rows with the checkbox column to <strong>bulk recategorize or delete</strong> them via the action bar that appears above the table — there's no single-row delete button, so even one row needs to be checked first.</li>
      </ul>
      <p class="text-muted small mb-0">A read-only summary of the last 30 days also appears on the Net Worth page, next to your manual Monthly Cash Flow entries, for comparison.</p>`
  },
  stressTest: {
    title: "Stress Testing — Methodology",
    body: `
      <p>Stress testing answers: <em>"If a historical crash happened today, how much would my current portfolio lose at the worst point?"</em></p>
      <p>For each open position the tool fetches the asset's <strong>actual historical return</strong> over the scenario window from Yahoo Finance, then applies that percentage to the current EUR value. Assets without a matching Yahoo symbol use an asset-class average (marked <sup>*</sup>).</p>

      <h6 class="fw-semibold mt-3 mb-1">Preset scenarios</h6>
      <table class="table table-sm small mb-3">
        <thead class="table-light"><tr><th>Scenario</th><th>Window</th><th>Duration</th><th>What happened</th></tr></thead>
        <tbody>
          <tr><td>2008 Crisis</td><td>Oct 2007 → Mar 2009</td><td>~17 months</td><td>Global banking collapse; S&amp;P 500 −57%</td></tr>
          <tr><td>2020 COVID</td><td>19 Feb → 23 Mar 2020</td><td>33 days</td><td>Pandemic panic selling; S&amp;P 500 −34%</td></tr>
          <tr><td>2022 Rates</td><td>31 Dec 2021 → 12 Oct 2022</td><td>~9.5 months</td><td>Aggressive rate hikes; S&amp;P 500 −25%, bonds −15%</td></tr>
          <tr><td>Dot-com</td><td>24 Mar 2000 → 9 Oct 2002</td><td>~2.5 years</td><td>Tech bubble burst; Nasdaq −78%</td></tr>
        </tbody>
      </table>

      <h6 class="fw-semibold mt-3 mb-1">Asset-class fallback rates (used when no Yahoo data is available)</h6>
      <table class="table table-sm small mb-3">
        <thead class="table-light"><tr><th>Asset type</th><th>2008</th><th>2020</th><th>2022</th><th>Dot-com</th></tr></thead>
        <tbody>
          <tr><td>Stock / ETF / Index</td><td class="text-danger">−50%</td><td class="text-danger">−32%</td><td class="text-danger">−22%</td><td class="text-danger">−60%</td></tr>
          <tr><td>Mutual fund</td><td class="text-danger">−40%</td><td class="text-danger">−25%</td><td class="text-danger">−18%</td><td class="text-danger">−45%</td></tr>
          <tr><td>Bond</td><td class="text-danger">−5%</td><td class="text-success">+5%</td><td class="text-danger">−15%</td><td class="text-success">+5%</td></tr>
          <tr><td>Crypto</td><td>0%</td><td class="text-danger">−50%</td><td class="text-danger">−65%</td><td>0%</td></tr>
          <tr><td>Commodity</td><td class="text-danger">−30%</td><td class="text-danger">−20%</td><td class="text-success">+20%</td><td class="text-danger">−15%</td></tr>
          <tr><td>Cash</td><td>0%</td><td>0%</td><td>0%</td><td>0%</td></tr>
        </tbody>
      </table>
      <p class="small text-muted">Crypto fallback is 0% for 2008 and dot-com because Bitcoin did not exist (no data, not because it was unaffected).</p>

      <h6 class="fw-semibold mt-3 mb-1">Custom date range</h6>
      <p class="small">Enter any start and end date to replay that window against your current holdings. Custom runs are <strong>not cached</strong> — each click fetches live data from Yahoo Finance. The S&amp;P 500 return over that window is used as the equity fallback for assets without historical data.</p>

      <h6 class="fw-semibold mt-3 mb-1">Limitations</h6>
      <ul class="small mb-2">
        <li><strong>Snapshot, not simulation.</strong> The result is the loss at the crash bottom. It does not model the recovery, nor any action you might have taken (rebalancing, stop-losses, new contributions).</li>
        <li><strong>Dividends ignored.</strong> Income received during the crash window is not counted.</li>
        <li><strong>Today's holdings, not historical ones.</strong> The tool applies past returns to your <em>current</em> positions — it does not reconstruct what you actually held during that period.</li>
        <li><strong>Preset results cached 7 days.</strong> The four named scenarios are cached in the database; custom ranges always run live.</li>
        <li><strong>FX effects not modelled.</strong> All EUR conversions use current FX rates, not crisis-era rates.</li>
      </ul>
      <p class="text-muted small mb-0">Informational only, not financial advice.</p>`
  },
  research: {
    title: "Research",
    body: `
      <p>Deep-dive on any ticker: fundamentals, your position, a thesis, and an LLM valuation backed by live news.</p>
      <h6 class="fw-semibold mt-3 mb-1">Portfolio Health</h6>
      <p>Click <strong>Run Analysis</strong> to get a scored health report across five dimensions (Diversification, Risk/Return, Income, Fees, Tax Efficiency), each rated 1–10 with an explanation and a prioritised action list. Results are cached — adjust the cache duration in Settings → Portfolio Advisor.</p>
      <h6 class="fw-semibold mt-3 mb-1">Workbench</h6>
      <ul class="mb-2">
        <li>Search any symbol → see Yahoo Finance fundamentals (P/E, yield, sector), your cost basis and P/L, set fair/buy/sell prices, write a thesis and get an AI analysis with news citations.</li>
        <li><strong>Save</strong> a research note to version your thinking over time. Saved price targets are pushed to your alerts so you get notified when a price is crossed.</li>
      </ul>
      <h6 class="fw-semibold mt-3 mb-1">Compare</h6>
      <p class="mb-0">Shows the latest saved note per symbol with current price vs fair value and upside %.</p>
      <p class="text-muted small mt-2 mb-0">LLM analysis is informational, not financial advice. Fundamentals from Yahoo Finance.</p>`
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
  actionitems: {
    title: "Action Items",
    body: `
      <p>A single, dismissible checklist of everything that needs your attention across the app — pulled from Diagnostics, Net Worth, Goals, Watchlist, and Research so you don't have to visit each page separately.</p>
      <ul class="mb-2 small">
        <li><strong>Broker Imports</strong>: a broker/portfolio with transaction history but no activity in 60+ days.</li>
        <li><strong>Data Quality</strong>: possible duplicate transactions or suspicious patterns (same checks as the Diagnostics Data Quality tab).</li>
        <li><strong>Errors</strong>: the most recent price-update run had failures, or held positions haven't been re-valued in 90+ days.</li>
        <li><strong>Goals</strong>: a savings goal is projected to miss its target.</li>
        <li><strong>Price Alerts</strong>: a watchlist symbol dropped into its buy zone, or a held position crossed a saved price target.</li>
        <li><strong>Net Worth</strong>: setup gaps (missing bank balance, income, etc.) or a fixed deposit past maturity — same checklist as the Net Worth page.</li>
      </ul>
      <p class="text-muted small mb-0">Dismissing an item hides it until the underlying issue changes (e.g. a new failing price-update run gets its own item).</p>`
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
