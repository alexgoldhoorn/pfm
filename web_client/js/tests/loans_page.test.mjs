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

// ── rate history, rate change, detect ──────────────────────────────────────

test("a new rate replaces the repayment options in the what-if query", () => {
    const q = params(win.loanWhatIfQuery({
        lump: "10000", extra: "50", mode: "payment", returnPct: "5",
        newRate: "4.25", ratePts: "1", rateFrom: "2026-12-01",
    }));
    assert.deepEqual(q, { new_rate_pct: "4.25", rate_from: "2026-12-01" });
    const pts = params(win.loanWhatIfQuery({ ratePts: "-0.5" }));
    assert.deepEqual(pts, { rate_change_pts: "-0.5" });
    // zero points is no change, an empty rate falls back to the repayment fields
    assert.equal(win.loanWhatIfQuery({ ratePts: "0", lump: "", extra: "" }), null);
    assert.equal(params(win.loanWhatIfQuery({ newRate: "0" })).new_rate_pct, "0");
});

const rateChange = {
    months_saved: 0, interest_saved_eur: -6000,
    baseline: { end_date: "2040-01-01", monthly_payment_eur: 500, interest_remaining_eur: 30000, months_left: 160 },
    scenario: { end_date: "2040-01-01", monthly_payment_eur: 560, interest_remaining_eur: 36000, months_left: 160 },
    invest: null,
    rate_change: { from_date: "2026-12-01", previous_rate_pct: 3, new_rate_pct: 4, balance_eur: 80000,
        payment_before_eur: 500, payment_after_eur: 560, payment_diff_eur: 60, interest_diff_eur: 6000 },
};

test("rate change what-if says what it costs and colours a rise as bad", () => {
    const html = win.loanWhatIfHtml(rateChange);
    assert.match(html, /With new rate/);
    assert.match(html, /Costs <strong>.*6[.,]000/);
    assert.match(html, /adds .*60/);
    assert.match(html, /text-danger/);
    assert.doesNotMatch(html, /text-success/);
    const rows = win.loanWhatIfRows(rateChange);
    assert.equal(rows[1].good, false);
    assert.equal(rows[0].good, null);
});

test("a lower rate reads as savings", () => {
    const res = { ...rateChange, interest_saved_eur: 3000,
        scenario: { ...rateChange.scenario, monthly_payment_eur: 450, interest_remaining_eur: 27000 },
        rate_change: { ...rateChange.rate_change, new_rate_pct: 2, payment_diff_eur: -50, interest_diff_eur: -3000 } };
    const html = win.loanWhatIfHtml(res);
    assert.match(html, /Saves/);
    assert.match(html, /takes off/);
    assert.match(html, /text-success/);
});

test("chart model flags history years and the svgs draw them differently", () => {
    const m = win.loanChartModel([
        { year: 2023, balance_end_eur: 900, principal_paid_eur: 100, interest_paid_eur: 30, history: true },
        { year: 2024, balance_end_eur: 800, principal_paid_eur: 100, interest_paid_eur: 28, history: false },
        { year: 2025, balance_end_eur: 700, principal_paid_eur: 100, interest_paid_eur: 26, history: false },
    ]);
    assert.deepEqual(Array.from(m.history), [true, false, false]);
    const balance = win.loanBalanceSvg(m, 400);
    assert.match(balance, /stroke-dasharray="4 3"/);
    assert.match(balance, /rate history/);
    assert.match(win.loanPaidSvg(m, 400), /opacity="0.55"/);
    // without history nothing is dashed or faded
    const plain = win.loanChartModel([{ year: 2025, balance_end_eur: 700, principal_paid_eur: 1, interest_paid_eur: 1 }]);
    assert.doesNotMatch(win.loanBalanceSvg(plain, 400), /stroke-dasharray/);
    assert.doesNotMatch(win.loanPaidSvg(plain, 400), /opacity="0.55"/);
});

test("yearly table rows mark history years and the anchor year's earlier payments", () => {
    const html = win.loanScheduleRowsHtml([
        { year: 2023, history: true, payments_eur: 6000, principal_paid_eur: 4000, interest_paid_eur: 2000, balance_end_eur: 90000 },
        { year: 2024, history: false, payments_eur: 6000, principal_paid_eur: 4200, interest_paid_eur: 1800, balance_end_eur: 86000,
            history_payments_count: 1, history_principal_paid_eur: 400, history_interest_paid_eur: 200 },
    ]);
    assert.match(html, /table-secondary/);
    assert.equal((html.match(/>history</g) || []).length, 1);
    assert.match(html, /incl\. 1 before the recorded balance/);
    assert.doesNotMatch(win.loanScheduleRowsHtml([{ year: 2025, payments_eur: 1, principal_paid_eur: 1, interest_paid_eur: 0, balance_end_eur: 0 }]), /history/);
});

test("rate period rows escape the note and carry the ids", () => {
    assert.match(win.loanPeriodsHtml([]), /No earlier rates/);
    const html = win.loanPeriodsHtml([
        { id: 7, from_date: "2020-01-01", annual_rate_pct: 2.4, balance_eur: null, note: "<b>fixed</b>" },
        { id: 8, from_date: "2023-01-01", annual_rate_pct: 3.6, balance_eur: 90000, note: null },
    ]);
    assert.match(html, /data-period-edit="7"/);
    assert.match(html, /data-period-delete="8"/);
    assert.match(html, /&lt;b&gt;fixed/);
    assert.doesNotMatch(html, /<b>fixed/);
});

