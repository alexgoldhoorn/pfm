# Customizable dashboard: design

**Why.** The dashboard grew block by block and the order no longer fits how it
is read: to-do style content crowded the first screen. The owner should be able
to put the blocks in their own order and hide the ones they do not use.

**Goals.** Reorder and hide whole dashboard blocks, on desktop and phone; the
same layout on every device; new blocks added later land in a sensible place.
**Non-goals.** Moving single cards inside a block, resizing, per-device layouts,
multiple saved layouts. **Anti-goal.** A layout that can lose a block for good:
every hidden block must be one tap from coming back, and Reset restores the default.

## Approaches

1. **Edit mode with drag handles (chosen).** A Customize button collapses every
   block to a bar with a grip and an eye. Needs a small drag library for touch.
2. Edit mode with up/down arrows only. No library, but clumsy for long moves.
3. Presets (Overview / Investing / Spending first). Simplest, but cannot fix the
   one thing that is out of place.

Chosen: 1, with the arrows of 2 kept on each bar as the keyboard path and the
fallback when the CDN library does not load.

## Design

- The KPI tiles stay pinned. Everything below is a `.dash-block` with a stable
  `data-block` id inside `#dashBlocks`.
- Layout = `{order, hidden}` of block ids in `app_settings` (`dashboard_layout`),
  read and written through `GET/PUT /api/v1/system/dashboard-layout`. No schema
  change. The server validates the id format only; which blocks exist is the
  client's business.
- `dashLayoutResolve(defaults, saved)` drops unknown ids and places a block the
  saved layout does not mention after its nearest default predecessor.
- Every change is saved at once; Reset asks first. A block that hides itself
  when empty still shows its bar in edit mode.
- First new blocks: **Net worth** (latest snapshot, what changed, line chart) and
  **Cash-flow outlook** (balance now, projected month end, low point of the next
  three months, next bills). Both only read existing endpoints.

Left to later: Upcoming, Month close, Mortgage, Subscriptions, Today's movers.
