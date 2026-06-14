// pfm_features.js — part of the portfolio_debug.js split.
// Features: watchlist, goals, chat, portfolios, import/export, forecast, rebalance, research, settings — plus the DOMContentLoaded bootstrap (must stay last in load order).
// Classic script (no build step): these files share one global scope
// and MUST load in this order: pfm_core, pfm_pages, pfm_analytics,
// pfm_features. See index.html.


function setupWatchlistPage() {
    const form = document.getElementById('addWatchlistForm');
    const refreshBtn = document.getElementById('refreshWatchlist');
    const status = document.getElementById('watchlistFormStatus');
    if (refreshBtn) refreshBtn.addEventListener('click', loadWatchlist);
    if (!form) return;

    // Autocomplete for the symbol input — suggestions come from holdings + all assets.
    // Fetched asynchronously so the form is immediately usable, suggestions appear once ready.
    let watchlistSymbolSuggestions = [];
    const symInput   = document.getElementById('addWatchlistSymbol');
    const symSuggest = document.getElementById('watchlistSymbolSuggest');
    if (symInput && symSuggest) {
        AssetSearch.buildAutocomplete(symInput, symSuggest, {
            getSuggestions: () => watchlistSymbolSuggestions,
            clearOnEscape: false,
        });
        (async () => {
            try {
                const [holdingsData, assets] = await Promise.all([
                    window.apiClient.getHoldings().catch(() => ({ holdings: [] })),
                    window.apiClient.getAssets().catch(() => []),
                ]);
                const map = new Map();
                (holdingsData.holdings || []).forEach(h => {
                    if (h.symbol) map.set(h.symbol, AssetSearch.enrich(h.symbol, h.name));
                });
                (assets || []).forEach(a => {
                    if (a.symbol && !map.has(a.symbol)) map.set(a.symbol, AssetSearch.enrich(a.symbol, a.name));
                });
                watchlistSymbolSuggestions = [...map.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
            } catch (e) { /* non-fatal — autocomplete just won't suggest */ }
        })();
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const symbol = document.getElementById('addWatchlistSymbol').value.trim();
        if (!symbol) return;
        const buyBelowRaw = document.getElementById('addWatchlistBuyBelow').value;
        const notes = document.getElementById('addWatchlistNotes').value.trim();
        const body = { symbol };
        if (buyBelowRaw !== '') body.buy_below = parseFloat(buyBelowRaw);
        if (notes) body.notes = notes;
        const btn = document.getElementById('addWatchlistBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Adding…';
        if (status) status.innerHTML = '';
        try {
            await window.apiClient.addWatchlist(body);
            form.reset();
            loadWatchlist();
        } catch (err) {
            if (status) status.innerHTML = `<span class="text-danger">Error: ${err.message}</span>`;
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>Add';
        }
    });
}

// ---------------------------------------------------------------------------
// Goals page
// ---------------------------------------------------------------------------

async function loadGoals() {
    const list = document.getElementById('goalsList');
    if (!list) return;
    list.innerHTML = '<div class="col-12 text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…</div>';
    try {
        const goals = await window.apiClient.getGoals();
        if (!Array.isArray(goals) || goals.length === 0) {
            list.innerHTML = '<div class="col-12"><p class="text-muted text-center py-4 mb-0">No goals yet. Add one above to start tracking your progress.</p></div>';
            return;
        }
        list.innerHTML = goals.map(g => {
            const progress = Math.max(0, Math.min(100, parseFloat(g.progress_pct || 0)));
            const onTrack = !!g.on_track;
            const trackBadge = onTrack
                ? '<span class="badge bg-success"><i class="bi bi-check-lg me-1"></i>On track</span>'
                : '<span class="badge bg-danger"><i class="bi bi-x-lg me-1"></i>Off track</span>';
            const barCls = onTrack ? 'bg-success' : 'bg-warning';
            const monthsLeft = g.months_left != null ? g.months_left : '—';
            const shortfall = parseFloat(g.shortfall_eur || 0);
            const reqMonthly = parseFloat(g.required_monthly_eur || 0);
            const offTrackNote = !onTrack
                ? `<div class="alert alert-warning py-2 small mb-0 mt-2">
                       <i class="bi bi-exclamation-triangle me-1"></i>
                       Need <strong>${anFmtEur2(reqMonthly)}/month</strong> to reach this goal
                       ${shortfall > 0 ? `(projected shortfall ${anFmtEur2(shortfall)})` : ''}.
                   </div>`
                : '';
            return `
                <div class="col-12 col-lg-6">
                    <div class="card h-100">
                        <div class="card-body">
                            <div class="d-flex justify-content-between align-items-start mb-2">
                                <h5 class="card-title mb-0"><i class="bi bi-bullseye me-2 text-primary"></i>${esc(g.name || 'Goal')}</h5>
                                <div class="d-flex align-items-center gap-2">
                                    ${trackBadge}
                                    <button class="btn btn-sm btn-outline-danger" onclick="window.deleteGoalRow(${g.id}, '${(g.name || '').replace(/'/g, "\\'")}')" title="Delete goal">
                                        <i class="bi bi-trash"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="d-flex justify-content-between small text-muted mb-1">
                                <span>${anFmtEur(g.current_networth_eur)} of ${anFmtEur(g.target_amount_eur)}</span>
                                <span>${progress.toFixed(1)}%</span>
                            </div>
                            <div class="progress mb-3" style="height:12px;">
                                <div class="progress-bar ${barCls}" role="progressbar" style="width:${progress}%;"></div>
                            </div>
                            <div class="row g-2 small">
                                <div class="col-6">
                                    <div class="text-muted">Target date</div>
                                    <div class="fw-semibold">${g.target_date ? Fmt.date(g.target_date) : '—'}</div>
                                </div>
                                <div class="col-6">
                                    <div class="text-muted">Months left</div>
                                    <div class="fw-semibold">${monthsLeft}</div>
                                </div>
                                <div class="col-6">
                                    <div class="text-muted">Monthly contribution</div>
                                    <div class="fw-semibold">${anFmtEur(g.monthly_contribution_eur)}</div>
                                </div>
                                <div class="col-6">
                                    <div class="text-muted">Projected at target</div>
                                    <div class="fw-semibold">${anFmtEur(g.projected_value_eur)}</div>
                                </div>
                            </div>
                            ${offTrackNote}
                        </div>
                    </div>
                </div>`;
        }).join('');
    } catch (err) {
        list.innerHTML = `<div class="col-12"><p class="text-danger small py-3 mb-0">Error loading goals: ${err.message}</p></div>`;
    }
}

window.deleteGoalRow = async function(id, name) {
    if (!confirm(`Delete goal "${name}"?`)) return;
    try {
        await window.apiClient.deleteGoal(id);
        loadGoals();
    } catch (err) {
        alert('Error deleting goal: ' + err.message);
    }
};

function setupGoalsPage() {
    const form = document.getElementById('addGoalForm');
    const refreshBtn = document.getElementById('refreshGoals');
    const status = document.getElementById('goalFormStatus');
    if (refreshBtn) refreshBtn.addEventListener('click', loadGoals);
    if (!form) return;
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            name: document.getElementById('addGoalName').value.trim(),
            target_amount_eur: parseFloat(document.getElementById('addGoalTarget').value),
            target_date: document.getElementById('addGoalDate').value,
            monthly_contribution_eur: parseFloat(document.getElementById('addGoalMonthly').value),
            expected_return_pct: parseFloat(document.getElementById('addGoalReturn').value)
        };
        const btn = document.getElementById('addGoalBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Adding…';
        if (status) status.innerHTML = '';
        try {
            await window.apiClient.createGoal(body);
            form.reset();
            document.getElementById('addGoalReturn').value = '6';
            loadGoals();
        } catch (err) {
            if (status) status.innerHTML = `<span class="text-danger">Error: ${err.message}</span>`;
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>Add';
        }
    });
}

// ---------------------------------------------------------------------------
// Navigation Manager
// ---------------------------------------------------------------------------
function createNavigationManager() {
    return {
        currentPage: 'dashboard',
        showPage: function(pageName) {
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'holdingsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage', 'helpPage', 'versionPage', 'aboutPage', 'resourcesPage', 'networthPage', 'diagnosticsPage'];
            pages.forEach(pageId => {
                const page = document.getElementById(pageId);
                if (page) page.style.display = 'none';
            });

            const targetPage = document.getElementById(pageName + 'Page');
            if (targetPage) {
                targetPage.style.display = 'block';
                this.loadPageData(pageName);
            }

            // Clear active state on all nav links (sidebar + offcanvas) and set
            // it on every link for this page (there are two copies of each).
            document.querySelectorAll('.sidebar-nav-link, .nav-link').forEach(
                link => link.classList.remove('active')
            );
            document.querySelectorAll(`[data-page="${pageName}"]`).forEach(
                link => link.classList.add('active')
            );
            // Make sure the active item's section is expanded so it's visible.
            if (typeof expandNavSectionFor === 'function') expandNavSectionFor(pageName);

            this.currentPage = pageName;
        },

        loadPageData: function(pageName) {
            switch(pageName) {
                case 'dashboard':    window.pageManager.loadDashboardPage(); break;
                case 'assets':       window.pageManager.loadAssetsPage(); break;
                case 'transactions': window.pageManager.loadTransactionsPage(); break;
                case 'holdings':     window.pageManager.loadHoldingsPage(); break;
                case 'analytics':    window.pageManager.loadAnalyticsPage(); break;
                case 'watchlist':    window.pageManager.loadWatchlistPage(); break;
                case 'goals':        window.pageManager.loadGoalsPage(); break;
                case 'chat':         break;
                case 'importexport': break;
                case 'portfolios':   window.pageManager.loadPortfoliosPage(); break;
                case 'research':     if (window.loadResearchPage) window.loadResearchPage(); break;
                case 'forecast':     if (window._fcLoadStartValue) window._fcLoadStartValue(); break;
                case 'help':         if (window.renderHelpPage) window.renderHelpPage(); break;
                case 'version':      break;
                case 'about':        break;
                case 'resources':    if (window.renderResourcesPage) window.renderResourcesPage(); break;
                case 'networth':     if (window.loadNetworthPage) window.loadNetworthPage(); break;
                case 'diagnostics':  if (window.loadDiagnosticsPage) window.loadDiagnosticsPage(); break;
            }
        },

        setupNavigation: function() {
            document.addEventListener('click', (e) => {
                const navLink = e.target.closest('[data-page]');
                if (navLink) {
                    e.preventDefault();
                    const page = navLink.dataset.page;
                    this.showPage(page);
                }
            });
        }
    };
}

// ---------------------------------------------------------------------------
// Auth Manager
// ---------------------------------------------------------------------------
function createAuthManager() {
    return {
        isAuthenticated: false,
        showLoginModal: function() {
            const modal = document.getElementById('loginModal');
            if (modal && typeof bootstrap !== 'undefined') {
                const modalInstance = new bootstrap.Modal(modal);
                modalInstance.show();
            }
        },
        hideLoginModal: function() {
            const modal = document.getElementById('loginModal');
            if (modal && typeof bootstrap !== 'undefined') {
                const modalInstance = bootstrap.Modal.getInstance(modal);
                if (modalInstance) modalInstance.hide();
            }
        },
        showDashboard: function() {
            // Only toggle the shell — sidebar/topbar visibility is CSS/media-query
            // controlled, so we must NOT set inline display on #mainNav (it would
            // override the mobile media query that hides the desktop sidebar).
            const shell = document.getElementById('appShell');
            if (shell) {
                shell.style.display = "";
            } else {
                // Legacy fallback for pre-redesign markup
                const nav = document.getElementById("mainNav");
                const content = document.getElementById("mainContent");
                if (nav) nav.style.display = "block";
                if (content) content.style.display = "block";
            }
            window.navigationManager.showPage((window.PREFS && window.PREFS.landingPage) || 'dashboard');
            this.isAuthenticated = true;
        },
        setupLogout: function() {
            // Wire both the desktop and mobile-offcanvas logout buttons
            const buttons = [
                document.getElementById('logoutBtn'),
                document.getElementById('logoutBtnOffcanvas'),
            ].filter(Boolean);
            buttons.forEach((btn) => {
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    window.apiClient.clearApiKey();
                    this.isAuthenticated = false;
                    const shell = document.getElementById('appShell');
                    if (shell) {
                        shell.style.display = 'none';
                    } else {
                        const nav = document.getElementById('mainNav');
                        const content = document.getElementById('mainContent');
                        if (nav) nav.style.display = 'none';
                        if (content) content.style.display = 'none';
                    }
                    this.showLoginModal();
                });
            });
        },

        setupLoginForm: function() {
            const form = document.getElementById('loginForm');
            if (!form) return;

            const toggleBtn = document.getElementById('toggleApiKey');
            if (toggleBtn) {
                toggleBtn.addEventListener('click', () => {
                    const input = document.getElementById('apiKey');
                    const icon = toggleBtn.querySelector('i');
                    const isPassword = input.getAttribute('type') !== 'text';
                    input.setAttribute('type', isPassword ? 'text' : 'password');
                    icon.className = isPassword ? 'bi bi-eye-slash' : 'bi bi-eye';
                });
            }

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const input = document.getElementById('apiKey');
                const apiKey = input.value.trim();

                if (!apiKey) {
                    alert('Please enter an API key');
                    return;
                }

                const isValid = await window.apiClient.validateApiKey(apiKey);
                if (isValid) {
                    window.apiClient.setApiKey(apiKey);
                    this.hideLoginModal();
                    this.showDashboard();
                } else {
                    alert('Invalid API key. Please try again.');
                }
            });

            // Shared helper: take an API key, persist it, enter the app
            const self = this;
            async function enterWithKey(key) {
                window.apiClient.setApiKey(key);
                localStorage.setItem('apiKey', key);
                self.hideLoginModal();
                self.showDashboard();
            }

            // Username + password login
            const pwForm = document.getElementById('passwordLoginForm');
            if (pwForm) {
                pwForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const errEl = document.getElementById('passwordLoginError');
                    errEl.textContent = '';
                    const u = document.getElementById('loginUsername').value.trim();
                    const p = document.getElementById('loginPassword').value;
                    try {
                        const key = await window.apiClient.loginWithPassword(u, p);
                        await enterWithKey(key);
                    } catch (err) {
                        errEl.textContent = err.message;
                    }
                });
            }

            // First-time account creation → auto sign-in
            const regForm = document.getElementById('registerForm');
            if (regForm) {
                regForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const errEl = document.getElementById('registerError');
                    errEl.textContent = '';
                    const u = document.getElementById('regUsername').value.trim();
                    const email = document.getElementById('regEmail').value.trim();
                    const p = document.getElementById('regPassword').value;
                    try {
                        await window.apiClient.registerUser(u, email, p);
                        const key = await window.apiClient.loginWithPassword(u, p);
                        await enterWithKey(key);
                    } catch (err) {
                        errEl.textContent = err.message;
                    }
                });
            }
        }
    };
}

// ---------------------------------------------------------------------------
// Chat page
// ---------------------------------------------------------------------------

