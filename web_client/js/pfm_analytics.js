// pfm_analytics.js — part of the portfolio_debug.js split.
// Analytics: net-worth + dividend + analytics-tab + diversification charts and loaders.
// Classic script (no build step): these files share one global scope
// and MUST load in this order: pfm_core, pfm_pages, pfm_analytics,
// pfm_features. See index.html.

// Net Worth page — brokerage value (auto) + manual assets/liabilities.
const NW_CATEGORY_LABELS = {
    savings_account: 'Savings account', current_account: 'Current account',
    cash: 'Cash', property: 'Property', vehicle: 'Vehicle', pension: 'Pension',
    investment_external: 'Investment (external)', other: 'Other asset',
    mortgage: 'Mortgage', personal_loan: 'Personal loan', car_loan: 'Car loan',
    credit_card: 'Credit card', other_debt: 'Other debt',
    // legacy values from earlier entries:
    loan: 'Loan', credit: 'Credit / debt',
};
// Type values that count as liabilities (debt). Used to derive is_liability
// from the chosen type (the separate checkbox was removed).
const NW_LIABILITY_CATS = new Set(['mortgage', 'personal_loan', 'car_loan', 'credit_card', 'other_debt', 'loan', 'credit']);
async function loadNetworthPage() {
    const $ = id => document.getElementById(id);
    _wireNetworthForm();
    _wireDepositForm();
    const body = $('nwItemsBody');
    if (body) body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</td></tr>';
    try {
        const d = await window.apiClient.getNetworth();
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        $('nwBrokerage').innerHTML = eur(d.brokerage_eur);
        $('nwAssets').innerHTML = eur(d.manual_assets_eur);
        if ($('nwDeposits')) $('nwDeposits').innerHTML = eur(d.deposits_eur || 0);
        $('nwLiabilities').innerHTML = eur(d.manual_liabilities_eur);
        $('nwTotal').innerHTML = eur(d.net_worth_eur);
        const card = $('nwTotalCard');
        if (card) card.style.background = d.net_worth_eur >= 0 ? '#0d6efd' : '#dc3545';
        const items = (d.items || []).slice()
            .sort((a, b) => (a.is_liability ? 1 : 0) - (b.is_liability ? 1 : 0));
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No off-brokerage items yet. Add cash, property, a mortgage… on the left.</td></tr>';
        } else {
            body.innerHTML = items.map(it => `
                <tr>
                    <td class="ps-3"><strong>${escapeForAttr(it.name)}</strong>${it.notes ? `<br><small class="text-muted">${escapeForAttr(it.notes)}</small>` : ''}</td>
                    <td><span class="badge ${it.is_liability ? 'bg-danger' : 'bg-secondary'}">${NW_CATEGORY_LABELS[it.category] || it.category}</span></td>
                    <td class="text-end">${Fmt.num(it.amount, 2, 2)} ${it.currency || ''}</td>
                    <td class="text-end ${it.is_liability ? 'text-danger' : ''}">${it.is_liability ? '−' : ''}${Fmt.amt('€' + Fmt.num(it.amount_eur, 0, 0))}</td>
                    <td class="pe-3 text-end"><button class="btn btn-sm btn-outline-danger" onclick="confirmDeleteManualAsset(${it.id})"><i class="bi bi-trash"></i></button></td>
                </tr>`).join('');
        }
        _renderDeposits(d.deposits || []);
    } catch (err) {
        if (body) body.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-3">${err.message}</td></tr>`;
    }
}
window.loadNetworthPage = loadNetworthPage;

function escapeForAttr(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _wireNetworthForm() {
    const form = document.getElementById('nwAddForm');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        const $ = id => document.getElementById(id);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = $('nwAddStatus');
            const category = $('nwCategory').value;
            const payload = {
                name: $('nwName').value.trim(),
                category: category,
                amount: parseFloat($('nwAmount').value) || 0,
                currency: ($('nwCurrency').value || 'EUR').toUpperCase(),
                // The chosen type determines whether it's a debt (no checkbox).
                is_liability: NW_LIABILITY_CATS.has(category),
                notes: $('nwNotes').value.trim() || null,
            };
            if (!payload.name) return;
            status.className = 'small text-muted'; status.textContent = 'Adding…';
            try {
                await window.apiClient.createManualAsset(payload);
                form.reset(); $('nwCurrency').value = 'EUR';
                status.textContent = '';
                loadNetworthPage();
            } catch (err) { status.className = 'small text-danger'; status.textContent = err.message; }
        });
    }
    const refresh = document.getElementById('refreshNetworth');
    if (refresh && !refresh.dataset.wired) {
        refresh.dataset.wired = '1';
        refresh.addEventListener('click', () => loadNetworthPage());
    }
}

window.confirmDeleteManualAsset = async function (id) {
    if (!confirm('Delete this item?')) return;
    try { await window.apiClient.deleteManualAsset(id); loadNetworthPage(); }
    catch (err) { alert('Error: ' + err.message); }
};

function _renderDeposits(deposits) {
    const tbody = document.getElementById('nwDepositsBody');
    if (!tbody) return;
    if (!deposits.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">No fixed deposits yet.</td></tr>';
        return;
    }
    tbody.innerHTML = deposits.map(d => {
        const statusBadge = d.status === 'active'
            ? '<span class="badge bg-success">Active</span>'
            : '<span class="badge bg-secondary">' + d.status.charAt(0).toUpperCase() + d.status.slice(1) + '</span>';
        const matureBtn = d.status === 'active'
            ? `<button class="btn btn-sm btn-outline-success me-1" onclick="openMatureDepositModal(${d.id}, ${d.projected_interest}, '${d.maturity_date}')"><i class="bi bi-check-circle"></i></button>`
            : '';
        return `<tr>
            <td class="ps-3"><strong>${escapeForAttr(d.name)}</strong>${d.notes ? `<br><small class="text-muted">${escapeForAttr(d.notes)}</small>` : ''}</td>
            <td>${d.portfolio_id ? escapeForAttr(String(d.portfolio_id)) : '<span class="text-muted">—</span>'}</td>
            <td class="text-end">${Fmt.num(d.principal, 2, 2)} ${d.currency}</td>
            <td class="text-end">${Fmt.num(d.interest_rate, 2, 2)}%</td>
            <td>${d.maturity_date}</td>
            <td class="text-end">${Fmt.num(d.projected_interest, 2, 2)} ${d.currency}</td>
            <td>${statusBadge}</td>
            <td class="pe-3 text-end">${matureBtn}<button class="btn btn-sm btn-outline-danger" onclick="confirmDeleteDeposit(${d.id})"><i class="bi bi-trash"></i></button></td>
        </tr>`;
    }).join('');
}

