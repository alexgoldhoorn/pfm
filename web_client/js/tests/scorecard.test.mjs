// Pure helpers behind the Research scorecard tab and the managed line.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("scorecardPointText: pending for null, signed return with error", () => {
    assert.equal(win.scorecardPointText(null), "pending");
    assert.equal(
        win.scorecardPointText({ realised_return_pct: 12.34, target_error_pct: -4.1 }),
        "+12.3% (err −4.1%)"
    );
    assert.equal(
        win.scorecardPointText({ realised_return_pct: -2, target_error_pct: null }),
        "−2.0%"
    );
    assert.equal(
        win.scorecardPointText({ realised_return_pct: null, target_error_pct: 5 }),
        "err +5.0%"
    );
});

test("scorecardHitBadge: empty for null, colours for hit/miss/target", () => {
    assert.equal(win.scorecardHitBadge(null), "");
    assert.equal(win.scorecardHitBadge({ direction_hit: null, reached_target: null }), "");
    const hit = win.scorecardHitBadge({ direction_hit: true, reached_target: false });
    assert.match(hit, /bg-success[^>]*>hit/);
    assert.match(hit, /bg-secondary[^>]*>no target/);
    const miss = win.scorecardHitBadge({ direction_hit: false, reached_target: true });
    assert.match(miss, /bg-danger[^>]*>miss/);
    assert.match(miss, /bg-success[^>]*>target/);
});

test("scorecardRowHtml: one row, pending points, escapes strings", () => {
    const row = win.scorecardRowHtml({
        symbol: "<b>X</b>", date: "2026-01-05", source: "llm", recommendation: "BUY",
        confidence: "high", fair_value: 120, price_at_report: 100, currency: "EUR",
        implied_return_pct: 20, at_horizon: null,
        at_next: { realised_return_pct: 5, target_error_pct: -10, direction_hit: true, reached_target: false },
        now: { realised_return_pct: 3 },
    });
    assert.ok(row.startsWith("<tr>") && row.trimEnd().endsWith("</tr>"));
    assert.ok(!row.includes("<b>X</b>"));
    assert.match(row, /&lt;b&gt;X/);
    assert.match(row, /pending/);
    assert.match(row, /\+20\.0% implied/);
    assert.match(row, /\+5\.0% \(err −10\.0%\)/);
    assert.match(row, /bg-success/);
});

test("scorecardRowHtml tolerates a bare entry", () => {
    assert.match(win.scorecardRowHtml({}), /<tr>/);
});

test("scorecardHtml: empty state and tiles", () => {
    assert.match(win.scorecardHtml({ entries: [] }), /No stored research/);
    const html = win.scorecardHtml({
        entries: [{ symbol: "AAA", date: "2026-01-01", at_horizon: null, at_next: null }],
        totals: { at_horizon: { n: 0 }, at_next: { n: 3, direction_hit_rate_pct: 67, median_abs_target_error_pct: 8.2 } },
        by_recommendation: { BUY: { at_horizon: { n: 0 }, at_next: { n: 3, direction_hit_rate_pct: 67 } } },
    });
    assert.match(html, /Nothing scored yet/);
    assert.match(html, /67%/);
    assert.match(html, /By recommendation/);
});

test("rebalanceManagedLineHtml: empty at zero, names and value otherwise", () => {
    assert.equal(win.rebalanceManagedLineHtml({ value_eur: 0, pct: 0, portfolios: [] }), "");
    assert.equal(win.rebalanceManagedLineHtml(undefined), "");
    const line = win.rebalanceManagedLineHtml({ value_eur: 5000, pct: 12.5, portfolios: ["Robo <1>"] });
    assert.match(line, /Managed by a robo-advisor, never traded/);
    assert.match(line, /12\.5%/);
    assert.match(line, /Robo &lt;1&gt;/);
});

test("rebalancePlanHeaderHtml shows managed symbols as kept at weight", () => {
    const h = win.rebalancePlanHeaderHtml({ inputs: { strategy: "balanced", managed_symbols: ["AAA", "BBB"] } });
    assert.match(h, /Kept at weight: AAA, BBB/);
    assert.ok(!win.rebalancePlanHeaderHtml({ inputs: { strategy: "balanced", managed_symbols: [] } }).includes("Kept at weight"));
});
