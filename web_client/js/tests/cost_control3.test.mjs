// Pure helpers behind cost control 3: grouped Action Items, net-worth history,
// recurring resolutions, the irregular pace and the month close. Invented data.
// Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const w = loadAppIntoContext();
const plain = (v) => JSON.parse(JSON.stringify(v));

// ── Action Items: groups ────────────────────────────────────────────────────

const group = {
    id: "group:price_targets", kind: "group", family: "price_targets", count: 3,
    severity: "medium", category: "research", title: "3 price targets crossed", detail: "2 BUY, 1 SELL",
    link_page: "research", status: "open", priority: 215,
    children: [
        { id: "research:A", severity: "medium", title: "A" },
        { id: "research:B", severity: "medium", title: "B" },
        { id: "research:C", severity: "low", title: "C" },
    ],
};

test("isActionGroup and flattenActionGroups: a group becomes its children", () => {
    assert.equal(w.isActionGroup(group), true);
    assert.equal(w.isActionGroup({ id: "x", severity: "low" }), false);
    assert.equal(w.isActionGroup(null), false);
    const flat = plain(w.flattenActionGroups([group, { id: "single", status: "open" }]));
    assert.deepEqual(flat.map(i => i.id), ["research:A", "research:B", "research:C", "single"]);
    assert.ok(flat.every(i => i.status === "open"));
});

test("actionItemsSummaryText: a group counts as the items it folds", () => {
    const items = [group, { id: "s", status: "open" }, { id: "c", status: "checked" }];
    assert.equal(w.actionItemsSummaryText(items), "4 open · 1 checked");
    assert.equal(w.actionItemsSummaryText([]), "0 open");
});

test("mergeActionItems: keeps the server priority order, severity still dominates items without one", () => {
    const backend = [
        { id: "a", severity: "low", priority: 140, title: "A", category: "data_quality" },
        { id: "b", severity: "medium", priority: 210, title: "B", category: "budget" },
        { id: "g", severity: "medium", priority: 215, title: "G", category: "research", kind: "group", children: [] },
    ];
    const nw = { checklist: [{ key: "bank", label: "Bank", done: false, hint: "" }], attention: [] };
    const merged = plain(w.mergeActionItems(backend, nw, []));
    assert.deepEqual(merged.map(i => i.id), ["g", "b", "a", "networth:bank"]);
});

test("actionItemRank: priority first, else the severity base", () => {
    assert.equal(w.actionItemRank({ priority: 77, severity: "high" }), 77);
    assert.equal(w.actionItemRank({ severity: "high" }), 300);
    assert.equal(w.actionItemRank({ severity: "low" }), 100);
    assert.equal(w.actionItemRank({}), 0);
});

test("thisWeekModel: top entries as rows, groups carry their count, the rest is counted", () => {
    const report = { top: [group, { id: "x", title: "Fix X", detail: "d", severity: "high", link_page: "diagnostics" }], summary: { open_entries: 9 } };
    const m = plain(w.thisWeekModel(report));
    assert.equal(m.rows.length, 2);
    assert.equal(m.rows[0].count, 3);
    assert.equal(m.rows[0].page, "research");
    assert.equal(m.rows[1].count, null);
    assert.equal(m.more, 7);
    assert.deepEqual(plain(w.thisWeekModel(null)), { rows: [], more: 0 });
});

// ── Net-worth history ───────────────────────────────────────────────────────

const snap = (d, nw, extra = {}) => ({
    snapshot_date: d, net_worth_eur: nw, investments_eur: nw / 2, cash_eur: nw / 2,
    property_eur: 0, other_assets_eur: 0, liabilities_eur: 0, ...extra,
});

test("nwHistoryModel: no snapshot, one snapshot and a series each say what they are", () => {
    assert.match(w.nwHistoryModel({ snapshots: [] }, "total").note, /No snapshots yet/);
    assert.equal(w.nwHistoryModel({ snapshots: [] }, "total").hasChart, false);
    const one = w.nwHistoryModel({ snapshots: [snap("2026-11-01", 1000)] }, "total");
    assert.match(one.note, /History starts/);
    assert.equal(one.hasChart, false);
    const two = w.nwHistoryModel({ snapshots: [snap("2026-11-01", 1000), snap("2026-11-02", 1100)] }, "total");
    assert.equal(two.hasChart, true);
    assert.deepEqual(plain(two.series[0].data), [1000, 1100]);
});

