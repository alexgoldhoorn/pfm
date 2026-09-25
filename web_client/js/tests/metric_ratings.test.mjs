// Tests for rateMetric / metricTile (pfm_core.js) — the good/OK/bad bands
// shared by the dashboard risk row and the Analytics risk card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("rated metrics map to good / ok / bad at the band edges", () => {
    assert.equal(win.rateMetric("sharpe", 1.0).level, "good");
    assert.equal(win.rateMetric("sharpe", 0.99).level, "ok");
    assert.equal(win.rateMetric("sharpe", 0.5).level, "ok");
    assert.equal(win.rateMetric("sharpe", 0.49).label, "Weak");
    assert.equal(win.rateMetric("sharpe", -0.2).label, "Negative");
    assert.equal(win.rateMetric("sortino", 1.5).level, "good");
    assert.equal(win.rateMetric("sortino", 0.8).level, "ok");
    assert.equal(win.rateMetric("calmar", 0.4).level, "bad");
    assert.equal(win.rateMetric("vsBenchmark", 2.1).label, "Ahead");
    assert.equal(win.rateMetric("vsBenchmark", -0.5).label, "In line");
    assert.equal(win.rateMetric("vsBenchmark", -3).label, "Behind");
    assert.equal(win.rateMetric("alpha", -1.5).level, "bad");
});

test("current drawdown bands", () => {
    assert.equal(win.rateMetric("currentDrawdown", 0).label, "Near high");
    assert.equal(win.rateMetric("currentDrawdown", -4.9).level, "good");
    assert.equal(win.rateMetric("currentDrawdown", -10).label, "Pullback");
    assert.equal(win.rateMetric("currentDrawdown", -20).label, "Deep");
    assert.equal(win.rateMetric("currentDrawdown", -30).label, "Severe");
    assert.equal(win.rateMetric("currentDrawdown", -30).level, "bad");
});

test("neutral metrics describe, never colour", () => {
    assert.deepEqual(
        { ...win.rateMetric("volatility", 13) },
        { level: "neutral", label: "Equity-like", range: win.METRIC_RATINGS.volatility.range },
    );
    assert.equal(win.rateMetric("volatility", 25).label, "High");
    assert.equal(win.rateMetric("beta", 0.44).label, "Swings less than market");
    assert.equal(win.rateMetric("maxDrawdown", -41).label, "Severe");
    assert.equal(win.rateMetric("maxDrawdown", -41).level, "neutral");
    // Low confidence doesn't apply to descriptive labels.
    assert.equal(win.rateMetric("volatility", 13, { lowConfidence: true }).level, "neutral");
});

test("short history greys out rated metrics", () => {
    const r = win.rateMetric("sharpe", 2.5, { lowConfidence: true, months: 5 });
    assert.equal(r.level, "low");
    assert.equal(r.label, "Low confidence (5 mo)");
});

test("missing values and unknown keys are neutral with no label", () => {
    assert.equal(win.rateMetric("sharpe", null).level, "neutral");
    assert.equal(win.rateMetric("sharpe", null).label, "");
    assert.equal(win.rateMetric("nope", 1).level, "neutral");
});

test("metricTile renders the word next to the colour, escaped", () => {
    const html = win.metricTile({ key: "sharpe", label: "Sharpe <1y>", value: 1.2, text: "1.20", help: "Return per risk" });
    assert.match(html, /pfm-metric-good/);
    assert.match(html, /pfm-metric-rating">Good</);
    assert.match(html, /Sharpe &lt;1y&gt;/);
    assert.match(html, /Return per risk — Good ≥ 1/);
    const empty = win.metricTile({ key: "sharpe", label: "Sharpe", value: null, text: "x" });
    assert.match(empty, /pfm-metric-value">—</);
    assert.doesNotMatch(empty, /pfm-metric-rating/);
});
