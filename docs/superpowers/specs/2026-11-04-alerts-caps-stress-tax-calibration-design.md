# Alert state, position caps, stress test, year-end tax, research calibration — design

Date: 2026-11-04. Five small features on one branch, after the research
scorecard (v39). Schema goes to **v40** (one new table, `position_caps`).

Shared rules: the 2026-11-01 and 2026-11-04 specs' constraints apply
(worktree only, black 88, flake8 clean, Google docstrings, comments on the
line before, invented fixtures, tests cannot reach the network, new tables in
BOTH `_create_all_tables` and `_migrate_to_v40`). Every new read surface ships
as an endpoint, a chat tool and a read-only MCP tool. The registries that
several features touch — the `TOOLS` count pin in `tests/unit/test_chat_tools.py`,
`mcp/README.md`'s table, `mcp/tests/test_package.py` `EXPECTED_TOOLS`,
`CLAUDE.md`, `PROJECT_STATUS.md` — are updated once at the end by the
integrator, not by the feature builders.

## 1. Alert once per crossing (no schema)

Today `GET /research/alerts/check` recomputes every target crossing and the
20:05 cron sends the same rows every night. Add state so a crossing is
reported when it happens, not daily.

- State lives in `app_settings` under `price_alert_state` (one JSON blob:
  `{ "<asset_id>:<BUY|SELL>": {"first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD", "notified": "YYYY-MM-DD"|null} }`),
  read/written through `db.get_setting`/`db.set_setting`.
- `compute_price_target_alerts(db, today=None, record=False)`: each trigger
  gains `first_seen` (today when the key is absent) and `is_new`
  (`first_seen == today`). With `record=True` the state is written: keys for
  current triggers are upserted (`last_seen = today`), keys whose trigger is no
  longer active are deleted, so a price that goes back inside the band and
  crosses again is "new" again. `record=False` never writes.
- `GET /alerts/check?record=true|false` (default **false**): the cron calls it
  with `record=true`; the Action Items and the web page keep reading without
  writing. Response adds `"new": <count of is_new triggers>` and
  `"standing": <count of the rest>`.
- `check_price_alerts` (Action Items): research rows say "since
  {first_seen}" in the detail when the crossing is older than today; nothing
  else changes.
- `~/mcp/scripts/price_alerts.py` (consumed by `~/scripts/portf-price-alerts.sh`):
  a trigger is "new" when `is_new` is true. Message rules: new triggers are
  listed in full (ranked as now, no top-3 cap for new ones), standing ones are
  summarised as one line `"… N standing since earlier (oldest {first_seen}) — full list in pfm"`.
  On Sundays (local date, Europe/Madrid) the standing ones are listed in full
  too, headed "Weekly digest". With zero new triggers on a non-Sunday the
  formatter exits 1 (the existing "No alerts triggered" path) so no message is
  sent. The shell script passes the API response through unchanged; the only
  script change is `?record=true` on the curl.
- Tests: `tests/unit/test_alert_state.py` (first run marks new; second run same
  day still `is_new`; next day not new, state kept; trigger gone → key removed;
  re-crossing new again; `record=False` writes nothing; endpoint flag) and
  `~/mcp/scripts/tests/test_price_alerts.py` (new/standing split, Sunday digest,
  exit code when nothing new).

## 2. Position and sector caps (schema v40)

Table `position_caps`: `id`, `scope` CHECK in (`symbol`, `sector`, `asset_type`),
`key` TEXT NOT NULL (upper-cased symbol / sector name as `compute_exposure`
emits it / asset_type), `max_pct` REAL NOT NULL CHECK 0 < max_pct ≤ 100,
`note` TEXT, `created_at`, `updated_at`, UNIQUE(scope, key).

Service `portf_manager/services/caps.py`:
- `cap_status(db, fx=None, today=None) -> dict`: runs `compute_exposure` once
  and, for every cap, reports `current_pct` (symbol → `by_position`; sector →
  `by_sector`; asset_type → `by_asset_type`), `max_pct`, `headroom_pct`
  (max − current), `headroom_eur` (headroom_pct × total), `status`
  (`ok` | `near` when headroom < 2 pp | `breached`). Also a `block` derived
  from the allocator's `max_single_stock_pct` setting for every held stock
  without its own symbol cap (`implicit: true`), so the page shows one list.
  Return `{"as_of", "total_value_eur", "caps": [...], "breached": n, "near": n}`.