function setupChatPage() {
    const messagesEl = document.getElementById('chatMessages');
    const inputEl    = document.getElementById('chatInput');
    const sendBtn    = document.getElementById('chatSendBtn');
    const extractBtn = document.getElementById('chatExtractBtn');
    if (!messagesEl || !inputEl) return;

    // Stable session ID for this page load
    const sessionId = 'web-' + Math.random().toString(36).slice(2, 10);

    function scrollBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function removeEmpty() {
        const empty = document.getElementById('chatEmpty');
        if (empty) empty.remove();
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function appendMessage(role, text) {
        removeEmpty();
        const isUser = role === 'user';
        // Assistant replies are markdown → render to HTML; user input is escaped.
        let body, extraStyle;
        if (isUser) {
            body = escapeHtml(text);
            extraStyle = 'white-space:pre-wrap;';
        } else if (window.marked) {
            body = marked.parse(String(text || ''), { breaks: true });
            extraStyle = '';
        } else {
            body = escapeHtml(text);
            extraStyle = 'white-space:pre-wrap;';
        }
        const div = document.createElement('div');
        div.className = `d-flex ${isUser ? 'justify-content-end' : 'justify-content-start'}`;
        div.innerHTML = `
            <div class="px-3 py-2 rounded-3 chat-bubble ${isUser ? 'bg-primary text-white' : 'chat-bubble-assistant border'}"
                 style="max-width:80%;word-break:break-word;${extraStyle}">${body}</div>`;
        messagesEl.appendChild(div);
        scrollBottom();
    }

    function appendTransactionCard(transactions) {
        if (!transactions || transactions.length === 0) return;
        removeEmpty();

        const rows = transactions.map((tx, i) => `
            <tr>
                <td><input class="form-check-input chat-tx-select" type="checkbox" checked data-idx="${i}"></td>
                <td><input type="date" class="form-control form-control-sm chat-tx-date" data-idx="${i}" value="${escapeForAttr(tx.date || '')}"></td>
                <td>
                    <input type="text" class="form-control form-control-sm chat-tx-symbol mb-1" data-idx="${i}" value="${escapeForAttr(tx.symbol || '')}" placeholder="Symbol">
                    <input type="text" class="form-control form-control-sm chat-tx-name" data-idx="${i}" value="${escapeForAttr(tx.asset_name || '')}" placeholder="Name">
                </td>
                <td>
                    <select class="form-select form-select-sm chat-tx-type" data-idx="${i}">
                        ${['buy','sell','dividend','interest'].map(t => `<option value="${t}" ${(tx.tx_type||'') === t ? 'selected' : ''}>${t.toUpperCase()}</option>`).join('')}
                    </select>
                </td>
                <td><input type="number" class="form-control form-control-sm chat-tx-qty" data-idx="${i}" value="${tx.quantity || 0}" step="any" min="0"></td>
                <td>
                    <input type="number" class="form-control form-control-sm chat-tx-price mb-1" data-idx="${i}" value="${tx.price || 0}" step="any" min="0">
                    <input type="text" class="form-control form-control-sm chat-tx-currency" data-idx="${i}" value="${escapeForAttr(tx.currency || 'EUR')}" maxlength="3" style="width:5ch">
                </td>
                <td><input type="number" class="form-control form-control-sm chat-tx-fees" data-idx="${i}" value="${parseFloat(tx.fees)||0}" step="any" min="0"></td>
            </tr>`).join('');

        const card = document.createElement('div');
        card.className = 'align-self-start w-100';
        card.innerHTML = `
            <div class="card border-success">
                <div class="card-header bg-success text-white py-1 small">
                    <i class="bi bi-magic me-1"></i>Found ${transactions.length} transaction(s) — select which to import
                </div>
                <div class="card-body p-2">
                    <div class="table-responsive">
                        <table class="table table-sm mb-2">
                            <thead><tr><th></th><th>Date</th><th>Asset</th><th>Type</th><th>Qty</th><th>Price / Cur</th><th>Fees</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                    <button class="btn btn-success btn-sm chat-import-btn">
                        <i class="bi bi-check-lg me-1"></i>Import selected
                    </button>
                </div>
            </div>`;

        card.querySelector('.chat-import-btn').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const checkedIdxs = Array.from(card.querySelectorAll('.chat-tx-select:checked'))
                .map(cb => parseInt(cb.dataset.idx));
            if (checkedIdxs.length === 0) { alert('Nothing selected.'); return; }

            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';
            const f = (cls, i) => card.querySelector(`.${cls}[data-idx="${i}"]`);
            const normalized = checkedIdxs.map(i => ({
                symbol: f('chat-tx-symbol', i).value,
                name: f('chat-tx-name', i).value || f('chat-tx-symbol', i).value,
                asset_type: 'stock',
                tx_type: f('chat-tx-type', i).value,
                date: f('chat-tx-date', i).value,
                quantity: parseFloat(f('chat-tx-qty', i).value) || 0,
                price: parseFloat(f('chat-tx-price', i).value) || 0,
                currency: (f('chat-tx-currency', i).value || 'EUR').toUpperCase(),
                fees: parseFloat(f('chat-tx-fees', i).value) || 0,
                notes: transactions[i] ? (transactions[i].raw_text || '') : ''
            }));
            try {
                const result = await window.apiClient.saveImportedTransactions(normalized);
                btn.remove();
                appendMessage('assistant', `Imported ${result.saved} transaction(s).${result.errors.length ? '\n' + result.errors.join('\n') : ''}`);
            } catch (err) {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Import selected';
                appendMessage('assistant', 'Save failed: ' + err.message);
            }
        });

        messagesEl.appendChild(card);
        scrollBottom();
    }

    async function doSend() {
        const text = inputEl.value.trim();
        if (!text) return;
        inputEl.value = '';
        appendMessage('user', text);

        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const data = await window.apiClient.sendChat(text, sessionId);
            appendMessage('assistant', data.answer || '(no response)');
        } catch (err) {
            appendMessage('assistant', 'Error: ' + err.message);
        } finally {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<i class="bi bi-send me-1"></i>Send';
        }
    }

    async function doExtract() {
        const text = inputEl.value.trim();
        if (!text) { alert('Paste a broker statement first.'); return; }
        inputEl.value = '';
        appendMessage('user', '[Broker statement — extracting transactions…]');

        extractBtn.disabled = true;
        extractBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const data = await window.apiClient.extractTransactions(text);
            if (!data.transactions || data.transactions.length === 0) {
                appendMessage('assistant', 'No transactions could be extracted from that text.');
            } else {
                appendTransactionCard(data.transactions);
            }
        } catch (err) {
            appendMessage('assistant', 'Extraction error: ' + err.message);
        } finally {
            extractBtn.disabled = false;
            extractBtn.innerHTML = '<i class="bi bi-magic me-1"></i>Extract &amp; Import';
        }
    }

    sendBtn.addEventListener('click', doSend);
    extractBtn.addEventListener('click', doExtract);
    inputEl.addEventListener('keydown', e => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) doSend();
    });
}

// ---------------------------------------------------------------------------
// Transaction edit / delete
// ---------------------------------------------------------------------------

function setupEditTransactionModal() {
    const form = document.getElementById('editTransactionForm');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = parseInt(document.getElementById('editTxId').value);
        const portfolioRaw = document.getElementById('editTxPortfolio').value;
        const data = {
            transaction_date: document.getElementById('editTxDate').value,
            transaction_type: document.getElementById('editTxType').value,
            quantity: parseFloat(document.getElementById('editTxQty').value),
            price: parseFloat(document.getElementById('editTxPrice').value),
            fees: parseFloat(document.getElementById('editTxFees').value) || 0,
            description: document.getElementById('editTxNotes').value || null,
        };
        if (portfolioRaw) data.portfolio_id = parseInt(portfolioRaw);
        try {
            await window.apiClient.updateTransaction(id, data);
            bootstrap.Modal.getInstance(document.getElementById('editTransactionModal')).hide();
            window.pageManager.loadTransactionsPage();
        } catch (err) {
            alert('Error updating transaction: ' + err.message);
        }
    });
}

window.openEditTransaction = async function(id, date, type, qty, price, fees, portfolioId, notes) {
    document.getElementById('editTxId').value    = id;
    document.getElementById('editTxDate').value  = date;
    document.getElementById('editTxType').value  = type;
    document.getElementById('editTxQty').value   = qty;
    document.getElementById('editTxPrice').value = price;
    document.getElementById('editTxFees').value  = fees;
    document.getElementById('editTxNotes').value = notes;

    // Populate portfolio dropdown
    const sel = document.getElementById('editTxPortfolio');
    sel.innerHTML = '<option value="">— none —</option>';
    const portfolios = await window.apiClient.getPortfolios();
    portfolios.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === portfolioId) opt.selected = true;
        sel.appendChild(opt);
    });

    new bootstrap.Modal(document.getElementById('editTransactionModal')).show();
};

window.confirmDeleteTransaction = async function(id, symbol) {
    if (!confirm(`Delete this ${symbol} transaction? This cannot be undone.`)) return;
    try {
        await window.apiClient.deleteTransaction(id);
        window.pageManager.loadTransactionsPage();
    } catch (err) {
        alert('Error deleting transaction: ' + err.message);
    }
};

window.confirmDeleteBooking = async function(id) {
    if (!confirm('Delete this cash booking? This cannot be undone.')) return;
    try {
        await window.apiClient.deleteBooking(id);
        window.pageManager.loadTransactionsPage();
    } catch (err) {
        alert('Error deleting booking: ' + err.message);
    }
};

// ---------------------------------------------------------------------------
// Portfolios page
// ---------------------------------------------------------------------------

function setupPortfoliosPage() {
    const addBtn = document.getElementById('addPortfolioBtn');
    const form   = document.getElementById('portfolioForm');
    const modal  = document.getElementById('portfolioModal');
    if (!addBtn || !form || !modal) return;

    const bsModal = new bootstrap.Modal(modal);

    addBtn.addEventListener('click', () => {
        document.getElementById('portfolioModalTitle').textContent = 'Add Portfolio';
        document.getElementById('portfolioEditId').value = '';
        document.getElementById('portfolioName').value = '';
        document.getElementById('portfolioCurrency').value = 'EUR';
        document.getElementById('portfolioDescription').value = '';
        document.getElementById('portfolioWebsite').value = '';
        bsModal.show();
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id   = document.getElementById('portfolioEditId').value;
        const data = {
            name: document.getElementById('portfolioName').value.trim(),
            base_currency: document.getElementById('portfolioCurrency').value.trim() || 'EUR',
            description: document.getElementById('portfolioDescription').value.trim() || null,
            website: document.getElementById('portfolioWebsite').value.trim() || null,
        };
        try {
            if (id) {
                await window.apiClient.updatePortfolio(parseInt(id), data);
            } else {
                await window.apiClient.createPortfolio(data);
            }
            bsModal.hide();
            window.pageManager.loadPortfoliosPage();
            // Invalidate portfolio dropdown cache on transactions page
            const filter = document.getElementById('txPortfolioFilter');
            if (filter) { filter.innerHTML = '<option value="">All Portfolios</option>'; }
        } catch (err) {
            alert('Error saving portfolio: ' + err.message);
        }
    });
}

window.setAssetPrice = async function(id, symbol, currency) {
    const val = prompt(`Set a manual price for ${symbol}${currency ? ' (' + currency + ')' : ''}.\nThe daily price update will stop overwriting it.`);
    if (val === null) return;
    const price = parseFloat(val);
    if (!(price > 0)) { alert('Please enter a positive number.'); return; }
    try {
        await window.apiClient.setAssetPrice(id, price);
        window.pageManager.loadAssetsPage();
    } catch (e) {
        alert('Error setting price: ' + e.message);
    }
};

window.editPortfolio = function(id, name, currency, description, website) {
    document.getElementById('portfolioModalTitle').textContent = 'Edit Portfolio';
    document.getElementById('portfolioEditId').value           = id;
    document.getElementById('portfolioName').value             = name;
    document.getElementById('portfolioCurrency').value         = currency;
    document.getElementById('portfolioDescription').value      = description === 'null' ? '' : (description || '');
    document.getElementById('portfolioWebsite').value          = website === 'null' ? '' : (website || '');
    new bootstrap.Modal(document.getElementById('portfolioModal')).show();
};

window.deletePortfolio = async function(id, name) {
    if (!confirm(`Delete portfolio "${name}"?\n\nTransactions will be kept but unlinked from this portfolio.`)) return;
    try {
        await window.apiClient.deletePortfolio(id);
        window.pageManager.loadPortfoliosPage();
        const filter = document.getElementById('txPortfolioFilter');
        if (filter) { filter.innerHTML = '<option value="">All Portfolios</option>'; }
    } catch (err) {
        alert('Error deleting portfolio: ' + err.message);
    }
};

window.clearPortfolioTransactions = async function(id, name) {
    const modal = document.getElementById('clearTransactionsModal');
    const bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    const nameEl = document.getElementById('clearPortfolioName');
    const checkbox = document.getElementById('clearConfirmCheck');
    const confirmBtn = document.getElementById('clearTransactionsConfirmBtn');
    const backupBtn = document.getElementById('clearModalBackupBtn');

    // Reset state
    nameEl.textContent = name;
    checkbox.checked = false;
    confirmBtn.disabled = true;

    checkbox.onchange = () => { confirmBtn.disabled = !checkbox.checked; };

    backupBtn.onclick = async () => {
        try {
            await window.apiClient.downloadBlob(
                window.apiClient.baseURL + '/api/v1/export/backup',
                'pfm-backup.db'
            );
        } catch (err) { alert('Backup error: ' + err.message); }
    };

    confirmBtn.onclick = async () => {
        confirmBtn.disabled = true;
        try {
            const resp = await fetch(
                window.apiClient.baseURL + `/api/v1/portfolios/${id}/transactions`,
                { method: 'DELETE', headers: { 'X-API-Key': window.apiClient.apiKey } }
            );
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || resp.statusText);
            bsModal.hide();
            checkbox.checked = false;
            window.showToast(`Deleted ${data.deleted} transaction${data.deleted !== 1 ? 's' : ''} from ${name}.`, 'success');
            window.pageManager.loadPortfoliosPage();
        } catch (err) {
            alert('Error clearing transactions: ' + err.message);
            confirmBtn.disabled = false;
        }
    };

    bsModal.show();
};

// ---------------------------------------------------------------------------
// Import / Export page (inline, no modals)
// ---------------------------------------------------------------------------

