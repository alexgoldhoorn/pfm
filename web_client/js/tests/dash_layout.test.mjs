// Pure helpers behind the customizable dashboard and its two new cards.
// Invented data. Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const w = loadAppIntoContext();
const plain = (v) => JSON.parse(JSON.stringify(v));
const DEF = ["attention", "progress", "risk", "investments", "cash", "activity"];

test("dashLayoutResolve: nothing saved is the default order, nothing hidden", () => {
    assert.deepEqual(plain(w.dashLayoutResolve(DEF, null)), { order: DEF, hidden: [] });
    assert.deepEqual(plain(w.dashLayoutResolve(DEF, { order: "x", hidden: 3 })), { order: DEF, hidden: [] });
});

test("dashLayoutResolve: keeps the saved order, drops unknown and repeated ids", () => {
    const saved = { order: ["activity", "gone", "cash", "activity", "attention", "progress", "risk", "investments"], hidden: ["risk", "gone", "risk"] };
    assert.deepEqual(plain(w.dashLayoutResolve(DEF, saved)), {
        order: ["activity", "cash", "attention", "progress", "risk", "investments"],
        hidden: ["risk"],
    });
});

test("dashLayoutResolve: a block the saved layout does not know follows its default predecessor", () => {
    // 'risk' and 'attention' are new since the layout was saved
    const saved = { order: ["cash", "progress", "activity", "investments"], hidden: [] };
    assert.deepEqual(plain(w.dashLayoutResolve(DEF, saved).order),
        ["attention", "cash", "progress", "risk", "activity", "investments"]);
});

test("dashLayoutMove: one place up or down, the ends stay put", () => {
    assert.deepEqual(plain(w.dashLayoutMove(["a", "b", "c"], "b", -1)), ["b", "a", "c"]);
    assert.deepEqual(plain(w.dashLayoutMove(["a", "b", "c"], "b", 1)), ["a", "c", "b"]);
    assert.deepEqual(plain(w.dashLayoutMove(["a", "b", "c"], "a", -1)), ["a", "b", "c"]);
    assert.deepEqual(plain(w.dashLayoutMove(["a", "b", "c"], "c", 1)), ["a", "b", "c"]);
    assert.deepEqual(plain(w.dashLayoutMove(["a", "b", "c"], "zz", 1)), ["a", "b", "c"]);
});

test("dashNwWindow: last month when the history is long enough, else since the first snapshot", () => {
    const snap = (d) => ({ snapshot_date: d, net_worth_eur: 1000 });
    assert.equal(w.dashNwWindow({ snapshots: [snap("2030-01-01")] }), null);
    assert.equal(w.dashNwWindow({ snapshots: [snap("2030-01-01"), snap("2030-01-10")] }).key, "all");
    const long = w.dashNwWindow({ snapshots: [snap("2030-01-01"), snap("2030-03-10")] });
    assert.equal(long.key, "1m");
    assert.equal(long.end, "2030-03-10");
});

test("dashCashflowModel: tiles, the low point and only bills still ahead", () => {
    const fc = {
        data_through: "2030-05-10",
        accounts: [{ name: "Example Bank", stale: false }, { name: "Old Bank", stale: true }],
        balance: { start_eur: 5000 },
        this_month: { month: "2030-05", projected_balance_end_eur: 4200 },
        next_months: [
            { month: "2030-06", projected_balance_eur: 3900 },
            { month: "2030-07", projected_balance_eur: 3100 },
            { month: "2030-08", projected_balance_eur: null },
        ],
        known_bills: [
            { date: "2030-05-20", merchant: "Example Energy", amount_eur: 80, kind: "spending" },
            { date: "2030-05-02", merchant: "Already Paid", amount_eur: 10, kind: "spending" },
            { date: "2030-05-12", merchant: "Example Gym", amount_eur: 30, kind: "spending" },
            { date: "2030-06-01", merchant: "Example Fund", amount_eur: 200, kind: "savings" },
        ],
        first_below_zero: null,
    };
    const m = plain(w.dashCashflowModel(fc, 2));
    assert.equal(m.hasData, true);
    assert.equal(m.balanceNow, 5000);
    assert.equal(m.monthEnd, 4200);
    assert.deepEqual([m.low, m.lowMonth, m.monthsAhead], [3100, "2030-07", 3]);
    assert.deepEqual(m.bills.map(b => b.merchant), ["Example Gym", "Example Energy"]);
    assert.equal(m.moreBills, 1);
    assert.deepEqual(m.staleAccounts, ["Old Bank"]);
});

test("dashCashflowModel: an empty forecast has no data and nulls, never zeros", () => {
    const m = plain(w.dashCashflowModel({ data_through: null, this_month: {}, next_months: [], known_bills: [] }));
    assert.equal(m.hasData, false);
    assert.deepEqual([m.balanceNow, m.monthEnd, m.low], [null, null, null]);
    assert.deepEqual(plain(w.dashCashflowModel(null)).bills, []);
});

// ── The second round of cards ───────────────────────────────────────────────

