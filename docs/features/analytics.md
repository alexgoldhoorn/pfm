# Analytics, fund look-through and PDF reports

> Moved out of `CLAUDE.md` on 2026-09-25 so it isn't loaded into every session. CLAUDE.md keeps the must-know gotchas and links here; this file is the full reference. "See the X section" means the matching file in `docs/features/`, or CLAUDE.md.

### Analytics API (`portf_server/routers/analytics.py` + `services/analytics_service.py`)
- `GET /api/v1/analytics/performance?benchmark=VWCE.DE&period=all|ytd|1m|1y|3y|5y` — lifetime `invested_eur` (cost of open positions), `capital_invested_eur` (all purchases), `current_value_eur`, `realised_pnl_eur`, `unrealised_pnl_eur`, `income_eur` (dividends + interest), `total_gain_eur`, `total_return_pct`, `money_weighted_irr_pct`, `inception_date`; time-weighted `period_return_pct` (for `all`: since the first trade, = `twr_since_inception_pct`), `annualised_twr_pct`; `benchmark`, `benchmark_label`, `benchmark_total_return`, `benchmark_return_pct` (in EUR over the same window). See "Performance metrics" below.
- `GET /api/v1/analytics/networth-history` | `POST /api/v1/analytics/snapshot` | `POST /api/v1/analytics/backfill-snapshots[?force=]`
- `period_return` is a **time-weighted return** (chains daily returns, removes contributions via cost-basis delta)
- `GET /api/v1/analytics/portfolio-comparison` — per-portfolio figures from the same `compute_performance` (it used to multiply an already-percent IRR by 100).
- `GET /api/v1/analytics/progress?benchmark=VWCE.DE` — `contributions` (`net_contributions_eur`, `growth_eur`, monthly `months`), `income_eur`, `calendar_years` (`year`, `return_pct`, `benchmark_return_pct`, `inflation_pct`, `real_return_pct`, `start`/`end`, `partial`), `monthly_returns` (`{YYYY: {MM: pct}}`), `real` (`irr_pct`, `inflation_annual_pct`, `real_irr_pct`, `inflation_to`, `source`), `latent_tax`, `savings`, `notes`. Plain `def`. See "Progress metrics" below.
- `GET /api/v1/analytics/dividends` — every amount in EUR at the payment date's rate; yield-on-cost over the EUR cost basis.
- `GET /api/v1/analytics/tax-estimate?year=` — IRPF savings base (realised gains + dividends + interest); `irpf_savings_tax()` progressive brackets (19/21/23/27/28%)
- `GET /api/v1/analytics/diversification` — sector/country/currency/type + Herfindahl HHI (slow, fetches yfinance), plus fund look-through: `by_region_equity`, `by_currency_exposure`, and a `coverage` block (`classified_pct`, `sector_classified_pct`, `unprofiled`, `stale_profiles`). See "Fund look-through" section below.
- `GET /api/v1/analytics/fund-overlap` — held funds grouped by one of three `kind`s (`portf_manager/services/exposure.py::find_fund_overlaps`, evaluated and reported in this order): shared index family (`"consolidation_candidate"` — two-plus held funds on the same `benchmark_key` family, worth merging), nesting (`"informational"` — a narrower fund's index sits inside a broader held fund's, e.g. a China Tech fund inside a World fund; a deliberate tilt, not a problem), and, only for the subset of held funds with **no** `benchmark_key` on either side, cosine similarity of their region+sector weight maps above `SIMILARITY_THRESHOLD` (`"similar"`) — the fallback for funds a benchmark can't identify. Each group carries `members`, `combined_pct`/`combined_value_eur`, `reason`, and `transferable` (both funds, so a Spanish `traspaso` avoids realising a gain). Plain `def`, same `compute_exposure()` call as `/diversification`.
- `GET /api/v1/analytics/risk?benchmark=VWCE.DE&window=all|1y` — `max_drawdown_pct`, `current_drawdown_pct`, `volatility_pct`, `sharpe_ratio`, `sortino_ratio`, `calmar_ratio`, `risk_free_rate_pct`/`risk_free_source`, `annualised_return_pct`, `period_return_pct`, `benchmark_return_pct`/`benchmark_annualised_return_pct` (same days, EUR), `benchmark_label`, `benchmark_total_return`, `beta`, `alpha_pct`, `beta_observations` (weeks), `start_date`/`end_date`, `history_days`, `periods_per_year`, `low_confidence`, `snapshots_used`. Plain `def` (threadpool). See "Risk metrics" below.
- `GET /api/v1/analytics/fees` — plain `def` (blocking `_fx()` calls); amounts converted to EUR at **current** FX via `_fx()`
- `GET /api/v1/analytics/tax-report?year=` — plain `def`; per-lot FIFO (full-history: prior-year sells consume lots, fees in amounts — see Spanish tax gotcha) + withholding; all amounts converted to EUR at **transaction-date FX** via `_fx_on()` (proceeds at sell-date, cost basis at purchase-date; dividends/withholding at dividend date); withholding counts the `tax` field on **both dividend and interest** rows (`dividend_withholding_eur` + `interest_withholding_eur`); response lot keys: `symbol`, `quantity`, `proceeds`, `cost_basis`, `gain_loss`, `proceeds_eur`, `cost_basis_eur`, `gain_loss_eur`, `purchase_date`; **`TaxTransaction` internal fields use `sell_quantity`/`sell_amount`/`purchase_amount` — different from response keys**. Its body is `_build_tax_report_data(db, yr)`, a plain function shared with the PDF export below so the two can't disagree.
- `GET /api/v1/analytics/tax-report/pdf?year=` — filing-ready Spanish IRPF PDF built with reportlab (`portf_manager/services/pdf_reports.py::build_tax_report_pdf`): the same per-lot FIFO table as `/tax-report` (no long/short-term split — Spain taxes all capital gains together in the base del ahorro, unlike the US) plus dividend/interest withholding and, when `current_year_savings_components`/`irpf_savings_tax` succeed, an estimated-tax line. Not tax advice. Web: "Download PDF" button next to the existing CSV download on the Analytics page's Tax tab.
- `GET /api/v1/analytics/data-freshness?stale_days=4` — price freshness + stale asset list; powers dashboard chip + alerts banner
- **Data Quality**: `GET /api/v1/analytics/dq/reconciliation|duplicates|suspicious` — pure DB checks (plain `def`). Powers Diagnostics page Data Quality tab. Dismissals in `localStorage["pfmDismissedIssues"]`.
- `services/tax_rates.py` — IRPF brackets; `GET /api/v1/public/summary` off unless `PORTF_PUBLIC_VIEW=true`
- Auth: `POST /api/v1/auth/login-key`
- Cron: `portf-price-alerts.sh` (20:05), `portf-monthly-report.sh` (1st of month 09:00)