function setupImportExportPage() {
    // --- File import section ---
    const fileBroker   = document.getElementById('ioFileBroker');
    const fileInput    = document.getElementById('ioFileInput');
    const fileHint     = document.getElementById('ioFileHint');
    const fileStep1    = document.getElementById('ioFileStep1');
    const fileStep2    = document.getElementById('ioFileStep2');
    const fileParseBtn = document.getElementById('ioFileParseBtn');
    const fileSaveBtn  = document.getElementById('ioFileSaveBtn');
    const fileBackBtn  = document.getElementById('ioFileBackBtn');
    const filePreview  = document.getElementById('ioFilePreview');
    if (!fileBroker) return;

    let parsedFile = [];
    let parsedFileBookings = [];
    let parsedFileDeposits = [];

    fileBroker.addEventListener('change', () => {
        const h = BROKER_HINTS[fileBroker.value];
        if (h) { fileHint.textContent = h; fileHint.style.display = ''; }
        else fileHint.style.display = 'none';
    });

    function fileShowStep1() {
        fileStep1.style.display = ''; fileStep2.style.display = 'none';
        fileParseBtn.style.display = ''; fileSaveBtn.style.display = 'none';
        fileBackBtn.style.display = 'none';
        parsedFile = [];
        parsedFileBookings = [];
        parsedFileDeposits = [];
    }

    fileBackBtn.addEventListener('click', fileShowStep1);

    fileParseBtn.addEventListener('click', async () => {
        const broker = fileBroker.value;
        const file   = fileInput.files[0];
        if (!broker) { alert('Please select a broker.'); return; }
        if (!file)   { alert('Please select a file.'); return; }
        fileParseBtn.disabled = true;
        fileParseBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Parsing…';
        try {
            const data = await window.apiClient.uploadBrokerFile(broker, file);
            parsedFile = data.transactions || [];
            parsedFileBookings = data.bookings || [];
            parsedFileDeposits = data.deposits || [];
            fileStep1.style.display = 'none'; fileStep2.style.display = '';
            fileParseBtn.style.display = 'none'; fileBackBtn.style.display = '';
            fileSaveBtn.style.display = (parsedFile.length > 0 || parsedFileBookings.length > 0 || parsedFileDeposits.length > 0) ? '' : 'none';
            let html = _buildPreviewTable(parsedFile, parsedFileBookings, parsedFileDeposits);
            if (data.skipped_count > 0) html += `<p class="text-muted small mt-2">${data.skipped_count} row(s) skipped.</p>`;
            filePreview.innerHTML = html;
        } catch (err) {
            alert('Error parsing file: ' + err.message);
        } finally {
            fileParseBtn.disabled = false;
            fileParseBtn.innerHTML = '<i class="bi bi-search me-1"></i>Parse File';
        }
    });

    fileSaveBtn.addEventListener('click', async () => {
        const selected = Array.from(document.querySelectorAll('#ioFilePreview .file-tx-select:checked'))
            .map(cb => parsedFile[parseInt(cb.dataset.idx)]);
        const selectedDeps = Array.from(document.querySelectorAll('#ioFilePreview .file-dep-select:checked'))
            .map(cb => parsedFileDeposits[parseInt(cb.dataset.idx)]);
        if (selected.length === 0 && parsedFileBookings.length === 0 && selectedDeps.length === 0) { alert('No data selected.'); return; }
        fileSaveBtn.disabled = true;
        fileSaveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving…';
        try {
            const result = await window.apiClient.saveImportedTransactions(selected, parsedFileBookings, null, _dupAction(), selectedDeps);
            const bkMsg = result.saved_bookings > 0 ? ` + ${result.saved_bookings} booking(s)` : '';
            const depMsg = result.saved_deposits > 0 ? ` + ${result.saved_deposits} deposit(s)` : '';
            const owMsg = result.overwritten > 0 ? `, ${result.overwritten} overwritten` : '';
            const dupMsg = result.duplicates_skipped > 0 ? `, ${result.duplicates_skipped} duplicate(s) skipped` : '';
            const realErrors = result.errors.filter(e => !e.startsWith('DUPLICATE'));
            alert(realErrors.length > 0
                ? `Saved ${result.saved}${bkMsg}${depMsg}${owMsg}${dupMsg}. Errors:\n${realErrors.join('\n')}`
                : `Successfully imported ${result.saved} transaction(s)${bkMsg}${depMsg}${owMsg}${dupMsg}.`);
            fileShowStep1();
        } catch (err) {
            alert('Error saving: ' + err.message);
        } finally {
            fileSaveBtn.disabled = false;
            fileSaveBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Save Selected';
        }
    });

    // --- Text / LLM import section ---
    const textarea       = document.getElementById('ioTextarea');
    const textStep1      = document.getElementById('ioTextStep1');
    const textStep2      = document.getElementById('ioTextStep2');
    const extractBtn     = document.getElementById('ioTextExtractBtn');
    const textSaveBtn    = document.getElementById('ioTextSaveBtn');
    const ioTextPortfolio = document.getElementById('ioTextPortfolio');
    const textBackBtn    = document.getElementById('ioTextBackBtn');
    const textPreview    = document.getElementById('ioTextPreview');
    if (!textarea) return;

    // Populate portfolio dropdown asynchronously
    (async () => {
        if (ioTextPortfolio && ioTextPortfolio.options.length <= 1) {
            try {
                const portfolios = await window.apiClient.getPortfolios();
                portfolios.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id; opt.textContent = p.name;
                    ioTextPortfolio.appendChild(opt);
                });
            } catch (e) { /* silent */ }
        }
    })();

    let extractedText = [];
    let extractedTextBookings = [];

    function textShowStep1() {
        textStep1.style.display = ''; textStep2.style.display = 'none';
        extractBtn.style.display = ''; textSaveBtn.style.display = 'none';
        textBackBtn.style.display = 'none';
        extractedText = [];
        extractedTextBookings = [];
    }

    textBackBtn.addEventListener('click', textShowStep1);

    extractBtn.addEventListener('click', async () => {
        const text = textarea.value.trim();
        if (!text) { alert('Please paste some broker statement text first.'); return; }
        extractBtn.disabled = true;
        extractBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Extracting…';
        try {
            // Async: extract trades+dividends and cash movements via a background
            // job and poll, so a big statement can't trip the gateway timeout.
            const result = await window.apiClient.extractTransactionsAndBookings(text, (secs) => {
                extractBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>Extracting… ${secs}s`;
            });
            extractedText = result.transactions || [];
            extractedTextBookings = result.bookings || [];

            // Flag rows that already exist in the DB (best-effort), same as the
            // file-upload preview.
            try {
                const ioPid = ioTextPortfolio && ioTextPortfolio.value ? parseInt(ioTextPortfolio.value) : null;
                const previewTx = extractedText.map(tx => ({
                    symbol: tx.symbol, name: tx.asset_name || tx.symbol, asset_type: 'stock',
                    tx_type: tx.tx_type, date: tx.date, quantity: tx.quantity, price: tx.price,
                    currency: tx.currency || 'EUR', fees: parseFloat(tx.fees) || 0.0
                }));
                const chk = await window.apiClient.checkDuplicates(previewTx, extractedTextBookings, ioPid);
                (chk.transactions || []).forEach((t, i) => { if (extractedText[i]) extractedText[i].is_duplicate = t.is_duplicate; });
                (chk.bookings || []).forEach((b, i) => { if (extractedTextBookings[i]) extractedTextBookings[i].is_duplicate = b.is_duplicate; });
            } catch (e) { /* flagging is optional */ }

            textStep1.style.display = 'none'; textStep2.style.display = '';
            extractBtn.style.display = 'none'; textBackBtn.style.display = '';
            textSaveBtn.style.display = (extractedText.length > 0 || extractedTextBookings.length > 0) ? '' : 'none';
            const dupControl = _dupControl(extractedText, extractedTextBookings);
            const dupBadge = '<span class="badge bg-warning text-dark ms-1">dup</span>';
            const bookingsSummary = extractedTextBookings.length > 0
                ? `<div class="alert alert-info py-1 mb-2 small"><i class="bi bi-bank me-1"></i><strong>${extractedTextBookings.length} cash movement(s)</strong> detected (`
                  + extractedTextBookings.map(b => `${b.action} ${b.amount.toFixed(2)} ${b.currency}${b.is_duplicate ? ' ' + dupBadge : ''}`).join(', ')
                  + ') — saved with the transactions.</div>'
                : '';
            const rows = extractedText.map((tx, i) => `
                <tr class="${tx.is_duplicate ? 'table-warning' : ''}">
                    <td><input class="form-check-input io-tx-select" type="checkbox" ${tx.is_duplicate ? '' : 'checked'} data-idx="${i}" data-dup="${tx.is_duplicate ? '1' : '0'}"></td>
                    <td><input type="date" class="form-control form-control-sm" id="iotx_date_${i}" value="${escapeForAttr(tx.date || '')}">
                        ${tx.is_duplicate ? dupBadge : ''}</td>
                    <td>
                        <input type="text" class="form-control form-control-sm mb-1" id="iotx_symbol_${i}" value="${escapeForAttr(tx.symbol || '')}" placeholder="Symbol">
                        <input type="text" class="form-control form-control-sm" id="iotx_name_${i}" value="${escapeForAttr(tx.asset_name || '')}" placeholder="Name">
                    </td>
                    <td>
                        <select class="form-select form-select-sm" id="iotx_type_${i}">
                            ${['buy','sell','dividend','interest'].map(t => `<option value="${t}" ${(tx.tx_type||'') === t ? 'selected' : ''}>${t.toUpperCase()}</option>`).join('')}
                        </select>
                    </td>
                    <td><input type="number" class="form-control form-control-sm" id="iotx_qty_${i}" value="${tx.quantity || 0}" step="any" min="0"></td>
                    <td>
                        <input type="number" class="form-control form-control-sm mb-1" id="iotx_price_${i}" value="${tx.price || 0}" step="any" min="0">
                        <input type="text" class="form-control form-control-sm" id="iotx_currency_${i}" value="${escapeForAttr(tx.currency || 'EUR')}" maxlength="3" style="width:5ch">
                    </td>
                    <td><input type="number" class="form-control form-control-sm" id="iotx_fees_${i}" value="${parseFloat(tx.fees)||0}" step="any" min="0"></td>
                </tr>`).join('');
            textPreview.innerHTML = extractedText.length === 0
                ? (bookingsSummary + dupControl || '<div class="alert alert-warning">No transactions or cash movements could be extracted.</div>')
                : bookingsSummary + dupControl
                   + `<p class="text-muted small mb-2">Found <strong>${extractedText.length}</strong> transaction(s). Uncheck any to skip.</p>
                   <div class="table-responsive"><table class="table table-sm table-hover">
                   <thead><tr><th></th><th>Date</th><th>Asset</th><th>Type</th><th>Qty</th><th>Price / Cur</th><th>Fees</th></tr></thead>
                   <tbody>${rows}</tbody></table></div>`;
        } catch (err) {
            alert('Error extracting: ' + err.message);
        } finally {
            extractBtn.disabled = false;
            extractBtn.innerHTML = '<i class="bi bi-magic me-1"></i>Extract';
        }
    });

    textSaveBtn.addEventListener('click', async () => {
        const checkedIdxs = Array.from(document.querySelectorAll('#ioTextPreview .io-tx-select:checked'))
            .map(cb => parseInt(cb.dataset.idx));
        if (checkedIdxs.length === 0 && extractedTextBookings.length === 0) {
            alert('Nothing selected to save.'); return;
        }
        const normalized = checkedIdxs.map(i => ({
            symbol: document.getElementById(`iotx_symbol_${i}`).value,
            name: document.getElementById(`iotx_name_${i}`).value || document.getElementById(`iotx_symbol_${i}`).value,
            asset_type: 'stock',
            tx_type: document.getElementById(`iotx_type_${i}`).value,
            date: document.getElementById(`iotx_date_${i}`).value,
            quantity: parseFloat(document.getElementById(`iotx_qty_${i}`).value) || 0,
            price: parseFloat(document.getElementById(`iotx_price_${i}`).value) || 0,
            currency: (document.getElementById(`iotx_currency_${i}`).value || 'EUR').toUpperCase(),
            fees: parseFloat(document.getElementById(`iotx_fees_${i}`).value) || 0,
            notes: extractedText[i] ? (extractedText[i].raw_text || '') : ''
        }));
        textSaveBtn.disabled = true;
        textSaveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving…';
        try {
            const ioPortfolioId = ioTextPortfolio && ioTextPortfolio.value ? parseInt(ioTextPortfolio.value) : null;
            const result = await window.apiClient.saveImportedTransactions(normalized, extractedTextBookings, ioPortfolioId, _dupAction());
            const dupNote = result.duplicates_skipped ? `, ${result.duplicates_skipped} duplicate(s) skipped` : '';
            const owMsg = result.overwritten > 0 ? `, ${result.overwritten} overwritten` : '';
            const bkMsg = result.saved_bookings > 0 ? ` + ${result.saved_bookings} cash movement(s)` : '';
            const realErrors = result.errors.filter(e => !e.startsWith('DUPLICATE'));
            alert(realErrors.length > 0
                ? `Saved ${result.saved}${bkMsg}${owMsg}${dupNote}. Errors:\n${realErrors.join('\n')}`
                : `Successfully imported ${result.saved} transaction(s)${bkMsg}${owMsg}${dupNote}.`);
            textShowStep1();
            textarea.value = '';
            loadBookings();
        } catch (err) {
            alert('Error saving: ' + err.message);
        } finally {
            textSaveBtn.disabled = false;
            textSaveBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Save Selected';
        }
    });

    // --- Export section ---
    const ioCsvBtn = document.getElementById('ioExportCsvBtn');
    const ioPdtBtn = document.getElementById('ioExportPdtBtn');
    if (ioCsvBtn) ioCsvBtn.addEventListener('click', async () => {
        try {
            await window.apiClient.downloadBlob(window.apiClient.baseURL + '/api/v1/export/csv', 'transactions.csv');
        } catch (err) { alert('Export error: ' + err.message); }
    });
    if (ioPdtBtn) ioPdtBtn.addEventListener('click', async () => {
        try {
            await window.apiClient.downloadBlob(window.apiClient.baseURL + '/api/v1/export/pdt', 'portfolio_pdt.xlsx');
        } catch (err) { alert('Export error: ' + err.message); }
    });
    const ioBackupBtn = document.getElementById('ioExportBackupBtn');
    if (ioBackupBtn) ioBackupBtn.addEventListener('click', async () => {
        try {
            await window.apiClient.downloadBlob(window.apiClient.baseURL + '/api/v1/export/backup', 'pfm-backup.db');
        } catch (err) { alert('Backup error: ' + err.message); }
    });
    const ioRestoreBtn = document.getElementById('ioRestoreBackupBtn');
    if (ioRestoreBtn) {
        const restoreModal = document.getElementById('restoreBackupModal');
        const bsRestoreModal = bootstrap.Modal.getOrCreateInstance(restoreModal);
        const restoreFileInput = document.getElementById('restoreFileInput');
        const restoreConfirmBtn = document.getElementById('restoreConfirmBtn');
        const restoreStatusMsg = document.getElementById('restoreStatusMsg');

        ioRestoreBtn.addEventListener('click', () => {
            restoreFileInput.value = '';
            restoreConfirmBtn.disabled = true;
            restoreStatusMsg.textContent = '';
            bsRestoreModal.show();
        });

        restoreFileInput.addEventListener('change', () => {
            restoreConfirmBtn.disabled = !restoreFileInput.files.length;
            restoreStatusMsg.textContent = '';
        });

        restoreConfirmBtn.addEventListener('click', async () => {
            const file = restoreFileInput.files[0];
            if (!file) return;
            restoreConfirmBtn.disabled = true;
            restoreStatusMsg.textContent = 'Restoring…';
            try {
                const formData = new FormData();
                formData.append('file', file);
                const resp = await fetch(
                    window.apiClient.baseURL + '/api/v1/system/restore',
                    { method: 'POST', headers: { 'X-API-Key': window.apiClient.apiKey }, body: formData }
                );
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || resp.statusText);
                bsRestoreModal.hide();
                const backupNote = data.pre_restore_backup
                    ? ` Pre-restore snapshot saved to ${data.pre_restore_backup}.`
                    : '';
                alert(`Database restored successfully.${backupNote}\n\nThe page will reload.`);
                window.location.reload();
            } catch (err) {
                restoreStatusMsg.textContent = 'Error: ' + err.message;
                restoreConfirmBtn.disabled = false;
            }
        });
    }

    // --- Bookings section ---
    async function loadBookings() {
        const container = document.getElementById('ioBookingsTable');
        if (!container) return;
        try {
            const bookings = await window.apiClient.getBookings();
            if (bookings.length === 0) {
                container.innerHTML = '<p class="text-muted small p-3 mb-0">No bookings found.</p>';
                return;
            }
            const rows = bookings.map(b => `
                <tr>
                    <td>${Fmt.date(b.date)}</td>
                    <td><span class="badge bg-${b.action === 'Deposit' ? 'success' : 'warning'}">${b.action}</span></td>
                    <td class="text-end">${parseFloat(b.amount).toFixed(2)}</td>
                    <td>${b.currency}</td>
                    <td class="text-muted small">${esc(b.portfolio_name || '')}</td>
                </tr>`).join('');
            container.innerHTML = `<table class="table table-sm table-hover mb-0">
                <thead><tr><th>Date</th><th>Action</th><th class="text-end">Amount</th><th>Currency</th><th>Portfolio</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
        } catch (err) {
            container.innerHTML = `<p class="text-danger small p-3 mb-0">Error loading bookings: ${err.message}</p>`;
        }
    }

    loadBookings();
    const refreshBookingsBtn = document.getElementById('ioRefreshBookingsBtn');
    if (refreshBookingsBtn) refreshBookingsBtn.addEventListener('click', loadBookings);

    // Manual "Add booking" form (deposit / withdrawal)
    const addBookingForm = document.getElementById('addBookingForm');
    const addBookingPortfolio = document.getElementById('addBookingPortfolio');
    if (addBookingPortfolio && addBookingPortfolio.options.length <= 1) {
        (async () => {
            try {
                const portfolios = await window.apiClient.getPortfolios();
                portfolios.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id; opt.textContent = p.name;
                    addBookingPortfolio.appendChild(opt);
                });
                selectDefaultBroker(addBookingPortfolio);
            } catch (e) { /* silent */ }
        })();
    }
    const addBookingDate = document.getElementById('addBookingDate');
    if (addBookingDate && !addBookingDate.value) {
        addBookingDate.value = new Date().toISOString().slice(0, 10);
    }
    if (addBookingForm) {
        addBookingForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = addBookingForm.querySelector('button[type="submit"]');
            const payload = {
                date: document.getElementById('addBookingDate').value,
                action: document.getElementById('addBookingAction').value,
                amount: parseFloat(document.getElementById('addBookingAmount').value),
                currency: (document.getElementById('addBookingCurrency').value || 'EUR').toUpperCase(),
                portfolio_id: addBookingPortfolio && addBookingPortfolio.value
                    ? parseInt(addBookingPortfolio.value) : null,
            };
            if (!payload.date || !payload.amount || payload.amount <= 0) {
                alert('Please enter a valid date and amount.'); return;
            }
            btn.disabled = true;
            const orig = btn.innerHTML;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            try {
                await window.apiClient.createBooking(payload);
                document.getElementById('addBookingAmount').value = '';
                loadBookings();
            } catch (err) {
                alert('Error adding booking: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = orig;
            }
        });
    }

    // --- Google Sheets PDT Sync section ---
    const syncSheetInput = document.getElementById('syncSheetId');
    const syncConfigInfo = document.getElementById('syncConfigInfo');
    const syncStatus     = document.getElementById('syncStatus');
    const syncPullBtn    = document.getElementById('syncPullBtn');
    const syncPushBtn    = document.getElementById('syncPushBtn');

    async function loadSyncConfig() {
        if (!syncConfigInfo) return;
        try {
            const cfg = await window.apiClient.getSyncConfig();
            const saStatus = cfg.service_account_configured
                ? `<span class="badge bg-success">Configured</span> <code class="small">${cfg.service_account_email || ''}</code>`
                : `<span class="badge bg-danger">Not configured</span> — set <code>GOOGLE_SERVICE_ACCOUNT_FILE</code>`;
            const sheetInfo = cfg.default_spreadsheet_id
                ? `<span class="text-muted small">Default sheet: <code>${cfg.default_spreadsheet_id}</code></span>`
                : `<span class="text-muted small">No default sheet — enter ID below or set <code>GOOGLE_SPREADSHEET_ID</code></span>`;
            syncConfigInfo.innerHTML = `
                <div class="d-flex flex-column gap-1">
                    <div><span class="text-muted small me-2">Service account:</span>${saStatus}</div>
                    <div>${sheetInfo}</div>
                    ${cfg.service_account_configured && cfg.service_account_email ?
                        `<div class="alert alert-info py-1 mb-0 small"><i class="bi bi-info-circle me-1"></i>Share your Google Sheet with <strong>${cfg.service_account_email}</strong> (Editor access).</div>` : ''}
                </div>`;
            if (cfg.default_spreadsheet_id && syncSheetInput && !syncSheetInput.value)
                syncSheetInput.placeholder = cfg.default_spreadsheet_id;
        } catch (e) {
            if (syncConfigInfo) syncConfigInfo.innerHTML = `<span class="text-danger small">${e.message}</span>`;
        }
    }

    function setSyncStatus(msg, type = 'info') {
        if (!syncStatus) return;
        syncStatus.innerHTML = `<div class="alert alert-${type} py-2 small mb-0">${msg}</div>`;
    }

    function getSheetId() {
        return syncSheetInput && syncSheetInput.value.trim()
            ? syncSheetInput.value.trim()
            : null;
    }

    if (syncPullBtn) syncPullBtn.addEventListener('click', async () => {
        const sheetId = getSheetId();
        syncPullBtn.disabled = true;
        syncPullBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Pulling…';
        setSyncStatus('Pulling data from Google Sheet…');
        try {
            const r = await window.apiClient.syncPull(sheetId);
            setSyncStatus(
                `Pulled from <code>${r.spreadsheet_id}</code>: `
                + `${r.imported_transactions} transactions, ${r.imported_dividends} dividends, `
                + `${r.imported_bookings} bookings.`
                + (r.errors.length ? `<br>${r.errors.length} error(s): ${r.errors[0]}` : ''),
                r.errors.length ? 'warning' : 'success'
            );
            loadBookings();
        } catch (err) {
            setSyncStatus(`Pull failed: ${err.message}`, 'danger');
        } finally {
            syncPullBtn.disabled = false;
            syncPullBtn.innerHTML = '<i class="bi bi-cloud-arrow-down me-2"></i>Pull from Sheet';
        }
    });

    if (syncPushBtn) syncPushBtn.addEventListener('click', async () => {
        const sheetId = getSheetId();
        syncPushBtn.disabled = true;
        syncPushBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Pushing…';
        setSyncStatus('Pushing data to Google Sheet…');
        try {
            const r = await window.apiClient.syncPush(sheetId);
            setSyncStatus(
                `Pushed to <a href="${r.spreadsheet_url}" target="_blank">${r.spreadsheet_id}</a>: `
                + `${r.transactions_written} transactions, ${r.dividends_written} dividends, `
                + `${r.bookings_written} bookings.`,
                'success'
            );
        } catch (err) {
            setSyncStatus(`Push failed: ${err.message}`, 'danger');
        } finally {
            syncPushBtn.disabled = false;
            syncPushBtn.innerHTML = '<i class="bi bi-cloud-arrow-up me-2"></i>Push to Sheet';
        }
    });

    loadSyncConfig();
}

