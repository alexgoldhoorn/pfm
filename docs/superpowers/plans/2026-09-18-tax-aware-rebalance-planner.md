# Tax-aware rebalancing planner

**Date:** 2026-09-18  
**Status:** planned  
**Owner:** finance/rebalance surface (`/api/v1/rebalance/*`, Rebalance page)

## 1) Goal

Add a **trade planner** that turns the current "analysis-only" rebalance output
into concrete trade proposals while minimizing taxable pain.

Today `GET /api/v1/rebalance/analysis` shows per-asset-type drift and generic
BUY/SELL amounts. It does not answer:

- which symbols to buy/sell,
- how much,
- what tax cost each sell creates,
- what alternative plans exist (tax-minimal vs close-to-target).

The new feature adds that missing execution bridge.

## 2) Scope and non-goals

### In scope (MVP)

- New endpoint: `POST /api/v1/rebalance/plan`
- Build candidate trades at **symbol level**
- Use existing allocation targets as destination constraints
- Estimate taxable impact per sell and prefer lower-tax sells
- Return **3 strategy views** from one request:
  - `tax_minimal`
  - `closest_to_target`
  - `balanced`
- Rebalance page UI block to configure constraints + render plans

### Explicitly out of scope (MVP)

- Broker order execution (no buy/sell API calls)
- Intraday execution/pricing guarantees
- Perfect tax filing output replacement (this is planning, not legal report)
- Cross-jurisdiction tax rules (keep Spanish IRPF focus)

## 3) Reuse existing code first

Use existing components before adding new logic:

- `portf_manager/positions.py::compute_positions` for holdings math
- `portf_manager/tax_calculator.py` concepts (FIFO lot handling)
- `portf_server/routers/rebalance.py` existing targets + analysis surface
- `portf_manager/services/analytics_service.py::irpf_savings_tax` for tax
  estimate (thin wrapper over `portf_manager/services/tax_rates.py::progressive_tax`
  — import the wrapper, not `progressive_tax` directly, and not from `tax_rates.py`,
  which does not define `irpf_savings_tax`)
- Existing DB methods for targets/assets/transactions/prices

Design rule: extract reusable planner logic into a service module; keep the
router thin.

## 4) API contract

### 4.1 Request

`POST /api/v1/rebalance/plan`

```json
{
  "portfolio_id": null,
  "strategy": "balanced",
  "cash_budget_eur": null,
  "allow_sells": true,
  "max_trades": 12,
  "min_trade_eur": 100,
  "max_sell_gain_eur": null,
  "excluded_symbols": ["MINTOS"],
  "locked_symbols": ["IWDA.AS"],
  "target_overrides": [
    {"asset_type": "stock", "target_pct": 55},
    {"asset_type": "etf", "target_pct": 45}
  ]
}
```

Notes:

- `target_overrides` optional; when omitted, use saved allocation targets.
- `excluded_symbols`: never appear in BUY or SELL.
- `locked_symbols`: can be held/bought but never sold.
- `max_sell_gain_eur`: optional guardrail for total estimated realized gain.

### 4.2 Response

```json
{
  "generated_at": "2026-09-18T10:11:12Z",
  "inputs": {"strategy": "balanced", "max_trades": 12},
  "before": {
    "total_value_eur": 123456.78,
    "allocations": []
  },
  "plans": [
    {
      "strategy": "balanced",
      "summary": {
        "trade_count": 6,
        "buy_total_eur": 4200.0,
        "sell_total_eur": 4200.0,
        "estimated_realized_gain_eur": 380.0,
        "estimated_tax_delta_eur": 72.2,
        "max_abs_drift_pct_after": 1.8
      },
      "trades": [
        {
          "symbol": "ABC",
          "asset_id": 12,
          "asset_type": "stock",
          "side": "SELL",
          "quantity": 4.0,
          "price_eur": 95.0,
          "amount_eur": 380.0,
          "estimated_gain_eur": 42.0,
          "estimated_tax_eur": 7.98,
          "reason": "Reduce stock overweight with low-gain lot"
        }
      ],
      "warnings": []
    }
  ],
  "warnings": []
}
```

## 5) Planner algorithm (deterministic)

Implement in a new service module:

- `portf_manager/services/rebalance_planner.py`

### Step A: load current state

1. Load transactions (filtered by `portfolio_id` if provided).
2. Build positions via `compute_positions`.
3. Join assets + latest prices, convert to EUR.
4. Build per-asset-type totals and current allocation.
5. Load targets from overrides or `allocation_targets` table.

### Step B: compute gaps

For each asset type:

- `target_value_eur = total_value_eur * target_pct / 100`
- `gap_eur = target_value_eur - current_value_eur`
  - positive => needs buy
  - negative => needs sell

