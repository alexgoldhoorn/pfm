// Pure helpers behind the year-end additions to the tax optimizer card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("taxDeadlineHtml: days left, urgent colour inside 30 days", () => {
    const soon = win.taxDeadlineHtml({ deadline: { date: "2026-12-31", days_left: 12 } });
    assert.match(soon, /12 days/);
    assert.match(soon, /text-danger/);
    const far = win.taxDeadlineHtml({ deadline: { date: "2026-12-31", days_left: 90 } });
    assert.ok(!far.includes("text-danger"));
    assert.match(win.taxDeadlineHtml({ deadline: { date: "2026-12-31", days_left: 1 } }), /1 day</);
});

test("taxDeadlineHtml: carry-forward only when non-zero, by year", () => {
    assert.ok(!win.taxDeadlineHtml({ carry_forward: { total_eur: 0, by_year: {} } }).includes("carry-forward"));
    const html = win.taxDeadlineHtml({ carry_forward: { total_eur: 500, by_year: { 2024: 200, 2025: 300 } }, base_after_carry_forward_eur: 1200 });
    assert.match(html, /carry-forward/);
    assert.match(html, /2024/);
    assert.match(html, /2025/);
    assert.match(html, /Taxable base after carry-forward/);
});

test("taxDeadlineHtml: empty object gives empty string", () => {
    assert.equal(win.taxDeadlineHtml({}), "");
    assert.equal(win.taxDeadlineHtml(null), "");
});

test("taxCandidateRowHtml: harvestable shows tax saved and until date", () => {
    const html = win.taxCandidateRowHtml({ symbol: "AAA", name: "Alpha", unrealised_loss_eur: -400, tax_saved_eur: 76, last_buy: "2025-01-02", can_harvest_until: "2026-12-31" }, {});
    assert.match(html, /harvestable/);
    assert.match(html, /until/);
    assert.match(html, /text-success/);
});

test("taxCandidateRowHtml: blocked symbol shows the 2-month marker with date", () => {
    const html = win.taxCandidateRowHtml({ symbol: "aaa", name: "<x>", unrealised_loss_eur: -400, tax_saved_eur: 76 }, { AAA: { symbol: "AAA", until: "2027-01-15" } });
    assert.match(html, /blocked by the 2-month rule until/);
    assert.ok(!html.includes("harvestable"));
    assert.ok(!html.includes("<x>"));
});
