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
    // The one money formatter: locale-aware symbol and placement ("€1.234,50"
    // or "1.234,50 €" per the number-locale setting, "$12.00", "£3.10").
    // Codes Intl doesn't know (crypto tickers, GBX) fall back to "12.00 XYZ".
    // decimals: fixed digits (default 2). Returns plain text; wrap in
    // Fmt.amt() for privacy blur, but never inside an HTML attribute.
    money(v, currency, decimals) {
        if (v === null || v === undefined || v === '' || isNaN(parseFloat(v))) return '—';
        const n = parseFloat(v);
        const d = decimals != null ? decimals : 2;
        const cur = String(currency || 'EUR').toUpperCase();
        if (/^[A-Z]{3}$/.test(cur) && cur !== 'GBX') {
            try {
                return n.toLocaleString(this.loc(), { style: 'currency', currency: cur, minimumFractionDigits: d, maximumFractionDigits: d });
            } catch (e) { /* unknown code: fall through */ }
        }
        // Codes come from imported data: escape before they reach innerHTML.
        const safe = cur.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        return `${this.num(n, d, d)} ${safe}`;
    },
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
    // Native <input type="date"> ignores PREFS.dateFormat — it renders using
    // the browser/OS locale. Chrome and Firefox both honor a per-element
    // `lang` attribute for the field/picker's day/month/year order, so we pick
    // a locale whose native order matches the user's setting (separator is
    // still locale-dependent, but the order lines up).
    dateInputLang() {
        if (window.PREFS.dateFormat === 'dmy') return 'en-GB';
        if (window.PREFS.dateFormat === 'mdy') return 'en-US';
        return 'en-CA'; // ISO order: YYYY-MM-DD
    },
};
window.Fmt = Fmt;

// The `lang` attribute above is a Firefox-only nudge: Chromium renders
// type="date" inputs using the browser's own UI locale and ignores the
// element's `lang` entirely (verified — en-GB/en-US/en-CA all rendered
// mm/dd/yyyy under an en-US Chromium locale), so PREFS.dateFormat has no
// effect there. Since Chromium's native text can't be overridden from the
// page, pair any date input with a `[data-date-hint]` sibling that always
// confirms the selected date in PREFS.dateFormat.
function updateDateHint(input) {
    const hint = input.nextElementSibling;
    if (!hint || !hint.hasAttribute('data-date-hint')) return;
    hint.textContent = input.value ? `= ${Fmt.date(input.value)}` : '';
}
function updateAllDateHints(root) {
    (root || document).querySelectorAll('input[type="date"]').forEach(updateDateHint);
}
document.addEventListener('input', e => {
    if (e.target.matches && e.target.matches('input[type="date"]')) updateDateHint(e.target);
});
// Modals often set a date's `.value` from JS (defaults, edit-populate) before
// showing, which doesn't fire an 'input' event — catch those on modal show.
document.addEventListener('shown.bs.modal', e => updateAllDateHints(e.target));

// Keep every native date input (including ones rendered later into import
// previews, chat cards, modals, etc.) in sync with PREFS.dateFormat.
function applyDateInputLocale(root) {
    const lang = Fmt.dateInputLang();
    (root || document).querySelectorAll('input[type="date"]').forEach(el => { el.lang = lang; });
    updateAllDateHints(root);
}
window.applyDateInputLocale = applyDateInputLocale;
applyDateInputLocale(); // static form inputs already in the DOM
if (document.body && typeof MutationObserver !== 'undefined') {
    new MutationObserver(muts => {
        for (const m of muts) {
            m.addedNodes.forEach(n => {
                if (n.nodeType !== 1) return;
                if (n.matches && n.matches('input[type="date"]')) { n.lang = Fmt.dateInputLang(); updateDateHint(n); }
                else if (n.querySelectorAll) applyDateInputLocale(n);
            });
        }
    }).observe(document.body, { childList: true, subtree: true });
}

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

// LLM extraction returns "YYYY-MM-DDTHH:MM:SS" when a statement shows an
// execution time, but <input type="date"> silently renders any value that
// isn't a bare "YYYY-MM-DD" as blank — so the preview looked like the date was
// never extracted. Feed the input the date part only.
function txDateInputValue(d) {
    const m = /^(\d{4}-\d{2}-\d{2})(?:[T ]|$)/.exec(String(d || '').trim());
    return m ? m[1] : '';
}
window.txDateInputValue = txDateInputValue;

// Re-attach the extracted time on save (duplicate detection is time-aware, so
// it tells same-day trades apart) — but only while the user left the date as
// extracted; a hand-edited date no longer belongs to that time.
function mergeTxDateTime(inputDate, originalDate) {
    if (!inputDate) return '';
    const orig = String(originalDate || '').trim();
    const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/.exec(orig);
    return m && m[1] === inputDate ? `${inputDate}T${m[2]}` : inputDate;
}
window.mergeTxDateTime = mergeTxDateTime;

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

// ---------------------------------------------------------------------------
// Shared chart helpers (dashboard + analytics hand-rolled SVG charts)
// ---------------------------------------------------------------------------
// Series colours are CSS custom properties (--viz-1..8, --viz-neutral in
// styles.css) so light/dark each get their own validated step. SVG
// presentation attributes can't read var(), so marks set them via style=.
const VIZ_SLOTS = 8;
function vizColor(i) { return `var(--viz-${(i % VIZ_SLOTS) + 1})`; }

// Asset types keep the same colour everywhere (colour follows the entity,
// never its rank), so ETF is always blue whatever its share this month.
const VIZ_TYPE_COLOR = {
    etf: 'var(--viz-1)', stock: 'var(--viz-2)', mutual_fund: 'var(--viz-3)',
    bond: 'var(--viz-4)', crypto: 'var(--viz-7)', index: 'var(--viz-6)',
    commodity: 'var(--viz-5)', p2p: 'var(--viz-8)',
    cash: 'var(--viz-neutral)', other: 'var(--viz-other)',
};
function vizTypeColor(type) { return VIZ_TYPE_COLOR[type] || 'var(--viz-other)'; }

const VIZ_TYPE_LABEL = {
    etf: 'ETF', stock: 'Stock', mutual_fund: 'Mutual fund', bond: 'Bond',
    crypto: 'Crypto', index: 'Index fund', commodity: 'Commodity', p2p: 'P2P',
    cash: 'Cash', other: 'Other',
};
function vizTypeLabel(type) {
    return VIZ_TYPE_LABEL[type] || String(type || 'other').replace(/_/g, ' ');
}

// Whole-euro, locale-aware ("192.777 €" / "€192,777"). KPIs and legends
// don't need cents; the exact figure goes in the title/tooltip.
function fmtEurWhole(v) { return Fmt.money(parseFloat(v) || 0, 'EUR', 0); }
function fmtEurCents(v) { return Fmt.money(parseFloat(v) || 0, 'EUR', 2); }

// Sort [key, value] entries descending and fold everything past maxSlices
// into one "other" slice, so a donut never grows a 9th generated colour.
// Non-positive values can't be drawn as an arc and are returned separately.
function donutSlices(entries, maxSlices) {
    const max = maxSlices || 6;
    const pos = entries.filter(e => e[1] > 0).sort((a, b) => b[1] - a[1]);
    const nonPositive = entries.filter(e => !(e[1] > 0));
    if (pos.length <= max) return { slices: pos, nonPositive };
    const head = pos.slice(0, max - 1);
    const rest = pos.slice(max - 1);
    const otherVal = rest.reduce((s, e) => s + e[1], 0);
    return { slices: head.concat([['__other__', otherVal, rest.map(e => e[0])]]), nonPositive };
}

// Index of the value in the ascending array xs closest to x.
function nearestIndex(xs, x) {
    if (!xs.length) return -1;
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (xs[mid] <= x) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
}

// One floating HTML tooltip shared by every chart (HTML rather than SVG
// text so it can't be clipped by the chart box and wraps naturally).
const chartTip = {
    el: null,
    ensure() {
        if (this.el) return this.el;
        this.el = document.createElement('div');
        this.el.className = 'pfm-chart-tip';
        this.el.setAttribute('role', 'tooltip');
        document.body.appendChild(this.el);
        return this.el;
    },
    show(html, clientX, clientY) {
        const el = this.ensure();
        el.innerHTML = html;
        el.style.display = 'block';
        const pad = 14;
        const w = el.offsetWidth, h = el.offsetHeight;
        let left = clientX + pad;
        let top = clientY + pad;
        if (left + w > window.innerWidth - 8) left = clientX - w - pad;
        if (top + h > window.innerHeight - 8) top = clientY - h - pad;
        el.style.left = Math.max(8, left) + 'px';
        el.style.top = Math.max(8, top) + 'px';
    },
    hide() { if (this.el) this.el.style.display = 'none'; },
};
window.chartTip = chartTip;

// Charts rendered as HTML strings can't attach listeners per mark, so any
// element carrying data-chart-tip (escaped chartTipHtml markup) gets the
// shared tooltip through one delegated listener.
if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('mousemove', (e) => {
        const el = e.target && e.target.closest ? e.target.closest('[data-chart-tip]') : null;
        if (el) chartTip.show(el.getAttribute('data-chart-tip'), e.clientX, e.clientY);
    });
    document.addEventListener('mouseout', (e) => {
        const el = e.target && e.target.closest ? e.target.closest('[data-chart-tip]') : null;
        if (el && !(e.relatedTarget && el.contains(e.relatedTarget))) chartTip.hide();
    });
}

// Tooltip body: a title line plus [swatch] label ..... value rows.
function chartTipHtml(title, rows) {
    return `<div class="pfm-chart-tip-title">${esc(title)}</div>` + rows.map(r => `
        <div class="pfm-chart-tip-row">
            ${r.color ? `<span class="pfm-swatch" style="background:${r.color}${r.dashed ? ';height:2px;border-radius:0' : ''}"></span>` : '<span></span>'}
            <span class="pfm-chart-tip-label">${esc(r.label)}</span>
            <span class="pfm-chart-tip-value">${esc(r.value)}</span>
        </div>`).join('');
}