test("nwHistoryModel: the components view draws liabilities below zero", () => {
    const m = w.nwHistoryModel({ snapshots: [snap("2026-11-01", 1000, { liabilities_eur: 300 }), snap("2026-11-02", 1100, { liabilities_eur: 250 })] }, "parts");
    const liab = m.series.find(s => s.key === "liabilities_eur");
    assert.deepEqual(plain(liab.data), [-300, -250]);
    assert.equal(m.series.length, 5);
});

test("nwChangeWindows: needs two snapshots; a window longer than the history is unavailable", () => {
    assert.deepEqual(plain(w.nwChangeWindows({ snapshots: [snap("2026-11-01", 1)] })), []);
    const history = { snapshots: [snap("2026-10-10", 1), snap("2026-11-10", 2)] };
    const wins = plain(w.nwChangeWindows(history));
    assert.deepEqual(wins.map(x => x.key), ["1m", "3m", "all"]);
    assert.equal(wins[0].start, "2026-10-11");
    assert.equal(wins[0].available, true);
    assert.equal(wins[1].available, false);
    assert.equal(wins[2].start, "2026-10-10");
    assert.ok(wins.every(x => x.end === "2026-11-10"));
});

test("nwChangeModel: null parts read not available, never zero", () => {
    const m = plain(w.nwChangeModel({
        start: "2026-10-10", end: "2026-11-10", net_worth_change_eur: 1200,
        parts: { saved_eur: null, market_eur: null, debt_paid_eur: 200, property_change_eur: 0, other_change_eur: -50 },
        warnings: ["flows unreadable"],
    }));
    assert.match(m.net, /\+/);
    const byLabel = Object.fromEntries(m.rows.map(r => [r.label, r]));
    assert.equal(byLabel["Saved"].text, "not available");
    assert.equal(byLabel["Saved"].missing, true);
    assert.equal(byLabel["Debt paid down"].cls, "text-success");
    assert.equal(byLabel["Other"].cls, "text-danger");
    assert.equal(byLabel["Property"].cls, "text-muted");
    assert.deepEqual(m.warnings, ["flows unreadable"]);
    assert.equal(plain(w.nwChangeModel(null)).net, "not available");
});

// ── Recurring ───────────────────────────────────────────────────────────────

test("recurringStatusBadges: cancelled, paid another way and came back", () => {
    assert.match(w.recurringStatusBadges({ status: "cancelled" }), />Cancelled</);
    assert.match(w.recurringStatusBadges({ status: "paid_elsewhere" }), />Paid another way</);
    const back = w.recurringStatusBadges({ status: "active", resolution_stale: true, resolution: { status: "cancelled" } });
    assert.match(back, />Came back</);
    assert.doesNotMatch(back, /Cancelled</);
});

test("recurringTotalsModel: the extra figures only when there is something to say", () => {
    const m = plain(w.recurringTotalsModel({ monthly_total_eur: 100, annual_total_eur: 1200, paid_elsewhere_annual_eur: 240, cancelled_annual_eur: 120 }));
    assert.ok(m.paidElsewhere && m.saved);
    const plainTotals = plain(w.recurringTotalsModel({ monthly_total_eur: 100, annual_total_eur: 1200, paid_elsewhere_annual_eur: 0, cancelled_annual_eur: 0 }));
    assert.equal(plainTotals.paidElsewhere, null);
    assert.equal(plainTotals.saved, null);
    assert.match(w.recurringTotalsHtml(m), /cancellations save/);
    assert.doesNotMatch(w.recurringTotalsHtml(plainTotals), /cancellations|another way/);
});

test("recurringMenuActions: Clear only when the series has a mark; the current state is not offered again", () => {
    assert.deepEqual(plain(w.recurringMenuActions({ status: "active" })).map(a => a.action), ["cancelled", "paid_elsewhere"]);
    assert.deepEqual(plain(w.recurringMenuActions({ status: "cancelled", resolution: { status: "cancelled" } })).map(a => a.action), ["paid_elsewhere", "clear"]);
    assert.deepEqual(plain(w.recurringMenuActions({ status: "active", resolution_stale: true, resolution: { status: "paid_elsewhere" } })).map(a => a.action), ["cancelled", "paid_elsewhere", "clear"]);
});

// ── Budget pace: irregular ──────────────────────────────────────────────────

