// Pure helpers behind chat chart blocks.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();
const ok = { type: "bar", title: "T", labels: ["a", "b"], series: [{ name: "s", data: [1, 2.5] }] };

test("chatChartSpec accepts a valid bar and defaults currency", () => {
    const s = win.chatChartSpec(JSON.stringify(ok));
    assert.equal(s.type, "bar");
    assert.equal(s.currency, "EUR");
    assert.equal(s.stacked, false);
    assert.deepEqual(Array.from(s.labels), ["a", "b"]);
});

test("chatChartSpec rejects invalid input without throwing", () => {
    const bad = [
        "not json", "[]", "null", JSON.stringify({ ...ok, type: "radar" }),
        JSON.stringify({ ...ok, labels: [] }), JSON.stringify({ type: "bar", labels: ["a"] }),
        JSON.stringify({ ...ok, series: [{ name: "s", data: [1] }] }),
        JSON.stringify({ ...ok, series: [{ name: "s", data: [1, "2"] }] }),
        JSON.stringify({ ...ok, series: [{ name: "s", data: [1, null] }] }),
        JSON.stringify({ ...ok, labels: Array(61).fill("x"), series: [{ name: "s", data: Array(61).fill(1) }] }),
        JSON.stringify({ ...ok, series: Array(7).fill({ name: "s", data: [1, 2] }) }),
    ];
    bad.forEach(b => assert.equal(win.chatChartSpec(b), null, b));
});

test("pie keeps the first series; stacked only for bar; bad currency falls back", () => {
    const two = [{ name: "a", data: [1, 2] }, { name: "b", data: [3, 4] }];
    const pie = win.chatChartSpec({ ...ok, type: "pie", series: two, stacked: true, currency: "usd" });
    assert.equal(pie.series.length, 1);
    assert.equal(pie.stacked, false);
    assert.equal(pie.currency, "USD");
    assert.equal(win.chatChartSpec({ ...ok, series: two, stacked: true, currency: "euros" }).currency, "EUR");
    assert.equal(win.chatChartSpec({ ...ok, series: two, stacked: true }).stacked, true);
});

test("chatChartConfig uses the colour callback and money formatter", () => {
    const spec = win.chatChartSpec({ ...ok, type: "pie" });
    const cfg = win.chatChartConfig(spec, i => `c${i}`, (v, c) => `${v}${c}`);
    assert.equal(cfg.type, "doughnut");
    assert.deepEqual(Array.from(cfg.data.datasets[0].backgroundColor), ["c0", "c1"]);
    const line = win.chatChartConfig(win.chatChartSpec({ ...ok, type: "line" }), i => `c${i}`, (v, c) => `${v}${c}`);
    assert.equal(line.data.datasets[0].borderColor, "c0");
    assert.equal(line.options.scales.y.ticks.callback(5), "5EUR");
});

test("chatPrepareCharts swaps valid fences, leaves invalid ones and escapes", () => {
    const md = "Intro\n\n```chart\n" + JSON.stringify({ ...ok, title: 'a"<b>' }) + "\n```\n\nafter";
    const out = win.chatPrepareCharts(md);
    assert.match(out, /<div class="chat-chart" data-chart="/);
    assert.ok(!out.includes('"<b>'));
    assert.ok(!out.includes("```chart"));
    const bad = "```chart\n{nope\n```";
    assert.equal(win.chatPrepareCharts(bad), bad);
    assert.equal(win.chatPrepareCharts("```js\nx\n```"), "```js\nx\n```");
});
