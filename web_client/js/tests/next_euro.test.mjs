// Pure renderers behind the Next euro card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const buffer = (over) => ({
    status: "topping_up", months: 3.2, min_months: 4, target_months: 6, max_months: 8,
    target_eur: 12000, ...over,
});
const dep = (over) => ({
    status: "ok", liquid_eur: 30000, deployable_eur: 9000,
    tax_reserve: { reserve_eur: 4000 }, buffer: buffer(), coming_in: [], warnings: [], ...over,
});
const result = (over) => ({
    status: "ok", amount_eur: 9000, buckets: [
        { asset_type: "etf", target_pct: 60, before_pct: 50, after_pct: 55, gap_eur: 5000, allocated_eur: 5000 },
    ],
    lines: [{
        asset_type: "etf", symbol: "EXA", name: "Example ETF", portfolio_name: "Main",
        amount_eur: 5000, reasons: ["Bucket is 10 pp under target"],
        runner_up: { symbol: "EXB", why_not: "higher fees" },
    }],
    traspasos: [], blocked: [], needs_research: [], unallocated_eur: 0, warnings: [], ...over,
});

test("summary shows the three parts and the status badge", () => {
    const html = win.renderDeployableSummary(dep());
    assert.match(html, /deployable/);
    assert.match(html, /liquid/);
    assert.match(html, /tax reserve/);
    assert.match(html, /buffer target/);
    assert.match(html, /text-bg-warning/);
    assert.match(html, /topping up/);
});

test("gauge marks min, target and max and the current months", () => {
    const html = win.bufferGaugeHtml(buffer());
    for (const m of ["min", "target", "max"]) assert.ok(html.includes(`data-marker="${m}"`), m);
    assert.match(html, /data-current="3.2"/);
    assert.match(html, /3\.2 months/);
});

test("lines render reasons and the runner-up", () => {
    const html = win.renderNextEuroLines(result());
    assert.match(html, /Bucket is 10 pp under target/);
    assert.match(html, /Runner-up EXB: higher fees/);
    assert.match(html, /EXA/);
});

test("markup in a reason is escaped", () => {
    const html = win.renderNextEuroLines(result({
        lines: [{ symbol: "EXA", name: "x", amount_eur: 100, reasons: ["<b>bold</b>"], runner_up: null }],
    }));
    assert.ok(!html.includes("<b>bold</b>"));
    assert.match(html, /&lt;b&gt;bold&lt;\/b&gt;/);
});

test("targets_incomplete renders the reason and no lines", () => {
    const html = win.renderNextEuroLines({
        status: "targets_incomplete", reasons: ["Targets sum to 90%"], lines: [result().lines[0]],
    });
    assert.match(html, /Targets sum to 90%/);
    assert.ok(!html.includes("EXA"));
});
