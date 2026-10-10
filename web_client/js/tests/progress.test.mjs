// Tests for the progress tiles and monthly grid (pfm_analytics.js) and the
// progress rating bands (pfm_core.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const payload = {
    contributions: { net_contributions_eur: 1000, growth_eur: 250, months: new Array(18).fill({}) },
    real: { irr_pct: 7.1, real_irr_pct: 4.0, inflation_annual_pct: 3.0 },
    savings: { emergency_months: 2.4, savings_rate_pct: 22.5, months_used: ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"] },
};

test("progressTiles: growth share, real return, buffer and savings rate", () => {
    const tiles = win.progressTiles(payload);
    assert.deepEqual(JSON.parse(JSON.stringify(tiles.map(t => t.key))), ["growth", "realReturn", "emergencyMonths", "savingsRate"]);
    assert.equal(tiles[0].value, 25);
    assert.equal(tiles[1].text, "+4.0%/yr");
    assert.equal(tiles[1].lowConfidence, false);
    assert.equal(tiles[2].text, "2.4 mo");
    assert.equal(tiles[3].text, "23%");
    // No euro amounts in tile text: tiles can't carry the privacy blur.
    assert.ok(tiles.every(t => !String(t.text).includes("€") && !String(t.sub).includes("€")));
});

test("progressTiles: missing data says what is needed", () => {
    const tiles = win.progressTiles({ contributions: { net_contributions_eur: 0, months: [] }, real: {}, savings: {} });
    assert.equal(tiles[0].value, null);
    assert.match(tiles[1].sub, /inflation data unavailable/);
    assert.match(tiles[2].sub, /needs bank balances/);
    assert.match(tiles[3].sub, /needs imported bank statements/);
});

test("progress bands", () => {
    assert.equal(win.rateMetric("emergencyMonths", 6).label, "Covered");
    assert.equal(win.rateMetric("emergencyMonths", 2.4).label, "Short");
    assert.equal(win.rateMetric("savingsRate", 15).level, "ok");
    assert.equal(win.rateMetric("realReturn", -0.5).label, "Losing to inflation");
});

test("monthlyGridRows: newest year first, 12 cells with gaps", () => {
    // JSON round-trip: arrays built inside the sandbox fail cross-realm deepEqual.
    const rows = JSON.parse(JSON.stringify(win.monthlyGridRows({ "2024": { "12": 1.5 }, "2025": { "01": -2, "03": 0.5 } })));
    assert.deepEqual(rows.map(r => r.year), ["2025", "2024"]);
    assert.equal(rows[0].cells.length, 12);
    assert.deepEqual(rows[0].cells.slice(0, 3), [-2, null, 0.5]);
    assert.equal(rows[1].cells[11], 1.5);
});