### Risk metrics (`portf_manager/services/risk_metrics.py`)

One implementation, used by `/analytics/risk`, the Portfolio Health advisor
(`gather_risk`), the chat `get_risk` tool, the PDF report and the MCP `risk` tool.

- **Flow-adjusted daily returns.** Snapshots hold the value and cost of open
  positions only, so a raw day-over-day change counts every buy as a gain and
  every sell as a loss. Each day's return is
  `(V_t − V_{t−1} − F) / (V_{t−1} + max(F, 0))`, with `F = ΔCost − realised gain`
  booked by sells since the previous snapshot. Realised gain comes from
  `compute_positions(..., on_sell=...)`, so position math stays in one place.
- **Why cost basis and not transaction dates:** trades imported after a snapshot
  was recorded, with earlier dates, are missing from that snapshot. The first
  snapshot that includes them jumps in value *and* cost together. A transaction-
  dated flow sits on the wrong day and turns the import into a spike (this is
  what made the old figures read vol 47%, Sharpe 2.5, alpha +368%). Known limit:
  the *unrealised* gain late-imported lots built up before the import still lands
  on the import day. Re-running the backfill with `force=true` rebuilds history
  from current transactions, but also overwrites the cron's own snapshots.
- **Annualisation uses observed frequency.** Snapshots are calendar-daily
  (weekends included), so `periods_per_year = returns / years spanned` (~365),
  not 252.
- **Windows:** `all` (every snapshot) or `1y` (returns dated after today − 365).
  Drawdowns are measured within the window, so `current_drawdown_pct` is the
  distance below the window's high. `annualised_return_pct` and Calmar need
  ≥ 350 days; `low_confidence` is true below that.
- **Risk-free rate = average €STR over the window** (`services/risk_free.py`,
  ECB Data Portal series `EST.B.EU000A2X2A25.WT`, cached 24h). Sharpe = <!-- allow-financial: ECB €STR series key, not a holding -->
  `(mean × periods/yr − rf) / volatility` (Sharpe 1994). If the ECB is
  unreachable the rate comes from the `risk_free_rate_pct` app setting, else
  2%, and `risk_free_source` says so (`ecb_estr` | `setting` | `default`).
  Unit tests stub `_fetch_estr` autouse in `tests/conftest.py`.
