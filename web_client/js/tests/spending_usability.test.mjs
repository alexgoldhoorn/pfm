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

test("spCategoryCellInnerHtml: plain category, no Transfer badge", () => {
    const { spCategoryCellInnerHtml } = loadAppIntoContext();
    assert.equal(
        spCategoryCellInnerHtml({ category: "Groceries", is_transfer: false }),
        '<span class="sp-cat-text border-bottom border-secondary-subtle">Groceries</span>',
    );
});

test("spCategoryCellInnerHtml: escapes the category and appends the Transfer badge", () => {
    const { spCategoryCellInnerHtml } = loadAppIntoContext();
    assert.equal(
        spCategoryCellInnerHtml({ category: "<b>x</b>", is_transfer: true }),
        '<span class="sp-cat-text border-bottom border-secondary-subtle">&lt;b&gt;x&lt;/b&gt;</span> <span class="badge bg-info ms-1">Transfer</span>',
    );
});

test("spBalanceWarningHtml: empty for no breaks", () => {
    const { spBalanceWarningHtml } = loadAppIntoContext();
    assert.equal(spBalanceWarningHtml([]), "");
    assert.equal(spBalanceWarningHtml(undefined), "");
});

test("spBalanceWarningHtml: names the gap kinds and escapes descriptions", () => {
    const { spBalanceWarningHtml } = loadAppIntoContext();
    const html = spBalanceWarningHtml([
        { date: "2026-02-01", description: "<b>A</b>", currency: "EUR", expected: 110, actual: 90, kind: "gap_before_file" },
        { date: "2026-02-03", description: "B", currency: "EUR", expected: 85, actual: 80, kind: "within_file" },
    ]);
    assert.match(html, /alert-warning/);
    assert.match(html, /since your last import/);
    assert.match(html, /missing from this file/);
    assert.ok(!html.includes("<b>A</b>"));
});

test("recurringCadenceLabel", () => {
    const { recurringCadenceLabel } = loadAppIntoContext();
    assert.equal(recurringCadenceLabel("weekly"), "Week");
    assert.equal(recurringCadenceLabel("monthly"), "Month");
    assert.equal(recurringCadenceLabel("quarterly"), "Quarter");
    assert.equal(recurringCadenceLabel("yearly"), "Year");
});

test("recurringStatusBadges: missed, ended, price up and down", () => {
    const { recurringStatusBadges } = loadAppIntoContext();
    assert.equal(recurringStatusBadges({ status: "active", price_change_pct: null }), "");
    assert.match(recurringStatusBadges({ status: "missed" }), /bg-danger[^>]*>Missed</);
    assert.match(recurringStatusBadges({ status: "ended" }), /bg-secondary[^>]*>Ended</);
    assert.match(recurringStatusBadges({ status: "active", price_change_pct: 20 }), /▲ \+20%/);
    assert.match(recurringStatusBadges({ status: "active", price_change_pct: -12.5 }), /▼ -12.5%/);
});

test("_wireSpRecurringTab: clicking a merchant scopes to its account and clears other filters", async () => {
    const sandbox = loadAppIntoContext();
    const classList = () => ({ add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false });
    const stub = (extra = {}) => ({
        value: "", classList: classList(), addEventListener: () => {}, dataset: {}, ...extra,
    });

    const removed = [];
    const elements = {
        spTabBtnRecurring: stub(),
        spRecurringShowEnded: stub(),
        spAccountFilter: stub({ value: "" }),
        spFromDate: stub({ value: "2026-01-01" }),
        spToDate: stub({ value: "2026-01-31" }),
        spMinAbsAmount: stub({ value: "50" }),
        spAmountSignNegative: stub({ classList: { ...classList(), remove: (c) => removed.push(["neg", c]) } }),
        spAmountSignPositive: stub({ classList: { ...classList(), remove: (c) => removed.push(["pos", c]) } }),
        spSearch: stub(),
        spTabBtnTransactions: stub(),
        spCategoryFilterBtn: stub(),
    };
    let bodyClickHandler = null;
    elements.spRecurringBody = {
        dataset: {},
        addEventListener: (evt, fn) => { if (evt === "click") bodyClickHandler = fn; },
    };
    sandbox.document.getElementById = (id) => elements[id] ?? null;
    sandbox.bootstrap = { Tab: { getOrCreateInstance: () => ({ show: () => {} }) } };
    sandbox.window._spTxState = { page: 3, pageSize: 50, sortBy: "date", sortDir: "desc" };
    sandbox.window._spAmountSign = "negative";
    sandbox.window._spCategoryFilterSelected = new Set(["Groceries"]);
    let fetchCalled = false;
    sandbox._fetchAndRenderSpendingTable = async () => { fetchCalled = true; };

    sandbox._wireSpRecurringTab();
    assert.ok(bodyClickHandler, "click handler was registered");

    const link = { dataset: { merchant: "EXAMPLE SHOP", portfolioId: "7" } };
    link.closest = () => link;
    await bodyClickHandler({ target: link, preventDefault: () => {} });

    assert.equal(elements.spSearch.value, "EXAMPLE SHOP");
    assert.equal(elements.spAccountFilter.value, "7");
    assert.equal(elements.spFromDate.value, "");
    assert.equal(elements.spToDate.value, "");
    assert.equal(elements.spMinAbsAmount.value, "");
    assert.equal(sandbox.window._spAmountSign, null);
    assert.deepEqual(removed, [["neg", "active"], ["pos", "active"]]);
    assert.equal(sandbox.window._spCategoryFilterSelected, null);
    assert.equal(elements.spCategoryFilterBtn.textContent, "All categories");
    assert.equal(sandbox.window._spTxState.page, 0);
    assert.ok(fetchCalled);
});

