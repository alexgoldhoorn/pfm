// Tests for the web client's pure utilities + a load/smoke test for the
// 4-file split of the former portfolio_debug.js.
//
// No npm dependencies: uses Node's built-in test runner (`node --test`) and the
// `vm` module. The four classic scripts share one global scope in the browser,
// so we load them concatenated into a single vm context with minimal DOM stubs
// — which also verifies the split loads cleanly together (a load-time cross-file
// reference would throw here).
//
// Run:  node --test web_client/js/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
// Must match the load order in index.html.
const FILES = ["pfm_core.js", "pfm_pages.js", "pfm_analytics.js", "pfm_features.js"];

// Build a sandbox with just enough browser surface for the files' top-level
// code (window.PREFS from localStorage, applyTheme() reading documentElement,
// the DOMContentLoaded registration in pfm_features). Runtime-only globals
// (bootstrap, Chart, marked, fetch) aren't touched at load.
function loadAppIntoContext() {
    const noop = () => {};
    const elementStub = {
        setAttribute: noop,
        getAttribute: () => null,
        addEventListener: noop,
        classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        style: {},
        appendChild: noop,
    };
    const store = {};
    const sandbox = {
        console,
        setTimeout,
        clearTimeout,
        localStorage: {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => {
                store[k] = String(v);
            },
            removeItem: (k) => {
                delete store[k];
            },
        },
        navigator: { language: "en-US" },
        document: {
            documentElement: elementStub,
            body: elementStub,
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            createElement: () => ({ ...elementStub }),
            addEventListener: noop,
        },
        matchMedia: () => ({ matches: false, addEventListener: noop }),
        bootstrap: {},
        fetch: () => Promise.resolve({ ok: true, status: 200, json: async () => ({}) }),
    };
    // window === the global object, mirroring a classic browser script.
    sandbox.window = sandbox;
    vm.createContext(sandbox);

    const source = FILES.map((f) => readFileSync(join(JS_DIR, f), "utf8")).join("\n;\n");
    vm.runInContext(source, sandbox, { filename: "pfm_app_concat.js" });
    return sandbox;
}

test("split loads in one scope and defines functions from every file", () => {
    const w = loadAppIntoContext();
    // One representative top-level function from each of the four files —
    // proves all four executed without a load-time error.
    assert.equal(typeof w.esc, "function", "pfm_core: esc");
    assert.equal(typeof w.createAPIClient, "function", "pfm_core: createAPIClient");
    assert.equal(typeof w.createPageManager, "function", "pfm_pages: createPageManager");
    assert.equal(typeof w.showAnalyticsTab, "function", "pfm_analytics: showAnalyticsTab");
    assert.equal(typeof w.setupResearchPage, "function", "pfm_features: setupResearchPage");
    assert.equal(typeof w.setupSettings, "function", "pfm_features: setupSettings");
    // Shared singletons exported onto window.
    assert.equal(typeof w.Fmt, "object", "window.Fmt present");
    assert.ok(w.PREFS && w.PREFS.defaultCurrency === "EUR", "PREFS seeded from defaults");
});

test("esc() escapes all HTML metacharacters (XSS guard)", () => {
    const { esc } = loadAppIntoContext();
    assert.equal(
        esc(`<img src=x onerror="alert('x')">`),
        "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;"
    );
    assert.equal(esc("a & b"), "a &amp; b");
    // A name that would break out of an attribute is neutralised.
    assert.equal(esc('"><script>'), "&quot;&gt;&lt;script&gt;");
});

test("esc() handles null/undefined/numbers", () => {
    const { esc } = loadAppIntoContext();
    assert.equal(esc(null), "");
    assert.equal(esc(undefined), "");
    assert.equal(esc(42), "42");
    assert.equal(esc(""), "");
});

test("Fmt.num formats with default decimals", () => {
    const w = loadAppIntoContext();
    // en-US locale set in the sandbox; default 2 decimals.
    assert.equal(w.Fmt.num(1234.5), "1,234.50");
    assert.equal(w.Fmt.num(0), "0.00");
    assert.equal(w.Fmt.num(null), "0.00");
});

test("Fmt.date respects the date-format preference", () => {
    const w = loadAppIntoContext();
    assert.equal(w.Fmt.date("2026-05-28"), "2026-05-28"); // iso (default)
    w.PREFS.dateFormat = "dmy";
    assert.equal(w.Fmt.date("2026-05-28"), "28-05-2026");
    w.PREFS.dateFormat = "mdy";
    assert.equal(w.Fmt.date("2026-05-28T14:30:00"), "05-28-2026 14:30");
    assert.equal(w.Fmt.date(""), "");
});