- **Sortino uses the downside deviation** (Sortino & Price 1994): root mean
  square of shortfalls below the risk-free rate over **all** periods. The old
  code took the standard deviation of the negative days only, around their own
  mean, which overstated the ratio.
- **Beta/alpha on weekly EUR returns.** The benchmark's closes are converted to
  EUR at each day's rate. Daily returns don't line up: the portfolio is
  calendar-daily (crypto moves on weekends) while the index trades 5 days a
  week and US indices close hours after European funds, which biases a daily
  beta towards zero (live: 0.40–0.67 against the S&P 500). Both series are
  compounded into ISO weeks (`weekly_from_daily`/`weekly_from_closes`; partial
  weeks dropped) and beta is computed on the weeks they share. Alpha is
  Jensen's: `(Rp − rf) − β (Rb − rf)` on annualised returns.
- Calmar uses the selected window, not the 36 months of Young (1991); the help
  text says so.

**Ratings (web).** `METRIC_RATINGS` + `rateMetric(key, value, {lowConfidence})`
in `pfm_core.js` hold the only good/OK/bad bands (Sharpe, Sortino, Calmar, current
drawdown, return vs benchmark, alpha) — rules of thumb, and the help text says
so. Volatility, beta and max drawdown are `neutral`: descriptive words, no
red/green, because they depend on risk appetite. Volatility is labelled with the
EU PRIIPs SRI market-risk class (Delegated Regulation (EU) 2017/653, Annex II:
< 0.5 / 5 / 12 / 20 / 30 / 80%), the 1–7 scale on every fund KID; the official
class uses 5 years of returns, so ours is an indication.

### Performance metrics (`portf_manager/services/performance.py`)

One implementation (`compute_performance`) behind `/analytics/performance`,
`/analytics/portfolio-comparison`, the Portfolio Health advisor, the chat
`get_performance` tool and the PDF report. Definitions follow CFA Institute
GIPS 2020.

- **Currency rule.** What was *paid* converts at the rate on the day it was paid
  (`to_eur_transactions` → `compute_positions` gives the EUR cost basis and
  realised gains); what is *held* converts at the valuation day's rate. The gap
  is the currency gain/loss, which is real return for a euro investor. Before
  2026-10 cost used today's rate, which (a) hid most of the FX effect in total
  return, and (b) made every snapshot's cost move with FX, which the
  flow-adjusted returns then treated as money in/out — stripping currency moves
  out of TWR, volatility and drawdowns too.
- **Total return** = `(unrealised + realised + dividends + interest) ÷
  capital_invested_eur` (all purchases). Not annualised. Trade fees are inside
  the buy/sell amounts; ongoing fund costs are inside fund prices; platform
  fees charged in cash (e.g. Indexa management/custody) are not modelled.
- **IRR** (money-weighted): buys negative, sells/dividends/interest positive,
  current value as the final inflow; bisection.
- **TWR since inception** (`lifetime_twr`): chained flow-adjusted daily returns,
  `None` when the snapshot history starts >10 days after the first trade
  (rebuild history), annualised only with ≥ 350 days.
- **CAGR and `annualized_gain_eur` were removed.** `(value + realised) / cost`
  over the years since the first trade is not a growth rate for a portfolio
  that keeps receiving money (most of it was invested for far less time).
- **Benchmarks** (`PERFORMANCE_BENCHMARKS`): default `VWCE.DE` (FTSE All-World,
  EUR, accumulating — dividends reinvested). Every benchmark is converted to EUR
  at each day's rate (`to_eur_closes`); a ticker outside the list has its
  currency looked up once (`fast_info`, cached 30 days). Price indices are
  labelled "price only" — they leave out ~1.5–2%/yr of dividends. The web
  `PREFS.benchmark` default moved from `^GSPC` to `VWCE.DE`, with a one-time
  migration of a stored `^GSPC` (`benchmarkV2`).
- **Snapshots** (`POST /snapshot`, `_run_backfill`) share `portfolio_totals`:
  cost at transaction-date FX, value at the day's FX. The backfill prefers the
  app's own stored price for a day (what the cron recorded), then the Yahoo
  close via the asset's `ticker` (ISIN-keyed assets used to get nothing), then
  the last earlier price; with none, the position is valued at cost. The
  web "Rebuild history" button now runs `force=true`: snapshots recorded before
  the currency fix carry today's-FX cost, and mixing them with new ones would
  read the switch as a flow.
