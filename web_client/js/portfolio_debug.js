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
            return (await resp.json()).api_key;
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

        async getHoldings() {
            try {
                const response = await fetch(this.baseURL + '/api/v1/portfolios/holdings', {
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

        async saveImportedTransactions(transactions, bookings = [], portfolioId = null, duplicateAction = 'skip') {
            const response = await fetch(this.baseURL + '/api/v1/import/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ transactions, bookings, portfolio_id: portfolioId, duplicate_action: duplicateAction })
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
};

// Shown above an import preview when some rows already exist in the DB. The
// <select id="ioDupAction"> value is read by the save handlers.
function _dupControl(transactions, bookings) {
    const dupTx = (transactions || []).filter(t => t.is_duplicate).length;
    const dupBk = (bookings || []).filter(b => b.is_duplicate).length;
    const total = dupTx + dupBk;
    if (total === 0) return '';
    return `
        <div class="alert alert-warning py-2 small d-flex flex-wrap align-items-center gap-2 mb-2">
            <span><i class="bi bi-exclamation-triangle me-1"></i><strong>${total}</strong> row(s) already exist (marked <span class="badge bg-warning text-dark">dup</span> below).</span>
            <label class="ms-auto mb-0 d-flex align-items-center">On duplicates:
                <select id="ioDupAction" class="form-select form-select-sm d-inline-block w-auto ms-1">
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

function _buildPreviewTable(transactions, bookings) {
    bookings = bookings || [];
    let bookingsSummary = '';
    if (bookings.length > 0) {
        const totalDeposits = bookings.filter(b => b.action === 'Deposit').reduce((s, b) => s + b.amount, 0);
        const totalWithdrawals = bookings.filter(b => b.action === 'Withdrawal').reduce((s, b) => s + b.amount, 0);
        const parts = [];
        if (totalDeposits > 0) parts.push(`${bookings.filter(b=>b.action==='Deposit').length} deposit(s) totalling ${totalDeposits.toFixed(2)}`);
        if (totalWithdrawals > 0) parts.push(`${bookings.filter(b=>b.action==='Withdrawal').length} withdrawal(s) totalling ${totalWithdrawals.toFixed(2)}`);
        bookingsSummary = `<div class="alert alert-info py-1 mb-2 small"><i class="bi bi-bank me-1"></i><strong>Bookings:</strong> ${parts.join(' + ')} — will be saved automatically.</div>`;
    }
    const dupControl = _dupControl(transactions, bookings);
    if (transactions.length === 0) {
        return bookingsSummary + dupControl + '<div class="alert alert-warning">No importable transactions found in this file.</div>';
    }
    const hasBroker = transactions.some(tx => tx.broker);
    const dupBadge = '<span class="badge bg-warning text-dark ms-1">dup</span>';
    const rows = transactions.map((tx, i) => `
        <tr class="${tx.is_duplicate ? 'table-warning' : ''}">
            <td><input class="form-check-input file-tx-select" type="checkbox" checked data-idx="${i}"></td>
            ${hasBroker ? `<td><small>${tx.broker || ''}</small></td>` : ''}
            <td>${tx.date || ''}${tx.is_duplicate ? dupBadge : ''}</td>
            <td><strong>${tx.symbol || ''}</strong><br><small class="text-muted">${tx.name || ''}</small></td>
            <td><span class="badge bg-${tx.tx_type === 'buy' ? 'success' : tx.tx_type === 'sell' ? 'danger' : 'secondary'}">${(tx.tx_type || '').toUpperCase()}</span></td>
            <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), {maximumFractionDigits: 4})}</td>
            <td class="text-end">${parseFloat(tx.price || 0).toFixed(4)}</td>
            <td>${tx.currency || ''}</td>
            <td class="text-end">${parseFloat(tx.fees || 0).toFixed(2)}</td>
        </tr>
    `).join('');
    return bookingsSummary + dupControl + `
        <p class="text-muted small mb-2">Found <strong>${transactions.length}</strong> transaction(s). Uncheck any to skip.
        ${hasBroker ? ' <span class="badge bg-info">Broker column detected — portfolios will be auto-assigned.</span>' : ''}</p>
        <div class="table-responsive">
            <table class="table table-sm table-hover">
                <thead><tr><th></th>${hasBroker ? '<th>Portfolio</th>' : ''}<th>Date</th><th>Asset</th><th>Type</th><th class="text-end">Qty</th><th class="text-end">Price</th><th>Currency</th><th class="text-end">Fees</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
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

    function showStep2(transactions, bookings, skippedCount) {
        step1.style.display = 'none';
        step2.style.display = '';
        parseBtn.style.display = 'none';
        saveBtn.style.display = (transactions.length > 0 || bookings.length > 0) ? '' : 'none';
        backBtn.style.display = '';
        let html = _buildPreviewTable(transactions, bookings);
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
            showStep2(parsedTransactions, parsedBookings, data.skipped_count || 0);
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
        if (selected.length === 0 && parsedBookings.length === 0) { alert('No transactions selected.'); return; }

        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';
        try {
            const result = await window.apiClient.saveImportedTransactions(selected, parsedBookings, null, _dupAction());
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Save Selected';
            bootstrap.Modal.getInstance(modal).hide();
            const bkMsg = result.saved_bookings > 0 ? ` + ${result.saved_bookings} booking(s)` : '';
            const msg = result.errors.length > 0
                ? `Saved ${result.saved}${bkMsg}. Errors:\n${result.errors.join('\n')}`
                : `Successfully imported ${result.saved} transaction(s)${bkMsg}.`;
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
                <td><strong>${tx.symbol || ''}</strong><br><small class="text-muted">${tx.asset_name || ''}</small></td>
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

// ---------------------------------------------------------------------------
// Page Manager
// ---------------------------------------------------------------------------
function createPageManager() {
    return {
        hideLoadingSpinners: function() {
            const loadingElements = document.querySelectorAll('.loading, .spinner-border, [data-loading]');
            loadingElements.forEach(el => el.style.display = 'none');
        },

        loadAssetsPage: async function() {
            try {
                const assets = await window.apiClient.getAssets();
                const tableBody = document.querySelector('#assetsPage tbody');

                if (tableBody) {
                    if (assets.length === 0) {
                        tableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No assets found. Click "Add Asset" to create your first asset.</td></tr>';
                    } else {
                        tableBody.innerHTML = assets.map(asset => `
                            <tr>
                                <td><strong>${asset.symbol || 'N/A'}</strong></td>
                                <td>${asset.name || 'N/A'}</td>
                                <td><span class="badge bg-primary">${asset.asset_type || 'N/A'}</span></td>
                                <td>${asset.exchange || 'N/A'}</td>
                                <td>
                                    ${fmtPrice(asset.current_price, asset.currency)}
                                    ${asset.auto_price === false ? '<span class="badge bg-secondary ms-1" title="Manual price — the daily cron will not overwrite it">manual</span>' : ''}
                                    <button class="btn btn-sm btn-link p-0 ms-1 align-baseline" title="Set a manual price" onclick="setAssetPrice(${asset.id}, '${(asset.symbol || '').replace(/'/g, "\\'")}', '${asset.currency || ''}')"><i class="bi bi-pencil-square"></i></button>
                                </td>
                                <td>${asset.currency || ''}</td>
                                <td>
                                    ${assetLinks(asset.symbol)}
                                </td>
                            </tr>
                        `).join('');
                    }
                }
                this.hideLoadingSpinners();
            } catch (error) {
                console.error('Error loading assets page:', error);
                this.hideLoadingSpinners();
            }
        },

        loadDashboardPage: async function() {
            const el = id => document.getElementById(id);

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

            if (el('totalAssets'))       el('totalAssets').textContent       = openPositions;
            if (el('totalTransactions')) el('totalTransactions').textContent = openPositions === 1 ? '1 position' : openPositions + ' positions';

            // --- Top 5 positions table ---
            const topBody = document.querySelector('#dashTopPositionsTable tbody');
            if (topBody) {
                const sorted = holdings
                    .filter(h => parseFloat(h.quantity || 0) > 0)
                    .sort((a, b) => parseFloat(b.total_value_eur || b.total_value || 0) - parseFloat(a.total_value_eur || a.total_value || 0))
                    .slice(0, 5);

                if (sorted.length === 0) {
                    topBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted ps-3 py-3">No positions yet. Add buy transactions to see them here.</td></tr>';
                } else {
                    const typeBadge = t => ({ stock: 'bg-primary', etf: 'bg-info', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[t] || 'bg-secondary');
                    topBody.innerHTML = sorted.map(h => {
                        const pnlPct  = parseFloat(h.pnl_pct || 0);
                        const valEur  = parseFloat(h.total_value_eur || h.total_value || 0);
                        const pnlClass = pnlPct >= 0 ? 'text-success' : 'text-danger';
                        const name    = h.name || h.symbol || '';
                        return `
                        <tr>
                            <td class="ps-3" style="max-width:220px;">
                                <div class="fw-semibold text-truncate" title="${name}">${name}</div>
                                <div class="small text-muted">${h.symbol || ''} ${assetLinks(h.symbol)}</div>
                            </td>
                            <td><span class="badge ${typeBadge(h.asset_type)}">${(h.asset_type || '').toUpperCase()}</span></td>
                            <td class="text-end">${valEur.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                            <td class="text-end pe-3 ${pnlClass} fw-semibold">${fmtPct(pnlPct)}</td>
                        </tr>`;
                    }).join('');
                }
            }

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

                    const R = 56;
                    const CX = 70;
                    const CY = 70;
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
                                    stroke="${colour}" stroke-width="22"
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
                        <svg width="140" height="140" viewBox="0 0 140 140" style="flex-shrink:0;">
                            ${svgSlices}
                            <text x="${CX}" y="${CY - 6}" text-anchor="middle" font-size="11" fill="#64748b">Total</text>
                            <text x="${CX}" y="${CY + 10}" text-anchor="middle" font-size="12" font-weight="bold" fill="#1e293b">${(grandTotal / 1000).toFixed(1)}k</text>
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
                                    <div class="small text-muted">${tx.symbol || ''}</div>
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
                const transactions = await window.apiClient.getTransactions(500, selectedPortfolioId);
                if (transactions.length === 0) {
                    tableBody.innerHTML = '<tr><td colspan="10" class="text-center text-muted">No transactions found.</td></tr>';
                } else {
                    tableBody.innerHTML = transactions.map(tx => `
                        <tr>
                            <td>${Fmt.date(tx.transaction_date)}</td>
                            <td><small>${tx.portfolio_name || ''}</small></td>
                            <td><strong>${tx.symbol || ''}</strong> ${assetLinks(tx.symbol)}<br><small class="text-muted">${tx.name || ''}</small></td>
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
                        </tr>
                    `).join('');
                }
            } catch (error) {
                console.error('Error loading transactions:', error);
                tableBody.innerHTML = '<tr><td colspan="10" class="text-center text-danger">Error loading transactions.</td></tr>';
            }

            this.hideLoadingSpinners();
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

                if (holdings.length === 0) {
                    tableBody.innerHTML = '<tr><td colspan="12" class="text-center text-muted">No holdings found. Add buy transactions to see your positions here.</td></tr>';
                } else {
                    tableBody.innerHTML = holdings.map(h => {
                        const pnlClass = h.pnl_amount >= 0 ? 'text-success' : 'text-danger';
                        const typeBadge = { stock: 'bg-primary', etf: 'bg-info', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[h.asset_type] || 'bg-secondary';
                        const symEsc = (h.symbol || '').replace(/'/g, "\\'");
                        return `
                        <tr>
                            <td><strong>${h.symbol}</strong></td>
                            <td>${h.name}</td>
                            <td><span class="badge ${typeBadge}">${(h.asset_type || '').toUpperCase()}</span></td>
                            <td>${h.currency || ''}</td>
                            <td class="text-end">${parseFloat(h.quantity).toLocaleString(Fmt.loc(), { maximumFractionDigits: 4 })}</td>
                            <td class="text-end">${fmt(h.avg_price)}</td>
                            <td class="text-end">${h.current_price > 0 ? fmt(h.current_price) : '<span class="text-muted">—</span>'}</td>
                            <td class="text-end fw-bold">${fmt(h.total_value)}</td>
                            <td class="text-end ${pnlClass}">${h.pnl_amount >= 0 ? '+' : ''}${fmt(h.pnl_amount)}</td>
                            <td class="text-end ${pnlClass}">${h.pnl_pct >= 0 ? '+' : ''}${fmt(h.pnl_pct)}%</td>
                            <td class="text-center text-nowrap">${assetLinks(h.symbol)}</td>
                            <td class="text-end pe-3">
                                <button class="btn btn-sm btn-outline-primary" title="Research / Valuation"
                                        onclick="openResearchModal('${symEsc}')">
                                    <i class="bi bi-graph-up"></i>
                                </button>
                            </td>
                        </tr>`;
                    }).join('');
                }
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
                            <td class="ps-3"><strong>${p.name}</strong>${site}</td>
                            <td>${p.base_currency || ''}</td>
                            <td class="text-end">${v ? eur(v.value_eur) : '<span class="text-muted">—</span>'}${v && Math.abs(v.cash_eur || 0) >= 1 ? `<div class="small text-muted" title="Cash balance (deposits − withdrawals + sells − buys + dividends)"><i class="bi bi-cash-coin me-1"></i>${eur(v.cash_eur)} cash</div>` : ''}</td>
                            ${pnlCell(v)}
                            <td>${activity}</td>
                            <td><small class="text-muted">${p.description || ''}</small></td>
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
            // Lazy-load each section independently (performance is slow ~3s)
            loadAnalyticsPerformance();
            loadAnalyticsNetworth();
            loadAnalyticsDividends();
            loadAnalyticsTax();
            loadAnalyticsDiversification();
            loadAnalyticsRisk();
            loadAnalyticsFees();
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

async function loadAnalyticsNetworth() {
    _wireBackfillButton();
    const container = document.getElementById('anNetworthContainer');
    const placeholder = document.getElementById('anNetworthPlaceholder');
    const svg = document.getElementById('anNetworthSvg');
    if (!container || !svg) return;
    svg.style.display = 'none';
    placeholder.style.display = 'flex';
    placeholder.innerHTML = '<div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…';
    try {
        const d = await window.apiClient.getNetworthHistory();
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
                    <td class="text-truncate small text-muted" style="max-width:180px;" title="${p.name}">${p.name !== p.sym ? p.name : ''}</td>
                    <td class="text-end">${anFmtEur2(p.total)}</td>
                    <td class="text-end">${p.yoc != null ? parseFloat(p.yoc).toFixed(2) + '%' : '—'}</td>
                </tr>`).join('')
            : '<tr><td colspan="4" class="text-center text-muted small">No dividend payers yet.</td></tr>';

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
            </div>`;
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
                    <td><strong>${c.symbol}</strong></td>
                    <td class="text-truncate small text-muted" style="max-width:220px;" title="${c.name || ''}">${c.name || ''}</td>
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
                    <td><strong>${r.symbol}</strong></td>
                    <td class="text-truncate small text-muted" style="max-width:220px;" title="${r.name || ''}">${r.name || ''}</td>
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
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">Your watchlist is empty. Add a symbol above to start tracking it.</td></tr>';
            return;
        }
        const typeBadge = t => ({ stock: 'bg-primary', etf: 'bg-info', crypto: 'bg-warning text-dark', bond: 'bg-secondary', commodity: 'bg-dark' }[t] || 'bg-secondary');
        tbody.innerHTML = items.map(w => {
            const price = w.current_price != null ? parseFloat(w.current_price) : null;
            const buyBelow = w.buy_below != null ? parseFloat(w.buy_below) : null;
            const dist = w.distance_to_buy_pct != null ? parseFloat(w.distance_to_buy_pct) : null;
            const inZone = !!w.in_buy_zone;
            const distCell = dist != null
                ? `<span class="${inZone ? 'text-success fw-semibold' : ''}">${(dist >= 0 ? '+' : '') + dist.toFixed(1)}%</span>`
                : '—';
            const symCell = inZone
                ? `<strong>${w.symbol}</strong> <span class="badge bg-success">BUY ZONE</span> ${assetLinks(w.symbol)}`
                : `<strong>${w.symbol}</strong> ${assetLinks(w.symbol)}`;
            return `
                <tr class="${inZone ? 'table-success' : ''}">
                    <td class="ps-3">${symCell}</td>
                    <td>${w.name || ''}</td>
                    <td><span class="badge ${typeBadge(w.asset_type)}">${(w.asset_type || '').toUpperCase() || '—'}</span></td>
                    <td class="text-end">${price != null ? price.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
                    <td class="text-end">${buyBelow != null ? buyBelow.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
                    <td class="text-end">${distCell}</td>
                    <td class="text-muted small">${w.notes || ''}</td>
                    <td class="pe-3">
                        <button class="btn btn-sm btn-outline-danger" onclick="window.deleteWatchlistRow('${(w.symbol || '').replace(/'/g, "\\'")}')" title="Remove from watchlist">
                            <i class="bi bi-trash"></i>
                        </button>
                    </td>
                </tr>`;
        }).join('');
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

function setupWatchlistPage() {
    const form = document.getElementById('addWatchlistForm');
    const refreshBtn = document.getElementById('refreshWatchlist');
    const status = document.getElementById('watchlistFormStatus');
    if (refreshBtn) refreshBtn.addEventListener('click', loadWatchlist);
    if (!form) return;
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
                                <h5 class="card-title mb-0"><i class="bi bi-bullseye me-2 text-primary"></i>${g.name || 'Goal'}</h5>
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
                                    <div class="fw-semibold">${g.target_date || '—'}</div>
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
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'holdingsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage'];
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

    function appendMessage(role, html) {
        removeEmpty();
        const isUser = role === 'user';
        const div = document.createElement('div');
        div.className = `d-flex ${isUser ? 'justify-content-end' : 'justify-content-start'}`;
        div.innerHTML = `
            <div class="px-3 py-2 rounded-3 ${isUser ? 'bg-primary text-white' : 'bg-light text-dark border'}"
                 style="max-width:80%;white-space:pre-wrap;word-break:break-word;">${html}</div>`;
        messagesEl.appendChild(div);
        scrollBottom();
    }

    function appendTransactionCard(transactions) {
        if (!transactions || transactions.length === 0) return;
        removeEmpty();

        const rows = transactions.map((tx, i) => `
            <tr>
                <td><input class="form-check-input chat-tx-select" type="checkbox" checked data-idx="${i}"></td>
                <td>${tx.date || ''}</td>
                <td><strong>${tx.symbol || ''}</strong> <small class="text-muted">${tx.asset_name || ''}</small></td>
                <td><span class="badge bg-${tx.tx_type === 'buy' ? 'success' : tx.tx_type === 'sell' ? 'danger' : 'info'}">${(tx.tx_type || '').toUpperCase()}</span></td>
                <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), {maximumFractionDigits:4})}</td>
                <td class="text-end">${parseFloat(tx.price || 0).toFixed(4)} ${tx.currency || ''}</td>
                <td class="text-end text-muted">${(parseFloat(tx.fees)||0) > 0 ? (parseFloat(tx.fees).toFixed(2) + ' ' + (tx.currency||'')) : '—'}</td>
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
                            <thead><tr><th></th><th>Date</th><th>Asset</th><th>Type</th><th class="text-end">Qty</th><th class="text-end">Price</th><th class="text-end">Fees</th></tr></thead>
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
            const selected = Array.from(card.querySelectorAll('.chat-tx-select:checked'))
                .map(cb => transactions[parseInt(cb.dataset.idx)]);
            if (selected.length === 0) { alert('Nothing selected.'); return; }

            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';
            const normalized = selected.map(tx => ({
                symbol: tx.symbol, name: tx.asset_name || tx.symbol,
                asset_type: 'stock', tx_type: tx.tx_type, date: tx.date,
                quantity: tx.quantity, price: tx.price,
                currency: tx.currency || 'EUR', fees: parseFloat(tx.fees) || 0.0,
                notes: tx.raw_text || ''
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
            fileStep1.style.display = 'none'; fileStep2.style.display = '';
            fileParseBtn.style.display = 'none'; fileBackBtn.style.display = '';
            fileSaveBtn.style.display = (parsedFile.length > 0 || parsedFileBookings.length > 0) ? '' : 'none';
            let html = _buildPreviewTable(parsedFile, parsedFileBookings);
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
        if (selected.length === 0 && parsedFileBookings.length === 0) { alert('No transactions selected.'); return; }
        fileSaveBtn.disabled = true;
        fileSaveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving…';
        try {
            const result = await window.apiClient.saveImportedTransactions(selected, parsedFileBookings, null, _dupAction());
            const bkMsg = result.saved_bookings > 0 ? ` + ${result.saved_bookings} booking(s)` : '';
            const owMsg = result.overwritten > 0 ? `, ${result.overwritten} overwritten` : '';
            const dupMsg = result.duplicates_skipped > 0 ? `, ${result.duplicates_skipped} duplicate(s) skipped` : '';
            const realErrors = result.errors.filter(e => !e.startsWith('DUPLICATE'));
            alert(realErrors.length > 0
                ? `Saved ${result.saved}${bkMsg}${owMsg}${dupMsg}. Errors:\n${realErrors.join('\n')}`
                : `Successfully imported ${result.saved} transaction(s)${bkMsg}${owMsg}${dupMsg}.`);
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
                    <td><input class="form-check-input io-tx-select" type="checkbox" checked data-idx="${i}"></td>
                    <td>${tx.date || ''}${tx.is_duplicate ? dupBadge : ''}</td>
                    <td><strong>${tx.symbol || ''}</strong> <small class="text-muted">${tx.asset_name || ''}</small></td>
                    <td><span class="badge bg-${tx.tx_type === 'buy' ? 'success' : tx.tx_type === 'sell' ? 'danger' : 'info'}">${(tx.tx_type || '').toUpperCase()}</span></td>
                    <td class="text-end">${parseFloat(tx.quantity || 0).toLocaleString(Fmt.loc(), {maximumFractionDigits:4})}</td>
                    <td class="text-end">${parseFloat(tx.price || 0).toFixed(4)} ${tx.currency || ''}</td>
                    <td class="text-end text-muted">${(parseFloat(tx.fees)||0) > 0 ? (parseFloat(tx.fees).toFixed(2) + ' ' + (tx.currency||'')) : '—'}</td>
                </tr>`).join('');
            textPreview.innerHTML = extractedText.length === 0
                ? (bookingsSummary + dupControl || '<div class="alert alert-warning">No transactions or cash movements could be extracted.</div>')
                : bookingsSummary + dupControl
                   + `<p class="text-muted small mb-2">Found <strong>${extractedText.length}</strong> transaction(s). Uncheck any to skip.</p>
                   <div class="table-responsive"><table class="table table-sm table-hover">
                   <thead><tr><th></th><th>Date</th><th>Asset</th><th>Type</th><th class="text-end">Qty</th><th class="text-end">Price</th><th class="text-end">Fees</th></tr></thead>
                   <tbody>${rows}</tbody></table></div>`;
        } catch (err) {
            alert('Error extracting: ' + err.message);
        } finally {
            extractBtn.disabled = false;
            extractBtn.innerHTML = '<i class="bi bi-magic me-1"></i>Extract';
        }
    });

    textSaveBtn.addEventListener('click', async () => {
        const checked = Array.from(document.querySelectorAll('#ioTextPreview .io-tx-select:checked'))
            .map(cb => extractedText[parseInt(cb.dataset.idx)]);
        if (checked.length === 0 && extractedTextBookings.length === 0) {
            alert('Nothing selected to save.'); return;
        }
        const normalized = checked.map(tx => ({
            symbol: tx.symbol, name: tx.asset_name || tx.symbol,
            asset_type: 'stock', tx_type: tx.tx_type, date: tx.date,
            quantity: tx.quantity, price: tx.price,
            currency: tx.currency || 'EUR', fees: parseFloat(tx.fees) || 0.0, notes: tx.raw_text || ''
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
                    <td class="text-muted small">${b.portfolio_name || ''}</td>
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

function setupForecastPage() {
    // DOM refs - asset allocation inputs
    const cashAmountInput   = document.getElementById('fcCashAmount');
    const cashRateInput     = document.getElementById('fcCashRate');
    const stocksAmountInput = document.getElementById('fcStocksAmount');
    const stocksRateInput   = document.getElementById('fcStocksRate');
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
                           mortgagePrincipal, mortgageRate, monthlyPayment, years, sigma) {
        const VOLATILITY = { cash: 0.01, bonds: 0.06, stocks: 0.16 };

        const cashProj   = projectAccount(cashAmt,   cashRate,   VOLATILITY.cash,   years, sigma);
        const stocksProj = projectAccount(stocksAmt, stocksRate, VOLATILITY.stocks, years, sigma);
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

        const proj = runProjection(
            cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate,
            mortPrincipal, mortRate, mortPayment, years, sigma
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
    const labels = { stock: 'Stocks', etf: 'ETFs', crypto: 'Crypto', bond: 'Bonds', commodity: 'Commodities', p2p: 'P2P' };
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

    function renderFundamentals(f) {
        const keys = Object.keys(f || {}).filter(k => k !== 'symbol');
        $('rsFundamentals').innerHTML = keys.length
            ? keys.map(k => `<div class="d-flex justify-content-between"><span class="text-muted">${k}</span><span>${typeof f[k] === 'number' ? Fmt.num(f[k], 0, 4) : f[k]}</span></div>`).join('')
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

    async function load(sym) {
        sym = (sym || '').trim().toUpperCase();
        if (!sym) return;
        $('researchHint').textContent = 'Loading ' + sym + '…';
        try {
            const d = await window.apiClient.researchLookup(sym);
            R = { symbol: sym, currency: d.currency, price: d.current_price, fundamentals: d.fundamentals || {}, llm: null };
            $('researchBody').style.display = '';
            $('rsName').textContent = `${sym} — ${d.name || ''}`;
            $('rsHeld').textContent = d.held ? 'held' : 'not held';
            $('rsHeld').className = 'badge ms-1 ' + (d.held ? 'bg-success' : 'bg-secondary');
            $('rsPrice').textContent = money(d.current_price, d.currency);
            $('rsAvgCost').textContent = d.avg_cost ? money(d.avg_cost, d.currency) : '—';
            $('rsQty').textContent = d.quantity || '—';
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
        } catch (e) {
            $('researchHint').innerHTML = '<span class="text-danger">' + (e.message || 'lookup failed') + '</span>';
        }
    }

    $('researchLoadBtn').addEventListener('click', () => load($('researchTicker').value));
    $('researchTicker').addEventListener('keydown', e => { if (e.key === 'Enter') load($('researchTicker').value); });

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
            });
            $('rvSaveMsg').innerHTML = `<span class="text-success">Saved.${res.targets_updated ? ' Targets updated (alerts active).' : ' (Not held — saved as research only.)'}</span>`;
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
                    <td class="ps-3"><strong>${r.symbol}</strong></td>
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

    // Expose loader (populates the ticker datalist on each page open).
    window.loadResearchPage = async function () {
        try {
            const [holdings, watch] = await Promise.all([
                window.apiClient.getHoldings().catch(() => ({ holdings: [] })),
                window.apiClient.getWatchlist().catch(() => []),
            ]);
            const syms = new Set();
            (holdings.holdings || []).forEach(h => syms.add(h.symbol));
            (watch || []).forEach(w => syms.add(w.symbol));
            $('researchTickerList').innerHTML = [...syms].map(s => `<option value="${s}">`).join('');
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
        $('setRowsPerPage').value = PREFS.rowsPerPage;
    }
    function openModal(e) { if (e) e.preventDefault(); load(); bs.show(); }

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
        PREFS.rowsPerPage = Math.max(10, Math.min(500, parseInt($('setRowsPerPage').value) || 50));
        savePrefs();
        applyTheme();
        applyPrivacy();
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
        anTaxYear.addEventListener('change', () => loadAnalyticsTax());
    }
    // Re-render the net worth chart on resize when the analytics page is visible
    window.addEventListener('resize', () => {
        if (window.navigationManager && window.navigationManager.currentPage === 'analytics') {
            loadAnalyticsNetworth();
        }
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
