# Analytics, fund look-through and PDF reports

> Moved out of `CLAUDE.md` on 2026-09-25 so it isn't loaded into every session. CLAUDE.md keeps the must-know gotchas and links here; this file is the full reference. "See the X section" means the matching file in `docs/features/`, or CLAUDE.md.

### Analytics API (`portf_server/routers/analytics.py` + `services/analytics_service.py`)
- `GET /api/v1/analytics/performance?benchmark=^GSPC` — IRR, benchmark, `inception_date`, `cagr_pct`, `annualized_gain_eur`
- `GET /api/v1/analytics/networth-history` | `POST /api/v1/analytics/snapshot` | `POST /api/v1/analytics/backfill-snapshots[?force=]`
- `period_return` is a **time-weighted return** (chains daily returns, removes contributions via cost-basis delta)
- `GET /api/v1/analytics/tax-estimate?year=` — IRPF savings base (realised gains + dividends + interest); `irpf_savings_tax()` progressive brackets (19/21/23/27/28%)
- `GET /api/v1/analytics/diversification` — sector/country/currency/type + Herfindahl HHI (slow, fetches yfinance), plus fund look-through: `by_region_equity`, `by_currency_exposure`, and a `coverage` block (`classified_pct`, `sector_classified_pct`, `unprofiled`, `stale_profiles`). See "Fund look-through" section below.
- `GET /api/v1/analytics/fund-overlap` — held funds grouped by one of three `kind`s (`portf_manager/services/exposure.py::find_fund_overlaps`, evaluated and reported in this order): shared index family (`"consolidation_candidate"` — two-plus held funds on the same `benchmark_key` family, worth merging), nesting (`"informational"` — a narrower fund's index sits inside a broader held fund's, e.g. a China Tech fund inside a World fund; a deliberate tilt, not a problem), and, only for the subset of held funds with **no** `benchmark_key` on either side, cosine similarity of their region+sector weight maps above `SIMILARITY_THRESHOLD` (`"similar"`) — the fallback for funds a benchmark can't identify. Each group carries `members`, `combined_pct`/`combined_value_eur`, `reason`, and `transferable` (both funds, so a Spanish `traspaso` avoids realising a gain). Plain `def`, same `compute_exposure()` call as `/diversification`.
- `GET /api/v1/analytics/risk?benchmark=^GSPC&window=all|1y` — `max_drawdown_pct`, `current_drawdown_pct`, `volatility_pct`, `sharpe_ratio`, `sortino_ratio`, `calmar_ratio`, `annualised_return_pct`, `period_return_pct`, `benchmark_return_pct`/`benchmark_annualised_return_pct` (same days), `beta`, `alpha_pct`, `start_date`/`end_date`, `history_days`, `periods_per_year`, `low_confidence`, `snapshots_used`. Plain `def` (threadpool). See "Risk metrics" below.
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
- rf = 0. Beta/alpha align flow-adjusted returns with benchmark daily closes over
  the same window; alpha is `annualised − beta × benchmark annualised`.

**Ratings (web).** `METRIC_RATINGS` + `rateMetric(key, value, {lowConfidence})`
in `pfm_core.js` hold the only good/OK/bad bands (Sharpe, Sortino, Calmar, current
drawdown, return vs benchmark, alpha). Volatility, beta and max drawdown are
`neutral`: descriptive words, no red/green, because they depend on risk appetite.
`metricTile({...})` renders one tile with the word beside the colour and the
bands in the tooltip. Low-confidence history greys rated tiles out.

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
