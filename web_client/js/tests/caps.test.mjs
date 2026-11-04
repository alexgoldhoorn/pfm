// Pure helpers behind the Caps card and the workbench headroom line.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("capStatusBadge: colour per status, escapes unknown", () => {
    assert.match(win.capStatusBadge("ok"), /bg-success[^>]*>ok/);
    assert.match(win.capStatusBadge("near"), /bg-warning[^>]*>near/);
    assert.match(win.capStatusBadge("breached"), /bg-danger[^>]*>breached/);
    assert.match(win.capStatusBadge("<b>"), /&lt;b&gt;/);
});

test("capRowHtml: explicit row has edit and delete buttons", () => {
    const html = win.capRowHtml({ id: 7, scope: "symbol", key: "AAA", max_pct: 5, current_pct: 6.5, headroom_pct: -1.5, headroom_eur: -150, status: "breached", implicit: false, note: "<i>x</i>" });
    assert.match(html, /data-cap-edit="7"/);
    assert.match(html, /data-cap-del="7"/);
    assert.match(html, /text-danger/);
    assert.ok(!html.includes("<i>x</i>"));
    assert.match(html, /&lt;i&gt;x/);
});

test("capRowHtml: implicit row is muted with no buttons", () => {
    const html = win.capRowHtml({ id: null, scope: "symbol", key: "BBB", max_pct: 10, current_pct: 2, headroom_pct: 8, headroom_eur: 800, status: "ok", implicit: true });
    assert.match(html, /text-muted/);
    assert.match(html, /default stock cap/);
    assert.ok(!html.includes("data-cap-del"));
});

test("capForSymbol: explicit beats implicit, case-insensitive, else null", () => {
    const st = { caps: [
        { scope: "symbol", key: "aaa", implicit: true, max_pct: 10 },
        { scope: "symbol", key: "AAA", implicit: false, max_pct: 5 },
        { scope: "sector", key: "AAA", implicit: false, max_pct: 1 },
    ] };
    assert.equal(win.capForSymbol(st, "aaa").max_pct, 5);
    assert.equal(win.capForSymbol(st, "ZZZ"), null);
    assert.equal(win.capForSymbol(null, "AAA"), null);
});

test("capHeadroomLineHtml: empty for null, headroom or over-by wording", () => {
    assert.equal(win.capHeadroomLineHtml(null), "");
    assert.match(win.capHeadroomLineHtml({ max_pct: 5, headroom_eur: 300, status: "ok" }), /Cap 5[.,]0%: headroom/);
    assert.match(win.capHeadroomLineHtml({ max_pct: 5, headroom_eur: -300, status: "breached" }), /over by/);
});

test("capsTableHtml: empty message and rows", () => {
    assert.match(win.capsTableHtml({ caps: [] }), /No caps yet/);
    const t = win.capsTableHtml({ caps: [{ id: 1, scope: "sector", key: "Tech", max_pct: 30, current_pct: 10, headroom_pct: 20, headroom_eur: 2000, status: "ok" }] });
    assert.match(t, /table-responsive/);
    assert.match(t, /Tech/);
});
