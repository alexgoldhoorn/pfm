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
