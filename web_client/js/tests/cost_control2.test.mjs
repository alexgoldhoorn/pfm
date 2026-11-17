// Pure helpers behind cost control 2: planned items, what-if, tax, seed
// proposals, the uncategorised queue and the year view. Invented data only.
// Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const w = loadAppIntoContext();
const plain = (v) => JSON.parse(JSON.stringify(v));

// ── planned items ───────────────────────────────────────────────────────────

test("plannedFormPayload: the kind decides the sign", () => {
    const base = { date: "2027-03-10", description: " New laptop ", amount: "900" };
    assert.equal(plain(w.plannedFormPayload({ ...base, kind: "spending" })).payload.amount, -900);
    assert.equal(plain(w.plannedFormPayload({ ...base, kind: "savings" })).payload.amount, -900);
    assert.equal(plain(w.plannedFormPayload({ ...base, kind: "income" })).payload.amount, 900);
    assert.equal(plain(w.plannedFormPayload({ ...base, kind: "spending" })).payload.description, "New laptop");
});

test("plannedFormPayload: bad input gives a message, not a payload", () => {
    const ok = { date: "2027-03-10", description: "x", amount: "5", kind: "spending" };
    for (const bad of [{ description: " " }, { date: "" }, { kind: "gift" }, { amount: "0" }, { amount: "abc" }]) {
        const r = plain(w.plannedFormPayload({ ...ok, ...bad }));
        assert.ok(r.error, JSON.stringify(bad));
        assert.equal(r.payload, undefined);
    }
});

test("plannedItemsModel: status comes from the forecast, done wins, a missing forecast shows none", () => {
    const items = [
        { id: 1, date: "2027-01-05", description: "Old repair", amount: -300, currency: "EUR", kind: "spending", done: false },
        { id: 2, date: "2027-02-05", description: "Bonus", amount: 500, currency: "EUR", kind: "income", done: false },
        { id: 3, date: "2027-02-06", description: "Paid trip", amount: -200, currency: "EUR", kind: "spending", done: true },
    ];
    const fc = { planned_items: [{ id: 1, status: "overdue" }, { id: 2, status: "projected" }] };
    const rows = plain(w.plannedItemsModel(items, fc));
    assert.equal(rows[0].status.label, "Overdue");
    assert.equal(rows[0].overdue, true);
    assert.equal(rows[1].status.label, "Projected");
    assert.equal(rows[1].amountClass, "text-success");
    assert.equal(rows[2].status.label, "Done");
    assert.equal(plain(w.plannedItemsModel(items, null))[0].status, null);
});

// ── what-if ─────────────────────────────────────────────────────────────────

test("forecastWhatIfModel: with and without, the month and the verdict", () => {
    const m = plain(w.forecastWhatIfModel({
        what_if: { lowest_balance_eur: -200, lowest_balance_month: "2027-03", lowest_balance_without_eur: 700, goes_below_zero: true },
    }));
    assert.equal(m.available, true);
    assert.equal(m.tone, "danger");
    assert.match(m.text, /below zero/);
    assert.match(m.text, /with it/);
    assert.equal(plain(w.forecastWhatIfModel({ what_if: { lowest_balance_eur: 50, lowest_balance_without_eur: 80, goes_below_zero: false } })).tone, "success");
});

test("forecastWhatIfModel: a null lowest balance reads 'not available', never zero", () => {
    const m = plain(w.forecastWhatIfModel({ what_if: { lowest_balance_eur: null, lowest_balance_without_eur: null, goes_below_zero: null } }));
    assert.equal(m.available, false);
    assert.match(m.text, /not available/);
    assert.equal(w.forecastWhatIfModel({}), null);
});

