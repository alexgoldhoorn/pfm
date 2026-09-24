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