test("paceMeta: an irregular line says in how many months it was spent", () => {
    const m = plain(w.paceMeta({ status: "irregular", projected_month_end_eur: 40, limit_eur: 100, actual_to_date_eur: 30, months_with_activity: 2, baseline_months: 12 }));
    assert.equal(m.label, "Irregular");
    assert.equal(m.months, "irregular: spent in 2 of 12 months");
    assert.equal(m.short, "irregular: spent in 2 of 12 months");
    assert.match(m.text, /irregular: spent in 2 of 12 months/);
    assert.match(plain(w.paceMeta({ status: "irregular", limit_eur: 100 })).months, /only a few months/);
});

// ── Month close ─────────────────────────────────────────────────────────────

test("monthCloseChecklistModel: an icon and text for every status, unknown reads not available", () => {
    const rows = plain(w.monthCloseChecklistModel([
        { key: "imports", label: "Imports", status: "ok", count: 0, detail: "all in", link_page: "spending" },
        { key: "uncategorized", label: "Uncategorised", status: "todo", count: 4, detail: "4 rows", link_page: "spending" },
        { key: "transfers", label: "Transfers", status: "warn", count: 1, detail: "1", link_page: null },
        { key: "anomalies", label: "Unusual", status: "unknown", count: null, detail: "failed", link_page: "spending" },
    ]));
    assert.deepEqual(rows.map(r => r.icon), ["bi-check-circle-fill", "bi-x-circle-fill", "bi-exclamation-triangle-fill", "bi-question-circle"]);
    assert.equal(rows[0].count, 0);
    assert.equal(rows[3].count, null);
    assert.equal(rows[3].word, "not available");
    assert.equal(rows[2].page, null);
    assert.deepEqual(plain(w.monthCloseChecklistModel(null)), []);
});

test("monthCloseFiguresModel: null figures are not available, a budget only when present", () => {
    const empty = plain(w.monthCloseFiguresModel({ income_eur: null, spending_eur: null, savings_rate_pct: null }));
    assert.ok(empty.tiles.every(t => t.text === "not available"));
    assert.equal(empty.budget, null);
    assert.equal(empty.versus, null);
    const full = plain(w.monthCloseFiguresModel({
        income_eur: 3000, spending_eur: 2000, savings_eur: 500, net_eur: 1000, savings_rate_pct: 33.3,
        top_categories: [{ name: "Groceries", actual_eur: 400, baseline_eur: 350, delta_eur: 50 }, { name: "Gifts", actual_eur: 100, baseline_eur: null, delta_eur: null }],
        versus_normal: { delta_eur: -100, baseline_eur: 2100 },
        budget: { name: "Plan", sections: [{ key: "spending", label: "Spending", planned_total: 2200, actual_total: 2000, variance_eur: 200 }] },
    }));
    assert.equal(full.tiles[4].text, "33.3%");
    assert.equal(full.tiles[3].cls, "text-success");
    assert.equal(full.topCategories[0].deltaClass, "text-danger");
    assert.equal(full.topCategories[1].delta, null);
    assert.equal(full.budget.sections.length, 1);
    assert.match(full.versus, /normal month/);
});

test("monthCloseStateModel: Close anyway only when not ready, Reopen only when closed, report link", () => {
    const open = plain(w.monthCloseStateModel({ ready: true, closed: null }));
    assert.equal(open.showForce, false);
    assert.equal(open.showReopen, false);
    assert.equal(open.closeLabel, "Close month");
    assert.equal(open.reportId, null);
    const notReady = plain(w.monthCloseStateModel({ ready: false, closed: null }));
    assert.equal(notReady.showForce, true);
    const closed = plain(w.monthCloseStateModel({ ready: true, closed: { closed_at: "2026-11-03T10:00:00", report_id: 12, stale: true, notes: "n" } }));
    assert.equal(closed.isClosed, true);
    assert.equal(closed.stale, true);
    assert.equal(closed.showReopen, true);
    assert.equal(closed.closeLabel, "Close again");
    assert.equal(closed.reportId, 12);
    assert.equal(closed.closedAt, "2026-11-03");
    assert.equal(closed.notes, "n");
});

test("monthCloseChoices: the last 12 complete months, newest first, never the month in progress", () => {
    const c = plain(w.monthCloseChoices("2026-11-18"));
    assert.equal(c.length, 12);
    assert.equal(c[0], "2026-10");
    assert.equal(c[11], "2025-11");
    assert.equal(plain(w.monthCloseChoices("2027-01-02"))[0], "2026-12");
});
