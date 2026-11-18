# Web client

> Moved out of `CLAUDE.md` on 2026-09-25 so it isn't loaded into every session. CLAUDE.md keeps the must-know gotchas and links here; this file is the full reference. "See the X section" means the matching file in `docs/features/`, or CLAUDE.md.

## Web Client (`web_client/`)
Single `index.html` + five JS files (no build step), **must load in order**: `help_text.js` → `pfm_core.js` → `pfm_pages.js` → `pfm_analytics.js` → `pfm_features.js`. They share one global scope.

- `pfm_core.js`: prefs, `Fmt`, `esc`, `AssetSearch`, API + modal managers (`createAPIClient`, `createModalManager`), shared chart helpers, `notify`/`confirmDialog`, `openChatWithContext()`, Diagnostics page (`loadDiagnosticsPage`)
- `pfm_pages.js`: page manager (`createPageManager`: dashboard, transactions, assets — positions + catalogue merged, see below), help/resources
- `pfm_analytics.js`: net-worth/dividend/analytics/diversification charts, Net Worth page
- `pfm_features.js`: nav + auth managers (`createNavigationManager`, `createAuthManager`, the page switch), watchlist, goals, action items, chat, portfolios, import/export, forecast, rebalance, research, spending, budget, next euro, settings + `DOMContentLoaded` bootstrap

When rendering errors with `innerHTML`, always escape dynamic text first
(`esc(err.message)` / `esc(e.message)`). Imported data and backend error details
are untrusted input; interpolating them raw into HTML is an XSS sink.

`web_client/nginx.conf` sets a CSP and basic browser hardening headers. Current
CSP allows local assets plus jsDelivr (`cdn.jsdelivr.net`) for external
Bootstrap/Chart.js/Marked dependencies and permits inline scripts/styles used by
the current static app shell.

`openChatWithContext(threadName, openingMessage)` — sets `window._chatPendingContext`, navigates to chat; used by Research ("Chat about this"), Portfolio Health ("Discuss with AI"), the Action Items page and dashboard group ("Ask AI"), and Diagnostics ("Discuss with AI" in the header, a robot button per Data Quality finding and per stale holding) and the Wealth Simulator ("Ask AI", `scenarioChatMessage`).

