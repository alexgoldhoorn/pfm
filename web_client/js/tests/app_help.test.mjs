// Help glossary + the exported app_help.json the AI chat's get_app_help reads.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildAppHelp, renderAppHelp, htmlToText, HELP_JSON } from "../../../scripts/export_app_help.mjs";
import { loadAppIntoContext } from "./helpers.mjs";

test("app_help.json matches help_text.js (run node scripts/export_app_help.mjs)", () => {
    assert.equal(readFileSync(HELP_JSON, "utf8"), renderAppHelp(buildAppHelp()));
});

test("glossary covers fundamentals the Research page shows", () => {
    const terms = buildAppHelp().glossary.map(g => g.term.toLowerCase()).join("\n");
    for (const t of ["capex", "free cash flow", "ebitda", "p/e", "peg", "eps", "roe", "payout ratio", "debt / equity", "ev/ebitda"]) {
        assert.ok(terms.includes(t), `missing glossary term: ${t}`);
    }
});

test("every glossary entry has a category, a term and a definition", () => {
    for (const g of buildAppHelp().glossary) {
        assert.ok(g.term && g.category && g.definition && g.definition.length > 20, JSON.stringify(g));
    }
});

test("htmlToText strips tags and decodes entities", () => {
    assert.equal(htmlToText("<p>A &amp; B</p><ul><li>one</li></ul>"), "A & B • one");
});

test("helpGlossaryHtml groups by category and escapes text", () => {
    const ctx = loadAppIntoContext();
    const html = ctx.helpGlossaryHtml([
        { cat: "X", term: "<b>T</b>", def: "d1" },
        { cat: "Y", term: "U", def: "d2" },
        { cat: "X", term: "V", def: "d3" },
    ]);
    assert.ok(html.indexOf(">X<") < html.indexOf(">Y<"));
    assert.ok(html.includes("&lt;b&gt;T&lt;/b&gt;"));
    assert.equal((html.match(/data-search-type="glossary"/g) || []).length, 3);
});

test("helpChatMessage keeps the question and points at get_app_help", () => {
    const ctx = loadAppIntoContext();
    const msg = ctx.helpChatMessage("What is capex?");
    assert.ok(msg.startsWith("What is capex?"));
    assert.ok(msg.includes("get_app_help"));
});