// Crosshair + tooltip for an SVG line/area chart whose viewBox width is W
// (rendered at width:100%). opts.xs: x position (viewBox units) of each
// point, ascending; opts.series: [{ y: i => viewBox y, color }] for the
// hover dots; opts.html(i): tooltip markup for point i.
function attachLineHover(svg, opts) {
    if (!svg || !opts.xs || !opts.xs.length) return;
    const NS = 'http://www.w3.org/2000/svg';
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('display', 'none');
    g.setAttribute('pointer-events', 'none');
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('y1', opts.top);
    line.setAttribute('y2', opts.bottom);
    line.setAttribute('stroke', 'currentColor');
    line.setAttribute('stroke-opacity', '0.45');
    line.setAttribute('stroke-dasharray', '3 3');
    g.appendChild(line);
    const dots = (opts.series || []).map(s => {
        const c = document.createElementNS(NS, 'circle');
        c.setAttribute('r', '4.5');
        c.setAttribute('style', `fill:${s.color};stroke:var(--viz-surface);stroke-width:2`);
        g.appendChild(c);
        return c;
    });
    const overlay = document.createElementNS(NS, 'rect');
    overlay.setAttribute('x', opts.left);
    overlay.setAttribute('y', opts.top);
    overlay.setAttribute('width', Math.max(0, opts.right - opts.left));
    overlay.setAttribute('height', Math.max(0, opts.bottom - opts.top));
    overlay.setAttribute('fill', 'transparent');
    overlay.style.cursor = 'crosshair';
    svg.appendChild(g);
    svg.appendChild(overlay);

    const move = (clientX, clientY) => {
        const rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        const vx = (clientX - rect.left) * (opts.W / rect.width);
        const i = nearestIndex(opts.xs, vx);
        if (i < 0) return;
        const x = opts.xs[i];
        line.setAttribute('x1', x);
        line.setAttribute('x2', x);
        (opts.series || []).forEach((s, k) => {
            const y = s.y(i);
            if (y == null || isNaN(y)) { dots[k].setAttribute('display', 'none'); return; }
            dots[k].removeAttribute('display');
            dots[k].setAttribute('cx', x);
            dots[k].setAttribute('cy', y);
        });
        g.removeAttribute('display');
        chartTip.show(opts.html(i), clientX, clientY);
    };
    const leave = () => { g.setAttribute('display', 'none'); chartTip.hide(); };
    overlay.addEventListener('mousemove', e => move(e.clientX, e.clientY));
    // Keyboard: the chart takes focus and ←/→ (Home/End) step through points.
    let kbIdx = opts.xs.length - 1;
    const showAt = (i) => {
        kbIdx = Math.max(0, Math.min(opts.xs.length - 1, i));
        const rect = svg.getBoundingClientRect();
        const px = rect.left + opts.xs[kbIdx] * (rect.width / opts.W);
        const y0 = (opts.series && opts.series[0]) ? opts.series[0].y(kbIdx) : opts.top;
        const py = rect.top + (y0 != null && !isNaN(y0) ? y0 : opts.top) * (rect.height / (svg.viewBox.baseVal.height || rect.height));
        move(px, py);
    };
    if (!svg.hasAttribute('tabindex')) svg.setAttribute('tabindex', '0');
    if (!svg.getAttribute('aria-label')) svg.setAttribute('aria-label', 'Chart');
    svg.setAttribute('aria-description', 'Use the left and right arrow keys to read values');
    const onFocus = () => showAt(kbIdx);
    const onKey = e => {
        const step = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
        if (step) { e.preventDefault(); showAt(kbIdx + step); }
        else if (e.key === 'Home') { e.preventDefault(); showAt(0); }
        else if (e.key === 'End') { e.preventDefault(); showAt(opts.xs.length - 1); }
        else if (e.key === 'Escape') { leave(); }
    };
    // Some charts re-render into the same <svg>: drop the previous render's
    // keyboard listeners so they don't accumulate.
    if (svg._pfmKbCleanup) svg._pfmKbCleanup();
    svg.addEventListener('focus', onFocus);
    svg.addEventListener('blur', leave);
    svg.addEventListener('keydown', onKey);
    svg._pfmKbCleanup = () => {
        svg.removeEventListener('focus', onFocus);
        svg.removeEventListener('blur', leave);
        svg.removeEventListener('keydown', onKey);
    };
    overlay.addEventListener('mouseleave', leave);
    overlay.addEventListener('touchstart', e => { const t = e.touches[0]; if (t) move(t.clientX, t.clientY); }, { passive: true });
    overlay.addEventListener('touchmove', e => { const t = e.touches[0]; if (t) move(t.clientX, t.clientY); }, { passive: true });
    overlay.addEventListener('touchend', leave);
}

// Donut + value legend. items: [{ key, label, value, color, detail? }].
// opts: { centerLabel, centerValue, emptyHtml, footerHtml, onClick(key) }.
// Hovering a slice or its legend row highlights both and shows the exact
// amount and share; the legend doubles as the table view (label, €, %).
function renderDonut(container, items, opts) {
    const o = opts || {};
    const total = items.reduce((s, it) => s + it.value, 0);
    if (!items.length || total <= 0) {
        container.innerHTML = o.emptyHtml || '<p class="text-muted small mb-0">No data yet.</p>';
        return;
    }
    const R = 62, CX = 80, CY = 80, SW = 22;
    const CIRC = 2 * Math.PI * R;
    // 2px surface gap between segments (skipped for a single full ring).
    const GAP = items.length > 1 ? 2 : 0;
    let acc = 0;
    const arcs = items.map((it, i) => {
        const frac = it.value / total;
        const len = Math.max(0, frac * CIRC - GAP);
        const off = -acc * CIRC;
        acc += frac;
        return `<circle class="pfm-donut-arc" data-idx="${i}" cx="${CX}" cy="${CY}" r="${R}" fill="none"
                    style="stroke:${it.color};stroke-width:${SW}px"
                    stroke-dasharray="${len.toFixed(2)} ${(CIRC - len).toFixed(2)}"
                    stroke-dashoffset="${off.toFixed(2)}" transform="rotate(-90 ${CX} ${CY})"/>`;
    }).join('');
    const legend = items.map((it, i) => {
        const pct = (it.value / total) * 100;
        return `<div class="pfm-legend-row${o.onClick ? ' pfm-legend-clickable' : ''}" data-idx="${i}" tabindex="0"
                    role="${o.onClick ? 'button' : 'listitem'}" aria-label="${esc(`${it.label}: ${fmtEurWhole(it.value)}, ${pct.toFixed(1)}%`)}">
                <span class="pfm-swatch" style="background:${it.color}"></span>
                <span class="pfm-legend-label text-truncate" title="${esc(it.label)}">${esc(it.label)}</span>
                <span class="pfm-legend-value">${Fmt.amt(esc(fmtEurWhole(it.value)))}</span>
                <span class="pfm-legend-pct">${pct.toFixed(1)}%</span>
            </div>`;
    }).join('');
    container.innerHTML = `
        <div class="pfm-donut">
            <svg viewBox="0 0 160 160" class="pfm-donut-svg" role="img" aria-label="${esc(o.centerLabel || 'Breakdown')}">
                ${arcs}
                <text x="${CX}" y="${CY - 6}" text-anchor="middle" font-size="11" class="pfm-donut-center-label">${esc(o.centerLabel || 'Total')}</text>
                <text x="${CX}" y="${CY + 14}" text-anchor="middle" font-size="17" font-weight="700" class="pfm-donut-center-value pfm-amt">${esc(o.centerValue || _fmtEurShort(total))}</text>
            </svg>
            <div class="pfm-legend" role="list">${legend}${o.footerHtml || ''}</div>
        </div>`;

    const svg = container.querySelector('svg');
    const rows = container.querySelectorAll('.pfm-legend-row');
    const arcEls = container.querySelectorAll('.pfm-donut-arc');
    const highlight = idx => {
        arcEls.forEach(a => a.classList.toggle('pfm-dim', idx != null && a.dataset.idx !== String(idx)));
        rows.forEach(r => r.classList.toggle('pfm-dim', idx != null && r.dataset.idx !== String(idx)));
    };
    const tipFor = idx => {
        const it = items[idx];
        const pct = (it.value / total) * 100;
        const rowsHtml = [
            { color: it.color, label: 'Amount', value: fmtEurCents(it.value) },
            { label: 'Share', value: pct.toFixed(1) + '%' },
        ];
        if (it.detail) rowsHtml.push({ label: '', value: it.detail });
        return chartTipHtml(it.label, rowsHtml);
    };
    const wire = (el) => {
        const idx = parseInt(el.dataset.idx, 10);
        el.addEventListener('mouseenter', () => highlight(idx));
        el.addEventListener('mousemove', e => chartTip.show(tipFor(idx), e.clientX, e.clientY));
        el.addEventListener('mouseleave', () => { highlight(null); chartTip.hide(); });
        if (o.onClick) el.addEventListener('click', () => o.onClick(items[idx].key));
        // Keyboard: focusing a legend row does what hovering does, with the
        // tooltip anchored to the row instead of the pointer.
        if (el.classList.contains('pfm-legend-row')) {
            el.addEventListener('focus', () => {
                highlight(idx);
                const r = el.getBoundingClientRect();
                chartTip.show(tipFor(idx), r.right, r.top);
            });
            el.addEventListener('blur', () => { highlight(null); chartTip.hide(); });
            if (o.onClick) {
                el.addEventListener('keydown', e => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); o.onClick(items[idx].key); }
                });
            }
        }
    };
    arcEls.forEach(wire);
    rows.forEach(wire);
    if (svg) svg.addEventListener('mouseleave', () => { highlight(null); chartTip.hide(); });
}

// €214k / €1.2M for a donut centre — fits the hole at any magnitude.
function _fmtEurShort(v) {
    const abs = Math.abs(v);
    if (abs >= 1e6) return '€' + (v / 1e6).toFixed(2) + 'M';
    if (abs >= 1e4) return '€' + (v / 1e3).toFixed(0) + 'k';
    if (abs >= 1e3) return '€' + (v / 1e3).toFixed(1) + 'k';
    return '€' + v.toFixed(0);
}

// Chart.js defaults for every Chart.js chart in the app: tooltips show all
// series at the hovered x instead of requiring a pixel-exact hit, and text
// uses the app font. Pies/doughnuts keep per-slice hover.
// Shared Chart.js tooltip for EUR series: "Spent: €1,234" in the same money
// format as the rest of the app (callbacks.label receives a tooltip item).
const chartEurTooltip = {
    label(item) {
        const name = item.dataset && item.dataset.label ? `${item.dataset.label}: ` : '';
        return ` ${name}${Fmt.money(item.raw, 'EUR', 0)}`;
    },
};
window.chartEurTooltip = chartEurTooltip;

// "2026-05" → "May 26" for monthly chart axes.
function monthKeyLabel(key) {
    const m = /^(\d{4})-(\d{2})/.exec(String(key || ''));
    if (!m) return String(key || '');
    const dt = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, 1);
    return dt.toLocaleDateString(Fmt.loc(), { month: 'short', year: '2-digit' });
}
window.monthKeyLabel = monthKeyLabel;

function applyChartJsDefaults() {
    if (typeof Chart === 'undefined' || !Chart.defaults) return;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily || Chart.defaults.font.family;
    Chart.defaults.interaction.mode = 'index';
    Chart.defaults.interaction.intersect = false;
    ['pie', 'doughnut', 'polarArea'].forEach(t => {
        if (Chart.overrides && Chart.overrides[t]) {
            Chart.overrides[t].interaction = { mode: 'nearest', intersect: true };
        }
    });
    // Chart.js ships light-mode greys (rgba(0,0,0,.1) grid, #666 text) that
    // all but vanish on the dark theme; follow the active theme instead.
    const dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    Chart.defaults.color = dark ? 'rgba(222,226,230,0.75)' : 'rgba(33,37,41,0.7)';
    Chart.defaults.borderColor = dark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.08)';
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.boxPadding = 4;
    Chart.defaults.plugins.tooltip.usePointStyle = true;
}

