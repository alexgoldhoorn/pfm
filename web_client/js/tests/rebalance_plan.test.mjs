// Pure helpers behind the Rebalance Trade Plan form + results area
// (Assets page, Rebalancing card).
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("rebalanceStrategyLabel maps all 3 known strategies", () => {
    assert.equal(win.rebalanceStrategyLabel("tax_minimal"), "Tax-minimal");
    assert.equal(win.rebalanceStrategyLabel("closest_to_target"), "Closest to target");
    assert.equal(win.rebalanceStrategyLabel("balanced"), "Balanced");
});

test("rebalanceStrategyLabel falls back sensibly on an unexpected value, never throws", () => {
    assert.equal(win.rebalanceStrategyLabel("some_weird_strategy"), "Some_weird_strategy");
    assert.equal(win.rebalanceStrategyLabel(""), "Unknown");
    assert.equal(win.rebalanceStrategyLabel(null), "Unknown");
    assert.equal(win.rebalanceStrategyLabel(undefined), "Unknown");
});

test("rebalanceDriftClass thresholds at the boundaries", () => {
    // Below 2% — success (green)
    assert.equal(win.rebalanceDriftClass(0), "text-success");
    assert.equal(win.rebalanceDriftClass(1.99), "text-success");
    // Exactly 2% — warning (amber) starts here
    assert.equal(win.rebalanceDriftClass(2), "text-warning");
    assert.equal(win.rebalanceDriftClass(4.99), "text-warning");
    // Exactly 5% — danger (red) starts here
    assert.equal(win.rebalanceDriftClass(5), "text-danger");
    assert.equal(win.rebalanceDriftClass(10), "text-danger");
    // Sign shouldn't matter — it's an absolute-drift threshold
    assert.equal(win.rebalanceDriftClass(-2), "text-warning");
    assert.equal(win.rebalanceDriftClass(-5), "text-danger");
});

test("rebalanceFmtEur renders '—' for null/undefined, not 'null'/'NaN'/empty", () => {
    assert.equal(win.rebalanceFmtEur(null), "—");
    assert.equal(win.rebalanceFmtEur(undefined), "—");
    // A real zero is not the same as a missing value.
    assert.notEqual(win.rebalanceFmtEur(0), "—");
    assert.match(win.rebalanceFmtEur(0), /0/);
});

test("rebalanceFmtEur formats a real number with the euro sign", () => {
    const out = win.rebalanceFmtEur(1234.5);
    assert.match(out, /1,234\.50/);
    assert.match(out, /€/);
});

test("rebalanceParseSymbolList trims, uppercases, drops empties, splits on comma or newline", () => {
    assert.deepEqual([...win.rebalanceParseSymbolList("aapl, msft\nbtc-eur")], ["AAPL", "MSFT", "BTC-EUR"]);
    assert.deepEqual([...win.rebalanceParseSymbolList("  , ,\n ")], []);
    assert.deepEqual([...win.rebalanceParseSymbolList("")], []);
    assert.deepEqual([...win.rebalanceParseSymbolList(null)], []);
});

test("buildRebalancePlanRequest: empty cash budget becomes null, not 0", () => {
    const req = win.buildRebalancePlanRequest({
        strategy: "balanced", cashBudget: "", maxTrades: "", minTradeEur: "",
        allowSells: true, excludedSymbols: "", lockedSymbols: "",
    });
    assert.equal(req.cash_budget_eur, null);
    assert.equal(req.max_trades, 12);
    assert.equal(req.min_trade_eur, 100);
    assert.equal(req.max_sell_gain_eur, null);
    assert.deepEqual([...req.excluded_symbols], []);
    assert.deepEqual([...req.locked_symbols], []);
    assert.equal(req.allow_sells, true);
    assert.equal(req.strategy, "balanced");
});

test("buildRebalancePlanRequest: an explicit 0 cash budget is preserved (not treated as blank)", () => {
    const req = win.buildRebalancePlanRequest({ strategy: "balanced", cashBudget: "0" });
    assert.equal(req.cash_budget_eur, 0);
});

test("buildRebalancePlanRequest: an empty max sell gain becomes null, not 0 (no cap, not a 0 cap)", () => {
    const req = win.buildRebalancePlanRequest({ strategy: "balanced", maxSellGainEur: "" });
    assert.equal(req.max_sell_gain_eur, null);
});

test("buildRebalancePlanRequest: an explicit 0 max sell gain is preserved (not treated as blank)", () => {
    const req = win.buildRebalancePlanRequest({ strategy: "balanced", maxSellGainEur: "0" });
    assert.equal(req.max_sell_gain_eur, 0);
});