- **Wealth Simulator "Use my history"** subtracts 2% assumed inflation
  (`ASSUMED_INFLATION_PCT`, ECB target) from the nominal IRR, because the
  simulator's inputs are real returns.
`metricTile({...})` renders one tile with the word beside the colour and the
bands in the tooltip. Low-confidence history greys rated tiles out.

### Progress metrics (`portf_manager/services/progress.py`, `services/inflation.py`)

Numbers a long-term, learning investor needs beyond return and risk.

- **Contributions vs growth.** Net contributions = purchases − sale proceeds,
  each at its own date's FX; growth = value − net contributions (= realised +
  unrealised gains). Dividends/interest are reported apart (`income_eur`),
  since they leave the positions as cash. Monthly series from the first trade;
  a month's value is its last snapshot, the current month shows today's value.
- **Calendar-year and monthly TWR** chain the same flow-adjusted daily returns
  as the risk metrics. A year runs from the previous year's last snapshot;
  `partial` marks a late start or the running year. The benchmark comes from
  `_benchmark_closes_eur` (EUR) over the same window (`window_return`).
- **Real returns** use the Fisher relation `(1 + r)/(1 + π) − 1` with Spanish
  HICP (ECB Data Portal series `ICP.M.ES.N.000000.4.INX`, cached 24h).
  Calendar year: December-to-December, or to the latest published month for
  the running year. Real IRR: inflation annualised from the month before the
  first trade. **No fallback**: without HICP the real fields are null and
  `notes` says so; unit tests stub `_fetch_hicp` autouse.
- **Latent tax** = `savings_tax(realised_ytd + unrealised, income_ytd) −
  savings_tax(realised_ytd, income_ytd)` — the IRPF savings base on top of what
  this year already owes. `savings_tax` (moved here from the tax optimizer,
  which now imports it) nets gains and losses and lets a net loss offset 25%
  of dividend/interest income (art. 49 LIRPF). Unrealised is on average cost at
  transaction-date FX. `fund_unrealised_gain_eur` uses `is_fund_like` (type
  `mutual_fund`/`index`, exchange `"Funds"`, or a fund-like name; never ETFs)
  for gains a traspaso can move tax-free.