Object.assign(window, {
    vizColor, vizTypeColor, vizTypeLabel, fmtEurWhole, fmtEurCents, donutSlices,
    nearestIndex, chartTipHtml, attachLineHover, renderDonut, applyChartJsDefaults,
});

// Dashboard "Needs attention" strip: one slim line that summarises price
// alerts and action items as badges, instead of two full-width banners
// above the KPIs. Clicking a group opens its detail list below the strip;
// the × hides that group until its content changes (signature-keyed).
// slotId: 'dashAlerts' | 'dashActionItems'. part = null hides the slot.
const _dashAttn = { open: null, detail: {} };
function dashAttentionSet(slotId, part) {
    const slot = document.getElementById(slotId);
    const wrap = document.getElementById('dashAttention');
    const detail = document.getElementById('dashAttentionDetail');
    if (!slot || !wrap || !detail) return;
    if (!part) {
        slot.style.display = 'none';
        slot.innerHTML = '';
        delete _dashAttn.detail[slotId];
        if (_dashAttn.open === slotId) _dashAttn.open = null;
    } else {
        _dashAttn.detail[slotId] = part.detailHtml;
        slot.style.display = '';
        slot.innerHTML = `
            <span class="pfm-attn-group">
                <button type="button" class="pfm-attn-toggle" aria-expanded="${_dashAttn.open === slotId}" title="Show details">
                    ${part.summaryHtml}
                    <i class="bi bi-chevron-${_dashAttn.open === slotId ? 'up' : 'down'} small"></i>
                </button>
                <button type="button" class="btn-close" style="font-size:.55rem;" aria-label="Dismiss" title="Hide until something changes"></button>
            </span>`;
        slot.querySelector('.pfm-attn-toggle').addEventListener('click', () => {
            _dashAttn.open = _dashAttn.open === slotId ? null : slotId;
            _dashAttentionRender();
        });
        slot.querySelector('.btn-close').addEventListener('click', () => {
            if (part.onDismiss) part.onDismiss();
            dashAttentionSet(slotId, null);
        });
    }
    _dashAttentionRender();
}

function _dashAttentionRender() {
    const wrap = document.getElementById('dashAttention');
    const bar = document.getElementById('dashAttentionBar');
    const detail = document.getElementById('dashAttentionDetail');
    if (!wrap || !detail) return;
    const anyVisible = ['dashAlerts', 'dashActionItems'].some(id => {
        const el = document.getElementById(id);
        return el && el.style.display !== 'none' && el.innerHTML.trim();
    });
    wrap.style.display = anyVisible ? '' : 'none';
    const open = _dashAttn.open && _dashAttn.detail[_dashAttn.open];
    detail.style.display = open ? '' : 'none';
    detail.innerHTML = open || '';
    if (bar) bar.classList.toggle('has-open', !!open);
    ['dashAlerts', 'dashActionItems'].forEach(id => {
        const t = document.querySelector(`#${id} .pfm-attn-toggle`);
        if (!t) return;
        const isOpen = _dashAttn.open === id;
        t.setAttribute('aria-expanded', String(isOpen));
        const ic = t.querySelector('.bi-chevron-down, .bi-chevron-up');
        if (ic) ic.className = `bi bi-chevron-${isOpen ? 'up' : 'down'} small`;
    });
    // Research-icon clicks inside the alert detail open the research modal.
    if (!detail._wired) {
        detail._wired = true;
        detail.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-research-symbol]');
            if (btn && window.openResearchModal) {
                window.openResearchModal(btn.dataset.researchSymbol, btn.dataset.researchName || '');
            }
        });
    }
}
window.dashAttentionSet = dashAttentionSet;