- Allocator: in `build_candidates` / the stock bucket, a stock whose symbol
  cap (explicit) is tighter than `max_single_stock_pct` uses the tighter one;
  a sector cap is enforced by excluding candidates whose sector is `breached`
  or would breach with the proposed line (reason text "would exceed the
  {sector} cap"). Keep the change inside the existing cap-handling functions
  (`_stock_candidates`-style exclusion and `_cap_stock_lines`), do not
  restructure the allocator. Sector of a stock = `assets.sector`.
- Action Item `check_position_caps(db)` (new module function in
  `services/caps.py`, registered in `_CHECKS`): one item per breached cap,
  id `cap:{scope}:{key}`, category `allocation`, severity high, title
  "{key} is over its {max_pct}% cap ({current_pct}%)", `link_page: caps`.
- Router `portf_server/routers/caps.py` at `/api/v1/caps`: `GET /` (list),
  `POST /` (201), `PUT /{id}`, `DELETE /{id}` (204), `GET /status`
  (registered before `/{id}`). Registered in `app.py` with `_PROTECTED`.
- Chat tool `get_position_caps()` → `cap_status`; MCP tool `position_caps()`.
- Web: a **Caps** card on the Rebalance page (list with current/max/headroom
  and a status badge, add/edit/delete inline) and the headroom line on the
  Research workbench for the loaded symbol ("Cap 5%: headroom €X"). Help
  text entries.
- Tests `tests/unit/test_caps.py` (schema v40 fresh + migration, status maths
  incl. implicit stock caps, breached/near, Action Item, router CRUD,
  allocator respects a tighter symbol cap and a breached sector cap) and
  `mcp/tests/test_caps.py`.

## 3. Stress test (no schema)

Service `portf_manager/services/stress.py`:
- `SCENARIOS` (ordered dict of presets): `equity_bear` (equities −30%),
  `semis_crash` (sector Technology/semiconductor names −50%, other equities
  −15%), `usd_down` (USD −10% vs EUR), `rates_up` (bonds −7%, equities −5%),
  `crypto_winter` (crypto −60%), `stagflation` (equities −20%, bonds −10%,
  crypto −40%). Each preset is a dict of shocks:
  `{"asset_class": {"equity": -30}, "sector": {...}, "currency": {"USD": -10}, "asset_type": {"crypto": -60}}`.
- `run_stress(db, shocks: dict, fx=None, today=None) -> dict` applies the
  shocks to the held portfolio through `compute_exposure`'s look-through
  (a fund's value is shocked by its class/sector/currency weights; a stock by
  its own class, `assets.sector`, and currency), sums the loss, and reports:
  `loss_eur`, `loss_pct_of_portfolio`, `by_asset_type`, `by_portfolio`,
  `top_losers` (5 positions), `net_worth_before/after` (balance-sheet net
  worth from `services/balance_sheet.build_balance_sheet(db)["totals"]["net_worth_eur"]`
  minus the loss), and, when the retirement plan is set up,
  `retirement_after` = the readiness verdict recomputed with the shocked
  investable total if `services/retirement.py` exposes a function that takes
  the investable value as an input; if it does not, add a keyword argument
  `investable_override_eur` to its entry point rather than duplicating the
  maths, and report `None` when the plan says "needs setup".
- `GET /api/v1/analytics/stress?scenario=<name>` and
  `POST /api/v1/analytics/stress` with a custom shocks body; `GET /analytics/stress/scenarios`
  lists the presets. Chat tool `get_stress_test(scenario)`; MCP tool
  `stress_test(scenario='equity_bear')`.
- Web: a **Stress test** card on Analytics → Risk (or the Risk pane the page
  has): preset picker, loss €/%, net worth before/after, top losers, the
  retirement line when available. Help text entry.
- Tests `tests/unit/test_stress.py` (look-through shock on a fund with a
  profile, stock by sector, currency shock, preset listing, endpoint,
  retirement None without setup) and `mcp/tests/test_stress.py`.

## 4. Year-end tax planner (no schema)

`GET /analytics/tax-optimizer` already returns realised gains, income,
harvest candidates with the 2-month flag and the tax saved. Extend it, do not
replace it:

- Per candidate add `tax_saved_eur` (the marginal saving of harvesting that
  one position, using `services/tax_rates.progressive_tax` on the savings
  base with and without it) and `can_harvest_until` (31 December of the year,
  or earlier if the broker's last trading day is known — keep 31 December).
- Add `carry_forward`: unused losses from the previous four tax years
  (`TaxCalculator.calculate_tax_report` per year; a year's net negative result
  not offset is carried) with `total_eur` and `by_year`, and
  `base_after_carry_forward_eur`. Flag the assumption in a `notes` list
  ("carry-forward assumes the losses were declared").
- Add `deadline`: `{"date": "YYYY-12-31", "days_left": n}` and
  `wash_blocked`: candidates whose harvest would be blocked by a buy inside the
  2-month window (reuse `services/wash_sale.analyse`'s `recent_buys`).
- Action Item `check_year_end_tax(db, today=None)` (own module
  `services/year_end_tax.py`, registered in `_CHECKS`): from 1 November to
  31 December, when `harvestable_loss_eur < -100` or realised gains > 0 with
  offsets available, one item `tax:year-end:{year}`, category `tax`, severity
  medium, title "Year-end tax check: €X of losses could offset €Y of gains",
  `link_page: analytics`. Silent outside the window.
- Chat tool `get_year_end_tax(year)`; MCP tool `year_end_tax(year=None)`.
- Web: the existing tax-optimizer card gains the deadline, carry-forward and
  per-candidate saving columns. Help text update.
- Tests `tests/unit/test_year_end_tax.py` (marginal saving per candidate,
  carry-forward from a seeded prior-year loss, deadline, Action Item inside
  and outside the window) and `mcp/tests/test_year_end_tax.py`.

## 5. Research calibration from the scorecard (no schema)

`services/research_scorecard.py`:
- `calibration(db, today=None) -> dict`: from `compute_scorecard`, per
  confidence bucket (high/medium/low/unknown) and overall: `n`,
  `direction_hit_rate_pct`, `median_abs_target_error_pct`, `implied_vs_realised`
  (mean implied / mean realised, None when realised ≤ 0), and a
  `shrink_factor` = clamp(mean_realised / mean_implied, 0.1, 1.0) when both
  are positive, else None. Prefer the `at_horizon` aggregates when `n ≥ 10`,
  else `at_next`; say which in `basis`. Cached 1 hour via
  `portf_manager.cache.cached(db, "research:calibration", 3600, producer)`.
- `GET /research/{symbol}` (lookup) response gains `calibration`:
  `{"confidence", "hit_rate_pct", "n", "shrink_factor", "adjusted_fair_value", "basis"}`
  for the stored LLM report (None when no report or no calibration data).
  `adjusted_fair_value = price + (fair_value − price) × shrink_factor`.
- Bulk refresh and single research prompts (`services/research.py`): add a
  "Your track record" paragraph built from `calibration` when `n ≥ 10`
  ("Your BUY calls over the last N reports were right X% of the time and
  your fair values overshot by a median Y%; be correspondingly conservative").
  Omit the paragraph when there is no data.
- `GET /research/scorecard` adds `calibration` to its response.
- Web: next to every target on the Research workbench, a muted "calibrated:
  €X (hit rate Y%, n)" line and a tooltip; the Scorecard tab shows the
  calibration table. Help text entry.
- Chat/MCP: `get_research_scorecard` / `research_scorecard` already exist;
  they print the calibration block. No new tool.
- Tests in `tests/unit/test_research_scorecard.py` (calibration maths, basis
  choice, cache) and the lookup response in `tests/unit/test_research_calibration.py`.

## Integration (done last, by the integrator)

- Count pins: chat tools 28 → 28 + {get_position_caps, get_stress_test,
  get_year_end_tax} = 31; MCP `EXPECTED_TOOLS` 35 → 38; README rows.
- `CLAUDE.md` schema 40 + gotchas; `PROJECT_STATUS.md`.
- Deploy: DB backup, merge, web rebuild, gateway registration of
  `position_caps`, `stress_test`, `year_end_tax`, cron script `?record=true`,
  finance adapter/persona.