// ---------------------------------------------------------------------------
// Forecast page
// ---------------------------------------------------------------------------

// Pure: map Net Worth manual-asset items to the forecast's modelled inputs.
// Cash = savings/current/cash; Bonds = external investment; Mortgage = the
// mortgage liability. Everything else (property/vehicle/pension, other debts)
// is collected into `skipped` (the simulator has no field for them). Amounts
// are EUR (prefers amount_eur). Unit-tested in web_client/js/tests/.
function mapNetworthToForecast(items) {
    const CASH = new Set(['savings_account', 'current_account', 'cash']);
    const BONDS = new Set(['investment_external']);
    let cash = 0, bonds = 0, mortgage = 0;
    const skipped = [];
    for (const it of (items || [])) {
        const eur = parseFloat(it.amount_eur != null ? it.amount_eur : it.amount) || 0;
        if (it.is_liability) {
            if (it.category === 'mortgage') mortgage += eur;
            else skipped.push(it.name || it.category);
        } else if (CASH.has(it.category)) {
            cash += eur;
        } else if (BONDS.has(it.category)) {
            bonds += eur;
        } else {
            skipped.push(it.name || it.category);
        }
    }
    return { cash, bonds, mortgage, skipped };
}
window.mapNetworthToForecast = mapNetworthToForecast;

// Pure: turn the analytics performance + risk payloads into forecast inputs.
// Uses money-weighted IRR for the return and annualised volatility; needs ≥3
// snapshots. Returns { ok, rate, vol, snapshots } or { ok:false, reason }.
// Unit-tested in web_client/js/tests/.
function historyToForecast(perf, risk) {
    const snaps = (risk && risk.snapshots_used) || 0;
    if (snaps < 3) return { ok: false, reason: 'Not enough history yet — need a few more daily snapshots.' };
    const rate = perf && typeof perf.money_weighted_irr_pct === 'number' ? perf.money_weighted_irr_pct : null;
    const vol = risk && typeof risk.volatility_pct === 'number' ? risk.volatility_pct : null;
    if (rate == null || vol == null) return { ok: false, reason: 'Return/volatility unavailable.' };
    return { ok: true, rate, vol, snapshots: snaps };
}
window.historyToForecast = historyToForecast;

function setupForecastPage() {
    // DOM refs - asset allocation inputs
    const cashAmountInput   = document.getElementById('fcCashAmount');
    const cashRateInput     = document.getElementById('fcCashRate');
    const stocksAmountInput = document.getElementById('fcStocksAmount');
    const stocksRateInput   = document.getElementById('fcStocksRate');
    const stocksVolInput    = document.getElementById('fcStocksVol');
    const bondsAmountInput  = document.getElementById('fcBondsAmount');
    const bondsRateInput    = document.getElementById('fcBondsRate');
    const startNote         = document.getElementById('fcStartValueNote');
    const refreshBtn        = document.getElementById('fcRefreshBtn');

    // Mortgage inputs
    const mortgagePrincipalInput = document.getElementById('fcMortgagePrincipal');
    const mortgageRateInput      = document.getElementById('fcMortgageRate');
    const monthlyPaymentInput    = document.getElementById('fcMonthlyPayment');
    const mortgageMinNote        = document.getElementById('fcMortgageMinNote');
    const mortgagePayoffBadge    = document.getElementById('fcMortgagePayoffBadge');

    // Settings
    const yearsSlider  = document.getElementById('fcYears');
    const yearsDisplay = document.getElementById('fcYearsDisplay');
    const confSelect   = document.getElementById('fcConfidence');
    const runBtn       = document.getElementById('fcRunBtn');

    // Chart + summary
    const chartSvg         = document.getElementById('fcChartSvg');
    const chartPlaceholder = document.getElementById('fcChartPlaceholder');
    const summaryRow       = document.getElementById('fcSummaryRow');
    const rangeRow         = document.getElementById('fcRangeRow');
    const totalLiquidBadge = document.getElementById('fcTotalLiquidBadge');

    if (!runBtn) return;

    // Compact EUR formatter - uses abbreviated thousands/millions for chart labels
    function fmtEur(val) {
        const n = Math.round(val);
        if (n >= 1000000) return '€' + (n / 1000000).toFixed(2) + 'M';
        if (n >= 1000)    return '€' + (n / 1000).toFixed(1) + 'k';
        return '€' + Fmt.num(n, 0, 0);
    }

    // Load stocks starting value from holdings API
    async function loadStartValue() {
        if (startNote) startNote.textContent = 'Loading…';
        try {
            const data = await window.apiClient.getHoldings();
            const totalValue = parseFloat((data.summary && data.summary.total_value) || 0);
            stocksAmountInput.value = totalValue.toFixed(0);
            if (startNote) {
                startNote.textContent = totalValue > 0
                    ? 'Auto-populated from current holdings.'
                    : 'Holdings total is zero or unavailable.';
            }
            updateTotalLiquidBadge();
        } catch (e) {
            stocksAmountInput.value = '0';
            if (startNote) startNote.textContent = 'Could not load holdings: ' + e.message;
        }
    }

    // Update total liquid badge
    function updateTotalLiquidBadge() {
        const total = (parseFloat(cashAmountInput.value) || 0)
                    + (parseFloat(stocksAmountInput.value) || 0)
                    + (parseFloat(bondsAmountInput.value) || 0);
        if (totalLiquidBadge) totalLiquidBadge.textContent = 'Total: ' + fmtEur(total);
    }

    // Update mortgage minimum-payment note
    function updateMortgageNote() {
        const principal = parseFloat(mortgagePrincipalInput.value) || 0;
        const rate      = parseFloat(mortgageRateInput.value) || 0;
        const minPmt    = Math.ceil(principal * (rate / 100) / 12);
        if (principal > 0 && minPmt > 0) {
            mortgageMinNote.textContent = `Min interest-only payment: ${fmtEur(minPmt)}/month.`;
            const currentPmt = parseFloat(monthlyPaymentInput.value) || 0;
            if (currentPmt > 0 && currentPmt < minPmt) {
                mortgageMinNote.textContent += ' Warning: payment is below interest — balance will grow!';
            }
        } else {
            mortgageMinNote.textContent = 'Leave at 0 if no mortgage.';
        }
    }

    // Per-asset GBM projection.
    // Returns array[0..years] of { year, mean, high, low }
    function projectAccount(startAmount, annualRatePct, volatility, years, sigma) {
        const r = annualRatePct / 100;
        const points = [];
        for (let i = 0; i <= years; i++) {
            const mean = startAmount * Math.pow(1 + r, i);
            const totalVol = volatility * Math.sqrt(i || 0.5);
            points.push({
                year: i,
                mean: Math.max(0, mean),
                high: Math.max(0, mean * Math.exp(sigma * totalVol)),
                low:  Math.max(0, mean * Math.exp(-sigma * totalVol))
            });
        }
        return points;
    }

    // Full projection: assets + mortgage amortization + net worth.
    // Returns { data[], mortgagePaidOffYear, totalInterestPaid }
    function runProjection(cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate,
                           mortgagePrincipal, mortgageRate, monthlyPayment, years, sigma, stocksVol) {
        const VOLATILITY = { cash: 0.01, bonds: 0.06, stocks: 0.16 };

        const cashProj   = projectAccount(cashAmt,   cashRate,   VOLATILITY.cash,   years, sigma);
        const stocksProj = projectAccount(stocksAmt, stocksRate, (stocksVol != null ? stocksVol : VOLATILITY.stocks), years, sigma);
        const bondsProj  = projectAccount(bondsAmt,  bondsRate,  VOLATILITY.bonds,  years, sigma);

        let currentMortgage     = mortgagePrincipal;
        const mRate             = mortgageRate / 100 / 12;
        let mortgagePaidOffYear = null;
        let totalInterestPaid   = 0;

        const data = [];

        for (let i = 0; i <= years; i++) {
            // Month-by-month amortization for year i
            if (i > 0 && currentMortgage > 0) {
                for (let m = 0; m < 12; m++) {
                    if (currentMortgage <= 0) break;
                    const interest = currentMortgage * mRate;
                    totalInterestPaid += interest;
                    const principal = monthlyPayment - interest;
                    currentMortgage -= principal;
                }
                if (currentMortgage < 0) currentMortgage = 0;
                if (currentMortgage === 0 && mortgagePaidOffYear === null) {
                    mortgagePaidOffYear = i;
                }
            }

            const assetMean = cashProj[i].mean + stocksProj[i].mean + bondsProj[i].mean;
            const assetHigh = cashProj[i].high + stocksProj[i].high + bondsProj[i].high;
            const assetLow  = cashProj[i].low  + stocksProj[i].low  + bondsProj[i].low;

            data.push({
                year:          i,
                assets:        assetMean,
                assetsHigh:    assetHigh,
                assetsLow:     assetLow,
                mortgage:      currentMortgage,
                netWorth:      assetMean - currentMortgage,
                netWorthHigh:  assetHigh - currentMortgage,
                netWorthLow:   assetLow  - currentMortgage
            });
        }

        return { data, mortgagePaidOffYear, totalInterestPaid };
    }

    // SVG chart rendering
    function renderChart(projResult, totalStarting, years) {
        const { data, mortgagePaidOffYear } = projResult;
        const container = document.getElementById('fcChartContainer');
        const W = container.clientWidth || 600;
        const H = 340;
        const PAD = { top: 20, right: 20, bottom: 40, left: 72 };
        const innerW = W - PAD.left - PAD.right;
        const innerH = H - PAD.top - PAD.bottom;

        const allVals = data.flatMap(p => [p.netWorthHigh, p.netWorthLow, p.mortgage, 0]);
        const maxVal = Math.max(...allVals);
        const minVal = Math.min(...allVals, 0);
        const range  = maxVal - minVal || 1;

        function xScale(t) {
            return PAD.left + (t / years) * innerW;
        }
        function yScale(v) {
            return PAD.top + innerH - ((v - minVal) / range) * innerH;
        }
        function yTickFmt(v) {
            if (Math.abs(v) >= 1000000) return '€' + (v / 1000000).toFixed(1) + 'M';
            if (Math.abs(v) >= 1000)    return '€' + (v / 1000).toFixed(0) + 'k';
            return '€' + v;
        }
        function pathD(key) {
            return data.map((p, i) =>
                (i === 0 ? 'M' : 'L') + xScale(p.year).toFixed(1) + ',' + yScale(p[key]).toFixed(1)
            ).join(' ');
        }

        // Confidence band polygon: high to low reversed
        const bandPath = pathD('netWorthHigh') + ' '
            + data.slice().reverse().map(p =>
                'L' + xScale(p.year).toFixed(1) + ',' + yScale(p.netWorthLow).toFixed(1)
            ).join(' ') + ' Z';

        // Y-axis ticks
        const yTicks = [];
        for (let i = 0; i <= 4; i++) {
            const v = minVal + range * (i / 4);
            yTicks.push({ v, y: yScale(v) });
        }

        // X-axis ticks (every 5 years + final year if needed)
        const xTicks = [];
        for (let t = 0; t <= years; t += 5) xTicks.push({ t, x: xScale(t) });
        if (years % 5 !== 0) xTicks.push({ t: years, x: xScale(years) });

        const startingNetWorth = data[0].netWorth;
        const startY = yScale(startingNetWorth);

        let payoffLine = '';
        if (mortgagePaidOffYear !== null && mortgagePaidOffYear <= years) {
            const px = xScale(mortgagePaidOffYear);
            payoffLine = `
                <line x1="${px.toFixed(1)}" y1="${PAD.top}" x2="${px.toFixed(1)}" y2="${(PAD.top + innerH).toFixed(1)}"
                      stroke="#22c55e" stroke-width="1.5" stroke-dasharray="6 4"/>
                <text x="${(px + 4).toFixed(1)}" y="${(PAD.top + 14).toFixed(1)}" font-size="10" fill="#22c55e">Paid off Yr ${mortgagePaidOffYear}</text>
            `;
        }

        const hasMortgage = data[0].mortgage > 0;
        const mortgageLine = hasMortgage
            ? `<path d="${pathD('mortgage')}" fill="none" stroke="#f43f5e" stroke-width="2" stroke-dasharray="5 5"/>`
            : '';

        const zeroLine = minVal < 0
            ? `<line x1="${PAD.left}" y1="${yScale(0).toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${yScale(0).toFixed(1)}"
                     stroke="#94a3b8" stroke-width="1" stroke-dasharray="3 3"/>`
            : '';

        const svg = chartSvg;
        svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
        svg.setAttribute('height', H);
        svg.style.display = 'block';

        svg.innerHTML = `
            <defs>
                <linearGradient id="fcBandGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#93c5fd" stop-opacity="0.35"/>
                    <stop offset="100%" stop-color="#93c5fd" stop-opacity="0.1"/>
                </linearGradient>
                <linearGradient id="fcLineGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#2563eb" stop-opacity="0.15"/>
                    <stop offset="100%" stop-color="#2563eb" stop-opacity="0.0"/>
                </linearGradient>
            </defs>

            <!-- Grid lines -->
            ${yTicks.map(t => `
                <line x1="${PAD.left}" y1="${t.y.toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${t.y.toFixed(1)}"
                      stroke="#e2e8f0" stroke-width="1"/>
            `).join('')}

            ${zeroLine}

            <!-- Confidence band -->
            <path d="${bandPath}" fill="url(#fcBandGrad)" stroke="none"/>

            <!-- Under net-worth-line fill -->
            <path d="${pathD('netWorth')} L${xScale(years).toFixed(1)},${(PAD.top + innerH).toFixed(1)} L${xScale(0).toFixed(1)},${(PAD.top + innerH).toFixed(1)} Z"
                  fill="url(#fcLineGrad)"/>

            <!-- Band edges (dashed) -->
            <path d="${pathD('netWorthHigh')}" fill="none" stroke="#93c5fd" stroke-width="1.5" stroke-dasharray="4 3"/>
            <path d="${pathD('netWorthLow')}"  fill="none" stroke="#93c5fd" stroke-width="1.5" stroke-dasharray="4 3"/>

            <!-- Net worth mean line -->
            <path d="${pathD('netWorth')}" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>

            <!-- Mortgage balance line -->
            ${mortgageLine}

            <!-- Mortgage payoff marker -->
            ${payoffLine}

            <!-- Starting net worth marker -->
            <line x1="${PAD.left}" y1="${startY.toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${startY.toFixed(1)}"
                  stroke="#64748b" stroke-width="1" stroke-dasharray="6 4"/>
            <text x="${(PAD.left + 4).toFixed(1)}" y="${(startY - 4).toFixed(1)}" font-size="10" fill="#64748b">Current</text>

            <!-- Y-axis labels -->
            ${yTicks.map(t => `
                <text x="${(PAD.left - 6).toFixed(1)}" y="${(t.y + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="#64748b">${yTickFmt(t.v)}</text>
            `).join('')}

            <!-- X-axis labels -->
            ${xTicks.map(t => `
                <text x="${t.x.toFixed(1)}" y="${(PAD.top + innerH + 16).toFixed(1)}" text-anchor="middle" font-size="11" fill="#64748b">Yr ${t.t}</text>
            `).join('')}

            <!-- Axis lines -->
            <line x1="${PAD.left}" y1="${PAD.top}" x2="${PAD.left}" y2="${(PAD.top + innerH).toFixed(1)}" stroke="#cbd5e1" stroke-width="1"/>
            <line x1="${PAD.left}" y1="${(PAD.top + innerH).toFixed(1)}" x2="${(PAD.left + innerW).toFixed(1)}" y2="${(PAD.top + innerH).toFixed(1)}" stroke="#cbd5e1" stroke-width="1"/>

            <!-- Endpoint dot on mean net worth -->
            <circle cx="${xScale(years).toFixed(1)}" cy="${yScale(data[years].netWorth).toFixed(1)}" r="5"
                    fill="#2563eb" stroke="white" stroke-width="2"/>
        `;

        chartPlaceholder.style.display = 'none';
    }

    // Run forecast
    function runForecast() {
        const cashAmt    = parseFloat(cashAmountInput.value)   || 0;
        const cashRate   = parseFloat(cashRateInput.value)     || 0;
        const stocksAmt  = parseFloat(stocksAmountInput.value) || 0;
        const stocksRate = parseFloat(stocksRateInput.value)   || 0;
        const bondsAmt   = parseFloat(bondsAmountInput.value)  || 0;
        const bondsRate  = parseFloat(bondsRateInput.value)    || 0;
        const mortPrincipal = parseFloat(mortgagePrincipalInput.value) || 0;
        const mortRate      = parseFloat(mortgageRateInput.value)      || 0;
        const mortPayment   = parseFloat(monthlyPaymentInput.value)    || 0;
        const years         = parseInt(yearsSlider.value)              || 30;
        const sigma         = parseFloat(confSelect.value)             || 1.96;
        const stocksVol     = stocksVolInput ? (parseFloat(stocksVolInput.value) || 16) / 100 : null;

        const proj = runProjection(
            cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate,
            mortPrincipal, mortRate, mortPayment, years, sigma, stocksVol
        );

        renderChart(proj, cashAmt + stocksAmt + bondsAmt, years);

        // Update mortgage payoff badge
        if (proj.mortgagePaidOffYear !== null) {
            mortgagePayoffBadge.textContent = `Paid off year ${proj.mortgagePaidOffYear}`;
            mortgagePayoffBadge.style.display = '';
        } else if (mortPrincipal > 0) {
            mortgagePayoffBadge.textContent = 'Not paid off in period';
            mortgagePayoffBadge.style.display = '';
            mortgagePayoffBadge.className = 'badge bg-warning text-dark';
        } else {
            mortgagePayoffBadge.style.display = 'none';
        }

        // Summary cards
        const finalData   = proj.data[years];
        const startLiquid = cashAmt + stocksAmt + bondsAmt;
        const gains       = finalData.assets - startLiquid;

        document.getElementById('fcSumMean').textContent      = fmtEur(finalData.assets);
        document.getElementById('fcSumYearLabel').textContent  = `at year ${years}`;
        document.getElementById('fcSumNetWorth').textContent   = fmtEur(finalData.netWorth);
        document.getElementById('fcSumInterest').textContent   = fmtEur(proj.totalInterestPaid);
        document.getElementById('fcSumGains').textContent      = (gains >= 0 ? '+' : '') + fmtEur(gains);
        document.getElementById('fcSumHigh').textContent       = fmtEur(finalData.netWorthHigh);
        document.getElementById('fcSumLow').textContent        = fmtEur(finalData.netWorthLow);

        summaryRow.style.removeProperty('display');
        rangeRow.style.removeProperty('display');
    }

    // Event listeners
    yearsSlider.addEventListener('input', () => {
        yearsDisplay.textContent = yearsSlider.value;
    });

    [cashAmountInput, stocksAmountInput, bondsAmountInput].forEach(el => {
        el.addEventListener('input', updateTotalLiquidBadge);
    });

    [mortgagePrincipalInput, mortgageRateInput, monthlyPaymentInput].forEach(el => {
        el.addEventListener('input', updateMortgageNote);
    });

    refreshBtn.addEventListener('click', loadStartValue);
    runBtn.addEventListener('click', runForecast);

    // Opt-in: pre-fill Cash / Bonds / Mortgage from the Net Worth page.
    const loadNwBtn = document.getElementById('fcLoadNetworth');
    const nwNote = document.getElementById('fcNetworthNote');
    if (loadNwBtn) {
        loadNwBtn.addEventListener('click', async () => {
            if (nwNote) nwNote.textContent = 'Loading from Net Worth…';
            try {
                const d = await window.apiClient.getNetworth();
                const m = mapNetworthToForecast(d.items || []);
                cashAmountInput.value = Math.round(m.cash);
                bondsAmountInput.value = Math.round(m.bonds);
                mortgagePrincipalInput.value = Math.round(m.mortgage);
                updateTotalLiquidBadge();
                updateMortgageNote();
                if (nwNote) {
                    let msg = `Loaded Cash ${fmtEur(m.cash)} · Bonds ${fmtEur(m.bonds)} · Mortgage ${fmtEur(m.mortgage)} from Net Worth.`;
                    if (m.skipped.length) msg += ` Skipped (not modelled): ${m.skipped.join(', ')}.`;
                    nwNote.textContent = msg;
                }
            } catch (e) {
                if (nwNote) nwNote.textContent = 'Could not load Net Worth: ' + e.message;
            }
        });
    }

    // Opt-in: set stock return + volatility from the user's own history.
    const useHistBtn = document.getElementById('fcUseHistory');
    const histNote = document.getElementById('fcHistoryNote');
    if (useHistBtn) {
        useHistBtn.addEventListener('click', async () => {
            if (histNote) histNote.textContent = 'Reading your history…';
            try {
                const [perf, risk] = await Promise.all([
                    window.apiClient.getPerformance(null, 'all'),
                    window.apiClient.getRisk(),
                ]);
                const h = historyToForecast(perf, risk);
                if (!h.ok) { if (histNote) histNote.textContent = h.reason; return; }
                stocksRateInput.value = h.rate.toFixed(1);
                if (stocksVolInput) stocksVolInput.value = Math.round(h.vol);
                if (histNote) {
                    histNote.textContent = `Set from your history: return ${h.rate.toFixed(1)}%/yr (money-weighted IRR), volatility ${Math.round(h.vol)}% — based on ${h.snapshots} daily snapshots. This is your whole-portfolio figure (incl. crypto), a proxy for the stocks bucket.`;
                }
            } catch (e) {
                if (histNote) histNote.textContent = 'Could not load history: ' + e.message;
            }
        });
    }

    // Re-render on window resize to keep chart responsive
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (chartSvg.style.display !== 'none') runForecast();
        }, 200);
    });

    // Expose loadStartValue so navigationManager can call it on page show
    window._fcLoadStartValue = loadStartValue;
}

