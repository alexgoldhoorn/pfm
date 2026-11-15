// Pure helpers behind the Loans & Mortgage page and the budget link picker.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

// the sandbox has no URLSearchParams
const params = (qs) => Object.fromEntries(qs.split("&").map(p => p.split("=").map(decodeURIComponent)));

test("what-if query is null until an amount is entered", () => {
    assert.equal(win.loanWhatIfQuery({ lump: "", extra: "", mode: "term" }), null);
    assert.equal(win.loanWhatIfQuery({ lump: "0", extra: "0", mode: "term" }), null);
    assert.equal(win.loanWhatIfQuery({ lump: "abc", extra: "", mode: "term" }), null);
});

test("what-if query carries the entered fields only", () => {
    const q = params(win.loanWhatIfQuery({
        lump: "10000", date: "2026-12-01", extra: "100", mode: "term", returnPct: "5",
    }));
    assert.equal(q.lump_sum, "10000");
    assert.equal(q.lump_date, "2026-12-01");
    assert.equal(q.extra_monthly, "100");
    assert.equal(q.mode, "term");
    assert.equal(q.invest_return_pct, "5");
});

test("payment mode drops the extra monthly amount", () => {
    const q = win.loanWhatIfQuery({ lump: "", extra: "100", mode: "payment" });
    assert.equal(q, null);
    const q2 = params(win.loanWhatIfQuery({ lump: "500", extra: "100", mode: "payment" }));
    assert.equal(q2.extra_monthly, undefined);
    assert.equal(q2.mode, "payment");
});

test("recent rows are the last n, newest first", () => {
    const rows = Array.from({ length: 20 }, (_, i) => ({ month: `m${i}` }));
    const out = win.loanRecentRows(rows, 12);
    assert.equal(out.length, 12);
    assert.equal(out[0].month, "m19");
    assert.equal(out[11].month, "m8");
    assert.deepEqual(Array.from(win.loanRecentRows(null, 3)), []);
});

test("status badges escape and fall back to pending", () => {
    assert.match(win.loanStatusBadge("differs"), /Differs/);
    assert.match(win.loanStatusBadge("missing"), /Not charged/);
    assert.match(win.loanStatusBadge("nonsense"), /Pending/);
});

test("months text", () => {
    assert.equal(win.loanMonthsText(0), "0 m");
    assert.equal(win.loanMonthsText(14), "1 y 2 m");
    assert.equal(win.loanMonthsText(24), "2 y 0 m");
});

test("chart model and svg for an empty or one-year schedule", () => {
    const empty = win.loanChartModel([]);
    assert.equal(win.loanBalanceSvg(empty), "");
    assert.equal(win.loanPaidSvg(empty), "");
    const one = win.loanChartModel([{ year: 2026, balance_end_eur: 1000, principal_paid_eur: 400, interest_paid_eur: 50 }]);
    assert.match(win.loanBalanceSvg(one, 400), /<svg/);
    assert.match(win.loanPaidSvg(one, 400), /Principal/);
});

test("chart model follows the schedule", () => {
    const m = win.loanChartModel([
        { year: 2025, balance_end_eur: 900, principal_paid_eur: 100, interest_paid_eur: 30 },
        { year: 2026, balance_end_eur: 800, principal_paid_eur: 100, interest_paid_eur: 28 },
    ]);
    assert.deepEqual(Array.from(m.years), [2025, 2026]);
    assert.deepEqual(Array.from(m.balance), [900, 800]);
});

test("cards without a property explain why equity is missing", () => {
    const cards = win.loanCards({
        summary: { balance_eur: 1000, months_left: 10, annual_rate_pct: 3, rate_as_of: null,
            monthly_payment_eur: 100, end_date: "2030-01-01", principal_paid_this_year_eur: 1,
            interest_paid_this_year_eur: 1, interest_remaining_eur: 50 },
        equity: { equity_eur: null, reason: "No property" },
    });
    const eq = cards.find(c => c.label === "Equity");
    assert.equal(eq.value, "—");
    assert.equal(eq.sub, "No property");
});

test("reconciliation without rows shows the reason", () => {
    assert.match(win.loanReconHtml({ rows: null, reason: "No spending category" }), /No spending category/);
    const html = win.loanReconHtml({
        category: "Mortgage", data_through: null, before_anchor: [],
        rows: [{ month: "2026-05", scheduled_eur: 700, actual_eur: 750, difference_eur: 50, status: "differs" }],
    });
    assert.match(html, /Differs/);
    assert.match(html, /\+/);
});

test("what-if html shows the investment comparison with its caveat", () => {
    const res = {
        months_saved: 14, interest_saved_eur: 5000,
        baseline: { end_date: "2040-01-01", monthly_payment_eur: 700, interest_remaining_eur: 40000, months_left: 160 },
        scenario: { end_date: "2038-11-01", monthly_payment_eur: 700, interest_remaining_eur: 35000, months_left: 146 },
        invest: { return_pct: 5, value_at_baseline_end_eur: 14000, gain_eur: 4000, contributed_eur: 10000,
            gain_minus_interest_saved_eur: -1000, note: "Before tax; not advice." },
    };
    const html = win.loanWhatIfHtml(res);
    assert.match(html, /1 y 2 m sooner/);
    assert.match(html, /not advice/);
    assert.match(html, /less than the interest saved/);
});

test("budget link picker values round trip", () => {
    assert.equal(win.budgetLinkValue("loan", 3), "loan:3");
    assert.equal(win.budgetLinkValue("manual", 5), "manual:5");
    assert.equal(win.budgetLinkValue("manual", null), "");
    const parsed = win.parseBudgetLink("loan:3");
    assert.equal(parsed.link_id, 3);
    assert.equal(parsed.link_type, "loan");
    const cleared = win.parseBudgetLink("");
    assert.equal(cleared.link_id, 0);
    assert.equal(cleared.link_type, "manual");
});

test("budget link options offer liabilities and loans and flag a dangling link", () => {
    const line = { link_id: 9, link_type: "loan" };
    const opts = win.budgetLinkOptions([{ id: 1, name: "Card" }], [{ id: 2, name: "Home" }], line);
    const values = Array.from(opts, o => o.value);
    assert.deepEqual(values, ["", "manual:1", "loan:2", "loan:9"]);
    assert.match(opts[3].label, /no longer exists/);
    const ok = win.budgetLinkOptions([], [{ id: 2, name: "Home" }], { link_id: 2, link_type: "loan" });
    assert.equal(ok.length, 2);
});
