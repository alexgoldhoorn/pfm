# Wealth Simulator and saved scenarios

The Wealth Simulator (`#forecastPage`, sidebar "Wealth Simulator") projects net
worth from a few inputs: cash, stocks/ETFs and bonds with an assumed long-run
*real* return each, a volatility for the stocks bucket, a monthly contribution
into stocks, a mortgage amortised month by month, a horizon and a confidence
band. Since v2.5.87 the inputs can be **saved as named scenarios** on the
server, compared on the chart, tracked against the real net worth, shown on the
Dashboard and read by the AI chat.

## Projection model

Per bucket, deterministic compound growth for the expected path and a GBM band
around it (`mean · e^(±z·σ·√t)`); contributions are an ordinary annuity with
annual compounding; the mortgage is a standard month-by-month amortisation
where a payment below the interest lets the balance grow. Fixed volatilities:
cash 1 %, bonds 6 %; the stocks volatility is an input (default 16 %).
Net worth = cash + stocks + bonds − remaining mortgage.

The model exists twice, on purpose, and the two copies are pinned to the same
fixture:

- `web_client/js/pfm_features.js` — `projectAccount`, `runProjection`,
  `runProjectionFromParams(params)` (the API's params object instead of 13
  positionals). The page, the comparison overlays and the Dashboard card use
  these.
- `portf_manager/services/wealth_scenarios.py` — `project_account_at`,
  `point_at` (fractional years, mortgage amortised for the whole months
  elapsed), `project`, `summarize`. The API, the chat tool and the Action Item
  use these.

`tests/unit/test_wealth_scenarios.py` and
`web_client/js/tests/wealth_scenarios.test.mjs` assert the same numbers for the
same inputs (year 0, 5 and 20, payoff year, total interest). **A change to one
copy must be made to the other**, or the Dashboard and the chat will quote
figures the page does not show. The band at year 0 uses half a year of
volatility — a quirk of the page kept so both charts agree at the origin;
tracking passes `exact_origin=True` so that on the baseline day the expected
range is exactly zero.

## Schema (v37)

`wealth_scenarios`: `id`, `name` (UNIQUE), `description`, `is_primary` (0/1, at
most one row is 1), `params` (JSON, see below), `baseline_date` (ISO),
`baseline_net_worth_eur` (nullable: NULL when the balance sheet could not be
built at save time), `created_at`, `updated_at`. DB methods:
`create_wealth_scenario`, `get_wealth_scenarios` (primary first, then by name),
`get_wealth_scenario`, `update_wealth_scenario`, `set_primary_wealth_scenario`
(None clears), `delete_wealth_scenario`. A duplicate name raises `ValueError`
from a pre-check, because `get_connection()` wraps anything raised inside it in
`DatabaseError`.

`params` keys (all numbers; `normalize_params` merges them over `DEFAULT_PARAMS`,
drops unknown keys and rejects out-of-range values with the parameter's name):
`cash_amount`, `cash_rate_pct`, `stocks_amount`, `stocks_rate_pct`,
`stocks_vol_pct` (0–100), `stocks_contribution_monthly`, `bonds_amount`,
`bonds_rate_pct`, `mortgage_principal`, `mortgage_rate_pct`,
`mortgage_payment_monthly`, `years` (1–60), `confidence_z` (0.674 = 50 %,
1.0 = 68 %, 1.96 = 95 %, 2.576 = 99 %). The web client maps them to the page's
`pfmForecastConfig` shape (camelCase strings) with `forecastParamsFromInputs` /
`forecastInputsFromParams`.

## API (`portf_server/routers/wealth_scenarios.py`, `/api/v1/wealth-scenarios`)

All plain `def` (the balance sheet may call FX).

- `GET /` — every scenario as a *view*: `id, name, description, is_primary,
  params, baseline_date, baseline_net_worth_eur, created_at, updated_at,
  summary, tracking`. Primary first.
- `POST /` `{name, description?, params, is_primary?}` → 201 with the view.
  Baseline = today and `current_net_worth(db)`
  (`build_balance_sheet(db)["totals"]["net_worth_eur"]`, None on failure —
  never a 5xx). 409 on a duplicate name, 422 on a bad parameter.
- `GET /{id}` — the view plus `projection`: the yearly points
  (`year, assets, assets_high, assets_low, mortgage, net_worth,
  net_worth_high, net_worth_low`).
- `PUT /{id}` `{name?, description?, params?, is_primary?}` — partial.
  **Sending `params` re-baselines to today** (the projection now starts from
  new amounts, so the old baseline would compare apples with pears).
  `is_primary: true` demotes the current primary; `false` on the primary leaves
  none.
- `POST /{id}/rebaseline` — new baseline date and net worth, inputs untouched.
- `DELETE /{id}`.

`summary` (`summarize`): `horizon_years`, `confidence_label`,
`start_liquid_eur`, `start_net_worth_eur`, `final_liquid_eur`,
`final_net_worth_eur` with `_low_`/`_high_`, `investment_gains_eur` (final
liquid − starting liquid − contributions), `total_contributions_eur`,
`mortgage_paid_off_year`, `total_interest_eur`, `milestones` (years 5 and 10
when inside the horizon, plus the horizon; each with `calendar_year`, expected,
low, high and mortgage).

## Tracking (plan vs actual)

`track(params, baseline_date, baseline_net_worth_eur, actual_net_worth_eur,
today)` compares **changes, not levels**: the simulator's net worth is liquid
assets minus the mortgage, while the balance sheet also holds property and
other manual lines, so the two levels differ by a constant that cancels out in
the deltas. With `t` = elapsed days / 365.25:

- `expected_change_eur` = `point_at(t).net_worth − point_at(0).net_worth`,
  and the same for the band edges (`expected_low/high_change_eur`);
- `actual_change_eur` = actual − baseline net worth; `gap_eur` = actual −
  expected change; `gap_pct` = gap / baseline net worth × 100.
- `status`: `on_track` inside the band, `ahead` above, `behind` below;
  `too_early` under `MIN_TRACKING_DAYS` (30 — on day two the band is a hair
  wide and everything reads as off-plan; the figures are still returned, only
  the verdict is withheld); `no_baseline` when either net worth is missing or
  the date is unreadable.

The band is the scenario's own `confidence_z`, so a 50 % scenario is judged
more strictly than a 95 % one — by design: the user picked how sure they wanted
to be.

## Web client

- **Saved scenarios card** (`#fcScenariosCard`, top of the inputs column):
  one row per scenario with a compare checkbox, the name (click = load), a ★
  for the primary, the tracking badge (`wealthTrackingBadgeHtml`), "Expected in
  N y" and the baseline date, and a dropdown (Load, Update with current inputs,
  Set/Unset as primary, Re-baseline, Ask AI about it, Delete). Update and
  Re-baseline and Delete go through `confirmDialog`. The **first scenario saved
  becomes primary** automatically. Save = name field + button (Enter works).
  Loading a scenario puts the *saved* stocks amount in the inputs (the plan as
  saved), and says so under the field; the refresh icon restores today's
  holdings.