test("forecastWhatIfParams: needs an amount, a month and a kind", () => {
    assert.deepEqual(plain(w.forecastWhatIfParams({ amount: "250", month: "2027-04", kind: "savings" })).params, { amount: 250, date: "2027-04", kind: "savings" });
    for (const bad of [{ amount: "0" }, { month: "" }, { kind: "x" }]) {
        assert.ok(plain(w.forecastWhatIfParams({ amount: "5", month: "2027-04", kind: "spending", ...bad })).error);
    }
});

// ── tax, extras, columns ────────────────────────────────────────────────────

const fc = {
    next_months: [
        { month: "2027-01", planned_spending_eur: 0, planned_savings_eur: 0, planned_income_eur: 0, tax_eur: 0 },
        { month: "2027-02", planned_spending_eur: 300, planned_savings_eur: 0, planned_income_eur: 500, tax_eur: 1200 },
        { month: "2027-03", planned_spending_eur: null, planned_savings_eur: 0, planned_income_eur: 0, tax_eur: 800 },
    ],
    tax_payments: [{ quarter: "2027-Q1", due: "2027-04-20", amount_eur: 1200, basis: "accrued" }, { quarter: "2027-Q2", due: "2027-07-20", amount_eur: 900, basis: "average" }],
};

test("forecastTaxRows: quarter, due, amount and the basis in words", () => {
    const rows = plain(w.forecastTaxRows(fc));
    assert.equal(rows.length, 2);
    assert.equal(rows[0].quarter, "2027 Q1");
    assert.match(rows[0].basis, /Accrued/);
    assert.match(rows[1].basis, /Average/);
    assert.deepEqual(plain(w.forecastTaxRows({})), []);
});

test("forecastPlannedColumns: net of planned items, dash for nothing, not available for unknown", () => {
    const cols = plain(w.forecastPlannedColumns(fc));
    assert.equal(cols[0].planned, "—");
    assert.equal(cols[0].tax, "—");
    assert.match(cols[1].planned, /^\+/);
    assert.equal(cols[1].plannedClass, "text-success");
    assert.match(cols[1].tax, /1/);
    assert.equal(cols[2].planned, "not available");
});

test("forecastExtrasModel: three stacked series, hasData only when something is planned", () => {
    const m = plain(w.forecastExtrasModel(fc));
    assert.equal(m.datasets.length, 3);
    assert.equal(m.hasData, true);
    assert.equal(m.datasets[2].data[1], 1200);
    assert.equal(m.datasets[0].data[2], null);
    assert.equal(plain(w.forecastExtrasModel({ next_months: [fc.next_months[0]] })).hasData, false);
});

// ── seed proposals ──────────────────────────────────────────────────────────

const yearlyProposal = {
    line_type: "spending", ref_key: "Insurance", label: "Insurance", monthly_amount: 100, months_seen: 12,
    suggested_period: "yearly", suggested_amount: 1200, suggested_rollover: false, reason: "Spending in only 2 of 12 months: plan it as a yearly amount.",
    existing_line_id: 7, change: "to_yearly",
};

test("seedProposalModel: yearly suggestion keeps the server's yearly amount and offers the conversion", () => {
    const m = plain(w.seedProposalModel(yearlyProposal));
    assert.equal(m.period, "yearly");
    assert.equal(m.yearlyAmount, 1200);
    assert.equal(m.monthlyAmount, 100);
    assert.match(m.suggestedAmountText, /year/);
    assert.equal(m.changeLabel, "Convert to yearly");
    assert.deepEqual(plain(w.seedChangePayload(m)), { period: "yearly", monthly_amount: 1200, rollover: false });
});

test("seedProposalModel: a monthly suggestion can still be switched to yearly (12 x)", () => {
    const m = plain(w.seedProposalModel({ line_type: "spending", ref_key: "Fun", monthly_amount: 80, suggested_period: "monthly", suggested_amount: 80, suggested_rollover: true, reason: "uneven", change: "add_rollover", existing_line_id: 3 }));
    assert.equal(m.period, "monthly");
    assert.equal(m.rollover, true);
    assert.equal(m.yearlyAmount, 960);
    assert.deepEqual(plain(w.seedChangePayload(m)), { rollover: true });
});