**AI Chat page (`setupChatPage`, 2026-10-23).** One `.chat-panel` (header, `#chatMessages.chat-thread`, `.chat-composer`), sized to the viewport (`max(420px, 100vh − 120px)`; `100dvh − 160px` under 992px, where the mobile top bar shows; full height under 576px, see Chat on phones). Each turn is a `.chat-row` with an avatar, a bubble (`chat-bubble-user` / `chat-bubble-assistant` / `chat-bubble-error` — errors never enter the export) and a `.chat-meta` line (time, model with the tier in its title, copy button) plus the `chatToolsHtml(tools_used)` chip ("Used N tools: …", a `<details>` listing each call's arguments and ms). While waiting, `showTyping()` adds a three-dot bubble that `hideTyping()` removes before the answer renders. **Keys:** `chatIsSendKey(e)` — Shift+Enter or Ctrl/Cmd+Enter sends, Enter is a newline; the textarea auto-grows to 12rem. **Model picker** `#chatModelSelect`: filled from `GET /llm/chat/models` (`tier · model` per option; hidden when the server has no provider or no such endpoint), the choice saved in `localStorage.pfmChatTier` and sent as `tier` on every `sendChat(text, sessionId, tier)`. **Auto-title:** `applySessionName(data.session_name)` renames the thread in the sidebar and header after a turn. **Export** dropdown (`#chatExportMenu`, enabled once the thread has a turn): Markdown (`chatSessionToMarkdown`), a self-contained web page (`chatSessionToHtml`, rendered with `marked` when loaded) or print/PDF (the same HTML in a new window + `window.print()`), named by `chatExportFilename(name, ext)`; the source is `currentMessages`, which mirrors what is on screen (reloaded threads included, with their stored `model`/`tools_used`). The empty state's `.chat-suggestion` chips send their text as a question. All four helpers are `window.`-exported and tested in `js/tests/chat.test.mjs`. `sendMessage(text)` is the one turn routine, shared by the composer and the pending-context openers.

**Wealth Simulator** (`pfm_features.js`, `setupForecastPage`): the projection runs client-side (the saved scenarios, their tracking and the Dashboard's primary-scenario card are in `wealth-simulator.md`) — `projectAccount(startAmount, annualRatePct, volatility, years, sigma, monthlyContribution)` models each asset class as GBM plus an optional ordinary-annuity monthly contribution (stocks bucket only, via `fcStocksContribution`); `runProjection(...)` adds deterministic mortgage amortization; `renderChart(projResult, totalStarting, years, goals)` draws the SVG. `computeGoalOverlays(goals, naturalMin, naturalMax, years)` (pure, unit-tested) decides per selected goal whether its target renders as an in-chart dashed line/marker or — when far outside the projection's natural range — an off-chart chip in `#fcGoalChips`, so a large goal (e.g. €1M) can't flatten a much smaller projection. `projectAccount`/`runProjection` are module-scope (hoisted out of `setupForecastPage`, same treatment as `computeGoalOverlays`) so the Dashboard's Wealth Simulator preview card can reuse the same math without duplicating it. Every input except the stocks amount (always live from holdings) persists to `localStorage['pfmForecastConfig']` on each "Run Forecast" click and prefills on the Forecast page's next load; the Dashboard preview reads the same key (falling back to the page's hardcoded defaults if nothing is saved yet) to render a compact SVG chart (`renderDashboardForecastChart`) in `#dashForecastArea` — no goal overlays or mortgage-payoff annotations there, those stay exclusive to the full page. `renderDashboardForecastChart` has dashed y-axis (4 compact-€ gridlines) and x-axis (4 "+Ny" year gridlines) — gridlines/labels use `stroke="currentColor"`/`fill="currentColor"` at reduced opacity so they pick up the page's ambient light/dark text color automatically, no separate dark-mode CSS rule needed. `_fmtEurCompact` (module-scope in `pfm_analytics.js`, shared with the Portfolio Value History sparkline below) formats axis labels as `€180k`/`€1.2M`.

**Net Worth page** (`index.html` `#nwBalanceSheet` / `#nwRetirement` / the manage cards, `loadNetworthPage` in `pfm_analytics.js`; schema v35) has three parts, top to bottom, and no new sidebar entry. (1) **Balance sheet**: `GET /api/v1/networth/balance-sheet` rendered by `renderBalanceSheetRows` / `renderBalanceSheetTotals` as a table grouped like the API (group subtotals, a totals row with assets, liabilities, net worth and "financial assets (excluding property)"); each row has a source badge, an "as of" date and an amber stale marker with its note, and an edit control chosen by the line's `edit.kind` (manual line form, loan form, valuation refresh, or a link to the import page for a bank account). The old summary cards are gone, their numbers are all in the sheet. (2) **Retirement card**: `loadRetirementCard` fetches `GET /api/v1/retirement/readiness`; `retirementVerdictHtml` shows the verdict for the retirement and statutory ages side by side (pessimistic case in small text), the spending target and its source, the pensions assumed, how long the money lasts and the surplus or the shortfall with the extra monthly saving needed. An SVG chart of the base path (`retirementChartSeries`: investments and cash stacked, loan balance dashed, a marker at each retirement age) does not redraw on resize. The gear icon opens the plan form (`PUT /retirement/plan`); a second section lists the retirement incomes with add, edit, delete and quick-add suggestions. `needs_setup` and `insufficient_data` show the form and the reason instead of a verdict. (3) **Manage**: the manual assets and liabilities (with an "as of" date defaulting to today), deposits, cash flow, **Loans** and **Account valuations** editors. Help text: `nw_*` and `rt_*` keys in `METRIC_HELP`. Pure helpers (`renderBalanceSheetRows`, `renderBalanceSheetTotals`, `retirementVerdictHtml`, `retirementChartSeries`, `retirementGoalLineHtml`) are `window.`-exported and tested in `web_client/js/tests/balance_sheet.test.mjs` and `retirement.test.mjs`.

**Goals page and dashboard card (2026-10-24).** See `networth-portfolios.md` → Goals UI: summary strip, status badges, the "Since set / Pace" block, notes, the per-goal **Ask AI** button (`goalChatMessage` → `openChatWithContext`, the same route the Research workbench and Portfolio Health use) and `#dashGoalsCard` (`loadDashboardGoals`, placed above the Budget card, hidden without goals). The chat empty state has an "Am I on track for my goals?" chip; the chat answers it with the `get_goals` tool. Pure helpers `goalStatusMeta`, `formatMonthsLeft`, `goalPaceModel`, `goalsSummaryChips`, `goalChatMessage` are `window.`-exported and tested in `goals.test.mjs`.

**Decisions page and attribution card (2026-11-01).** Sidebar → Insights → Decisions (`#decisionsPage`, `loadDecisionsPage` in `pfm_features.js`): add form, Open/Due/Reviewed/All filter, Review modal, Reopen, Delete; pure helpers `decisionStatusMeta`/`decisionActionMeta`/`decisionPriceMoveHtml` tested in `js/tests/decisions.test.mjs` (`decisions.md`). Analytics → Performance gets the "What drove the return" card (`loadAttributionCard` in `pfm_analytics.js`, period remembered in `localStorage` `pfm.attributionPeriod`, `attributionRowsHtml` tested in `js/tests/attribution.test.mjs`; `analytics.md`). The Portfolios modal has a "Managed by the broker" checkbox and the table a `managed` badge (`networth-portfolios.md`).

**Goals page readiness line**: for a goal whose name contains "retire" (case-insensitive) the Goals page fetches the readiness once per load and shows `retirementGoalLineHtml` ("Readiness: <verdict>, money lasts until <age>", linking to Net Worth) in place of the progress bar, since net worth over a placeholder target is not progress. If the readiness call fails the ordinary progress bar stays.

**Wealth Simulator "Fill from my data"** (`#fcLoadNetworth`) calls `GET /api/v1/wealth-scenarios/prefill` and applies it with `forecastFromPrefill` (field map `FORECAST_PREFILL_FIELDS`); a null value leaves its field alone, and each filled value is listed with its source under the button. See `wealth-simulator.md`.

**Dashboard KPI row** (`index.html`, `.row-cols-2.row-cols-md-3.row-cols-xl-5`) is 5 cards: **Total value** (positions from `GET /api/v1/portfolios/holdings`'s `summary.total_value` plus the combined Cash figure below; the headline falls back to positions only if both cash sources fail, and its subtitle `dashValueSub` then says so), Total Invested (subtitle: the positions' current value, then the broker count), **Cash** (all idle cash combined — uninvested brokerage cash from `GET /api/v1/portfolios/values`'s `total_cash_eur`, the same call the Brokers page already uses, **plus** bank account balances from `GET /api/v1/networth/`'s `bank_accounts_eur` — both fetched in parallel with the rest of the dashboard's data and best-effort, degrading to "—" only if *both* fail), Return (period selector), and Open Positions. Cash is included in the Total value headline; the positions-only value is shown under Invested, the cost it compares with. The Bank Accounts card below still shows the per-account breakdown that this combined figure rolls up — deriving both from the same `bank_accounts_eur` concept, not a separate computation. The allocation donut (`loadDashboardPage`'s `grouped` map, keyed by `asset_type`) also gets a `cash` slice with this same combined total when it's `> 0`, so a cash-heavy portfolio isn't shown as 100% invested. Total Invested and Cash each get a one-line subtitle (`dashInvestedBrokers`/`dashCashAccounts`) — "Now €X · N brokers" (portfolios from `portfolioValues.portfolios` with nonzero `cost_eur`) and "N accounts" (portfolios with nonzero `cash_eur` plus bank accounts with an imported balance) — sourced from data already fetched for the KPI values, no extra calls.

**Dashboard layout & shared chart helpers (v2.5.70).** Order: KPI tiles → slim `#dashAttention` strip → Risk row → Investments (history + allocation donut, top positions + wealth projection) → Cash & spending (bank donut + spending donut) → Budget → Recent activity. Shared helpers live in `pfm_core.js` so every page can use them: `renderDonut(container, items, opts)` (donut + value/% legend, hover highlights slice and row, 2px gaps; items `{key,label,value,color,detail}`), `donutSlices(entries, max)` (sort + fold the tail into `__other__`, splits out non-positive values that can't be arcs — e.g. an overdrawn account), `attachLineHover(svg, {W, top, bottom, left, right, xs, series, html})` (crosshair + dots + the shared floating `chartTip`), `chartTipHtml(title, rows)`, `fmtEurWhole`/`fmtEurCents`, `vizColor(i)`/`vizTypeColor(type)`/`vizTypeLabel(type)`. Colours are CSS tokens `--viz-1..8`, `--viz-neutral`, `--viz-other`, `--viz-cost` in `styles.css`, with separate light/dark steps validated for CVD separation — SVG presentation attributes can't read `var()`, so set them via `style="fill:var(--viz-1)"`. **Asset types map to a fixed colour** (ETF always slot 1, cash neutral grey) — never colour by rank. `applyChartJsDefaults()` (called at bootstrap) sets Chart.js to index-mode tooltips (pies keep per-slice). `niceTicks(min,max,n)` and `filterSnapshotsByRange(snaps, range, today)` are in `pfm_analytics.js`. The attention strip: `dashAttentionSet(slotId, {summaryHtml, detailHtml, onDismiss} | null)` — `loadDashboardAlerts` (slot `dashAlerts`) and `loadDashboardActionItems` (slot `dashActionItems`) each fill one group; dismiss keys `pfmAlertsDismissed`/`pfmDashActionItemsDismissed` unchanged. History range persists in `localStorage['pfmDashHistoryRange']`. Don't put `text-success`/`text-danger` spans inside the coloured KPI tiles (green-on-green made CAGR invisible).

**Notifications, not `alert()` (v2.5.71).** Use `notify(msg, level?)` (`pfm_core.js`) for every user message — stacked Bootstrap toasts, `level` one of `success|info|warning|danger`, inferred by `notifyLevel(msg)` when omitted. Never add a native `alert()`; the only one left is the database-restore message, kept blocking on purpose because `location.reload()` follows it. `showToast(msg, type)` is an alias. **Dialogs, not `confirm()` (v2.5.72).** Ask with `await confirmDialog({title, message, confirmLabel, cancelLabel, danger})` (resolves true/false; closing = false) and report outcomes with `showResultDialog(model)` — never native `confirm()`. Say what the action changes, not just "Are you sure?". Imports go through `showImportResult(result, {kind: 'spending'?, afterModal, actions})`, built on the pure, unit-tested `importResultModel`/`spendingImportResultModel` (`importResultText` for chat); pass `afterModal` when the import modal is closing so the result opens after it has hidden (two modals animating at once leave a stray backdrop). `/import/save` and `/spending/save` responses carry the summary fields (`by_type`, `date_from`/`date_to`, `new_assets`, `asset_types_corrected`, `portfolios`, `booking_totals`; spending: `account_name`, `money_in`/`money_out`, `uncategorized`, `latest_balance*`) — additive, so keep them optional. **Money: `Fmt.money(v, currency, decimals=2)` is the only formatter** (v2.5.73) — locale-aware symbol/placement, `—` for null, escaped `"12.00 XYZ"` fallback for codes Intl rejects (GBX, crypto tickers). Don't build `'€' + Fmt.num(…)` or `` `${Fmt.num(x)} ${cur}` `` strings; compact axis ticks (`_fmtEurCompact`, `fmtEurTick`) are the one exception. Chart.js EUR series use `tooltip: { callbacks: chartEurTooltip }` and `monthKeyLabel()` for `YYYY-MM` axes. **Projection charts** cap the y-axis with `projectionAxisMax(data, 2.5)` and clip the band (a 95% band is ~6× the mean at 30y); the Dashboard preview is fixed at the 50% band (`sigma = 0.674`). **Keyboard**: `attachLineHover` makes the SVG focusable (←/→/Home/End/Esc) and removes its previous listeners when a chart re-renders into the same `<svg>` (`svg._pfmKbCleanup`); `renderDonut` legend rows are `tabindex=0`. **Mobile**: page headers (`.page-content > .d-flex.justify-content-between:first-child`) wrap via CSS — check new pages at 390px for horizontal overflow. **Chart tooltips on HTML-string charts**: put `data-chart-tip="${esc(chartTipHtml(title, rows))}"` on the mark; one delegated listener shows the shared tooltip. SVG axis colours: `SVG_GRID`/`SVG_AXIS`/`SVG_LABEL` constants (`currentColor` at reduced opacity) and `fmtEurTick(v, step)` + `niceTicks` in `pfm_analytics.js` — never hard-code `#e2e8f0`/`#64748b`, they glare on the dark theme. `applyChartJsDefaults()` also runs from `applyTheme()` so Chart.js grid/label colours follow the theme.

⚠️ **Formatters that return `Fmt.amt(...)` (e.g. `anFmtEur2`, `_bgEur`, local `eur`) emit a `<span class="pfm-amt">` — never interpolate them into an attribute** (`title=`, `data-*`); the span's quotes close the attribute and the rest of the markup renders as text (this broke the Dividends tab). Use `esc(fmtEurCents(v))` / `esc(fmtEurWhole(v))` there. ⚠️ **An element with a Bootstrap `d-flex`/`d-block` class can't be hidden with `el.style.display = 'none'`** — those utilities are `!important`. Swap classes (`classList.replace('d-flex', 'd-none')`) instead; this kept the Wealth Simulator placeholder on screen above every rendered chart.

**Dashboard risk row** (`#dashRisk` in `index.html`, `loadDashboardRisk` in `pfm_analytics.js`, one `GET /api/v1/analytics/risk?window=1y` call): four `metricTile`s over the trailing year — return vs the chosen benchmark in EUR (pts, default FTSE All-World), distance below the 12-month high (with the worst drop as subtitle), volatility (neutral), Sharpe (Sortino as subtitle). Hidden while there's too little history; on an error it shows the error instead of disappearing. "details" jumps to Analytics → Risk (`openAnalyticsRisk`), whose card has a Last 12 months / All history selector (`#anRiskWindow`) and all nine tiles. Bands and wording come from `rateMetric` (see `analytics.md` → Risk metrics); the tiles live outside the coloured KPI cards, so the rating colours stay readable.

**Dashboard progress row** (`#dashProgress`, `loadDashboardProgress` in `pfm_analytics.js`, one `GET /api/v1/analytics/progress` call), above the risk row: market growth on top of the money put in, real return (IRR after Spanish inflation), emergency-fund months and savings rate, built by the pure `progressTiles()` (unit-tested). Tile text carries **no euro amounts** — `metricTile` escapes text, so an amount there would skip the privacy blur; amounts live on Analytics → Progress (`loadAnalyticsProgress`: tiles, money-in-vs-value Chart.js line, year-by-year table vs benchmark/inflation/real, month grid via `monthlyGridRows()`, sell-everything tax, savings & safety net). "details" uses `openAnalyticsTab('progress')`. `cssVar()` (`pfm_core.js`) resolves `--viz-*` tokens for Chart.js, which can't read `var()`.

**Dashboard Portfolio Value History** (`dashNetworthArea` in `index.html`, `loadDashboardNetworthHistory`/`renderDashboardNetworthSparkline` in `pfm_analytics.js`) is a line chart of actual daily snapshots (value solid + cost basis dashed, 3M/6M/YTD/1Y/All range, crosshair tooltip) — the same `GET /api/v1/analytics/networth-history` data the Analytics page's full "Net Worth Over Time" chart uses (positions value/cost only; cash isn't tracked historically, hence "Portfolio Value" not "Net Worth" in the card title, matching the positions value under the Invested card). Complements the Wealth projection card's *projected future* with the *actual past* — sits beside the allocation donut. Unlike the Analytics chart (which force-includes a zero baseline for its labelled y-axis), the chart scales tightly to the data's own min/max, rounded outwards to `niceTicks` gridlines; the line is always `--viz-1` (the change figure above it carries the up/down colour). Shows "Not enough history yet" below 2 snapshots, same message as the Analytics page.

**Dashboard Action Items summary** (`dashActionItems` in `index.html`, `loadDashboardActionItems` in `pfm_features.js`) is one group ("N to-dos" + severity badges) in the slim `#dashAttention` strip below the KPI tiles, next to the price-signals group, surfacing the same items the Action Items page lists in full: stale imports, data-quality issues, price-update errors, stale research, off-track goals, and the Net Worth setup checklist (via `getActionItems()` + `getNetworth()` + `getCashflow()` → `computeNetWorthChecklist()` → `mergeActionItems()`, the exact same pipeline `loadActionItemsPage()` uses, filtered by the same `pfmDismissedActionItems` localStorage list). The `watchlist` category (price-target/watchlist-alert items) is deliberately excluded here — it reuses the exact same `check_watchlist_alerts`/`compute_price_target_alerts` sources the Alerts banner above it already shows in full, so including it would duplicate those triggers. Hidden entirely when there are no (non-watchlist) items; otherwise shows severity-count badges; expanding it lists the first 6 item titles and a link to the Action Items page. Independently dismissible via a `pfmDashActionItemsDismissed` localStorage signature hash of the current item-id set (same "reappears only if the set changed" pattern as the Alerts banner's own dismiss) — separate from `pfmDismissedActionItems`, which permanently dismisses an item everywhere including the full Action Items page.

**Follow-up state and the Diagnostics overview (2026-10-24).** Checked / snoozed findings are server-side (`GET /api/v1/action-items/?status=all`, `GET …/acks`, `POST …/ack`; `apiClient.getActionItems(status)`, `getActionItemAcks()`, `setActionItemStatus({id, status, note, days})`). The pure rule `ackIsActive(ack, today)` (`pfm_core.js`) mirrors the server's; `annotateActionItems`/`filterActionItems` drive the Action Items page (`_aiState`: severity filter `[data-ai-sev]`, `#aiShowResolved`, `#aiSummary`, per-item `data-ai-ack`/`data-ai-chat` delegation on `#actionItemsList`, `window.setActionItemStatus(id, status, opts)`). `noteDialog(opts)` is `confirmDialog` with a textarea (`#pfmDialogNote`), resolving to the text or `null`. Diagnostics (`loadDiagnosticsPage`) now fetches freshness, runs, acks, duplicates, suspicious rows and the WARNING+ log in one `Promise.all` into `_diagState`; `diagOverviewModel(state, now)` turns that into the `#diagOverview` chips (click → `showDiagTab`) and the `#diagTab*Badge` counts, `renderDiagOverview()` redraws both after every ack; the update-history table lists failed symbols with their API errors in a `<details>`; the Data Quality cards read `_diagState.dups/susp` (Re-run refetches) and render `_dqActionButtons` (Checked → `noteDialog`, Snooze 7/30 d, Ask AI, Reopen). `migrateLocalDismissals()` runs once per load (dashboard, Action Items, Diagnostics) to move the old `localStorage` dismissals to the server. "Go to page" on a Diagnostics item carries `data-diag-tab`, which `setupNavigation` stores in `localStorage.pfmDiagTab` before switching. Tests: `web_client/js/tests/action_items_followup.test.mjs`.

**Dashboard** (`pfm_pages.js`, `loadDashboardPage`) also renders three independent, non-blocking cards alongside the KPI/positions/donut/simulator content: **Bank Accounts** (`renderDashboardBankAccounts` in `pfm_analytics.js` → `#dashBankArea` donut, same `getNetworth().bank_accounts` source as the Net Worth page's card; negative/unimported accounts listed under the legend) and **Spending** (`loadDashboardSpending` in `pfm_features.js` — a Spent/Income/Transferred stat row via `renderDashboardSpendingStats` (plus Net) plus a category donut via `renderDashboardTopCategories` (shared `renderDonut`, top 6 + Other), both from one `getSpendingSummary(days)` call). The third is **Budget** (`loadDashboardBudget` in `pfm_features.js`, one `GET /api/v1/budgets/summary` call) — current-month planned-vs-actual progress bars per section plus a net line, hidden entirely when there's no active budget with lines. All three cards degrade independently of each other and of the simulator preview. The Spending card's time frame (7/30/90/365 days) is shared with the Spending page's own period selector via `localStorage['pfmSpendingSummaryDays']` (read/written through `getSpendingPeriodDays()`/`setSpendingPeriodDays()`) — picking a period on either page is reflected on the other the next time it loads. The Net Worth page's own "Actual (last 30 days)" comparison widget is separate and stays fixed at 30 days.

**Analytics page — Diversification (fund look-through)** (`pfm_analytics.js`, `loadAnalyticsDiversification`): the existing By Asset Class/Sector/Currency/Type/Country bar blocks gained a By Region (equity) block (`d.by_region_equity`, region-coded labels via `regionLabel()`) and a By Currency Exposure block (`d.by_currency_exposure`, look-through — see the "Fund look-through" section of `analytics.md` for how it differs from the existing quote-currency By Currency block). Above them, `renderCoverageBanner(coverage)` states the fund-classified share up front. **The calm/warning split is gated on `coverage.unprofiled`/`coverage.stale_profiles` being empty, never on `classified_pct` hitting a percentage threshold** — `classified_pct` legitimately never reaches 100% (see below), so an earlier `pct >= 99.9` gate rendered the warning branch on a portfolio with nothing left to fix: an amber box naming no fund and asking for no action. The calm branch now reads "All held funds have a look-through profile. N% of value classified by region, M% by sector — the remainder is holdings with no region in our data, such as direct bonds."; the warning branch (unchanged) names every `coverage.unprofiled`/`stale_profiles` fund as a clickable link (`.fp-open`) whenever either list is non-empty. Below the bar grid, `renderOverlapCard(groups)` (fetched via a second, independently-failing `getFundOverlap()` call so a fund-overlap outage doesn't blank the whole section) partitions groups by `kind !== 'informational'` (`consolidation_candidate` **and** `similar`, shown up front) vs `=== 'informational'` (nested, collapsed behind a "Show N nested holdings" toggle) — each group showing its combined %, its members (also `.fp-open` links), and — when `transferable` — a note that a `traspaso` can merge them tax-free. Clicking any `.fp-open` link calls `window.openFundProfileModal(assetId, symbol, name)`, opening **`#fpModal`** (`index.html`): editable region and asset-class weight sliders (`FP_REGION_KEYS`/`FP_CLASS_KEYS`, normalized on save via `normalizeWeights`), a read-only sector display (`_fpRenderSectors` — sectors come from yfinance and hand-entering a split would be busywork), and buttons to refresh from a benchmark or request an LLM `suggest`ed profile, then `PUT` the reviewed result. `fundProfileValidate(profile)` (module-scope, unit-tested) mirrors the server's own profile validation client-side before save.

**Why `classified_pct` doesn't reach 100% even with an empty `unprofiled` list**: `by_region_equity`/`classified_pct` only cover assets that have a *region* concept at all — a fund's look-through weights, or a directly-held equity's own country. A handful of direct, non-fund holdings have no country in pfm's data by design: the synthetic Mintos P2P line (`MINTOS`, priced at par — see the Mintos parser note in `imports-exports.md`, it represents pooled loan principal, not a single country) and individual bond ISINs (a bond's issuer country isn't tracked the way an equity's is). These land in region `"unknown"`, which is not a fund-coverage gap — every fund-like asset in `FUND_ASSET_TYPES` can and does have a profile — and is exactly why the coverage banner's calm state (above) is keyed on the actionable lists, not on `classified_pct` itself.

`window.METRIC_HELP` / `window.PAGE_HELP` in `help_text.js` — tooltip definitions and per-page help modal content. Add entries when adding new pages or non-obvious cards. `METRIC_HELP` is a flat `key: "plain string"` map used directly as a `title=` attribute — there's no shared helper function; the convention is inline `data-bs-toggle="tooltip" title="${METRIC_HELP.xxx}"` (optionally on a `<i class="bi bi-info-circle">` icon next to a label). Bootstrap tooltips need explicit init after dynamic rendering — call `initTooltips()` (defined in `pfm_analytics.js`, exposed as `window.initTooltips`) once after any table/card is (re)rendered with new tooltip-bearing elements; static tooltips already in `index.html` are covered by the `initTooltips()` call in `loadDashboardPage()`.

`openResearchModal(symbol, name)` — the modal title renders as `"Name (SYMBOL)"` when a name is passed, else falls back to the bare symbol; callers should pass the asset name when they have it (e.g. the Assets page row).

`makeSortableTable(config)` / `applyTableState(rows, columns, state)` in `pfm_core.js` — shared sortable/filterable tables; per-table state persists in `PREFS.tableState[<page>]`.

**Assets page** (`loadAssetsPage`/`_loadHoldingsAndRender`/`_renderFilteredAssets` in `pfm_pages.js`): merges the old separate Holdings and Assets pages. Defaults to owned positions for the selected portfolio (`GET /api/v1/portfolios/holdings`, unchanged); the "Show assets with no holding" checkbox appends `GET /api/v1/assets/` catalogue entries whose symbol has zero quantity in *any* portfolio — computed via one unfiltered `getHoldings()` call, cached in `this._heldSymbolsGlobal`, independent of the portfolio filter. An asset held in a different, non-selected portfolio is excluded from both halves (not a position for the selected portfolio, not "held nowhere" either) — same as the old Holdings portfolio-filter behavior, nothing new to reconcile. `PREFS.tableState.holdings` is the sort/filter-state key (kept from the old Holdings page, not renamed).

`window.PREFS` → `localStorage['pfmPrefs']`. Format numbers via `Fmt.num()`, dates via `Fmt.date()`. Theme = `data-bs-theme` on `<html>` (Bootstrap 5.3).

**Next euro** (`pfm_features.js`, `_ne*` helpers; help keys `ne_*`): a collapsed card `#nextEuroCard` on the Assets page (deployable breakdown and buffer gauge, amount input, bucket table, lines with reasons, traspasos, blocked, needs-research, "Copy for chat", settings form with the work-merchant checkboxes) and a `#dashNextEuro` entry in the Dashboard attention strip (hidden when `insufficient_data`). Data from `/api/v1/allocate/*`; see `allocator.md`.

**Deploy after editing web files** (`portf_web` bakes files at build time — NOT live-mounted):
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

`saveImportedTransactions(transactions, bookings = [], portfolioId = null, duplicateAction = 'skip', deposits = [])` (`pfm_core.js`, API client) — always pass bookings array (even if empty) so PDT bookings are saved alongside transactions; `deposits` carries `PreviewDeposit` rows from the `deposits` CSV.

**Research scorecard and managed line (2026-11-04).** The Research page has a third tab, Scorecard (`#researchScorecard`, `loadResearchScorecard` in `pfm_features.js`, queried lazily on the first click per page visit; the symbol filter `#scorecardSymbol` re-queries with `?symbol=`). It shows two tiles (at horizon, at next report), breakdowns by recommendation and confidence, best/worst five and a table of all entries with a pending count; the help text is `METRIC_HELP.researchScorecard`. Pure helpers `scorecardPointText`/`scorecardHitBadge`/`scorecardRowHtml` are tested in `js/tests/scorecard.test.mjs` (`research-rebalance.md`). The Rebalancing card shows a muted line (`#rebalanceManaged`, `rebalanceManagedLineHtml`) with the value and share held in robo-advisor portfolios when the analysis `managed` block is above zero, and the plan header lists `inputs.managed_symbols` as "Kept at weight".

### Caps, stress test, year-end tax and calibration (2026-11-04)

The Rebalance page has a **Caps** card (`#rebalanceCaps`, `loadCapsCard`): one table from `GET /caps/status` (implicit "default stock cap" rows muted, no buttons), an inline add form that doubles as the edit form (the pencil fills it, `PUT` sends key, max % and note only) and a `confirmDialog` before delete. The Research workbench shows a muted `#rvCapLine` ("Cap 5%: headroom €X") from the same status call, and `#rvCalibration` from the `calibration` block of `/research/{symbol}/lookup`; the Scorecard tab prepends `calibrationTableHtml`. Analytics → Risk has a **Stress test** card (`#anShockSelect`/`#anShockBody`; the `anStress*` ids belong to the older historical-crash tab) that remembers the preset in `localStorage` `pfm.stressScenario`. The tax-optimizer card adds `taxDeadlineHtml` (days to 31 Dec, carry-forward, base) and a per-candidate tax-saved column; a symbol in `wash_blocked` gets the "blocked by the 2-month rule until …" badge. Pure helpers are tested in `caps`, `stress`, `yearend` and `calibration` `.test.mjs`.

## Charts in chat

The AI chat draws a chart when an assistant answer contains a fenced code block
with language `chart` holding JSON:

    {"type":"bar","title":"Spending by category","labels":["Food","Rent"],
     "series":[{"name":"Oct","data":[300,900]}],"currency":"EUR"}

- `type`: `bar`, `hbar` (horizontal), `line`, `area` (filled line), `pie`
  (donut, first series only), `combo` or `waterfall`.
- `labels`: 1-60 strings. `series`: 1-6 objects `{name, data}`; `data` has one
  finite number per label. Optional: `title`, `currency` (3 letters, default
  `EUR`, money via `Fmt.money`), `stacked` (bar, hbar, area), `unit` (a string
  such as `%` or `months`: values are then plain numbers with that unit, not
  money), `x_title`, `y_title` (they title the horizontal and vertical axis as
  drawn, so on an `hbar` `x_title` names the value axis).
- `combo`: each series may carry `kind` (`bar` default, or `line`) and `axis`
  (`left` default, or `right`). A right axis exists only when a series uses it;
  it is a plain number axis with the optional `right_unit` appended. An unknown
  `kind` or `axis` falls back to the default rather than invalidating the chart.
- `waterfall`: first series only; `data` are signed deltas. `totals` lists label
  indexes drawn as running totals from zero (the data value is replaced by the
  running sum; at index 0 it seeds the starting amount). Invalid indexes are
  dropped. Colours: `--viz-good` up, `--viz-bad` down, `--viz-neutral` totals.
- `chatChartSpec` validates (pure, `window.`-exported, tested in
  `js/tests/chat_charts.test.mjs`); an invalid or unparseable block stays an
  ordinary code block. `chatPrepareCharts` swaps valid fences for a placeholder
  before `marked`, and `chatHydrateCharts` draws them with Chart.js after the
  message is in the DOM. Colours are the `--viz-1..8` tokens.
- Each chart has a toolbar: download PNG (on a white background) and expand (a
  Bootstrap modal, `#pfmChartModal`). Clicking a bar, slice or point sends
  `chatChartDrillPrompt(spec, labelIndex, seriesIndex)`, i.e. `Break down "<label>"
  from the chart "<title>" in more detail.` (`from the chart above` when there is
  no title; with several series a `Focus on the series "<name>".` sentence is
  added). On the Reports page charts are not clickable.
- Exports: the Markdown export keeps the block as is. The HTML export and print
  embed each chart as a PNG `<img>` read from the live canvases
  (`chatCollectChartImages`, one entry per chart in document order, `''` when a
  chart is not on screen, which falls back to a `<pre>` of the JSON).
  `chatSessionToHtml(name, messages, chartImages)` stays pure: the third argument
  is that array (or, for `chatMarkdownToExportHtml`, an object keyed by the
  chart's JSON).

## Tables in chat

`chatEnhanceTables(root)` gives every Markdown table in an answer or report a CSV
button (`tableToCsv`, UTF-8 BOM so Excel reads the euro sign) and click-to-sort
headers (keyboard accessible, `aria-sort`). `chatSortCompare` sorts numbers
numerically, best effort on "1,200 EUR", "(250)" and "-12%"
(`chatParseNumber`; one separator followed by exactly three digits is thousands,
as in the import parsers), otherwise text in natural order, empty cells last.
Tables sit in a horizontally scrolling wrapper, so they do not widen the page at
390px. Inside a table the bubble's `overflow-wrap: anywhere` is switched off:
headers and numeric cells (`td.chat-num`, tagged by `chatEnhanceTables` with
`chatParseNumber`) never wrap, text cells wrap at word boundaries, and a table
that still does not fit scrolls sideways instead of breaking figures across
lines.

## Chat on phones

Under 576px the chat fills everything below the sticky top bar: `#chatPage` has
a negative margin equal to the `.app-content` padding (change both together), the
page title row is hidden, and the panel is `100dvh − 57px` high (57px is the top
bar; `100vh` fallback), so the composer stays above the mobile browser's toolbar.
The header is one row (web search and Extract show their icon only, the model
picker is capped at 6.5rem) and the composer is one row (textarea beside the
buttons), which leaves the thread about 690px of an 844px screen, up from 490px.
Avatars are hidden and an answer bubble takes the full width: about 350px of
text on a 390px screen, where the desktop layout left 267px. The table scroller
extends to the bubble edges. Under 768px the thread list
(`#chatThreadsPanel`, Bootstrap `offcanvas-md`) is a drawer opened from the list
button in the chat header; `hideThreadsPanel()` closes it after a thread is
picked or a new one is started.

## Follow-ups, sources and web search

- `followups` (max 4) of the latest assistant answer render as chips (class
  `chat-suggestion`, so a click sends them). Any new message removes the older
  chips.
- `sources` render as a "Sources" list under the answer (`chatSourcesHtml`): only
  `http`/`https` links, titles escaped, `target="_blank" rel="noopener
  noreferrer"`. Both exports include them.
- The Web search switch in the chat header shows only when
  `GET /llm/chat/models` says `web_search_available: true`; the choice is kept in
  `localStorage` (`pfmChatWebSearch`) and sent as `web_search` on each turn. Its
  tooltip says searches leave this machine and personal figures are not sent.
- The empty chat offers starter questions in three groups (Portfolio, Spending,
  What if), `CHAT_STARTERS`; `index.html` carries the same markup until a thread
  is activated.

## Reports

Saved reports use `/api/v1/saved-reports` (`/api/v1/reports/portfolio` is the PDF
portfolio report and is unrelated).

- The bookmark button under an answer asks for a title (`noteDialog`, default from
  `chatReportDefaultTitle`) and posts the answer's Markdown with the thread as
  `source_session_id`. Export, "Write report from this thread" calls
  `POST /llm/chat/sessions/{id}/report` (30-60 s, busy state on the Export
  button) and opens the result on the Reports page via `window._reportsOpenId`.
- The Reports page (Insights, after Decisions) lists reports newest first and
  renders the selected one with the chat pipeline (`chatRenderMarkdown`,
  `chatHydrateCharts`, `chatEnhanceTables`). Actions: rename, delete
  (`confirmDialog`), download `.md`, download `.html` (charts embedded as PNG),
  print. When the endpoint is missing or fails, the empty state shows (the e2e
  smoke test relies on this); a non-404 failure adds its message.

## Spending categories: essentiality

Categories under the `Spend` root get a selector (-, Fixed, Flexible, Optional) in
the Categories tab (`PUT /spending/categories/{name}/essentiality`). "Suggest with
AI" (`POST .../suggest-essentiality`) opens a panel where each suggestion can be
ticked and its level changed before anything is saved; `spendingEssentialitySuggestions`
drops unknown names, invalid levels and categories outside Spend. The levels drive
the chat's "what to cut first" answers (explained in the Spending help).

## Untrusted Markdown

Model-written Markdown (chat answers, reports, the dashboard chat bubble) goes
through `safeMarkdownHtml(html, fallbackText)` in `pfm_core.js` before it
reaches `innerHTML`: DOMPurify removes scripts, handlers, forms, frames and
`style`; a hook drops every image that is not an inline PNG/JPEG data URI (a
remote image URL could carry private figures out of the browser, which matters
once web search can bring outside text into an answer) and gives http(s) links
`target="_blank" rel="noopener noreferrer"`, removing any other `href`. Without
DOMPurify (CDN blocked) the text is shown escaped. Chart placeholders survive
because `class` and `data-*` attributes are allowed.

## Cost control (2026-11-15)

Web side of `feat/cost-control` (backend: `spending_insights.py`,
`cashflow_forecast.py`, budget pace/fund/rollover). Pure helpers sit in a
"Cost control" block of `pfm_features.js` (before the Dashboard budget card
code), are `window.`-exported and tested in `js/tests/cost_control.test.mjs`.
API calls: `getSpendingCompare/Anomalies/Tiers`, `getCashflowForecast`,
`setSpendingOneOff` in `pfm_core.js`.

- **Rule for every figure:** a `null` from the backend is shown as "not
  available" (`ccMoney`/`ccSignedMoney`), never `0`, and a delta that is `null`
  is a dash in a neutral colour, never green. `compareRowsModel` keeps the
  backend's `warnings` as the visible reason when the month is `in_progress`,
  `partial` or `no_imports`.
- **Spending › Transactions:** a flag button per row (`.sp-oneoff-btn`) toggles
  `one_off` through `PUT /spending/{id}`, a "one-off" badge in the description,
  the `#spOneOffFilter` select (`one_off=true|false`) and the bulk buttons
  `#spBulkMarkOneOffBtn` / `#spBulkUnmarkOneOffBtn`.
- **Spending › Analytics** (`loadSpCostControl`, run when the tab is shown):
  `#spCmpCard` month vs normal (month picker, 3/6/12 month normal, category /
  merchant toggle; savings are reported apart), `#spAnomCard` unusual charges
  (`loadSpAnomalies`), `#spTierCard` stacked fixed / flexible / optional /
  unclassified bars (`loadSpTiers`). Tier colours are fixed per tier
  (`CC_TIER_COLOR`: `--viz-1`, `--viz-3`, `--viz-4`, `--viz-neutral`); a month
  with no imports is a gap, not a zero bar.
- **Anomalies:** an anomaly's `id` is also its Action Item id
  (`check_spending_anomalies`), so *Dismiss* uses the existing server-side
  follow-up state (`setFindingStatus(id, 'checked')`, hidden by
  `anomalyVisibleItems` with `ackIsActive`), not `localStorage`. *Mark
  one-off* (`anomalyCanMarkOneOff`) sends `one_off: true` for the item's
  `transaction_ids` and reloads the comparison, anomalies and tiers; a
  `category_spike` has no rows and a `possible_duplicate` is a possible double
  charge (calling it one-off would hide it instead of settling it), so those
  only offer Dismiss.
- **Budget › Overview:** a Pace column (badge plus projected month end, also
  a line under the label on phones; `too_early` shows a grey "Too early" badge
  and a dash, never a figure), a section banner for the spending pace,
  tier badges on lines, a tier totals strip under the spending section, the
  yearly fund line ("Last 12 months: spent X of Y, Z left", `fundText`) and "available" under
  Planned for any line whose plan differs from `planned_total`. The progress
  bar and the KPI tile use `available_total` when present (that is what the
  variance is measured against). `variance.warnings` and `data_through` are
  printed above the sections. The existing no-activity alert is unchanged.
- **Budget › Edit:** the Add form has a period select and a carry-over
  checkbox (`#bgNewLineFundRow`, hidden for income and investment: the server
  returns 400 there); the amount label becomes "€ / year" for a yearly line.
  Each spending/debt row has a period select and, for monthly lines, a
  carry-over checkbox (`.bg-line-period`, `.bg-line-rollover`). A yearly line's
  stored amount is the YEARLY figure; the override grid and the Scenarios
  totals use a twelfth (`budgetMonthlyShare`, `expandBudgetLineMonths`).
- **Budget › Forecast** (`#bgPaneForecast`, `loadCashflowForecast`): data
  freshness per account (stale badge), warnings and a below-zero banner, the
  this-month projection (with the `no_imports` / `no_baseline` banners and the
  spending range), a projected-balance line chart (null balances are gaps), the
  month-by-month table, the known-bills list (15, then "Show all") and the
  assumptions. The Overview and Edit tabs are hidden while no budget exists
  (`_bgShowBudgetTabs`); the tab bar stays so the Forecast works without one.
- **Dashboard budget card:** one pace line for the spending section, only when
  the backend sent a `pace` and the no-activity rule is not active.
- **Help:** `METRIC_HELP` keys `costCompare`, `costAnomalies`, `costTiers`,
  `oneOff`, `budgetPace`, `budgetYearly`, `budgetRollover`, `cashflowForecast`,
  `cashflowRange`; `PAGE_HELP` budget and spending gained list items.
- **Tests:** `make test-js` (the Makefile runs every `*.test.mjs`);
  `tests/e2e/test_web_client_e2e.py::test_cost_control_views_render_against_real_data`
  seeds invented rows, a monthly and a yearly line, and loads the Forecast,
  comparison, anomalies and tiers. The e2e harness stubs Bootstrap's CSS, so
  layout (390px overflow) cannot be checked there.


## Cost control 2 (2026-11-17)

Web side of `feat/cost-control-2`. Pure helpers sit in the "Cost control 2"
block of `pfm_features.js` (after `forecastBelowZeroText`), are `window.`-exported
and tested in `js/tests/cost_control2.test.mjs`. API calls (`pfm_core.js`):
`getCashflowForecast(months, extra, includeTax)`, `getPlannedItems`,
`create/update/deletePlannedItem`, `getUncategorizedQueue`,
`classifyUncategorizedGroup`, `getSpendingYear`.

- **Budget › Forecast:** a *Planned* card (`#bgFcPlannedCard`: add/edit form,
  list with a status badge — projected, overdue, beyond horizon, done —, mark
  done, delete; the status comes from the forecast's `planned_items` matched by
  id), an *Include estimated tax* switch (`include_tax`), a *What if I spend…*
  row (`#bgWhatIfForm`: amount, month, kind; one result line from `what_if`,
  nothing saved, Clear drops it; a failed forecast also drops it), a stacked
  bar chart of planned spending, planned savings and estimated tax by month
  (`#bgFcExtrasCard`, `--viz-3/2/4`), the estimated quarterly tax table
  (`#bgFcTaxBody`, labelled as an estimate, basis in words) and Planned / Tax
  columns in the month table (Planned is the signed net of the month's planned
  income, spending and savings).
- **Budget seed dialog:** per spending proposal the reason, a "Suggested:
  yearly/monthly (+ carry-over)" badge with its amount, a Period select (switching
  moves the amount between the monthly average and the yearly figure), a Carry
  over box (monthly only) and, for a category that already has a line, a
  *Convert to yearly* / *Add carry-over* button that uses the existing line PUT.
  Apply sends `period`/`rollover` through the bulk endpoint, only for spending
  and debt lines (`seedLinePayload`).
- **Spending › Transactions (top):** the *To classify* card (`#spQueueCard`,
  loaded at the end of `_refreshSpendingData`, so a classification refreshes
  table, rules and queue): groups by merchant with samples and date range, a
  category input on the shared `#spCategoryList`, "Also add a rule" with the
  proposed pattern editable, one request per group; five groups shown, the rest
  behind a toggle; empty state when nothing is uncategorised. Placed at the top
  of Transactions because that is the tab opened first.
- **Spending › Analytics (top):** the *Year* card (`#spYearCard`, loaded with
  `loadSpCostControl`): year picker, like-for-like totals with the "compared
  through" note and the full previous year, a 12-month bar chart of both years
  (a month without imports is a gap), category table with deltas, movers, top
  merchants and one-off rows.
- Every null figure prints "not available" or a dash, never 0.

## Cost control 3 (2026-11-18)

Web side of `feat/cost-control-3`. Pure helpers sit in the "Cost control 3"
block at the end of `pfm_features.js` (and `actionItemRank`, `isActionGroup`,
`flattenActionGroups`, `thisWeekModel` next to `mergeActionItems`), are
`window.`-exported and tested in `js/tests/cost_control3.test.mjs`. API calls
(`pfm_core.js`): `getActionItems(status, {grouped})`, `getNetworthSnapshots`,
`getNetworthSnapshotChange`, `takeNetworthSnapshot`, `getSpendingRecurring({includeEnded, sort})`,
`set/clearRecurringResolution`, `getMonthClose`, `closeMonth`, `reopenMonth`.
(`getNetworthHistory` is the older portfolio-value history behind Analytics and
the Net Worth chart; the snapshot calls have their own names on purpose.)

- **Action Items page:** reads `grouped=true&status=all`. A group (`kind: "group"`,
  id `group:<family>`) is one card with a count badge and a Show/Hide button
  that expands its children (`_aiState.expanded`); a child keeps its own
  Checked/Snooze. Checked/Snooze on the group row posts the group id (the server
  applies it to every open child and stores nothing under the group id), then the
  page reloads. Acting on a child also reloads, because the folded list does not
  hold it. `mergeActionItems` still merges the Net Worth checklist and the
  `dq:`/ack handling is unchanged; items are sorted by the server's `priority`,
  and an item without one (checklist items, an older server) ranks at its
  severity base. A group's "Go to page" is hidden when its children link to
  different pages. The chat hand-offs use `flattenActionGroups`.
- **Dashboard:** the needs-attention strip also reads `grouped=true` (a folded
  family is one line). The *This week* card (`#dashThisWeek`, `renderDashThisWeek`)
  lists the report's `top` (title, severity, count for a group, link) and hides
  when nothing is open or the call fails.
- **Net Worth › History** (`#nwHistory`, `loadNetworthHistory`): a line chart of
  stored snapshots with a toggle to stacked components (investments, cash,
  property, other assets; liabilities drawn below zero; `--viz-*` colours), a
  *Snapshot now* button, and *What changed* tiles for the last month, 3 months and
  since the first snapshot (`nwChangeWindows`: a window longer than the history is
  not requested and says so). A null part reads "not available" and the server's
  warnings are printed under the tile. With one snapshot the card says "History
  starts <date>" and has no chart.
- **Spending › Recurring:** status/yearly-cost sort buttons (`sort`), a totals
  line (`recurringTotalsModel`: monthly and yearly, of which paid another way,
  and what cancellations save), badges for Cancelled, Paid another way and Came
  back (`resolution_stale`), the resolution note under the merchant, and a per-row
  `...` menu (Cancelled / Paid another way / Clear) that asks for an optional
  note. The menu is a Bootstrap dropdown with `strategy: fixed` so the
  responsive table does not clip it.
- **Budget pace:** `paceMeta` knows `irregular`: badge "Irregular" and
  "irregular: spent in N of M months" (`months_with_activity` of `baseline_months`)
  in the table cell, the mobile line and the title.
- **Budget › Month close** (`#bgPaneClose`, `loadMonthClose`): month picker (last
  12 complete months, the server picks the first one), checklist with status
  icons and a link to the relevant page, the figures (null = "not available"),
  a notes box, *Close month* / *Close again*, *Close anyway* (shown only when the
  month is not ready; behind `confirmDialog`), *Reopen* (behind `confirmDialog`),
  a banner when `closed.stale`, and a link that opens the saved report on the
  Reports page.
- Every failing call shows "not available" in its own card; nothing was added that
  uses a native dialog, and money goes through `Fmt.money`.

## Loans & Mortgage page (2026-11-15)

Sidebar page `loans` (`#loansPage`, `loadLoansPage` in `pfm_analytics.js`): summary cards, two hand-drawn SVG charts (balance line, stacked principal/interest bars, `data-chart-tip` hover), a collapsible yearly table, the payments-vs-bank table and a debounced what-if panel. The budget editor's link picker (`budgetLinkOptions`, `parseBudgetLink` in `pfm_features.js`) offers manual liabilities and loans. Details: `mortgage.md`.
