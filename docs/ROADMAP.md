# Portfolio Manager — Feature Roadmap

Phased plan for the requested enhancements. Each phase is independently
shippable, follows the project motto (clean code, reuse, small files +
helpers, docstrings, unit tests, black/flake8, CLAUDE.md updates), and ends
with: tests green → commit → rebuild web → push.

Status legend: ☐ todo · ◐ in progress · ☑ done

---

## ✅ Already fixed (this session)
- ☑ **GBX cost-basis bug** — UK-listed stocks (GB-prefixed ISINs) quoted by Yahoo in
  pence were imported as GBP, making transaction prices/amounts 100× too high and
  badly inflating cost basis. Corrected by dividing GBX amounts by 100 on import.
- ☑ **Blank page after login** — `showDashboard()` targeted dead `#mainNav`/
  `#mainContent` IDs; now toggles `#appShell`.
- ☑ **Missing sidebar CSS** — restored `.app-sidebar`/`.sidebar-nav`/`.mobile-topbar`
  styles dropped by a parallel CSS rewrite; desktop sidebar + mobile offcanvas
  now render correctly.

---

## Phase 1 — Quick UX wins ✅ DONE

### 1a. Names instead of symbols on Dashboard
- Dashboard "Top Positions" and "Recent Transactions" show the ISIN/symbol.
- Show `asset.name` as primary, symbol as secondary muted text.
- Files: `web_client/js/portfolio_debug.js` (loadDashboardPage renderers).
- Reuse: holdings already return `name`; transactions return `name`.

### 1b. Yahoo Finance + Wall Street links
- Anywhere a symbol is shown (holdings, transactions, watchlist, research modal):
  add small external-link icons →
  - Yahoo: `https://finance.yahoo.com/quote/{symbol}`
  - WSJ: `https://www.wsj.com/market-data/quotes/{symbol}`
- Helper: add `web_client/js/portfolio_debug.js` → `assetLinks(symbol)` returning
  the two `<a target="_blank" rel="noopener">` icons. Reuse everywhere.
- Note: ISIN symbols need mapping for Yahoo (e.g. append exchange suffix). Use the
  asset's stored ticker where available; fall back to ISIN.

### 1c. Return timeframe selector (YTD / 1M / 1Y / Overall)
- **Backend**: extend `GET /api/v1/analytics/performance` with `?period=ytd|1m|1y|all`.
  - Filter cash flows + use a start-of-period snapshot value as the opening basis.
  - TWR per period needs period-boundary values → use `portfolio_snapshots`
    (already collected daily). For periods before snapshots exist, return `null`
    with a note.
- **Frontend**: a period toggle on the Analytics performance card and the Dashboard
  KPI ("Total Return" shows the chosen period).
- Files: `routers/analytics.py`, `services/analytics_service.py` (add
  `period_return(snapshots, cashflows, period)`), web Analytics + Dashboard.
- Tests: `tests/unit/test_analytics.py` — period filtering, null when no data.

---

## Phase 2 — Help & explainability ✅ DONE

### 2a. Tooltips everywhere
- Bootstrap tooltips (already bundled) on: KPI card headers, table column headers,
  icon buttons, and the metric labels (IRR, TWR, HHI, Sharpe, fair value, yield-on-cost).
- Helper: a small `data-bs-toggle="tooltip" title="…"` convention + one
  `initTooltips()` call after each page render. Centralise the explanation text in
  a `web_client/js/help_text.js` map so wording is consistent and reusable.

### 2b. Per-page "What is this?" help
- Each page header gets an `(i)` info button opening a help panel/modal explaining:
  what's shown, where the data comes from (yfinance, FIFO, snapshots, LLM), and how
  metrics are computed.
- Single `helpContent` map keyed by page id → reused by the info button.
- Keep copy short; link to ROADMAP/methodology where deeper.

---

## Phase 3 — Authentication ✅ DONE

- The DB already has a `users` table (v3) + `portf_manager/auth.py` (password hashing).
  Currently the web client uses an API key only.
