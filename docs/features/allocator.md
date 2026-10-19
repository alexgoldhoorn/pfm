# Next-euro allocator

Answers two questions with rules it can show: how much of the household's cash is
deployable this month, and where a given amount should go. No LLM is involved and
nothing is persisted except one settings key. Spec:
`docs/superpowers/specs/2026-10-19-next-euro-allocator-design.md`.

Code: `services/deployable.py` (amount), `services/allocator.py` (proposal),
`services/wash_sale.py` (antiaplicación, shared with the MCP `wash_sale_check`),
`portf_server/routers/allocate.py`, `check_allocation` in `services/action_items.py`.
No schema change (schema stays 35).

## Deployable cash (`services/deployable.py`)

```
liquid       = counted (in_total) lines of the balance sheet's `cash` group
deployable   = max(0, liquid - tax_reserve - buffer_months_target x monthly_spend)
buffer_months = (liquid - tax_reserve) / monthly_spend
```

Monthly spend is `retirement.spending_inputs` with the plan's exclusions minus
"Social Security" (the autónomo quota is a fixed outflow today). Fewer than 6
complete imported months gives `insufficient_data`.

**Buffer band** (months of spend): `rebuild` below `buffer_months_min` (4),
`topping_up` from 4 to the target (6), `ok` from the target to `buffer_months_max`
(8), `idle` above 8. In the first two `deployable_eur` is 0 by the formula; in
`idle` the response also carries `idle_eur = liquid - reserve - max x spend`.

**Tax reserve** (autonomo), per calendar quarter:

```
iva     = gross x iva_pct / (100 + iva_pct)
net     = gross - iva
irpf    = max(0, net x irpf_pct/100 - net x irpf_retention_pct/100)
accrued = max(0, iva + irpf)
```

- *Work income* is not the whole income category: only positive, non-transfer rows
  in `income_category` whose `merchant` is in `work_income_merchants` (the tick
  boxes in the settings form, listing the merchants seen in the last 12 months).
  An empty list counts every merchant and adds a warning. The response lists the
  merchants it counted.
- *Retention*: `irpf_retention_pct` is the share each invoice already withholds.
- The IRPF part is floored at 0 on its own, so a retention above the IRPF rate
  never cancels the IVA.
- *Settlement*: an outgoing (negative) row in `tax_paid_categories` dated up to 30
  days after a quarter end, or up to 5 days after its due date when that is later
  (a weekend or holiday shifts the filing; this is what lets a Q4 payment on
  2 February count), is that quarter's payment; refund rows are ignored; other tax rows (the annual settlement)
  are ignored. The reserve is the current quarter's accrued plus the previous
  quarter's remainder, which stays reserved until a payment is seen or 30 days
  past its due date (the 20th of the month after the quarter, 30 January for Q4);
  after that the quarter is dropped as past due.
- Modelo 130 is cumulative over the year in law; the flat per-quarter figure is an
  estimate. Social security is ordinary spending and is not reserved.

`coming_in` lists a deposit maturing within 30 days (deposits are not liquid).

## Settings

One `app_settings` JSON key, `allocator_settings` (`GET|PUT /allocate/settings`).
Defaults: `buffer_months_target 6`, `buffer_months_min 4`, `buffer_months_max 8`,
`iva_pct 21`, `irpf_pct 20`, `irpf_retention_pct 0`, `income_category "Income"`,
`work_income_merchants []`, `tax_paid_categories ["Income Tax"]`,
`managed_portfolio_ids []` (filled with a portfolio named like "indexa" when one
exists, and the response says so, but only while the key was never stored: a saved
empty list is the owner turning the default off, and an unrelated save leaves the
key absent), `min_line_eur 100`,
`max_lines_per_bucket 2`, `max_single_stock_pct 5`, `traspaso_threshold_pp 5`.
Validation: `0 < min <= target <= max <= 24`, percentages 0-100, amounts >= 0,
portfolio ids must exist.

## Proposal (`services/allocator.py`)

1. **Targets.** Buckets are the raw `asset_type` keys of `allocation_targets`
   (`merge_targets`). `targets_incomplete` when a held type above 1 % has no
   target or the targets do not sum to 100 (a reason list, never a 500).
2. **Renormalisation.** `cash` and any bucket with no candidate at all are left
   out and the remaining targets are renormalised to 100. A bucket that has
   candidates which are all excluded (blocked, SELL, over the cap, ...) keeps its
   share, and the money stays `unallocated` with the reasons named. The denominator is the
   positions value from `compute_before_state`, not net worth.
3. **Traspasos first.** Any bucket holding non-ETF funds that is overweight by more
   than `traspaso_threshold_pp`, with another bucket holding such funds underweight,
   yields a traspaso of the smaller gap from the highest-TER (or smaller overlap
   member) fund to the best-ranked fund of the underweight bucket. A fund blocked by
   the antiaplicación rule is never a destination (it may be a source: nothing is
   sold), and a traspaso under `min_line_eur` is not proposed. with the gain a sale would have
   realised (FIFO). Only funds move (`mutual_fund`, exchange `"Funds"`, or a fund
   word in the name of a non-ETF); ETFs and managed portfolios never do.