test("apiErrorDetail reads a string detail", () => {
    const { apiErrorDetail } = loadAppIntoContext();
    assert.equal(apiErrorDetail({ detail: "Pattern cannot be empty" }, "x"), "Pattern cannot be empty");
});

test("apiErrorDetail joins a FastAPI 422 validation list", () => {
    const { apiErrorDetail } = loadAppIntoContext();
    const body = {
        detail: [
            { loc: ["body", "min_amount"], msg: "Input should be a valid number" },
            { loc: ["body", "amount_sign"], msg: "Input should be 'positive' or 'negative'" },
        ],
    };
    assert.equal(
        apiErrorDetail(body, "Failed to create rule"),
        "Input should be a valid number; Input should be 'positive' or 'negative'",
    );
});

test("_scheduleSpRulePreview ignores a stale out-of-order response", async () => {
    const sandbox = loadAppIntoContext();
    const preview = { textContent: "", innerHTML: "" };
    const patternInput = { value: "SHOP" };
    sandbox.document.getElementById = (id) => {
        if (id === "spRulePreview") return preview;
        if (id === "spRulePattern") return patternInput;
        return null;
    };
    // Fire the debounce timer synchronously so the test doesn't need to
    // wait out the real 400ms — each _scheduleSpRulePreview() call still
    // starts its own independent request, which is what can race.
    sandbox.setTimeout = (fn) => fn();
    sandbox.clearTimeout = () => {};

    const resolvers = [];
    sandbox.window.apiClient = {
        previewSpendingRule: () => new Promise((resolve) => resolvers.push(resolve)),
    };

    sandbox._scheduleSpRulePreview();
    sandbox._scheduleSpRulePreview();
    assert.equal(resolvers.length, 2);

    // The second (newer) request's response arrives first.
    resolvers[1]({ match_count: 2, sample: [] });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    assert.match(preview.innerHTML, /Matches 2 uncategorized/);

    // The first (now-stale) request's response arrives late and must not
    // overwrite the newer, already-displayed result.
    resolvers[0]({ match_count: 99, sample: [] });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    assert.match(preview.innerHTML, /Matches 2 uncategorized/);
});

test("_scheduleSpRulePreview ignores a stale rejection after a newer success", async () => {
    const sandbox = loadAppIntoContext();
    const preview = { textContent: "", innerHTML: "" };
    const patternInput = { value: "SHOP" };
    sandbox.document.getElementById = (id) => {
        if (id === "spRulePreview") return preview;
        if (id === "spRulePattern") return patternInput;
        return null;
    };
    sandbox.setTimeout = (fn) => fn();
    sandbox.clearTimeout = () => {};

    const pending = [];
    sandbox.window.apiClient = {
        previewSpendingRule: () =>
            new Promise((resolve, reject) => pending.push({ resolve, reject })),
    };

    sandbox._scheduleSpRulePreview();
    sandbox._scheduleSpRulePreview();
    assert.equal(pending.length, 2);

    pending[1].resolve({ match_count: 2, sample: [] });
    await new Promise((r) => setImmediate(r));
    assert.match(preview.innerHTML, /Matches 2 uncategorized/);

    // The older request fails late; its error must not replace the result.
    pending[0].reject(new Error("network down"));
    await new Promise((r) => setImmediate(r));
    assert.match(preview.innerHTML, /Matches 2 uncategorized/);
    assert.doesNotMatch(preview.textContent, /unavailable/);
});

test("apiErrorDetail falls back when there is no usable detail", () => {
    const { apiErrorDetail } = loadAppIntoContext();
    assert.equal(apiErrorDetail(null, "Failed to update rule"), "Failed to update rule");
    assert.equal(apiErrorDetail({}, "Failed"), "Failed");
    assert.equal(apiErrorDetail({ detail: [] }, "Failed"), "Failed");
    assert.equal(apiErrorDetail({ detail: "" }, "Failed"), "Failed");
});
