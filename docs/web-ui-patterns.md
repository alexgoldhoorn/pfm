# Web UI Patterns

Design reference for the web client (`web_client/`). Follow these patterns for all new pages and components.

## Tab Navigation

Use Bootstrap 5 `nav-tabs` for all tabbed pages. This matches the Diagnostics and Help pages.

### Standard markup

```html
<!-- Tab bar -->
<ul class="nav nav-tabs mb-3" id="fooTabs">
    <li class="nav-item">
        <button type="button" class="nav-link active"
                data-bs-toggle="tab" data-bs-target="#fooTabFirst"
                id="fooTabBtnFirst">
            <i class="bi bi-icon me-1"></i>First
        </button>
    </li>
    <li class="nav-item">
        <button type="button" class="nav-link"
                data-bs-toggle="tab" data-bs-target="#fooTabSecond"
                id="fooTabBtnSecond">
            <i class="bi bi-icon me-1"></i>Second
        </button>
    </li>
</ul>

<!-- Tab panes -->
<div class="tab-content">
    <div class="tab-pane fade show active" id="fooTabFirst">
        <!-- content -->
    </div>
    <div class="tab-pane fade" id="fooTabSecond">
        <!-- content -->
    </div>
</div>
```

Bootstrap handles show/hide natively — no custom CSS or JS needed for the toggle itself.

### ID naming convention

| Element | Pattern | Example |
|---------|---------|---------|
| Tab bar `ul` | `{page}Tabs` | `#analyticsTabs` |
| Tab button | `{page}TabBtn{Name}` | `#anTabBtnPerformance` |
| Tab pane | `{page}Tab{Name}` | `#anTabPerformance` |

### Lazy loading

When a tab's content is expensive to fetch, load it on first activation using the `shown.bs.tab` event:

```javascript
function setupFooTabs() {
    const secondBtn = document.getElementById('fooTabBtnSecond');
    if (secondBtn && !secondBtn._fooWired) {
        secondBtn._fooWired = true;
        // shown.bs.tab fires after Bootstrap shows the pane (after fade)
        secondBtn.addEventListener('shown.bs.tab', () => {
            if (!_fooSecondLoaded) { _fooSecondLoaded = true; loadFooSecond(); }
        });
        // Also hook click with a short delay — guards against Bootstrap
        // animation timing issues (same pattern as Diagnostics page)
        secondBtn.addEventListener('click', () =>
            setTimeout(() => {
                if (!_fooSecondLoaded) { _fooSecondLoaded = true; loadFooSecond(); }
            }, 50));
    }
    _fooSecondLoaded = false;
    // Reset to default tab on each page visit
    const firstBtn = document.getElementById('fooTabBtnFirst');
    if (firstBtn && window.bootstrap) {
        const pane = document.getElementById('fooTabFirst');
        if (!pane || !pane.classList.contains('active')) {
            new window.bootstrap.Tab(firstBtn).show();
        }
    }
}
```

### Per-visit state reset

Pages that need fresh data each time they are opened should expose a reset function:

```javascript
// At the end of setupFooPage():
window.loadFooPage = () => setupFooTabs();
```

Wire it in the navigation switch in `pfm_features.js`:

```javascript
case 'foo': if (window.loadFooPage) window.loadFooPage(); break;
```

### Programmatic tab activation

To activate a tab via JS (e.g., restore last-used tab from `localStorage`):

```javascript
const pane = document.getElementById('fooTabSecond');
if (pane && pane.classList.contains('active')) {
    // Pane already visible — shown.bs.tab won't fire, call loader directly
    loadFooSecond();
} else {
    new window.bootstrap.Tab(document.getElementById('fooTabBtnSecond')).show();
    // shown.bs.tab fires → loader runs via listener
}
```

### Don't use

- `<div class="d-flex flex-wrap gap-1">` with `btn btn-sm btn-outline-secondary` buttons — this is the old pattern, replaced by `nav-tabs`.
- Custom `data-*-tab` / `data-*-section` attributes with manual `style.display` toggling.
- `data-bs-toggle="pill"` — reserved for the login modal only.