4. **Water-filling.** New money is split over underweight buckets on the
   post-traspaso state, proportional to gap and capped at each gap, the remainder
   by target weight. Overweight buckets get nothing; if none is underweight the
   amount follows target weights. A line under `min_line_eur` goes back to the pool.
5. **Ranking per bucket**, at most `max_lines_per_bucket` lines, each with its
   reasons and a runner-up "why not":
   - funds and ETFs: drop the smaller member of a consolidation-candidate overlap
     group, then lowest TER (unknown TER last), then the larger existing position;
   - stocks: blocked, SELL-rated, HOLD-rated above their `buy_below`, over-cap and
     unresearched ones out; at or below `buy_below` first (the native price against
     a target in the stock's own currency, never the EUR price), then report
     recommendation and note conviction, then lowest weight. A fund typed `stock`
     (the shared fund heuristic) is ranked fund-style (overlap, TER) and is exempt
     from the single-stock cap and from `needs_research`; when a `stock` bucket
     holds both, each kind is ranked in its own list and the bucket takes the best
     of each in turn, fund-style first, up to `max_lines_per_bucket`;
   - crypto: held coins in proportion to current value; bonds: lightest holding.
6. **Single-stock cap.** A stock line is clamped so its weight after the buy stays
   within `max_single_stock_pct`; the excess spills to the next candidate and
   otherwise to `unallocated_eur` with a reason.
7. **Antiaplicación** (`services/wash_sale.py`, art. 33.5 LIRPF): an asset sold at a
   loss inside its window (2 months listed, 1 year unlisted fund units) is
   listed under `blocked` with the date it is safe. A blocked candidate of any
   kind (stock, fund, ETF, bond, other) is dropped before ranking and named in the
   reason when its bucket ends up unallocated. If the rule cannot be evaluated
   (`WashSaleUnavailable`), ruling R1: no stock is proposed (every stock is blocked
   with that reason), and funds and ETFs are still proposed, with a warning that
   their window could not be checked.
8. **Managed portfolios** (`managed_portfolio_ids`) are one line, "contribution to
   <portfolio>", with the account's value-weighted TER. Every line carries
   `portfolio_id`/`portfolio_name` (where the order goes).
9. **Unresearched stocks** are listed under `needs_research`, not proposed.

The bucket table's `before_pct` is the mix as it stands now, before the
traspasos; `after_pct` includes the traspasos and the new money.
`deployable()` is cached for 60 seconds in `kv_cache` (keyed on the date, bypassed
when `fx` or `settings` are passed explicitly, cleared by a settings PUT) and is
used by `next_euro` and `check_allocation`.

An `amount` above deployable is allowed with a warning; `nothing_to_deploy` only
applies when no amount was given and deployable is 0.

## Endpoints (`portf_server/routers/allocate.py`, API key)

- `GET /api/v1/allocate/deployable`: `status` (`ok`, `insufficient_data`),
  `as_of`, `liquid_eur`, `liquid_lines`, `tax_reserve` (`reserve_eur`, `quarters`
  with gross, IVA, IRPF, paid, accrued, due, `merchants_counted`), `buffer`
  (`monthly_spend_eur`, `months_used`, `target/min/max_months`, `target_eur`,
  `months`, `status`, `idle_eur`), `deployable_eur`, `coming_in`, `stale_lines`,
  `warnings`.
- `GET|PUT /api/v1/allocate/settings`: the settings plus the merchants seen.
- `GET /api/v1/allocate/next-euro?amount=`: `status` (`ok`, `nothing_to_deploy`,
  `targets_incomplete`, `insufficient_data`), `deployable`, `amount_eur`,
  `buckets` (target, before/after %, gap, allocated), `lines` (symbol, name,
  amount, `reasons`, `runner_up`, `portfolio_*`), `traspasos`, `blocked`,
  `needs_research`, `unallocated_eur`, `chat_summary` (the text the MCP tool
  prints), `warnings`. Plain `def` endpoints.

## UI

Assets page "Next euro" card (`#nextEuroCard`, collapsed): deployable breakdown
with the buffer gauge, amount input, bucket table, lines with reasons, traspasos,
blocked, needs-research, "Copy for chat", and the settings form with the work
merchant checkboxes. Dashboard attention strip `#dashNextEuro` (figure, buffer
badge, top lines; hidden on `insufficient_data`). Help keys `ne_*`.

## Action Items

`check_allocation` (category `allocate`): `allocate:idle_cash` (low, buffer above
the ceiling), `allocate:buffer_low` (medium, below the floor),
`allocate:targets_incomplete` (low). No item when there are no targets at all.

## MCP

`next_euro(amount=None)` (read-only) prints `chat_summary` whatever the status; only
when there is none does it fall back to one line `Next euro: <status> - <reason>`. Tests are in `mcp/test_server_local.py`
until they move to `~/mcp/pfm`. The gateway, finance adapter and persona register
it after the merge.

## Known limits

- An asset held in two portfolios is attributed to the larger one.
- Region is not used as a fund tie-break (the spec's informational rule).
- Modelo 130 is a flat per-quarter estimate, not cumulative.
- Traspasos are only shown when there is new money to deploy.
- `wash_sale.py` imports the private `analytics._build_tax_report_data`, and the
  FX helpers (`_get_fx_rate`, `_get_fx_rate_on`) come from the portfolios router.
