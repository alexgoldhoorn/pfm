// Pure helpers behind the Decisions page.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("decisionStatusMeta maps open, due, reviewed", () => {
    assert.equal(win.decisionStatusMeta({ status: "open" }).label, "open");
    assert.match(win.decisionStatusMeta({ status: "due" }).badge, /warning/);
    assert.match(win.decisionStatusMeta({ status: "reviewed" }).badge, /success/);
    assert.equal(win.decisionStatusMeta({}).label, "open");
});

test("decisionActionMeta colours by action and falls back", () => {
    assert.match(win.decisionActionMeta("buy").badge, /success/);
    assert.match(win.decisionActionMeta("dca").badge, /success/);
    assert.match(win.decisionActionMeta("sell").badge, /danger/);
    assert.match(win.decisionActionMeta("hold").badge, /secondary/);
    assert.match(win.decisionActionMeta("traspaso").badge, /info/);
    assert.equal(win.decisionActionMeta("weird").label, "weird");
    assert.equal(win.decisionActionMeta("rebalance").label, "Rebalance");
});

test("decisionPriceMoveHtml: empty without price, signed with one", () => {
    assert.equal(win.decisionPriceMoveHtml({ price_at_decision: 10, price_now: null }), "");
    assert.equal(win.decisionPriceMoveHtml({ price_at_decision: null, price_now: 10 }), "");
    const up = win.decisionPriceMoveHtml({ price_at_decision: 10, price_now: 12, price_change_pct: 20, currency: "EUR" });
    assert.match(up, /text-success/);
    assert.match(up, /\+20\.0%/);
    assert.match(up, /→/);
    const down = win.decisionPriceMoveHtml({ price_at_decision: 10, price_now: 9, price_change_pct: -10, currency: "EUR" });
    assert.match(down, /text-danger/);
    assert.match(down, /-10\.0%/);
    const nopct = win.decisionPriceMoveHtml({ price_at_decision: 10, price_now: 9, price_change_pct: null });
    assert.ok(!nopct.includes("%"));
});
