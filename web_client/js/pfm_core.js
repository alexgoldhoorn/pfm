// pfm_core.js — part of the portfolio_debug.js split.
// Core: prefs, formatters, esc, dashboard/diagnostics helpers, AssetSearch, API + modal managers, import modals.
// Classic script (no build step): these files share one global scope
// and MUST load in this order: pfm_core, pfm_pages, pfm_analytics,
// pfm_features. See index.html.

// Portfolio Manager — Web Client

// ---------------------------------------------------------------------------
// User preferences (browser-local) + central formatting
// ---------------------------------------------------------------------------
const PREFS_KEY = 'pfmPrefs';
const PREFS_DEFAULTS = {
    numberLocale: '',      // '' = browser default; e.g. 'en-US', 'es-ES', 'de-DE', 'nl-NL', 'en-GB'
    decimals: 2,
    dateFormat: 'iso',     // 'iso' (2026-05-28) | 'dmy' (28-05-2026) | 'mdy' (05-28-2026)
    theme: 'auto',         // 'auto' | 'light' | 'dark'
    privacy: false,        // blur monetary amounts
    benchmark: '^GSPC',
    landingPage: 'dashboard',
    rowsPerPage: 50,
    defaultCurrency: 'EUR',   // pre-fills currency on new assets/transactions/bookings
    defaultBroker: '',        // portfolio/broker name to preselect on new entries
    holdingsSort: 'value',    // value | pnl | pnlpct | name
    hideBelowEur: 0,          // hide holdings below this EUR value (0 = show all)
    dashTopPositions: { n: 5, type: 'all', broker: 'all', sort: 'value' },
    tableState: {},   // per-table sort/filter, keyed by table (holdings, transactions, assets, portfolios)
};
window.PREFS = Object.assign({}, PREFS_DEFAULTS, (() => {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'); } catch (e) { return {}; }
})());
function savePrefs() { localStorage.setItem(PREFS_KEY, JSON.stringify(window.PREFS)); }

const Fmt = {
    loc() { return window.PREFS.numberLocale || undefined; },
    num(v, min, max) {
        const d = (window.PREFS.decimals != null) ? window.PREFS.decimals : 2;
        return parseFloat(v || 0).toLocaleString(this.loc(), {
            minimumFractionDigits: (min != null ? min : d),
            maximumFractionDigits: (max != null ? max : d),
        });
    },
    // Wrap money text so the privacy toggle can blur it (hover to reveal).
    amt(text) { return `<span class="pfm-amt">${text}</span>`; },
    date(s) {
        if (!s) return '';
        const str = String(s);
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(str);
        if (!m) return str;
        const [, y, mo, d] = m;
        const time = str.length > 10 ? str.replace('T', ' ').slice(11, 16) : '';
        let out;
        if (window.PREFS.dateFormat === 'dmy') out = `${d}-${mo}-${y}`;
        else if (window.PREFS.dateFormat === 'mdy') out = `${mo}-${d}-${y}`;
        else out = `${y}-${mo}-${d}`;
        return time ? `${out} ${time}` : out;
    },
};
window.Fmt = Fmt;

// Escape text before interpolating it into innerHTML. Asset names, symbols,
// notes and broker names come from imported broker files and LLM extraction —
// untrusted — and the API key lives in localStorage, so an unescaped value
// could script-inject and exfiltrate it. Use this for any such field.
function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}
window.esc = esc;

// Pure, DOM-free filter+sort for the dashboard Top Positions card (unit-tested
// in web_client/js/tests/). Drops zero/negative-quantity positions, filters by
// asset type, sorts by the chosen mode, then takes the top N.
function _topVal(h) { return parseFloat(h.total_value_eur || h.total_value || 0); }
function _topPct(h) { return parseFloat(h.pnl_pct || 0); }
function _topAmt(h) { return parseFloat(h.pnl_amount || 0); }
const TOP_POSITION_SORTS = {
    value:      (a, b) => _topVal(b) - _topVal(a),
    gain_pct:   (a, b) => _topPct(b) - _topPct(a),
    loss_pct:   (a, b) => _topPct(a) - _topPct(b),
    gain_total: (a, b) => _topAmt(b) - _topAmt(a),
    loss_total: (a, b) => _topAmt(a) - _topAmt(b),
};
function topPositions(holdings, opts) {
    const { n = 5, type = 'all', sort = 'value' } = opts || {};
    let list = (holdings || []).filter(h => parseFloat(h.quantity || 0) > 0);
    if (type && type !== 'all') {
        list = list.filter(h => (h.asset_type || 'other') === type);
    }
    list = list.slice().sort(TOP_POSITION_SORTS[sort] || TOP_POSITION_SORTS.value);
    if (n === 'all' || n == null) return list;
    return list.slice(0, Number(n));
}
window.topPositions = topPositions;

// Pure filter+sort for the shared sortable tables (unit-tested). `columns` is
// [{key, type:'text'|'num'|'date', filter?}]; `state` is {sort:{key,dir}, filters:{key:value}}.
// Blanks/missing always sort last; returns a new array (no mutation).
function applyTableState(rows, columns, state) {
    const byKey = Object.fromEntries((columns || []).map(c => [c.key, c]));
    let out = (rows || []).slice();
    const filters = (state && state.filters) || {};
    for (const [k, v] of Object.entries(filters)) {
        if (v && v !== 'all') out = out.filter(r => String(r[k] == null ? '' : r[k]) === String(v));
    }
    const s = state && state.sort;
    if (s && s.key && byKey[s.key]) {
        const type = byKey[s.key].type || 'text';
        const dir = s.dir === 'asc' ? 1 : -1;
        out.sort((a, b) => {
            const av = a[s.key], bv = b[s.key];
            const ab = av == null || av === '';
            const bb = bv == null || bv === '';
            if (ab && bb) return 0;
            if (ab) return 1;   // blanks last regardless of direction
            if (bb) return -1;
            let cmp;
            if (type === 'num') cmp = (parseFloat(av) || 0) - (parseFloat(bv) || 0);
            else if (type === 'date') cmp = String(av).localeCompare(String(bv));
            else cmp = String(av).toLowerCase().localeCompare(String(bv).toLowerCase());
            return cmp * dir;
        });
    }
    return out;
}
window.applyTableState = applyTableState;

// Per-table state accessor (seeds a default sort the first time).
function _tableState(prefsKey, columns) {
    if (!window.PREFS.tableState) window.PREFS.tableState = {};
    if (!window.PREFS.tableState[prefsKey]) {
        const first = (columns || []).find(c => c.sortable !== false && c.key);
        window.PREFS.tableState[prefsKey] = {
            sort: first ? { key: first.key, dir: (first.type === 'text' ? 'asc' : 'desc') } : null,
            filters: {},
        };
    }
    const st = window.PREFS.tableState[prefsKey];
    if (!st.filters) st.filters = {};
    return st;
}

// Enhance an existing <table> with clickable-header sort + a filter row.
// config: { table, columns, getRows, renderRows, prefsKey }
//  - columns: [{key, type, sortable?, filter?}] matched to <th data-key=...>
//  - getRows(): current data array  - renderRows(rows, tbody): fills tbody
// Returns { refresh() } — call after (re)loading data.
function makeSortableTable(config) {
    const { table, columns, getRows, renderRows, prefsKey } = config;
    if (!table) return { refresh() {} };
    const thead = table.querySelector('thead');
    const tbody = table.querySelector('tbody');
    const state = _tableState(prefsKey, columns);

    function updateIndicators() {
        thead.querySelectorAll('th[data-key]').forEach(th => {
            let arrow = th.querySelector('.pfm-sort-arrow');
            if (!arrow) {
                arrow = document.createElement('span');
                arrow.className = 'pfm-sort-arrow ms-1';
                th.appendChild(arrow);
            }
            const active = state.sort && state.sort.key === th.dataset.key;
            arrow.textContent = active ? (state.sort.dir === 'asc' ? '▲' : '▼') : '';
        });
    }

    function render() {
        renderRows(applyTableState(getRows(), columns, state), tbody);
        updateIndicators();
    }

    function populateFilters() {
        const rows = getRows() || [];
        thead.querySelectorAll('select[data-filter-key]').forEach(sel => {
            const key = sel.dataset.filterKey;
            const vals = [...new Set(rows.map(r => String(r[key] == null ? '' : r[key])).filter(Boolean))].sort();
            const cur = state.filters[key] || 'all';
            sel.innerHTML = '<option value="all">All</option>' +
                vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
            sel.value = [...sel.options].some(o => o.value === cur) ? cur : 'all';
        });
    }

    if (!table.dataset.sortWired) {
        table.dataset.sortWired = '1';
        // Header click → toggle/set sort.
        thead.querySelectorAll('th[data-key]').forEach(th => {
            th.style.cursor = 'pointer';
            th.classList.add('pfm-sortable-th');
            th.addEventListener('click', () => {
                const key = th.dataset.key;
                if (state.sort && state.sort.key === key) {
                    state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    state.sort = { key, dir: (th.dataset.type === 'text' ? 'asc' : 'desc') };
                }
                savePrefs();
                render();
            });
        });
        // Build a second thead row of filter <select>s (one cell per column).
        const filterCols = (columns || []).filter(c => c.filter === 'select');
        if (filterCols.length) {
            const fr = document.createElement('tr');
            fr.className = 'pfm-filter-row';
            const headerCells = thead.querySelector('tr').children.length;
            for (let i = 0; i < headerCells; i++) {
                const cell = document.createElement('th');
                cell.className = 'py-1 fw-normal';
                const col = columns[i];
                if (col && col.filter === 'select') {
                    const sel = document.createElement('select');
                    sel.className = 'form-select form-select-sm';
                    sel.dataset.filterKey = col.key;
                    sel.addEventListener('change', () => {
                        state.filters[col.key] = sel.value;
                        savePrefs();
                        render();
                    });
                    cell.appendChild(sel);
                }
                fr.appendChild(cell);
            }
            thead.appendChild(fr);
        }
    }

    return {
        refresh() { populateFilters(); render(); },
    };
}
window.makeSortableTable = makeSortableTable;

