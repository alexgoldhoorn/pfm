// Pure helper behind the Stress test card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const result = {
    loss_eur: 3000, loss_pct_of_portfolio: 30, net_worth_before_eur: 50000, net_worth_after_eur: 47000,
    by_asset_type: [{ asset_type: "etf", value_eur: 10000, loss_eur: 3000, loss_pct: 30 }],
    top_losers: [{ symbol: "AAA", name: "Alpha <Corp>", value_eur: 6000, loss_eur: 1800, loss_pct: 30 }],
    assumed_equity: ["FND1"],
    retirement_after: {
        scenarios_before: [{ retirement_age: 62, verdict: "on_track", lasts_until_age: 95 }],
        scenarios_after: [{ retirement_age: 62, verdict: "at_risk", lasts_until_age: 80 }],
    },
};

test("stressSummaryHtml: loss in red, net worth before and after", () => {
    const html = win.stressSummaryHtml(result);
    assert.match(html, /text-danger/);
    assert.match(html, /Net worth/);
    assert.match(html, /30[.,]0%/);
});

test("stressSummaryHtml: by-type table, losers escaped, assumed equity note", () => {
    const html = win.stressSummaryHtml(result);
    assert.match(html, /By asset type/);
    assert.match(html, /<td>etf<\/td>/);
    assert.ok(!html.includes("<Corp>"));
    assert.match(html, /Alpha &lt;Corp&gt;/);
    assert.match(html, /Assumed to be all equity[^<]*FND1/);
});

test("stressSummaryHtml: retirement verdict before and after", () => {
    const html = win.stressSummaryHtml(result);
    assert.match(html, /on track<\/strong> → <strong[^>]*>at risk/);
});

test("stressSummaryHtml: no retirement line when absent, tolerates empty", () => {
    const html = win.stressSummaryHtml({ ...result, retirement_after: null, assumed_equity: [] });
    assert.ok(!html.includes("Retirement"));
    assert.ok(!html.includes("Assumed"));
    assert.doesNotThrow(() => win.stressSummaryHtml({}));
});