// ---------------------------------------------------------------------------
// Rebalancing (Holdings page)
// ---------------------------------------------------------------------------

// Friendly label for an asset type code
function rebalanceTypeLabel(type) {
    const labels = { stock: 'Stocks', etf: 'ETFs', index: 'Index funds', crypto: 'Crypto', bond: 'Bonds', commodity: 'Commodities', p2p: 'P2P' };
    return labels[type] || (type ? type.charAt(0).toUpperCase() + type.slice(1) : 'Other');
}

// Build the target-allocation form rows, merging saved targets with the
// asset types present in current holdings, then refresh the analysis table.
async function setupRebalanceTargets(holdings) {
    const rowsEl = document.getElementById('rebalanceTargetsRows');
    if (!rowsEl) return;

    let saved = [];
    try {
        saved = await window.apiClient.getRebalanceTargets();
    } catch (err) {
        console.error('Error loading rebalance targets:', err);
    }

    // Union of asset types from holdings and saved targets
    const types = new Set();
    (holdings || []).forEach(h => { if (h.asset_type) types.add(h.asset_type); });
    (saved || []).forEach(t => { if (t.asset_type) types.add(t.asset_type); });

    const savedMap = {};
    (saved || []).forEach(t => { savedMap[t.asset_type] = t.target_pct; });

    const ordered = Array.from(types).sort();
    if (ordered.length === 0) {
        rowsEl.innerHTML = '<p class="text-muted small mb-0">No asset types to allocate yet.</p>';
        return;
    }

    rowsEl.innerHTML = ordered.map(type => `
        <div class="row g-2 align-items-center mb-2">
            <div class="col-7">
                <span class="badge bg-secondary me-1">${(type || '').toUpperCase()}</span>${rebalanceTypeLabel(type)}
            </div>
            <div class="col-5">
                <div class="input-group input-group-sm">
                    <input type="number" class="form-control rebalance-target-input" data-asset-type="${type}"
                           min="0" max="100" step="0.1" value="${savedMap[type] !== undefined ? savedMap[type] : 0}">
                    <span class="input-group-text">%</span>
                </div>
            </div>
        </div>`).join('');

    // Recompute the running total whenever an input changes
    rowsEl.querySelectorAll('.rebalance-target-input').forEach(inp => {
        inp.addEventListener('input', updateRebalanceTotal);
    });
    updateRebalanceTotal();

    // Show analysis if targets are already saved
    if ((saved || []).length > 0) {
        loadRebalanceAnalysis();
    }
}

// Update the running total and warn (red) if not 100%
function updateRebalanceTotal() {
    const totalEl = document.getElementById('rebalanceTargetsTotal');
    if (!totalEl) return;
    let total = 0;
    document.querySelectorAll('.rebalance-target-input').forEach(inp => {
        total += parseFloat(inp.value) || 0;
    });
    totalEl.textContent = total.toFixed(1) + '%';
    const off = Math.abs(total - 100) > 0.05;
    totalEl.className = off ? 'text-danger' : 'text-success';
}

// Fetch and render the rebalance analysis table + action list
async function loadRebalanceAnalysis() {
    const tbody = document.querySelector('#rebalanceAnalysisTable tbody');
    const actionsEl = document.getElementById('rebalanceActions');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="5" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</td></tr>';
    if (actionsEl) actionsEl.innerHTML = '';

    try {
        const data = await window.apiClient.getRebalanceAnalysis();
        const allocations = data.allocations || [];
        const fmtEur = (v) => parseFloat(v || 0).toLocaleString(Fmt.loc(), { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ' €';

        if (allocations.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted small">No allocation data.</td></tr>';
        } else {
            tbody.innerHTML = allocations.map(a => {
                const drift = a.drift_pct || 0;
                const absDrift = Math.abs(drift);
                let driftClass = 'text-success';
                if (absDrift >= 5) driftClass = 'text-danger';
                else if (absDrift >= 2) driftClass = 'text-warning';
                let action = '<span class="text-muted">—</span>';
                if (a.drift_eur < -0.5) action = `<span class="text-success">BUY ${fmtEur(Math.abs(a.drift_eur))}</span>`;
                else if (a.drift_eur > 0.5) action = `<span class="text-danger">SELL ${fmtEur(Math.abs(a.drift_eur))}</span>`;
                return `
                <tr>
                    <td><span class="badge bg-secondary me-1">${(a.asset_type || '').toUpperCase()}</span>${rebalanceTypeLabel(a.asset_type)}</td>
                    <td class="text-end">${(a.current_pct || 0).toFixed(1)}%</td>
                    <td class="text-end">${(a.target_pct || 0).toFixed(1)}%</td>
                    <td class="text-end ${driftClass}">${drift >= 0 ? '+' : ''}${drift.toFixed(1)}%</td>
                    <td class="text-end">${action}</td>
                </tr>`;
            }).join('');
        }

        // Actions to rebalance list
        const actions = (data.actions || []).filter(a => a.action && a.action !== 'hold' && Math.abs(a.amount_eur || 0) > 0.5);
        if (actionsEl) {
            if (actions.length === 0) {
                actionsEl.innerHTML = '<div class="alert alert-success py-2 small mb-0"><i class="bi bi-check-circle me-1"></i>Portfolio is balanced &mdash; no action needed.</div>';
            } else {
                actionsEl.innerHTML = '<h6 class="fw-semibold mb-2"><i class="bi bi-list-check me-2"></i>Actions to rebalance</h6>'
                    + '<ul class="list-group list-group-flush">'
                    + actions.map(a => {
                        const isBuy = (a.action || '').toLowerCase() === 'buy';
                        const cls = isBuy ? 'text-success' : 'text-danger';
                        const icon = isBuy ? 'bi-arrow-down-circle' : 'bi-arrow-up-circle';
                        const verb = isBuy ? 'Buy' : 'Sell';
                        return `<li class="list-group-item px-0 py-1 small">
                            <i class="bi ${icon} ${cls} me-2"></i>
                            <span class="${cls} fw-semibold">${verb} ${fmtEur(Math.abs(a.amount_eur))}</span>
                            of ${rebalanceTypeLabel(a.asset_type)}
                            ${a.reason ? `<span class="text-muted">&mdash; ${a.reason}</span>` : ''}
                        </li>`;
                    }).join('')
                    + '</ul>';
            }
        }
    } catch (err) {
        console.error('Error loading rebalance analysis:', err);
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger small">Error loading analysis.</td></tr>';
    }
}

// Wire up the Save Targets form (called once at init)
function setupRebalanceForm() {
    const form = document.getElementById('rebalanceTargetsForm');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const targets = [];
        document.querySelectorAll('.rebalance-target-input').forEach(inp => {
            targets.push({
                asset_type: inp.dataset.assetType,
                target_pct: parseFloat(inp.value) || 0
            });
        });
        const btn = document.getElementById('rebalanceSaveBtn');
        const orig = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…'; }
        try {
            await window.apiClient.setRebalanceTargets(targets);
            await loadRebalanceAnalysis();
        } catch (err) {
            alert('Error saving targets: ' + err.message);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
        }
    });
}

// ---------------------------------------------------------------------------
// Research / Valuation (Holdings page modal)
// ---------------------------------------------------------------------------

let _researchSymbol = null;