// Dashboard alerts banner: price targets crossed + watchlist buy zones.
// Loaded async so it never blocks the dashboard (watchlist check hits live
// prices). Hidden entirely when nothing is triggered.
async function loadDashboardAlerts() {
    const box = document.getElementById('dashAlerts');
    if (!box) return;
    try {
        const [targets, watch, fresh] = await Promise.all([
            window.apiClient.getResearchAlerts().catch(() => ({ alerts: [] })),
            window.apiClient.getWatchlistAlerts().catch(() => ({ alerts: [] })),
            window.apiClient.getDataFreshness().catch(() => null),
        ]);
        const items = [];
        // Stale price-data warning: prices feed value & gain/loss, so flag when
        // the last refresh is old or some holdings have gone stale/unpriced.
        if (fresh) {
            const ageH = fresh.refresh_age_hours;
            const oldRefresh = (ageH == null) || (ageH > 30);
            if (oldRefresh || fresh.stale_count > 0) {
                const bits = [];
                if (ageH == null) bits.push('prices never refreshed');
                else if (oldRefresh) bits.push(`prices last refreshed ${relAge(ageH)}`);
                if (fresh.stale_count > 0) {
                    const names = (fresh.stale || []).slice(0, 6)
                        .map(s => {
                            // ISIN/P2P symbols are unreadable; prefer the asset name.
                            const lbl = (s.name && s.name !== s.symbol) ? s.name : s.symbol;
                            return s.age_days != null ? `${esc(lbl)} (${s.age_days}d)` : `${esc(lbl)} (no price)`;
                        })
                        .join(', ');
                    const more = fresh.stale_count > 6 ? ` +${fresh.stale_count - 6} more` : '';
                    bits.push(`${fresh.stale_count} holding${fresh.stale_count > 1 ? 's' : ''} with stale prices: ${names}${more}`);
                }
                items.push(`<li class="mb-1"><span class="badge bg-warning text-dark me-2">DATA</span>`
                    + `<strong>Price data</strong> — ${bits.join('; ')}. Gain/loss may be out of date.</li>`);
            }
        }
        (targets.alerts || []).forEach(a => {
            const cur = a.currency || 'EUR';
            // Position context: quantity, value, and P&L vs weighted-average cost.
            let posInfo = '';
            if (a.quantity > 0) {
                const pnl = a.unrealized_pnl || 0;
                const pnlCls = pnl >= 0 ? 'text-success' : 'text-danger';
                const pnlSign = pnl >= 0 ? '+' : '';
                const avgCostTxt = a.avg_price ? ` vs avg cost ${Fmt.num(a.avg_price, 2, 2)} ${cur}` : '';
                posInfo = ` <span class="text-muted">— ${Fmt.num(a.quantity, 0, 4)} sh · ${Fmt.num(a.value, 2, 2)} ${cur} `
                    + `(<span class="${pnlCls}">${pnlSign}${Fmt.num(pnl, 2, 2)} ${cur}, ${pnlSign}${Fmt.num(a.unrealized_pnl_pct || 0, 2, 2)}%${avgCostTxt}</span>)</span>`;
            } else {
                posInfo = ` <span class="text-muted">— not held</span>`;
            }
            const priceDateTxt = a.price_date ? ` <small class="text-muted">[${a.price_date}]</small>` : '';
            (a.triggers || []).forEach(t => {
                const buy = t.type === 'BUY';
                const nameTxt = a.name ? ` <span class="text-muted">· ${esc(a.name)}</span>` : '';
                items.push(`<li class="mb-1"><span class="badge bg-${buy ? 'success' : 'danger'} me-2">${t.type}</span><strong>${esc(a.symbol)}</strong>${nameTxt} at ${Fmt.num(t.price, 2, 2)} ${cur} ${buy ? '≤ buy-below' : '≥ sell-above'} ${Fmt.num(t.threshold, 2, 2)} ${cur}${posInfo}${priceDateTxt}</li>`);
            });
        });
        (watch.alerts || []).forEach(a => {
            const fetchedTxt = a.price_fetched_at ? ` <small class="text-muted">[${fmtFetchedAt(a.price_fetched_at)}]</small>` : '';
            items.push(`<li class="mb-1"><span class="badge bg-info text-dark me-2">WATCH</span><strong>${esc(a.symbol)}</strong> ${a.name ? '· ' + esc(a.name) : ''} at ${Fmt.num(a.price, 2, 2)} entered buy zone (≤ ${Fmt.num(a.buy_below, 2, 2)})${fetchedTxt}</li>`);
        });
        if (!items.length) { box.style.display = 'none'; box.innerHTML = ''; return; }
        // Dismissal is keyed by the alert content so a *new* alert reappears even
        // after the user closed the previous set; the same set stays hidden.
        const sig = hashStr(items.join(''));
        if (localStorage.getItem('pfmAlertsDismissed') === sig) {
            box.style.display = 'none'; box.innerHTML = ''; return;
        }
        box.style.display = '';
        box.innerHTML = `
            <div class="alert alert-warning alert-dismissible mb-0">
                <button type="button" class="btn-close" id="dashAlertsClose" aria-label="Dismiss"></button>
                <div class="fw-semibold mb-1"><i class="bi bi-bell-fill me-2"></i>${items.length} alert${items.length > 1 ? 's' : ''}</div>
                <ul class="list-unstyled mb-0 small">${items.join('')}</ul>
            </div>`;
        const closeBtn = document.getElementById('dashAlertsClose');
        if (closeBtn) closeBtn.addEventListener('click', () => {
            localStorage.setItem('pfmAlertsDismissed', sig);
            box.style.display = 'none'; box.innerHTML = '';
        });
    } catch (e) {
        box.style.display = 'none';
    }
}

// Format a UTC ISO timestamp as "Xm ago" / "Xh ago" for alert freshness labels.
function fmtFetchedAt(iso) {
    if (!iso) return '';
    try {
        const mins = Math.round((Date.now() - new Date(iso)) / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        return `${Math.round(mins / 60)}h ago`;
    } catch { return ''; }
}

// Tiny stable string hash (djb2) for keying dismissed-alert state.
function hashStr(str) {
    let h = 5381;
    for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) | 0;
    return String(h >>> 0);
}

// Human-friendly "x ago" for an age given in hours.
function relAge(hours) {
    if (hours == null) return 'never';
    if (hours < 1) return `${Math.round(hours * 60)}m ago`;
    if (hours < 48) return `${Math.round(hours)}h ago`;
    return `${Math.round(hours / 24)}d ago`;
}

// Price-data freshness chip in the dashboard header. External prices back the
// value & gain/loss figures, so we surface how recently they were refreshed and
// whether any held assets have gone stale. Green = fresh, amber = aging/stale,
// red = very old or never refreshed.
async function loadDataFreshness() {
    const chip = document.getElementById('dataFreshness');
    if (!chip) return;
    let f;
    try {
        f = await window.apiClient.getDataFreshness();
    } catch (e) {
        chip.style.display = 'none';
        return;
    }
    const ageH = f.refresh_age_hours;
    let cls = 'bg-success';
    if (ageH == null || ageH > 48) cls = 'bg-danger';
    else if (ageH > 30 || f.stale_count > 0) cls = 'bg-warning text-dark';

    const asOf = f.prices_as_of ? Fmt.date(f.prices_as_of) : '—';
    let label = `Prices ${relAge(ageH)}`;
    if (f.stale_count > 0) label += ` · ${f.stale_count} stale`;

    const staleList = (f.stale || [])
        .map(s => {
            const lbl = (s.name && s.name !== s.symbol) ? `${esc(s.symbol)} (${esc(s.name)})` : s.symbol;
            return s.age_days != null ? `${lbl}: ${s.age_days}d old` : `${lbl}: no price`;
        })
        .join('\n');
    const title = `Prices as of ${asOf}\nLast refreshed ${relAge(ageH)}`
        + `\n${f.checked} priced holding${f.checked === 1 ? '' : 's'}`
        + (staleList ? `\n\nStale / unpriced:\n${staleList}` : '');

    chip.className = `badge rounded-pill ${cls}`;
    chip.innerHTML = `<i class="bi bi-clock-history me-1"></i>${label}`;
    chip.title = title;
    chip.style.display = '';
}
window.loadDataFreshness = loadDataFreshness;

// Diagnostics page: price-data freshness + the daily update-run history.
// Surfaces *why* a price may be stale (no Yahoo data vs. just old) and what
// the cron actually did, so it isn't lost to stdout.
async function loadDiagnosticsPage() {
    const freshBox = document.getElementById('diagFreshness');
    const staleBody = document.getElementById('diagStaleBody');
    const runsBody = document.getElementById('diagRunsBody');
    if (!freshBox) return;

    _dqLoaded = false; // reset so DQ refreshes when tab is next activated

    // Wire the refresh button once.
    const refreshBtn = document.getElementById('refreshDiagnostics');
    if (refreshBtn && !refreshBtn._wired) {
        refreshBtn._wired = true;
        refreshBtn.addEventListener('click', () => {
            const dqPane = document.getElementById('diagDataQuality');
            const dqActive = dqPane && dqPane.classList.contains('active');
            if (dqActive) {
                _dqLoaded = false;
                loadDataQualityTab();
            } else {
                loadDiagnosticsPage();
            }
        });
    }

    // Wire DQ tab activation (lazy load on first switch)
    const dqTabBtn = document.getElementById('diagTabDQ');
    if (dqTabBtn && !dqTabBtn._dqWired) {
        dqTabBtn._dqWired = true;
        dqTabBtn.addEventListener('shown.bs.tab', () => loadDataQualityTab());
    }

    // Restore last active tab, or ensure Price Health is active (nav clearing may have
    // stripped the active class from both tab buttons).
    const lastTab = localStorage.getItem('pfmDiagTab');
    const dqBtn2  = document.getElementById('diagTabDQ');
    const phBtn   = document.getElementById('diagTabPrice');
    const dqPane  = document.getElementById('diagDataQuality');
    if (lastTab === 'dq' && dqBtn2 && window.bootstrap) {
        if (dqPane && dqPane.classList.contains('active')) {
            // Pane already visible; shown.bs.tab won't fire — load directly.
            loadDataQualityTab();
        } else {
            new window.bootstrap.Tab(dqBtn2).show();
            // shown.bs.tab fires → loadDataQualityTab() via listener above
        }
    } else if (phBtn && window.bootstrap && !phBtn.classList.contains('active')) {
        new window.bootstrap.Tab(phBtn).show();
    }

    // Persist active tab to localStorage on switch
    document.querySelectorAll('#diagTabs button[data-bs-toggle="tab"]').forEach(btn => {
        btn.addEventListener('shown.bs.tab', () => {
            localStorage.setItem('pfmDiagTab', btn.id === 'diagTabDQ' ? 'dq' : 'price');
        });
    });

    const esc = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

    const [fresh, runsResp] = await Promise.all([
        window.apiClient.getDataFreshness().catch(() => null),
        window.apiClient.getUpdateRuns(20).catch(() => ({ runs: [] })),
    ]);

    // --- Freshness summary ---
    if (fresh) {
        const ageH = fresh.refresh_age_hours;
        let badge = 'bg-success', txt = 'Fresh';
        if (ageH == null || ageH > 48) { badge = 'bg-danger'; txt = 'Very stale'; }
        else if (ageH > 30 || fresh.stale_count > 0) { badge = 'bg-warning text-dark'; txt = 'Aging'; }
        freshBox.innerHTML = `
            <div class="row g-3 small">
                <div class="col-6 col-md-3"><div class="text-muted">Status</div><span class="badge ${badge}">${txt}</span></div>
                <div class="col-6 col-md-3"><div class="text-muted">Last refreshed</div><div class="fw-semibold">${relAge(ageH)}</div></div>
                <div class="col-6 col-md-3"><div class="text-muted">Prices as of</div><div class="fw-semibold">${fresh.prices_as_of ? Fmt.date(fresh.prices_as_of) : '—'}</div></div>
                <div class="col-6 col-md-3"><div class="text-muted">Priced holdings</div><div class="fw-semibold">${fresh.checked} priced · ${fresh.stale_count} stale</div></div>
            </div>`;
    } else {
        freshBox.innerHTML = '<div class="text-danger small">Could not load freshness data.</div>';
    }

    // --- Stale / unpriced holdings ---
    if (staleBody) {
        const stale = (fresh && fresh.stale) || [];
        if (!stale.length) {
            staleBody.innerHTML = '<tr><td colspan="4" class="text-success small"><i class="bi bi-check-circle me-1"></i>All auto-priced holdings are up to date.</td></tr>';
        } else {
            staleBody.innerHTML = stale.map(s => {
                const age = s.age_days != null ? `${s.age_days}d` : '—';
                const reasonCls = s.reason === 'no price data' ? 'text-muted' : 'text-warning';
                return `<tr><td><code>${esc(s.symbol)}</code></td><td class="small">${esc(s.name)}</td>`
                    + `<td class="text-end">${age}</td><td class="small ${reasonCls}">${esc(s.reason)}</td></tr>`;
            }).join('');
        }
    }

    // --- Update history ---
    if (runsBody) {
        const runs = (runsResp && runsResp.runs) || [];
        if (!runs.length) {
            runsBody.innerHTML = '<tr><td colspan="7" class="text-muted small">No update runs recorded yet. The first run will appear after the next price update.</td></tr>';
        } else {
            runsBody.innerHTML = runs.map(r => {
                const when = r.finished_at ? Fmt.date(String(r.finished_at).replace(' ', 'T')) : '—';
                const dur = r.duration_seconds != null ? `${r.duration_seconds}s` : '—';
                const errCls = r.error_count > 0 ? 'text-danger fw-semibold' : '';
                const skipList = (r.skipped_symbols || []).join(', ');
                const skipCell = skipList ? `<span class="small text-muted" title="${esc(skipList)}">${esc(skipList.length > 60 ? skipList.slice(0, 60) + '…' : skipList)}</span>` : '';
                return `<tr><td class="small">${esc(when)}</td><td class="small">${esc(r.source)}</td>`
                    + `<td class="text-end small">${dur}</td><td class="text-end">${r.updated_count}</td>`
                    + `<td class="text-end">${r.skipped_count}</td><td class="text-end ${errCls}">${r.error_count}</td>`
                    + `<td>${skipCell}</td></tr>`;
            }).join('');
        }
    }
}
window.loadDiagnosticsPage = loadDiagnosticsPage;

