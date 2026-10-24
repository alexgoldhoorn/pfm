// Pure helpers behind the Goals page, its dashboard card and "Ask AI".
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const goal = (over) => ({
    id: 7, name: "Example Fund", notes: null,
    target_amount_eur: 12000, target_date: "2027-10-24",
    monthly_contribution_eur: 500, expected_return_pct: 0,
    current_networth_eur: 7000, progress_pct: 58.3, months_left: 12,
    projected_value_eur: 13000, on_track: true, shortfall_eur: 0,
    required_monthly_eur: 416.67, status: "on_track",
    start_date: "2026-04-24", start_networth_eur: 4000, months_elapsed: 6,
    gained_since_start_eur: 3000, gap_closed_pct: 37.5,
    actual_monthly_eur: 500, pace: "ahead", ...over,
});

test("goalStatusMeta maps every status and falls back to on_track", () => {
    assert.equal(win.goalStatusMeta(goal()).label, "On track");
    assert.equal(win.goalStatusMeta(goal({ status: "achieved" })).icon, "bi-trophy");
    assert.equal(win.goalStatusMeta(goal({ status: "overdue" })).badge, "bg-danger");
    assert.equal(win.goalStatusMeta(goal({ status: "off_track" })).bar, "bg-warning");
    // an older row without status: derived from on_track
    assert.equal(win.goalStatusMeta({ on_track: false }).key, "off_track");
    assert.equal(win.goalStatusMeta({ on_track: true }).key, "on_track");
    assert.equal(win.goalStatusMeta({ status: "weird" }).label, "Off track");
});

test("formatMonthsLeft reads as years and months", () => {
    assert.equal(win.formatMonthsLeft(0), "due");
    assert.equal(win.formatMonthsLeft(7), "7 mo");
    assert.equal(win.formatMonthsLeft(12), "1 y");
    assert.equal(win.formatMonthsLeft(14), "1 y 2 mo");
    assert.equal(win.formatMonthsLeft(null), "—");
});

test("goalPaceModel: ahead, behind, too early and no baseline", () => {
    const ahead = win.goalPaceModel(goal());
    assert.equal(ahead.level, "success");
    assert.equal(ahead.actualMonthly, 500);
    assert.equal(ahead.requiredMonthly, 416.67);
    assert.equal(ahead.gapClosedPct, 37.5);
    assert.equal(ahead.tooEarly, false);

    const behind = win.goalPaceModel(goal({ pace: "behind", actual_monthly_eur: 50 }));
    assert.equal(behind.level, "danger");

    const young = win.goalPaceModel(goal({ actual_monthly_eur: null, pace: null, months_elapsed: 0.1 }));
    assert.equal(young.tooEarly, true);
    assert.equal(young.actualMonthly, null);
    assert.equal(young.level, "muted");

    assert.equal(win.goalPaceModel(goal({ gained_since_start_eur: null })), null);
});

test("goalsSummaryChips counts statuses and flags over-committed cash flow", () => {
    assert.equal(win.goalsSummaryChips({ total: 0 }, 1000).length, 0);
    const summary = { total: 3, achieved: 1, on_track: 1, off_track: 1, overdue: 0,
        planned_monthly_eur: 900, next_target_date: "2028-01-01" };
    const texts = win.goalsSummaryChips(summary, 1200).map((c) => c.text);
    assert.equal(texts[0], "3 goals");
    assert.ok(texts.includes("1 achieved"));
    assert.ok(texts.includes("1 on track"));
    assert.ok(texts.includes("1 off track"));
    assert.ok(!texts.some((t) => t.includes("overdue")));
    const planned = win.goalsSummaryChips(summary, 1200).find((c) => c.text.includes("planned"));
    assert.ok(planned.text.includes("net cash flow"));
    assert.equal(planned.cls, "text-bg-light border");
    // planned contributions above the cash flow turn the chip amber
    const over = win.goalsSummaryChips(summary, 600).find((c) => c.text.includes("planned"));
    assert.equal(over.cls, "text-bg-warning");
    // unknown cash flow: no comparison, no warning
    const noCf = win.goalsSummaryChips(summary, null).find((c) => c.text.includes("planned"));
    assert.ok(!noCf.text.includes("net cash flow"));
    assert.ok(texts.some((t) => t.startsWith("next due")));
});

test("goalChatMessage carries the goal's numbers, pace and notes", () => {
    const msg = win.goalChatMessage(goal({ notes: "Example house deposit" }));
    assert.ok(msg.includes('savings goal "Example Fund"'));
    assert.ok(msg.includes("by 2027-10-24 (1 y left)"));
    assert.ok(msg.includes("58.3% of target"));
    assert.ok(msg.includes("(on track)"));
    assert.ok(msg.includes("Needed from now:"));
    assert.ok(msg.includes("Since set on 2026-04-24: +"));
    assert.ok(msg.includes("/month actual)"));
    assert.ok(msg.includes("Notes: Example house deposit"));
    assert.ok(msg.includes("get_goals"));
    // no baseline, no notes: those lines are simply absent
    const bare = win.goalChatMessage(goal({ gained_since_start_eur: null, required_monthly_eur: null }));
    assert.ok(!bare.includes("Since set"));
    assert.ok(!bare.includes("Needed from now"));
    assert.ok(!bare.includes("Notes:"));
});