Ignore tiny gaps under `min_trade_eur`.

### Step C: derive sell candidates with tax estimate

For each held symbol in overweight types:

1. Build open FIFO lots (reuse/extract lot logic from tax calculator).
2. Compute estimated gain for selling `x` units at latest price:
   - `gain_eur = proceeds_eur - lot_cost_basis_eur`
3. Rank candidates by strategy score:
   - `tax_minimal`: lowest gain per euro sold first
   - `closest_to_target`: largest drift reduction first
   - `balanced`: weighted blend of both

Tax estimate (MVP):

- Compute baseline current-year taxable base. **This is not currently a
  reusable function** — `GET /api/v1/analytics/tax-estimate`
  (`portf_server/routers/analytics.py`, `get_tax_estimate`) computes
  `savings_base = realised_gain + div_this_year + interest_this_year` inline,
  built from private helpers local to that router
  (`_savings_income_eur`, `_lot_eur_amounts`, `_compute_positions`). Task 1
  below extracts this into a shared, importable helper
  (`portf_manager/services/analytics_service.py::current_year_savings_base(db, year=None)`)
  used by both `get_tax_estimate` and the planner, so the two can't diverge.
- Simulate tax delta with `irpf_savings_tax(baseline + added_gain) - irpf_savings_tax(baseline)`
  (`analytics_service.irpf_savings_tax`, see reuse list above).
- Keep this as an estimate field, not filing output.

### Step D: assign buys

Use sell proceeds + optional `cash_budget_eur` to fill underweight types.

Buy routing (MVP deterministic rule):

- Prefer existing held symbols in that type with largest current weight
  (minimizes symbol churn), unless excluded.
- If no eligible held symbol exists in type, add warning and skip that gap.

### Step E: constraints

Apply in order:

1. `excluded_symbols`
2. `locked_symbols` for sells
3. `max_trades`
4. `max_sell_gain_eur`
5. `allow_sells=false` => buy-only plan

If constraints block full rebalance, return partial plan with explicit warnings.

## 6) Data structures and schemas

Add Pydantic models in `portf_server/routers/rebalance.py` (or new
`portf_server/schemas/rebalance.py` if file grows):

- `RebalancePlanRequest`
- `RebalanceTrade`
- `RebalancePlanSummary`
- `RebalancePlan`
- `RebalancePlanResponse`

Validation rules:

- `max_trades` between 1 and 100
- `min_trade_eur` >= 0
- `strategy in {tax_minimal, closest_to_target, balanced}`
- target percentages in overrides each `0..100`
- target sum tolerance `99.5..100.5` (or documented if looser)

## 7) Router changes

File: `portf_server/routers/rebalance.py`

1. Keep existing endpoints unchanged (`/targets`, `/analysis`).
2. Add `POST /plan` route (plain `def`; blocking DB/price work).
3. Route calls service function only; no business logic in router.
4. Give the new route the same local `_auth = Depends(...)` dependency the
   three existing endpoints in this file already use — do NOT remove it.
   (Corrected 2026-09-18: the original text here said to remove the local
   `_auth` dependency as an inconsistency cleanup, but `/targets`/`/analysis`
   keep it deliberately — `APIKeyBearer` caches the validation on
   `request.state` per CLAUDE.md, so it's cheap, not redundant. Removing it
   only for `/plan` would make this file internally inconsistent instead of
   fixing anything. A repo-wide `_auth` cleanup, if wanted, is a separate,
   unscoped change — not part of this feature.)

## 8) Frontend integration

Files:

- `web_client/index.html`
- `web_client/js/pfm_features.js`
- `web_client/js/help_text.js`

### UI additions on Rebalance page

- Planner form:
  - strategy select
  - cash budget (optional)
  - max trades
  - min trade EUR
  - allow sells toggle
  - excluded/locked symbols inputs
- "Generate plan" button
- Results area:
  - tabs/cards for 3 strategies
  - table of trades
  - summary chips (drift after, estimated gains/tax)
  - warnings block

Safety copy in UI:

- "Planning output only. Review before placing orders."
- "Tax fields are estimates based on current data and latest prices."

## 9) Testing plan (must ship with feature)

### Unit tests (Python)

Add:

- `tests/unit/test_rebalance_planner.py`

Cases:

1. Pure buy-only rebalance with `allow_sells=false`
2. Overweight sell selection prefers low-gain lots in `tax_minimal`
3. `closest_to_target` reduces max drift more than `tax_minimal` on crafted data
4. `excluded_symbols` and `locked_symbols` are honored
5. `max_trades` truncation is deterministic
6. `max_sell_gain_eur` cap stops additional sells and emits warning
7. Empty/invalid targets fail validation with 422

### API tests