test("seedProposalModel: income and investment proposals carry no suggestion", () => {
    const m = plain(w.seedProposalModel({ line_type: "income", ref_key: "Salary", monthly_amount: 3000 }));
    assert.equal(m.hasSuggestion, false);
    assert.equal(m.changeLabel, null);
    assert.equal(w.seedChangePayload(w.seedProposalModel({ line_type: "income", ref_key: "x", monthly_amount: 1 })), null);
});

test("seedLinePayload: period and rollover only for cost lines with a suggestion", () => {
    const spend = plain(w.seedLinePayload(yearlyProposal, { lineType: "spending", amount: 1200, period: "yearly", rollover: true }));
    assert.deepEqual(spend, { line_type: "spending", ref_key: "Insurance", monthly_amount: 1200, period: "yearly", rollover: false });
    const monthly = plain(w.seedLinePayload(yearlyProposal, { lineType: "debt", amount: 100, period: "monthly", rollover: true }));
    assert.equal(monthly.rollover, true);
    const inv = plain(w.seedLinePayload(yearlyProposal, { lineType: "investment", amount: 100, period: "yearly", rollover: true }));
    assert.deepEqual(inv, { line_type: "investment", ref_key: "Insurance", monthly_amount: 100 });
    const income = plain(w.seedLinePayload({ ref_key: "Salary" }, { lineType: "income", amount: 3000, period: "monthly" }));
    assert.equal(income.period, undefined);
});

// ── uncategorised queue ─────────────────────────────────────────────────────

const queue = {
    total_count: 9, total_eur: 1500, share_of_spending_pct: 12.5,
    groups: [
        { merchant: "EXAMPLE LANDLORD", direction: "out", count: 4, total_eur: 3600, first_date: "2026-08-10", last_date: "2026-11-10", sample_descriptions: ["EXAMPLE LANDLORD RENT"], transaction_ids: [1, 2, 3, 4], proposed_pattern: "EXAMPLE LANDLORD" },
        { merchant: "Someone", direction: "in", count: 1, total_eur: 50, first_date: "2026-09-01", last_date: "2026-09-01", sample_descriptions: [], transaction_ids: [9], proposed_pattern: null },
    ],
};

test("uncategorizedQueueModel: groups with ids, range, direction and the unlisted remainder", () => {
    const m = plain(w.uncategorizedQueueModel(queue));
    assert.equal(m.empty, false);
    assert.equal(m.groups.length, 2);
    assert.deepEqual(m.groups[0].ids, [1, 2, 3, 4]);
    assert.equal(m.groups[0].directionLabel, "money out");
    assert.equal(m.groups[1].directionLabel, "money in");
    assert.equal(m.groups[1].pattern, "");
    assert.equal(m.hidden, 4);
    assert.match(m.summary, /12.5%/);
    assert.equal(plain(w.uncategorizedQueueModel({ groups: [], total_count: 0 })).empty, true);
});

test("classifyPayload: needs a category, and a pattern when a rule is asked for", () => {
    const g = { ids: [1, 2] };
    assert.ok(plain(w.classifyPayload(g, " ", false, "")).error);
    assert.ok(plain(w.classifyPayload(g, "Housing", true, " ")).error);
    assert.deepEqual(plain(w.classifyPayload(g, "Housing", false, "ignored")).payload, { transaction_ids: [1, 2], category: "Housing", create_rule: false });
    assert.deepEqual(plain(w.classifyPayload(g, "Housing", true, " LANDLORD ")).payload, { transaction_ids: [1, 2], category: "Housing", create_rule: true, pattern: "LANDLORD" });
});

test("classifyResultText: says what happened", () => {
    assert.match(w.classifyResultText({ updated: 4, skipped: 0, rule_created: true, recategorized_by_rule: 2 }), /Filed 4 rows; rule added, 2 more/);
    assert.match(w.classifyResultText({ updated: 1, skipped: 2, rule_id: 5, rule_created: false }), /Filed 1 row, 2 already had a category; that rule already existed/);
});