test("dashUpcomingModel: one dated list from bills, planned items, tax and reviews", () => {
    const fc = {
        data_through: "2030-05-06",
        known_bills: [
            { date: "2030-05-07", merchant: "Example Gym", amount_eur: 30, kind: "spending" },
            { date: "2030-05-02", merchant: "Before Import", amount_eur: 10, kind: "spending" },
            { date: "2030-05-20", merchant: "Example Fund", amount_eur: 200, kind: "savings" },
            { date: "2030-07-20", merchant: "Too Far", amount_eur: 99, kind: "spending" },
        ],
        planned_items: [
            { date: "2030-05-15", description: "Example trip", amount_eur: -400, kind: "spending", status: "projected" },
            { date: "2030-05-25", description: "Example refund", amount_eur: 120, kind: "income", status: "projected" },
            { date: "2030-05-01", description: "Late thing", amount_eur: -50, kind: "spending", status: "overdue" },
            { date: "2030-05-12", description: "Finished", amount_eur: -1, kind: "spending", status: "done" },
        ],
        tax_payments: [{ quarter: "2030-Q1", due: "2030-05-18", amount_eur: 300 }],
    };
    const decisions = { items: [
        { title: "Hold Example Corp", review_on: "2030-05-09" },
        { title: "Already reviewed", review_on: "2030-05-09", reviewed_at: "2030-05-09T10:00:00" },
        { title: "Next year", review_on: "2031-01-01" },
    ] };
    const m = plain(w.dashUpcomingModel(fc, decisions, "2030-05-10", 30, 20));
    assert.deepEqual(m.items.map(e => [e.date, e.type, e.amount, e.overdue]), [
        ["2030-05-01", "planned", -50, true],
        ["2030-05-07", "bill", -30, true],
        ["2030-05-09", "review", null, true],
        ["2030-05-15", "planned", -400, false],
        ["2030-05-18", "tax", -300, false],
        ["2030-05-20", "bill", -200, false],
        ["2030-05-25", "planned", 120, false],
    ]);
    assert.equal(m.outEur, 980);
    assert.equal(m.to, "2030-06-09");
    const capped = plain(w.dashUpcomingModel(fc, decisions, "2030-05-10", 30, 3));
    assert.deepEqual([capped.items.length, capped.more, capped.count], [3, 4, 7]);
});

test("dashUpcomingModel: a failed source leaves the other one listed", () => {
    const m = plain(w.dashUpcomingModel(null, { items: [{ title: "T", review_on: "2030-05-11" }] }, "2030-05-10"));
    assert.deepEqual(m.items.map(e => e.label), ["Review: T"]);
    assert.equal(plain(w.dashUpcomingModel(null, null, "2030-05-10")).count, 0);
});

test("dashMonthCloseModel: state, open checks and figures", () => {
    const mc = {
        month: "2030-04", closed: null, ready: false,
        checklist: [
            { key: "imports", label: "Imports", status: "ok", detail: "", link_page: "importexport" },
            { key: "uncategorized", label: "Uncategorised", status: "warn", detail: "3 rows", link_page: "spending" },
        ],
        figures: { income_eur: 3000, spending_eur: 2000, savings_eur: 500, net_eur: 1000 },
    };
    const m = plain(w.dashMonthCloseModel(mc));
    assert.deepEqual([m.state, m.total, m.openCount], ["open", 2, 1]);
    assert.deepEqual(m.open, [{ label: "Uncategorised", detail: "3 rows", page: "spending" }]);
    assert.deepEqual(m.figures.map(f => f.value), [3000, 2000, 500, 1000]);
    assert.equal(w.dashMonthCloseModel({ ...mc, ready: true }).state, "ready");
    assert.equal(w.dashMonthCloseModel({ ...mc, closed: { stale: false } }).state, "closed");
    assert.equal(w.dashMonthCloseModel({ ...mc, closed: { stale: true } }).state, "closed_stale");
    assert.deepEqual(plain(w.dashMonthCloseModel(null)).figures.map(f => f.value), [null, null, null, null]);
});

test("dashLoansModel: share repaid from the original amount, null when unknown", () => {
    const m = plain(w.dashLoansModel([
        { name: "Example mortgage", principal: 200000, balance_eur: 150000, monthly_payment_eur: 800, end_date: "2045-01-01", currency: "EUR" },
        { name: "No principal", principal: 0, balance_eur: 5000, monthly_payment_eur: 100 },
    ]));
    assert.deepEqual(m.rows.map(r => r.repaidPct), [25, null]);
    assert.equal(m.totalBalance, 155000);
    assert.deepEqual(plain(w.dashLoansModel(null)), { rows: [], totalBalance: null });
});

test("dashSubsModel: live series by yearly cost, missed and returned charges", () => {
    const rec = {
        monthly_total_eur: 50, annual_total_eur: 600,
        items: [
            { merchant: "Small", cadence: "monthly", annual_amount_eur: 60, status: "active", resolution: null, resolution_stale: false },
            { merchant: "Big", cadence: "yearly", annual_amount_eur: 300, status: "missed", resolution: null, resolution_stale: false },
            { merchant: "Cancelled", cadence: "monthly", annual_amount_eur: 999, status: "active", resolution: { status: "cancelled" }, resolution_stale: false },
            { merchant: "Came Back", cadence: "monthly", annual_amount_eur: 120, status: "active", resolution: { status: "cancelled" }, resolution_stale: true },
        ],
    };
    const m = plain(w.dashSubsModel(rec, 2));
    assert.equal(m.count, 3);
    assert.deepEqual(m.top.map(t => t.merchant), ["Big", "Came Back"]);
    assert.equal(m.more, 1);
    assert.deepEqual(m.missed, ["Big"]);
    assert.deepEqual(m.returned, ["Came Back"]);
    assert.equal(plain(w.dashSubsModel(null)).count, 0);
});

test("dashMoversModel: empty when nothing moved", () => {
    const m = plain(w.dashMoversModel({ up: [{ symbol: "AAA", name: "Example Corp", change_pct: 2.5, change_eur: 40, price_date: "2030-05-09" }], down: [], as_of: "2030-05-09", counted: 4, skipped: 1 }));
    assert.equal(m.empty, false);
    assert.deepEqual(m.up[0], { symbol: "AAA", name: "Example Corp", pct: 2.5, eur: 40, priceDate: "2030-05-09" });
    assert.equal(plain(w.dashMoversModel(null)).empty, true);
});