- **Compare**: ticked scenarios are run client-side (`runProjectionFromParams`)
  and drawn on the chart as dashed expected paths in `--viz-6/2/5/3`, labelled
  at their end, listed in the hover tooltip and counted towards the y-axis (a
  line to read, not a band — never clipped). The legend entry
  `#fcCompareLegend` shows only while something is compared.
- **Plan vs actual card** (`#fcTrackingCard`, under the chart) for the loaded
  scenario: badge plus `wealthTrackingHtml(tracking)` — the baseline date and
  days, actual vs expected change with the band, the gap in EUR and as a
  percentage of the baseline net worth, and a hint for `too_early` / `behind`.
- **Ask AI** (`#fcAskAiBtn`, header): `openChatWithContext('Wealth plan: <name>',
  scenarioChatMessage(view))` for the loaded scenario; without one the current
  inputs are summarised client-side in the same shape ("not saved yet"). The
  message names the inputs, the expected outcome and range, the payoff year,
  the tracking status and asks for a review; it mentions `get_wealth_scenarios`
  so the assistant fetches the rest itself.
- **Dashboard** (`loadDashboardForecastPreview`, now async): when a primary
  scenario exists the card projects **its saved params** (always with the 50 %
  band) and adds a footer line with the ★ name and
  `wealthTrackingHtml(tracking, {compact: true})`; otherwise it falls back to
  the last-run inputs with the live holdings total as before
  (`loadDashboardForecastFromInputs`). The call site wraps the promise so a
  rejection cannot blank the rest of the dashboard.
- Page help: `PAGE_HELP.forecast` (`help_text.js`) has a "Saved scenarios"
  section. Pure helpers are `window.`-exported for the DOM-free tests:
  `forecastParamsFromInputs`, `forecastInputsFromParams`,
  `runProjectionFromParams`, `wealthTrackingBadgeHtml`, `wealthTrackingHtml`,
  `signedEurWhole`, `scenarioChatMessage`.

## Chat and Action Items

- Chat tool `get_wealth_scenarios` (`portf_server/chat_tools.py`, no
  arguments): every scenario's params, summary and tracking plus `notes`
  reminding the model that returns are real, how tracking is defined and what
  "primary" means. 24 tools in total.
- `check_wealth_scenario` (`services/action_items.py`, category `forecast`,
  label "Wealth Plan" in the web client): one `medium` item,
  `wealth_scenario:behind:{id}`, when the **primary** scenario's tracking is
  `behind`; other scenarios are what-ifs and raise nothing. Links to the
  Wealth Simulator page.

## Not done / ideas

- No MCP tool yet (the finance agent on the homelab cannot read scenarios);
  adding one means the three registrations in CLAUDE.md.
- Tracking keeps only the baseline, not a history of checks; a monthly
  snapshot of actual vs expected would allow a "progress against plan" chart.
- A scenario's `stocks_amount` is a snapshot; the page's refresh icon brings in
  today's holdings but does not update the scenario (that is "Update with
  current inputs", which also re-baselines).
