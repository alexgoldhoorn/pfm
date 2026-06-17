# Import/Export Page — Tab Redesign

**Date:** 2026-06-17
**Status:** Approved

## Problem

The Import/Export page stacks six heterogeneous sections in a long scrollable grid. Users must scroll past irrelevant sections to reach what they need (e.g., the Bookings table or Google Sheets Sync).

## Design

Reorganise the six sections into three tabs using the existing Analytics button-group pattern for visual and behavioural consistency.

### Tab structure

| Tab | Sections | Layout |
|-----|----------|--------|
| **Import** | File Import + AI Text Import | Two columns (col-12 col-lg-6 each), same as today |
| **Export** | Export (CSV/PDT/Backup) + Platform Export | Full-width cards stacked |
| **Data** | Bookings table + Google Sheets PDT Sync | Full-width cards stacked |

### Tab implementation

Mirrors the Analytics pattern exactly:

- Buttons carry `data-io-tab="import|export|data"` (instead of `data-an-tab`)
- Sections (cards) carry `data-io-section="import|export|data"` (instead of `data-an-section`)
- `showImportExportTab(tab)` mirrors `showAnalyticsTab(tab)`:  hides all `[data-io-section]` cards, shows only those matching the active tab, updates button active state
- `setupImportExportTabs()` wires click listeners and calls `showImportExportTab('import')` on load

### Lazy loading

The Bookings table currently calls `loadBookings()` eagerly on `setupImportExportPage()`. Under the new design it loads lazily: `showImportExportTab('data')` triggers `loadBookings()` on the first visit to the Data tab (guarded by a `_dataTabLoaded` flag), matching the Analytics lazy-loader pattern.

### HTML changes (`index.html`)

1. Add the button-group tab bar below the page header (before the `<div class="row g-4">`).
2. Add `data-io-section="import"` to the two import cards.
3. Add `data-io-section="export"` to the Export and Platform Export cards.
4. Change both export cards from `col-12 col-lg-6` / `col-12` to `col-12` (full width, stacked).
5. Add `data-io-section="data"` to the Bookings and Google Sheets Sync cards.

### JS changes (`pfm_features.js`)

1. Add `showImportExportTab(tab, forceReload)` function.
2. Add `setupImportExportTabs()` function called at the end of `setupImportExportPage()`.
3. Move `loadBookings()` call inside `showImportExportTab` behind the `_dataTabLoaded` guard (remove the eager call at the bottom of `setupImportExportPage`).

## Out of scope

- No changes to section content, form fields, or API calls.
- No changes to the page's help text or sidebar label.
- No tab state persistence in `PREFS` (Import is always the landing tab).
