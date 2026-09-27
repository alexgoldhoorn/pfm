// Pure helpers behind the Spending usability upgrades (search, rules,
// click-to-edit, balance check, recurring). Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

test("spDescriptionCellHtml shows merchant first and raw description muted", () => {
    const { spDescriptionCellHtml } = loadAppIntoContext();
    const html = spDescriptionCellHtml({
        description: "123456789012EXAMPLE SHOP 0100",
        merchant: "EXAMPLE SHOP",
    });
    assert.match(html, /^EXAMPLE SHOP<div class="small text-muted">123456789012EXAMPLE SHOP 0100<\/div>$/);
});

test("spDescriptionCellHtml shows only the description when merchant equals it", () => {
    const { spDescriptionCellHtml } = loadAppIntoContext();
    assert.equal(spDescriptionCellHtml({ description: "SHOP", merchant: "SHOP" }), "SHOP");
    assert.equal(spDescriptionCellHtml({ description: "SHOP", merchant: null }), "SHOP");
});

test("spDescriptionCellHtml escapes both lines", () => {
    const { spDescriptionCellHtml } = loadAppIntoContext();
    const html = spDescriptionCellHtml({ description: "<b>x</b> 1", merchant: "<b>x</b>" });
    assert.ok(!html.includes("<b>"));
});

test("ruleConditionSummary: no conditions reads 'Any'", () => {
    const { ruleConditionSummary } = loadAppIntoContext();
    assert.equal(ruleConditionSummary({}, {}), "Any");
});

test("ruleConditionSummary: account, sign and range", () => {
    const { ruleConditionSummary } = loadAppIntoContext();
    const s = ruleConditionSummary(
        { portfolio_id: 3, amount_sign: "negative", min_amount: 5, max_amount: 50 },
        { 3: "Example Bank" },
    );
    assert.equal(s, "Example Bank · money out · 5.00–50.00");
});

test("ruleConditionSummary: one-sided ranges and a deleted account", () => {
    const { ruleConditionSummary } = loadAppIntoContext();
    assert.equal(ruleConditionSummary({ min_amount: 5 }, {}), "≥ 5.00");
    assert.equal(ruleConditionSummary({ max_amount: 5, amount_sign: "positive" }, {}), "money in · ≤ 5.00");
    assert.equal(ruleConditionSummary({ portfolio_id: 9 }, {}), "Account #9 (deleted)");
});

test("spRulePayloadFromForm converts blanks to null and numbers to numbers", () => {
    const { spRulePayloadFromForm } = loadAppIntoContext();
    const p = spRulePayloadFromForm({
        pattern: "  SHOP ", category: " Groceries ", priority: "",
        portfolioId: "", sign: "", min: "", max: "",
    });
    assert.deepEqual({ ...p }, {
        pattern: "SHOP", category: "Groceries", priority: 100,
        portfolio_id: null, amount_sign: null, min_amount: null, max_amount: null,
    });
    const q = spRulePayloadFromForm({
        pattern: "X", category: "Y", priority: "5",
        portfolioId: "3", sign: "negative", min: "1.5", max: "20",
    });
    assert.deepEqual({ ...q }, {
        pattern: "X", category: "Y", priority: 5,
        portfolio_id: 3, amount_sign: "negative", min_amount: 1.5, max_amount: 20,
    });
});

test("spDeletedAccountOptionHtml labels a deleted account like ruleConditionSummary does", () => {
    const { spDeletedAccountOptionHtml } = loadAppIntoContext();
    assert.equal(spDeletedAccountOptionHtml(9), '<option value="9">Account #9 (deleted)</option>');
});

test("spDeletedAccountOptionHtml escapes its id", () => {
    const { spDeletedAccountOptionHtml } = loadAppIntoContext();
    // The id always comes from a rule's own portfolio_id/a <select> value, never
    // free text, but the template still runs it through esc() like every other
    // dynamic string reaching innerHTML — pin that rather than trust the source.
    const html = spDeletedAccountOptionHtml('"><script>1</script>');
    assert.ok(!html.includes("<script>"));
});

test("spRuleOfferText: nothing to offer at zero", () => {
    const { spRuleOfferText } = loadAppIntoContext();
    assert.equal(spRuleOfferText("EXAMPLE SHOP", "Groceries", 0), "");
});

test("spRuleOfferText: singular and plural", () => {
    const { spRuleOfferText } = loadAppIntoContext();
    assert.equal(
        spRuleOfferText("EXAMPLE SHOP", "Groceries", 1),
        'Also file 1 other uncategorized "EXAMPLE SHOP" row as Groceries?',
    );
    assert.equal(
        spRuleOfferText("EXAMPLE SHOP", "Groceries", 14),
        'Also file 14 other uncategorized "EXAMPLE SHOP" rows as Groceries?',
    );
});