test("buildRebalancePlanRequest: parses numeric fields and symbol lists", () => {
    const req = win.buildRebalancePlanRequest({
        strategy: "tax_minimal", cashBudget: "500", maxTrades: "20", minTradeEur: "50",
        maxSellGainEur: "1000", allowSells: false, excludedSymbols: "aapl,msft", lockedSymbols: "btc-eur",
    });
    assert.equal(req.strategy, "tax_minimal");
    assert.equal(req.cash_budget_eur, 500);
    assert.equal(req.max_trades, 20);
    assert.equal(req.min_trade_eur, 50);
    assert.equal(req.max_sell_gain_eur, 1000);
    assert.equal(req.allow_sells, false);
    assert.deepEqual([...req.excluded_symbols], ["AAPL", "MSFT"]);
    assert.deepEqual([...req.locked_symbols], ["BTC-EUR"]);
});

test("buildRebalancePlanRequest: an unknown strategy falls back to balanced", () => {
    const req = win.buildRebalancePlanRequest({ strategy: "not_a_real_strategy" });
    assert.equal(req.strategy, "balanced");
});

test("rebalanceWarningsHtml escapes a malicious warning string (XSS guard)", () => {
    const html = win.rebalanceWarningsHtml(['<script>alert(1)</script>']);
    assert.ok(!html.includes("<script>alert(1)</script>"), "raw <script> tag must not reach innerHTML");
    assert.ok(html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"), "escaped form must be present");
});

test("rebalanceWarningsHtml renders nothing for an empty/missing warnings list", () => {
    assert.equal(win.rebalanceWarningsHtml([]), "");
    assert.equal(win.rebalanceWarningsHtml(null), "");
    assert.equal(win.rebalanceWarningsHtml(undefined), "");
});

test("rebalanceTradeRowHtml escapes a malicious symbol/reason (XSS guard)", () => {
    const row = win.rebalanceTradeRowHtml({
        symbol: '<img src=x onerror=alert(1)>',
        asset_id: 1,
        asset_type: 'stock',
        side: 'SELL',
        quantity: 4,
        price_eur: 95,
        amount_eur: 380,
        estimated_gain_eur: 42,
        estimated_tax_eur: 7.98,
        reason: '"><script>alert(2)</script>',
    });
    assert.ok(!row.includes('<img src=x onerror=alert(1)>'));
    assert.ok(!row.includes('<script>alert(2)</script>'));
    assert.ok(row.includes('&lt;img src=x onerror=alert(1)&gt;'));
    assert.ok(row.includes('&lt;script&gt;alert(2)&lt;/script&gt;'));
});

test("rebalanceTradeRowHtml escapes an attacker-controlled asset_type", () => {
    // asset_type is server-echoed and, per CLAUDE.md, can carry an unbounded
    // string reflected from request input — must never reach innerHTML raw.
    const row = win.rebalanceTradeRowHtml({
        symbol: 'ABC', asset_type: '<b>evil</b>', side: 'BUY',
        quantity: 1, price_eur: 1, amount_eur: 1, reason: 'ok',
    });
    assert.ok(!row.includes('<b>evil</b>'));
    assert.ok(!row.includes('<B>EVIL</B>'));
    // Badge text is uppercased before escaping, the friendly label is not —
    // both escaped forms must be present, neither raw tag ever should be.
    assert.ok(row.includes('&lt;B&gt;EVIL&lt;/B&gt;'));
    assert.ok(row.includes('&lt;b&gt;evil&lt;/b&gt;'));
});

test("rebalanceTradeRowHtml: BUY trade's null gain/tax renders as '—', not 'null'/'NaN'", () => {
    const row = win.rebalanceTradeRowHtml({
        symbol: 'ABC', asset_type: 'stock', side: 'BUY', quantity: 2,
        price_eur: 10, amount_eur: 20, estimated_gain_eur: null, estimated_tax_eur: null,
        reason: 'underweight',
    });
    assert.ok(!row.includes('null'));
    assert.ok(!row.includes('NaN'));
    // Two dashes: one for gain, one for tax.
    const dashCount = (row.match(/—/g) || []).length;
    assert.equal(dashCount, 2);
});

test("rebalanceTradeRowHtml: SELL trade with real gain/tax does not render a dash for those cells", () => {
    const row = win.rebalanceTradeRowHtml({
        symbol: 'ABC', asset_type: 'stock', side: 'SELL', quantity: 4,
        price_eur: 95, amount_eur: 380, estimated_gain_eur: 42, estimated_tax_eur: 7.98,
        reason: 'overweight',
    });
    assert.ok(row.includes('SELL'));
    assert.ok(row.includes('text-danger'));
    assert.match(row, /42\.00/);
    assert.match(row, /7\.98/);
});

test("rebalanceTradesTableHtml renders an empty state for no trades", () => {
    const html = win.rebalanceTradesTableHtml([]);
    assert.ok(html.includes('No trades proposed'));
    assert.ok(!html.includes('<table'));
});

test("rebalanceSummaryChipsHtml formats drift/trades/gain/tax and color-codes drift", () => {
    const html = win.rebalanceSummaryChipsHtml({
        trade_count: 3,
        max_abs_drift_pct_after: 1.5,
        estimated_realized_gain_eur: 100.5,
        estimated_tax_delta_eur: 20.1,
    });
    assert.ok(html.includes('text-success'));
    assert.ok(html.includes('Trades: <strong>3</strong>'));
    assert.match(html, /100\.50/);
    assert.match(html, /20\.10/);
});

test("rebalanceSummaryChipsHtml colors high drift red", () => {
    const html = win.rebalanceSummaryChipsHtml({ max_abs_drift_pct_after: 7.2, trade_count: 1 });
    assert.ok(html.includes('text-danger'));
});

test("rebalancePlanTabHtml combines chips + warnings + trades for one plan", () => {
    const html = win.rebalancePlanTabHtml({
        summary: { trade_count: 1, max_abs_drift_pct_after: 1, estimated_realized_gain_eur: 0, estimated_tax_delta_eur: 0 },
        trades: [{ symbol: 'X', asset_type: 'stock', side: 'BUY', quantity: 1, price_eur: 1, amount_eur: 1, reason: 'r' }],
        warnings: ['<em>careful</em>'],
    });
    assert.ok(html.includes('Drift after'));
    assert.ok(html.includes('&lt;em&gt;careful&lt;/em&gt;'));
    assert.ok(!html.includes('<em>careful</em>'));
    assert.ok(html.includes('<table'));
});

test("rebalancePlanTabHtml de-dupes a warning already shown at the top level (each string renders once)", () => {
    const shared = 'Missing price for MINTOS — treated as 0.';
    const ownOnly = 'This strategy skipped a locked symbol.';
    const html = win.rebalancePlanTabHtml(
        { summary: {}, trades: [], warnings: [shared, ownOnly] },
        [shared],
    );
    // The shared warning must not appear in this pane's own rendering at all
    // (it's already shown once, above the tabs) — the plan-specific one still does.
    assert.ok(!html.includes(shared));
    assert.ok(html.includes(ownOnly));
});

test("rebalancePlanTabHtml renders all of a plan's own warnings when no top-level list is passed", () => {
    const html = win.rebalancePlanTabHtml({ summary: {}, trades: [], warnings: ['only warning'] });
    assert.ok(html.includes('only warning'));
});

test("rebalancePlanTabHtml renders nothing extra when a plan has no warnings beyond the shared ones", () => {
    const shared = 'Stale FX rate used for USD.';
    const html = win.rebalancePlanTabHtml({ summary: {}, trades: [], warnings: [shared] }, [shared]);
    assert.ok(!html.includes('alert-warning'));
});

test("rebalancePlanHeaderHtml renders generated time, strategy label and max trades", () => {
    const html = win.rebalancePlanHeaderHtml({
        generated_at: '2026-09-18T14:32:05Z',
        inputs: { strategy: 'tax_minimal', max_trades: 8 },
    });
    assert.match(html, /Generated/);
    assert.match(html, /14:32/);
    assert.ok(html.includes('Tax-minimal'));
    assert.ok(html.includes('max 8 trades'));
});

test("rebalancePlanHeaderHtml escapes an attacker-controlled strategy value", () => {
    const html = win.rebalancePlanHeaderHtml({
        generated_at: '2026-09-18T14:32:05Z',
        inputs: { strategy: '<script>alert(1)</script>', max_trades: 5 },
    });
    assert.ok(!html.includes('<script>alert(1)</script>'));
    assert.ok(html.includes('&lt;script&gt;'));
});

test("rebalancePlanHeaderHtml tolerates missing generated_at/inputs without crashing", () => {
    assert.equal(win.rebalancePlanHeaderHtml({}), win.rebalancePlanHeaderHtml({ inputs: {} }));
    assert.doesNotThrow(() => win.rebalancePlanHeaderHtml(null));
    assert.doesNotThrow(() => win.rebalancePlanHeaderHtml(undefined));
});

test("rebalancePlanErrorMessage reads a Pydantic-style detail array", () => {
    const raw = JSON.stringify({ detail: [{ loc: ['body', 'max_trades'], msg: 'ensure this value is <= 100', type: 'value_error' }] });
    assert.equal(win.rebalancePlanErrorMessage(raw), 'ensure this value is <= 100');
});

test("rebalancePlanErrorMessage reads a service-level string detail", () => {
    const raw = JSON.stringify({ detail: 'Targets must sum to 100%' });
    assert.equal(win.rebalancePlanErrorMessage(raw), 'Targets must sum to 100%');
});

test("rebalancePlanErrorMessage doesn't crash on unparseable or empty input", () => {
    assert.equal(win.rebalancePlanErrorMessage(''), 'Error generating plan.');
    assert.equal(win.rebalancePlanErrorMessage(undefined), 'Error generating plan.');
    assert.equal(win.rebalancePlanErrorMessage('not json'), 'not json');
});

test("rebalanceInitTabLabels runs without throwing (no matching DOM elements under test)", () => {
    assert.doesNotThrow(() => win.rebalanceInitTabLabels());
});
