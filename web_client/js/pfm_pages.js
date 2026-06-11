// pfm_pages.js — part of the portfolio_debug.js split.
// Pages: page/nav/auth managers, dashboard, transactions, assets, holdings, help/resources.
// Classic script (no build step): these files share one global scope
// and MUST load in this order: pfm_core, pfm_pages, pfm_analytics,
// pfm_features. See index.html.

// Dashboard Top Positions state + rendering. _dashHoldingsAll backs the KPIs,
// donut and the table when broker=all; _dashTopHoldings backs the table when a
// broker filter is active (separate per-broker fetch).
let _dashHoldingsAll = [];
let _dashTopHoldings = [];

function _dashTypeBadge(t) {
    return ({ stock: 'bg-primary', etf: 'bg-info', index: 'bg-success', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[t] || 'bg-secondary');
}

function renderDashTopPositions() {
    const cfg = window.PREFS.dashTopPositions || { n: 5, type: 'all', broker: 'all', sort: 'value' };
    const body = document.querySelector('#dashTopPositionsTable tbody');
    if (!body) return;

    // Right column shows € P&L for the "total" sorts, otherwise % P&L.
    const isTotal = cfg.sort === 'gain_total' || cfg.sort === 'loss_total';
    const hdr = document.getElementById('dashTopPnlHeader');
    if (hdr) hdr.textContent = isTotal ? 'P&L (EUR)' : 'P&L %';

    const rows = topPositions(_dashTopHoldings, { n: cfg.n, type: cfg.type, sort: cfg.sort });
    if (rows.length === 0) {
        body.innerHTML = '<tr><td colspan="4" class="text-center text-muted ps-3 py-3">No positions match this filter.</td></tr>';
        return;
    }
    body.innerHTML = rows.map(h => {
        const valEur = parseFloat(h.total_value_eur || h.total_value || 0);
        const pnlPct = parseFloat(h.pnl_pct || 0);
        const pnlAmt = parseFloat(h.pnl_amount || 0);
        const metric = isTotal ? pnlAmt : pnlPct;
        const cls = metric >= 0 ? 'text-success' : 'text-danger';
        const txt = isTotal
            ? (metric >= 0 ? '+' : '') + metric.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : (metric >= 0 ? '+' : '') + metric.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%';
        const name = h.name || h.symbol || '';
        return `
        <tr>
            <td class="ps-3" style="max-width:220px;">
                <div class="fw-semibold text-truncate" title="${esc(name)}">${esc(name)}</div>
                <div class="small text-muted">${esc(h.symbol || '')} ${assetLinks(h.symbol)}</div>
            </td>
            <td><span class="badge ${_dashTypeBadge(h.asset_type)}">${esc((h.asset_type || '').toUpperCase())}</span></td>
            <td class="text-end">${valEur.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
            <td class="text-end pe-3 ${cls} fw-semibold">${txt}</td>
        </tr>`;
    }).join('');
}

// Re-fetch the table's backing data if a broker filter is active, else reuse
// the all-broker holdings; then render.
async function refreshDashTopHoldings() {
    const cfg = window.PREFS.dashTopPositions || {};
    if (cfg.broker && cfg.broker !== 'all') {
        const data = await window.apiClient.getHoldings(cfg.broker);
        _dashTopHoldings = (data && data.holdings) || [];
    } else {
        _dashTopHoldings = _dashHoldingsAll;
    }
    renderDashTopPositions();
}

// Populate the Type (from current holdings) and Broker (from portfolios) selects
// and apply saved values. Wire change handlers once.
async function setupDashTopControls() {
    const cfg = window.PREFS.dashTopPositions || { n: 5, type: 'all', broker: 'all', sort: 'value' };
    const elN = document.getElementById('dashTopN');
    const elType = document.getElementById('dashTopType');
    const elBroker = document.getElementById('dashTopBroker');
    const elSort = document.getElementById('dashTopSort');
    if (!elN || !elType || !elBroker || !elSort) return;

    // Type options from the asset types actually present.
    const types = [...new Set(_dashHoldingsAll
        .filter(h => parseFloat(h.quantity || 0) > 0)
        .map(h => h.asset_type || 'other'))].sort();
    elType.innerHTML = '<option value="all">All</option>' +
        types.map(t => `<option value="${esc(t)}">${esc(t.toUpperCase())}</option>`).join('');

    // Broker options from portfolios (fetch once — the select keeps its options
    // across dashboard re-visits since the page div is never destroyed).
    if (elBroker.options.length <= 1) {
        try {
            const ps = await window.apiClient.getPortfolios();
            const list = Array.isArray(ps) ? ps : (ps.portfolios || []);
            elBroker.innerHTML = '<option value="all">All</option>' +
                list.map(p => `<option value="${esc(String(p.id))}">${esc(p.name || '')}</option>`).join('');
        } catch (e) { /* keep just "All" */ }
    }

    // Apply saved values (fall back to defaults if an option no longer exists).
    elN.value = String(cfg.n);
    elType.value = [...elType.options].some(o => o.value === cfg.type) ? cfg.type : 'all';
    elBroker.value = [...elBroker.options].some(o => o.value === String(cfg.broker)) ? String(cfg.broker) : 'all';
    elSort.value = cfg.sort;

    // Wire once.
    const save = (patch, refetch) => {
        window.PREFS.dashTopPositions = Object.assign({}, window.PREFS.dashTopPositions, patch);
        savePrefs();
        if (refetch) refreshDashTopHoldings(); else renderDashTopPositions();
    };
    if (!elN._bound) { elN._bound = true; elN.addEventListener('change', () => save({ n: elN.value }, false)); }
    if (!elType._bound) { elType._bound = true; elType.addEventListener('change', () => save({ type: elType.value }, false)); }
    if (!elSort._bound) { elSort._bound = true; elSort.addEventListener('change', () => save({ sort: elSort.value }, false)); }
    if (!elBroker._bound) { elBroker._bound = true; elBroker.addEventListener('change', () => save({ broker: elBroker.value }, true)); }

    const gear = document.getElementById('dashTopConfigBtn');
    const panel = document.getElementById('dashTopControls');
    if (gear && panel && !gear._bound) {
        gear._bound = true;
        gear.addEventListener('click', () => {
            panel.style.display = panel.style.display === 'none' ? '' : 'none';
        });
    }
}

// Page Manager
// ---------------------------------------------------------------------------
function createPageManager() {
    return {
        hideLoadingSpinners: function() {
            const loadingElements = document.querySelectorAll('.loading, .spinner-border, [data-loading]');
            loadingElements.forEach(el => el.style.display = 'none');
        },

        loadAssetsPage: async function() {
            const tableBody = document.querySelector('#assetsPage tbody');
            if (tableBody) tableBody.innerHTML = '<tr><td colspan="7" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</td></tr>';
            try {
                const assets = await window.apiClient.getAssets();

                this._assetsData = assets;
                this._assetSuggestions = assets
                    .filter(a => a.symbol)
                    .map(a => AssetSearch.enrich(a.symbol, a.name));

                // Wire filters once per page lifecycle
                const page = document.getElementById('assetsPage');
                if (page && !page.dataset.filtersWired) {
                    page.dataset.filtersWired = '1';
                    document.getElementById('assetTypeFilter')
                        ?.addEventListener('change', () => this._renderFilteredAssets());
                    document.getElementById('refreshAssets')
                        ?.addEventListener('click', () => this.loadAssetsPage());
                    this._setupAssetAutocomplete();
                }

                this._renderFilteredAssets();
                this.hideLoadingSpinners();
            } catch (error) {
                console.error('Error loading assets page:', error);
                if (tableBody) tableBody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error loading assets.</td></tr>';
                this.hideLoadingSpinners();
            }
        },

        _renderFilteredAssets: function() {
            const tableBody = document.querySelector('#assetsPage tbody');
            if (!tableBody || !this._assetsData) return;

            const typeVal   = document.getElementById('assetTypeFilter')?.value || '';
            const searchVal = (document.getElementById('assetSearchInput')?.value || '').trim();

            let filtered = this._assetsData;
            if (typeVal) filtered = filtered.filter(a => a.asset_type === typeVal);
            if (searchVal) {
                // Smart match using AssetSearch (no result limit — this is a table filter)
                const matched = new Set(
                    AssetSearch.match(searchVal, this._assetSuggestions || [], this._assetsData.length)
                        .map(s => s.symbol)
                );
                // Also let exchange substring through (not in AssetSearch scoring)
                const sq = searchVal.toLowerCase();
                filtered = filtered.filter(a =>
                    matched.has(a.symbol) || (a.exchange || '').toLowerCase().includes(sq)
                );
            }

            this._assetsRows = filtered;
            const emptyMsg = '<tr><td colspan="7" class="text-center text-muted py-4">No assets match the current filters.</td></tr>';
            const renderAssetRow = (asset) => `
                    <tr>
                        <td><strong>${esc(asset.symbol || 'N/A')}</strong></td>
                        <td>${esc(asset.name || 'N/A')}</td>
                        <td><span class="badge bg-primary">${asset.asset_type || 'N/A'}</span></td>
                        <td>${asset.exchange || 'N/A'}</td>
                        <td>
                            ${fmtPrice(asset.current_price, asset.currency)}
                            ${asset.auto_price === false ? '<span class="badge bg-secondary ms-1" title="Manual price — the daily cron will not overwrite it">manual</span>' : ''}
                            <button class="btn btn-sm btn-link p-0 ms-1 align-baseline" title="Set a manual price" onclick="setAssetPrice(${asset.id}, '${(asset.symbol || '').replace(/'/g, "\\'")}', '${asset.currency || ''}')"><i class="bi bi-pencil-square"></i></button>
                        </td>
                        <td>${asset.currency || ''}</td>
                        <td>${assetLinks(asset.symbol)}</td>
                    </tr>`;
            this._assetsST = this._assetsST || makeSortableTable({
                table: document.querySelector('#assetsPage table'),
                columns: [
                    { key: 'symbol', type: 'text' }, { key: 'name', type: 'text' },
                    { key: 'asset_type', type: 'text' }, { key: 'exchange', type: 'text' },
                    { key: 'current_price', type: 'num' }, { key: 'currency', type: 'text' },
                    { key: null },
                ],
                getRows: () => this._assetsRows,
                renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderAssetRow).join('') : emptyMsg; },
                prefsKey: 'assets',
            });
            this._assetsST.refresh();
        },

        _setupAssetAutocomplete: function() {
            const input   = document.getElementById('assetSearchInput');
            const suggest = document.getElementById('assetSuggest');
            if (!input || !suggest) return;
            const self = this;
            AssetSearch.buildAutocomplete(input, suggest, {
                getSuggestions: () => self._assetSuggestions || [],
                onSelect: () => self._renderFilteredAssets(),
                onInput:  () => self._renderFilteredAssets(),
                clearOnEscape: true,
            });
        },

        loadDashboardPage: async function() {
            const el = id => document.getElementById(id);

            // Alerts banner + price-data freshness chip load independently (non-blocking).
            loadDashboardAlerts();
            loadDataFreshness();

            const fmtEur = (val) => {
                const n = parseFloat(val) || 0;
                return n.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' EUR';
            };

            const fmtPct = (val) => {
                const n = parseFloat(val) || 0;
                return (n >= 0 ? '+' : '') + n.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%';
            };

            // Kick off both data fetches in parallel
            const [holdingsData, transactions] = await Promise.all([
                window.apiClient.getHoldings(),
                window.apiClient.getTransactions(10)
            ]).catch(err => {
                console.error('Error loading dashboard data:', err);
                return [{ holdings: [], summary: {} }, []];
            });

            const { holdings = [], summary = {} } = holdingsData;

            // --- KPI cards ---
            const totalValue = parseFloat(summary.total_value || 0);
            const totalCost  = parseFloat(summary.total_cost  || 0);
            const totalPnl   = parseFloat(summary.total_pnl   || 0);
            const totalPnlPct = parseFloat(summary.total_pnl_pct || 0);
            const openPositions = holdings.filter(h => parseFloat(h.quantity || 0) > 0).length;

            if (el('totalValue'))    el('totalValue').textContent    = fmtEur(totalValue);
            if (el('dashTotalCost')) el('dashTotalCost').textContent = fmtEur(totalCost);

            if (el('totalGainLoss')) {
                el('totalGainLoss').textContent = (totalPnl >= 0 ? '+' : '') + fmtEur(totalPnl);
            }
            if (el('dashPnlPct')) {
                el('dashPnlPct').textContent = fmtPct(totalPnlPct);
            }
            const pnlCard = el('dashPnlCard');
            if (pnlCard) {
                pnlCard.style.background = totalPnl >= 0 ? '#198754' : '#dc3545';
            }

            if (el('totalAssets')) el('totalAssets').textContent = openPositions;
            // Per-asset-type breakdown (e.g. "12 stock · 19 etf · 4 index · 10 crypto")
            if (el('totalTransactions')) {
                const byType = {};
                holdings.filter(h => parseFloat(h.quantity || 0) > 0).forEach(h => {
                    const t = (h.asset_type || 'other');
                    byType[t] = (byType[t] || 0) + 1;
                });
                const parts = Object.entries(byType).sort((a, b) => b[1] - a[1])
                    .map(([t, n]) => `${n} ${t}`);
                el('totalTransactions').textContent = parts.length ? parts.join(' · ')
                    : (openPositions === 1 ? '1 position' : openPositions + ' positions');
            }

            // --- Top positions (configurable) ---
            _dashHoldingsAll = holdings;
            await setupDashTopControls();
            await refreshDashTopHoldings();

            // --- Allocation donut chart ---
            const donutArea = el('dashDonutArea');
            if (donutArea) {
                const grouped = {};
                holdings.filter(h => parseFloat(h.quantity || 0) > 0).forEach(h => {
                    const t = h.asset_type || 'other';
                    grouped[t] = (grouped[t] || 0) + parseFloat(h.total_value_eur || h.total_value || 0);
                });

                const COLOURS = ['#2563eb', '#16a34a', '#f59e0b', '#dc2626', '#7c3aed', '#64748b'];
                const entries = Object.entries(grouped).sort((a, b) => b[1] - a[1]);
                const grandTotal = entries.reduce((s, e) => s + e[1], 0);

                if (grandTotal <= 0 || entries.length === 0) {
                    donutArea.innerHTML = '<p class="text-muted small mb-0">No holdings data yet.</p>';
                } else {
                    const MAX_SLICES = 5;
                    let slices = entries.slice(0, MAX_SLICES);
                    if (entries.length > MAX_SLICES) {
                        const otherVal = entries.slice(MAX_SLICES).reduce((s, e) => s + e[1], 0);
                        if (otherVal > 0) slices.push(['other', otherVal]);
                    }

                    const R = 64;
                    const CX = 80;
                    const CY = 80;
                    const CIRC = 2 * Math.PI * R;

                    let offset = 0;
                    const svgSlices = slices.map(([ label, value ], i) => {
                        const pct     = value / grandTotal;
                        const dash    = pct * CIRC;
                        const gap     = CIRC - dash;
                        const dashStr = `${dash.toFixed(2)} ${gap.toFixed(2)}`;
                        const offStr  = (-offset * CIRC).toFixed(2);
                        offset += pct;
                        const colour  = COLOURS[i % COLOURS.length];
                        return `<circle cx="${CX}" cy="${CY}" r="${R}" fill="none"
                                    stroke="${colour}" stroke-width="26"
                                    stroke-dasharray="${dashStr}"
                                    stroke-dashoffset="${offStr}"
                                    transform="rotate(-90 ${CX} ${CY})"/>`;
                    }).join('');

                    const legendItems = slices.map(([ label, value ], i) => {
                        const pct    = ((value / grandTotal) * 100).toFixed(1);
                        const colour = COLOURS[i % COLOURS.length];
                        return `<div class="d-flex align-items-center gap-2 mb-1">
                                    <span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:${colour};flex-shrink:0;"></span>
                                    <span class="small">${label.toUpperCase()} <span class="text-muted">${pct}%</span></span>
                                </div>`;
                    }).join('');

                    donutArea.innerHTML = `
                        <svg viewBox="0 0 160 160" style="flex:0 0 auto;width:100%;max-width:200px;height:auto;">
                            ${svgSlices}
                            <text x="${CX}" y="${CY - 8}" text-anchor="middle" font-size="12" fill="#94a3b8">Total</text>
                            <text x="${CX}" y="${CY + 12}" text-anchor="middle" font-size="16" font-weight="bold" class="donut-total">${(grandTotal / 1000).toFixed(1)}k</text>
                        </svg>
                        <div class="d-flex flex-column justify-content-center">${legendItems}</div>
                    `;
                }
            }

            // --- Recent transactions table ---
            const tableBody = document.querySelector('#recentTransactionsTable tbody');
            if (tableBody) {
                if (transactions.length === 0) {
                    tableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted ps-3 py-3">No transactions yet.</td></tr>';
                } else {
                    tableBody.innerHTML = transactions.slice(0, 10).map(tx => {
                        const typeCls = tx.transaction_type === 'buy' ? 'success' : tx.transaction_type === 'sell' ? 'danger' : 'secondary';
                        const txName = tx.name || tx.symbol || '';
                        return `
                            <tr>
                                <td class="ps-3">${Fmt.date(tx.transaction_date)}</td>
                                <td style="max-width:200px;">
                                    <div class="fw-semibold text-truncate" title="${txName}">${txName}</div>
                                    <div class="small text-muted">${esc(tx.symbol || '')}</div>
                                </td>
                                <td><span class="badge bg-${typeCls}">${(tx.transaction_type || '').toUpperCase()}</span></td>
                                <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), { maximumFractionDigits: 4 })}</td>
                                <td class="text-end">${fmtPrice(tx.price, tx.currency)}</td>
                                <td class="text-end pe-3">${fmtPrice(tx.total_amount, tx.currency)}</td>
                            </tr>
                        `;
                    }).join('');
                }
            }

            // Wire up the refresh button for top positions (shares the full dashboard reload)
            const refreshTopBtn = document.getElementById('refreshTopPositions');
            if (refreshTopBtn && !refreshTopBtn._bound) {
                refreshTopBtn._bound = true;
                refreshTopBtn.addEventListener('click', () => this.loadDashboardPage());
            }

            // --- Return KPI with period selector ---
            const periodSel = el('dashReturnPeriod');
            if (periodSel && !periodSel._bound) {
                periodSel._bound = true;
                periodSel.addEventListener('change', () => loadDashboardReturn(periodSel.value));
            }
            loadDashboardReturn(periodSel ? periodSel.value : 'all');

            initTooltips();
            this.hideLoadingSpinners();
        },

        loadTransactionsPage: async function() {
            const tableBody = document.querySelector('#transactionsPage tbody');
            if (!tableBody) return;

            tableBody.innerHTML = '<tr><td colspan="10" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>Loading...</td></tr>';

            // Populate portfolio filter dropdown (once)
            const portfolioFilter = document.getElementById('txPortfolioFilter');
            if (portfolioFilter && portfolioFilter.options.length <= 1) {
                const portfolios = await window.apiClient.getPortfolios();
                portfolios.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id;
                    opt.textContent = p.name;
                    portfolioFilter.appendChild(opt);
                });
                portfolioFilter.onchange = () => window.pageManager.loadTransactionsPage();
            }

            const selectedPortfolioId = portfolioFilter ? (portfolioFilter.value || null) : null;

            try {
                // Fetch trades and cash bookings together; cash movements
                // to/from the broker are shown inline with the trades.
                const [transactions, allBookings] = await Promise.all([
                    window.apiClient.getTransactions(500, selectedPortfolioId),
                    window.apiClient.getBookings().catch(() => []),
                ]);
                let bookings = Array.isArray(allBookings) ? allBookings : [];
                if (selectedPortfolioId) {
                    bookings = bookings.filter(b => String(b.portfolio_id) === String(selectedPortfolioId));
                }

                // Unify into one date-sorted list (trades + cash bookings).
                // Each row carries filter metadata alongside its HTML.
                const rows = [];
                transactions.forEach(tx => rows.push({
                    date: tx.transaction_date,
                    txType: tx.transaction_type || '',
                    sym: (tx.symbol || '').toLowerCase(),
                    txName: (tx.name || '').toLowerCase(),
                    isBooking: false,
                    html: `
                        <tr>
                            <td>${Fmt.date(tx.transaction_date)}</td>
                            <td><small>${esc(tx.portfolio_name || '')}</small></td>
                            <td><strong>${esc(tx.symbol || '')}</strong> ${assetLinks(tx.symbol)}<br><small class="text-muted">${esc(tx.name || '')}</small></td>
                            <td><span class="badge bg-${tx.transaction_type === 'buy' ? 'success' : tx.transaction_type === 'sell' ? 'danger' : 'secondary'}">${(tx.transaction_type || '').toUpperCase()}</span></td>
                            <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), {maximumFractionDigits: 6})}</td>
                            <td class="text-end">${fmtPrice(tx.price, tx.currency)}</td>
                            <td>${tx.currency || ''}</td>
                            <td class="text-end">${fmtPrice(tx.total_amount, tx.currency)}</td>
                            <td class="text-end">${fmtPrice(tx.fees, tx.currency)}</td>
                            <td class="text-nowrap">
                                <button class="btn btn-sm btn-outline-primary me-1"
                                    onclick="openEditTransaction(${tx.id},'${tx.transaction_date}','${tx.transaction_type}',${tx.quantity},${tx.price},${tx.fees||0},${tx.portfolio_id||0},'${(tx.description||'').replace(/'/g,"\\'")}')">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger"
                                    onclick="confirmDeleteTransaction(${tx.id},'${tx.symbol}')">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </td>
                        </tr>`
                }));
                bookings.forEach(b => {
                    const isDep = b.action === 'Deposit';
                    rows.push({
                        date: b.date,
                        txType: '',
                        sym: '',
                        txName: '',
                        isBooking: true,
                        html: `
                        <tr class="tx-cash-row">
                            <td>${Fmt.date(b.date)}</td>
                            <td><small>${esc(b.portfolio_name || '')}</small></td>
                            <td><i class="bi bi-cash-coin me-1 text-muted"></i><span class="text-muted">Cash ${isDep ? 'in' : 'out'} (broker)</span></td>
                            <td><span class="badge ${isDep ? 'bg-info text-dark' : 'bg-warning text-dark'}">${b.action.toUpperCase()}</span></td>
                            <td class="text-end">—</td>
                            <td class="text-end">—</td>
                            <td>${b.currency || ''}</td>
                            <td class="text-end">${isDep ? '+' : '−'}${fmtPrice(Math.abs(parseFloat(b.amount || 0)), b.currency)}</td>
                            <td class="text-end">—</td>
                            <td class="text-nowrap">
                                <button class="btn btn-sm btn-outline-danger"
                                    onclick="confirmDeleteBooking(${b.id})" title="Delete cash booking">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </td>
                        </tr>`
                    });
                });

                // Newest first; fall back to stable order when dates match
                rows.sort((a, b) => String(b.date).localeCompare(String(a.date)));

                // Store for client-side filter re-renders
                this._txAllRows = rows;

                // Build autocomplete suggestions from unique symbols in loaded transactions
                const symMap = new Map();
                transactions.forEach(tx => {
                    if (tx.symbol && !symMap.has(tx.symbol)) {
                        symMap.set(tx.symbol, AssetSearch.enrich(tx.symbol, tx.name));
                    }
                });
                this._txSuggestions = [...symMap.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));

                // Wire client-side filters once per page lifecycle
                const page = document.getElementById('transactionsPage');
                if (page && !page.dataset.filtersWired) {
                    page.dataset.filtersWired = '1';
                    ['transactionTypeFilter', 'fromDate', 'toDate'].forEach(id => {
                        const el = document.getElementById(id);
                        if (el) el.addEventListener('change', () => this._renderFilteredTx());
                    });
                    this._setupTxAssetAutocomplete();
                }

                this._renderFilteredTx();
            } catch (error) {
                console.error('Error loading transactions:', error);
                tableBody.innerHTML = '<tr><td colspan="10" class="text-center text-danger">Error loading transactions.</td></tr>';
            }

            this.hideLoadingSpinners();
        },

        _renderFilteredTx: function() {
            const tableBody = document.querySelector('#transactionsPage tbody');
            if (!tableBody || !this._txAllRows) return;

            const typeVal = document.getElementById('transactionTypeFilter')?.value || '';
            const fromVal = document.getElementById('fromDate')?.value || '';
            const toVal = document.getElementById('toDate')?.value || '';
            const assetVal = (document.getElementById('txAssetFilter')?.value || '').trim().toLowerCase();

            const filtered = this._txAllRows.filter(r => {
                if (typeVal) {
                    if (r.isBooking) return false;
                    if (r.txType !== typeVal) return false;
                }
                if (fromVal && r.date < fromVal) return false;
                if (toVal && r.date > toVal) return false;
                if (assetVal) {
                    if (r.isBooking) return false;
                    if (!r.sym.includes(assetVal) && !r.txName.includes(assetVal)) return false;
                }
                return true;
            });

            if (filtered.length === 0) {
                tableBody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-4">No transactions match the current filters.</td></tr>';
            } else {
                tableBody.innerHTML = filtered.map(r => r.html).join('');
            }
        },

        _setupTxAssetAutocomplete: function() {
            const input = document.getElementById('txAssetFilter');
            const suggest = document.getElementById('txAssetSuggest');
            if (!input || !suggest) return;
            const self = this;
            AssetSearch.buildAutocomplete(input, suggest, {
                getSuggestions: () => self._txSuggestions || [],
                onSelect: () => self._renderFilteredTx(),
                onInput: () => self._renderFilteredTx(),
                clearOnEscape: true,
            });
        },

        loadHoldingsPage: async function() {
            const tableBody = document.querySelector('#holdingsTable tbody');
            if (!tableBody) return;

            tableBody.innerHTML = '<tr><td colspan="12" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>Loading...</td></tr>';

            try {
                const data = await window.apiClient.getHoldings();
                const { holdings = [], summary = {} } = data;

                // Update summary cards
                const fmt = (n) => n !== undefined ? parseFloat(n).toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
                const el = id => document.getElementById(id);
                if (el('holdingsTotalValue')) el('holdingsTotalValue').textContent = fmt(summary.total_value);
                if (el('holdingsTotalCost'))  el('holdingsTotalCost').textContent  = fmt(summary.total_cost);

                const pnl = summary.total_pnl || 0;
                const pnlPct = summary.total_pnl_pct || 0;
                const pnlCard = el('holdingsPnlCard');
                const pnlEl = el('holdingsTotalPnl');
                if (pnlEl) pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${fmt(pnl)} (${pnlPct >= 0 ? '+' : ''}${fmt(pnlPct)}%)`;
                if (pnlCard) {
                    pnlCard.className = `card text-white ${pnl >= 0 ? 'bg-success' : 'bg-danger'}`;
                }

                // Apply the user's "hide tiny positions" threshold + sort pref.
                // Summary cards above stay on the full set; only the table view
                // is filtered/sorted.
                const hideBelow = parseFloat(window.PREFS.hideBelowEur) || 0;
                const hVal = h => parseFloat(h.total_value_eur ?? h.total_value ?? 0) || 0;
                let view = holdings.slice();
                if (hideBelow > 0) view = view.filter(h => hVal(h) >= hideBelow);
                // Seed the default holdings sort from the legacy holdingsSort pref (once).
                if (!window.PREFS.tableState || !window.PREFS.tableState.holdings) {
                    const legacy = { value: { key: 'total_value_eur', dir: 'desc' }, pnl: { key: 'pnl_amount', dir: 'desc' }, pnlpct: { key: 'pnl_pct', dir: 'desc' }, name: { key: 'name', dir: 'asc' } }[window.PREFS.holdingsSort || 'value'];
                    if (legacy) { if (!window.PREFS.tableState) window.PREFS.tableState = {}; window.PREFS.tableState.holdings = { sort: legacy, filters: {} }; }
                }
                // Store the hide-tiny-filtered set; the shared table does sort+filter.
                this._holdingsRows = view;
                const emptyMsg = `<tr><td colspan=”12” class=”text-center text-muted”>${holdings.length ? 'No holdings match the current filter.' : 'No holdings found. Add buy transactions to see your positions here.'}</td></tr>`;
                const renderHoldingRow = (h) => {
                    const pnlClass = h.pnl_amount >= 0 ? 'text-success' : 'text-danger';
                    const typeBadge = { stock: 'bg-primary', etf: 'bg-info', index: 'bg-success', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[h.asset_type] || 'bg-secondary';
                    const symEsc = (h.symbol || '').replace(/'/g, "\\'");
                    return `
                        <tr>
                            <td><strong>${esc(h.symbol)}</strong></td>
                            <td>${esc(h.name)}</td>
                            <td><span class=”badge ${typeBadge}”>${esc((h.asset_type || '').toUpperCase())}</span></td>
                            <td>${esc(h.currency || '')}</td>
                            <td class=”text-end”>${parseFloat(h.quantity).toLocaleString(Fmt.loc(), { maximumFractionDigits: 4 })}</td>
                            <td class=”text-end”>${fmt(h.avg_price)}</td>
                            <td class=”text-end”>${h.current_price > 0 ? fmt(h.current_price) : '<span class=”text-muted”>—</span>'}</td>
                            <td class=”text-end fw-bold”>${fmt(h.total_value)}</td>
                            <td class=”text-end ${pnlClass}”>${h.pnl_amount >= 0 ? '+' : ''}${fmt(h.pnl_amount)}</td>
                            <td class=”text-end ${pnlClass}”>${h.pnl_pct >= 0 ? '+' : ''}${fmt(h.pnl_pct)}%</td>
                            <td class=”text-center text-nowrap”>${assetLinks(h.symbol)}</td>
                            <td class=”text-end pe-3”><button class=”btn btn-sm btn-outline-primary” title=”Research / Valuation” onclick=”openResearchModal('${symEsc}')”><i class=”bi bi-graph-up”></i></button></td>
                        </tr>`;
                };
                this._holdingsST = this._holdingsST || makeSortableTable({
                    table: document.getElementById('holdingsTable'),
                    columns: [
                        { key: 'symbol', type: 'text' }, { key: 'name', type: 'text' },
                        { key: 'asset_type', type: 'text', filter: 'select' }, { key: 'currency', type: 'text' },
                        { key: 'quantity', type: 'num' }, { key: 'avg_price', type: 'num' },
                        { key: 'current_price', type: 'num' }, { key: 'total_value_eur', type: 'num' },
                        { key: 'pnl_amount', type: 'num' }, { key: 'pnl_pct', type: 'num' },
                        { key: null }, { key: null },
                    ],
                    getRows: () => this._holdingsRows,
                    renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderHoldingRow).join('') : emptyMsg; },
                    prefsKey: 'holdings',
                });
                this._holdingsST.refresh();
            } catch (error) {
                console.error('Error loading holdings:', error);
                tableBody.innerHTML = '<tr><td colspan="12" class="text-center text-danger">Error loading holdings.</td></tr>';
            }

            // Populate the rebalancing target rows from current holdings' asset types
            setupRebalanceTargets(data.holdings || []);

            this.hideLoadingSpinners();
        },

        loadPortfoliosPage: async function() {
            const tableBody = document.querySelector('#portfoliosTable tbody');
            const footer = document.getElementById('portfoliosFooter');
            if (!tableBody) return;
            tableBody.innerHTML = '<tr><td colspan="7" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</td></tr>';
            if (footer) footer.innerHTML = '';
            try {
                // Portfolios list + per-portfolio EUR values (best-effort; values may be slow)
                const portfolios = await window.apiClient.getPortfolios();
                let values = { portfolios: [], total_value_eur: 0, total_cost_eur: 0, total_pnl_eur: 0 };
                try { values = await window.apiClient.getPortfolioValues(); } catch (e) { /* values optional */ }
                const valByName = {};
                (values.portfolios || []).forEach(v => { valByName[v.name] = v; });

                const eur = n => Fmt.amt('€' + Fmt.num(Math.round(n), 0, 0));
                const pnlCell = v => {
                    if (!v) return '<td class="text-end text-muted">—</td>';
                    const cls = v.pnl_eur >= 0 ? 'text-success' : 'text-danger';
                    const sign = v.pnl_eur >= 0 ? '+' : '';
                    return `<td class="text-end ${cls}">${sign}${eur(v.pnl_eur)} <small>(${sign}${v.pnl_pct}%)</small></td>`;
                };

                const esc = s => (s || '').replace(/'/g, "\\'");
                // Compact "first → last" date range, or "—"
                const range = (a, b) => {
                    if (!a && !b) return '<span class="text-muted">—</span>';
                    if (a === b) return Fmt.date(a);
                    return `${a ? Fmt.date(a) : '?'} <span class="text-muted">→</span> ${b ? Fmt.date(b) : '?'}`;
                };
                if (portfolios.length === 0) {
                    tableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No brokers yet. Click "Add Portfolio" to create one.</td></tr>';
                } else {
                    tableBody.innerHTML = portfolios.map(p => {
                        const v = valByName[p.name];
                        const site = p.website
                            ? ` <a href="${p.website}" target="_blank" rel="noopener" title="${p.website}${p.website_is_default ? ' (suggested)' : ''}" class="text-decoration-none"><i class="bi bi-box-arrow-up-right small ${p.website_is_default ? 'text-muted' : ''}"></i></a>`
                            : '';
                        const activity = `
                            <div class="small"><i class="bi bi-graph-up me-1 text-muted" title="Transactions"></i>${range(p.first_transaction_date, p.last_transaction_date)}</div>
                            <div class="small"><i class="bi bi-cash-stack me-1 text-muted" title="Cash deposits/withdrawals"></i>${range(p.first_booking_date, p.last_booking_date)}</div>`;
                        return `
                        <tr>
                            <td class="ps-3"><strong>${esc(p.name)}</strong>${site}</td>
                            <td>${p.base_currency || ''}</td>
                            <td class="text-end">${v ? eur(v.value_eur) : '<span class="text-muted">—</span>'}${v && Math.abs(v.cash_eur || 0) >= 1 ? `<div class="small text-muted" title="Cash balance (deposits − withdrawals + sells − buys + dividends)"><i class="bi bi-cash-coin me-1"></i>${eur(v.cash_eur)} cash</div>` : ''}</td>
                            ${pnlCell(v)}
                            <td>${activity}</td>
                            <td><small class="text-muted">${esc(p.description || '')}</small></td>
                            <td class="pe-3">
                                <button class="btn btn-sm btn-outline-primary me-1" title="Edit" onclick="editPortfolio(${p.id}, '${esc(p.name)}', '${p.base_currency || 'EUR'}', '${esc(p.description)}', '${esc(p.website)}')">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" title="Delete" onclick="deletePortfolio(${p.id}, '${esc(p.name)}')">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </td>
                        </tr>`;
                    }).join('');
                    if (footer && (values.total_value_eur || 0) > 0) {
                        const tp = values.total_pnl_eur;
                        const tcls = tp >= 0 ? 'text-success' : 'text-danger';
                        const tsign = tp >= 0 ? '+' : '';
                        const cash = values.total_cash_eur || 0;
                        const nw = values.total_networth_eur != null ? values.total_networth_eur : (values.total_value_eur + cash);
                        footer.innerHTML = `<tr class="fw-bold border-top">
                            <td class="ps-3">Total holdings</td><td></td>
                            <td class="text-end">${eur(values.total_value_eur)}</td>
                            <td class="text-end ${tcls}">${tsign}${eur(tp)}</td>
                            <td colspan="3"></td>
                        </tr>
                        <tr class="text-muted">
                            <td class="ps-3">+ Cash</td><td></td>
                            <td class="text-end">${eur(cash)}</td>
                            <td colspan="4"></td>
                        </tr>
                        <tr class="fw-bold">
                            <td class="ps-3">Net worth</td><td></td>
                            <td class="text-end">${eur(nw)}</td>
                            <td colspan="4"></td>
                        </tr>`;
                    }
                }
            } catch (err) {
                tableBody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error loading portfolios.</td></tr>';
            }
        },

        loadAnalyticsPage: function() {
            // Tabbed + lazy: only the active tab's sections load, so opening
            // Analytics no longer fires every endpoint at once. Diversification
            // stays on its own on-demand button inside the Risk tab (its
            // per-holding Yahoo lookup is slow even though now cached/threaded).
            setupAnalyticsTabs();
        },

        loadWatchlistPage: function() {
            loadWatchlist();
        },

        loadGoalsPage: function() {
            loadGoals();
        }
    };
}

// ---------------------------------------------------------------------------
// Help & explainability
// ---------------------------------------------------------------------------

// Open the reusable page-help modal, populated from window.PAGE_HELP[key].
function showPageHelp(key) {
    const help = (window.PAGE_HELP || {})[key];
    if (!help) return;
    const titleEl = document.getElementById('pageHelpTitle');
    const bodyEl = document.getElementById('pageHelpBody');
    if (titleEl) titleEl.textContent = help.title || 'Help';
    if (bodyEl) bodyEl.innerHTML = help.body || '';
    const modalEl = document.getElementById('pageHelpModal');
    if (modalEl && window.bootstrap && bootstrap.Modal) {
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
}
window.showPageHelp = showPageHelp;

// Render the Help page: an accordion of per-page guides (from PAGE_HELP) plus
// a glossary of the abbreviations used across the app.
const HELP_GLOSSARY = [
    ['FIRE', 'Financial Independence, Retire Early — a savings/investing target where assets cover your living costs.'],
    ['IRR', 'Internal Rate of Return — the money-weighted annual return that accounts for the timing and size of your buys/sells.'],
    ['TWR', 'Time-Weighted Return — return that strips out the effect of deposits/withdrawals, for comparing to a benchmark.'],
    ['FIFO', 'First-In, First-Out — the oldest shares are sold first when computing cost basis and realised gains.'],
    ['HHI', 'Herfindahl-Hirschman Index — a 0–10000 concentration score; lower means more diversified.'],
    ['IRPF', 'Impuesto sobre la Renta de las Personas Físicas — Spanish personal income tax; investment income sits in the “savings base”.'],
    ['PDT', 'Portfolio Dividend Tracker — the spreadsheet format this app imports/exports and syncs with Google Sheets.'],
    ['GBM', 'Geometric Brownian Motion — the stochastic model the Wealth Simulator uses to project future value.'],
    ['Yield on cost', 'Trailing-12-month dividends from a position divided by what you paid for it.'],
    ['Max drawdown', 'The largest peak-to-trough drop in portfolio value over the recorded history.'],
    ['ETF / Index', 'ETF = exchange-traded fund; Index = index mutual fund (e.g. Indexa Capital holdings). Tracked as separate asset types.'],
    ['GBX', 'UK pence — some London-listed prices are quoted in pence (÷100 for GBP).'],
];

function renderHelpPage() {
    const acc = document.getElementById('helpAccordion');
    if (acc && window.PAGE_HELP) {
        acc.innerHTML = Object.entries(window.PAGE_HELP).map(([key, h], i) => `
            <div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#help_${key}">
                        ${h.title || key}
                    </button>
                </h2>
                <div id="help_${key}" class="accordion-collapse collapse" data-bs-parent="#helpAccordion">
                    <div class="accordion-body">${h.body || ''}</div>
                </div>
            </div>`).join('');
    }
    const gl = document.getElementById('helpGlossary');
    if (gl) {
        gl.innerHTML = HELP_GLOSSARY.map(([term, def]) =>
            `<div class="mb-2"><strong>${term}</strong><div class="text-muted">${def}</div></div>`
        ).join('');
    }
}
window.renderHelpPage = renderHelpPage;

// Curated external financial resources, rendered as a card grid.
const RESOURCE_LINKS = [
    { cat: 'Research & analysis', items: [
        ['Yahoo Finance', 'https://finance.yahoo.com', 'Quotes, fundamentals, news — the app\'s own price source.', 'bi-graph-up'],
        ['Simply Wall St', 'https://simplywall.st', 'Visual company analysis, fair value & snowflake.', 'bi-pie-chart'],
        ['Morningstar', 'https://www.morningstar.com', 'Fund/ETF ratings and analysis.', 'bi-star'],
        ['Finviz', 'https://finviz.com', 'Stock screener, heatmaps and news.', 'bi-grid-3x3'],
        ['Koyfin', 'https://www.koyfin.com', 'Charts, dashboards and macro data.', 'bi-bar-chart'],
    ]},
    { cat: 'Tools', items: [
        ['Portfolio Dividend Tracker', 'https://app.portfoliodividendtracker.com/portfolio', 'Dividend tracking with broker auto-imports (your PDT account).', 'bi-cash-coin'],
        ['Portfolio Visualizer', 'https://www.portfoliovisualizer.com', 'Backtesting, asset allocation and Monte Carlo.', 'bi-clipboard-data'],
        ['Curvo', 'https://curvo.eu', 'European index-portfolio builder & backtester.', 'bi-graph-up-arrow'],
        ['TradingView', 'https://www.tradingview.com', 'Advanced charting and ideas.', 'bi-graph-up-arrow'],
        ['Google Finance', 'https://www.google.com/finance', 'Quick quotes and watchlists.', 'bi-google'],
    ]},
    { cat: 'ETFs & funds', items: [
        ['JustETF', 'https://www.justetf.com', 'European ETF database and screener.', 'bi-collection'],
        ['ETF.com', 'https://www.etf.com', 'ETF research and comparisons.', 'bi-collection'],
    ]},
    { cat: 'Brokers', items: [
        ['Indexa Capital', 'https://indexacapital.com', 'Automated index-fund portfolios (ES).', 'bi-bank'],
        ['MyInvestor', 'https://myinvestor.es', 'Spanish broker / neobank.', 'bi-bank'],
        ['Coinbase', 'https://www.coinbase.com', 'Crypto exchange.', 'bi-currency-bitcoin'],
        ['DEGIRO', 'https://www.degiro.com', 'Low-cost European broker.', 'bi-bank'],
        ['Trade Republic', 'https://traderepublic.com', 'Mobile broker (EU).', 'bi-bank'],
        ['Interactive Brokers', 'https://www.interactivebrokers.com', 'Global multi-asset broker.', 'bi-bank'],
    ]},
    { cat: 'Books', items: [
        ['One Up On Wall Street', 'https://en.wikipedia.org/wiki/One_Up_on_Wall_Street', 'Peter Lynch — invest in what you know. (read)', 'bi-bookmark-check'],
        ['The Essays of Warren Buffett', 'https://www.google.com/search?q=The+Essays+of+Warren+Buffett+book', 'Buffett on business & investing, by L. Cunningham. (read)', 'bi-bookmark-check'],
        ['The Little Book of Common Sense Investing', 'https://en.wikipedia.org/wiki/The_Little_Book_of_Common_Sense_Investing', 'John Bogle — low-cost index investing. (read)', 'bi-bookmark-check'],
        ['The Wealthy Barber', 'https://en.wikipedia.org/wiki/The_Wealthy_Barber', 'David Chilton — personal-finance basics. (read)', 'bi-bookmark-check'],
        ['The Intelligent Investor', 'https://en.wikipedia.org/wiki/The_Intelligent_Investor', 'Benjamin Graham — value-investing classic.', 'bi-book'],
        ['A Random Walk Down Wall Street', 'https://en.wikipedia.org/wiki/A_Random_Walk_Down_Wall_Street', 'Burton Malkiel — the case for index investing.', 'bi-book'],
        ['The Psychology of Money', 'https://en.wikipedia.org/wiki/The_Psychology_of_Money', 'Morgan Housel — behaviour & wealth.', 'bi-book'],
    ]},
    { cat: 'Courses & learning', items: [
        ['Khan Academy — Finance', 'https://www.khanacademy.org/economics-finance-domain/core-finance', 'Free finance & capital-markets lessons.', 'bi-mortarboard'],
        ['Coursera — Financial Markets (Yale)', 'https://www.coursera.org/learn/financial-markets-global', 'Robert Shiller\'s markets course.', 'bi-mortarboard'],
        ['Bogleheads Wiki', 'https://www.bogleheads.org/wiki/Main_Page', 'Index-investing knowledge base & forum.', 'bi-mortarboard'],
        ['Investopedia', 'https://www.investopedia.com', 'Definitions, tutorials and explainers.', 'bi-mortarboard'],
    ]},
];
function renderResourcesPage() {
    const grid = document.getElementById('resourcesGrid');
    if (!grid) return;
    grid.innerHTML = RESOURCE_LINKS.map(group => `
        <div class="col-12 col-lg-6">
            <div class="card h-100">
                <div class="card-header fw-semibold">${group.cat}</div>
                <div class="list-group list-group-flush">
                    ${group.items.map(([name, url, desc, icon]) => `
                        <a class="list-group-item list-group-item-action d-flex align-items-start gap-2" href="${url}" target="_blank" rel="noopener">
                            <i class="bi ${icon} mt-1"></i>
                            <span><span class="fw-semibold">${name}</span> <i class="bi bi-box-arrow-up-right small text-muted"></i><br><span class="small text-muted">${desc}</span></span>
                        </a>`).join('')}
                </div>
            </div>
        </div>`).join('');
}
window.renderResourcesPage = renderResourcesPage;
