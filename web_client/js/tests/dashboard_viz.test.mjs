// Pure helpers behind the dashboard charts (donut folding, hover lookup,
// axis ticks, history range filter).
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("donutSlices sorts descending and keeps small sets intact", () => {
    const { slices, nonPositive } = win.donutSlices([["a", 1], ["b", 3], ["c", 2]], 6);
    assert.deepEqual(slices.map((s) => s[0]), ["b", "c", "a"]);
    assert.equal(nonPositive.length, 0);
});

test("donutSlices folds the tail into one other slice", () => {
    const entries = [["a", 10], ["b", 9], ["c", 8], ["d", 1], ["e", 1]];
    const { slices } = win.donutSlices(entries, 3);
    assert.equal(slices.length, 3);
    assert.equal(slices[2][0], "__other__");
    assert.equal(slices[2][1], 10);
    assert.deepEqual([...slices[2][2]], ["c", "d", "e"]);
});

test("donutSlices separates zero and negative values", () => {
    const { slices, nonPositive } = win.donutSlices([["a", 5], ["overdrawn", -20], ["empty", 0]], 6);
    assert.deepEqual(slices.map((s) => s[0]), ["a"]);
    assert.deepEqual(nonPositive.map((s) => s[0]).sort(), ["empty", "overdrawn"]);
});

test("nearestIndex picks the closest x", () => {
    const xs = [0, 10, 20, 30];
    assert.equal(win.nearestIndex(xs, -5), 0);
    assert.equal(win.nearestIndex(xs, 14), 1);
    assert.equal(win.nearestIndex(xs, 16), 2);
    assert.equal(win.nearestIndex(xs, 99), 3);
    assert.equal(win.nearestIndex([], 1), -1);
});

test("niceTicks lands on round steps that cover the range", () => {
    const nt = win.niceTicks(151234, 196789, 4);
    assert.ok(nt.lo <= 151234 && nt.hi >= 196789);
    assert.equal(nt.step, 20000);
    assert.deepEqual([...nt.ticks], [140000, 160000, 180000, 200000]);
});

test("niceTicks survives a flat series", () => {
    const nt = win.niceTicks(1000, 1000, 4);
    assert.ok(nt.hi > nt.lo);
    assert.ok(nt.ticks.length >= 2);
});

test("filterSnapshotsByRange keeps only the selected window", () => {
    const snaps = ["2025-01-10", "2026-03-01", "2026-07-01", "2026-09-20"].map((d) => ({ snapshot_date: d }));
    const today = "2026-09-24T12:00:00";
    assert.equal(win.filterSnapshotsByRange(snaps, "all", today).length, 4);
    assert.deepEqual(
        win.filterSnapshotsByRange(snaps, "ytd", today).map((s) => s.snapshot_date),
        ["2026-03-01", "2026-07-01", "2026-09-20"],
    );
    assert.deepEqual(
        win.filterSnapshotsByRange(snaps, "3m", today).map((s) => s.snapshot_date),
        ["2026-07-01", "2026-09-20"],
    );
});

test("filterSnapshotsByRange falls back to the last two points", () => {
    const snaps = ["2025-01-10", "2025-02-10"].map((d) => ({ snapshot_date: d }));
    const out = win.filterSnapshotsByRange(snaps, "3m", "2026-09-24T12:00:00");
    assert.equal(out.length, 2);
});

test("vizTypeColor is stable per asset type", () => {
    assert.equal(win.vizTypeColor("etf"), "var(--viz-1)");
    assert.equal(win.vizTypeColor("cash"), "var(--viz-neutral)");
    assert.equal(win.vizTypeColor("something-new"), "var(--viz-other)");
    assert.equal(win.vizTypeLabel("mutual_fund"), "Mutual fund");
});

test("notifyLevel classifies messages", () => {
    assert.equal(win.notifyLevel("Error saving: boom"), "danger");
    assert.equal(win.notifyLevel("Backfill failed: timeout"), "danger");
    assert.equal(win.notifyLevel("Goal not found"), "danger");
    assert.equal(win.notifyLevel("Asset created successfully!"), "success");
    assert.equal(win.notifyLevel("Please select a broker."), "warning");
    assert.equal(win.notifyLevel("Pattern and category cannot be empty."), "warning");
    assert.equal(win.notifyLevel("Asset, type, date and a positive quantity are required."), "warning");
    assert.equal(win.notifyLevel("No data selected."), "warning");
    assert.equal(win.notifyLevel("Open the Tax tab first."), "warning");
    assert.equal(win.notifyLevel("Imported 12 rows"), "success");
});

test("fmtEurTick never prints two equal adjacent labels", () => {
    assert.equal(win.fmtEurTick(152500, 2500), "€152.5k");
    assert.equal(win.fmtEurTick(160000, 20000), "€160k");
    assert.equal(win.fmtEurTick(1250000, 250000), "€1.25M");
    assert.equal(win.fmtEurTick(2000000, 500000), "€2.0M");
});

