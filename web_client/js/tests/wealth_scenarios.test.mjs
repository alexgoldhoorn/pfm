// Saved Wealth Simulator scenarios: the params mapping, the tracking copy,
// the chat opener, and the projection parity with the Python service.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

// The same fixture as tests/unit/test_wealth_scenarios.py — both sides must
// produce these numbers, or the dashboard/chat and the page disagree.
const FIXTURE = {
    cash_amount: 20000, cash_rate_pct: 1.5,
    stocks_amount: 100000, stocks_rate_pct: 6, stocks_vol_pct: 16, stocks_contribution_monthly: 500,
    bonds_amount: 10000, bonds_rate_pct: 3,
    mortgage_principal: 150000, mortgage_rate_pct: 3, mortgage_payment_monthly: 900,
    years: 20, confidence_z: 0.674,
};
const r2 = (v) => Math.round(v * 100) / 100;

test("runProjectionFromParams matches the Python service on the shared fixture", () => {
    const r = win.runProjectionFromParams(FIXTURE);
    assert.equal(r2(r.data[0].assets), 130000);
    assert.equal(r2(r.data[0].netWorthHigh), -11690.66);
    assert.equal(r2(r.data[5].assets), 200783.54);
    assert.equal(r2(r.data[5].mortgage), 116060.48);
    assert.equal(r2(r.data[5].netWorthLow), 47477.76);
    assert.equal(r2(r.data[20].netWorth), 586425.31);
    assert.equal(r2(r.data[20].netWorthHigh), 926382.39);
    assert.equal(r.mortgagePaidOffYear, 18);
    assert.equal(r2(r.totalInterestPaid), 44281.32);
});

test("params <-> inputs round-trip, defaults fill the gaps", () => {
    const inputs = win.forecastInputsFromParams(FIXTURE);
    assert.equal(inputs.stocksContribution, "500");
    assert.equal(inputs.monthlyPayment, "900");
    assert.equal(inputs.confidence, "0.674");
    // objects from the vm sandbox have another realm's prototype, so compare by value
    assert.deepEqual(JSON.parse(JSON.stringify(win.forecastParamsFromInputs(inputs))), FIXTURE);

    const sparse = win.forecastParamsFromInputs({ cashAmount: "abc", years: "" });
    assert.equal(sparse.cash_amount, 0);
    assert.equal(sparse.years, 30);
    assert.equal(sparse.stocks_rate_pct, 8);
    assert.equal(win.forecastInputsFromParams({}).stocksVol, "16");
});

test("signedEurWhole carries the sign outside the money format", () => {
    assert.match(win.signedEurWhole(1234), /^\+/);
    assert.match(win.signedEurWhole(-1234), /^−/);
    assert.match(win.signedEurWhole(0), /^\+/);
});

test("tracking badge labels every status and falls back", () => {
    assert.match(win.wealthTrackingBadgeHtml("on_track"), /bg-success.*On track/);
    assert.match(win.wealthTrackingBadgeHtml("behind"), /bg-warning.*Behind plan/);
    assert.match(win.wealthTrackingBadgeHtml("ahead"), /Ahead of plan/);
    assert.match(win.wealthTrackingBadgeHtml("too_early"), /Too early/);
    assert.match(win.wealthTrackingBadgeHtml(undefined), /No baseline/);
});

const TRACK = {
    status: "behind", baseline_date: "2026-01-01", days_elapsed: 120,
    actual_change_eur: -5000, expected_change_eur: 2000,
    expected_low_change_eur: -1500, expected_high_change_eur: 6000,
    gap_eur: -7000, gap_pct: -1.4,
};

test("wealthTrackingHtml spells out plan vs actual and the gap", () => {
    const html = win.wealthTrackingHtml(TRACK);
    assert.match(html, /Since <strong>.*2026.*<\/strong> \(120 days\)/);
    assert.match(html, /Gap to plan/);
    assert.match(html, /−1\.4% of the baseline net worth/);
    assert.match(html, /Check the return assumptions/);
    assert.doesNotMatch(html, /at least 30 days/);
    const early = win.wealthTrackingHtml({ ...TRACK, status: "too_early", days_elapsed: 3 });
    assert.match(early, /at least 30 days/);
});

test("wealthTrackingHtml compact form is one line with the badge", () => {
    const html = win.wealthTrackingHtml(TRACK, { compact: true });
    assert.match(html, /Behind plan/);
    assert.match(html, /vs .* expected since/);
    assert.doesNotMatch(html, /Gap to plan/);
});

test("wealthTrackingHtml explains a missing baseline", () => {
    assert.match(win.wealthTrackingHtml({ status: "no_baseline", baseline_date: "2026-01-01" }), /Re-baseline to start tracking/);
    assert.equal(win.wealthTrackingHtml(null), "");
});

test("scenarioChatMessage names the plan, its inputs, outcome and tracking", () => {
    const msg = win.scenarioChatMessage({
        name: "Base case", baseline_date: "2026-01-01", params: FIXTURE,
        summary: { horizon_years: 20, final_net_worth_eur: 586425.31, final_net_worth_low_eur: 375475.04, final_net_worth_high_eur: 926382.39, mortgage_paid_off_year: 18 },
        tracking: TRACK,
    });
    assert.match(msg, /"Base case" \(saved 2026-01-01; it is in get_wealth_scenarios\)/);
    assert.match(msg, /6% real return with 16% volatility/);
    assert.match(msg, /mortgage .* at 3% paying/);
    assert.match(msg, /mortgage paid off in year 18/);
    assert.match(msg, /Tracking: Behind plan/);
    assert.match(msg, /what single change/);

    const unsaved = win.scenarioChatMessage({
        name: "current inputs", params: { ...FIXTURE, mortgage_principal: 0 },
        summary: { horizon_years: 20, final_net_worth_eur: 1, final_net_worth_low_eur: 1, final_net_worth_high_eur: 1, mortgage_paid_off_year: null },
    });
    assert.match(unsaved, /\(not saved yet\)/);
    assert.match(unsaved, /no mortgage/);
    assert.doesNotMatch(unsaved, /Tracking:/);
});