- **Savings rate and emergency fund** from non-transfer bank rows (EUR at
  today's rate, the `/spending/trend` convention) over the last 12 **complete
  months that have imports** — the running month and empty months would read
  as "spent nothing". Cash = bank balances + `cash`-category manual assets;
  `None` when neither exists, never 0.
- Bands (`METRIC_RATINGS`: `emergencyMonths` 3/6, `savingsRate` 10/20 —
  the 50/30/20 rule —, `realReturn` 0/3) are rules of thumb, said in the help.
- The MCP `performance` tool appends a PROGRESS block for `period="all"`.

### Fund look-through (`portf_manager/services/exposure.py` + `services/fund_profiles.py` + `services/benchmarks.py`, db v30)

A fund or ETF used to count as one opaque line in every exposure breakdown —
a global-equity fund and a US-only fund looked identical, and two funds
tracking the same index couldn't be spotted as duplicates. `fund_profiles`
(one row per `etf`/`mutual_fund`/`index` asset, see the v30 migration note
above) stores a weight map that splits that fund's value across asset class,
region and sector, so its underlying holdings — not just its own ticker —
count toward the totals.

- **Region weights come from `benchmarks.json` (`portf_manager/data/`), never
  from yfinance.** yfinance has no geography for a fund — `fast_info`/
  `get_info()` return a sector breakdown for a fund's *own* holdings at best,
  and nothing at all for a UCITS share class. Region weights are instead
  looked up by matching a fund to the closest tracked index (`benchmark_key`,
  e.g. `msci_world`, `msci_em`, `sp500`) and copying that index's published
  regional split. `benchmarks.json` is **hand-maintained**, each entry
  carrying its own `as_of` date — index weights drift a few points a year, so
  `fund_profiles.as_of` (copied from the benchmark at refresh time, or set by
  hand on a manual/LLM profile) is what `is_stale()` checks against
  `STALE_AFTER_DAYS` (365) to flag a profile worth refreshing.
- Sectors, where available, **do** come from yfinance (a live ticker lookup
  against the fund's own `ticker`), which is why a fund with no resolvable
  ticker — for example, a UCITS share class Yahoo has no listing for — gets
  full region/asset-class coverage from its benchmark but empty `sectors`.
  That's expected, not a bug: don't chase empty sectors on such a fund by
  inventing a ticker.
- `POST /{asset_id}/refresh {"benchmark_key": "..."}` copies a benchmark's
  `asset_class`/`regions` weights onto the fund's profile (plus a live sector
  fetch if it has a ticker). `POST /{asset_id}/suggest` is the fallback for an
  index not in `benchmarks.json`: an LLM drafts a weight map from the fund's
  name/description for **manual review before saving** via `PUT` — never
  auto-applied. A currency-hedged share class (e.g. "... Eur Hdg") needs a
  manual `PUT` follow-up setting `currency_hedged: true` and
  `hedge_currency`, since the benchmark table has no notion of a particular
  share class being hedged — without it, a EUR-hedged global bond fund
  reports as USD/JPY currency exposure.
- **A fund with no profile is named, not folded into "Unknown".**
  `coverage.unprofiled` lists each one (`asset_id`, `symbol`, `name`,
  `value_eur`) so a gap in coverage is visible and actionable rather than
  silently understating every breakdown; `classified_pct`/
  `sector_classified_pct` are the EUR-weighted share of the portfolio that
  *does* have a profile.
- **`by_currency` (quote currency) and `by_currency_exposure` (look-through)
  answer different questions and are not interchangeable.** `by_currency`
  groups holdings by the currency their price is quoted in — a EUR-domiciled
  UCITS ETF tracking the S&P 500 shows as EUR even though its underlying
  companies earn and trade in USD. `by_currency_exposure` instead derives
  currency from each fund's *region* weights (region → currency, e.g. North
  America → USD, Japan → JPY; emerging markets have no single currency and
  render as an `"EM basket"` bucket) unless the profile is marked
  `currency_hedged`, in which case that fund's exposure is attributed to
  `hedge_currency` instead. This is why `by_currency_exposure` is
  **approximate** (region-to-currency is a coarse proxy, and per-fund FX
  hedging beyond the explicit `currency_hedged` flag isn't modelled) and
  usually shows far more USD than the quote-currency view for a portfolio
  built from EUR-domiciled global-equity funds.
- **One implementation backs both the endpoint and Portfolio Health.**
  `exposure.compute_exposure(db, fx=...)` is called by both
  `GET /api/v1/analytics/diversification` and
  `portfolio_advisor.gather_diversification()` (which feeds the LLM-scored
  Portfolio Health report), so the two can't disagree about a fund's
  look-through breakdown — a repeat of the `dq_reconciliation`-vs-`/dq/*`
  drift this pattern is meant to avoid elsewhere in the codebase.
  `find_fund_overlaps()` (same module) groups the per-fund list from
  `compute_exposure()` by shared `benchmark_key`/index family for
  `/analytics/fund-overlap` and the `check_fund_exposure` Action Items check.

### Reports API (`portf_server/routers/reports.py` + `services/pdf_reports.py`)
- `GET /api/v1/reports/portfolio?sections=networth,performance,diversification,health` — general portfolio-report PDF, `sections` a comma-separated subset (default: all four; unknown value → 400). Gathers data by calling other routers' endpoint functions **directly, in-process**, the same `api_key_info={}` reuse pattern `action_items.py` uses for its checks — `networth.get_networth` + `portfolios.get_holdings` (net worth), `analytics.get_performance`/`get_risk` (performance & risk), `analytics.get_diversification` (fund look-through) — so the PDF can never disagree with the page/endpoint each section summarises.
- **The Portfolio Health section reads `db.cache_get("portf:advisor:all")` only — it never triggers a fresh LLM run.** Running the advisor synchronously inside a PDF request risked the same `proxy_read_timeout` class of failure documented for the Spending AI-suggest batch cap; if nothing is cached yet, the PDF says so and points at the Research page instead of blocking.
- Rendering lives in `portf_manager/services/pdf_reports.py` (`build_portfolio_report_pdf` / `build_tax_report_pdf`, see the Analytics API section above for the tax one) — reportlab, already a project dependency, pure-Python (no system libraries added to the Docker image, unlike e.g. WeasyPrint). One page break per section.
- Web: "Report PDF" button on the Analytics page header opens `#portfolioReportModal` (a section checkbox picker), downloads via the existing `apiClient.downloadBlob()`.