function _wireDepositForm() {
    const form = document.getElementById('nwDepositForm');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('depAddStatus');
            const payload = {
                name: document.getElementById('depName').value.trim(),
                principal: parseFloat(document.getElementById('depPrincipal').value) || 0,
                currency: (document.getElementById('depCurrency').value || 'EUR').toUpperCase(),
                interest_rate: parseFloat(document.getElementById('depRate').value) || 0,
                start_date: document.getElementById('depStart').value,
                maturity_date: document.getElementById('depMaturity').value,
            };
            if (!payload.name || !payload.start_date || !payload.maturity_date) return;
            status.className = 'small text-muted'; status.textContent = 'Adding…';
            try {
                await window.apiClient.createDeposit(payload);
                form.reset();
                document.getElementById('depCurrency').value = 'EUR';
                status.textContent = '';
                loadNetworthPage();
            } catch (err) { status.className = 'small text-danger'; status.textContent = err.message; }
        });
    }

    const extractBtn = document.getElementById('depExtractBtn');
    if (extractBtn && !extractBtn.dataset.wired) {
        extractBtn.dataset.wired = '1';
        extractBtn.addEventListener('click', async () => {
            const text = document.getElementById('depLlmText').value.trim();
            const statusEl = document.getElementById('depExtractStatus');
            const preview = document.getElementById('depExtractPreview');
            if (!text) return;
            statusEl.textContent = 'Extracting…';
            preview.innerHTML = '';
            try {
                const result = await window.apiClient.extractDepositsLLM(text);
                statusEl.textContent = '';
                if (!result.deposits.length) {
                    preview.innerHTML = '<p class="small text-muted">No deposits found in the text.</p>';
                    return;
                }
                preview.innerHTML = `
                    <table class="table table-sm table-bordered mt-2 mb-2">
                        <thead><tr><th>Name</th><th>Principal</th><th>Rate</th><th>Start</th><th>Maturity</th></tr></thead>
                        <tbody>${result.deposits.map(dep => `
                            <tr>
                                <td>${escapeForAttr(dep.name)}</td>
                                <td>${Fmt.num(dep.principal, 2, 2)} ${dep.currency}</td>
                                <td>${dep.interest_rate}%</td>
                                <td>${escapeForAttr(dep.start_date)}</td>
                                <td>${escapeForAttr(dep.maturity_date)}</td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
                    <button class="btn btn-sm btn-primary" id="depSaveExtracted">
                        <i class="bi bi-cloud-upload me-1"></i>Save all (${result.deposits.length})
                    </button>
                    <span class="small text-muted ms-2" id="depSaveStatus"></span>`;
                document.getElementById('depSaveExtracted').addEventListener('click', async () => {
                    const saveStatus = document.getElementById('depSaveStatus');
                    saveStatus.textContent = 'Saving…';
                    try {
                        for (const dep of result.deposits) {
                            await window.apiClient.createDeposit(dep);
                        }
                        preview.innerHTML = '';
                        document.getElementById('depLlmText').value = '';
                        statusEl.textContent = `${result.deposits.length} deposit(s) saved.`;
                        loadNetworthPage();
                    } catch (err) { saveStatus.textContent = 'Error: ' + err.message; }
                });
            } catch (err) { statusEl.textContent = 'Error: ' + err.message; }
        });
    }
}

window.confirmDeleteDeposit = async function (id) {
    if (!confirm('Delete this deposit?')) return;
    try { await window.apiClient.deleteDeposit(id); loadNetworthPage(); }
    catch (err) { alert('Error: ' + err.message); }
};

window.openMatureDepositModal = function (id, projectedInterest, maturityDate) {
    document.getElementById('matureDepositId').value = id;
    document.getElementById('matureInterestPaid').value = projectedInterest;
    document.getElementById('maturePayoutDate').value = maturityDate;
    document.getElementById('matureDepositError').textContent = '';

    const confirmBtn = document.getElementById('matureDepositConfirm');
    const newBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);
    newBtn.addEventListener('click', async () => {
        const depId = parseInt(document.getElementById('matureDepositId').value);
        const interest = parseFloat(document.getElementById('matureInterestPaid').value);
        const dt = document.getElementById('maturePayoutDate').value;
        const errEl = document.getElementById('matureDepositError');
        if (!dt || isNaN(interest)) { errEl.textContent = 'Please fill in all fields.'; return; }
        newBtn.disabled = true;
        try {
            await window.apiClient.matureDeposit(depId, { interest_paid: interest, date: dt });
            bootstrap.Modal.getInstance(document.getElementById('depositMatureModal')).hide();
            window.showToast(`Interest of ${Fmt.num(interest, 2, 2)} recorded.`, 'success');
            loadNetworthPage();
        } catch (err) { errEl.textContent = err.message; newBtn.disabled = false; }
    });

    new bootstrap.Modal(document.getElementById('depositMatureModal')).show();
};

// (Re)initialise Bootstrap tooltips on all [data-bs-toggle="tooltip"] elements.
// Disposes any existing instance first so re-rendered tiles don't leak handlers.
function initTooltips() {
    if (!window.bootstrap || !bootstrap.Tooltip) return;
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        const existing = bootstrap.Tooltip.getInstance(el);
        if (existing) existing.dispose();
        new bootstrap.Tooltip(el);
    });
}
window.initTooltips = initTooltips;

// Fetch performance for a period and update just the dashboard Return KPI
async function loadDashboardReturn(period) {
    const el = document.getElementById('dashReturnPct');
    if (!el) return;
    el.textContent = '…';
    try {
        const d = await window.apiClient.getPerformance(null, period || 'all');
        // 'All' = lifetime return (cost-basis based). Named periods use the
        // snapshot-based period return, which needs accumulated daily history.
        const pct = (period && period !== 'all')
            ? d.period_return_pct
            : d.total_return_pct;
        if (pct == null) {
            el.textContent = '—';
            el.title = (period && period !== 'all')
                ? 'Not enough daily snapshot history for this period yet'
                : 'No data';
            return;
        }
        const n = parseFloat(pct);
        el.textContent = (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
        el.title = (period && period !== 'all')
            ? 'Change over the selected period (from daily snapshots)'
            : 'Lifetime return vs cost basis';
    } catch (err) {
        el.textContent = '—';
        el.title = err.message;
    }
}

// ---------------------------------------------------------------------------
// Analytics page
// ---------------------------------------------------------------------------

// Compact euro formatter, no decimals (matches dashboard / forecast style)
function anFmtEur(val) {
    return Fmt.amt(Fmt.num(val, 0, 0) + ' €');
}

// Euro formatter with 2 decimals, used for dividend / tax detail figures
function anFmtEur2(val) {
    return Fmt.amt(Fmt.num(val, 2, 2) + ' €');
}

function anFmtPct(val) {
    const n = parseFloat(val || 0);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

// a) Performance section
// Map a period code to a human-readable label for the period-return tile
function anPeriodLabel(period) {
    return ({ all: 'All-Time', ytd: 'YTD', '1y': '1Y', '1m': '1M' }[period] || 'All-Time');
}

async function loadAnalyticsPerformance() {
    const body = document.getElementById('anPerformanceBody');
    const select = document.getElementById('anBenchmark');
    if (!body) return;
    const benchmark = select ? select.value : '^GSPC';
    const periodEl = document.querySelector('input[name="anPeriod"]:checked');
    const period = periodEl ? periodEl.value : 'all';
    body.innerHTML = `
        <div class="text-center text-muted py-4">
            <div class="spinner-border spinner-border-sm me-2" role="status"></div>
            Loading performance (fetching benchmark, may take a few seconds)…
        </div>`;
    try {
        const d = await window.apiClient.getPerformance(benchmark, period);
        const totalPct = parseFloat(d.total_return_pct || 0);
        const totalCls = totalPct >= 0 ? 'text-success' : 'text-danger';
        const irrPct = parseFloat(d.money_weighted_irr_pct || 0);
        const irrCls = irrPct >= 0 ? 'text-success' : 'text-danger';
        // Period-scoped return (null when history is too short)
        const periodPct = d.period_return_pct;
        const periodHas = periodPct != null;
        const periodVal = parseFloat(periodPct || 0);
        const periodCls = periodHas ? (periodVal >= 0 ? 'text-success' : 'text-danger') : 'text-muted';
        const periodTxt = periodHas ? anFmtPct(periodVal) : '—';
        const benchReturn = parseFloat(d.benchmark_return_pct || 0);
        // Compare the period return (when available) to the period-scoped benchmark
        const myReturn = periodHas ? periodVal : totalPct;
        const beat = myReturn - benchReturn;
        const beatCls = beat >= 0 ? 'text-success' : 'text-danger';
        const beatWord = beat >= 0 ? 'ahead of' : 'behind';
        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.invested}">Invested</div>
                        <div class="fs-5 fw-bold">${anFmtEur(d.invested_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.netWorth}">Current Value</div>
                        <div class="fs-5 fw-bold">${anFmtEur(d.current_value_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.totalReturn}">Total Return</div>
                        <div class="fs-5 fw-bold ${totalCls}">${anFmtPct(totalPct)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.irr}">Money-Weighted IRR</div>
                        <div class="fs-5 fw-bold ${irrCls}">${anFmtPct(irrPct)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border border-primary rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.periodReturn}">Return (${anPeriodLabel(period)})</div>
                        <div class="fs-5 fw-bold ${periodCls}" ${periodHas ? '' : 'title="Not enough history for this period"'}>${periodTxt}</div>
                    </div>
                </div>
            </div>
            <div class="small">
                <i class="bi bi-flag me-1"></i>
                vs <strong data-bs-toggle="tooltip" title="${METRIC_HELP.benchmark}">${d.benchmark || benchmark}</strong> (${anFmtPct(benchReturn)}, ${anPeriodLabel(period)}):
                <span class="${beatCls} fw-semibold">${anFmtPct(beat)} ${beatWord} benchmark</span>
            </div>`;
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading performance: ${err.message}</div>`;
    }
    initTooltips();
}

// b) Net worth over time section (SVG line chart)
// Wire the "Backfill history" button once (reconstruct daily snapshots).
function _wireBackfillButton() {
    const btn = document.getElementById('anBackfillBtn');
    if (!btn || btn.dataset.wired) return;
    btn.dataset.wired = '1';
    btn.addEventListener('click', async () => {
        if (!confirm('Reconstruct daily net-worth history from your transactions and historical prices? This can take a minute and fills dates that are missing.')) return;
        const orig = btn.innerHTML;
        btn.disabled = true;
        try {
            await window.apiClient.startBackfill(false);
            for (let i = 0; i < 60; i++) {
                await new Promise(r => setTimeout(r, 3000));
                const s = await window.apiClient.getBackfillStatus();
                btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${s.running ? (s.total ? `prices ${s.done}/${s.total}` : '…') + (s.added ? ` · ${s.added} days` : '') : 'finishing'}`;
                if (!s.running) {
                    if (s.error) alert('Backfill error: ' + s.error);
                    break;
                }
            }
            loadAnalyticsNetworth();
        } catch (e) {
            alert('Backfill failed: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = orig;
        }
    });
}