test("period payload validates the form values", () => {
    assert.match(win.loanPeriodPayload({ from: "", rate: "2" }).error, /date/);
    assert.match(win.loanPeriodPayload({ from: "2020-01-01", rate: "" }).error, /rate/);
    assert.match(win.loanPeriodPayload({ from: "2020-01-01", rate: "99" }).error, /rate/);
    assert.match(win.loanPeriodPayload({ from: "2020-01-01", rate: "2", balance: "-5" }).error, /balance/);
    const ok = win.loanPeriodPayload({ from: "2020-01-01", rate: "2.4", balance: "", note: " fixed " });
    assert.deepEqual({ ...ok.body }, { from_date: "2020-01-01", annual_rate_pct: 2.4, note: "fixed", balance_eur: null });
    assert.equal(win.loanPeriodPayload({ from: "2020-01-01", rate: "0", balance: "5000" }).body.balance_eur, 5000);
});

test("paid-to-date text covers unanchored, incomplete and complete loans", () => {
    assert.match(win.loanPaidToDateHtml({ anchored: false }), /nothing earlier is missing/);
    assert.match(win.loanPaidToDateHtml({ anchored: true, anchor_date: "2025-01-01", paid_to_date: { complete: false } }), /Add the rates/);
    const html = win.loanPaidToDateHtml({ anchored: true, paid_to_date: {
        complete: true, from_date: "2020-02-01", principal_eur: 20000, interest_eur: 9000, payments_count: 77, anchor_gap_eur: 2500 } });
    assert.match(html, /77 payments/);
    assert.match(html, /above/);
    assert.doesNotMatch(win.loanPaidToDateHtml({ anchored: true, paid_to_date: {
        complete: true, from_date: "2020-02-01", principal_eur: 1, interest_eur: 1, payments_count: 1, anchor_gap_eur: 0.4 } }), /above|below/);
});

test("cards add a paid-so-far card only when the history is complete", () => {
    const base = { balance_eur: 1000, months_left: 10, annual_rate_pct: 3, rate_as_of: null,
        monthly_payment_eur: 100, end_date: "2030-01-01", principal_paid_this_year_eur: 1,
        interest_paid_this_year_eur: 1, interest_remaining_eur: 50 };
    const eq = { equity_eur: null, reason: "x" };
    assert.equal(win.loanCards({ summary: base, equity: eq }).some(c => c.label === "Paid so far"), false);
    const withPaid = win.loanCards({ summary: { ...base, paid_to_date: { complete: true, total_eur: 29000,
        principal_eur: 20000, interest_eur: 9000, from_date: "2020-02-01" } }, equity: eq });
    assert.equal(withPaid.find(c => c.label === "Paid so far").help, "loanPaidToDate");
});

test("before-anchor comparison table lists the compared months", () => {
    const html = win.loanReconHtml({ category: "Mortgage", rows: [], data_through: null, before_anchor: [
        { month: "2023-10", compared: true, scheduled_eur: 500, actual_eur: 500, difference_eur: 0, status: "ok" },
        { month: "2023-11", compared: true, scheduled_eur: 500, actual_eur: 520, difference_eur: 20, status: "differs" },
        { month: "2019-12", compared: false, actual_eur: 100 },
    ] });
    assert.match(html, /Before the recorded balance/);
    assert.match(html, /1 differ/);
    assert.match(html, /Before the schedule's start: 2019-12/);
});

test("detect query and fill", () => {
    assert.equal(win.loanDetectQuery({ category: "", principal: "", term: "" }), "");
    assert.deepEqual(params(win.loanDetectQuery({ category: "Mortgage", principal: "150000", term: "300" })),
        { category: "Mortgage", principal: "150000", term_months: "300" });
    const none = win.loanDetectFill({ found: false, reason: "Only 2 month(s)" });
    assert.equal(none.ok, false);
    assert.deepEqual({ ...none.fields }, {});
    assert.match(none.message, /Only 2/);
    const fill = win.loanDetectFill({
        found: true, monthly_payment_eur: 612.34, months_observed: 36, first_payment_date: "2021-02-01",
        rates_reason: null, notes: ["Rates assume the first debit is the first payment."],
        rate_periods: [{ from_date: "2021-01-01", annual_rate_pct: 2 }],
        form: { start_date: "2021-01-01", annual_rate_pct: 3.5, rate_as_of: "2023-01-01", balance_as_of: 120000.5 },
    });
    assert.equal(fill.ok, true);
    assert.deepEqual({ ...fill.fields }, { nwLoanStart: "2021-01-01", nwLoanRate: 3.5,
        nwLoanRateAsOf: "2023-01-01", nwLoanBalanceAsOf: 120000.5 });
    assert.match(fill.message, /612/);
    assert.match(fill.message, /Earlier rates to add/);
    assert.match(fill.message, /Rates assume/);
});
