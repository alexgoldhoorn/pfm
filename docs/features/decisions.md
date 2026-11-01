# Decision journal

> Added 2026-11-01 (schema v38). What was decided, why, what was expected, the
> price at the time, and how it turned out. CLAUDE.md keeps the must-know
> gotchas and links here; this file is the full reference.

## Why

Investment decisions ("hold AMD below the sell gate", "keep the monthly DCA,
no lump sum", "sell Fastned") were made in chats and digests and then lost:
the next conversation re-argued them from scratch, and nobody checked later
whether the call was right. The journal makes a decision a record with a
review date, and stores the price when it was made so the review compares
against a number rather than memory.

## Schema (`decisions`, v38)

| Column | Meaning |
|---|---|
| `decided_on` | Date of the decision (default today) |
| `symbol`, `asset_id`, `currency` | Optional asset; `asset_id` resolves through `get_asset_by_symbol` and is NULL for an unknown symbol (the typed symbol is kept, upper-cased) |
| `action` | `buy`, `sell`, `hold`, `dca`, `rebalance`, `traspaso`, `other` (CHECK) |
| `title`, `rationale`, `expectation` | One line, why, and what should be true if the call was right |
| `review_on` | When to look back; drives the Action Item |
| `price_at_decision` | Latest stored price of the asset when created, unless given explicitly |
| `outcome`, `outcome_rating`, `reviewed_at` | Filled by the review; `outcome_rating` in `good`, `bad`, `neutral`, `unclear` |
| `source` | `web`, `chat` or `api` |

Index `idx_decisions_review (reviewed_at, review_on)`. DB methods:
`create_decision`, `get_decision`, `get_decisions(status, symbol)`,
`update_decision`, `delete_decision` (`portf_manager/database.py`).

## API (`portf_server/routers/decisions.py`, prefix `/api/v1/decisions`)

- `GET /?status=open|reviewed|all&symbol=` → `{items, total, due}`. Each item
  is the row plus `status` (`open`, `due` when open and `review_on` has
  passed, `reviewed`), `price_now`, `price_now_date` and `price_change_pct`
  (null without an asset or a stored price). Newest decision first.
- `POST /` (201) — `title` and `action` required; `decided_on` defaults to
  today; a known `symbol` gets its latest stored price attached when
  `price_at_decision` is omitted.
- `GET /{id}`, `PUT /{id}` (partial; sending `symbol` re-resolves the asset
  and currency), `DELETE /{id}` (204).
- `POST /{id}/review` — `{outcome?, outcome_rating, reviewed: true}` stamps
  `reviewed_at`; `reviewed: false` re-opens and clears outcome, rating and
  stamp.

`enrich_decision`, `list_decisions_enriched` and `create_decision_entry` are
module functions shared with the chat tools, so the three surfaces agree.

## Action Item

`check_decision_reviews` (`services/action_items.py`, category `decisions`,
`medium`) raises `decision:review:{id}` for every open decision whose
`review_on` is today or earlier; the detail adds the price move since the
decision when both prices are known. `link_page: decisions`.

## Chat tools (`portf_server/chat_tools.py`)

- `get_decisions(status, symbol)` — the journal with price context. The
  description tells the model to check it before advising on a holding the
  user already decided about.
- `record_decision(title, action, symbol?, rationale?, expectation?,
  review_on?)` — the second write-capable chat tool (after
  `set_action_item_status`). It writes only the journal, with
  `source='chat'`, and its description says to record only a decision the
  user has explicitly made, never a suggestion.

## MCP tool (`mcp/pfm_mcp/tools/research.py`)

`decisions(status='open', symbol=None)` — read-only listing with the price
then/now line, rationale, expectation, review date and outcome. Recording a
decision from the phone is deliberately not offered: the gateway's write tools
are limited to Trilium and device control.

## Web (`web_client/`)

Sidebar → Insights → **Decisions** (`#decisionsPage`): add form, Open / Due /
Reviewed / All filter, per-row Review (modal), Reopen and Delete. Pure helpers
`decisionStatusMeta`, `decisionActionMeta`, `decisionPriceMoveHtml` are tested
in `web_client/js/tests/decisions.test.mjs`. `PAGE_HELP.decisions` explains
the loop.

## Design notes

- No link from a decision to the transaction that executed it: a decision is
  often "do nothing" or "keep doing", and matching trades would mis-attribute
  the DCA buys.
- No LLM-written outcomes. The review is the owner's judgement.
- Tests: `tests/unit/test_decisions.py` (router, Action Item, chat tools),
  `mcp/tests/test_attribution_decisions.py`.
