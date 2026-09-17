# Fund look-through exposure — design

Date: 2026-09-16
Status: approved, not implemented

## Problem

`GET /api/v1/analytics/diversification` reports **61% of sector and 63% of
country exposure as "Unknown"**. That share is almost exactly the funds and
ETFs: they are more than half the book by value, and a fund resolves to no
sector and no country through yfinance's per-symbol `.info` lookup.

Three consequences:

- Concentration cannot be checked. A global equity fund and a set of direct
  equity positions may be concentrated in the same names, sectors or region,
  and nothing says so.
- Overlap is invisible. Several emerging-market products tracking
  near-identical indices read as several unrelated positions.
- Portfolio Health scores diversification from data that is 60% unknown,
  without being told so.

Anything that allocates new money by region or sector — sub-project C, the
"next euro" allocator — needs this first.

`by_currency` has a related flaw: it reports the *quote* currency. A
EUR-quoted world equity fund is mostly USD exposure, so a book that reads
79% EUR is not one.

## Scope

**In:** exposure rollups (asset class, region, sector, currency) with funds
looked through; fund overlap detection; a stored, editable profile per fund;
the coverage figure that says how much of the book is actually classified.

**Out:** allocation targets by region and the allocator itself (sub-project
C); TER and cost drag; stock-level look-through — PDT's X-ray already expands
the same funds into their underlying holdings, and duplicating it needs a
full-holdings data source per issuer; importing PDT X-ray (no public API
found, only the claude.ai connector).

## Data sources, and why

| Source | Gives | Used for |
|---|---|---|
| `benchmarks.json` (in-repo) | region weights per index | **regions** — the part the allocator depends on |
| yfinance `funds_data` | `sector_weightings`, `asset_classes` | sectors and the equity/bond/cash split |
| LLM with search (Gemini) | a drafted profile | funds that track no index |
| manual | any field | corrections, and anything else |

Verified against the live book before choosing: yfinance `funds_data` returned
sector weights and asset classes for every fund tested, including Morningstar
`0P…` tickers for the fund share classes, but exposes **no country or region
data at all** and only the top 10 holdings. Region therefore cannot come from
yfinance, and benchmark mapping is what fills it.

Benchmark mapping fits this book because nearly every fund held is an index
fund, sector ETFs included; roughly a dozen indices cover all of them.

## Data model

### `fund_profiles` (db v30)

| column | type | notes |
|---|---|---|
| `asset_id` | INTEGER PK | FK → `assets`, ON DELETE CASCADE |
| `benchmark_key` | TEXT NULL | key into `benchmarks.json` |
| `source` | TEXT | `benchmark` \| `llm` \| `manual` |
| `asset_class` | TEXT (JSON) | `{equity, bond, cash, other}`, fractions summing to 1 |
| `regions` | TEXT (JSON) | fixed taxonomy below, fractions summing to 1 |
| `sectors` | TEXT (JSON) | GICS-style names, matching what direct stocks report |
| `currency_hedged` | INTEGER | 0/1 |
| `hedge_currency` | TEXT NULL | share-class currency when hedged |
| `as_of` | TEXT | ISO date of the underlying data |
| `updated_at` | TIMESTAMP | |
| `notes` | TEXT NULL | |

Weights are **stored, not derived per request**: a profile can be reviewed,
diversification needs no live fund lookup, and a manual edit is not silently
overwritten. A refresh recomputes a profile only when `source != 'manual'`,
unless `force` is passed.

⚠️ The table must appear in **both** `_create_all_tables` and
`_migrate_to_v30`; a migration-only add breaks fresh installs.

### Region taxonomy (fixed)

`north_america`, `europe_ex_uk`, `uk`, `japan`, `pacific_ex_japan`,
`emerging`, `unknown`.

Region, not country: it is detailed enough for allocation targets, and
country-level index weights would be far more upkeep for no decision value.

Direct stocks map to a region via a country → region dict in code, from the
country yfinance already returns for them.

### `portf_manager/data/benchmarks.json`

About 12 entries:

```json
{
  "msci_world": {
    "label": "MSCI World",
    "family": "developed_global",
    "parent": "acwi",
    "asset_class": {"equity": 1.0},
    "regions": {"north_america": 0.72, "europe_ex_uk": 0.13, "uk": 0.04,
                "japan": 0.06, "pacific_ex_japan": 0.03, "emerging": 0.02},
    "as_of": "2026-09-01",
    "aliases": ["MSCI World Index", "MSCI World IMI"]
  }
}
```