test("importResultModel summarises a clean import", () => {
    const m = win.importResultModel({
        saved: 3, saved_bookings: 2, saved_deposits: 0, duplicates_skipped: 1, overwritten: 0,
        errors: ["DUPLICATE: EX buy 1@10 on 2024-01-01 (existing id=4)"],
        by_type: { buy: 2, dividend: 1 }, date_from: "2024-01-01", date_to: "2024-03-01",
        new_assets: ["Example Corp (EX)"], portfolios: ["Example Broker"],
        booking_totals: { Deposit: { EUR: 750 } },
    });
    assert.equal(m.title, "Import complete");
    assert.equal(m.level, "success");
    const chips = Object.fromEntries(m.chips.map((c) => [c.label, c.value]));
    assert.equal(chips.transactions, 3);
    assert.equal(chips["cash movements"], 2);
    assert.equal(chips["duplicates skipped"], 1);
    assert.equal(chips.errors, undefined);
    const facts = Object.fromEntries(m.facts.map((f) => [f.label, f.value]));
    assert.equal(facts.Breakdown, "2 buys · 1 dividend");
    assert.equal(facts.Account, "Example Broker");
    assert.ok(facts.Deposited.includes("750"));
    const dup = m.lists.find((l) => l.title === "Skipped as duplicates");
    assert.deepEqual([...dup.items], ["EX buy 1@10 on 2024-01-01 (existing id=4)"]);
    assert.ok(m.note.includes("price refresh"));
});

test("importResultModel flags errors and all-duplicate imports", () => {
    const bad = win.importResultModel({ saved: 0, errors: ["EX (2024-01-01): date is required"] });
    assert.equal(bad.level, "danger");
    assert.equal(bad.title, "Nothing imported");
    const partial = win.importResultModel({ saved: 2, errors: ["boom"] });
    assert.equal(partial.level, "warning");
    const dupsOnly = win.importResultModel({ saved: 0, duplicates_skipped: 5, errors: [] });
    assert.equal(dupsOnly.title, "Nothing new to import");
    assert.ok(dupsOnly.note.includes("already imported"));
});

test("spendingImportResultModel reports balance and work left", () => {
    const m = win.spendingImportResultModel({
        saved: 40, duplicates_skipped: 2, overwritten: 0, transfers_linked: 3, errors: [],
        account_name: "Example Bank", date_from: "2026-01-01", date_to: "2026-01-31",
        money_in: { EUR: 2000 }, money_out: { EUR: 1500.5 }, uncategorized: 7,
        latest_balance: 3200, latest_balance_currency: "EUR", latest_balance_date: "2026-01-31",
    });
    assert.equal(m.title, "Bank statement imported");
    const chips = Object.fromEntries(m.chips.map((c) => [c.label, c.value]));
    assert.equal(chips.rows, 40);
    assert.equal(chips["to categorise"], 7);
    assert.equal(chips["transfers linked"], 3);
    const facts = Object.fromEntries(m.facts.map((f) => [f.label, f.value]));
    assert.equal(facts.Account, "Example Bank");
    assert.ok(facts.Balance.includes("3,200") || facts.Balance.includes("3.200"));
    assert.ok(m.note.includes("7 row(s)"));
});

test("importResultText is readable in chat", () => {
    const t = win.importResultText({ saved: 1, errors: [], by_type: { buy: 1 } });
    assert.ok(t.startsWith("Import complete: 1 transaction."));
    assert.ok(t.includes("Breakdown: 1 buy"));
});

test("projectionAxisMax caps a wide band at 2.5x the expected path", () => {
    const data = [0, 10, 20].map((y) => ({ year: y, netWorth: 100 + y * 10, netWorthHigh: (100 + y * 10) * (1 + y / 2) }));
    const wide = win.projectionAxisMax(data, 2.5);
    assert.equal(wide.clipped, true);
    assert.equal(wide.max, 300 * 2.5);
    const narrow = data.map((p) => ({ ...p, netWorthHigh: p.netWorth * 1.5 }));
    const ok = win.projectionAxisMax(narrow, 2.5);
    assert.equal(ok.clipped, false);
    assert.equal(ok.max, 450);
});

test("Fmt.money formats known currencies and falls back safely", () => {
    win.PREFS.numberLocale = "en-US";
    assert.equal(win.Fmt.money(1234.5, "EUR", 2), "€1,234.50");
    assert.equal(win.Fmt.money(1234.5, "usd", 0), "$1,235");
    assert.equal(win.Fmt.money(null, "EUR"), "—");
    // Non-ISO / pence / crypto codes keep the code, escaped for innerHTML.
    assert.equal(win.Fmt.money(10, "GBX", 2), "10.00 GBX");
    assert.equal(win.Fmt.money(1, "BTC-EUR", 2), "1.00 BTC-EUR");
    assert.equal(win.Fmt.money(1, "<b>", 2), "1.00 &lt;B&gt;");
    win.PREFS.numberLocale = "es-ES";
    assert.ok(win.Fmt.money(1234.5, "EUR", 2).includes("€"));
    delete win.PREFS.numberLocale;
});
