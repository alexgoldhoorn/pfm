// Pure renderers behind the Net Worth retirement card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const path = (n) => Array.from({ length: n }, (_, i) => ({
    age: 43 + i, year: 2026 + i, investments_eur: 1000 * (i + 1),
    cash_eur: 500, loans_eur: 2000 - 100 * i, pensions_eur: 0, withdrawal_eur: 0,
}));

const scen = (age, kase, over) => ({
    retirement_age: age, case: kase, verdict: "on_track", lasts_until_age: 95,
    pot_at_retirement_eur: 500000, pot_at_horizon_eur: 1000,
    sustainable_monthly_eur: 2200, gap_monthly_eur: -200,
    extra_saving_needed_monthly_eur: 0, path: path(5), ...over,
});

const ready = (over) => ({
    status: "ok", as_of: "2026-10-18",
    plan: { retirement_age: 65, statutory_age: 67, horizon_age: 95 },
    inputs: {
        age: 43, spending_target_monthly_eur: 2000, spending_months_used: 12,
        incomes: [{ name: "Example Pension", monthly_eur: 1000, from_age: 67 }],
    },
    scenarios: [
        scen(65, "base"), scen(65, "pessimistic", { lasts_until_age: 88 }),
        scen(67, "base"), scen(67, "pessimistic", { lasts_until_age: 90 }),
    ],
    warnings: [], ...over,
});

test("needs_setup renders the setup copy", () => {
    const html = win.retirementVerdictHtml({ status: "needs_setup" });
    assert.match(html, /Enter your birth date to get a retirement verdict\./);
});

test("insufficient_data renders the reason and no verdict", () => {
    const html = win.retirementVerdictHtml({
        status: "insufficient_data", reason: "No <spending> imported",
    });
    assert.match(html, /No &lt;spending&gt; imported/);
    assert.doesNotMatch(html, /On track|Short|At risk/);
});

test("a surplus is shown as a surplus", () => {
    const html = win.retirementVerdictHtml(ready());
    assert.match(html, /On track/);
    assert.match(html, /surplus of/);
    assert.match(html, /text-bg-success/);
    assert.match(html, /Pessimistic: lasts until 88/);
});

test("a gap shows short by and the saving needed", () => {
    const r = ready();
    r.scenarios[0] = scen(65, "base", {
        verdict: "short", lasts_until_age: 85, pot_at_horizon_eur: 0,
        gap_monthly_eur: 300, extra_saving_needed_monthly_eur: 150,
    });
    const html = win.retirementVerdictHtml(r);
    assert.match(html, /Short/);
    assert.match(html, /text-bg-warning/);
    assert.match(html, /short by/);
    assert.match(html, /save/);
    assert.match(html, /lasts until age 85/);
});

test("a depleted scenario says until age N, never past the horizon", () => {
    const r = ready();
    r.scenarios[0] = scen(65, "base", {
        verdict: "at_risk", lasts_until_age: 71, pot_at_horizon_eur: 0,
        gap_monthly_eur: 900, extra_saving_needed_monthly_eur: 400,
    });
    r.scenarios = r.scenarios.filter(s => s.retirement_age === 65);
    const html = win.retirementVerdictHtml(r);
    assert.match(html, /Money lasts until age 71/);
    assert.doesNotMatch(html, /Money lasts past/);
});

test("null extra saving says it is not reachable", () => {
    const r = ready();
    r.scenarios[0] = scen(65, "base", {
        verdict: "at_risk", lasts_until_age: 70, pot_at_horizon_eur: 0,
        gap_monthly_eur: 900, extra_saving_needed_monthly_eur: null,
    });
    const html = win.retirementVerdictHtml(r);
    assert.match(html, /At risk/);
    assert.match(html, /text-bg-danger/);
    assert.match(html, /not reachable by saving alone/);
});

test("a warning with markup is escaped", () => {
    const html = win.retirementVerdictHtml(ready({ warnings: ["x <script>y</script>"] }));
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
});

test("chart series have equal lengths", () => {
    const s = win.retirementChartSeries(scen(65, "base"));
    assert.equal(s.ages.length, 5);
    assert.equal(s.investments.length, 5);
    assert.equal(s.cash.length, 5);
    assert.equal(s.loans.length, 5);
});

test("goal line falls back to a setup hint for needs_setup", () => {
    const html = win.retirementGoalLineHtml({ status: "needs_setup" });
    assert.match(html, /Retirement readiness/);
    assert.match(html, /set up your plan on the Net Worth page/);
});

test("goal line prints the reason for insufficient_data", () => {
    const html = win.retirementGoalLineHtml({ status: "insufficient_data", reason: "No <b>data</b>" });
    assert.match(html, /No &lt;b&gt;data&lt;\/b&gt;/);
});

test("zero gap is not 'short by'", () => {
    const r = ready();
    r.scenarios[0] = scen(65, "base", { gap_monthly_eur: 0 });
    const html = win.retirementVerdictHtml(r);
    assert.doesNotMatch(html, /short by/);
    assert.match(html, /no gap/);
});
