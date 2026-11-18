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