// ── Data Quality tab ──────────────────────────────────────────────────────────

let _dqLoaded = false;

function _dqDismissed(check, key) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedIssues') || '[]');
    return items.some(i => i.check === check && i.key === key);
}
function _dqDismiss(check, key) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedIssues') || '[]');
    if (!items.some(i => i.check === check && i.key === key)) {
        items.push({ check, key, dismissed_at: new Date().toISOString() });
        localStorage.setItem('pfmDismissedIssues', JSON.stringify(items));
    }
}
function _dqUndismiss(check, key) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedIssues') || '[]');
    localStorage.setItem('pfmDismissedIssues',
        JSON.stringify(items.filter(i => !(i.check === check && i.key === key))));
}

async function loadDataQualityTab(force = false) {
    if (_dqLoaded && !force) return;
    _dqLoaded = true;

    function _wireOnce(id, fn) {
        const btn = document.getElementById(id);
        if (btn && !btn._dqWired) { btn._dqWired = true; btn.addEventListener('click', fn); }
    }
    _wireOnce('dqRerunRecon', () => { _dqLoaded = false; _loadReconCard(); });
    _wireOnce('dqRerunDups',  () => { _dqLoaded = false; _loadDupsCard(); });
    _wireOnce('dqRerunSusp',  () => { _dqLoaded = false; _loadSuspCard(); });

    await Promise.all([_loadReconCard(), _loadDupsCard(), _loadSuspCard()]);

    async function _loadReconCard() {
        const el = document.getElementById('dqReconBody');
        if (!el) return;
        el.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">Loading…</td></tr>';
        const data = await window.apiClient.getDQReconciliation().catch(() => null);
        if (!data) {
            el.innerHTML = '<tr><td colspan="5" class="text-danger small p-3">Could not load reconciliation data.</td></tr>';
            return;
        }
        if (!data.portfolios.length) {
            el.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">No portfolios found.</td></tr>';
            return;
        }
        el.innerHTML = data.portfolios.map(p => `
            <tr>
                <td class="fw-semibold">${esc(p.portfolio_name)}</td>
                <td class="text-end font-monospace">${Fmt.money(p.implied_cash, 'EUR')}</td>
                <td class="text-end font-monospace">${Fmt.money(p.invested_value, 'EUR')}</td>
                <td class="text-end font-monospace fw-semibold">${Fmt.money(p.total_accounted, 'EUR')}</td>
                <td class="text-end small text-muted">${Fmt.money(p.net_bookings, 'EUR')}</td>
            </tr>`).join('');
    }

    async function _loadDupsCard() {
        const body   = document.getElementById('dqDupsBody');
        const footer = document.getElementById('dqDupsFooter');
        if (!body) return;
        body.innerHTML = '<div class="text-muted small p-3">Loading…</div>';
        const data = await window.apiClient.getDQDuplicates().catch(() => null);
        if (!data) {
            body.innerHTML = '<div class="text-danger small p-3">Could not load duplicates.</div>';
            return;
        }
        const dups = data.duplicates || [];
        if (!dups.length) {
            body.innerHTML = '<div class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>No possible duplicates found.</div>';
            if (footer) footer.innerHTML = '';
            return;
        }

        let showDismissed = false;

        function _renderDups() {
            const toShow = showDismissed ? dups : dups.filter(d => !_dqDismissed('dup', d.key));
            if (!toShow.length) {
                body.innerHTML = '<div class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>All findings dismissed.</div>';
            } else {
                body.innerHTML = toShow.map(d => {
                    const isDism = _dqDismissed('dup', d.key);
                    const badge = d.label === 'likely'
                        ? '<span class="badge bg-danger">LIKELY</span>'
                        : '<span class="badge bg-warning text-dark">POSSIBLE</span>';
                    const olderId = d.tx_a.date <= d.tx_b.date ? d.tx_a.id : d.tx_b.id;
                    const op = isDism ? ' opacity-50' : '';
                    return `<div class="border-bottom p-2${op}" data-dup-key="${esc(d.key)}">
                        <div class="d-flex justify-content-between align-items-start mb-1">
                            <div>${badge} <span class="small text-muted">${esc(d.tx_a.portfolio)}</span></div>
                            <div class="btn-group btn-group-sm">
                                <button type="button" class="btn btn-outline-danger btn-sm dq-del-older" data-id="${olderId}" data-key="${esc(d.key)}"><i class="bi bi-trash me-1"></i>Delete older</button>
                                <button type="button" class="btn btn-outline-danger btn-sm dropdown-toggle dropdown-toggle-split" data-bs-toggle="dropdown" aria-label="More delete options"><span class="visually-hidden">Toggle dropdown</span></button>
                                <ul class="dropdown-menu dropdown-menu-end">
                                    <li><button type="button" class="dropdown-item dq-del-tx" data-id="${d.tx_a.id}" data-key="${esc(d.key)}">Delete #${esc(d.tx_a.id)} (${esc(d.tx_a.date)})</button></li>
                                    <li><button type="button" class="dropdown-item dq-del-tx" data-id="${d.tx_b.id}" data-key="${esc(d.key)}">Delete #${esc(d.tx_b.id)} (${esc(d.tx_b.date)})</button></li>
                                </ul>
                                <button type="button" class="btn btn-outline-secondary btn-sm dq-dism-dup" data-key="${esc(d.key)}" title="${isDism ? 'Undismiss' : 'Dismiss'}">${isDism ? '<i class="bi bi-eye"></i>' : '<i class="bi bi-eye-slash"></i>'}</button>
                            </div>
                        </div>
                        <div class="row small g-1">
                            <div class="col-6 bg-body-secondary rounded p-1">
                                <div class="fw-semibold">${esc(d.tx_a.asset)}</div>
                                <div>${esc(d.tx_a.type)} · ${Fmt.num(d.tx_a.quantity, 4)} @ ${Fmt.num(d.tx_a.price, 4)}</div>
                                <div class="text-muted">${esc(d.tx_a.date)} · #${d.tx_a.id}</div>
                            </div>
                            <div class="col-6 bg-body-secondary rounded p-1">
                                <div class="fw-semibold">${esc(d.tx_b.asset)}</div>
                                <div>${esc(d.tx_b.type)} · ${Fmt.num(d.tx_b.quantity, 4)} @ ${Fmt.num(d.tx_b.price, 4)}</div>
                                <div class="text-muted">${esc(d.tx_b.date)} · #${d.tx_b.id}</div>
                            </div>
                        </div>
                    </div>`;
                }).join('');

                body.querySelectorAll('.dq-del-older, .dq-del-tx').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const id  = parseInt(btn.dataset.id);
                        const key = btn.dataset.key;
                        if (!confirm(`Delete transaction #${id}?`)) return;
                        try {
                            await window.apiClient.deleteTransaction(id);
                            _dqDismiss('dup', key);
                            await _loadDupsCard();
                        } catch (e) {
                            alert('Failed to delete: ' + e.message);
                        }
                    });
                });

                body.querySelectorAll('.dq-dism-dup').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const key = btn.dataset.key;
                        _dqDismissed('dup', key) ? _dqUndismiss('dup', key) : _dqDismiss('dup', key);
                        _renderDups();
                        _renderDupsFooter();
                    });
                });
            }
        }

        function _renderDupsFooter() {
            if (!footer) return;
            const n = dups.filter(d => _dqDismissed('dup', d.key)).length;
            if (!n) { footer.innerHTML = ''; return; }
            footer.innerHTML = `<button type="button" class="btn btn-link btn-sm p-0 text-muted">${showDismissed ? 'Hide' : 'Show'} ${n} dismissed</button>`;
            footer.querySelector('button').addEventListener('click', () => {
                showDismissed = !showDismissed;
                _renderDups();
                _renderDupsFooter();
            });
        }

        _renderDups();
        _renderDupsFooter();
    }

    async function _loadSuspCard() {
        const body   = document.getElementById('dqSuspBody');
        const footer = document.getElementById('dqSuspFooter');
        if (!body) return;
        body.innerHTML = '<tr><td colspan="6" class="text-muted small p-3">Loading…</td></tr>';
        const data = await window.apiClient.getDQSuspicious().catch(() => null);
        if (!data) {
            body.innerHTML = '<tr><td colspan="6" class="text-danger small p-3">Could not load suspicious patterns.</td></tr>';
            return;
        }
        const issues = data.issues || [];
        if (!issues.length) {
            body.innerHTML = '<tr><td colspan="6" class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>No suspicious patterns found.</td></tr>';
            if (footer) footer.innerHTML = '';
            return;
        }

        let showDismissed = false;

        function _renderSusp() {
            const toShow = showDismissed ? issues : issues.filter(i => !_dqDismissed('susp', i.key));
            if (!toShow.length) {
                body.innerHTML = '<tr><td colspan="6" class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>All findings dismissed.</td></tr>';
            } else {
                body.innerHTML = toShow.map(i => {
                    const isDism = _dqDismissed('susp', i.key);
                    const badge = i.severity === 'warning'
                        ? '<span class="badge bg-warning text-dark">warning</span>'
                        : '<span class="badge bg-info text-dark">info</span>';
                    const op = isDism ? ' class="opacity-50"' : '';
                    return `<tr${op}>
                        <td>${badge}</td>
                        <td><code>${esc(i.asset)}</code><div class="small text-muted">${esc(i.asset_name)}</div></td>
                        <td class="small">${esc(i.date)}</td>
                        <td class="small">${esc(i.type)}</td>
                        <td class="small">${esc(i.description)}</td>
                        <td class="text-nowrap">
                            <button type="button" class="btn btn-link btn-sm p-0 me-2 dq-view-tx" data-asset="${esc(i.asset)}">View</button>
                            <button type="button" class="btn btn-link btn-sm p-0 text-muted dq-dism-susp" data-key="${esc(i.key)}" title="${isDism ? 'Undismiss' : 'Dismiss'}">${isDism ? '<i class="bi bi-eye"></i>' : '<i class="bi bi-eye-slash"></i>'}</button>
                        </td>
                    </tr>`;
                }).join('');

                body.querySelectorAll('.dq-view-tx').forEach(btn => {
                    btn.addEventListener('click', () => {
                        if (window.navigationManager) window.navigationManager.showPage('transactions');
                        const f = document.getElementById('txAssetFilter');
                        if (f) { f.value = btn.dataset.asset; f.dispatchEvent(new Event('change')); }
                    });
                });

                body.querySelectorAll('.dq-dism-susp').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const key = btn.dataset.key;
                        _dqDismissed('susp', key) ? _dqUndismiss('susp', key) : _dqDismiss('susp', key);
                        _renderSusp();
                        _renderSuspFooter();
                    });
                });
            }
        }

        function _renderSuspFooter() {
            if (!footer) return;
            const n = issues.filter(i => _dqDismissed('susp', i.key)).length;
            if (!n) { footer.innerHTML = ''; return; }
            footer.innerHTML = `<button type="button" class="btn btn-link btn-sm p-0 text-muted">${showDismissed ? 'Hide' : 'Show'} ${n} dismissed</button>`;
            footer.querySelector('button').addEventListener('click', () => {
                showDismissed = !showDismissed;
                _renderSusp();
                _renderSuspFooter();
            });
        }

        _renderSusp();
        _renderSuspFooter();
    }
}
window.loadDataQualityTab = loadDataQualityTab;