// Render a research report object into the modal body
function renderResearchReport(report) {
    const body = document.getElementById('researchReportBody');
    const generatedEl = document.getElementById('researchGeneratedAt');
    if (!body) return;

    if (!report) {
        body.innerHTML = '<p class="text-muted text-center py-4 mb-0">No analysis yet. Click <strong>Generate / Refresh Analysis</strong> to run the LLM.</p>';
        if (generatedEl) generatedEl.textContent = '';
        return;
    }

    const rec = (report.recommendation || '').toUpperCase();
    const recClass = { BUY: 'bg-success', HOLD: 'bg-secondary', SELL: 'bg-danger' }[rec] || 'bg-secondary';

    const fmtNum = (v) => (v === undefined || v === null || v === '') ? '—'
        : parseFloat(v).toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const fairValue = report.fair_value;
    const current = report.current_price;
    const confidence = report.confidence;

    const listBlock = (title, icon, items) => {
        if (!items || items.length === 0) return '';
        return `<h6 class="fw-semibold mt-3 mb-1"><i class="bi ${icon} me-2"></i>${title}</h6>
            <ul class="small mb-0">${items.map(i => `<li>${i}</li>`).join('')}</ul>`;
    };

    body.innerHTML = `
        <div class="d-flex align-items-center gap-3 mb-3 flex-wrap">
            <span class="badge ${recClass} fs-6">${rec || '—'}</span>
            ${confidence !== undefined && confidence !== null ? `<span class="small text-muted">Confidence: <strong>${typeof confidence === 'number' && confidence <= 1 ? Math.round(confidence * 100) + '%' : confidence}</strong></span>` : ''}
        </div>
        <div class="row g-2 mb-3">
            <div class="col-6 col-md-4">
                <div class="border rounded p-2 text-center">
                    <div class="small text-muted">Fair Value</div>
                    <div class="fw-bold">${fmtNum(fairValue)}</div>
                </div>
            </div>
            <div class="col-6 col-md-4">
                <div class="border rounded p-2 text-center">
                    <div class="small text-muted">Current Price</div>
                    <div class="fw-bold">${fmtNum(current)}</div>
                </div>
            </div>
            <div class="col-6 col-md-4">
                <div class="border rounded p-2 text-center">
                    <div class="small text-muted">Avg Cost</div>
                    <div class="fw-bold">${fmtNum(report.avg_cost)}</div>
                </div>
            </div>
        </div>
        ${report.summary ? `<p class="mb-2">${report.summary}</p>` : ''}
        ${report.rationale ? `<h6 class="fw-semibold mt-3 mb-1"><i class="bi bi-chat-left-text me-2"></i>Rationale</h6><p class="small mb-0">${report.rationale}</p>` : ''}
        ${listBlock('Risks', 'bi-exclamation-triangle', report.risks)}
        ${listBlock('Catalysts', 'bi-rocket-takeoff', report.catalysts)}
    `;

    if (generatedEl) {
        generatedEl.textContent = report.generated_at ? 'Generated: ' + new Date(report.generated_at).toLocaleString() : '';
    }

    // Pre-fill suggested targets from the report if present
    if (report.buy_below !== undefined && report.buy_below !== null) {
        const el = document.getElementById('researchBuyBelow');
        if (el && !el.value) el.value = report.buy_below;
    }
    if (report.sell_above !== undefined && report.sell_above !== null) {
        const el = document.getElementById('researchSellAbove');
        if (el && !el.value) el.value = report.sell_above;
    }
}

// Open the research modal for a symbol, load cached report + targets
window.openResearchModal = async function(symbol) {
    _researchSymbol = symbol;
    const symEl = document.getElementById('researchModalSymbol');
    if (symEl) symEl.textContent = symbol;
    const linksEl = document.getElementById('researchModalLinks');
    if (linksEl) linksEl.innerHTML = assetLinks(symbol);
    const statusEl = document.getElementById('researchTargetsStatus');
    if (statusEl) statusEl.textContent = '';
    const buyEl = document.getElementById('researchBuyBelow');
    const sellEl = document.getElementById('researchSellAbove');
    if (buyEl) buyEl.value = '';
    if (sellEl) sellEl.value = '';
    const body = document.getElementById('researchReportBody');
    if (body) body.innerHTML = '<p class="text-muted text-center py-4 mb-0"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</p>';

    new bootstrap.Modal(document.getElementById('researchModal')).show();

    // Load cached report and saved price targets in parallel
    try {
        const [report, targets] = await Promise.all([
            window.apiClient.getResearchReport(symbol),
            window.apiClient.getPriceTargets(symbol).catch(() => ({}))
        ]);
        if (targets && targets.buy_below !== undefined && targets.buy_below !== null && buyEl) buyEl.value = targets.buy_below;
        if (targets && targets.sell_above !== undefined && targets.sell_above !== null && sellEl) sellEl.value = targets.sell_above;
        renderResearchReport(report);
    } catch (err) {
        if (body) body.innerHTML = `<p class="text-danger text-center py-4 mb-0">Error: ${err.message}</p>`;
    }
};

// Wire up Generate button + targets form (called once at init)
function setupResearchModal() {
    const genBtn = document.getElementById('researchGenerateBtn');
    if (genBtn) {
        genBtn.addEventListener('click', async () => {
            if (!_researchSymbol) return;
            const orig = genBtn.innerHTML;
            genBtn.disabled = true;
            genBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Analyzing…';
            const body = document.getElementById('researchReportBody');
            if (body) body.innerHTML = '<p class="text-muted text-center py-4 mb-0"><span class="spinner-border spinner-border-sm me-2"></span>Running analysis (this can take ~10s)…</p>';
            try {
                const report = await window.apiClient.generateResearchReport(_researchSymbol);
                renderResearchReport(report);
            } catch (err) {
                if (body) body.innerHTML = `<p class="text-danger text-center py-4 mb-0">Error: ${err.message}</p>`;
            } finally {
                genBtn.disabled = false;
                genBtn.innerHTML = orig;
            }
        });
    }

    const form = document.getElementById('researchTargetsForm');
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (!_researchSymbol) return;
            const statusEl = document.getElementById('researchTargetsStatus');
            const buyVal = document.getElementById('researchBuyBelow').value;
            const sellVal = document.getElementById('researchSellAbove').value;
            const body = {
                buy_below: buyVal === '' ? null : parseFloat(buyVal),
                sell_above: sellVal === '' ? null : parseFloat(sellVal)
            };
            try {
                await window.apiClient.setPriceTargets(_researchSymbol, body);
                if (statusEl) { statusEl.className = 'small mt-2 text-success'; statusEl.textContent = 'Targets saved.'; }
            } catch (err) {
                if (statusEl) { statusEl.className = 'small mt-2 text-danger'; statusEl.textContent = 'Error: ' + err.message; }
            }
        });
    }
}

// ---------------------------------------------------------------------------
// Export helpers (Transactions page buttons)
// ---------------------------------------------------------------------------

function setupExportButtons() {
    const csvBtn = document.getElementById('exportCsvBtn');
    const pdtBtn = document.getElementById('exportPdtBtn');

    if (csvBtn) csvBtn.addEventListener('click', async () => {
        try {
            await window.apiClient.downloadBlob(window.apiClient.baseURL + '/api/v1/export/csv', 'transactions.csv');
        } catch (err) { alert('Export error: ' + err.message); }
    });
    if (pdtBtn) pdtBtn.addEventListener('click', async () => {
        try {
            await window.apiClient.downloadBlob(window.apiClient.baseURL + '/api/v1/export/pdt', 'portfolio_pdt.xlsx');
        } catch (err) { alert('Export error: ' + err.message); }
    });
}

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------
// Manual "Add Transaction" modal — buy / sell / dividend / stock split.
function setupAddTransaction() {
    const form = document.getElementById('addTransactionForm');
    const modalEl = document.getElementById('addTransactionModal');
    if (!form || !modalEl) return;
    const $ = id => document.getElementById(id);
    const typeSel = $('transactionType');
    const hint = $('transactionTypeHint');
    const priceInput = $('transactionPrice');
    let populated = false;

    async function populate() {
        if (populated) return;
        populated = true;
        try {
            const assets = await window.apiClient.getAssets();
            (assets || []).forEach(a => {
                const o = document.createElement('option');
                o.value = a.id; o.textContent = `${a.symbol} — ${a.name || ''}`;
                $('transactionAsset').appendChild(o);
            });
        } catch (e) { /* ignore */ }
        try {
            const pfs = await window.apiClient.getPortfolios();
            (pfs || []).forEach(p => {
                const o = document.createElement('option');
                o.value = p.id; o.textContent = p.name;
                $('transactionPortfolio').appendChild(o);
            });
            selectDefaultBroker($('transactionPortfolio'));
        } catch (e) { /* ignore */ }
    }
    modalEl.addEventListener('show.bs.modal', populate);

    // Split = ratio in Quantity, price irrelevant.
    typeSel.addEventListener('change', () => {
        if (typeSel.value === 'split') {
            hint.textContent = 'For a split, put the ratio in Quantity (2-for-1 → 2; 1-for-10 reverse → 0.1). Price is ignored.';
            hint.style.display = '';
            priceInput.value = '0'; priceInput.required = false;
        } else {
            hint.style.display = 'none';
            priceInput.required = true;
        }
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const type = typeSel.value;
        const qty = parseFloat($('transactionQuantity').value) || 0;
        const price = type === 'split' ? 0 : (parseFloat($('transactionPrice').value) || 0);
        const fees = parseFloat($('transactionFees').value) || 0;
        let total;
        if (type === 'split') total = 0;
        else if (type === 'dividend') total = qty * price;
        else if (type === 'sell') total = qty * price - fees;
        else total = qty * price + fees;  // buy
        const payload = {
            asset_id: parseInt($('transactionAsset').value),
            transaction_type: type,
            quantity: qty,
            price: price,
            total_amount: total,
            transaction_date: $('transactionDate').value,
            portfolio_id: $('transactionPortfolio').value ? parseInt($('transactionPortfolio').value) : null,
            fees: fees,
            description: $('transactionNotes').value || null,
        };
        if (!payload.asset_id || !type || !payload.transaction_date || qty <= 0) {
            alert('Asset, type, date and a positive quantity are required.'); return;
        }
        try {
            await window.apiClient.createTransaction(payload);
            bootstrap.Modal.getInstance(modalEl).hide();
            form.reset(); hint.style.display = 'none'; priceInput.required = true;
            if (window.pageManager) window.pageManager.loadTransactionsPage();
        } catch (err) {
            alert('Error adding transaction: ' + err.message);
        }
    });
}