// Bumped on every call so overlapping invocations (e.g. tab-load + a resize)
// don't fight: only the latest one is allowed to touch the DOM after its await,
// which is what kept the "Loading…" spinner up over an already-rendered chart.
let _networthSeq = 0;
async function loadAnalyticsNetworth() {
    _wireBackfillButton();
    const container = document.getElementById('anNetworthContainer');
    const placeholder = document.getElementById('anNetworthPlaceholder');
    const svg = document.getElementById('anNetworthSvg');
    if (!container || !svg) return;
    const seq = ++_networthSeq;
    svg.style.display = 'none';
    placeholder.style.display = 'flex';
    placeholder.innerHTML = '<div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…';
    try {
        const d = await window.apiClient.getNetworthHistory();
        if (seq !== _networthSeq) return; // superseded by a newer call
        const snaps = (d.snapshots || []).slice().sort(
            (a, b) => new Date(a.snapshot_date) - new Date(b.snapshot_date)
        );
        if (snaps.length < 2) {
            svg.style.display = 'none';
            placeholder.style.display = 'flex';
            placeholder.innerHTML = '<span class="text-muted">Not enough history yet — snapshots are recorded daily.</span>';
            return;
        }
        renderNetworthChart(snaps);
        placeholder.style.display = 'none';
        svg.style.display = '';
    } catch (err) {
        if (seq !== _networthSeq) return;
        svg.style.display = 'none';
        placeholder.style.display = 'flex';
        placeholder.innerHTML = `<span class="text-danger small">Error loading net worth history: ${err.message}</span>`;
    }
}

function renderNetworthChart(snaps) {
    const container = document.getElementById('anNetworthContainer');
    const placeholder = document.getElementById('anNetworthPlaceholder');
    const svg = document.getElementById('anNetworthSvg');
    const W = container.clientWidth || 600;
    const H = 300;
    const PAD = { top: 20, right: 20, bottom: 40, left: 72 };
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;

    const n = snaps.length;
    const vals = snaps.flatMap(s => [
        parseFloat(s.total_value_eur || 0),
        parseFloat(s.total_cost_eur || 0)
    ]);
    const maxVal = Math.max(...vals, 0);
    const minVal = Math.min(...vals, 0);
    const range = maxVal - minVal || 1;

    function xScale(i) {
        return PAD.left + (n === 1 ? 0 : (i / (n - 1)) * innerW);
    }
    function yScale(v) {
        return PAD.top + innerH - ((v - minVal) / range) * innerH;
    }
    function yTickFmt(v) {
        if (Math.abs(v) >= 1000000) return '€' + (v / 1000000).toFixed(1) + 'M';
        if (Math.abs(v) >= 1000)    return '€' + (v / 1000).toFixed(0) + 'k';
        return '€' + v.toFixed(0);
    }
    function pathD(key) {
        return snaps.map((s, i) =>
            (i === 0 ? 'M' : 'L') + xScale(i).toFixed(1) + ',' + yScale(parseFloat(s[key] || 0)).toFixed(1)
        ).join(' ');
    }

    // Y-axis ticks
    const yTicks = [];
    for (let i = 0; i <= 4; i++) {
        const v = minVal + range * (i / 4);
        yTicks.push({ v, y: yScale(v) });
    }

    // X-axis ticks: ~5 evenly spaced date labels
    const xTicks = [];
    const step = Math.max(1, Math.floor((n - 1) / 4));
    for (let i = 0; i < n; i += step) {
        xTicks.push({ i, x: xScale(i), label: shortDate(snaps[i].snapshot_date) });
    }
    if (xTicks[xTicks.length - 1].i !== n - 1) {
        xTicks.push({ i: n - 1, x: xScale(n - 1), label: shortDate(snaps[n - 1].snapshot_date) });
    }

    function shortDate(dStr) {
        const dt = new Date(dStr);
        if (isNaN(dt)) return String(dStr);
        // Include the year (history can span multiple years) e.g. "Jun '25".
        return dt.toLocaleDateString(Fmt.loc(), { month: 'short', year: '2-digit' });
    }

    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    svg.style.display = 'block';

    svg.innerHTML = `
        <defs>
            <linearGradient id="anValGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#2563eb" stop-opacity="0.15"/>
                <stop offset="100%" stop-color="#2563eb" stop-opacity="0.0"/>
            </linearGradient>
        </defs>

        <!-- Grid lines -->
        ${yTicks.map(t => `
            <line x1="${PAD.left}" y1="${t.y.toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${t.y.toFixed(1)}"
                  stroke="#e2e8f0" stroke-width="1"/>
        `).join('')}

        <!-- Under value-line fill -->
        <path d="${pathD('total_value_eur')} L${xScale(n - 1).toFixed(1)},${(PAD.top + innerH).toFixed(1)} L${xScale(0).toFixed(1)},${(PAD.top + innerH).toFixed(1)} Z"
              fill="url(#anValGrad)"/>

        <!-- Invested (cost) dashed grey line -->
        <path d="${pathD('total_cost_eur')}" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4 3"/>

        <!-- Total value solid blue line -->
        <path d="${pathD('total_value_eur')}" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>

        <!-- Y-axis labels -->
        ${yTicks.map(t => `
            <text x="${(PAD.left - 6).toFixed(1)}" y="${(t.y + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="#64748b">${yTickFmt(t.v)}</text>
        `).join('')}

        <!-- X-axis labels -->
        ${xTicks.map(t => `
            <text x="${t.x.toFixed(1)}" y="${(PAD.top + innerH + 16).toFixed(1)}" text-anchor="middle" font-size="11" fill="#64748b">${t.label}</text>
        `).join('')}

        <!-- Axis lines -->
        <line x1="${PAD.left}" y1="${PAD.top}" x2="${PAD.left}" y2="${(PAD.top + innerH).toFixed(1)}" stroke="#cbd5e1" stroke-width="1"/>
        <line x1="${PAD.left}" y1="${(PAD.top + innerH).toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${(PAD.top + innerH).toFixed(1)}" stroke="#cbd5e1" stroke-width="1"/>

        <!-- Endpoint dot on value line -->
        <circle cx="${xScale(n - 1).toFixed(1)}" cy="${yScale(parseFloat(snaps[n - 1].total_value_eur || 0)).toFixed(1)}" r="5"
                fill="#2563eb" stroke="white" stroke-width="2"/>
    `;

    placeholder.style.display = 'none';
}