- **Plan**:
  - Add `POST /api/v1/auth/login` (username+password → returns/sets the API key or a
    session token). `auth.py` already hashes; wire it to the router.
  - Web: add a username/password login form alongside the API-key field; on success
    store the returned key.
  - Keep API-key auth for the MCP server + cron scripts (machine clients).
- Files: `routers/auth.py`, `web_client` login form, `auth.py` (reuse).
- Tests: login success/failure, password change.
- **Security note**: do this BEFORE exposing externally (Phase 4).

---

## Phase 4 — External access ◐ code done; see docs/EXTERNAL_ACCESS.md for host steps

- Mirror the existing pattern (`app1.example.com`, `app2.example.com` via
  nginx-proxy-manager on this host).
- **Plan**:
  1. nginx-proxy-manager: add a proxy host `portfolio.example.com` → `portf_web:80`
     (container is on the same docker network) or `127.0.0.1:8080`.
  2. Let's Encrypt cert via NPM (same as other hosts).
  3. IONOS DNS: add `pfm` A/CNAME record pointing to the home IP / DDNS.
  4. The web client calls the API at `<your-host-ip>:8000` (hardcoded `baseURL`) — for
     external access this must become same-origin or a proxied `/api`. **Plan**:
     proxy `portfolio.example.com/api/` → `portf_backend_dev:8000` and change the web
     client `baseURL` to relative `/api` (or detect host). This also fixes the
     "insecure password field over HTTP" warning (HTTPS via NPM).
- **Prerequisite**: Phase 3 (auth) must be live — do not expose an unauthenticated
  portfolio to the internet. API-key-only is acceptable if the key is strong and
  the login form is the gate.
- Files: NPM config (host-level, outside repo), `web_client` baseURL handling,
  CORS settings in `portf_server/settings.py`.

---

## Phase 5 — Public (shareable) view ✅ DONE

- Like PDT's public page: show **allocation %** (by asset type / sector / top
  holdings) and performance %, but **never absolute amounts**.
- **Plan**:
  - `GET /api/v1/public/summary` — no auth, returns ONLY percentages and returns
    (no € values, no quantities). Derive from holdings but strip amounts.
  - A separate minimal `public.html` (or a `?public` mode) that renders the donut +
    return % + top-holdings-by-weight. No login required.
  - Gate behind a config flag + a random share slug so it's opt-in.
- Files: new `routers/public.py`, `web_client/public.html`, settings flag.
- Tests: assert the public payload contains no absolute amounts/quantities.

---

## Phase 6 — Tax tooling ✅ DONE (per-lot report + withholding + configurable brackets)

- We have `tax_calculator.py` (FIFO realised gains) + `/analytics/tax-estimate`
  (Spanish IRPF savings-base estimate, harvest candidates).
- **Plan to extend**:
  - Full per-lot realised-gains report for a tax year (downloadable), reusing the
    existing FIFO engine.
  - Dividend withholding tax tracking (we store `tax` per transaction) → summary
    of foreign withholding for double-taxation relief (Modelo 720 / form data).
  - Configurable jurisdiction brackets (currently hardcoded Spanish) → move to a
    small `tax_rates.py` so other regimes can be added.
  - A clear disclaimer ("estimate, not advice") — already present, keep prominent.
- Files: `tax_calculator.py`, new `services/tax_rates.py`, `routers/tax.py` +
  `/analytics/tax-estimate`, web Tax section under Analytics.
- Tests: bracket math, FIFO lot matching edge cases, withholding aggregation.

---

## Cross-cutting engineering notes
- **Import GBX normalization ✅ DONE**: `portf_manager/currency_utils.py`
  (`is_gbx` + `normalize_gbx_amounts`, cached per symbol) divides GBX prices/
  amounts ÷100 on import. Wired into `imports.py` save and `sync.py` pull;
  unit-tested in `tests/unit/test_currency_utils.py`.
- Keep files small: when `analytics.py` or `portfolio_debug.js` grows further,
  split helpers into `services/` modules / dedicated JS files.
- Every phase: black + flake8 clean (pre-commit), unit tests, update CLAUDE.md.