// Apply the user's default currency to the static "new entry" form fields
// (Add booking, new broker). Add-asset is handled on modal-show.
function applyDefaultCurrency() {
    const cur = (window.PREFS && window.PREFS.defaultCurrency) || 'EUR';
    ['addBookingCurrency', 'portfolioCurrency'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = cur;
    });
}
window.applyDefaultCurrency = applyDefaultCurrency;

// Collapsible sidebar sections — persisted in localStorage, defaulting to
// Portfolio open and the rest collapsed. Applies to both the desktop sidebar
// and the mobile offcanvas (matched by data-sect).
const _NAV_SECTION_DEFAULTS = { portfolio: true, insights: false, planning: false, tools: false, help: false };
function _applyNavSection(sect, open) {
    document.querySelectorAll(`.sidebar-section-toggle[data-sect="${sect}"]`)
        .forEach(b => b.classList.toggle('collapsed', !open));
    document.querySelectorAll(`.sidebar-section-items[data-sect-items="${sect}"]`)
        .forEach(d => d.classList.toggle('collapsed', !open));
}
function setupSidebarSections() {
    let state = {};
    try { state = JSON.parse(localStorage.getItem('pfm_nav_sections') || '{}'); } catch (e) { state = {}; }
    Object.keys(_NAV_SECTION_DEFAULTS).forEach(sect => {
        const open = (sect in state) ? state[sect] : _NAV_SECTION_DEFAULTS[sect];
        _applyNavSection(sect, open);
    });
    document.querySelectorAll('.sidebar-section-toggle').forEach(btn => {
        if (btn.dataset.wired) return;
        btn.dataset.wired = '1';
        btn.addEventListener('click', () => {
            const sect = btn.dataset.sect;
            const willOpen = btn.classList.contains('collapsed');
            _applyNavSection(sect, willOpen);
            state[sect] = willOpen;
            try { localStorage.setItem('pfm_nav_sections', JSON.stringify(state)); } catch (e) { /* ignore */ }
        });
    });
}
// Expand whichever section contains the given page (so the active item shows).
function expandNavSectionFor(pageName) {
    const link = document.querySelector(`.sidebar-section-items [data-page="${pageName}"]`);
    const items = link && link.closest('.sidebar-section-items');
    if (items) _applyNavSection(items.dataset.sectItems, true);
}

// Preselect the user's default broker (by name) in a populated <select>.
function selectDefaultBroker(selectEl) {
    const name = (window.PREFS && window.PREFS.defaultBroker) || '';
    if (!selectEl || !name) return;
    for (const opt of selectEl.options) {
        if (opt.textContent === name) { selectEl.value = opt.value; break; }
    }
}
window.selectDefaultBroker = selectDefaultBroker;

function applyTheme() {
    let t = window.PREFS.theme;
    if (t === 'auto') {
        t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-bs-theme', t);
}
function applyPrivacy() {
    if (document.body) document.body.classList.toggle('pfm-privacy', !!window.PREFS.privacy);
}
applyTheme();  // before first paint

// ---------------------------------------------------------------------------
// AssetSearch — shared asset autocomplete matching used by all search inputs
// ---------------------------------------------------------------------------
const AssetSearch = (() => {
    // Maps a term the user might type → lowercase fragment that must appear in the
    // asset name. Covers popular tickers with non-obvious Yahoo symbols, class-share
    // variants, and common company nicknames. `acronymOf` (below) derives first-letter
    // acronyms automatically, so this table only needs hand-crafted entries.
    const ALIASES = {
        // US Tech
        GOOGL: 'alphabet', GOOG: 'alphabet', GOOGLE: 'alphabet',
        META: 'meta platforms', FB: 'meta platforms', FACEBOOK: 'meta platforms',
        AMZN: 'amazon', MSFT: 'microsoft', AAPL: 'apple', NVDA: 'nvidia',
        TSLA: 'tesla', NFLX: 'netflix', PYPL: 'paypal', DIS: 'walt disney',
        AMD: 'advanced micro devices', INTC: 'intel corporation',
        AVGO: 'broadcom', QCOM: 'qualcomm',
        ORCL: 'oracle', CRM: 'salesforce', NOW: 'servicenow',
        ADBE: 'adobe', INTU: 'intuit',
        // Class-share / exchange variants
        ISRG: 'intuitive surgical',   // Yahoo may suffix with A (ISRGA)
        BRKB: 'berkshire', BRKA: 'berkshire', BRK: 'berkshire', BERKSHIRE: 'berkshire',
        // US Finance
        JPM: 'jpmorgan', GS: 'goldman sachs', MS: 'morgan stanley',
        WFC: 'wells fargo', BAC: 'bank of america', C: 'citigroup',
        V: 'visa', MA: 'mastercard', AXP: 'american express',
        BLK: 'blackrock', SCHW: 'charles schwab',
        // US Healthcare / Pharma
        JNJ: 'johnson', UNH: 'unitedhealth', LLY: 'eli lilly',
        ABBV: 'abbvie', MRK: 'merck', PFE: 'pfizer', CVS: 'cvs health',
        // US Consumer / Industrial
        NKE: 'nike', SBUX: 'starbucks', MCD: 'mcdonalds',
        KO: 'coca-cola', PEP: 'pepsico', PG: 'procter', PROCTER: 'procter',
        HD: 'home depot', LOW: 'lowe', TGT: 'target', WMT: 'walmart',
        BA: 'boeing', GE: 'general electric', MMM: '3m company', CAT: 'caterpillar',
        // European
        LVMH: 'lvmh', 'LOUIS VUITTON': 'lvmh', MOET: 'moet',
        LOREAL: "l'oreal", LOR: "l'oreal",
        NESTLE: 'nestle', NOVARTIS: 'novartis', ROCHE: 'roche',
        SIEMENS: 'siemens', ALLIANZ: 'allianz', BASF: 'basf',
        VW: 'volkswagen', VOLKSWAGEN: 'volkswagen',
        AIRBUS: 'airbus',
        // Semiconductors / Global
        TSM: 'taiwan semiconductor', TSMC: 'taiwan semiconductor',
        NVO: 'novo nordisk', NOVO: 'novo nordisk',
        ASML: 'asml',
    };

    // Strips exchange and currency-pair suffixes so users can omit them:
    // "ASML.AS" → "ASML",  "BTC-EUR" → "BTC",  "BRK.B" → "BRK"
    function baseTicker(symbol) {
        return (symbol || '').replace(/[.\-][A-Z0-9]+$/i, '').toUpperCase();
    }

    // First letter of each word in the name → "Advanced Micro Devices" → "AMD"
    function acronymOf(name) {
        const words = (name || '').replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean);
        if (words.length < 2) return '';
        return words.map(w => w[0]).join('').toUpperCase();
    }

    // Returns ALIASES keys whose target fragment appears in this asset's name.
    function aliasesFor(name) {
        const n = (name || '').toLowerCase();
        return Object.keys(ALIASES).filter(a => n.includes(ALIASES[a]));
    }

    // Produce a search-ready asset object. Pass extra fields (currency, source, …) as extras.
    function enrich(symbol, name, extras = {}) {
        return {
            symbol, name: name || '', ...extras,
            acronym: acronymOf(name),
            aliases: aliasesFor(name),
            base: baseTicker(symbol),
        };
    }

    // Score and rank assets against query. Returns up to `limit` best matches.
    function match(query, assets, limit = 10) {
        const q = (query || '').trim().toLowerCase();
        if (!q) return [];
        const scored = assets.map(s => {
            const sym  = (s.symbol  || '').toLowerCase();
            const name = (s.name    || '').toLowerCase();
            const acr  = (s.acronym || acronymOf(s.name)).toLowerCase();
            const base = (s.base    || baseTicker(s.symbol)).toLowerCase();
            const al   = (s.aliases || aliasesFor(s.name)).map(a => a.toLowerCase());
            let score = -1;
            if (sym === q)                                         score = 0;    // exact symbol
            else if (base === q)                                   score = 0.5;  // exact base (ASML ↔ ASML.AS)
            else if (sym.startsWith(q))                            score = 1;    // symbol prefix
            else if (base.startsWith(q))                           score = 1.5;  // base prefix
            else if (acr === q || al.some(a => a === q))           score = 2;    // exact acronym/alias
            else if (name.startsWith(q))                           score = 3;    // name prefix
            else if (acr.startsWith(q) || al.some(a => a.startsWith(q))) score = 3.5;
            else if (sym.includes(q))                              score = 4;
            else if (name.includes(q))                             score = 5;
            return { s, score };
        }).filter(x => x.score >= 0).sort((a, b) => a.score - b.score).slice(0, limit);
        return scored.map(x => x.s);
    }

    // Wire a full autocomplete UI on an input+dropdown pair.
    // opts: { getSuggestions, onSelect, onInput, renderItem, limit, clearOnEscape }
    function buildAutocomplete(inputEl, suggestEl, opts = {}) {
        const { getSuggestions, onSelect, onInput, limit = 10, clearOnEscape = false } = opts;
        const renderItem = opts.renderItem || (s => `
            <button type="button" class="list-group-item list-group-item-action py-1 px-2" data-sym="${s.symbol}">
                <strong>${esc(s.symbol)}</strong>
                ${s.name ? `<div class="small text-muted text-truncate">${esc(s.name)}</div>` : ''}
            </button>`);
        let activeIdx = -1;
        const hideSuggest = () => { suggestEl.style.display = 'none'; activeIdx = -1; };
        const showSuggest = (q) => {
            const assets = getSuggestions ? getSuggestions() : [];
            const hits = match(q, assets, limit);
            if (!hits.length) { hideSuggest(); return; }
            suggestEl.innerHTML = hits.map(renderItem).join('');
            suggestEl.querySelectorAll('[data-sym]').forEach(b => {
                b.addEventListener('mousedown', e => {
                    e.preventDefault();
                    const asset = hits.find(h => h.symbol === b.dataset.sym) || { symbol: b.dataset.sym };
                    inputEl.value = asset.symbol;
                    hideSuggest();
                    if (onSelect) onSelect(asset);
                });
            });
            activeIdx = -1;
            suggestEl.style.display = '';
        };
        inputEl.addEventListener('input', () => { showSuggest(inputEl.value); if (onInput) onInput(inputEl.value); });
        inputEl.addEventListener('focus', () => { if (inputEl.value) showSuggest(inputEl.value); });
        inputEl.addEventListener('blur', () => setTimeout(hideSuggest, 150));
        inputEl.addEventListener('keydown', e => {
            const items = suggestEl.querySelectorAll('[data-sym]');
            if (suggestEl.style.display !== 'none' && items.length) {
                if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, items.length - 1); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); }
                else if (e.key === 'Enter' && activeIdx >= 0) { e.preventDefault(); items[activeIdx].dispatchEvent(new Event('mousedown')); return; }
                items.forEach((it, i) => it.classList.toggle('active', i === activeIdx));
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') return;
            }
            if (e.key === 'Escape') { hideSuggest(); if (clearOnEscape) { inputEl.value = ''; if (onInput) onInput(''); } }
        });
    }

    return { enrich, match, buildAutocomplete, baseTicker, acronymOf, aliasesFor };
})();

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function fmtPrice(amount, currency) {
    const n = Fmt.num(amount, 2, 2);
    return currency ? `${n} ${currency}` : n;
}