// c) Dividend income section (SVG bar chart + KPIs + top payers table)
async function loadAnalyticsDividends() {
    const body = document.getElementById('anDividendsBody');
    if (!body) return;
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…</div>';
    try {
        const d = await window.apiClient.getDividends();
        const byMonth = d.by_month || {};
        // Last 12 months sorted ascending by month key (e.g. "2026-05")
        const months = Object.keys(byMonth).sort().slice(-12);
        const barChart = renderDividendBars(months, byMonth);

        const yieldByline = d.yield_on_cost || {};
        const bySymbol = d.by_symbol || {};
        const names = d.names || {};
        // Top payers by total received, descending
        const topPayers = Object.keys(bySymbol)
            .map(sym => ({ sym, name: names[sym] || sym, total: parseFloat(bySymbol[sym] || 0), yoc: yieldByline[sym] }))
            .sort((a, b) => b.total - a.total)
            .slice(0, 8);

        const payersRows = topPayers.length
            ? topPayers.map(p => `
                <tr>
                    <td><strong>${p.sym}</strong></td>
                    <td class="text-truncate small text-muted" style="max-width:180px;" title="${esc(p.name)}">${p.name !== p.sym ? esc(p.name) : ''}</td>
                    <td class="text-end">${anFmtEur2(p.total)}</td>
                    <td class="text-end">${p.yoc != null ? parseFloat(p.yoc).toFixed(2) + '%' : '—'}</td>
                </tr>`).join('')
            : '<tr><td colspan="4" class="text-center text-muted small">No dividend payers yet.</td></tr>';

        // Forward income: each holding's trailing-12-month dividends as a
        // forward estimate, + a 12-month calendar from the historical
        // month-of-year pattern scaled to the projected annual total.
        const ttmBySym = d.ttm_by_symbol || {};
        const fwd = Object.entries(ttmBySym).map(([s, v]) => ({ s, name: names[s] || s, v: parseFloat(v) || 0 }))
            .filter(x => x.v > 0).sort((a, b) => b.v - a.v);
        const fwdTotal = fwd.reduce((acc, x) => acc + x.v, 0);
        const fwdRows = fwd.length ? fwd.map(x => `
            <tr>
                <td><strong>${x.s}</strong></td>
                <td class="text-truncate small text-muted" style="max-width:200px;" title="${esc(x.name)}">${x.name !== x.s ? esc(x.name) : ''}</td>
                <td class="text-end">${anFmtEur2(x.v)}</td>
                <td class="text-end text-muted">${fwdTotal > 0 ? (x.v / fwdTotal * 100).toFixed(1) + '%' : '—'}</td>
            </tr>`).join('') : '<tr><td colspan="4" class="text-center text-muted small">No recurring dividends in the last 12 months.</td></tr>';
        // Month-of-year pattern (calendar) from history
        const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const moy = new Array(12).fill(0);
        Object.entries(byMonth).forEach(([k, v]) => {
            const m = parseInt(String(k).split('-')[1], 10) - 1;
            if (m >= 0 && m < 12) moy[m] += parseFloat(v) || 0;
        });
        const moyMax = Math.max(...moy, 1);
        const calBars = MONTHS.map((m, i) => `
            <div class="text-center" style="flex:1 1 0;">
                <div class="d-flex align-items-end justify-content-center" style="height:60px;">
                    <div title="${m}: ${anFmtEur2(moy[i])} received historically" style="width:60%;background:#16a34a;border-radius:3px 3px 0 0;height:${(moy[i] / moyMax * 100).toFixed(0)}%;min-height:2px;"></div>
                </div>
                <div class="small text-muted">${m}</div>
            </div>`).join('');
        const forwardSection = `
            <hr class="my-3">
            <div class="row g-4">
                <div class="col-12 col-lg-6">
                    <h6 class="fw-semibold small text-muted text-uppercase mb-2">Projected forward annual income <i class="bi bi-info-circle text-muted" style="cursor:help;" data-bs-toggle="tooltip" title="Each holding's trailing-12-month dividends, used as a forward estimate. Total ≈ ${anFmtEur2(fwdTotal)}/yr."></i></h6>
                    <div class="table-responsive" style="max-height:300px;overflow:auto;">
                        <table class="table table-sm table-hover mb-0">
                            <thead><tr><th>Symbol</th><th>Name</th><th class="text-end">€/yr</th><th class="text-end">Share</th></tr></thead>
                            <tbody>${fwdRows}</tbody>
                        </table>
                    </div>
                </div>
                <div class="col-12 col-lg-6">
                    <h6 class="fw-semibold small text-muted text-uppercase mb-2">Income by calendar month <span class="text-muted">(historical pattern)</span></h6>
                    <div class="d-flex align-items-end gap-1" style="min-height:80px;">${calBars}</div>
                </div>
            </div>`;

        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-12 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Total Received</div>
                        <div class="fs-5 fw-bold text-success">${anFmtEur2(d.total)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Trailing 12 Months</div>
                        <div class="fs-5 fw-bold">${anFmtEur2(d.ttm)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Projected Annual</div>
                        <div class="fs-5 fw-bold">${anFmtEur2(d.projected_annual)}</div>
                    </div>
                </div>
            </div>
            <div class="row g-4">
                <div class="col-12 col-lg-7">
                    <h6 class="fw-semibold small text-muted text-uppercase mb-2">Monthly Income (last 12 months)</h6>
                    ${barChart}
                </div>
                <div class="col-12 col-lg-5">
                    <h6 class="fw-semibold small text-muted text-uppercase mb-2">Top Payers</h6>
                    <div class="table-responsive">
                        <table class="table table-sm table-hover mb-0">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Name</th>
                                    <th class="text-end">Received</th>
                                    <th class="text-end">Yield on Cost <i class="bi bi-info-circle text-muted" style="cursor:help;" data-bs-toggle="tooltip" title="${METRIC_HELP.yieldOnCost}"></i></th>
                                </tr>
                            </thead>
                            <tbody>${payersRows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
            ${forwardSection}`;
        initTooltips();
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading dividends: ${err.message}</div>`;
    }
}

function renderDividendBars(months, byMonth) {
    if (!months.length) {
        return '<p class="text-muted small mb-0">No dividend income recorded yet.</p>';
    }
    const W = 480;
    const H = 220;
    const PAD = { top: 16, right: 12, bottom: 36, left: 56 };
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const vals = months.map(m => parseFloat(byMonth[m] || 0));
    const maxVal = Math.max(...vals, 1);
    const gap = 6;
    const barW = (innerW / months.length) - gap;

    function yScale(v) {
        return PAD.top + innerH - (v / maxVal) * innerH;
    }
    function yTickFmt(v) {
        if (Math.abs(v) >= 1000) return '€' + (v / 1000).toFixed(1) + 'k';
        return '€' + v.toFixed(0);
    }

    const yTicks = [];
    for (let i = 0; i <= 4; i++) {
        const v = maxVal * (i / 4);
        yTicks.push({ v, y: yScale(v) });
    }

    function shortMonth(mKey) {
        // mKey like "2026-05"
        const parts = String(mKey).split('-');
        const dt = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
        if (isNaN(dt)) return mKey;
        return dt.toLocaleDateString(undefined, { month: 'short' }) + (parts[1] === '01' ? " '" + parts[0].slice(2) : '');
    }

    const bars = months.map((m, i) => {
        const v = parseFloat(byMonth[m] || 0);
        const x = PAD.left + i * (barW + gap) + gap / 2;
        const y = yScale(v);
        const h = (PAD.top + innerH) - y;
        return `
            <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(0, h).toFixed(1)}"
                  fill="#22c55e" rx="2">
                <title>${m}: ${anFmtEur2(v)}</title>
            </rect>
            <text x="${(x + barW / 2).toFixed(1)}" y="${(PAD.top + innerH + 14).toFixed(1)}" text-anchor="middle" font-size="9" fill="#64748b">${shortMonth(m)}</text>
        `;
    }).join('');

    return `
        <svg viewBox="0 0 ${W} ${H}" width="100%" style="overflow:visible;">
            ${yTicks.map(t => `
                <line x1="${PAD.left}" y1="${t.y.toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${t.y.toFixed(1)}" stroke="#e2e8f0" stroke-width="1"/>
                <text x="${(PAD.left - 6).toFixed(1)}" y="${(t.y + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#64748b">${yTickFmt(t.v)}</text>
            `).join('')}
            ${bars}
            <line x1="${PAD.left}" y1="${(PAD.top + innerH).toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${(PAD.top + innerH).toFixed(1)}" stroke="#cbd5e1" stroke-width="1"/>
        </svg>`;
}

// d) Tax estimate section
async function loadAnalyticsTax() {
    const body = document.getElementById('anTaxBody');
    const yearSelect = document.getElementById('anTaxYear');
    if (!body) return;
    // Populate year select once (current year and 2 prior)
    if (yearSelect && !yearSelect.options.length) {
        const cur = new Date().getFullYear();
        for (let y = cur; y >= cur - 2; y--) {
            const opt = document.createElement('option');
            opt.value = String(y);
            opt.textContent = String(y);
            yearSelect.appendChild(opt);
        }
    }
    const year = yearSelect ? yearSelect.value : new Date().getFullYear();
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…</div>';
    try {
        const d = await window.apiClient.getTaxEstimate(year);
        const unreal = parseFloat(d.unrealised_gain_eur || 0);
        const unrealCls = unreal >= 0 ? 'text-success' : 'text-danger';
        const candidates = d.harvest_candidates || [];
        const candRows = candidates.length
            ? candidates.map(c => `
                <tr>
                    <td><strong>${esc(c.symbol)}</strong></td>
                    <td class="text-truncate small text-muted" style="max-width:220px;" title="${esc(c.name || '')}">${esc(c.name || '')}</td>
                    <td class="text-end">${c.quantity != null ? Fmt.num(c.quantity, 0, 4) : '—'}</td>
                    <td class="text-end text-danger">${anFmtEur2(c.unrealised_loss_eur)}</td>
                </tr>`).join('')
            : '<tr><td colspan="4" class="text-center text-muted small">No positions currently at a loss.</td></tr>';
        // Realised gains/losses per symbol (sorted most-negative first by the API)
        const realised = d.realised_by_symbol || [];
        const realisedRows = realised.length
            ? realised.map(r => {
                const v = parseFloat(r.realised_eur || 0);
                return `
                <tr>
                    <td><strong>${esc(r.symbol)}</strong></td>
                    <td class="text-truncate small text-muted" style="max-width:220px;" title="${esc(r.name || '')}">${esc(r.name || '')}</td>
                    <td class="text-end ${v >= 0 ? 'text-success' : 'text-danger'}">${anFmtEur2(v)}</td>
                </tr>`;
            }).join('')
            : `<tr><td colspan="3" class="text-center text-muted small">No sales realised in ${year}.</td></tr>`;
        const harvestTooltip = 'These positions are currently at a loss. Selling them realises the loss, which can offset realised gains and reduce your savings-base tax.';
        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="Net realised capital gains and losses on shares sold in ${year}, computed FIFO (first-in, first-out) across all brokers. Negative when losses outweigh gains.">Realised Gains ${year}</div>
                        <div class="fs-6 fw-bold">${anFmtEur2(d.realised_gain_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="Cash dividends received in ${year}.">Dividend Income ${year}</div>
                        <div class="fs-6 fw-bold">${anFmtEur2(d.dividend_income_eur)}</div>
                    </div>
                </div>
                ${(d.interest_income_eur || 0) ? `<div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="P2P / savings interest received in ${year} (e.g. Mintos). Taxed in the savings base like dividends.">Interest Income ${year}</div>
                        <div class="fs-6 fw-bold">${anFmtEur2(d.interest_income_eur)}</div>
                    </div>
                </div>` : ''}
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.savingsBase}">Savings Base</div>
                        <div class="fs-6 fw-bold">${anFmtEur2(d.savings_base_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border border-danger rounded p-3 h-100 bg-light">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.taxEstimate}"><i class="bi bi-receipt me-1"></i>Estimated IRPF Tax</div>
                        <div class="fs-5 fw-bold text-danger">${anFmtEur2(d.estimated_tax_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.unrealisedGain}">Unrealised Gain / Loss</div>
                        <div class="fs-6 fw-bold ${unrealCls}">${anFmtEur2(unreal)}</div>
                    </div>
                </div>
            </div>
            <h6 class="fw-semibold mb-2">
                <i class="bi bi-arrow-left-right me-1 text-secondary"></i>Realised Gains / Losses ${year}
                <i class="bi bi-info-circle text-muted ms-1" style="cursor:help;" data-bs-toggle="tooltip" title="${METRIC_HELP.realisedGain}"></i>
            </h6>
            <div class="table-responsive">
                <table class="table table-sm table-hover mb-3" style="max-width:560px;">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th class="text-end">Realised G/L</th>
                        </tr>
                    </thead>
                    <tbody>${realisedRows}</tbody>
                </table>
            </div>
            <h6 class="fw-semibold mb-2">
                <i class="bi bi-scissors me-1 text-danger"></i>Tax-Loss Harvesting Candidates
                <i class="bi bi-info-circle text-muted ms-1" style="cursor:help;" title="${harvestTooltip}"></i>
            </h6>
            <div class="table-responsive">
                <table class="table table-sm table-hover mb-2" style="max-width:620px;">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th class="text-end">Qty</th>
                            <th class="text-end">Unrealised Loss</th>
                        </tr>
                    </thead>
                    <tbody>${candRows}</tbody>
                </table>
            </div>
            ${d.note ? `<p class="text-muted small mb-0"><em>${d.note}</em></p>` : ''}`;
        initTooltips();
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading tax estimate: ${err.message}</div>`;
    }
}

// Analytics sub-navigation: show one tab's sections at a time and lazy-load
// each tab's data on first activation. Loaders per tab reuse the existing
// section render functions.
const _ANALYTICS_LOADERS = {
    performance: () => { loadAnalyticsPerformance(); loadAnalyticsNetworth(); },
    dividends: () => { loadAnalyticsDividends(); },
    gainloss: () => { loadAnalyticsGainLoss(); },
    tax: () => { loadAnalyticsTax(); loadAnalyticsTaxReport(); loadTaxOptimizer(); },
    risk: () => { loadAnalyticsRisk(); _wireDiversificationButtons(); },
    fees: () => { loadAnalyticsFees(); },
};
let _analyticsLoaded = {};
let _analyticsActiveTab = 'performance';

function showAnalyticsTab(tab, forceReload) {
    _analyticsActiveTab = tab;
    // Toggle nav pill active state
    document.querySelectorAll('#analyticsTabs [data-an-tab]').forEach(b =>
        b.classList.toggle('active', b.dataset.anTab === tab));
    // Show only this tab's section cards
    document.querySelectorAll('#analyticsPage [data-an-section]').forEach(card => {
        card.style.display = card.dataset.anSection === tab ? '' : 'none';
    });
    // Lazy-load (once) unless a refresh is forced
    if (forceReload || !_analyticsLoaded[tab]) {
        _analyticsLoaded[tab] = true;
        (_ANALYTICS_LOADERS[tab] || (() => {}))();
    }
}

function setupAnalyticsTabs() {
    const tabs = document.getElementById('analyticsTabs');
    if (tabs && !tabs.dataset.wired) {
        tabs.dataset.wired = '1';
        tabs.querySelectorAll('[data-an-tab]').forEach(btn => {
            btn.addEventListener('click', () => showAnalyticsTab(btn.dataset.anTab));
        });
        // Note: the #refreshAnalytics button calls loadAnalyticsPage() (which
        // re-enters here), so refresh is handled by the reset below — no extra
        // listener needed.
    }
    // Reset load state each time the page opens so data stays fresh per visit
    _analyticsLoaded = {};
    showAnalyticsTab(_analyticsActiveTab || 'performance');
}

// Gain / Loss leaderboard: top unrealised winners & losers (from holdings)
// plus realised gains/losses this year (from the tax estimate).
async function loadAnalyticsGainLoss() {
    const body = document.getElementById('anGainLossBody');
    if (!body) return;
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    try {
        const year = (document.getElementById('anTaxYear') || {}).value || new Date().getFullYear();
        const [data, tax] = await Promise.all([
            window.apiClient.getHoldings(),
            window.apiClient.getTaxEstimate(year).catch(() => ({ realised_by_symbol: [] })),
        ]);
        const holdings = (data.holdings || []).filter(h => parseFloat(h.quantity || 0) > 0);
        const hv = h => parseFloat(h.total_value_eur ?? h.total_value ?? 0) || 0;
        const pnl = h => parseFloat(h.pnl_amount || 0);
        const pnlpct = h => parseFloat(h.pnl_pct || 0);

        const byAmt = holdings.slice().sort((a, b) => pnl(b) - pnl(a));
        const byPct = holdings.slice().sort((a, b) => pnlpct(b) - pnlpct(a));
        const winners = byAmt.filter(h => pnl(h) > 0).slice(0, 5);
        const losers = byAmt.filter(h => pnl(h) < 0).reverse().slice(0, 5);
        const bestPct = byPct.filter(h => pnlpct(h) > 0).slice(0, 5);
        const worstPct = byPct.filter(h => pnlpct(h) < 0).reverse().slice(0, 5);

        const row = h => {
            const cls = pnl(h) >= 0 ? 'text-success' : 'text-danger';
            return `<tr>
                <td><strong>${esc(h.symbol)}</strong> <span class="small text-muted">${esc(h.name || '')}</span></td>
                <td class="text-end">${anFmtEur2(hv(h))}</td>
                <td class="text-end ${cls}">${pnl(h) >= 0 ? '+' : ''}${anFmtEur2(pnl(h))}</td>
                <td class="text-end ${cls}">${anFmtPct(pnlpct(h))}</td>
            </tr>`;
        };
        const tbl = (title, rows) => `
            <div class="col-12 col-lg-6">
                <h6 class="fw-semibold small text-muted text-uppercase mb-2">${title}</h6>
                <div class="table-responsive"><table class="table table-sm table-hover mb-3">
                    <thead><tr><th>Holding</th><th class="text-end">Value</th><th class="text-end">P/L €</th><th class="text-end">P/L %</th></tr></thead>
                    <tbody>${rows.length ? rows.map(row).join('') : '<tr><td colspan="4" class="text-center text-muted small">None.</td></tr>'}</tbody>
                </table></div>
            </div>`;

        // Realised this year (from tax estimate's per-symbol FIFO)
        const realised = (tax.realised_by_symbol || []).slice().sort((a, b) => (b.realised_eur || 0) - (a.realised_eur || 0));
        const realisedRows = realised.length ? realised.map(r => {
            const v = parseFloat(r.realised_eur || 0);
            return `<tr><td><strong>${esc(r.symbol)}</strong> <span class="small text-muted">${esc(r.name || '')}</span></td>
                <td class="text-end ${v >= 0 ? 'text-success' : 'text-danger'}">${anFmtEur2(v)}</td></tr>`;
        }).join('') : `<tr><td colspan="2" class="text-center text-muted small">No realised sales in ${year}.</td></tr>`;

        body.innerHTML = `
            <p class="text-muted small mb-3">Unrealised is mark-to-market on current holdings (EUR). Realised is locked-in FIFO gains/losses for ${year}.</p>
            <div class="row g-3">
                ${tbl('Top unrealised winners (€)', winners)}
                ${tbl('Top unrealised losers (€)', losers)}
                ${tbl('Best performers (%)', bestPct)}
                ${tbl('Worst performers (%)', worstPct)}
            </div>
            <h6 class="fw-semibold small text-muted text-uppercase mb-2">Realised gains / losses ${year}</h6>
            <div class="table-responsive" style="max-width:520px;"><table class="table table-sm table-hover mb-0">
                <thead><tr><th>Holding</th><th class="text-end">Realised €</th></tr></thead>
                <tbody>${realisedRows}</tbody>
            </table></div>`;
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading gain/loss: ${err.message}</div>`;
    }
}

let _lastTaxReport = null;
// Detailed per-lot FIFO tax report + dividend withholding for the selected year.
async function loadAnalyticsTaxReport() {
    const body = document.getElementById('anTaxReportBody');
    if (!body) return;
    const year = (document.getElementById('anTaxYear') || {}).value || new Date().getFullYear();
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    try {
        const d = await window.apiClient.getTaxReport(year);
        _lastTaxReport = d;
        const lots = d.realised_lots || [];
        const lotRows = lots.length ? lots.map(l => `
            <tr>
                <td><strong>${esc(l.symbol)}</strong></td>
                <td>${Fmt.date(l.sell_date)}</td>
                <td class="text-end">${Fmt.num(l.quantity, 0, 4)}</td>
                <td class="text-end">${anFmtEur2(l.proceeds)}</td>
                <td class="text-end">${anFmtEur2(l.cost_basis)}</td>
                <td class="text-end ${l.gain_loss >= 0 ? 'text-success' : 'text-danger'}">${anFmtEur2(l.gain_loss)}</td>
                <td class="text-end text-muted">${l.holding_days != null ? l.holding_days + 'd' : '—'}</td>
            </tr>`).join('') : `<tr><td colspan="7" class="text-center text-muted small">No sales realised in ${year}.</td></tr>`;
        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1">Realised gain ${year}</div><div class="fw-bold ${d.realised_gain_total >= 0 ? 'text-success' : 'text-danger'}">${anFmtEur2(d.realised_gain_total)}</div></div></div>
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1">Lots sold</div><div class="fw-bold">${d.lot_count}</div></div></div>
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1">Dividends (gross)</div><div class="fw-bold">${anFmtEur2(d.dividends_gross_eur)}</div></div></div>
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1">Withholding paid</div><div class="fw-bold">${anFmtEur2(d.dividend_withholding_eur)}</div></div></div>
            </div>
            <div class="table-responsive"><table class="table table-sm table-hover mb-2">
                <thead><tr><th>Symbol</th><th>Sold</th><th class="text-end">Qty</th><th class="text-end">Proceeds</th><th class="text-end">Cost basis</th><th class="text-end">Gain/Loss</th><th class="text-end">Held</th></tr></thead>
                <tbody>${lotRows}</tbody>
            </table></div>
            ${d.note ? `<p class="text-muted small mb-0"><em>${d.note}</em></p>` : ''}`;
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading tax report: ${err.message}</div>`;
    }
}

// Download the current tax report as CSV (built client-side from the JSON).
function downloadTaxReportCsv() {
    const d = _lastTaxReport;
    if (!d) { alert('Open the Tax tab first.'); return; }
    const rows = [['symbol', 'sell_date', 'quantity', 'proceeds', 'cost_basis', 'gain_loss', 'holding_days']];
    (d.realised_lots || []).forEach(l => rows.push([l.symbol, l.sell_date, l.quantity, l.proceeds, l.cost_basis, l.gain_loss, l.holding_days]));
    rows.push([]);
    rows.push(['Realised gain total', d.realised_gain_total]);
    rows.push(['Dividends gross (EUR)', d.dividends_gross_eur]);
    rows.push(['Dividend withholding (EUR)', d.dividend_withholding_eur]);
    const csv = '﻿' + rows.map(r => r.map(c => {
        const s = c == null ? '' : String(c);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `tax_report_${d.year}.csv`; a.click();
    URL.revokeObjectURL(url);
}

// Year-end tax optimizer: current vs after-harvest tax + candidate list.
async function loadTaxOptimizer() {
    const body = document.getElementById('anTaxOptimizerBody');
    if (!body) return;
    const year = (document.getElementById('anTaxYear') || {}).value || new Date().getFullYear();
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    try {
        const d = await window.apiClient.getTaxOptimizer(year);
        const saved = d.estimated_tax_saved_eur || 0;
        const cands = d.candidates || [];
        const rows = cands.length ? cands.map(c => `
            <tr class="${c.wash_sale_risk ? 'text-muted' : ''}">
                <td><strong>${esc(c.symbol)}</strong> <span class="small text-muted">${esc(c.name || '')}</span></td>
                <td class="text-end text-danger">${anFmtEur2(c.unrealised_loss_eur)}</td>
                <td>${c.last_buy ? Fmt.date(c.last_buy) : '—'}</td>
                <td>${c.wash_sale_risk
                    ? '<span class="badge bg-warning text-dark" title="Bought in the last 60 days — Spain disallows the loss within 2 months">2-month rule</span>'
                    : '<span class="badge bg-success">harvestable</span>'}</td>
            </tr>`).join('')
            : '<tr><td colspan="4" class="text-center text-muted small">No positions at a loss to harvest.</td></tr>';
        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1">Realised gains ${year}</div><div class="fw-bold ${d.realised_gain_eur >= 0 ? 'text-success' : 'text-danger'}">${anFmtEur2(d.realised_gain_eur)}</div></div></div>
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1">Income (div + interest)</div><div class="fw-bold">${anFmtEur2(d.income_eur)}</div></div></div>
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100"><div class="small text-muted mb-1" data-bs-toggle="tooltip" title="Sum of losses you can still harvest this year (excludes positions bought in the last 60 days).">Harvestable losses</div><div class="fw-bold text-danger">${anFmtEur2(d.harvestable_loss_eur)}</div></div></div>
                <div class="col-6 col-md-3"><div class="border rounded p-3 h-100 ${saved > 0 ? 'bg-success-subtle' : ''}"><div class="small text-muted mb-1">Est. tax saved</div><div class="fw-bold ${saved > 0 ? 'text-success' : ''}">${anFmtEur2(saved)}</div></div></div>
            </div>
            <p class="small mb-3">Estimated IRPF now: <strong>${anFmtEur2(d.estimated_tax_now_eur)}</strong> → after harvesting eligible losses: <strong>${anFmtEur2(d.estimated_tax_after_harvest_eur)}</strong>.</p>
            <div class="table-responsive">
                <table class="table table-sm table-hover mb-2">
                    <thead><tr><th>Position</th><th class="text-end">Unrealised loss</th><th>Last buy</th><th>Status</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            ${d.note ? `<p class="text-muted small mb-0"><em>${d.note}</em></p>` : ''}`;
        initTooltips();
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading tax optimizer: ${err.message}</div>`;
    }
}

// Wire the two "Load diversification" triggers (header + inline) once.
function _wireDiversificationButtons() {
    const btn = document.getElementById('anDiversificationBtn');
    const inline = document.getElementById('anDiversificationLoadInline');
    if (btn && !btn.dataset.wired) {
        btn.dataset.wired = '1';
        btn.addEventListener('click', () => loadAnalyticsDiversification());
    }
    if (inline && !inline.dataset.wired) {
        inline.dataset.wired = '1';
        inline.addEventListener('click', () => loadAnalyticsDiversification());
    }
}

// e) Diversification & concentration section
async function loadAnalyticsDiversification() {
    const body = document.getElementById('anDiversificationBody');
    if (!body) return;
    body.innerHTML = `
        <div class="text-center text-muted py-4">
            <div class="spinner-border spinner-border-sm me-2" role="status"></div>
            Loading diversification (fetching sector / country data, may take a few seconds)…
        </div>`;
    try {
        const d = await window.apiClient.getDiversification();
        const hhi = parseFloat(d.concentration_hhi || 0);
        // HHI > 2500 = concentrated, < 1500 = diversified, otherwise moderate
        let hhiLabel, hhiCls;
        if (hhi >= 2500) { hhiLabel = 'Concentrated'; hhiCls = 'bg-danger'; }
        else if (hhi < 1500) { hhiLabel = 'Diversified'; hhiCls = 'bg-success'; }
        else { hhiLabel = 'Moderate'; hhiCls = 'bg-warning text-dark'; }
        const largest = parseFloat(d.largest_position_pct || 0);

        const blocks = [
            { title: 'By Asset Type', data: d.by_asset_type, upper: true },
            { title: 'By Currency', data: d.by_currency, upper: true },
            { title: 'By Sector', data: d.by_sector, upper: false },
            { title: 'By Country', data: d.by_country, upper: false }
        ];
        const cols = blocks.map(b => `
            <div class="col-12 col-md-6 col-lg-3">
                <h6 class="fw-semibold small text-muted text-uppercase mb-2">${b.title}</h6>
                ${renderDiversificationBars(b.data, b.upper)}
            </div>`).join('');

        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Total Value</div>
                        <div class="fs-5 fw-bold">${anFmtEur(d.total_value_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.hhi}">Concentration (HHI)</div>
                        <div class="fs-5 fw-bold">${hhi.toFixed(0)} <span class="badge ${hhiCls} align-middle">${hhiLabel}</span></div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="Your single biggest holding as a share of total portfolio value — a quick read on single-name risk.">Largest Holding</div>
                        <div class="fs-5 fw-bold">${largest.toFixed(1)}%</div>
                        ${d.largest_position_symbol ? `<div class="small text-muted text-truncate" title="${d.largest_position_name || ''}">${d.largest_position_symbol}${d.largest_position_name && d.largest_position_name !== d.largest_position_symbol ? ' · ' + d.largest_position_name : ''}</div>` : ''}
                    </div>
                </div>
            </div>
            <p class="text-muted small mb-3">${METRIC_HELP.diversification}</p>
            <div class="row g-4">${cols}</div>`;
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading diversification: ${err.message}</div>`;
    }
    initTooltips();
}

// Render a labelled list of horizontal progress bars from a {label: pct} map
function renderDiversificationBars(map, upper) {
    const entries = Object.entries(map || {})
        .map(([k, v]) => [k, parseFloat(v || 0)])
        .sort((a, b) => b[1] - a[1]);
    if (!entries.length) {
        return '<p class="text-muted small mb-0">No data.</p>';
    }
    const COLOURS = ['#2563eb', '#16a34a', '#f59e0b', '#dc2626', '#7c3aed', '#64748b', '#0891b2', '#db2777'];
    return entries.map(([label, pct], i) => {
        const colour = COLOURS[i % COLOURS.length];
        const shown = upper ? String(label).toUpperCase() : label;
        return `
            <div class="mb-2">
                <div class="d-flex justify-content-between small mb-1">
                    <span class="text-truncate" style="max-width:70%;" title="${label}">${shown}</span>
                    <span class="text-muted">${pct.toFixed(1)}%</span>
                </div>
                <div class="progress" style="height:8px;">
                    <div class="progress-bar" role="progressbar" style="width:${Math.min(100, pct).toFixed(1)}%;background:${colour};"></div>
                </div>
            </div>`;
    }).join('');
}

// f) Risk section
async function loadAnalyticsRisk() {
    const body = document.getElementById('anRiskBody');
    if (!body) return;
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…</div>';
    try {
        const d = await window.apiClient.getRisk();
        // Insufficient history: API returns null metrics plus a note
        if (d.max_drawdown_pct == null) {
            body.innerHTML = `<p class="text-muted small mb-0"><em>${d.note || 'Not enough snapshot history yet to compute risk metrics.'}</em></p>`;
            return;
        }
        const dd = parseFloat(d.max_drawdown_pct || 0);
        const vol = parseFloat(d.volatility_pct || 0);
        const sharpe = parseFloat(d.sharpe_ratio || 0);
        const sharpeCls = sharpe >= 1 ? 'text-success' : (sharpe >= 0 ? 'text-warning' : 'text-danger');
        body.innerHTML = `
            <div class="row g-3">
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.maxDrawdown}">Max Drawdown</div>
                        <div class="fs-5 fw-bold text-danger">${dd.toFixed(2)}%</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.volatility}">Volatility (ann.)</div>
                        <div class="fs-5 fw-bold">${vol.toFixed(2)}%</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.sharpe}">Sharpe Ratio</div>
                        <div class="fs-5 fw-bold ${sharpeCls}">${sharpe.toFixed(2)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.snapshots}">Snapshots Used</div>
                        <div class="fs-5 fw-bold">${d.snapshots_used != null ? d.snapshots_used : '—'}</div>
                    </div>
                </div>
            </div>`;
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading risk metrics: ${err.message}</div>`;
    }
    initTooltips();
}

// g) Fees & costs section
async function loadAnalyticsFees() {
    const body = document.getElementById('anFeesBody');
    if (!body) return;
    body.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…</div>';
    try {
        const d = await window.apiClient.getFees();
        const drag = parseFloat(d.fee_drag_pct || 0);
        const byBroker = d.by_broker || {};
        const brokers = Object.keys(byBroker).sort(
            (a, b) => parseFloat(byBroker[b].fees_eur || 0) - parseFloat(byBroker[a].fees_eur || 0)
        );
        const rows = brokers.length
            ? brokers.map(name => {
                const b = byBroker[name];
                return `
                <tr>
                    <td><strong>${name}</strong></td>
                    <td class="text-end">${b.tx_count != null ? b.tx_count : '—'}</td>
                    <td class="text-end">${anFmtEur2(b.invested_eur)}</td>
                    <td class="text-end">${anFmtEur2(b.fees_eur)}</td>
                    <td class="text-end">${anFmtEur2(b.tax_eur)}</td>
                    <td class="text-end">${parseFloat(b.fee_drag_pct || 0).toFixed(2)}%</td>
                </tr>`;
            }).join('')
            : '<tr><td colspan="6" class="text-center text-muted small">No fee data yet.</td></tr>';

        body.innerHTML = `
            <div class="row g-3 mb-3">
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Total Fees</div>
                        <div class="fs-5 fw-bold text-warning">${anFmtEur2(d.total_fees_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Total Tax</div>
                        <div class="fs-5 fw-bold">${anFmtEur2(d.total_tax_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1">Total Invested</div>
                        <div class="fs-5 fw-bold">${anFmtEur(d.total_invested_eur)}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.feeDrag}">Fee Drag</div>
                        <div class="fs-5 fw-bold">${drag.toFixed(2)}%</div>
                    </div>
                </div>
            </div>
            <h6 class="fw-semibold small text-muted text-uppercase mb-2">Fees by Broker</h6>
            <div class="table-responsive">
                <table class="table table-sm table-hover mb-0">
                    <thead>
                        <tr>
                            <th>Broker</th>
                            <th class="text-end"># Tx</th>
                            <th class="text-end">Invested</th>
                            <th class="text-end">Fees</th>
                            <th class="text-end">Tax</th>
                            <th class="text-end">Drag %</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">Error loading fees: ${err.message}</div>`;
    }
    initTooltips();
}

// ---------------------------------------------------------------------------
// Watchlist page
// ---------------------------------------------------------------------------

// Watchlist sortable-table state (loadWatchlist is a plain function, not a method).
let _watchlistRows = [];
let _watchlistST = null;

async function loadWatchlist() {
    const tbody = document.querySelector('#watchlistTable tbody');
    if (!tbody) return;
    tbody.innerHTML = `
        <tr>
            <td colspan="8" class="text-center py-4">
                <div class="spinner-border spinner-border-sm me-2" role="status"></div>
                Loading watchlist (fetching live prices)…
            </td>
        </tr>`;
    try {
        const items = await window.apiClient.getWatchlist();
        if (!Array.isArray(items) || items.length === 0) {
            _watchlistRows = [];
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">Your watchlist is empty. Add a symbol above to start tracking it.</td></tr>';
            return;
        }
        const typeBadge = t => ({ stock: 'bg-primary', etf: 'bg-info', index: 'bg-success', crypto: 'bg-warning text-dark', bond: 'bg-secondary', commodity: 'bg-dark' }[t] || 'bg-secondary');
        const renderWatchRow = (w) => {
            const price = w.current_price != null ? parseFloat(w.current_price) : null;
            const buyBelow = w.buy_below != null ? parseFloat(w.buy_below) : null;
            const dist = w.distance_to_buy_pct != null ? parseFloat(w.distance_to_buy_pct) : null;
            const inZone = !!w.in_buy_zone;
            const distCell = dist != null
                ? `<span class="${inZone ? 'text-success fw-semibold' : ''}">${(dist >= 0 ? '+' : '') + dist.toFixed(1)}%</span>`
                : '—';
            const symCell = inZone
                ? `<strong>${esc(w.symbol)}</strong> <span class="badge bg-success">BUY ZONE</span> ${assetLinks(w.symbol)}`
                : `<strong>${esc(w.symbol)}</strong> ${assetLinks(w.symbol)}`;
            return `
                <tr class="${inZone ? 'table-success' : ''}">
                    <td class="ps-3">${symCell}</td>
                    <td>${esc(w.name || '')}</td>
                    <td><span class="badge ${typeBadge(w.asset_type)}">${(w.asset_type || '').toUpperCase() || '—'}</span></td>
                    <td class="text-end">${price != null ? price.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
                    <td class="text-end">${buyBelow != null ? buyBelow.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
                    <td class="text-end">${distCell}</td>
                    <td class="text-muted small">${esc(w.notes || '')}</td>
                    <td class="pe-3">
                        <button class="btn btn-sm btn-outline-danger" onclick="window.deleteWatchlistRow('${(w.symbol || '').replace(/'/g, "\\'")}')" title="Remove from watchlist">
                            <i class="bi bi-trash"></i>
                        </button>
                    </td>
                </tr>`;
        };
        _watchlistRows = items;

        // Above-table asset-type filter (consistent with Holdings/Assets); writes
        // into the table's state.filters, which applyTableState honours.
        const wTypeSel = document.getElementById('watchlistTypeFilter');
        if (wTypeSel) {
            if (!window.PREFS.tableState) window.PREFS.tableState = {};
            const st = window.PREFS.tableState.watchlist = window.PREFS.tableState.watchlist || { sort: null, filters: {} };
            st.filters = st.filters || {};
            const types = [...new Set(items.map(w => w.asset_type || 'other').filter(Boolean))].sort();
            const cur = st.filters.asset_type || 'all';
            wTypeSel.innerHTML = '<option value="all">All Asset Types</option>' +
                types.map(t => `<option value="${esc(t)}">${esc(t.toUpperCase())}</option>`).join('');
            wTypeSel.value = [...wTypeSel.options].some(o => o.value === cur) ? cur : 'all';
            if (!wTypeSel._bound) {
                wTypeSel._bound = true;
                wTypeSel.addEventListener('change', () => {
                    const s = window.PREFS.tableState.watchlist;
                    s.filters = s.filters || {};
                    s.filters.asset_type = wTypeSel.value;
                    savePrefs();
                    if (_watchlistST) _watchlistST.refresh();
                });
            }
        }

        _watchlistST = _watchlistST || makeSortableTable({
            table: document.getElementById('watchlistTable'),
            columns: [
                { key: 'symbol', type: 'text' }, { key: 'name', type: 'text' },
                { key: 'asset_type', type: 'text' }, { key: 'current_price', type: 'num' },
                { key: 'buy_below', type: 'num' }, { key: 'distance_to_buy_pct', type: 'num' },
                { key: 'notes', type: 'text' }, { key: null },
            ],
            getRows: () => _watchlistRows,
            renderRows: (rows, tb) => { tb.innerHTML = rows.length ? rows.map(renderWatchRow).join('') : '<tr><td colspan="8" class="text-center text-muted py-4">No watchlist items match the filter.</td></tr>'; },
            prefsKey: 'watchlist',
        });
        _watchlistST.refresh();
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-danger small py-3 ps-3">Error loading watchlist: ${err.message}</td></tr>`;
    }
}

window.deleteWatchlistRow = async function(symbol) {
    if (!confirm(`Remove ${symbol} from your watchlist?`)) return;
    try {
        await window.apiClient.deleteWatchlist(symbol);
        loadWatchlist();
    } catch (err) {
        alert('Error removing from watchlist: ' + err.message);
    }
};