const SAMPLE_HOLDINGS = [
    { symbol: "A", asset_type: "stock", quantity: 1, total_value_eur: 100, pnl_pct: 5, pnl_amount: 10 },
    { symbol: "B", asset_type: "crypto", quantity: 2, total_value_eur: 300, pnl_pct: -8, pnl_amount: -40 },
    { symbol: "C", asset_type: "stock", quantity: 3, total_value_eur: 200, pnl_pct: 20, pnl_amount: 15 },
    { symbol: "D", asset_type: "etf", quantity: 0, total_value_eur: 999, pnl_pct: 99, pnl_amount: 99 },
];

test("topPositions: drops zero-qty, sorts by value desc, slices N", () => {
    const { topPositions } = loadAppIntoContext();
    const r = topPositions(SAMPLE_HOLDINGS, { n: 2, type: "all", sort: "value" });
    assert.deepEqual(r.map((h) => h.symbol), ["B", "C"]); // D dropped (qty 0)
});

test("topPositions: each sort mode orders correctly", () => {
    const { topPositions } = loadAppIntoContext();
    const syms = (sort) =>
        topPositions(SAMPLE_HOLDINGS, { n: "all", type: "all", sort }).map((h) => h.symbol);
    assert.deepEqual(syms("value"), ["B", "C", "A"]);        // 300,200,100
    assert.deepEqual(syms("gain_pct"), ["C", "A", "B"]);     // 20,5,-8
    assert.deepEqual(syms("loss_pct"), ["B", "A", "C"]);     // -8,5,20
    assert.deepEqual(syms("gain_total"), ["C", "A", "B"]);   // 15,10,-40
    assert.deepEqual(syms("loss_total"), ["B", "A", "C"]);   // -40,10,15
});

test("topPositions: type filter + N='all'", () => {
    const { topPositions } = loadAppIntoContext();
    const r = topPositions(SAMPLE_HOLDINGS, { n: "all", type: "stock", sort: "value" });
    assert.deepEqual(r.map((h) => h.symbol), ["C", "A"]);
});

test("topPositions: loss sort still returns rows when nothing is negative", () => {
    const { topPositions } = loadAppIntoContext();
    const winners = [
        { symbol: "X", quantity: 1, total_value_eur: 1, pnl_pct: 5, pnl_amount: 5 },
        { symbol: "Y", quantity: 1, total_value_eur: 1, pnl_pct: 2, pnl_amount: 2 },
    ];
    const r = topPositions(winners, { n: 5, type: "all", sort: "loss_pct" });
    assert.deepEqual(r.map((h) => h.symbol), ["Y", "X"]); // least-positive first
});

const TS_COLUMNS = [
    { key: "symbol", type: "text" },
    { key: "value", type: "num" },
    { key: "date", type: "date" },
    { key: "asset_type", type: "text", filter: "select" },
];
const TS_ROWS = [
    { symbol: "bbb", value: 10, date: "2025-01-02", asset_type: "stock" },
    { symbol: "AAA", value: 30, date: "2025-03-01", asset_type: "crypto" },
    { symbol: "ccc", value: 20, date: "2025-02-01", asset_type: "stock" },
    { symbol: "ddd", value: null, date: "", asset_type: "stock" },
];

test("applyTableState: numeric sort desc, blanks last", () => {
    const { applyTableState } = loadAppIntoContext();
    const r = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "value", dir: "desc" }, filters: {} });
    assert.deepEqual(r.map((x) => x.symbol), ["AAA", "ccc", "bbb", "ddd"]);
});

test("applyTableState: text sort asc is case-insensitive", () => {
    const { applyTableState } = loadAppIntoContext();
    const r = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "symbol", dir: "asc" }, filters: {} });
    assert.deepEqual(r.map((x) => x.symbol), ["AAA", "bbb", "ccc", "ddd"]);
});

test("applyTableState: date sort asc", () => {
    const { applyTableState } = loadAppIntoContext();
    const r = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "date", dir: "asc" }, filters: {} });
    assert.deepEqual(r.map((x) => x.symbol), ["bbb", "ccc", "AAA", "ddd"]);
});

test("applyTableState: select filter keeps matches; 'all' passes through", () => {
    const { applyTableState } = loadAppIntoContext();
    const f = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "symbol", dir: "asc" }, filters: { asset_type: "stock" } });
    assert.deepEqual(f.map((x) => x.symbol), ["bbb", "ccc", "ddd"]);
    const all = applyTableState(TS_ROWS, TS_COLUMNS, { sort: null, filters: { asset_type: "all" } });
    assert.equal(all.length, 4);
});

test("applyTableState: does not mutate input", () => {
    const { applyTableState } = loadAppIntoContext();
    const before = TS_ROWS.map((x) => x.symbol);
    applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "value", dir: "asc" }, filters: {} });
    assert.deepEqual(TS_ROWS.map((x) => x.symbol), before);
});