`family` groups indices with the same exposure (MSCI EM ≈ MSCI EM IMI ≈ FTSE
EM All Cap); `parent` expresses containment (S&P 500 ⊂ MSCI World). Both feed
overlap detection. Index weights are public data, so the file belongs in the
public repo.

### Which assets need a profile

Held assets typed `etf`, `mutual_fund` or `index`. Two held index funds are
currently typed `stock` from a heuristic import and are corrected during
rollout — a data fix via `PUT /api/v1/assets/{id}`, not code.

## Exposure service

New `portf_manager/services/exposure.py`. Both `/analytics/diversification`
and `portfolio_advisor.gather_diversification` delegate to it; the duplicated
breakdown loop in `analytics.py` goes away.

Per held position:

- **Fund with a profile** — value split by `asset_class`; the equity part
  split by `regions` and `sectors`.
- **Direct stock** — today's sector/country resolution, plus country → region.
- **Crypto** — its own asset class; excluded from region and sector.
- **Fund with no profile** — value goes to `unknown` **and the fund is named**
  in `coverage.unprofiled` with its value. Nothing lands anonymously in
  "Unknown" again.

Currency exposure is derived from regions (`north_america` → USD, `uk` → GBP,
`japan` → JPY, and so on), with a hedged fund counted in its share-class
currency. It is an approximation — `europe_ex_uk` is mostly but not only EUR —
and is labelled as one in the API and the UI.

## API

### `GET /api/v1/analytics/diversification` — additive only

`~/mcp/scripts/finance_review.py` (the Sunday review) consumes this endpoint,
so `by_asset_type`, `by_currency`, `by_sector`, `by_country` and
`concentration_hhi` keep their names. New fields:

| field | content |
|---|---|
| `by_asset_class` | equity / bond / cash / crypto, funds looked through |
| `by_region` | all asset classes |
| `by_region_equity` | equity only — what allocation targets will use |
| `by_currency_exposure` | look-through currency, flagged approximate |
| `coverage` | `{classified_pct, unprofiled: [...], stale_profiles: [...]}` |

`by_sector` keeps its key and gains look-through content. `by_country` stays
for direct holdings; fund value appears there as a single "Via funds (see
regions)" bucket rather than as Unknown.

### `GET /api/v1/analytics/fund-overlap`

Groups of held funds with the same exposure:

1. **Same `family`** → `consolidation_candidate`, severity medium.
2. **`parent` containment** (a country or sector fund inside a global one) →
   `informational`: a deliberate tilt is legitimate, so this never becomes an
   action item.
3. **No benchmark on either side** → cosine similarity of the region and
   sector vectors ≥ 0.95 within the same asset class → `similar`.

Each group carries its members (asset id, name, portfolio, value), the
combined weight, and `transferable`: true when every member is a
`mutual_fund`, i.e. consolidation can go through a traspaso rather than a
taxable sale. Members are listed per portfolio, so overlap *inside* a
managed robo-advisor account reads as that and not as something to act on.

### `GET|PUT /api/v1/fund-profiles/...`

Plain `def` (blocking yfinance and LLM calls), following the project's
threadpool convention.

| route | does |
|---|---|
| `GET /api/v1/fund-profiles/` | held funds with profile status |
| `GET /api/v1/fund-profiles/benchmarks` | dropdown source |
| `GET /api/v1/fund-profiles/{asset_id}` | one profile |
| `PUT /api/v1/fund-profiles/{asset_id}` | edit; sets `source=manual` |
| `POST /api/v1/fund-profiles/{asset_id}/refresh` | recompute; refuses a manual profile without `force=true` |
| `POST /api/v1/fund-profiles/{asset_id}/suggest` | LLM draft, writes nothing |

⚠️ `benchmarks` must be registered **before** `/{asset_id}`, or FastAPI
matches it as an id — the hazard already documented for `research.py`'s
`compare` and `portfolio-analysis`.

Validation on `PUT`: each weight map sums to 1.0 ±0.005; unknown region keys
are rejected; an unknown `benchmark_key` is rejected.

## Failure behaviour

- yfinance `funds_data` fails → the profile keeps its benchmark regions,
  `sectors` stays empty, and sector coverage reports the gap. No zero-fill.