// Research workbench page
function setupResearchPage() {
    const page = document.getElementById('researchPage');
    if (!page || page.dataset.wired) return;
    page.dataset.wired = '1';
    const $ = id => document.getElementById(id);
    const money = (v, cur) => (v == null ? '—' : Fmt.num(v, 2, 2) + (cur ? ' ' + cur : ''));
    let R = { symbol: null, currency: '', price: 0, fundamentals: {}, llm: null };
    // Autocomplete suggestions: {symbol, name, currency, source, acronym, aliases}
    // — populated on page open.
    let suggestions = [];

    // Tabs
    page.querySelectorAll('#researchTabs [data-rtab]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            page.querySelectorAll('#researchTabs [data-rtab]').forEach(n => n.classList.remove('active'));
            a.classList.add('active');
            const t = a.dataset.rtab;
            $('researchWorkbench').style.display = t === 'workbench' ? '' : 'none';
            $('researchCompare').style.display = t === 'compare' ? '' : 'none';
            if (t === 'compare') loadCompare();
        });
    });

    function recompute() {
        const method = $('rvMethod').value;
        page.querySelectorAll('[data-method]').forEach(el => {
            el.style.display = el.dataset.method === method ? '' : 'none';
        });
        let fair = null;
        if (method === 'pe') {
            const eps = parseFloat($('rvEps').value), pe = parseFloat($('rvTargetPe').value);
            if (eps && pe) fair = eps * pe;
        } else {
            const div = parseFloat($('rvDiv').value), ty = parseFloat($('rvTargetYield').value);
            if (div && ty) fair = div / (ty / 100);
        }
        const mos = parseFloat($('rvMos').value) || 0, prem = parseFloat($('rvPremium').value) || 0;
        const buy = fair != null ? fair * (1 - mos / 100) : null;
        const sell = fair != null ? fair * (1 + prem / 100) : null;
        $('rvFairValue').textContent = money(fair, R.currency);
        $('rvBuyBelow').textContent = money(buy, R.currency);
        $('rvSellAbove').textContent = money(sell, R.currency);
        if (fair && R.price) {
            const up = (fair - R.price) / R.price * 100;
            $('rvUpside').innerHTML = `Upside to fair value: <strong class="${up >= 0 ? 'text-success' : 'text-danger'}">${anFmtPct(up)}</strong> (price ${money(R.price, R.currency)})`;
        } else $('rvUpside').textContent = '';
        return { method, fair, buy, sell };
    }
    ['rvMethod', 'rvEps', 'rvTargetPe', 'rvDiv', 'rvTargetYield', 'rvMos', 'rvPremium'].forEach(id => {
        $(id).addEventListener('input', recompute);
        $(id).addEventListener('change', recompute);
    });

    // Human-readable labels for the camelCase keys yfinance returns.
    const FUND_LABELS = {
        currentPrice: 'Current price', marketCap: 'Market cap',
        trailingPE: 'Trailing P/E', forwardPE: 'Forward P/E',
        trailingEps: 'Trailing EPS', forwardEps: 'Forward EPS',
        dividendRate: 'Dividend rate', dividendYield: 'Dividend yield',
        fiftyTwoWeekLow: '52-week low', fiftyTwoWeekHigh: '52-week high',
        fiftyDayAverage: '50-day average', twoHundredDayAverage: '200-day average',
        beta: 'Beta', priceToBook: 'Price / book', bookValue: 'Book value',
        payoutRatio: 'Payout ratio', profitMargins: 'Profit margin',
        operatingMargins: 'Operating margin', grossMargins: 'Gross margin',
        returnOnEquity: 'Return on equity', returnOnAssets: 'Return on assets',
        revenueGrowth: 'Revenue growth', earningsGrowth: 'Earnings growth',
        debtToEquity: 'Debt / equity', freeCashflow: 'Free cash flow',
        operatingCashflow: 'Operating cash flow', totalRevenue: 'Total revenue',
        totalCash: 'Total cash', totalDebt: 'Total debt', ebitda: 'EBITDA',
        enterpriseValue: 'Enterprise value', sector: 'Sector', industry: 'Industry',
        country: 'Country', longName: 'Name', shortName: 'Name', currency: 'Currency',
        recommendationKey: 'Analyst consensus', recommendationMean: 'Analyst rating (1=buy,5=sell)',
        targetMeanPrice: 'Analyst mean target', targetHighPrice: 'Analyst high target',
        targetLowPrice: 'Analyst low target', numberOfAnalystOpinions: '# analysts',
        previousClose: 'Previous close',
    };
    // Yahoo's recommendationKey is a code — show it human-readable.
    const REC_LABELS = {
        strong_buy: 'Strong buy', buy: 'Buy', hold: 'Hold',
        underperform: 'Underperform', sell: 'Sell', none: 'No rating',
    };
    const FUND_PCT = new Set(['dividendYield', 'payoutRatio', 'profitMargins',
        'operatingMargins', 'grossMargins', 'returnOnEquity', 'returnOnAssets',
        'revenueGrowth', 'earningsGrowth']);
    const FUND_BIG = new Set(['marketCap', 'freeCashflow', 'operatingCashflow',
        'totalRevenue', 'totalCash', 'totalDebt', 'ebitda', 'enterpriseValue']);
    function humanizeFundKey(k) {
        if (FUND_LABELS[k]) return FUND_LABELS[k];
        // camelCase / PascalCase → spaced + capitalized; keep digit groups (52 → "52")
        return k.replace(/([a-z])([A-Z])/g, '$1 $2')
            .replace(/([A-Za-z])([0-9])/g, '$1 $2')
            .replace(/^./, c => c.toUpperCase());
    }
    function compactNum(v) {
        const a = Math.abs(v);
        if (a >= 1e12) return (v / 1e12).toFixed(2) + 'T';
        if (a >= 1e9) return (v / 1e9).toFixed(2) + 'B';
        if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
        if (a >= 1e3) return (v / 1e3).toFixed(2) + 'k';
        return Fmt.num(v, 0, 2);
    }
    function fmtFundVal(k, v) {
        if (k === 'recommendationKey') return REC_LABELS[v] || String(v).replace(/_/g, ' ');
        if (typeof v !== 'number') return v;
        if (FUND_PCT.has(k)) return (v * 100).toFixed(2) + '%';
        if (FUND_BIG.has(k)) return compactNum(v);
        return Fmt.num(v, 0, 2);
    }
    function renderFundamentals(f) {
        const keys = Object.keys(f || {}).filter(k => k !== 'symbol' && f[k] != null);
        $('rsFundamentals').innerHTML = keys.length
            ? keys.map(k => `<div class="d-flex justify-content-between"><span class="text-muted">${humanizeFundKey(k)}</span><span>${fmtFundVal(k, f[k])}</span></div>`).join('')
            : '<span class="text-muted">No fundamentals available.</span>';
    }

    async function loadHistory(sym) {
        try {
            const notes = await window.apiClient.researchHistory(sym);
            $('rvHistory').innerHTML = notes.length ? notes.map(n => `
                <button class="list-group-item list-group-item-action" data-note='${JSON.stringify({ thesis: n.thesis, conviction: n.conviction, method: n.method, assumptions: n.assumptions }).replace(/'/g, "&#39;")}'>
                    <div class="d-flex justify-content-between"><strong>${money(n.fair_value, '')}</strong><small class="text-muted">${Fmt.date(n.created_at)}</small></div>
                    <div class="small text-muted">buy ${money(n.buy_below, '')} · sell ${money(n.sell_above, '')} · conv ${n.conviction ?? '—'}</div>
                </button>`).join('') : '<div class="p-3 text-muted small">No saved research yet.</div>';
            $('rvHistory').querySelectorAll('[data-note]').forEach(b => b.addEventListener('click', () => {
                try {
                    const n = JSON.parse(b.dataset.note);
                    if (n.thesis) $('rvThesis').value = n.thesis;
                    if (n.conviction) $('rvConviction').value = n.conviction;
                    if (n.method) $('rvMethod').value = n.method;
                    const a = n.assumptions || {};
                    if (a.eps != null) $('rvEps').value = a.eps;
                    if (a.target_pe != null) $('rvTargetPe').value = a.target_pe;
                    if (a.annual_dividend != null) $('rvDiv').value = a.annual_dividend;
                    if (a.target_yield != null) $('rvTargetYield').value = a.target_yield;
                    if (a.margin_of_safety != null) $('rvMos').value = a.margin_of_safety;
                    if (a.premium != null) $('rvPremium').value = a.premium;
                    recompute();
                } catch (e) { /* ignore */ }
            }));
        } catch (e) { $('rvHistory').innerHTML = '<div class="p-3 text-danger small">' + e.message + '</div>'; }
    }

    const pnlCls = v => (v > 0 ? 'text-success' : v < 0 ? 'text-danger' : '');

    function renderSellCalc() {
        const d = R.pos || {};
        const qtyHeld = d.quantity || 0, avg = d.avg_cost || 0, price = d.current_price || 0, cur = d.currency;
        const out = $('rsSellResult');
        if (!qtyHeld) { out.innerHTML = '<span class="text-muted">No position to sell.</span>'; return; }
        let n = parseFloat($('rsSellQty').value);
        const pct = parseFloat($('rsSellPct').value);
        if ((isNaN(n) || n <= 0) && !isNaN(pct) && pct > 0) n = qtyHeld * pct / 100;
        if (isNaN(n) || n <= 0) { out.innerHTML = '<span class="text-muted">Enter a quantity or % to see proceeds and gain/loss.</span>'; return; }
        n = Math.min(n, qtyHeld);
        const proceeds = n * price;
        const costOfSold = n * avg;
        const gain = proceeds - costOfSold;
        const gainPct = costOfSold > 0 ? gain / costOfSold * 100 : 0;
        out.innerHTML = `
            <div class="d-flex justify-content-between"><span class="text-muted">Selling</span><strong>${Fmt.num(n, 0, 4)} sh (${(n / qtyHeld * 100).toFixed(1)}%)</strong></div>
            <div class="d-flex justify-content-between"><span class="text-muted">Proceeds</span><span>${money(proceeds, cur)}</span></div>
            <div class="d-flex justify-content-between"><span class="text-muted">Cost of sold shares</span><span>${money(costOfSold, cur)}</span></div>
            <div class="d-flex justify-content-between"><span class="text-muted">Realised gain/loss</span><strong class="${pnlCls(gain)}">${money(gain, cur)} (${anFmtPct(gainPct)})</strong></div>
            <div class="d-flex justify-content-between"><span class="text-muted">Remaining</span><span>${Fmt.num(qtyHeld - n, 0, 4)} sh</span></div>`;
    }

    function renderCostChart(evolution, price, cur) {
        const svg = $('rsCostChart');
        const pts = (evolution || []).filter(p => p.avg_cost > 0);
        if (pts.length < 1) { svg.innerHTML = '<text x="8" y="20" font-size="12" fill="#94a3b8">No cost history.</text>'; return; }
        const W = svg.clientWidth || 460, H = 180, PAD = { t: 14, r: 14, b: 38, l: 60 };
        const iW = W - PAD.l - PAD.r, iH = H - PAD.t - PAD.b;
        // Scale across avg cost, the live price, AND every transaction price so
        // entry points are visible even when the running average looks flat.
        const txPrices = pts.map(p => p.tx_price).filter(v => v > 0);
        const all = pts.map(p => p.avg_cost).concat(price ? [price] : []).concat(txPrices);
        let lo = Math.min(...all), hi = Math.max(...all);
        const pad = (hi - lo) * 0.08 || hi * 0.05 || 1;
        lo -= pad; hi += pad;
        const rng = (hi - lo) || 1;
        const n = pts.length;
        const x = i => PAD.l + (n === 1 ? iW / 2 : i / (n - 1) * iW);
        const y = v => PAD.t + iH - (v - lo) / rng * iH;
        const cy = v => Math.max(PAD.t, Math.min(PAD.t + iH, y(v)));
        const sym = ({ EUR: '€', USD: '$', GBP: '£' })[cur] || '';
        const fmtY = v => sym + Fmt.num(v, 0, v >= 100 ? 0 : 2);
        const path = pts.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(p.avg_cost).toFixed(1)).join(' ');
        // Transaction markers (buys green, sells red) at their actual price
        const markers = pts.map((p, i) => {
            if (!p.tx_price) return '';
            const col = p.tx_type === 'sell' ? '#dc2626' : p.tx_type === 'buy' ? '#16a34a' : '#94a3b8';
            return `<circle cx="${x(i).toFixed(1)}" cy="${cy(p.tx_price).toFixed(1)}" r="2.8" fill="${col}" opacity="0.8"><title>${p.tx_type} @ ${fmtY(p.tx_price)} (${p.date})</title></circle>`;
        }).join('');
        const priceLine = price ? `<line x1="${PAD.l}" y1="${y(price).toFixed(1)}" x2="${(PAD.l + iW).toFixed(1)}" y2="${y(price).toFixed(1)}" stroke="#16a34a" stroke-width="1.5" stroke-dasharray="4 3"/><text x="${(PAD.l + iW).toFixed(1)}" y="${(y(price) - 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#16a34a">price ${fmtY(price)}</text>` : '';
        // Y-axis ticks (currency) + gridlines
        const yt = [lo + rng * 0.1, lo + rng * 0.5, hi - rng * 0.1];
        const yTicks = yt.map(v => `
            <line x1="${PAD.l}" y1="${y(v).toFixed(1)}" x2="${PAD.l + iW}" y2="${y(v).toFixed(1)}" stroke="#eef2f6"/>
            <text x="${PAD.l - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#64748b">${fmtY(v)}</text>`).join('');
        // X-axis date ticks (~4)
        const step = Math.max(1, Math.floor((n - 1) / 3));
        const xTicks = [];
        for (let i = 0; i < n; i += step) xTicks.push(i);
        if (xTicks[xTicks.length - 1] !== n - 1) xTicks.push(n - 1);
        const xLabels = xTicks.map(i => {
            const d = new Date(pts[i].date);
            const lbl = isNaN(d) ? pts[i].date : d.toLocaleDateString(Fmt.loc(), { month: 'short', year: '2-digit' });
            return `<text x="${x(i).toFixed(1)}" y="${(PAD.t + iH + 14).toFixed(1)}" text-anchor="middle" font-size="10" fill="#64748b">${lbl}</text>`;
        }).join('');
        svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
        svg.innerHTML = `${yTicks}
            <line x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t + iH}" stroke="#cbd5e1"/>
            <line x1="${PAD.l}" y1="${PAD.t + iH}" x2="${PAD.l + iW}" y2="${PAD.t + iH}" stroke="#cbd5e1"/>
            ${xLabels}
            ${priceLine}
            <path d="${path}" fill="none" stroke="#2563eb" stroke-width="2"/>
            ${markers}
            <circle cx="${x(n - 1).toFixed(1)}" cy="${y(pts[n - 1].avg_cost).toFixed(1)}" r="3.5" fill="#2563eb"/>
            <text x="${PAD.l}" y="${H - 4}" font-size="10" fill="#64748b">avg cost (blue line) · buys (green) / sells (red) · current price (green dash)</text>`;
    }

    function renderTransactions(txns, cur) {
        const tb = $('rsTxBody');
        if (!txns || !txns.length) { tb.innerHTML = '<tr><td colspan="5" class="text-center text-muted small">No transactions.</td></tr>'; return; }
        const badge = { buy: 'bg-success', sell: 'bg-danger', dividend: 'bg-info', split: 'bg-warning text-dark' };
        tb.innerHTML = txns.map(t => `
            <tr>
                <td>${Fmt.date(t.date)}</td>
                <td><span class="badge ${badge[t.type] || 'bg-secondary'}">${t.type}</span></td>
                <td class="text-end">${Fmt.num(t.quantity, 0, 4)}</td>
                <td class="text-end">${money(t.price, t.currency || cur)}</td>
                <td class="text-end">${money(t.total, t.currency || cur)}</td>
            </tr>`).join('');
    }

    function renderPosition(d) {
        const card = $('rsPositionCard');
        if (!d.held) { card.style.display = 'none'; return; }
        card.style.display = '';
        const cur = d.currency;
        $('rsCostBasis').textContent = money(d.cost_basis, cur);
        $('rsMarketValue').textContent = money(d.market_value, cur);
        const u = d.unrealised_gain || 0;
        $('rsUnrealised').innerHTML = `<span class="${pnlCls(u)}">${money(u, cur)}${d.unrealised_pct != null ? ` (${anFmtPct(d.unrealised_pct)})` : ''}</span>`;
        const r = d.realised_gain || 0;
        $('rsRealised').innerHTML = `<span class="${pnlCls(r)}">${money(r, cur)}</span>`;
        $('rsSellQty').value = ''; $('rsSellPct').value = '';
        renderSellCalc();
        renderCostChart(d.cost_evolution, d.current_price, cur);
        renderTransactions(d.transactions, cur);
    }

    ['rsSellQty', 'rsSellPct'].forEach(id => {
        const el = $(id);
        if (el) el.addEventListener('input', () => {
            // typing in one clears the other so they don't fight
            if (id === 'rsSellQty') $('rsSellPct').value = '';
            else $('rsSellQty').value = '';
            renderSellCalc();
        });
    });

    async function load(sym) {
        sym = (sym || '').trim().toUpperCase();
        if (!sym) return;
        $('researchHint').textContent = 'Loading ' + sym + '…';
        try {
            const d = await window.apiClient.researchLookup(sym);
            R = { symbol: sym, currency: d.currency, price: d.current_price, fundamentals: d.fundamentals || {}, llm: null, pos: d, held: d.held, targets: d.targets || null, onWatchlist: d.on_watchlist, watchBuyBelow: d.watch_buy_below };
            $('researchBody').style.display = '';
            $('researchReportBtn').style.display = '';
            $('rsName').textContent = `${sym} — ${d.name || ''}`;
            $('rsHeld').textContent = d.held ? 'held' : 'not held';
            $('rsHeld').className = 'badge ms-1 ' + (d.held ? 'bg-success' : 'bg-secondary');
            // Watchlist badge (independent of holding)
            const watch = $('rsWatch');
            if (d.on_watchlist) {
                watch.style.display = '';
                watch.textContent = d.watch_buy_below != null ? `watchlist · buy < ${money(d.watch_buy_below, d.currency)}` : 'watchlist';
            } else { watch.style.display = 'none'; }
            $('rsPrice').textContent = money(d.current_price, d.currency);
            $('rsAvgCost').textContent = d.avg_cost ? money(d.avg_cost, d.currency) : '—';
            $('rsQty').textContent = d.quantity || '—';
            renderPosition(d);
            renderFundamentals(d.fundamentals);
            // Pre-fill calculator from fundamentals + any existing target.
            $('rvEps').value = d.fundamentals?.trailingEps ?? '';
            $('rvTargetPe').value = d.fundamentals?.trailingPE ? Math.round(d.fundamentals.trailingPE) : 20;
            $('rvDiv').value = d.fundamentals?.dividendRate ?? '';
            $('rvTargetYield').value = d.fundamentals?.dividendYield ? (d.fundamentals.dividendYield * 100).toFixed(1) : 4;
            const t = d.targets || {};
            if (d.latest_note && d.latest_note.thesis) $('rvThesis').value = d.latest_note.thesis;
            recompute();
            $('rvLlmBody').innerHTML = '<span class="text-muted small">Click “Research with web” for an LLM read.</span>';
            $('rvSaveMsg').textContent = '';
            loadHistory(sym);
            $('researchHint').textContent = '';
            if (window.initTooltips) initTooltips();
        } catch (e) {
            $('researchHint').innerHTML = '<span class="text-danger">' + (e.message || 'lookup failed') + '</span>';
        }
    }

    // ── Ticker autocomplete ────────────────────────────────────────────────
    const tickerInput = $('researchTicker');
    const suggestBox = $('researchSuggest');
    let activeIdx = -1;

    function hideSuggest() { suggestBox.style.display = 'none'; activeIdx = -1; }

    function matchSuggestions(q) {
        return AssetSearch.match(q, suggestions, 10);
    }

    function renderSuggest(q) {
        const matches = matchSuggestions(q);
        if (!matches.length) { hideSuggest(); return; }
        suggestBox.innerHTML = matches.map((s, i) => `
            <button type="button" class="list-group-item list-group-item-action py-1 px-2" data-sym="${s.symbol}" data-idx="${i}">
                <div class="d-flex justify-content-between align-items-center">
                    <span><strong>${esc(s.symbol)}</strong>${s.currency ? ` <span class="badge bg-light text-secondary border ms-1">${s.currency}</span>` : ''}</span>
                    <span class="badge ${s.source === 'watchlist' ? 'bg-info' : 'bg-secondary'} ms-2">${s.source}</span>
                </div>
                <div class="small text-muted text-truncate">${esc(s.name || '')}</div>
            </button>`).join('');
        suggestBox.querySelectorAll('[data-sym]').forEach(b => {
            b.addEventListener('mousedown', e => {
                // mousedown (not click) so it fires before the input blur hides the box
                e.preventDefault();
                tickerInput.value = b.dataset.sym;
                hideSuggest();
                load(b.dataset.sym);
            });
        });
        activeIdx = -1;
        suggestBox.style.display = '';
    }

    tickerInput.addEventListener('input', () => renderSuggest(tickerInput.value));
    tickerInput.addEventListener('focus', () => { if (tickerInput.value) renderSuggest(tickerInput.value); });
    tickerInput.addEventListener('blur', () => setTimeout(hideSuggest, 150));
    tickerInput.addEventListener('keydown', e => {
        const items = suggestBox.querySelectorAll('[data-sym]');
        if (suggestBox.style.display !== 'none' && items.length) {
            if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, items.length - 1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); }
            else if (e.key === 'Enter') {
                if (activeIdx >= 0) { e.preventDefault(); items[activeIdx].dispatchEvent(new Event('mousedown')); return; }
            }
            items.forEach((it, i) => it.classList.toggle('active', i === activeIdx));
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') return;
        }
        if (e.key === 'Enter') { hideSuggest(); load(tickerInput.value); }
        if (e.key === 'Escape') hideSuggest();
    });

    $('researchLoadBtn').addEventListener('click', () => { hideSuggest(); load(tickerInput.value); });

    // Download an archivable Markdown research report for the loaded ticker.
    $('researchReportBtn').addEventListener('click', async () => {
        if (!R.symbol) return;
        const btn = $('researchReportBtn'); const orig = btn.innerHTML;
        btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Preparing…';
        try {
            const url = window.apiClient.baseURL + `/api/v1/research/${encodeURIComponent(R.symbol)}/report?format=md&download=true`;
            await window.apiClient.downloadBlob(url, `research_${esc(R.symbol)}_${new Date().toISOString().slice(0, 10)}.md`);
        } catch (e) {
            alert('Could not generate report: ' + (e.message || e));
        } finally { btn.disabled = false; btn.innerHTML = orig; }
    });

    $('rvGenerateBtn').addEventListener('click', async () => {
        if (!R.symbol) return;
        const btn = $('rvGenerateBtn'); const orig = btn.innerHTML;
        btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Researching…';
        try {
            const r = await window.apiClient.generateResearchReport(R.symbol);
            R.llm = r;
            const sources = (r.sources || []).map(s => `<li><a href="${s.url}" target="_blank" rel="noopener">${s.title || s.url}</a> <span class="text-muted small">${s.publisher || ''}</span></li>`).join('');
            const recCls = r.recommendation === 'BUY' ? 'success' : r.recommendation === 'SELL' ? 'danger' : 'secondary';
            $('rvLlmBody').innerHTML = `
                <div class="d-flex gap-2 align-items-center mb-2">
                    <span class="badge bg-${recCls}">${r.recommendation || '—'}</span>
                    <span class="text-muted small">confidence: ${r.confidence || '—'}</span>
                    ${r.fair_value != null ? `<span class="ms-auto">LLM fair value: <strong>${money(r.fair_value, R.currency)}</strong> <button class="btn btn-sm btn-outline-secondary ms-1" id="rvPullFair">Use ↓</button></span>` : ''}
                </div>
                <p class="mb-2">${r.summary || ''}</p>
                ${r.rationale ? `<p class="small text-muted">${r.rationale}</p>` : ''}
                ${(r.risks || []).length ? `<div class="small"><strong>Risks:</strong> ${(r.risks || []).join('; ')}</div>` : ''}
                ${(r.catalysts || []).length ? `<div class="small"><strong>Catalysts:</strong> ${(r.catalysts || []).join('; ')}</div>` : ''}
                ${sources ? `<div class="small mt-2"><strong>Sources (recent news):</strong><ul class="mb-0">${sources}</ul></div>` : ''}`;
            const pull = $('rvPullFair');
            if (pull) pull.addEventListener('click', () => {
                // Set EPS-implied target so the calculator reflects the LLM fair value when possible.
                if (r.fair_value && R.fundamentals?.trailingEps) {
                    $('rvMethod').value = 'pe';
                    $('rvEps').value = R.fundamentals.trailingEps;
                    $('rvTargetPe').value = (r.fair_value / R.fundamentals.trailingEps).toFixed(1);
                }
                recompute();
            });
        } catch (e) {
            $('rvLlmBody').innerHTML = '<span class="text-danger small">' + (e.message || 'failed') + '</span>';
        } finally { btn.disabled = false; btn.innerHTML = orig; }
    });

    $('rvSaveBtn').addEventListener('click', async () => {
        if (!R.symbol) { alert('Load a ticker first.'); return; }
        const c = recompute();
        const assumptions = {
            eps: parseFloat($('rvEps').value) || null,
            target_pe: parseFloat($('rvTargetPe').value) || null,
            annual_dividend: parseFloat($('rvDiv').value) || null,
            target_yield: parseFloat($('rvTargetYield').value) || null,
            margin_of_safety: parseFloat($('rvMos').value) || null,
            premium: parseFloat($('rvPremium').value) || null,
        };
        // Whether saving these values would change an existing alert target.
        // Held assets compare against price_targets (buy + sell); watchlisted
        // symbols against their buy_below. If a different target already exists,
        // ask before overwriting (the user may have set it deliberately).
        let updateTargets = true;
        const hasNew = c.buy != null || c.sell != null || c.fair != null;
        if (hasNew) {
            const t = R.targets || {};
            const near = (a, b) => a != null && b != null && Math.abs(a - b) <= Math.max(0.01, Math.abs(b) * 0.005);
            const existingBuy = R.held ? t.buy_below : (R.onWatchlist ? R.watchBuyBelow : null);
            const existingSell = R.held ? t.sell_above : null;
            const hasExisting = existingBuy != null || existingSell != null;
            const differs = (existingBuy != null && !near(c.buy, existingBuy)) ||
                            (existingSell != null && !near(c.sell, existingSell));
            if (hasExisting && differs) {
                const cur = R.currency || '';
                const fmt = v => v == null ? '—' : Fmt.num(v, 2, 2) + (cur ? ' ' + cur : '');
                updateTargets = confirm(
                    `${esc(R.symbol)} already has an alert target:\n` +
                    `  buy ${fmt(existingBuy)} · sell ${fmt(existingSell)}\n\n` +
                    `Overwrite with your researched values?\n` +
                    `  buy ${fmt(c.buy)} · sell ${fmt(c.sell)}\n\n` +
                    `OK = overwrite (alerts use new values)\n` +
                    `Cancel = keep existing target, save research note only`);
            }
        }
        try {
            const res = await window.apiClient.researchSave(R.symbol, {
                thesis: $('rvThesis').value || null,
                conviction: parseInt($('rvConviction').value),
                method: c.method,
                assumptions,
                fair_value: c.fair, buy_below: c.buy, sell_above: c.sell,
                current_price: R.price,
                llm_summary: R.llm ? R.llm.summary : null,
                sources: R.llm ? R.llm.sources : null,
                update_targets: updateTargets,
            });
            let msg;
            if (res.targets_updated || res.watchlist_updated) {
                const where = [res.targets_updated ? 'price target' : null, res.watchlist_updated ? 'watchlist buy zone' : null].filter(Boolean).join(' + ');
                msg = `<span class="text-success">Saved. Updated ${where} — alerts active.</span>`;
            } else if (hasNew && !updateTargets) {
                msg = '<span class="text-success">Saved as research note. Existing alert target kept.</span>';
            } else if (!res.held && !res.watchlist_updated) {
                msg = '<span class="text-success">Saved as research only (not held / not watchlisted — add to watchlist to get alerts).</span>';
            } else {
                msg = '<span class="text-success">Saved.</span>';
            }
            $('rvSaveMsg').innerHTML = msg;
            // Reflect the new target locally so a second save doesn't re-prompt.
            if (res.targets_updated) R.targets = { buy_below: c.buy, sell_above: c.sell, fair_value: c.fair };
            if (res.watchlist_updated) R.watchBuyBelow = c.buy;
            loadHistory(R.symbol);
        } catch (e) { $('rvSaveMsg').innerHTML = '<span class="text-danger">' + e.message + '</span>'; }
    });

    async function loadCompare() {
        const body = $('researchCompareBody');
        body.innerHTML = '<tr><td colspan="8" class="text-center py-3"><span class="spinner-border spinner-border-sm"></span></td></tr>';
        try {
            const rows = await window.apiClient.researchCompare();
            if (!rows.length) { body.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">No saved research yet.</td></tr>'; return; }
            body.innerHTML = rows.map(r => {
                const up = r.upside_pct;
                const upCls = up == null ? 'text-muted' : up >= 0 ? 'text-success' : 'text-danger';
                return `<tr style="cursor:pointer" data-sym="${r.symbol}">
                    <td class="ps-3"><strong>${esc(r.symbol)}</strong></td>
                    <td class="text-end">${money(r.current_price, '')}</td>
                    <td class="text-end">${money(r.fair_value, '')}</td>
                    <td class="text-end ${upCls}">${up == null ? '—' : anFmtPct(up)}</td>
                    <td class="text-end">${money(r.buy_below, '')}</td>
                    <td class="text-end">${money(r.sell_above, '')}</td>
                    <td class="text-center">${r.conviction ?? '—'}</td>
                    <td>${Fmt.date(r.updated_at)}</td></tr>`;
            }).join('');
            body.querySelectorAll('[data-sym]').forEach(tr => tr.addEventListener('click', () => {
                page.querySelector('#researchTabs [data-rtab="workbench"]').click();
                $('researchTicker').value = tr.dataset.sym; load(tr.dataset.sym);
            }));
        } catch (e) { body.innerHTML = '<tr><td colspan="8" class="text-danger text-center py-3">' + e.message + '</td></tr>'; }
    }

    // Expose loader (builds the autocomplete suggestion list on each page open).
    window.loadResearchPage = async function () {
        try {
            const [holdings, watch] = await Promise.all([
                window.apiClient.getHoldings().catch(() => ({ holdings: [] })),
                window.apiClient.getWatchlist().catch(() => []),
            ]);
            const bySym = new Map();
            (holdings.holdings || []).forEach(h => {
                if (h.symbol && !bySym.has(h.symbol)) {
                    bySym.set(h.symbol, AssetSearch.enrich(h.symbol, h.name, { currency: h.currency || '', source: 'held' }));
                }
            });
            (Array.isArray(watch) ? watch : (watch.watchlist || [])).forEach(w => {
                if (w.symbol && !bySym.has(w.symbol)) {
                    bySym.set(w.symbol, AssetSearch.enrich(w.symbol, w.name, { currency: w.currency || '', source: 'watchlist' }));
                }
            });
            suggestions = [...bySym.values()];
        } catch (e) { /* ignore */ }
    };
}

