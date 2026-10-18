# Price-spike guard + ongoing charges (TER) — plan

Date 2026-10-18. Branch `feat/price-guard-ter`, worktree `~/repos/pfm-guard-ter`.
Two independent features; recurring commitments were dropped (the Spending
Recurring tab + `check_recurring_charges` already cover them).

Conventions: black (88), Google docstrings, type hints, comments on the line
before. Tests under `tests/unit/`, invented asset names, round fixture
numbers, no network (mock at the seam). Run:
`UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
and `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` (0 warnings).

## 1. Price-spike guard (`portf_manager/services/price_guard.py`)

Why: a yfinance glitch once stored every GBP asset 100× too high for a day
(prices-market.md, "nothing currently guards against this"). Exchanges treat an
out-of-band print as suspect until a later print confirms it; so does this.

Rule (pure function, no DB):
- Inputs: previous stored price + its date, the newly fetched price, today,
  and the currently held observation for the asset (or None).
- Band: `|ln(new / prev)| > ln(1 + MAX_DAILY_MOVE) * sqrt(days)` with
  `MAX_DAILY_MOVE = 0.5` (chosen: wider than any exchange daily limit, far
  inside a ×100 or ÷100 error) and `days = max(1, (today - prev_date).days)`
  (volatility grows with the square root of time). Log-symmetric so a ÷100 is
  caught as surely as a ×100.
- Outcomes: `store` (inside band, or no previous price), `hold` (outside band,
  nothing held yet or a held value from the same day), `confirm` (held value
  from an earlier day and the new price is within 5 % of it: a real
  repricing persists, a glitch does not), `clear` (a held value exists but the
  new price is back inside the band: the glitch is gone).
- `store`/`confirm`/`clear` store the price; `confirm`/`clear` delete the held
  value. `hold` stores nothing and records the held observation.

Held state: `app_settings` key `price_guard:{asset_id}` holding JSON
`{price, date, prev_price, prev_date, ratio, source}` via `db.get_setting` /
`db.set_setting` (no migration). A `list_held_prices(db)` helper scans keys with
that prefix (add a small DB method if there is no prefix listing).

Wiring in `run_price_update`: between the currency self-healing and
`insert_price_record`, call the guard with `db.get_latest_price(...)` (check its
signature). A held symbol goes into `skipped_symbols`, gets an `api_errors` line
`Price held for {sym}: {new} vs {prev} ({pct:+.0f} % in {days} d), awaiting
confirmation`, and an `app_logs` event `price.guard.held` via `db.log_event`
(also `price.guard.confirmed` / `price.guard.cleared`). The return dict gains
`held_symbols`. Manual prices (`POST /assets/{id}/prices`) never pass through the
guard.

Action item: `check_held_prices(db)` in `services/action_items.py`, registered
in `_CHECKS`; one item per held asset: id `errors:price-held:{asset_id}:{date}`,
category `errors`, severity `medium`, title "Price for {name} held: {pct:+.0f} %
in {days} day(s)", detail naming both prices and how to resolve ("confirmed by
tomorrow's fetch if it repeats; or store a manual price"), `link_page`
`diagnostics`.

Tests: `tests/unit/test_price_guard.py` (pure rule: inside band, ×100, ÷100,
30-day gap widens the band, same-day repeat stays held, next-day confirm,
next-day clear) and extend `tests/unit/test_price_updater.py` (held price is
not stored, appears in `held_symbols`, and is stored on the confirming run) and
`tests/unit/test_action_items.py` (the check is registered and renders).

## 2. Ongoing charges / TER (`fund_profiles.ongoing_charges_pct`)

Why: fund costs are invisible; progress metrics now show growth vs
contributions, and the cost side of that is the TER (UCITS KIID "ongoing
charges", annual % of NAV).

- Migration v34: `ALTER TABLE fund_profiles ADD COLUMN ongoing_charges_pct REAL`
  (guarded), also in `_create_all_tables`; `DATABASE_VERSION = 34`; update every
  `== 33` in `tests/test_database.py`; CLAUDE.md migration list + schema line.
- `services/fund_profiles.py`: parse/serialize the field; `validate_profile`
  rejects values outside 0–10.
- API: the fund-profile schemas (`portf_server/schemas/...`) gain
  `ongoing_charges_pct: Optional[float]`; the profile PUT stores it.
- `GET /analytics/fees` gains an `ongoing_charges` block computed by a new
  `services/ongoing_charges.py::compute_ongoing_charges(db, fx)`:
  positions and EUR values from `compute_exposure(db)["by_position"]` (check
  the key names), fund-like assets by the same rule exposure/fund_profiles use;
  returns `weighted_avg_pct` (value-weighted over funds with a TER),
  `annual_cost_eur`, `covered_value_eur`, `fund_value_eur`, `uncovered`
  (`[{symbol, name, value_eur}]`, funds without a TER) and `projection`:
  for H in (10, 20, 30) years at `ASSUMED_GROSS_RETURN_PCT = 5.0` (chosen),
  `cost_eur = Σ v_i((1+r)^H − (1+r−t_i)^H)` and `riy_pct` = the reduction in
  yield, PRIIPs style: `r − ((Σ v_i(1+r−t_i)^H / Σ v_i)^(1/H) − 1)`.
- Web: Fees tab (`loadAnalyticsFees`, `pfm_analytics.js`) adds an "Ongoing
  charges" card: weighted TER, €/year, the 10/20/30-year cost and RIY, and the
  uncovered funds with a note to set the TER in the fund profile editor; the
  fund-profile editor (around `fundProfileValidate`) gets a "Ongoing charges
  (TER) %" number input. `help_text.js` `METRIC_HELP` entries cite the UCITS
  KIID ongoing-charges figure and PRIIPs reduction in yield. Money via
  `Fmt.money`, escape with `esc`. Check 390 px width.
- Tests: `tests/unit/test_ongoing_charges.py` (weighted average, uncovered
  list, projection numbers on round fixtures: one fund 10 000 € at 0.2 %,
  r 5 %, H 10), fund-profile API test for the new field, `test_database.py`
  version bump.

## 3. Docs (mandatory)
`docs/features/prices-market.md` (guard section replaces the "nothing guards"
sentence), `docs/features/analytics.md` (fees block, fund profile field),
`docs/features/action-items.md` (new check), `CLAUDE.md` (v34 line, one-line
gotcha: held prices live in `app_settings` `price_guard:*`), `PROJECT_STATUS.md`
(Last updated + Recent line, v2.5.80).

## 4. Done when
Unit suite green, flake8 clean, `make test-js` green if JS helpers were added,
black run on touched files, conventional commits on the branch (do not merge).