Extend `tests/unit/test_rebalance_research.py`:

- `test_plan_shape`
- `test_plan_validation`
- `test_plan_buy_only_mode`

### JS tests

Add/extend DOM-free helpers in `web_client/js/tests/`:

- strategy label mapping
- summary formatting
- warning rendering escapes text (`esc(...)`) before `innerHTML`

## 10) Rollout plan (agent-friendly)

Implement in 5 task-sized steps (Task 1 was split out of the original PR1
during plan review, 2026-09-18, because the tax-delta estimate in §5 Step C
depends on a helper that does not exist yet — see the corrected §5 note
above). Each task should land as its own PR.

### Task 1 - extract the taxable-base helper (prerequisite)

- In `portf_manager/services/analytics_service.py`, extract
  `current_year_savings_base(db, year=None) -> float` from
  `get_tax_estimate`'s inline computation
  (`portf_server/routers/analytics.py`): realised gain (via `TaxCalculator`
  + `_lot_eur_amounts`) + dividend/interest income for the year (via
  `_savings_income_eur`). Reuse the existing private helpers rather than
  duplicating their logic; move or import them as needed — whichever keeps
  `analytics.py` and the new helper from disagreeing.
- Update `get_tax_estimate` to call the new helper instead of computing
  `savings_base` inline, so there is exactly one implementation.
- Add/extend a unit test asserting the extracted helper returns the same
  value the existing `/analytics/tax-estimate` endpoint test already pins
  for its `savings_base`/`estimated_tax` fields (regression guard for the
  extraction).
- No behavior change to `/analytics/tax-estimate`'s response — this task is
  a pure refactor. Do not touch `/rebalance/*` in this task.

### Task 2 - backend planner service + route skeleton (was PR 1)

- Create `portf_manager/services/rebalance_planner.py` with basic gap
  calculation and stub strategy outputs (Steps A/B from §5).
- Add `RebalancePlanRequest`/`RebalancePlan`/etc. schemas + `POST
  /rebalance/plan` route (§6, §7) — route stays thin, local `_auth`
  dependency matches the file's existing three endpoints (see corrected §7
  note above).
- Add baseline tests for shape/validation (`test_plan_shape`,
  `test_plan_validation`).

### Task 3 - tax-aware sell ranking and constraints (was PR 2)

- Add lot-level sell estimator (§5 Step C), calling
  `analytics_service.current_year_savings_base` (from Task 1) and
  `analytics_service.irpf_savings_tax` for the tax delta — not
  `tax_rates.progressive_tax` directly.
- Add strategy scoring (`tax_minimal`/`closest_to_target`/`balanced`) and
  constraint application (§5 Step E).
- Add unit tests for ranking/caps/locks (plan cases 1-7 in §9).

### Task 4 - Rebalance page UI (was PR 3)

- Add planner form + output rendering (§8) to
  `web_client/index.html`/`pfm_features.js`.
- Add frontend tests and `help_text.js` entries.

### Task 5 - polish and docs (was PR 4)

- Performance pass (avoid repeated DB lookups).
- Error/warning clarity pass.
- Update `PROJECT_STATUS.md` and `CLAUDE.md` per this repo's mandatory
  documentation rule.
- docs updates (`CLAUDE.md`, `PROJECT_STATUS.md` when implemented)

## 11) Performance and reliability guardrails

- Cache FX rates and latest prices per request run.
- Avoid N+1 asset/price queries where possible (batch if helper exists).
- Keep deterministic output ordering for stable tests.
- Return partial plans with warnings rather than 500 where possible.

## 12) Acceptance criteria (definition of done)

Feature is done when all are true:

1. `POST /api/v1/rebalance/plan` returns 3 strategy plans with trade rows.
2. At least one strategy is demonstrably more tax-efficient on test fixtures.
3. Rebalance page can generate and render plans end-to-end.
4. Locked/excluded symbols and trade/gain caps are enforced and visible.
5. Python and JS tests pass.
6. Docs updated after implementation:
   - `PROJECT_STATUS.md`
   - relevant sections in `CLAUDE.md`

## 13) Risks and mitigations

- **Risk:** Tax estimate mismatch vs filing report.  
  **Mitigation:** label estimates clearly; reuse IRPF rate helper; include warning.

- **Risk:** Large portfolios cause slow planning.  
  **Mitigation:** request-level caches + limit default `max_trades`.

- **Risk:** Users over-trust single plan.  
  **Mitigation:** always return alternatives and warnings.

## 14) Future extensions (post-MVP)

- Lot-level sell overrides in UI (manual lot picker)
- "New symbol buy" mode using watchlist/research targets
- Scenario persistence (`rebalance_plans` table)
- Action Items integration when drift exceeds threshold and no plan exists