- Region data does not depend on yfinance at all, so the allocator's input
  survives a yfinance outage or schema change.
- An LLM suggestion is never auto-applied; `source=llm` is recorded so a
  drafted profile stays identifiable afterwards.
- A fund with no profile is always *named*, never folded silently into
  "Unknown".

## Web client

**Analytics → diversification section** gains:

- A **coverage banner**: "84% of value classified. 3 funds have no profile:
  …", each name linking to its editor. Below full coverage, the breakdowns
  never read as complete.
- Bars for asset class, region and currency exposure, alongside today's
  sector, country and HHI.
- An **overlap card**: consolidation candidates first, informational nesting
  behind a "show" toggle.

**Fund profile editor** (`#fpModal`), opened from the coverage banner, the
overlap card, or a per-row action on fund rows of the Assets page. Benchmark
dropdown, auto-filled but editable region weights, sector weights, hedged
checkbox, `as_of`, and "Suggest (AI)" which fills the form for review without
saving. Save blocked unless weights sum to 100 (±0.5).

Pure helpers at module scope, `window.`-exported for the DOM-free runner
(the budget helpers' pattern): `fundProfileValidate`, `normalizeWeights`,
`regionLabel`, `overlapGroupLabel`.

## MCP

Extend the existing `diversification` tool with asset class, region, currency
exposure, coverage and overlap. **No new tool** — that avoids a second
three-place registration (gateway, `~/.claude/agents/finance.md`,
`~/agents/finance/persona.md`) and puts overlap in front of the finance agent
by default.

## Action Items

New `check_fund_exposure` in `services/action_items.py`, the eighth check,
wrapped in its own try/except like the rest. Category `exposure`:

| condition | severity | id |
|---|---|---|
| Held fund with no profile | medium | `exposure:profile:{asset_id}` |
| Profile `as_of` older than 12 months | low | `exposure:stale:{asset_id}` |
| Same-family overlap group | low | `exposure:overlap:{asset_ids joined}` |

Deterministic ids, so dismissing one does not hide a later, different one.

## Portfolio Health

Picks up look-through for free through `gather_diversification`. One prompt
change: pass `coverage.classified_pct`, so the model cannot score
diversification confidently from unknown data — which is what it does today.

## Testing

- **Exposure service (fake db):** region and sector splitting; an unprofiled
  fund appearing in `unknown` *and* in the named list; hedged vs unhedged
  currency exposure; crypto excluded from region and sector; coverage
  arithmetic.
- **Overlap:** one test per rule (family, nested, cosine), plus
  `transferable`.
- **`benchmarks.json` schema:** every entry's weights sum to 1.0, aliases
  resolve, `parent`/`family` refer to known keys, `as_of` present. A
  hand-edited data file without a test drifts silently.
- **Regression pin:** `/analytics/diversification` still returns the five
  keys `finance_review.py` reads.
- **Route order:** `GET /fund-profiles/benchmarks` is not matched as an id.
- **Refresh:** refuses a manual profile without `force`.
- **DB:** v30 present in both creation paths; `tests/test_database.py`
  version assertion bumped to 30 (four places).
- **JS:** the four pure helpers.

yfinance and the LLM are mocked throughout; no network in the suite.

## Rollout

1. Migration, `benchmarks.json`, exposure service, API fields. Nothing
   visible changes.
2. **Backfill and verify:** profiles for the held funds, correct the two
   index funds typed `stock`, set missing tickers. Success is measurable —
   sector coverage rises from 39% to above 95%, and `by_region_equity`
   becomes plausible for a book whose largest single position is a global
   developed-market equity fund.
3. UI, action items, MCP output.

## Risks

| Risk | Handling |
|---|---|
| Index weights drift a few points a year | `as_of` per benchmark plus the staleness action item — visible, not pretended static |
| Two held funds have no `ticker`, so no sectors | Data fix in step 2; coverage shows it if missed |
| LLM invents a profile | Never auto-applied; reviewed before save; `source=llm` recorded |
| yfinance `funds_data` changes shape | Sectors empty, coverage reports it; regions unaffected |
| Double counting | Fund value is *split*, never added on top of the fund's own value; the asset-class map sums to 1 by validation |

## Follow-up, outside this repo

`~/mcp/scripts/finance_review.py` (Sunday finance review) reads
`/analytics/diversification`. A few lines there would add region exposure and
coverage to the weekly message. Separate repo, done after this lands.
