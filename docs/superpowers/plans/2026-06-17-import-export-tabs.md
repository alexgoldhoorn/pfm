# Import/Export Page Tabs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganise the Import/Export page's six stacked sections into three tabs (Import / Export / Data) using the same button-group pattern as the Analytics page.

**Architecture:** Add a `data-io-section` attribute to each card's column wrapper in `index.html` and a `<div id="ioTabs">` tab bar, then add `showImportExportTab()` + `setupImportExportTabs()` as inner functions inside `setupImportExportPage()` in `pfm_features.js`. The Bookings table switches from eager to lazy-load (triggered on first Data tab click). No API or backend changes needed.

**Tech Stack:** Vanilla JS, Bootstrap 5 (btn-group style, not nav-tabs), no build step.

---

### Task 1: HTML — add tab bar and section attributes

**Files:**
- Modify: `web_client/index.html:1751–1997`

The Import/Export page runs from line 1745 to 1998. Current section wrappers have no `data-io-section` attribute; the Export card uses `col-12 col-lg-6` (half-width) which we widen to `col-12`.

- [ ] **Step 1: Insert the tab bar after the page header**

In `index.html`, find this line (≈ line 1751):
```html
                    </div>
                    <div class="row g-4">
```

Replace with:
```html
                    </div>
                    <div class="d-flex flex-wrap gap-1 mb-3" id="ioTabs">
                        <button type="button" class="btn btn-sm btn-outline-secondary active" data-io-tab="import"><i class="bi bi-file-earmark-arrow-up me-1"></i>Import</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-io-tab="export"><i class="bi bi-file-earmark-arrow-down me-1"></i>Export</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-io-tab="data"><i class="bi bi-bank me-1"></i>Data</button>
                    </div>
                    <div class="row g-4">
```

- [ ] **Step 2: Tag the two Import cards**

Find (≈ line 1754):
```html
                        <!-- File Import -->
                        <div class="col-12 col-lg-6">
```
Replace with:
```html
                        <!-- File Import -->
                        <div class="col-12 col-lg-6" data-io-section="import">
```

Find (≈ line 1809):
```html
                        <!-- LLM Text Import -->
                        <div class="col-12 col-lg-6">
```
Replace with:
```html
                        <!-- LLM Text Import -->
                        <div class="col-12 col-lg-6" data-io-section="import">
```

- [ ] **Step 3: Tag the two Export cards (and widen the first one)**

Find (≈ line 1844):
```html
                        <!-- Export -->
                        <div class="col-12 col-lg-6">
```
Replace with:
```html
                        <!-- Export -->
                        <div class="col-12" data-io-section="export">
```

Find (≈ line 1870):
```html
                        <!-- Platform Export -->
                        <div class="col-12">
```
Replace with:
```html
                        <!-- Platform Export -->
                        <div class="col-12" data-io-section="export">
```

- [ ] **Step 4: Tag the two Data cards**

Find (≈ line 1916):
```html
                        <!-- Bookings (cash transactions: deposits / withdrawals) -->
                        <div class="col-12">
```
Replace with:
```html
                        <!-- Bookings (cash transactions: deposits / withdrawals) -->
                        <div class="col-12" data-io-section="data">
```

Find (≈ line 1967):
```html
                        <!-- Google Sheets PDT Sync -->
                        <div class="col-12">
```
Replace with:
```html
                        <!-- Google Sheets PDT Sync -->
                        <div class="col-12" data-io-section="data">
```

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add tab bar and data-io-section attributes to Import/Export page"
```

---

### Task 2: JS — add tab functions, switch Bookings to lazy-load

**Files:**
- Modify: `web_client/js/pfm_features.js:1274,1415–1416`

`setupImportExportPage()` spans lines 832–1416. `loadBookings` is an inner async function (line 1248). The two new functions are also defined as closures inside `setupImportExportPage` so they share the same `loadBookings` reference without module-level plumbing.

- [ ] **Step 1: Remove the eager `loadBookings()` call**

In `pfm_features.js`, find (≈ line 1274):
```javascript
    loadBookings();
    const refreshBookingsBtn = document.getElementById('ioRefreshBookingsBtn');
```
Replace with:
```javascript
    const refreshBookingsBtn = document.getElementById('ioRefreshBookingsBtn');
```

The refresh button's existing listener (`refreshBookingsBtn.addEventListener('click', loadBookings)`) is left intact — it calls `loadBookings` directly and is unaffected by the lazy-load guard.

- [ ] **Step 2: Add `showImportExportTab`, `_ioDataTabLoaded`, and `setupImportExportTabs` at end of `setupImportExportPage`**

In `pfm_features.js`, find the last two lines of `setupImportExportPage` (≈ lines 1415–1416):
```javascript
    loadSyncConfig();
}
```
Replace with:
```javascript
    loadSyncConfig();

    let _ioDataTabLoaded = false;

    function showImportExportTab(tab) {
        document.querySelectorAll('#ioTabs [data-io-tab]').forEach(b =>
            b.classList.toggle('active', b.dataset.ioTab === tab));
        document.querySelectorAll('#importexportPage [data-io-section]').forEach(card => {
            card.style.display = card.dataset.ioSection === tab ? '' : 'none';
        });
        if (tab === 'data' && !_ioDataTabLoaded) {
            _ioDataTabLoaded = true;
            loadBookings();
        }
    }

    function setupImportExportTabs() {
        const tabs = document.getElementById('ioTabs');
        if (tabs && !tabs.dataset.wired) {
            tabs.dataset.wired = '1';
            tabs.querySelectorAll('[data-io-tab]').forEach(btn => {
                btn.addEventListener('click', () => showImportExportTab(btn.dataset.ioTab));
            });
        }
        _ioDataTabLoaded = false;
        showImportExportTab('import');
    }

    setupImportExportTabs();
}
```

- [ ] **Step 3: Run JS smoke test**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv make test-js
```

Expected: all tests pass (the smoke test loads all 4 JS files — catches syntax errors in the new code).

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: add Import/Export tab switching with lazy Bookings load"
```

---

### Task 3: Cache-bust, redeploy, and verify

**Files:**
- Modify: `web_client/index.html` (bump `?v=` for `pfm_features.js`)

- [ ] **Step 1: Bump the `pfm_features.js` version string**

In `index.html`, find (≈ line 3219):
```html
    <script src="js/pfm_features.js?v=1780000057"></script>
```
Replace with:
```html
    <script src="js/pfm_features.js?v=1780000058"></script>
```

- [ ] **Step 2: Rebuild and redeploy the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Expected: container starts, nginx serves the new JS.

- [ ] **Step 3: Manually verify all three tabs**

Open the app and navigate to Import / Export. Check:

1. **Import tab** (default): File Import and AI Text Import cards are visible side by side. Export, Platform Export, Bookings, and Sheets Sync cards are hidden.
2. **Export tab**: Export and Platform Export cards are visible full-width. Import and Data cards are hidden.
3. **Data tab**: Bookings table loads (spinner then data). Google Sheets Sync card is visible. Import and Export cards are hidden.
4. **Refresh button** on Bookings: clicking it reloads the bookings table correctly.
5. **Tab active state**: the active button has the `active` class (blue outline) and updates on click.

- [ ] **Step 4: Commit**

```bash
git add web_client/index.html
git commit -m "feat: bump pfm_features.js cache-buster for Import/Export tabs"
```