// Price alerts part of the strip: price targets crossed + watchlist buy
// zones + stale price data. Loaded async so it never blocks the dashboard
// (watchlist check hits live prices). BUY/SELL/WATCH stay grouped into
// collapsible sections in the detail so ~45 targets don't make a wall.
async function loadDashboardAlerts() {
    const researchLink = (symbol, name) =>
        `<button type="button" class="btn btn-link btn-sm p-0 ms-1 align-baseline" `
        + `data-research-symbol="${esc(symbol)}" data-research-name="${esc(name || '')}" `
        + `title="View research"><i class="bi bi-clipboard-data"></i></button>`;
    try {
        const [targets, watch, fresh] = await Promise.all([
            window.apiClient.getResearchAlerts().catch(() => ({ alerts: [] })),
            window.apiClient.getWatchlistAlerts().catch(() => ({ alerts: [] })),
            window.apiClient.getDataFreshness().catch(() => null),
        ]);
        let dataItem = '';
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
                dataItem = `<li class="mb-1"><span class="badge bg-warning text-dark me-2">DATA</span>`
                    + `<strong>Price data</strong> — ${bits.join('; ')}. Gain/loss may be out of date.</li>`;
            }
        }
        const buyItems = [], sellItems = [];
        (targets.alerts || []).forEach(a => {
            const cur = a.currency || 'EUR';
            // Position context: quantity, value, and P&L vs weighted-average cost.
            let posInfo = '';
            if (a.quantity > 0) {
                const pnl = a.unrealized_pnl || 0;
                const pnlCls = pnl >= 0 ? 'text-success' : 'text-danger';
                const pnlSign = pnl >= 0 ? '+' : '';
                const avgCostTxt = a.avg_price ? ` vs avg cost ${Fmt.money(a.avg_price, cur, 2)}` : '';
                posInfo = ` <span class="text-muted">— ${Fmt.num(a.quantity, 0, 4)} sh · ${Fmt.money(a.value, cur, 2)} `
                    + `(<span class="${pnlCls}">${pnlSign}${Fmt.money(pnl, cur, 2)}, ${pnlSign}${Fmt.num(a.unrealized_pnl_pct || 0, 2, 2)}%${avgCostTxt}</span>)</span>`;
            } else {
                posInfo = ` <span class="text-muted">— not held</span>`;
            }
            const priceDateTxt = a.price_date ? ` <small class="text-muted">[${Fmt.date(a.price_date)}]</small>` : '';
            (a.triggers || []).forEach(t => {
                const buy = t.type === 'BUY';
                const nameTxt = a.name ? ` <span class="text-muted">· ${esc(a.name)}</span>` : '';
                const li = `<li class="mb-1"><span class="badge bg-${buy ? 'success' : 'danger'} me-2">${t.type}</span><strong>${esc(a.symbol)}</strong>${nameTxt} at ${Fmt.money(t.price, cur, 2)} ${buy ? '≤ buy-below' : '≥ sell-above'} ${Fmt.money(t.threshold, cur, 2)}${posInfo}${priceDateTxt}${researchLink(a.symbol, a.name)}</li>`;
                (buy ? buyItems : sellItems).push(li);
            });
        });
        const watchItems = [];
        (watch.alerts || []).forEach(a => {
            const fetchedTxt = a.price_fetched_at ? ` <small class="text-muted">[${fmtFetchedAt(a.price_fetched_at)}]</small>` : '';
            watchItems.push(`<li class="mb-1"><span class="badge bg-info text-dark me-2">WATCH</span><strong>${esc(a.symbol)}</strong> ${a.name ? '· ' + esc(a.name) : ''} at ${Fmt.num(a.price, 2, 2)} entered buy zone (≤ ${Fmt.num(a.buy_below, 2, 2)})${fetchedTxt}${researchLink(a.symbol, a.name)}</li>`);
        });
        const totalCount = (dataItem ? 1 : 0) + buyItems.length + sellItems.length + watchItems.length;
        if (!totalCount) { dashAttentionSet('dashAlerts', null); return; }
        // Dismissal is keyed by the alert content so a *new* alert reappears even
        // after the user closed the previous set; the same set stays hidden.
        const sig = hashStr(dataItem + buyItems.join('') + sellItems.join('') + watchItems.join(''));
        if (localStorage.getItem('pfmAlertsDismissed') === sig) {
            dashAttentionSet('dashAlerts', null); return;
        }
        const section = (key, label, badgeCls, list) => {
            if (!list.length) return '';
            const bodyId = `dashAlerts${key}Body`;
            return `
                <div class="mt-1">
                    <div class="d-flex align-items-center gap-2" role="button" data-bs-toggle="collapse" data-bs-target="#${bodyId}" aria-expanded="false">
                        <span class="badge ${badgeCls}">${label} (${list.length})</span>
                        <i class="bi bi-chevron-down small"></i>
                    </div>
                    <div id="${bodyId}" class="collapse">
                        <ul class="list-unstyled mb-0 small mt-1">${list.join('')}</ul>
                    </div>
                </div>`;
        };
        const chip = (n, label, cls) => n ? `<span class="badge ${cls}">${n} ${label}</span>` : '';
        dashAttentionSet('dashAlerts', {
            summaryHtml: `<span>Price signals</span>`
                + chip(buyItems.length, 'buy', 'text-bg-success')
                + chip(sellItems.length, 'sell', 'text-bg-danger')
                + chip(watchItems.length, 'watch', 'text-bg-info')
                + (dataItem ? '<span class="badge text-bg-warning">stale prices</span>' : ''),
            detailHtml: `
                <div class="fw-semibold mb-1">Price signals</div>
                ${dataItem ? `<ul class="list-unstyled mb-1 small">${dataItem}</ul>` : ''}
                ${section('Buy', 'BUY', 'bg-success', buyItems)}
                ${section('Sell', 'SELL', 'bg-danger', sellItems)}
                ${section('Watch', 'WATCH', 'bg-info text-dark', watchItems)}
                <div class="small text-muted mt-2">Targets come from your research notes — review them on the <a href="#" data-page="research">Research</a> page.</div>`,
            onDismiss: () => localStorage.setItem('pfmAlertsDismissed', sig),
        });
    } catch (e) {
        dashAttentionSet('dashAlerts', null);
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

// Manual price-update trigger wired to the dashboard "Refresh prices" button.
// Calls POST /api/v1/analytics/trigger-price-update, then polls
// GET /api/v1/analytics/price-update-status every 4 s until the run completes,
// then reloads the freshness chip and the dashboard holdings data.
async function triggerPriceUpdate() {
    const btn = document.getElementById('refreshPricesBtn');
    const icon = document.getElementById('refreshPricesIcon');
    const label = document.getElementById('refreshPricesLabel');
    if (!btn) return;

    const setRunning = () => {
        btn.disabled = true;
        if (icon) { icon.className = 'spinner-border spinner-border-sm me-1'; }
        if (label) label.textContent = 'Updating…';
    };
    const setDone = (text) => {
        btn.disabled = false;
        if (icon) { icon.className = 'bi bi-graph-up-arrow me-1'; }
        if (label) label.textContent = text || 'Refresh prices';
    };

    setRunning();
    try {
        const resp = await fetch(window.apiClient.baseURL + '/api/v1/analytics/trigger-price-update', {
            method: 'POST',
            headers: { 'X-API-Key': window.apiClient.apiKey },
        });
        if (resp.status === 409) {
            // Already running — just wait for it
        } else if (!resp.ok) {
            console.error('trigger-price-update failed', resp.status);
            setDone('Refresh prices');
            return;
        }
    } catch (e) {
        console.error('trigger-price-update error', e);
        setDone('Refresh prices');
        return;
    }

    // Poll until the background thread finishes
    const poll = async () => {
        try {
            const r = await fetch(window.apiClient.baseURL + '/api/v1/analytics/price-update-status', {
                headers: { 'X-API-Key': window.apiClient.apiKey },
            });
            if (!r.ok) { setDone('Refresh prices'); return; }
            const s = await r.json();
            if (s.running) {
                setTimeout(poll, 4000);
            } else {
                // Reload freshness chip and dashboard data
                await loadDataFreshness();
                setDone('Refresh prices');
                // Trigger the dashboard data refresh if we're on the dashboard page
                const refreshBtn = document.getElementById('refreshTransactions');
                if (refreshBtn) refreshBtn.click();
            }
        } catch (e) {
            console.error('price-update-status error', e);
            setDone('Refresh prices');
        }
    };
    setTimeout(poll, 2000);
}
window.triggerPriceUpdate = triggerPriceUpdate;

// Generates and downloads the generic CSV import template as a file.
function downloadGenericTemplate() {
    const csv = [
        'date,symbol,name,type,quantity,price,currency,fees,asset_type,notes',
        '2024-01-15,AAPL,Apple Inc,buy,10,185.50,USD,1.00,stock,',
        '2024-01-20,BTC-EUR,Bitcoin,buy,0.5,40000,EUR,0,crypto,',
        '2024-02-01,AAPL,Apple Inc,dividend,10,0.24,USD,0,stock,Q1 2024 dividend',
        '2024-03-10,MSFT,Microsoft Corp,sell,5,420.00,USD,1.50,stock,',
    ].join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'generic_import_template.csv';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
}
window.downloadGenericTemplate = downloadGenericTemplate;

// Diagnostics page: price-data freshness + the daily update-run history.
// Surfaces *why* a price may be stale (no Yahoo data vs. just old) and what
// the cron actually did, so it isn't lost to stdout.
// Readable message from an error response body: FastAPI's {"detail": ...}
// (a string, or a list of validation errors), else the raw text.
function errorDetailFromBody(text, fallback = 'Request failed') {
    const raw = String(text == null ? '' : text).trim();
    if (!raw) return fallback;
    try {
        const body = JSON.parse(raw);
        const detail = body && body.detail;
        if (typeof detail === 'string' && detail) return detail;
        if (Array.isArray(detail) && detail.length) {
            return detail.map(d => (d && d.msg) || JSON.stringify(d)).join('; ');
        }
    } catch (e) {
        // Not JSON: fall through to the raw text
    }
    return raw.length > 300 ? raw.slice(0, 300) + '…' : raw;
}
window.errorDetailFromBody = errorDetailFromBody;

// One app_logs row → badge class, headline and extra lines for the Logs tab.
// LLM calls read as "Gemini generate (model) — failed, 3 attempts, 5210 ms".
function logEntrySummary(entry) {
    const d = (entry && entry.details) || {};
    const level = String((entry && entry.level) || 'INFO').toUpperCase();
    const badge = (level === 'ERROR' || level === 'CRITICAL') ? 'bg-danger'
        : level === 'WARNING' ? 'bg-warning text-dark' : 'bg-secondary';
    let text = (entry && entry.message) || '';
    if (entry && entry.event === 'llm.call') {
        const attempts = d.attempts || 1;
        text = `${d.provider || 'LLM'} ${d.operation || 'call'} (${d.model || '?'}) — `
            + `${d.outcome || '?'}, ${attempts} attempt${attempts === 1 ? '' : 's'}`
            + (d.duration_ms != null ? `, ${d.duration_ms} ms` : '');
    }
    let extra = Array.isArray(d.errors) ? d.errors.slice() : [];
    if (!extra.length && d.exception) extra = [d.exception];
    return { level, badge, text, extra };
}
window.logEntrySummary = logEntrySummary;

async function loadLogsTab() {
    const body = document.getElementById('diagLogBody');
    const filter = document.getElementById('diagLogFilter');
    if (!body) return;
    if (filter && !filter._wired) {
        filter._wired = true;
        filter.addEventListener('change', () => loadLogsTab());
    }
    const mode = filter ? filter.value : 'problems';
    const params = mode === 'llm' ? { event: 'llm.call' }
        : mode === 'problems' ? { level: 'WARNING' } : {};
    body.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">Loading…</td></tr>';
    let items;
    try {
        items = (await window.apiClient.getLogs(params)).items || [];
    } catch (e) {
        body.innerHTML = `<tr><td colspan="4" class="text-danger small p-3">Could not load the log: ${esc(e.message)}</td></tr>`;
        return;
    }
    if (!items.length) {
        body.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">Nothing logged for this filter in the last 30 days.</td></tr>';
        return;
    }
    body.innerHTML = items.map(entry => {
        const s = logEntrySummary(entry);
        const when = entry.created_at ? new Date(entry.created_at).toLocaleString() : '';
        const source = String(entry.source || '').replace(/^portf_(manager|server)\./, '');
        const extra = s.extra.length
            ? `<details class="small text-muted mt-1"><summary>${s.extra.length} error${s.extra.length === 1 ? '' : 's'}</summary>`
              + s.extra.map(x => `<div class="font-monospace text-break">${esc(x)}</div>`).join('') + '</details>'
            : '';
        return `<tr><td class="small text-nowrap">${esc(when)}</td>`
            + `<td><span class="badge ${s.badge}">${esc(s.level)}</span></td>`
            + `<td class="small text-muted">${esc(source)}</td>`
            + `<td class="small">${esc(s.text)}${extra}</td></tr>`;
    }).join('');
}
window.loadLogsTab = loadLogsTab;

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
            const logPane = document.getElementById('diagLogs');
            if (logPane && logPane.classList.contains('active')) {
                loadLogsTab();
            } else if (dqActive) {
                _dqLoaded = false;
                loadDataQualityTab();
            } else {
                loadDiagnosticsPage();
            }
        });
    }

    // Wire DQ tab activation.  Use both shown.bs.tab (after animation) and click
    // (immediate, before Bootstrap fires its event) so loading is reliable regardless
    // of Bootstrap version or animation state.
    const dqTabBtn = document.getElementById('diagTabDQ');
    if (dqTabBtn && !dqTabBtn._dqWired) {
        dqTabBtn._dqWired = true;
        dqTabBtn.addEventListener('click', () => setTimeout(() => loadDataQualityTab(), 50));
        dqTabBtn.addEventListener('shown.bs.tab', () => loadDataQualityTab());
    }

    const logTabBtn = document.getElementById('diagTabLogs');
    if (logTabBtn && !logTabBtn._logWired) {
        logTabBtn._logWired = true;
        logTabBtn.addEventListener('shown.bs.tab', () => loadLogsTab());
    }

    // Restore last active tab, or ensure Price Health is active (nav clearing may have
    // stripped the active class from both tab buttons).
    const lastTab = localStorage.getItem('pfmDiagTab');
    const dqBtn2  = document.getElementById('diagTabDQ');
    const phBtn   = document.getElementById('diagTabPrice');
    const dqPane  = document.getElementById('diagDataQuality');
    const logPane = document.getElementById('diagLogs');
    if (lastTab === 'logs' && logTabBtn && window.bootstrap) {
        if (logPane && logPane.classList.contains('active')) loadLogsTab();
        else new window.bootstrap.Tab(logTabBtn).show();
    } else if (lastTab === 'dq' && dqBtn2 && window.bootstrap) {
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
            const tab = btn.id === 'diagTabDQ' ? 'dq' : btn.id === 'diagTabLogs' ? 'logs' : 'price';
            localStorage.setItem('pfmDiagTab', tab);
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

    await Promise.all([
        _loadReconCard().catch(() => {}),
        _loadDupsCard().catch(() => {}),
        _loadSuspCard().catch(() => {}),
    ]);

    async function _loadReconCard() {
        const el = document.getElementById('dqReconBody');
        if (!el) return;
        el.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">Loading…</td></tr>';
        let data = null;
        try { data = await window.apiClient.getDQReconciliation(); } catch (_) {}
        if (!data) {
            el.innerHTML = '<tr><td colspan="5" class="text-danger small p-3">Could not load reconciliation data.</td></tr>';
            return;
        }
        const portfolios = Array.isArray(data) ? data : (data.portfolios || []);
        if (!portfolios.length) {
            el.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">No portfolios found.</td></tr>';
            return;
        }
        el.innerHTML = portfolios.map(p => `
            <tr>
                <td class="fw-semibold">${esc(p.portfolio_name)}</td>
                <td class="text-end font-monospace">${Fmt.amt(Fmt.num(p.implied_cash, 2, 2))}</td>
                <td class="text-end font-monospace">${Fmt.amt(Fmt.num(p.invested_value, 2, 2))}</td>
                <td class="text-end font-monospace fw-semibold">${Fmt.amt(Fmt.num(p.total_accounted, 2, 2))}</td>
                <td class="text-end small text-muted">${Fmt.amt(Fmt.num(p.net_bookings, 2, 2))}</td>
            </tr>`).join('');
    }

    async function _loadDupsCard() {
        const body   = document.getElementById('dqDupsBody');
        const footer = document.getElementById('dqDupsFooter');
        if (!body) return;
        body.innerHTML = '<div class="text-muted small p-3">Loading…</div>';
        let data = null;
        try { data = await window.apiClient.getDQDuplicates(); } catch (_) {}
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
                                    <li><button type="button" class="dropdown-item dq-del-tx" data-id="${d.tx_a.id}" data-key="${esc(d.key)}">Delete #${esc(d.tx_a.id)} (${Fmt.date(d.tx_a.date)})</button></li>
                                    <li><button type="button" class="dropdown-item dq-del-tx" data-id="${d.tx_b.id}" data-key="${esc(d.key)}">Delete #${esc(d.tx_b.id)} (${Fmt.date(d.tx_b.date)})</button></li>
                                </ul>
                                <button type="button" class="btn btn-outline-secondary btn-sm dq-dism-dup" data-key="${esc(d.key)}" title="${isDism ? 'Undismiss' : 'Dismiss'}">${isDism ? '<i class="bi bi-eye"></i>' : '<i class="bi bi-eye-slash"></i>'}</button>
                            </div>
                        </div>
                        <div class="row small g-1">
                            <div class="col-6 bg-body-secondary rounded p-1">
                                <div class="fw-semibold">${esc(d.tx_a.asset)}${d.tx_a.asset_name && d.tx_a.asset_name !== d.tx_a.asset ? `<div class="small text-muted fw-normal">${esc(d.tx_a.asset_name)}</div>` : ''}</div>
                                <div>${esc(d.tx_a.type)} · ${Fmt.num(d.tx_a.quantity, 0, 4)} @ ${Fmt.num(d.tx_a.price, 0, 4)}</div>
                                <div class="text-muted">${Fmt.date(d.tx_a.date)} · #${d.tx_a.id}</div>
                            </div>
                            <div class="col-6 bg-body-secondary rounded p-1">
                                <div class="fw-semibold">${esc(d.tx_b.asset)}${d.tx_b.asset_name && d.tx_b.asset_name !== d.tx_b.asset ? `<div class="small text-muted fw-normal">${esc(d.tx_b.asset_name)}</div>` : ''}</div>
                                <div>${esc(d.tx_b.type)} · ${Fmt.num(d.tx_b.quantity, 0, 4)} @ ${Fmt.num(d.tx_b.price, 0, 4)}</div>
                                <div class="text-muted">${Fmt.date(d.tx_b.date)} · #${d.tx_b.id}</div>
                            </div>
                        </div>
                    </div>`;
                }).join('');

                body.querySelectorAll('.dq-del-older, .dq-del-tx').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const id  = parseInt(btn.dataset.id);
                        const key = btn.dataset.key;
                        if (!(await confirmDialog({ title: 'Delete transaction', message: `Delete transaction #${id}? Holdings, cost basis and tax figures are recalculated without it. This cannot be undone.`, danger: true }))) return;
                        try {
                            await window.apiClient.deleteTransaction(id);
                            _dqDismiss('dup', key);
                            await _loadDupsCard();
                        } catch (e) {
                            notify('Failed to delete: ' + e.message);
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
        let data = null;
        try { data = await window.apiClient.getDQSuspicious(); } catch (_) {}
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
                        <td class="small">${Fmt.date(i.date)}</td>
                        <td class="small">${esc(i.type)}</td>
                        <td class="small">${esc(i.description)}</td>
                        <td class="text-nowrap">
                            <button type="button" class="btn btn-link btn-sm p-0 me-1 dq-view-tx" data-asset="${esc(i.asset)}" title="View transactions"><i class="bi bi-box-arrow-up-right"></i></button>
                            ${i.transaction_id ? `<button type="button" class="btn btn-link btn-sm p-0 me-1 text-danger dq-del-susp" data-id="${i.transaction_id}" title="Delete transaction"><i class="bi bi-trash"></i></button>` : ''}
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

                body.querySelectorAll('.dq-del-susp').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const id = parseInt(btn.dataset.id);
                        if (!(await confirmDialog({ title: 'Delete transaction', message: `Delete transaction #${id}? Holdings, cost basis and tax figures are recalculated without it. This cannot be undone.`, danger: true }))) return;
                        try {
                            await window.apiClient.deleteTransaction(id);
                            await _loadSuspCard();
                        } catch (e) {
                            notify('Failed to delete: ' + e.message);
                        }
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
    // Charts created after a theme switch pick up matching axis colours.
    if (document.body) applyChartJsDefaults();
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
    return currency ? Fmt.money(amount, currency, 2) : Fmt.num(amount, 2, 2);
}

// Build Yahoo Finance quote link + Simply Wall St lookup link for a symbol.
// Simply Wall St has no ticker-only deep link (its asset URLs need a
// country/sector/exchange/company-name slug we don't store), so this routes
// through a Google site-search instead of guessing a URL that would 404.
function assetLinks(symbol) {
    if (!symbol) return '';
    const s = encodeURIComponent(symbol);
    return `
      <a href="https://finance.yahoo.com/quote/${s}" target="_blank" rel="noopener" class="text-decoration-none me-1" title="View on Yahoo Finance"><i class="bi bi-graph-up"></i></a>
      <a href="https://www.google.com/search?q=${s}+site:simplywall.st" target="_blank" rel="noopener" class="text-decoration-none" title="Search for this asset on Simply Wall St"><i class="bi bi-search"></i></a>`;
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
                if (error instanceof TypeError) {
                    throw new Error('Cannot reach the server. Please check that the backend is running.');
                }
                console.error('API validation error:', error);
                return false;
            }
        },

        // Username/password login → returns the API key the data endpoints need
        loginWithPassword: async function(username, password) {
            let resp;
            try {
                resp = await fetch(this.baseURL + '/api/v1/auth/login-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
            } catch (error) {
                throw new Error('Cannot reach the server. Please check that the backend is running.');
            }
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

        async getTransaction(id) {
            const response = await fetch(this.baseURL + `/api/v1/transactions/${id}`, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error(await response.text());
            return response.json();
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
                throw new Error(errorDetailFromBody(await response.text(), 'Extraction failed'));
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

        async uploadBankStatement(file, accountPortfolioId, accountName) {
            const form = new FormData();
            form.append('file', file);
            if (accountPortfolioId) form.append('account_portfolio_id', accountPortfolioId);
            if (accountName) form.append('account_name', accountName);
            const response = await fetch(this.baseURL + '/api/v1/spending/upload', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey },
                body: form
            });
            if (!response.ok) {
                let detail = 'Parse failed';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async saveSpendingTransactions(accountPortfolioId, rows, duplicateAction = 'skip') {
            const response = await fetch(this.baseURL + '/api/v1/spending/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ account_portfolio_id: accountPortfolioId, rows, duplicate_action: duplicateAction })
            });
            if (!response.ok) {
                let detail = 'Save failed';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async suggestSpendingCategories(rows) {
            const response = await fetch(this.baseURL + '/api/v1/spending/suggest-categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ rows })
            });
            if (!response.ok) {
                let detail = 'Suggestion failed';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async getSpendingTransactions(params = {}) {
            // Array values (e.g. categories: ['A', 'B']) must become repeated
            // query params (?categories=A&categories=B) for FastAPI's
            // List[str] params -- URLSearchParams(plainObject) would instead
            // stringify the array as one comma-joined value.
            const qsParams = new URLSearchParams();
            Object.entries(params).forEach(([key, value]) => {
                if (value === undefined || value === null || value === '') return;
                if (Array.isArray(value)) value.forEach(v => qsParams.append(key, v));
                else qsParams.append(key, value);
            });
            const qs = qsParams.toString();
            const response = await fetch(this.baseURL + '/api/v1/spending/' + (qs ? '?' + qs : ''), {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load spending transactions');
            return response.json();
        },
        // The transfer matcher can only pair a counterpart that was actually
        // imported, so a genuine move to an untracked account needs to be
        // settled by hand or it counts as spending forever.
        async setSpendingTransferFlag(id, isTransfer) {
            const response = await fetch(this.baseURL + '/api/v1/spending/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ is_transfer: isTransfer })
            });
            if (!response.ok) {
                let detail = 'Failed to update transfer flag';
                try { const body = await response.json(); detail = body.detail || detail; }
                catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async updateSpendingCategory(id, category) {
            const response = await fetch(this.baseURL + '/api/v1/spending/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ category })
            });
            if (!response.ok) {
                let detail = 'Failed to update category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async rescanTransfers() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rescan-transfers', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to rescan transfers';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async rescanCategories(ids) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rescan-categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(ids ? { ids } : {})
            });
            if (!response.ok) {
                let detail = 'Failed to rescan categories';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async getSpendingRules() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to load rules';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async getSpendingCategories() {
            const response = await fetch(this.baseURL + '/api/v1/spending/categories', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load categories');
            return response.json();
        },
        async createSpendingCategory(name, parentName) {
            const response = await fetch(this.baseURL + '/api/v1/spending/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name, parent_name: parentName })
            });
            if (!response.ok) {
                let detail = 'Failed to create category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async getSpendingCategoryTree() {
            const response = await fetch(this.baseURL + '/api/v1/spending/categories/tree', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load category tree');
            return response.json();
        },
        async getSpendingTrend(months = 12) {
            const response = await fetch(this.baseURL + `/api/v1/spending/trend?months=${months}`, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load spending trend');
            return response.json();
        },
        async getSpendingCategoryBreakdown(parent, days) {
            const response = await fetch(
                this.baseURL + `/api/v1/spending/categories/breakdown?parent=${encodeURIComponent(parent)}&days=${days}`,
                { headers: { 'X-API-Key': this.apiKey } }
            );
            if (!response.ok) {
                let detail = 'Failed to load category breakdown';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                const error = new Error(detail);
                error.status = response.status;
                throw error;
            }
            return response.json();
        },
        async reparentSpendingCategory(name, newParentName) {
            const response = await fetch(
                this.baseURL + '/api/v1/spending/categories/' + encodeURIComponent(name) + '/parent',
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                    body: JSON.stringify({ new_parent_name: newParentName })
                }
            );
            if (!response.ok) {
                let detail = 'Failed to move category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async renameSpendingCategory(oldName, newName) {
            const response = await fetch(
                this.baseURL + '/api/v1/spending/categories/' + encodeURIComponent(oldName),
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                    body: JSON.stringify({ new_name: newName })
                }
            );
            if (!response.ok) {
                let detail = 'Failed to rename category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async createSpendingRule(pattern, category) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ pattern, category })
            });
            if (!response.ok) {
                let detail = 'Failed to create rule';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async deleteSpendingRule(id) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to delete rule';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async updateSpendingRule(id, payload) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!response.ok) {
                let detail = 'Failed to update rule';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async getSpendingSummary(days = 30) {
            const response = await fetch(this.baseURL + '/api/v1/spending/summary?days=' + days, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to load spending summary';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async deleteSpendingTransaction(id) {
            const response = await fetch(this.baseURL + '/api/v1/spending/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to delete transaction';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },

        // ── Budget ─────────────────────────────────────────────────
        // All of these share one error shape: FastAPI's `detail` when the
        // response carries JSON, a generic message otherwise.
        async _budgetFetch(path, options = {}, fallback = 'Budget request failed') {
            const headers = { 'X-API-Key': this.apiKey };
            if (options.body) headers['Content-Type'] = 'application/json';
            const response = await fetch(this.baseURL + '/api/v1/budgets' + path, {
                ...options,
                headers
            });
            if (!response.ok) {
                let detail = fallback;
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                const error = new Error(detail);
                error.status = response.status;
                throw error;
            }
            return response.json();
        },
        async getBudgets() {
            return this._budgetFetch('/', {}, 'Failed to load budgets');
        },
        async getBudget(id) {
            return this._budgetFetch('/' + id, {}, 'Failed to load budget');
        },
        async createBudget(payload) {
            return this._budgetFetch('/', { method: 'POST', body: JSON.stringify(payload) }, 'Failed to create budget');
        },
        async updateBudget(id, payload) {
            return this._budgetFetch('/' + id, { method: 'PUT', body: JSON.stringify(payload) }, 'Failed to update budget');
        },
        async deleteBudget(id) {
            return this._budgetFetch('/' + id, { method: 'DELETE' }, 'Failed to delete budget');
        },
        async activateBudget(id) {
            return this._budgetFetch('/' + id + '/activate', { method: 'POST' }, 'Failed to activate budget');
        },
        async getBudgetLines(id) {
            return this._budgetFetch('/' + id + '/lines', {}, 'Failed to load budget lines');
        },
        async createBudgetLine(id, payload) {
            return this._budgetFetch('/' + id + '/lines', { method: 'POST', body: JSON.stringify(payload) }, 'Failed to add budget line');
        },
        async updateBudgetLine(id, lineId, payload) {
            return this._budgetFetch('/' + id + '/lines/' + lineId, { method: 'PUT', body: JSON.stringify(payload) }, 'Failed to update budget line');
        },
        async deleteBudgetLine(id, lineId) {
            return this._budgetFetch('/' + id + '/lines/' + lineId, { method: 'DELETE' }, 'Failed to delete budget line');
        },
        async bulkUpsertBudgetLines(id, lines) {
            return this._budgetFetch('/' + id + '/lines/bulk', { method: 'POST', body: JSON.stringify({ lines }) }, 'Failed to save budget lines');
        },
        async getBudgetVariance(id, months = 6, endMonth = null) {
            const params = new URLSearchParams({ months: String(months) });
            if (endMonth) params.append('end_month', endMonth);
            return this._budgetFetch('/' + id + '/variance?' + params.toString(), {}, 'Failed to load budget variance');
        },
        async getBudgetSeedProposals(id, months = 12) {
            return this._budgetFetch('/' + id + '/seed-proposals?months=' + months, {}, 'Failed to load suggestions');
        },
        async getBudgetSummary() {
            return this._budgetFetch('/summary', {}, 'Failed to load budget summary');
        },

        async sendChat(message, sessionId) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ message, session_id: sessionId, live: false })
            });
            if (!response.ok) {
                throw new Error(errorDetailFromBody(await response.text(), 'Chat failed'));
            }
            return response.json();
        },

        async getLogs(params = {}) {
            const qs = new URLSearchParams({ limit: '200', ...params }).toString();
            const response = await fetch(this.baseURL + '/api/v1/system/logs?' + qs, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                throw new Error(errorDetailFromBody(await response.text(), 'Failed to load logs'));
            }
            return response.json();
        },

        async listChatSessions() {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load sessions');
            return response.json();
        },

        async createChatSession(name) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name })
            });
            if (!response.ok) throw new Error('Failed to create session');
            return response.json();
        },

        async deleteChatSession(id) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok && response.status !== 404) throw new Error('Failed to delete session');
        },

        async renameChatSession(id, name) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name })
            });
            if (!response.ok) throw new Error('Failed to rename session');
            return response.json();
        },

        async getChatSessionMessages(id) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id + '/messages', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load messages');
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

        async resolveAssetTickers() {
            const r = await fetch(this.baseURL + '/api/v1/assets/resolve-tickers', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey },
            });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
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
        async updateManualAsset(id, payload) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/' + id, {
                method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to update');
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
        async getCashflow() {
            const resp = await fetch(this.baseURL + '/api/v1/networth/cashflow', { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to load cash flow');
            return resp.json();
        },
        async createCashflowEntry(payload) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/cashflow', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to add entry');
            return resp.json();
        },
        async deleteCashflowEntry(id) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/cashflow/' + id, { method: 'DELETE', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to delete entry');
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

        async getActionItems() {
            const resp = await fetch(this.baseURL + '/api/v1/action-items/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load action items');
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

        async saveSyncConfig(sheetId) {
            const resp = await fetch(this.baseURL + '/api/v1/sync/pdt-config', {
                method: 'PUT',
                headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
                body: JSON.stringify({ spreadsheet_id: sheetId }),
            });
            if (!resp.ok) { const t = await resp.text(); throw new Error(t); }
            return resp.json();
        },

        async syncBackup(sheetId) {
            const params = new URLSearchParams();
            if (sheetId) params.set('spreadsheet_id', sheetId);
            const url = this.baseURL + '/api/v1/sync/pdt-backup?' + params.toString();
            const resp = await fetch(url, { method: 'POST', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) { const t = await resp.text(); throw new Error(t); }
            return resp.json();
        },

        async syncDownload(sheetId, fmt = 'xlsx') {
            const params = new URLSearchParams({ fmt });
            if (sheetId) params.set('spreadsheet_id', sheetId);
            const url = this.baseURL + '/api/v1/sync/pdt-download?' + params.toString();
            const resp = await fetch(url, { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) { const t = await resp.text(); throw new Error(t); }
            const blob = await resp.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `pdt-backup-${new Date().toISOString().slice(0, 10)}.${fmt}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
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

        async getRebalancePlan(requestBody) {
            const resp = await fetch(this.baseURL + '/api/v1/rebalance/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(requestBody)
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
            if (!resp.ok) throw new Error(errorDetailFromBody(await resp.text(), 'Analysis failed'));
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

        async getPortfolioAnalysis(portfolioId = null, refresh = false) {
            const params = new URLSearchParams();
            if (portfolioId) params.set('portfolio_id', portfolioId);
            if (refresh) params.set('refresh', 'true');
            const qs = params.toString() ? '?' + params.toString() : '';
            const r = await fetch(this.baseURL + '/api/v1/research/portfolio-analysis' + qs, { headers: { 'X-API-Key': this.apiKey } });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },

        async getAdvisorSettings() {
            const r = await fetch(this.baseURL + '/api/v1/research/portfolio-analysis/settings', { headers: { 'X-API-Key': this.apiKey } });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },

        async getVapidKey() {
            const r = await fetch(this.baseURL + '/api/v1/notifications/vapid-key');
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },

        async subscribePush(sub) {
            const r = await fetch(this.baseURL + '/api/v1/notifications/subscribe', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
                body: JSON.stringify(sub),
            });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },

        async unsubscribePush(sub) {
            const r = await fetch(this.baseURL + '/api/v1/notifications/subscribe', {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
                body: JSON.stringify(sub),
            });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },

        async putAdvisorSettings(cacheTtlHours) {
            const r = await fetch(this.baseURL + '/api/v1/research/portfolio-analysis/settings', {
                method: 'PUT',
                headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
                body: JSON.stringify({ cache_ttl_hours: cacheTtlHours }),
            });
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

        async getFundOverlap() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/fund-overlap', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getFundProfiles() {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load fund profiles');
            return resp.json();
        },

        async getBenchmarks() {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/benchmarks', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load benchmarks');
            return resp.json();
        },

        async getFundProfile(assetId) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (resp.status === 404) return null;
            if (!resp.ok) throw new Error('Failed to load fund profile');
            return resp.json();
        },

        async saveFundProfile(assetId, payload) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to save profile');
            return resp.json();
        },

        async refreshFundProfile(assetId, benchmarkKey, force = false) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId + '/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ benchmark_key: benchmarkKey, force })
            });
            if (!resp.ok) {
                const err = new Error((await resp.json().catch(() => ({}))).detail || 'Failed to refresh profile');
                // Callers need to tell "hand-edited profile, needs confirmation"
                // (409) apart from every other failure mode.
                err.status = resp.status;
                throw err;
            }
            return resp.json();
        },

        async suggestFundProfile(assetId) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId + '/suggest', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Suggestion failed');
            return resp.json();
        },

        async getRisk(benchmark) {
            const params = benchmark ? `?benchmark=${encodeURIComponent(benchmark)}` : '';
            const resp = await fetch(this.baseURL + '/api/v1/analytics/risk' + params, {
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

        async getCorrelation(portfolioId = null, days = 90) {
            let url = this.baseURL + '/api/v1/analytics/correlation?days=' + days;
            if (portfolioId != null) url += '&portfolio_id=' + portfolioId;
            const resp = await fetch(url, { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },

        async getPortfolioComparison() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/portfolio-comparison', {
                headers: { 'X-API-Key': this.apiKey }
            });
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

        async updateGoal(id, body) {
            const resp = await fetch(this.baseURL + `/api/v1/goals/${id}`, {
                method: 'PUT',
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
                    notify('Form elements not found. Please refresh the page.');
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
                    notify('Please fill in all required fields (Symbol, Name, Asset Type)');
                    return;
                }

                try {
                    await window.apiClient.createAsset(assetData);
                    notify('Asset created successfully!');

                    const modal = bootstrap.Modal.getInstance(document.getElementById('addAssetModal'));
                    if (modal) modal.hide();

                    if (window.navigationManager.currentPage === 'assets') {
                        window.pageManager.loadAssetsPage();
                    }

                    form.reset();
                } catch (error) {
                    console.error('Asset creation failed:', error);
                    notify('Error creating asset: ' + error.message);
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
    generic: 'Universal CSV for any broker. Required columns: date, symbol, type (buy/sell/dividend/interest), quantity, price, currency. Optional: name, fees, asset_type, notes. <a href="#" onclick="downloadGenericTemplate();return false;">Download template</a>.',
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
            <td>${Fmt.date(tx.date)}${tx.is_duplicate ? dupBadge : ''}</td>
            <td class="position-relative">
              <input class="form-control form-control-sm symbol-edit border-0 fw-bold px-1" style="width:110px" data-idx="${i}" value="${esc(tx.symbol || '')}" autocomplete="off" spellcheck="false" placeholder="SYMBOL">
              <div class="list-group position-absolute shadow symbol-suggest" style="min-width:210px;max-height:180px;overflow-y:auto;display:none;z-index:1050"></div>
              <small class="text-muted d-block">${esc(tx.name || '')}</small>
            </td>
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
            <td>${Fmt.date(bk.date)}${bk.is_duplicate ? dupBadge : ''}</td>
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
            <td>${Fmt.date(dep.start_date)}</td>
            <td>${Fmt.date(dep.maturity_date)}</td>
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

// Wire AssetSearch autocomplete on all .symbol-edit inputs inside containerEl.
// Called after setting innerHTML on a preview container.
async function _wireImportSymbolSearch(containerEl) {
    let assets;
    try {
        assets = await window.apiClient.getAssets();
    } catch (e) {
        return;
    }
    const enriched = (assets || []).map(a => AssetSearch.enrich(a.symbol, a.name, { currency: a.currency }));
    containerEl.querySelectorAll('.symbol-edit').forEach(input => {
        const suggest = input.nextElementSibling;
        if (!suggest || !suggest.classList.contains('symbol-suggest')) return;
        AssetSearch.buildAutocomplete(input, suggest, {
            getSuggestions: () => enriched,
            onSelect: (a) => { input.value = a.symbol; },
        });
    });
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
        // BROKER_HINTS is a static literal — safe for innerHTML. Never interpolate user/server data here.
        if (h) { hint.innerHTML = h; hint.style.display = ''; }
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
        _wireImportSymbolSearch(results);
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
        if (!broker) { notify('Please select a broker.'); return; }
        if (!file) { notify('Please select a file.'); return; }

        parseBtn.disabled = true;
        parseBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Parsing...';
        try {
            const data = await window.apiClient.uploadBrokerFile(broker, file);
            parsedTransactions = data.transactions || [];
            parsedBookings = data.bookings || [];
            parsedDeposits = data.deposits || [];
            showStep2(parsedTransactions, parsedBookings, data.skipped_count || 0, parsedDeposits);
        } catch (err) {
            notify('Error parsing file: ' + err.message);
        } finally {
            parseBtn.disabled = false;
            parseBtn.innerHTML = '<i class="bi bi-search me-2"></i>Parse File';
        }
    });

    saveBtn.addEventListener('click', async () => {
        const selected = Array.from(document.querySelectorAll('.file-tx-select:checked'))
            .map(cb => {
                const tx = Object.assign({}, parsedTransactions[parseInt(cb.dataset.idx)]);
                const inp = cb.closest('tr')?.querySelector('.symbol-edit');
                if (inp && inp.value.trim()) tx.symbol = inp.value.trim().toUpperCase();
                return tx;
            });
        const selectedDeps = Array.from(document.querySelectorAll('.file-dep-select:checked'))
            .map(cb => parsedDeposits[parseInt(cb.dataset.idx)]);
        if (selected.length === 0 && parsedBookings.length === 0 && selectedDeps.length === 0) { notify('No data selected.'); return; }

        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';
        try {
            const result = await window.apiClient.saveImportedTransactions(selected, parsedBookings, null, _dupAction(), selectedDeps);
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save Selected';
            showImportResult(result, { afterModal: modal, actions: [_VIEW_TX_ACTION] });
            bootstrap.Modal.getInstance(modal).hide();
            window.pageManager.loadTransactionsPage();
        } catch (err) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save Selected';
            notify('Error saving: ' + err.message);
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
                const portfolios = (await window.apiClient.getPortfolios()).filter(p => p.account_type !== 'bank');
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
                <td>${Fmt.date(tx.date)}</td>
                <td><strong>${esc(tx.symbol || '')}</strong><br><small class="text-muted">${esc(tx.asset_name || '')}</small></td>
                <td><span class="badge bg-${tx.tx_type === 'buy' ? 'success' : tx.tx_type === 'sell' ? 'danger' : 'info'}">${(tx.tx_type || '').toUpperCase()}</span></td>
                <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), {maximumFractionDigits: 4})}</td>
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
        if (!text) { notify('Please paste some broker statement text first.'); return; }

        extractBtn.disabled = true;
        extractBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Extracting...';

        try {
            const data = await window.apiClient.extractTransactions(text);
            extractedTransactions = data.transactions || [];
            showStep2(extractedTransactions);
        } catch (err) {
            notify('Error extracting transactions: ' + err.message);
        } finally {
            extractBtn.disabled = false;
            extractBtn.innerHTML = '<i class="bi bi-magic me-2"></i>Extract Transactions';
        }
    });

    saveBtn.addEventListener('click', async () => {
        const checked = Array.from(document.querySelectorAll('.tx-select:checked'))
            .map(cb => extractedTransactions[parseInt(cb.dataset.idx)]);

        if (checked.length === 0) { notify('No transactions selected.'); return; }

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
            showImportResult(result, { afterModal: modal, actions: [_VIEW_TX_ACTION] });
            bootstrap.Modal.getInstance(modal).hide();
            window.pageManager.loadTransactionsPage();
        } catch (err) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save All';
            notify('Error saving: ' + err.message);
        }
    });
}

// Non-blocking notifications (replaces native notify(), which froze the page
// and couldn't be styled). Toasts stack top-right; errors stay longer and
// never auto-hide while hovered. level: success | info | warning | danger.
function notifyLevel(msg) {
    const m = String(msg || '').toLowerCase();
    // Validation nudges first: "... cannot be empty" is a form hint, not a failure.
    if (/(required|cannot be empty|at least one|please |first\.$)/.test(m) && !/\berror\b|\bfailed\b/.test(m)) return 'warning';
    if (/\b(error|failed|failure|could not|couldn't|cannot|can't|invalid|not found)\b/.test(m)) return 'danger';
    if (/\b(success|successfully|saved|created|imported|updated|deleted|done)\b/.test(m)) return 'success';
    if (/^(please|no |nothing|select|paste|open |choose|enter )/.test(m.trim())) return 'warning';
    return 'info';
}
window.notifyLevel = notifyLevel;

const NOTIFY_STYLE = {
    success: { icon: 'bi-check-circle-fill', cls: 'text-success', title: 'Done', delay: 4000 },
    info:    { icon: 'bi-info-circle-fill', cls: 'text-info', title: 'Info', delay: 6000 },
    warning: { icon: 'bi-exclamation-triangle-fill', cls: 'text-warning', title: 'Check this', delay: 7000 },
    danger:  { icon: 'bi-x-octagon-fill', cls: 'text-danger', title: 'Something went wrong', delay: 12000 },
};

function notify(msg, level) {
    const lvl = NOTIFY_STYLE[level] ? level : notifyLevel(msg);
    const st = NOTIFY_STYLE[lvl];
    let host = document.getElementById('pfmToastStack');
    if (!host) {
        host = document.createElement('div');
        host.id = 'pfmToastStack';
        host.className = 'toast-container position-fixed top-0 end-0 p-3';
        host.style.zIndex = '2100';
        host.setAttribute('aria-live', 'polite');
        document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = `toast pfm-toast pfm-toast-${lvl}`;
    el.setAttribute('role', lvl === 'danger' ? 'alert' : 'status');
    el.innerHTML = `
        <div class="toast-header">
            <i class="bi ${st.icon} ${st.cls} me-2"></i>
            <strong class="me-auto">${st.title}</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body" style="white-space:pre-line;"></div>`;
    el.querySelector('.toast-body').textContent = String(msg == null ? '' : msg);
    host.appendChild(el);
    // Keep at most 4 on screen; the oldest goes first.
    while (host.children.length > 4) host.removeChild(host.firstChild);
    if (typeof bootstrap !== 'undefined' && bootstrap.Toast) {
        const t = new bootstrap.Toast(el, { delay: st.delay, autohide: true });
        el.addEventListener('hidden.bs.toast', () => el.remove());
        t.show();
    } else {
        setTimeout(() => el.remove(), st.delay);
    }
}
window.notify = notify;

// ---------------------------------------------------------------------------
// Dialogs: one styled confirm and one result box, used everywhere instead of
// native confirm()/alert() so every question and outcome looks the same.
// ---------------------------------------------------------------------------
function _pfmDialogEl() {
    let el = document.getElementById('pfmDialog');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'pfmDialog';
    el.className = 'modal fade';
    el.tabIndex = -1;
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
        <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h6 class="modal-title d-flex align-items-center gap-2 mb-0" id="pfmDialogTitle"></h6>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body" id="pfmDialogBody"></div>
                <div class="modal-footer py-2" id="pfmDialogFooter"></div>
            </div>
        </div>`;
    document.body.appendChild(el);
    // May open on top of another modal (fund profile, research): lift it and
    // its own backdrop above the one underneath.
    el.style.zIndex = '1065';
    el.addEventListener('shown.bs.modal', () => {
        const drops = document.querySelectorAll('.modal-backdrop');
        if (drops.length > 1) drops[drops.length - 1].style.zIndex = '1062';
    });
    el.addEventListener('hidden.bs.modal', () => {
        // Bootstrap removes body.modal-open when any modal closes; restore it
        // if another modal is still open underneath so it keeps scrolling.
        if (document.querySelector('.modal.show')) document.body.classList.add('modal-open');
    });
    return el;
}

const _DIALOG_ICON = {
    danger: 'bi-exclamation-octagon-fill text-danger',
    warning: 'bi-exclamation-triangle-fill text-warning',
    success: 'bi-check-circle-fill text-success',
    info: 'bi-info-circle-fill text-info',
    question: 'bi-question-circle-fill text-primary',
};

// Styled replacement for window.confirm(). Resolves true on confirm, false on
// cancel / close / Esc. opts: string, or { title, message (plain text, keeps
// line breaks), detailHtml (trusted markup), confirmLabel, cancelLabel, danger }.
function confirmDialog(opts) {
    const o = typeof opts === 'string' ? { message: opts } : (opts || {});
    if (typeof bootstrap === 'undefined' || !bootstrap.Modal) {
        return Promise.resolve(window.confirm(o.message || o.title || 'Are you sure?'));
    }
    const el = _pfmDialogEl();
    const danger = !!o.danger;
    el.querySelector('#pfmDialogTitle').innerHTML =
        `<i class="bi ${_DIALOG_ICON[danger ? 'danger' : 'question']}"></i><span></span>`;
    el.querySelector('#pfmDialogTitle span').textContent = o.title || (danger ? 'Are you sure?' : 'Please confirm');
    const body = el.querySelector('#pfmDialogBody');
    body.innerHTML = '<p class="mb-0" style="white-space:pre-line;"></p>' + (o.detailHtml || '');
    body.querySelector('p').textContent = o.message || '';
    const footer = el.querySelector('#pfmDialogFooter');
    footer.innerHTML = `
        <button type="button" class="btn btn-sm btn-outline-secondary" data-act="cancel"></button>
        <button type="button" class="btn btn-sm ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok"></button>`;
    footer.querySelector('[data-act="cancel"]').textContent = o.cancelLabel || 'Cancel';
    footer.querySelector('[data-act="ok"]').textContent = o.confirmLabel || (danger ? 'Delete' : 'OK');
    return new Promise(resolve => {
        let result = false;
        const modal = bootstrap.Modal.getOrCreateInstance(el);
        footer.querySelector('[data-act="ok"]').onclick = () => { result = true; modal.hide(); };
        footer.querySelector('[data-act="cancel"]').onclick = () => { result = false; modal.hide(); };
        el.addEventListener('hidden.bs.modal', () => resolve(result), { once: true });
        el.addEventListener('shown.bs.modal', () => {
            // Focus the safe choice for destructive actions, the action otherwise.
            const f = footer.querySelector(danger ? '[data-act="cancel"]' : '[data-act="ok"]');
            if (f) f.focus();
        }, { once: true });
        modal.show();
    });
}
window.confirmDialog = confirmDialog;

// Result box: headline + stat chips + key facts + collapsible lists.
// model: { title, level, chips: [{label, value, cls}], facts: [{label, value}],
//          lists: [{title, items: [string], level, open}], note, actions: [{label, onClick}] }
function showResultDialog(model) {
    const m = model || {};
    if (typeof bootstrap === 'undefined' || !bootstrap.Modal) { notify(m.title || 'Done', m.level); return; }
    const el = _pfmDialogEl();
    el.querySelector('#pfmDialogTitle').innerHTML =
        `<i class="bi ${_DIALOG_ICON[m.level] || _DIALOG_ICON.info}"></i><span></span>`;
    el.querySelector('#pfmDialogTitle span').textContent = m.title || 'Done';
    const chips = (m.chips || []).map(c => `
        <div class="pfm-stat ${c.cls || ''}">
            <div class="pfm-stat-value">${esc(String(c.value))}</div>
            <div class="pfm-stat-label">${esc(c.label)}</div>
        </div>`).join('');
    const facts = (m.facts || []).map(f => `
        <div class="pfm-fact"><span class="text-muted">${esc(f.label)}</span><span class="text-end">${esc(String(f.value))}</span></div>`).join('');
    const lists = (m.lists || []).filter(l => l.items && l.items.length).map((l, i) => {
        const id = `pfmDialogList${i}`;
        const MAX = 50;
        const shown = l.items.slice(0, MAX).map(t => `<li>${esc(t)}</li>`).join('');
        const more = l.items.length > MAX ? `<li class="text-muted">… and ${l.items.length - MAX} more</li>` : '';
        const badge = l.level === 'danger' ? 'text-bg-danger' : l.level === 'warning' ? 'text-bg-warning' : 'text-bg-secondary';
        return `
            <div class="mt-2">
                <button class="btn btn-link btn-sm p-0 text-decoration-none d-flex align-items-center gap-2" type="button"
                        data-bs-toggle="collapse" data-bs-target="#${id}" aria-expanded="${l.open ? 'true' : 'false'}">
                    <i class="bi bi-chevron-right pfm-chev"></i><span>${esc(l.title)}</span>
                    <span class="badge ${badge}">${l.items.length}</span>
                </button>
                <div id="${id}" class="collapse${l.open ? ' show' : ''}">
                    <ul class="small mb-0 mt-1 ps-4 pfm-result-list">${shown}${more}</ul>
                </div>
            </div>`;
    }).join('');
    const body = el.querySelector('#pfmDialogBody');
    body.innerHTML = `
        ${chips ? `<div class="pfm-stats mb-3">${chips}</div>` : ''}
        ${facts ? `<div class="pfm-facts">${facts}</div>` : ''}
        ${lists}
        ${m.note ? `<div class="small text-muted mt-3"></div>` : ''}`;
    if (m.note) body.lastElementChild.textContent = m.note;
    const footer = el.querySelector('#pfmDialogFooter');
    footer.innerHTML = '';
    const modal = bootstrap.Modal.getOrCreateInstance(el);
    (m.actions || []).forEach(a => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-sm btn-outline-primary';
        b.textContent = a.label;
        b.onclick = () => { modal.hide(); if (a.onClick) a.onClick(); };
        footer.appendChild(b);
    });
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'btn btn-sm btn-primary';
    ok.textContent = 'Close';
    ok.onclick = () => modal.hide();
    footer.appendChild(ok);
    modal.show();
}
window.showResultDialog = showResultDialog;

function _fmtMoneyMap(map) {
    return Object.entries(map || {})
        .filter(([, v]) => v)
        .map(([ccy, v]) => {
            try {
                return Number(v).toLocaleString(Fmt.loc(), { style: 'currency', currency: ccy, maximumFractionDigits: 2 });
            } catch (e) {
                return `${Fmt.money(v, ccy, 2)}`;
            }
        }).join(' + ');
}

function _dateSpan(from, to) {
    if (!from) return null;
    return from === to ? Fmt.date(from) : `${Fmt.date(from)} → ${Fmt.date(to)}`;
}

const _TX_TYPE_LABEL = {
    buy: ['buy', 'buys'], sell: ['sell', 'sells'], dividend: ['dividend', 'dividends'],
    interest: ['interest payment', 'interest payments'], split: ['split', 'splits'],
};

// Pure: turn an /import/save response into the result-box model, so the five
// import flows (file, text/LLM, legacy modal x2, chat) all say the same thing.
function importResultModel(r) {
    const res = r || {};
    const errors = (res.errors || []).filter(e => !String(e).startsWith('DUPLICATE'));
    const dups = (res.errors || []).filter(e => String(e).startsWith('DUPLICATE'))
        .map(e => String(e).replace(/^DUPLICATE:\s*/, ''));
    const saved = res.saved || 0;
    const bookings = res.saved_bookings || 0;
    const deposits = res.saved_deposits || 0;
    const written = saved + bookings + deposits + (res.overwritten || 0);
    const level = errors.length ? (written ? 'warning' : 'danger') : (written ? 'success' : 'info');
    const title = errors.length
        ? (written ? 'Import finished with problems' : 'Nothing imported')
        : (written ? 'Import complete' : 'Nothing new to import');

    const chips = [{ label: saved === 1 ? 'transaction' : 'transactions', value: saved, cls: saved ? 'pfm-stat-good' : '' }];
    if (bookings) chips.push({ label: bookings === 1 ? 'cash movement' : 'cash movements', value: bookings, cls: 'pfm-stat-good' });
    if (deposits) chips.push({ label: deposits === 1 ? 'fixed deposit' : 'fixed deposits', value: deposits, cls: 'pfm-stat-good' });
    if (res.overwritten) chips.push({ label: 'overwritten', value: res.overwritten, cls: 'pfm-stat-info' });
    if (res.duplicates_skipped) chips.push({ label: 'duplicates skipped', value: res.duplicates_skipped, cls: 'pfm-stat-muted' });
    if (errors.length) chips.push({ label: errors.length === 1 ? 'error' : 'errors', value: errors.length, cls: 'pfm-stat-bad' });

    const facts = [];
    const types = Object.entries(res.by_type || {}).filter(([, n]) => n)
        .map(([t, n]) => `${n} ${(_TX_TYPE_LABEL[t] || [t, t])[n === 1 ? 0 : 1]}`);
    if (types.length) facts.push({ label: 'Breakdown', value: types.join(' · ') });
    const span = _dateSpan(res.date_from, res.date_to);
    if (span) facts.push({ label: 'Dates', value: span });
    if ((res.portfolios || []).length) facts.push({ label: res.portfolios.length === 1 ? 'Account' : 'Accounts', value: res.portfolios.join(', ') });
    const bt = res.booking_totals || {};
    if (bt.Deposit) facts.push({ label: 'Deposited', value: _fmtMoneyMap(bt.Deposit) });
    if (bt.Withdrawal) facts.push({ label: 'Withdrawn', value: _fmtMoneyMap(bt.Withdrawal) });

    const lists = [
        { title: 'Errors', items: errors, level: 'danger', open: true },
        { title: 'New assets created', items: res.new_assets || [], level: 'info', open: (res.new_assets || []).length <= 5 },
        { title: 'Asset types corrected', items: res.asset_types_corrected || [], level: 'info' },
        { title: 'Skipped as duplicates', items: dups, level: 'secondary' },
    ];
    let note = null;
    if ((res.new_assets || []).length) note = 'New assets get prices on the next price refresh. Check their type and ticker on the Assets page.';
    else if (!written && res.duplicates_skipped) note = 'Everything in this file was already imported.';
    return { title, level, chips, facts, lists, note };
}
window.importResultModel = importResultModel;

// Pure: same for a bank-statement (/spending/save) response.
function spendingImportResultModel(r) {
    const res = r || {};
    const errors = res.errors || [];
    const written = (res.saved || 0) + (res.overwritten || 0);
    const level = errors.length ? (written ? 'warning' : 'danger') : (written ? 'success' : 'info');
    const title = errors.length
        ? (written ? 'Import finished with problems' : 'Nothing imported')
        : (written ? 'Bank statement imported' : 'Nothing new to import');
    const chips = [{ label: res.saved === 1 ? 'row' : 'rows', value: res.saved || 0, cls: res.saved ? 'pfm-stat-good' : '' }];
    if (res.overwritten) chips.push({ label: 'overwritten', value: res.overwritten, cls: 'pfm-stat-info' });
    if (res.duplicates_skipped) chips.push({ label: 'duplicates skipped', value: res.duplicates_skipped, cls: 'pfm-stat-muted' });
    if (res.transfers_linked) chips.push({ label: 'transfers linked', value: res.transfers_linked, cls: 'pfm-stat-info' });
    if (res.uncategorized) chips.push({ label: 'to categorise', value: res.uncategorized, cls: 'pfm-stat-warn' });
    if (errors.length) chips.push({ label: errors.length === 1 ? 'error' : 'errors', value: errors.length, cls: 'pfm-stat-bad' });
    const facts = [];
    if (res.account_name) facts.push({ label: 'Account', value: res.account_name });
    const span = _dateSpan(res.date_from, res.date_to);
    if (span) facts.push({ label: 'Dates', value: span });
    const inTxt = _fmtMoneyMap(res.money_in), outTxt = _fmtMoneyMap(res.money_out);
    if (inTxt) facts.push({ label: 'Money in', value: inTxt });
    if (outTxt) facts.push({ label: 'Money out', value: outTxt });
    if (res.latest_balance != null) {
        facts.push({
            label: 'Balance',
            value: _fmtMoneyMap({ [res.latest_balance_currency || 'EUR']: res.latest_balance })
                + (res.latest_balance_date ? ` on ${Fmt.date(res.latest_balance_date)}` : ''),
        });
    }
    let note = null;
    if (res.uncategorized) note = `${res.uncategorized} row(s) matched no rule. Use "Select all uncategorized" → "Suggest categories (AI)" on the Spending page to file them.`;
    else if (!written && res.duplicates_skipped) note = 'Everything in this statement was already imported.';
    return { title, level, chips, facts, lists: [{ title: 'Errors', items: errors, level: 'danger', open: true }], note };
}
window.spendingImportResultModel = spendingImportResultModel;

// Show the import result once `modalEl` (the import modal being closed) has
// finished hiding — opening a second modal mid-animation leaves a stray backdrop.
function showImportResult(result, opts) {
    const o = opts || {};
    const model = (o.kind === 'spending' ? spendingImportResultModel : importResultModel)(result);
    if (o.actions) model.actions = o.actions;
    const open = () => showResultDialog(model);
    if (o.afterModal && o.afterModal.classList.contains('show')) {
        o.afterModal.addEventListener('hidden.bs.modal', open, { once: true });
    } else {
        open();
    }
}
window.showImportResult = showImportResult;

// Plain-text form of the same summary, for the chat thread (which reports an
// import inside the conversation instead of opening a dialog).
function importResultText(result) {
    const m = importResultModel(result);
    const lines = [`${m.title}: ` + m.chips.map(c => `${c.value} ${c.label}`).join(', ') + '.'];
    m.facts.forEach(f => lines.push(`${f.label}: ${f.value}`));
    m.lists.filter(l => l.items.length).forEach(l => {
        lines.push(`${l.title} (${l.items.length}):`);
        l.items.slice(0, 10).forEach(i => lines.push(`- ${i}`));
        if (l.items.length > 10) lines.push(`- … and ${l.items.length - 10} more`);
    });
    if (m.note) lines.push(m.note);
    return lines.join('\n');
}
window.importResultText = importResultText;

const _VIEW_TX_ACTION = { label: 'View transactions', onClick: () => window.navigationManager && window.navigationManager.showPage('transactions') };
// Kept for existing callers; type now actually selects the style.
window.showToast = function(msg, type) { notify(msg, type === 'error' ? 'danger' : type); };

// Navigate to the chat page with a pre-loaded context (thread name + opening message).
// Called from Research workbench and Portfolio Health panel.
// pfm_features.js loads after pfm_core.js in the same global scope so no window. prefix needed.
function openChatWithContext(threadName, openingMessage) {
    window._chatPendingContext = { threadName, openingMessage };
    window.navigationManager.showPage('chat');
}

// ---------------------------------------------------------------------------
