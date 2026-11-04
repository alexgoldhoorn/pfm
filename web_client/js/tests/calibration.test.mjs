// Pure helpers behind the research calibration line and the Scorecard table.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const block = (over = {}) => ({ n: 12, basis: "at_horizon", direction_hit_rate_pct: 58.3, median_abs_target_error_pct: 14.2, mean_implied_return_pct: 20, mean_realised_return_pct: 8, implied_vs_realised: 2.5, shrink_factor: 0.4, ...over });

test("calibrationLineHtml: empty for null", () => {
    assert.equal(win.calibrationLineHtml(null, "EUR"), "");
});

test("calibrationLineHtml: fair value, shrink, hit rate, n and basis", () => {
    const html = win.calibrationLineHtml({ confidence: "high", hit_rate_pct: 58.3, n: 12, shrink_factor: 0.4, adjusted_fair_value: 105, basis: "at_horizon" }, "EUR");
    assert.match(html, /Calibrated fair value/);
    assert.match(html, /105/);
    assert.match(html, /shrink 0[.,]40/);
    assert.match(html, /hit rate 58% on 12/);
    assert.match(html, /at horizon/);
});

test("calibrationTableHtml: empty without data", () => {
    assert.equal(win.calibrationTableHtml(null), "");
    assert.equal(win.calibrationTableHtml({ overall: { n: 0 } }), "");
});

test("calibrationTableHtml: overall and confidence rows, missing values as dash", () => {
    const html = win.calibrationTableHtml({ as_of: "2026-11-04", overall: block(), by_confidence: { high: block({ n: 6 }), low: block({ shrink_factor: null, implied_vs_realised: null, direction_hit_rate_pct: null }) } });
    assert.match(html, /Overall/);
    assert.match(html, />high</);
    assert.match(html, />low</);
    assert.match(html, /table-responsive/);
    assert.match(html, /—/);
});

test("scorecardHtml: prepends the calibration table when present", () => {
    const d = { entries: [{ id: 1, symbol: "AAA" }], totals: {}, calibration: { overall: block(), by_confidence: {} } };
    assert.match(win.scorecardHtml(d), /Calibration/);
});
