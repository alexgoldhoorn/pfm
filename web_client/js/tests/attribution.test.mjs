// Pure rendering helper behind the "What drove the return" card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const row = (over) => ({
    symbol: "UPX", name: "Example Up Corp", asset_type: "stock", gain_eur: 200,
    contribution_pct: 10, priced: true, ...over,
});

test("attributionRowsHtml: empty rows render a placeholder", () => {
    assert.match(win.attributionRowsHtml([], "symbol"), /None/);
    assert.match(win.attributionRowsHtml(null, "symbol"), /None/);
});

test("attributionRowsHtml: sign classes, share and escaping", () => {
    const up = win.attributionRowsHtml([row()], "symbol");
    assert.match(up, /text-success/);
    assert.match(up, /10\.00% of start/);
    assert.match(up, /Example Up Corp/);
    const down = win.attributionRowsHtml([row({ gain_eur: -50, contribution_pct: -2.5 })], "symbol");
    assert.match(down, /text-danger/);
    assert.match(down, /-2\.50% of start/);
    const evil = win.attributionRowsHtml([row({ symbol: "<b>x</b>" })], "symbol");
    assert.ok(!evil.includes("<b>x</b>"));
});

test("attributionRowsHtml: null contribution shows no share; unpriced gets a marker", () => {
    const none = win.attributionRowsHtml([row({ contribution_pct: null })], "symbol");
    assert.ok(!none.includes("of start"));
    assert.ok(!none.includes("bi-asterisk"));
    const unpriced = win.attributionRowsHtml([row({ priced: false })], "symbol");
    assert.match(unpriced, /bi-asterisk/);
});

test("attributionRowsHtml: labelKey picks the label and hides the name", () => {
    const html = win.attributionRowsHtml([{ asset_type: "etf", gain_eur: 5, contribution_pct: 1, priced: true, name: "n" }], "asset_type");
    assert.match(html, />etf</);
    assert.ok(!html.includes(">n<"));
});

test("attributionHtml: empty state and unpriced note", () => {
    assert.match(win.attributionHtml({ by_asset: [] }), /No priced positions/);
    const html = win.attributionHtml({
        period: { start: "2026-01-01", end: "2026-11-01" },
        totals: { gain_eur: 10, return_pct: null, start_value_eur: 1, end_value_eur: 2, income_eur: 0 },
        by_asset: [row()], top_contributors: [row()], top_detractors: [], by_type: [], by_portfolio: [],
        unpriced: ["DNX"],
    });
    assert.match(html, /n\/a/);
    assert.match(html, /DNX/);
});