// Build Yahoo Finance + Wall Street Journal quote links for a symbol
function assetLinks(symbol) {
    if (!symbol) return '';
    const s = encodeURIComponent(symbol);
    return `
      <a href="https://finance.yahoo.com/quote/${s}" target="_blank" rel="noopener" class="text-decoration-none me-1" title="View on Yahoo Finance"><i class="bi bi-graph-up"></i></a>
      <a href="https://www.wsj.com/market-data/quotes/${s}" target="_blank" rel="noopener" class="text-decoration-none" title="View on Wall Street Journal"><i class="bi bi-newspaper"></i></a>`;
}

// ---------------------------------------------------------------------------
// API Client
// ---------------------------------------------------------------------------
function createAPIClient() {
    return {
        // Same-origin: API calls go to /api/... and are proxied to the backend
        // by the web container's nginx. Works for LAN (host:8080) and external
        // HTTPS (your domain) alike. Override via window.PORTF_API_BASE if
        // ever serving the API from a different origin.
        baseURL: (typeof window !== 'undefined' && window.PORTF_API_BASE) || '',
        apiKey: localStorage.getItem('apiKey'),

        setApiKey: function(key) {
            this.apiKey = key;
            localStorage.setItem('apiKey', key);
        },

        clearApiKey: function() {
            this.apiKey = null;
            localStorage.removeItem('apiKey');
        },

        validateApiKey: async function(key) {
            try {
                const response = await fetch(this.baseURL + '/api/v1/transactions/?limit=1', {
                    method: 'GET',
                    headers: { 'X-API-Key': key }
                });
                return response.status === 200;
            } catch (error) {
                console.error('API validation error:', error);
                return false;
            }
        },

        // Username/password login → returns the API key the data endpoints need
        loginWithPassword: async function(username, password) {
            const resp = await fetch(this.baseURL + '/api/v1/auth/login-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || 'Login failed');
            }
            // Remember username so the Settings → Change password form can prefill it
            try { localStorage.setItem('pfm_username', username); } catch (e) { /* ignore */ }
            return (await resp.json()).api_key;
        },

        // Change password (web/shared-key model: current password is the gate)
        changePassword: async function(username, currentPassword, newPassword) {
            const resp = await fetch(this.baseURL + '/api/v1/auth/change-password-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, current_password: currentPassword, new_password: newPassword })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || 'Password change failed');
            }
            return resp.json();
        },

        // First-time account creation
        registerUser: async function(username, email, password) {
            const resp = await fetch(this.baseURL + '/api/v1/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, email, password })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                const detail = Array.isArray(err.detail)
                    ? err.detail.map(d => d.msg).join(', ')
                    : (err.detail || 'Registration failed');
                throw new Error(detail);
            }
            return resp.json();
        },

        async getAssets() {
            try {
                const response = await fetch(this.baseURL + '/api/v1/assets/', {
                    headers: { 'X-API-Key': this.apiKey }
                });
                const data = await response.json();
                return Array.isArray(data) ? data : [];
            } catch (error) {
                console.error('Error loading assets:', error);
                return [];
            }
        },

        async getTransactions(limit = 100, portfolioId = null) {
            try {
                let url = this.baseURL + `/api/v1/transactions/?limit=${limit}`;
                if (portfolioId) url += `&portfolio_id=${portfolioId}`;
                const response = await fetch(url, {
                    headers: { 'X-API-Key': this.apiKey }
                });
                const data = await response.json();
                return Array.isArray(data) ? data : [];
            } catch (error) {
                console.error('Error loading transactions:', error);
                return [];
            }
        },

        async getPortfolioValues() {
            const response = await fetch(this.baseURL + '/api/v1/portfolios/values', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error(await response.text());
            return response.json();
        },

        async getPortfolios() {
            try {
                const response = await fetch(this.baseURL + '/api/v1/portfolios/', {
                    headers: { 'X-API-Key': this.apiKey }
                });
                const data = await response.json();
                return Array.isArray(data) ? data : [];
            } catch (error) {
                console.error('Error loading portfolios:', error);
                return [];
            }
        },

        async createPortfolio(data) {
            const response = await fetch(this.baseURL + '/api/v1/portfolios/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(data)
            });
            if (!response.ok) throw new Error(await response.text());
            return response.json();
        },

        async updatePortfolio(id, data) {
            const response = await fetch(this.baseURL + `/api/v1/portfolios/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(data)
            });
            if (!response.ok) throw new Error(await response.text());
            return response.json();
        },

        async deleteTransaction(id) {
            const response = await fetch(this.baseURL + `/api/v1/transactions/${id}`, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error(await response.text());
        },

        async updateTransaction(id, data) {
            const response = await fetch(this.baseURL + `/api/v1/transactions/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(data)
            });
            if (!response.ok) throw new Error(await response.text());
            return response.json();
        },

        async deletePortfolio(id) {
            const response = await fetch(this.baseURL + `/api/v1/portfolios/${id}`, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error(await response.text());
        },

        async getHoldings(portfolioId = null) {
            try {
                const q = (portfolioId != null && portfolioId !== 'all')
                    ? `?portfolio_id=${encodeURIComponent(portfolioId)}` : '';
                const response = await fetch(this.baseURL + '/api/v1/portfolios/holdings' + q, {
                    headers: { 'X-API-Key': this.apiKey }
                });
                const data = await response.json();
                return data;
            } catch (error) {
                console.error('Error loading holdings:', error);
                return { holdings: [], summary: {} };
            }
        },

        async extractTransactions(text) {
            const response = await fetch(this.baseURL + '/api/v1/llm/extract-transactions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ text })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Extraction failed: ${err}`);
            }
            return response.json();
        },

        async uploadBrokerFile(broker, file) {
            const form = new FormData();
            form.append('broker', broker);
            form.append('file', file);
            const response = await fetch(this.baseURL + '/api/v1/import/upload', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey },
                body: form
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Parse failed: ${err}`);
            }
            return response.json();
        },

        async saveImportedTransactions(transactions, bookings = [], portfolioId = null, duplicateAction = 'skip', deposits = []) {
            const response = await fetch(this.baseURL + '/api/v1/import/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ transactions, bookings, deposits, portfolio_id: portfolioId, duplicate_action: duplicateAction })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Save failed: ${err}`);
            }
            return response.json();
        },

        async sendChat(message, sessionId) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ message, session_id: sessionId, live: false })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Chat failed: ${err}`);
            }
            return response.json();
        },

        exportUrl(format) {
            return `${this.baseURL}/api/v1/export/${format}`;
        },

        async createAsset(assetData) {
            try {
                const response = await fetch(this.baseURL + '/api/v1/assets/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': this.apiKey
                    },
                    body: JSON.stringify(assetData)
                });

                if (response.status === 201) {
                    return response.json();
                } else {
                    const error = await response.text();
                    throw new Error(`Failed to create asset: ${error}`);
                }
            } catch (error) {
                console.error('Error creating asset:', error);
                throw error;
            }
        },

        async getBookings() {
            const resp = await fetch(this.baseURL + '/api/v1/bookings/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load bookings');
            return resp.json();
        },

        async getNetworth() {
            const resp = await fetch(this.baseURL + '/api/v1/networth/', { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to load net worth');
            return resp.json();
        },
        async createManualAsset(payload) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to add');
            return resp.json();
        },
        async deleteManualAsset(id) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/' + id, { method: 'DELETE', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to delete');
            return resp.json().catch(() => ({}));
        },

        async getDeposits() {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/', { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to load deposits');
            return resp.json();
        },
        async createDeposit(payload) {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to create deposit');
            return resp.json();
        },
        async deleteDeposit(id) {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/' + id, { method: 'DELETE', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to delete deposit');
            return resp.json().catch(() => ({}));
        },
        async matureDeposit(id, payload) {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/' + id + '/mature', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to mature deposit');
            return resp.json();
        },
        async extractDepositsLLM(text) {
            const resp = await fetch(this.baseURL + '/api/v1/llm/extract-deposits', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ text })
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'LLM extraction failed');
            return resp.json();
        },

        async getTaxReport(year) {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/tax-report' + (year ? `?year=${year}` : ''), {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load tax report');
            return resp.json();
        },

        async getTaxOptimizer(year) {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/tax-optimizer' + (year ? `?year=${year}` : ''), {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load tax optimizer');
            return resp.json();
        },

        async getResearchAlerts() {
            const resp = await fetch(this.baseURL + '/api/v1/research/alerts/check', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to check alerts');
            return resp.json();
        },

        async getWatchlistAlerts() {
            const resp = await fetch(this.baseURL + '/api/v1/watchlist/alerts/check', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to check watchlist alerts');
            return resp.json();
        },

        async getDataFreshness() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/data-freshness', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to check data freshness');
            return resp.json();
        },

        async getUpdateRuns(limit = 20) {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/update-runs?limit=' + limit, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load update runs');
            return resp.json();
        },

        async getDQReconciliation() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dq/reconciliation', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load reconciliation data');
            return resp.json();
        },

        async getDQDuplicates() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dq/duplicates', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load duplicates');
            return resp.json();
        },

        async getDQSuspicious() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dq/suspicious', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load suspicious patterns');
            return resp.json();
        },

        async deleteBooking(id) {
            const resp = await fetch(this.baseURL + '/api/v1/bookings/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to delete booking');
            return resp.json().catch(() => ({}));
        },

        async createTransaction(payload) {
            const resp = await fetch(this.baseURL + '/api/v1/transactions/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({}));
                throw new Error(e.detail || 'Failed to create transaction');
            }
            return resp.json();
        },

        async setAssetPrice(assetId, price) {
            const today = new Date().toISOString().slice(0, 10);
            const resp = await fetch(this.baseURL + `/api/v1/assets/${assetId}/prices`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ price, price_date: today, price_type: 'close', source: 'manual' })
            });
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({}));
                throw new Error(e.detail || 'Failed to set price');
            }
            return resp.json();
        },

        async createBooking(booking) {
            const resp = await fetch(this.baseURL + '/api/v1/bookings/', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
                body: JSON.stringify(booking)
            });
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({}));
                throw new Error(e.detail || 'Failed to create booking');
            }
            return resp.json();
        },

        async extractAsync(text) {
            const response = await fetch(this.baseURL + '/api/v1/llm/extract-async', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ text })
            });
            if (!response.ok) throw new Error('Could not start extraction');
            return response.json();
        },

        async getExtractStatus(jobId) {
            const response = await fetch(this.baseURL + '/api/v1/llm/extract-status/' + encodeURIComponent(jobId), {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Lost track of the extraction job');
            return response.json();
        },

        // Submit text, then poll until the extraction job finishes (or times out).
        async extractTransactionsAndBookings(text, onProgress) {
            const { job_id } = await this.extractAsync(text);
            const started = Date.now();
            const maxMs = 180000; // 3 min ceiling
            while (Date.now() - started < maxMs) {
                await new Promise(r => setTimeout(r, 1800));
                const s = await this.getExtractStatus(job_id);
                if (onProgress) onProgress(Math.round((Date.now() - started) / 1000));
                if (s.status === 'done') return { transactions: s.transactions || [], bookings: s.bookings || [] };
                if (s.status === 'error') throw new Error(s.error || 'Extraction failed');
            }
            throw new Error('Extraction is taking too long — try a shorter statement or split it.');
        },

        async startBackfill(force = false) {
            const r = await fetch(this.baseURL + '/api/v1/analytics/backfill-snapshots' + (force ? '?force=true' : ''), {
                method: 'POST', headers: { 'X-API-Key': this.apiKey }
            });
            if (!r.ok) throw new Error('Could not start backfill');
            return r.json();
        },
        async getBackfillStatus() {
            const r = await fetch(this.baseURL + '/api/v1/analytics/backfill-status', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!r.ok) throw new Error('status failed');
            return r.json();
        },

        async checkDuplicates(transactions, bookings = [], portfolioId = null) {
            const response = await fetch(this.baseURL + '/api/v1/import/check-duplicates', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ transactions, bookings, portfolio_id: portfolioId })
            });
            if (!response.ok) throw new Error('Duplicate check failed');
            return response.json();
        },

        async extractBookings(text) {
            const resp = await fetch(this.baseURL + '/api/v1/llm/extract-bookings', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });
            if (!resp.ok) {
                const e = await resp.json().catch(() => ({}));
                throw new Error(e.detail || 'Failed to extract bookings');
            }
            return resp.json();
        },

        async getSyncConfig() {
            const resp = await fetch(this.baseURL + '/api/v1/sync/pdt-config', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Config fetch failed');
            return resp.json();
        },

        async syncPull(sheetId) {
            const url = this.baseURL + '/api/v1/sync/pdt-pull'
                + (sheetId ? `?spreadsheet_id=${encodeURIComponent(sheetId)}` : '');
            const resp = await fetch(url, { method: 'POST', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) { const t = await resp.text(); throw new Error(t); }
            return resp.json();
        },

        async syncPush(sheetId) {
            const url = this.baseURL + '/api/v1/sync/pdt-push'
                + (sheetId ? `?spreadsheet_id=${encodeURIComponent(sheetId)}` : '');
            const resp = await fetch(url, { method: 'POST', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) { const t = await resp.text(); throw new Error(t); }
            return resp.json();
        },

        // Fetch a URL and trigger a file download in the browser
        async downloadBlob(url, filename) {
            const r = await fetch(url, { headers: { 'X-API-Key': this.apiKey } });
            if (!r.ok) throw new Error('Export failed: ' + r.status);
            const blob = await r.blob();
            const objectUrl = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = objectUrl;
            link.download = filename;
            link.click();
            URL.revokeObjectURL(objectUrl);
        },

        async getRebalanceTargets() {
            const resp = await fetch(this.baseURL + '/api/v1/rebalance/targets', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async setRebalanceTargets(targets) {
            const resp = await fetch(this.baseURL + '/api/v1/rebalance/targets', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(targets)
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getRebalanceAnalysis() {
            const resp = await fetch(this.baseURL + '/api/v1/rebalance/analysis', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getResearchReport(symbol) {
            const resp = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}`, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (resp.status === 404) return null;
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async generateResearchReport(symbol) {
            const resp = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}/generate`, {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async researchLookup(symbol) {
            const r = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}/lookup`, { headers: { 'X-API-Key': this.apiKey } });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },
        async researchSave(symbol, body) {
            const r = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}/save`, {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey }, body: JSON.stringify(body)
            });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },
        async researchHistory(symbol) {
            const r = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}/history`, { headers: { 'X-API-Key': this.apiKey } });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },
        async researchCompare() {
            const r = await fetch(this.baseURL + '/api/v1/research/compare', { headers: { 'X-API-Key': this.apiKey } });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },

        async getPriceTargets(symbol) {
            const resp = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}/targets`, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async setPriceTargets(symbol, body) {
            const resp = await fetch(this.baseURL + `/api/v1/research/${encodeURIComponent(symbol)}/targets`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(body)
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getDividends() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dividends', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getPerformance(benchmark, period = 'all') {
            const params = [];
            if (benchmark) params.push(`benchmark=${encodeURIComponent(benchmark)}`);
            params.push(`period=${encodeURIComponent(period || 'all')}`);
            const url = this.baseURL + '/api/v1/analytics/performance?' + params.join('&');
            const resp = await fetch(url, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getNetworthHistory() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/networth-history', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getTaxEstimate(year) {
            const url = this.baseURL + '/api/v1/analytics/tax-estimate'
                + (year ? `?year=${encodeURIComponent(year)}` : '');
            const resp = await fetch(url, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getDiversification() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/diversification', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getRisk() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/risk', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getFees() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/fees', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getStressTest(scenario, fromDate, toDate) {
            let url = this.baseURL + '/api/v1/analytics/stress-test';
            if (scenario) {
                url += '?scenario=' + encodeURIComponent(scenario);
            } else {
                url += '?from=' + encodeURIComponent(fromDate) + '&to=' + encodeURIComponent(toDate);
            }
            const resp = await fetch(url, { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getWatchlist() {
            const resp = await fetch(this.baseURL + '/api/v1/watchlist/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async addWatchlist(body) {
            const resp = await fetch(this.baseURL + '/api/v1/watchlist/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(body)
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async deleteWatchlist(symbol) {
            const resp = await fetch(this.baseURL + `/api/v1/watchlist/${encodeURIComponent(symbol)}`, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
        },

        async getGoals() {
            const resp = await fetch(this.baseURL + '/api/v1/goals/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async createGoal(body) {
            const resp = await fetch(this.baseURL + '/api/v1/goals/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(body)
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async deleteGoal(id) {
            const resp = await fetch(this.baseURL + `/api/v1/goals/${id}`, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
        }
    };
}

// ---------------------------------------------------------------------------
// Modal Manager
// ---------------------------------------------------------------------------
function createModalManager() {
    return {
        setupAddAssetModal: function() {
            const form = document.getElementById('addAssetForm');
            if (!form) {
                console.error('Add Asset form not found!');
                return;
            }

            // Pre-fill the currency from the user's default each time it opens.
            const addAssetModalEl = document.getElementById('addAssetModal');
            if (addAssetModalEl) {
                addAssetModalEl.addEventListener('show.bs.modal', () => {
                    const c = document.getElementById('assetCurrency');
                    if (c) c.value = (window.PREFS && window.PREFS.defaultCurrency) || 'EUR';
                });
            }

            form.addEventListener('submit', async (e) => {
                e.preventDefault();

                const symbolEl   = document.getElementById('assetSymbol');
                const nameEl     = document.getElementById('assetName');
                const typeEl     = document.getElementById('assetType');
                const exchangeEl = document.getElementById('assetExchange');
                const currencyEl = document.getElementById('assetCurrency');
                const sectorEl   = document.getElementById('assetSector');
                const descEl     = document.getElementById('assetDescription');

                if (!symbolEl || !nameEl || !typeEl) {
                    alert('Form elements not found. Please refresh the page.');
                    return;
                }

                const assetData = {
                    symbol:      symbolEl.value || null,
                    name:        nameEl.value || null,
                    asset_type:  typeEl.value || null,
                    exchange:    exchangeEl ? (exchangeEl.value || null) : null,
                    currency:    currencyEl ? (currencyEl.value || 'USD') : 'USD',
                    sector:      sectorEl ? (sectorEl.value || null) : null,
                    description: descEl ? (descEl.value || null) : null
                };

                if (!assetData.symbol || !assetData.name || !assetData.asset_type) {
                    alert('Please fill in all required fields (Symbol, Name, Asset Type)');
                    return;
                }

                try {
                    await window.apiClient.createAsset(assetData);
                    alert('Asset created successfully!');

                    const modal = bootstrap.Modal.getInstance(document.getElementById('addAssetModal'));
                    if (modal) modal.hide();

                    if (window.navigationManager.currentPage === 'assets') {
                        window.pageManager.loadAssetsPage();
                    }

                    form.reset();
                } catch (error) {
                    console.error('Asset creation failed:', error);
                    alert('Error creating asset: ' + error.message);
                }
            });
        }
    };
}

// ---------------------------------------------------------------------------
// Import helpers
// ---------------------------------------------------------------------------

const BROKER_HINTS = {
    indexacapital: 'Export from IndexaCapital → "Mis fondos" → Download CSV.',
    coinbase: 'Export from Coinbase → Reports → Generate → Transaction History CSV.',
    pdt: 'Export from Portfolio Dividend Tracker (app.portfoliodividendtracker.com) → Download XLSX.',
    bookings: 'Generic cash CSV with columns: date, action (deposit/withdrawal), amount, currency, broker (optional). Delimiter and decimal style are auto-detected.',
    deposits: 'Generic fixed-deposit CSV with columns: name, principal, interest_rate, start_date, maturity_date, currency (default EUR), portfolio (optional). Delimiter and European/US numbers are auto-detected.',
};

// Toggle duplicate-row checkboxes when the dup action changes.
function _applyDupCheckboxes(action) {
    const shouldCheck = action !== 'skip';
    document.querySelectorAll('.file-tx-select[data-dup="1"], .io-tx-select[data-dup="1"], .file-dep-select[data-dup="1"]').forEach(cb => {
        cb.checked = shouldCheck;
    });
}

// Shown above an import preview when some rows already exist in the DB. The
// <select id="ioDupAction"> value is read by the save handlers.
function _dupControl(transactions, bookings, deposits) {
    const dupTx = (transactions || []).filter(t => t.is_duplicate).length;
    const dupBk = (bookings || []).filter(b => b.is_duplicate).length;
    const dupDep = (deposits || []).filter(d => d.is_duplicate).length;
    const total = dupTx + dupBk + dupDep;
    if (total === 0) return '';
    return `
        <div class="alert alert-warning py-2 small d-flex flex-wrap align-items-center gap-2 mb-2">
            <span><i class="bi bi-exclamation-triangle me-1"></i><strong>${total}</strong> row(s) already exist (marked <span class="badge bg-warning text-dark">dup</span> below). Duplicates are unchecked by default.</span>
            <label class="ms-auto mb-0 d-flex align-items-center">On duplicates:
                <select id="ioDupAction" class="form-select form-select-sm d-inline-block w-auto ms-1" onchange="_applyDupCheckboxes(this.value)">
                    <option value="skip">Skip them</option>
                    <option value="add">Import anyway (add copy)</option>
                    <option value="overwrite">Overwrite existing</option>
                </select>
            </label>
        </div>`;
}

function _dupAction() {
    const el = document.getElementById('ioDupAction');
    return el ? el.value : 'skip';
}

function _toggleImportType(btn, type) {
    // btn lives in a flex div that is a direct child of the preview container (#ioFilePreview etc.)
    const container = btn.parentElement && btn.parentElement.parentElement;
    if (!container) return;
    const checkboxes = [...container.querySelectorAll('.file-tx-select')];
    if (type === 'all')  { checkboxes.forEach(cb => cb.checked = true);  return; }
    if (type === 'none') { checkboxes.forEach(cb => cb.checked = false); return; }
    const targets = checkboxes.filter(cb => cb.closest('tr') && cb.closest('tr').dataset.txtype === type);
    const allChecked = targets.length > 0 && targets.every(cb => cb.checked);
    targets.forEach(cb => cb.checked = !allChecked);
}

function _buildPreviewTable(transactions, bookings, deposits) {
    bookings = bookings || [];
    deposits = deposits || [];
    const dupControl = _dupControl(transactions, bookings, deposits);
    const hasBroker = transactions.some(tx => tx.broker) || bookings.some(b => b.broker);
    const dupBadge = '<span class="badge bg-warning text-dark ms-1">dup</span>';

    // Merge transactions + bookings sorted newest-first so dates interleave correctly.
    const merged = [
        ...transactions.map((tx, i) => ({ kind: 'tx', i, date: tx.date || '', d: tx })),
        ...bookings.map((bk, i)    => ({ kind: 'bk', i, date: bk.date || '', d: bk })),
    ].sort((a, b) => b.date.localeCompare(a.date));

    const mergedRows = merged.map(({ kind, i, d }) => {
        if (kind === 'tx') {
            const tx = d;
            return `
        <tr class="${tx.is_duplicate ? 'table-warning' : ''}" data-txtype="${esc(tx.tx_type || '')}">
            <td><input class="form-check-input file-tx-select" type="checkbox" ${(tx.is_duplicate || tx.skip) ? '' : 'checked'} data-idx="${i}" data-dup="${tx.is_duplicate ? '1' : '0'}"></td>
            ${hasBroker ? `<td><small>${esc(tx.broker || '')}</small></td>` : ''}
            <td>${tx.date || ''}${tx.is_duplicate ? dupBadge : ''}</td>
            <td><strong>${esc(tx.symbol || '')}</strong><br><small class="text-muted">${esc(tx.name || '')}</small></td>
            <td><span class="badge bg-${tx.tx_type === 'buy' ? 'success' : tx.tx_type === 'sell' ? 'danger' : 'secondary'}">${(tx.tx_type || '').toUpperCase()}</span></td>
            <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), {maximumFractionDigits: 4})}</td>
            <td class="text-end">${parseFloat(tx.price || 0).toFixed(4)}</td>
            <td>${esc(tx.currency || '')}</td>
            <td class="text-end">${parseFloat(tx.fees || 0).toFixed(2)}</td>
        </tr>`;
        } else {
            const bk = d;
            return `
        <tr class="table-info${bk.is_duplicate ? ' table-warning' : ''}" data-txtype="deposit">
            <td><i class="bi bi-bank text-muted" title="Cash booking — saved automatically"></i></td>
            ${hasBroker ? `<td><small>${esc(bk.broker || '')}</small></td>` : ''}
            <td>${bk.date || ''}${bk.is_duplicate ? dupBadge : ''}</td>
            <td><em class="text-muted">${esc(bk.action || '')} ${esc(bk.currency || '')}</em></td>
            <td><span class="badge bg-info">${esc(bk.action || '').toUpperCase()}</span></td>
            <td class="text-end">${parseFloat(bk.amount || 0).toFixed(2)}</td>
            <td class="text-end">—</td>
            <td>${esc(bk.currency || '')}</td>
            <td class="text-end">—</td>
        </tr>`;
        }
    }).join('');

    const depRows = deposits.map((dep, i) => `
        <tr class="${dep.is_duplicate ? 'table-warning' : ''}">
            <td><input class="form-check-input file-dep-select" type="checkbox" ${dep.is_duplicate ? '' : 'checked'} data-idx="${i}" data-dup="${dep.is_duplicate ? '1' : '0'}"></td>
            <td>${esc(dep.name || '')}${dep.is_duplicate ? dupBadge : ''}</td>
            <td class="text-end">${parseFloat(dep.principal || 0).toFixed(2)} ${dep.currency || ''}</td>
            <td class="text-end">${parseFloat(dep.interest_rate || 0).toFixed(3)}%</td>
            <td>${dep.start_date || ''}</td>
            <td>${dep.maturity_date || ''}</td>
            <td>${esc(dep.broker || '')}</td>
        </tr>
    `).join('');

    const totalRows = transactions.length + bookings.length + deposits.length;
    if (totalRows === 0) {
        return dupControl + '<div class="alert alert-warning">No importable data found in this file.</div>';
    }
    const bkNote = bookings.length > 0
        ? ` <span class="badge bg-info"><i class="bi bi-bank me-1"></i>${bookings.length} cash booking(s) auto-saved</span>`
        : '';
    const types = [...new Set(transactions.map(t => t.tx_type))];
    const hasDeposits = bookings.length > 0;
    const filterBtns = (types.length + (hasDeposits ? 1 : 0)) > 1 ? `
        <div class="d-flex align-items-center gap-1 flex-wrap mb-2">
            <small class="text-muted me-1">Select:</small>
            <button type="button" class="btn btn-sm py-0 btn-outline-secondary" onclick="_toggleImportType(this,'all')">All</button>
            <button type="button" class="btn btn-sm py-0 btn-outline-secondary" onclick="_toggleImportType(this,'none')">None</button>
            ${types.includes('buy')      ? '<button type="button" class="btn btn-sm py-0 btn-outline-success"   onclick="_toggleImportType(this,\'buy\')">Buy</button>' : ''}
            ${types.includes('sell')     ? '<button type="button" class="btn btn-sm py-0 btn-outline-danger"    onclick="_toggleImportType(this,\'sell\')">Sell</button>' : ''}
            ${types.includes('dividend') ? '<button type="button" class="btn btn-sm py-0 btn-outline-secondary" onclick="_toggleImportType(this,\'dividend\')">Dividend</button>' : ''}
            ${types.includes('interest') ? '<button type="button" class="btn btn-sm py-0 btn-outline-info"      onclick="_toggleImportType(this,\'interest\')">Interest</button>' : ''}
            ${hasDeposits               ? '<button type="button" class="btn btn-sm py-0 btn-outline-info"      onclick="_toggleImportType(this,\'deposit\')">Deposit</button>' : ''}
        </div>` : '';

    let html = dupControl + `
        <p class="text-muted small mb-2">Found <strong>${transactions.length}</strong> transaction(s)${bookings.length > 0 ? ` + <strong>${bookings.length}</strong> cash booking(s)` : ''}${deposits.length > 0 ? ` + <strong>${deposits.length}</strong> fixed deposit(s)` : ''}. Uncheck rows to skip.
        ${hasBroker ? ' <span class="badge bg-secondary">Portfolios auto-assigned</span>' : ''}${bkNote}</p>
        ${filterBtns}`;
    if (transactions.length > 0 || bookings.length > 0) {
        html += `
        <div class="table-responsive">
            <table class="table table-sm table-hover">
                <thead><tr><th></th>${hasBroker ? '<th>Portfolio</th>' : ''}<th>Date</th><th>Asset / Action</th><th>Type</th><th class="text-end">Qty / Amount</th><th class="text-end">Price</th><th>Currency</th><th class="text-end">Fees</th></tr></thead>
                <tbody>${mergedRows}</tbody>
            </table>
        </div>`;
    }
    if (deposits.length > 0) {
        html += `
        <h6 class="mt-3 mb-1">Fixed Deposits <span class="badge bg-secondary">${deposits.length}</span></h6>
        <div class="table-responsive">
            <table class="table table-sm table-hover">
                <thead><tr><th></th><th>Name</th><th class="text-end">Principal</th><th class="text-end">Rate</th><th>Start</th><th>Maturity</th><th>Broker</th></tr></thead>
                <tbody>${depRows}</tbody>
            </table>
        </div>`;
    }
    return html;
}

// ---------------------------------------------------------------------------
// File Import Modal (Transactions page)
// ---------------------------------------------------------------------------

function setupFileImportModal() {
    const modal = document.getElementById('fileImportModal');
    if (!modal) return;

    const step1 = document.getElementById('fileImportStep1');
    const step2 = document.getElementById('fileImportStep2');
    const parseBtn = document.getElementById('fileImportParseBtn');
    const saveBtn = document.getElementById('fileImportSaveBtn');
    const backBtn = document.getElementById('fileImportBackBtn');
    const brokerSelect = document.getElementById('fileImportBroker');
    const fileInput = document.getElementById('fileImportFile');
    const hint = document.getElementById('fileImportHint');
    const results = document.getElementById('fileImportResults');

    let parsedTransactions = [];
    let parsedBookings = [];
    let parsedDeposits = [];

    brokerSelect.addEventListener('change', () => {
        const h = BROKER_HINTS[brokerSelect.value];
        if (h) { hint.textContent = h; hint.style.display = ''; }
        else hint.style.display = 'none';
    });

    function showStep1() {
        step1.style.display = '';
        step2.style.display = 'none';
        parseBtn.style.display = '';
        saveBtn.style.display = 'none';
        backBtn.style.display = 'none';
    }

    function showStep2(transactions, bookings, skippedCount, deposits) {
        deposits = deposits || [];
        step1.style.display = 'none';
        step2.style.display = '';
        parseBtn.style.display = 'none';
        saveBtn.style.display = (transactions.length > 0 || bookings.length > 0 || deposits.length > 0) ? '' : 'none';
        backBtn.style.display = '';
        let html = _buildPreviewTable(transactions, bookings, deposits);
        if (skippedCount > 0) {
            html += `<p class="text-muted small mt-2"><i class="bi bi-info-circle me-1"></i>${skippedCount} row(s) skipped (non-trade entries, incomplete data, etc.)</p>`;
        }
        results.innerHTML = html;
    }

    modal.addEventListener('hidden.bs.modal', () => {
        fileInput.value = '';
        brokerSelect.value = '';
        hint.style.display = 'none';
        parsedTransactions = [];
        parsedBookings = [];
        parsedDeposits = [];
        showStep1();
    });

    backBtn.addEventListener('click', showStep1);

    parseBtn.addEventListener('click', async () => {
        const broker = brokerSelect.value;
        const file = fileInput.files[0];
        if (!broker) { alert('Please select a broker.'); return; }
        if (!file) { alert('Please select a file.'); return; }

        parseBtn.disabled = true;
        parseBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Parsing...';
        try {
            const data = await window.apiClient.uploadBrokerFile(broker, file);
            parsedTransactions = data.transactions || [];
            parsedBookings = data.bookings || [];
            parsedDeposits = data.deposits || [];
            showStep2(parsedTransactions, parsedBookings, data.skipped_count || 0, parsedDeposits);
        } catch (err) {
            alert('Error parsing file: ' + err.message);
        } finally {
            parseBtn.disabled = false;
            parseBtn.innerHTML = '<i class="bi bi-search me-2"></i>Parse File';
        }
    });

    saveBtn.addEventListener('click', async () => {
        const selected = Array.from(document.querySelectorAll('.file-tx-select:checked'))
            .map(cb => parsedTransactions[parseInt(cb.dataset.idx)]);
        const selectedDeps = Array.from(document.querySelectorAll('.file-dep-select:checked'))
            .map(cb => parsedDeposits[parseInt(cb.dataset.idx)]);
        if (selected.length === 0 && parsedBookings.length === 0 && selectedDeps.length === 0) { alert('No data selected.'); return; }

        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';
        try {
            const result = await window.apiClient.saveImportedTransactions(selected, parsedBookings, null, _dupAction(), selectedDeps);
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save Selected';
            bootstrap.Modal.getInstance(modal).hide();
            const bkMsg = result.saved_bookings > 0 ? ` + ${result.saved_bookings} booking(s)` : '';
            const depMsg = result.saved_deposits > 0 ? ` + ${result.saved_deposits} deposit(s)` : '';
            const msg = result.errors.length > 0
                ? `Saved ${result.saved}${bkMsg}${depMsg}. Errors:\n${result.errors.join('\n')}`
                : `Successfully imported ${result.saved} transaction(s)${bkMsg}${depMsg}.`;
            alert(msg);
            window.pageManager.loadTransactionsPage();
        } catch (err) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save Selected';
            alert('Error saving: ' + err.message);
        }
    });
}

// ---------------------------------------------------------------------------
// LLM Import Modal (Transactions page)
// ---------------------------------------------------------------------------

function setupLlmImportModal() {
    const modal = document.getElementById('llmImportModal');
    if (!modal) return;

    const step1 = document.getElementById('llmImportStep1');
    const step2 = document.getElementById('llmImportStep2');
    const extractBtn = document.getElementById('llmImportExtractBtn');
    const saveBtn = document.getElementById('llmImportSaveBtn');
    const backBtn = document.getElementById('llmImportBackBtn');
    const textarea = document.getElementById('llmImportText');
    const results = document.getElementById('llmImportResults');
    const portfolioSelect = document.getElementById('llmImportPortfolio');

    let extractedTransactions = [];

    // Populate portfolio dropdown when modal opens
    modal.addEventListener('show.bs.modal', async () => {
        if (portfolioSelect && portfolioSelect.options.length <= 1) {
            try {
                const portfolios = await window.apiClient.getPortfolios();
                portfolios.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id;
                    opt.textContent = p.name;
                    portfolioSelect.appendChild(opt);
                });
            } catch (e) { /* silent */ }
        }
    });

    function showStep1() {
        step1.style.display = '';
        step2.style.display = 'none';
        extractBtn.style.display = '';
        saveBtn.style.display = 'none';
        backBtn.style.display = 'none';
    }

    function showStep2(transactions) {
        step1.style.display = 'none';
        step2.style.display = '';
        extractBtn.style.display = 'none';
        saveBtn.style.display = transactions.length > 0 ? '' : 'none';
        backBtn.style.display = '';

        if (transactions.length === 0) {
            results.innerHTML = '<div class="alert alert-warning">No transactions could be extracted from the provided text.</div>';
            return;
        }

        const rows = transactions.map((tx, i) => `
            <tr>
                <td><input class="form-check-input tx-select" type="checkbox" checked data-idx="${i}"></td>
                <td>${tx.date || ''}</td>
                <td><strong>${esc(tx.symbol || '')}</strong><br><small class="text-muted">${esc(tx.asset_name || '')}</small></td>
                <td><span class="badge bg-${tx.tx_type === 'buy' ? 'success' : tx.tx_type === 'sell' ? 'danger' : 'info'}">${(tx.tx_type || '').toUpperCase()}</span></td>
                <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString()}</td>
                <td class="text-end">${parseFloat(tx.price || 0).toFixed(4)}</td>
                <td>${tx.currency || ''}</td>
                <td class="text-end text-muted">${(parseFloat(tx.fees) || 0) > 0 ? parseFloat(tx.fees).toFixed(2) : '—'}</td>
            </tr>
        `).join('');

        results.innerHTML = `
            <p class="text-muted small mb-2">Found <strong>${transactions.length}</strong> transaction(s). Uncheck any you don't want to import.</p>
            <div class="table-responsive">
                <table class="table table-sm table-hover">
                    <thead><tr><th></th><th>Date</th><th>Asset</th><th>Type</th><th class="text-end">Qty</th><th class="text-end">Price</th><th>Currency</th><th class="text-end">Fees</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    }

    modal.addEventListener('hidden.bs.modal', () => {
        textarea.value = '';
        extractedTransactions = [];
        showStep1();
    });

    backBtn.addEventListener('click', showStep1);

    extractBtn.addEventListener('click', async () => {
        const text = textarea.value.trim();
        if (!text) { alert('Please paste some broker statement text first.'); return; }

        extractBtn.disabled = true;
        extractBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Extracting...';

        try {
            const data = await window.apiClient.extractTransactions(text);
            extractedTransactions = data.transactions || [];
            showStep2(extractedTransactions);
        } catch (err) {
            alert('Error extracting transactions: ' + err.message);
        } finally {
            extractBtn.disabled = false;
            extractBtn.innerHTML = '<i class="bi bi-magic me-2"></i>Extract Transactions';
        }
    });

    saveBtn.addEventListener('click', async () => {
        const checked = Array.from(document.querySelectorAll('.tx-select:checked'))
            .map(cb => extractedTransactions[parseInt(cb.dataset.idx)]);

        if (checked.length === 0) { alert('No transactions selected.'); return; }

        // Normalise LLM transactions to the import/save schema
        const normalized = checked.map(tx => ({
            symbol: tx.symbol,
            name: tx.asset_name || tx.symbol,
            asset_type: 'stock',
            tx_type: tx.tx_type,
            date: tx.date,
            quantity: tx.quantity,
            price: tx.price,
            currency: tx.currency || 'EUR',
            fees: parseFloat(tx.fees) || 0.0,
            notes: tx.raw_text || ''
        }));

        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';

        try {
            const portfolioId = portfolioSelect && portfolioSelect.value ? parseInt(portfolioSelect.value) : null;
            const result = await window.apiClient.saveImportedTransactions(normalized, [], portfolioId);
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save All';
            bootstrap.Modal.getInstance(modal).hide();
            const msg = result.errors.length > 0
                ? `Saved ${result.saved}${result.duplicates_skipped ? `, ${result.duplicates_skipped} duplicate(s) skipped` : ''}. Errors:\n${result.errors.filter(e => !e.startsWith('DUPLICATE')).join('\n')}`
                : `Successfully imported ${result.saved} transaction(s)${result.duplicates_skipped ? `, ${result.duplicates_skipped} duplicate(s) skipped` : ''}.`;
            alert(msg);
            window.pageManager.loadTransactionsPage();
        } catch (err) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save All';
            alert('Error saving: ' + err.message);
        }
    });
}

window.showToast = function(msg, type) {
    const toastEl = document.getElementById('toast');
    const toastBody = document.getElementById('toastBody');
    if (!toastEl || !toastBody) return;
    toastBody.textContent = msg;
    bootstrap.Toast.getOrCreateInstance(toastEl).show();
};

// ---------------------------------------------------------------------------