// Settings modal — browser-local preferences (theme, formats, defaults, privacy)
function setupSettings() {
    const modalEl = document.getElementById('settingsModal');
    if (!modalEl) return;
    const bs = new bootstrap.Modal(modalEl);
    const $ = id => document.getElementById(id);

    function load() {
        $('setTheme').value = PREFS.theme;
        $('setPrivacy').checked = !!PREFS.privacy;
        $('setNumberLocale').value = PREFS.numberLocale || '';
        $('setDateFormat').value = PREFS.dateFormat;
        $('setDecimals').value = PREFS.decimals;
        $('setBenchmark').value = PREFS.benchmark;
        $('setLandingPage').value = PREFS.landingPage;
        $('setDefaultCurrency').value = PREFS.defaultCurrency || 'EUR';
        $('setRowsPerPage').value = PREFS.rowsPerPage;
        $('setHoldingsSort').value = PREFS.holdingsSort || 'value';
        $('setHideBelowEur').value = PREFS.hideBelowEur || 0;
        // Populate the broker select from the user's portfolios (by name)
        (async () => {
            try {
                const sel = $('setDefaultBroker');
                const pfs = await window.apiClient.getPortfolios();
                sel.innerHTML = '<option value="">— none —</option>' +
                    (pfs || []).map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('');
                sel.value = PREFS.defaultBroker || '';
            } catch (e) { /* ignore */ }
        })();
        // Pre-fill the username for the password form from the last login
        const u = localStorage.getItem('pfm_username');
        if (u && $('setPwUser')) $('setPwUser').value = u;
    }
    function openModal(e) { if (e) e.preventDefault(); load(); bs.show(); if (window.initTooltips) initTooltips(); }

    ['settingsBtn', 'settingsBtnOffcanvas'].forEach(id => {
        const el = $(id);
        if (el) el.addEventListener('click', openModal);
    });

    $('settingsSaveBtn').addEventListener('click', () => {
        PREFS.theme = $('setTheme').value;
        PREFS.privacy = $('setPrivacy').checked;
        PREFS.numberLocale = $('setNumberLocale').value;
        PREFS.dateFormat = $('setDateFormat').value;
        PREFS.decimals = Math.max(0, Math.min(6, parseInt($('setDecimals').value) || 0));
        PREFS.benchmark = $('setBenchmark').value;
        PREFS.landingPage = $('setLandingPage').value;
        PREFS.defaultCurrency = $('setDefaultCurrency').value || 'EUR';
        PREFS.rowsPerPage = Math.max(10, Math.min(500, parseInt($('setRowsPerPage').value) || 50));
        PREFS.defaultBroker = $('setDefaultBroker').value || '';
        PREFS.holdingsSort = $('setHoldingsSort').value || 'value';
        PREFS.hideBelowEur = Math.max(0, parseFloat($('setHideBelowEur').value) || 0);
        savePrefs();
        applyTheme();
        applyPrivacy();
        applyDefaultCurrency();
        // Reflect the default benchmark in the analytics selector if present.
        const bm = document.getElementById('anBenchmark');
        if (bm) bm.value = PREFS.benchmark;
        bs.hide();
        // Re-render the current page so number/date formatting updates everywhere.
        const active = document.querySelector('.page-content[style*="block"]');
        const page = active ? active.id.replace('Page', '') : null;
        if (page && window.navigationManager) window.navigationManager.showPage(page);
    });

    $('settingsResetBtn').addEventListener('click', () => {
        Object.assign(PREFS, PREFS_DEFAULTS);
        savePrefs();
        load();
        applyTheme();
        applyPrivacy();
    });

    // Change password
    const pwBtn = $('setChangePwBtn');
    if (pwBtn) pwBtn.addEventListener('click', async () => {
        const status = $('setChangePwStatus');
        const user = ($('setPwUser').value || '').trim();
        const cur = $('setPwCurrent').value;
        const nw = $('setPwNew').value;
        status.className = 'small text-muted';
        if (!user || !cur || !nw) { status.className = 'small text-danger'; status.textContent = 'Fill in all three fields.'; return; }
        if (nw.length < 8) { status.className = 'small text-danger'; status.textContent = 'New password must be at least 8 characters.'; return; }
        pwBtn.disabled = true;
        status.textContent = 'Changing…';
        try {
            await window.apiClient.changePassword(user, cur, nw);
            status.className = 'small text-success';
            status.textContent = 'Password changed.';
            $('setPwCurrent').value = ''; $('setPwNew').value = '';
        } catch (e) {
            status.className = 'small text-danger';
            status.textContent = e.message || 'Failed.';
        } finally { pwBtn.disabled = false; }
    });

    // Apply the saved default benchmark to the analytics selector on first load.
    const bm = document.getElementById('anBenchmark');
    if (bm && PREFS.benchmark) bm.value = PREFS.benchmark;
}

document.addEventListener('DOMContentLoaded', function() {
    window.apiClient         = createAPIClient();
    window.authManager       = createAuthManager();
    window.navigationManager = createNavigationManager();
    window.pageManager       = createPageManager();
    window.modalManager      = createModalManager();

    applyTheme();
    applyPrivacy();
    applyDefaultCurrency();
    setupSidebarSections();
    setupSettings();
    setupAddTransaction();
    setupResearchPage();

    window.authManager.setupLoginForm();
    window.authManager.setupLogout();
    window.navigationManager.setupNavigation();
    window.modalManager.setupAddAssetModal();
    setupEditTransactionModal();
    setupPortfoliosPage();
    setupFileImportModal();
    setupLlmImportModal();
    setupImportExportPage();
    setupChatPage();
    setupExportButtons();
    setupForecastPage();
    setupRebalanceForm();
    setupResearchModal();
    setupWatchlistPage();
    setupGoalsPage();

    const refreshHoldings = document.getElementById('refreshHoldings');
    if (refreshHoldings) {
        refreshHoldings.addEventListener('click', () => window.pageManager.loadHoldingsPage());
    }

    const refreshTxPage = document.getElementById('refreshTransactionsPage');
    if (refreshTxPage) {
        refreshTxPage.addEventListener('click', () => window.pageManager.loadTransactionsPage());
    }

    const refreshDashTx = document.getElementById('refreshTransactions');
    if (refreshDashTx) {
        refreshDashTx.addEventListener('click', () => window.pageManager.loadDashboardPage());
    }

    // Analytics page wiring
    const refreshAnalytics = document.getElementById('refreshAnalytics');
    if (refreshAnalytics) {
        refreshAnalytics.addEventListener('click', () => window.pageManager.loadAnalyticsPage());
    }
    const anBenchmark = document.getElementById('anBenchmark');
    if (anBenchmark) {
        anBenchmark.addEventListener('change', () => loadAnalyticsPerformance());
    }
    document.querySelectorAll('input[name="anPeriod"]').forEach(radio => {
        radio.addEventListener('change', () => loadAnalyticsPerformance());
    });
    const anTaxYear = document.getElementById('anTaxYear');
    if (anTaxYear) {
        anTaxYear.addEventListener('change', () => { loadAnalyticsTax(); loadAnalyticsTaxReport(); loadTaxOptimizer(); });
    }
    const anTaxReportCsvBtn = document.getElementById('anTaxReportCsvBtn');
    if (anTaxReportCsvBtn) {
        anTaxReportCsvBtn.addEventListener('click', downloadTaxReportCsv);
    }
    // Re-render the net worth chart on resize when the analytics page is
    // visible — debounced so a drag-resize doesn't fire a burst of reloads.
    let _nwResizeTimer = null;
    window.addEventListener('resize', () => {
        if (!(window.navigationManager && window.navigationManager.currentPage === 'analytics')) return;
        clearTimeout(_nwResizeTimer);
        _nwResizeTimer = setTimeout(() => loadAnalyticsNetworth(), 300);
    });

    // Global 401 handling: when the API key expires or is rotated, every /api
    // call returns 401. Clear it and show the login modal once — instead of a
    // confusing per-card error like "Failed to load bookings".
    if (!window.__fetchPatchedFor401) {
        window.__fetchPatchedFor401 = true;
        const _origFetch = window.fetch.bind(window);
        let _authPrompted = false;
        window.fetch = async function(input, init) {
            const resp = await _origFetch(input, init);
            try {
                const url = typeof input === 'string' ? input : (input && input.url) || '';
                if (resp.status === 401 && url.includes('/api/') && !url.includes('/auth/')) {
                    if (!_authPrompted) {
                        _authPrompted = true;
                        localStorage.removeItem('apiKey');
                        if (window.apiClient && window.apiClient.clearApiKey) window.apiClient.clearApiKey();
                        if (window.authManager && window.authManager.showLoginModal) window.authManager.showLoginModal();
                        setTimeout(() => { _authPrompted = false; }, 3000);
                    }
                }
            } catch (e) { /* ignore */ }
            return resp;
        };
    }

    const existingKey = localStorage.getItem('apiKey');
    if (existingKey) {
        window.apiClient.validateApiKey(existingKey).then(isValid => {
            if (isValid) {
                window.apiClient.setApiKey(existingKey);
                window.authManager.showDashboard();
            } else {
                localStorage.removeItem('apiKey');
                window.authManager.showLoginModal();
            }
        });
    } else {
        window.authManager.showLoginModal();
    }
});