// ── year ────────────────────────────────────────────────────────────────────

const monthsOf = (y, spend) => Array.from({ length: 12 }, (_, i) => ({ month: `${y}-${String(i + 1).padStart(2, "0")}`, spending_eur: spend[i] ?? 0, has_imports: spend[i] !== undefined }));
const yearData = {
    year: 2027, previous_year: 2026, complete: false, compared_through: "2027-03",
    totals: {
        this: { income_eur: 9000, spending_eur: 6000, savings_eur: 600, net_eur: 3000, savings_rate_pct: 33.3, one_off_eur: 400 },
        previous: { income_eur: 8700, spending_eur: 6500, savings_eur: 0, net_eur: 2200, savings_rate_pct: 25.3, one_off_eur: 0 },
        previous_full: { income_eur: 35000, spending_eur: 26000, savings_eur: 0, net_eur: 9000, savings_rate_pct: 25.7, one_off_eur: 0 },
    },
    months: { this: monthsOf(2027, [2000, 2000, 2000]), previous: monthsOf(2026, [2100, 2200, 2200, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]) },
    categories: [{ category: "Housing", this_eur: 3000, previous_eur: 2700, delta_eur: 300, delta_pct: 11.1, share_pct: 50, children: [] }],
    movers: { increases: [{ category: "Housing", delta_eur: 300, delta_pct: 11.1 }], decreases: [{ category: "Food", delta_eur: -100, delta_pct: null }] },
    merchants: [{ merchant: "EXAMPLE GROCER", this_eur: 900, previous_eur: 800 }],
    one_offs: [{ id: 1, date: "2027-02-03", description: "EXAMPLE REPAIR", amount_eur: -400, category: "Home", account: "Example Bank" }],
    warnings: ["Left out of the comparison: 2027-04."],
};

test("spendingYearModel: like-for-like rows, colours, the note and the full previous year", () => {
    const m = plain(w.spendingYearModel(yearData));
    assert.equal(m.compared, true);
    assert.match(m.note, /Compared through/);
    assert.match(m.note, /2026 is cut/);
    const spending = m.rows.find(r => r.label === "Spending");
    assert.equal(spending.deltaClass, "text-success");
    assert.match(spending.deltaText, /^−/);
    assert.equal(m.rows.find(r => r.label === "Income").deltaClass, "text-success");
    assert.match(m.fullPrevious, /Full 2026/);
    assert.match(m.oneOffText, /one-off/);
    assert.equal(m.categories[0].deltaClass, "text-danger");
    assert.equal(m.decreases[0].pctText, "—");
});

test("spendingYearModel: months without imports are gaps in the chart, not zero bars", () => {
    const m = plain(w.spendingYearModel(yearData));
    assert.equal(m.chart.thisYear[0], 2000);
    assert.equal(m.chart.thisYear[3], null);
    assert.equal(m.chart.hasData, true);
});

test("spendingYearModel: nothing comparable reads 'not available', never a zero", () => {
    const m = plain(w.spendingYearModel({ year: 2027, previous_year: 2026, complete: false, compared_through: null, totals: { this: null, previous: null, previous_full: null }, months: { this: monthsOf(2027, []), previous: monthsOf(2026, []) }, categories: [], movers: { increases: [], decreases: [] }, merchants: [], one_offs: [], warnings: [] }));
    assert.equal(m.compared, false);
    assert.match(m.note, /No month can be compared/);
    assert.match(m.rateText, /not available/);
    assert.match(m.fullPrevious, /not available/);
    assert.equal(m.chart.hasData, false);
    assert.equal(m.rows[1].thisText, "not available");
});

test("yearChoices: newest first", () => {
    assert.deepEqual(plain(w.yearChoices(2027, 3)), [2027, 2026, 2025]);
});
