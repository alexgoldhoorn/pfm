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

// ── New chart types ────────────────────────────────────────────────────
const colour = i => `c${i}`;
const two = [{ name: "a", data: [1, 2] }, { name: "b", data: [3, 4] }];

test("hbar is horizontal, keeps stacked and swaps which axis carries the titles", () => {
    const spec = win.chatChartSpec({ ...ok, type: "hbar", series: two, stacked: true, x_title: "Amount", y_title: "Category" });
    assert.equal(spec.stacked, true);
    const cfg = win.chatChartConfig(spec, colour);
    assert.equal(cfg.type, "bar");
    assert.equal(cfg.options.indexAxis, "y");
    assert.equal(cfg.options.scales.x.title.text, "Amount");
    assert.equal(cfg.options.scales.y.title.text, "Category");
    assert.equal(cfg.options.scales.x.stacked, true);
});

test("area is a filled line and may stack", () => {
    const spec = win.chatChartSpec({ ...ok, type: "area", series: two, stacked: true });
    assert.equal(spec.stacked, true);
    const cfg = win.chatChartConfig(spec, i => ["#2a78d6", "#eb6834"][i]);
    assert.equal(cfg.type, "line");
    assert.equal(cfg.data.datasets[0].fill, true);
    assert.equal(cfg.data.datasets[0].backgroundColor, "rgba(42, 120, 214, 0.35)");
    assert.equal(cfg.options.scales.y.stacked, true);
});

test("chatColourAlpha handles hex and rgb and leaves other values alone", () => {
    assert.equal(win.chatColourAlpha("#ff0000", 0.5), "rgba(255, 0, 0, 0.5)");
    assert.equal(win.chatColourAlpha("rgb(1, 2, 3)", 0.1), "rgba(1, 2, 3, 0.1)");
    assert.equal(win.chatColourAlpha("var(--x)", 0.5), "var(--x)");
});

test("combo: series kind and axis, a right axis only when one series uses it", () => {
    const series = [
        { name: "Spend", data: [1, 2], kind: "bar" },
        { name: "Rate", data: [5, 6], kind: "line", axis: "right" },
        { name: "Odd", data: [1, 1], kind: "radar", axis: "up" },
    ];
    const spec = win.chatChartSpec({ ...ok, type: "combo", series, right_unit: "%" });
    assert.deepEqual(Array.from(spec.series, s => s.kind), ["bar", "line", "bar"]);
    assert.deepEqual(Array.from(spec.series, s => s.axis), ["left", "right", "left"]);
    const cfg = win.chatChartConfig(spec, colour, (v, c) => `${v}${c}`);
    assert.equal(cfg.type, "bar");
    assert.equal(cfg.data.datasets[1].type, "line");
    assert.equal(cfg.data.datasets[1].yAxisID, "y1");
    assert.equal(cfg.data.datasets[0].yAxisID, "y");
    assert.ok(cfg.data.datasets[1].order < cfg.data.datasets[0].order);
    assert.equal(cfg.options.scales.y1.position, "right");
    assert.equal(cfg.options.scales.y1.ticks.callback(12.5), "12.5%");
    assert.equal(cfg.options.scales.y.ticks.callback(5), "5EUR");
    const noRight = win.chatChartConfig(win.chatChartSpec({ ...ok, type: "combo", series: two }), colour);
    assert.equal(noRight.options.scales.y1, undefined);
});

test("unit makes plain numbers: percent sticks, other units follow a space", () => {
    const pct = win.chatChartConfig(win.chatChartSpec({ ...ok, unit: "%" }), colour);
    assert.equal(pct.options.scales.y.ticks.callback(12.345), "12.35%");
    const months = win.chatChartConfig(win.chatChartSpec({ ...ok, unit: "months" }), colour);
    assert.equal(months.options.scales.y.ticks.callback(1500), "1,500 months");
    assert.equal(months.options.plugins.tooltip.callbacks.label({ dataset: { label: "s" }, raw: 3, datasetIndex: 0 }), " s: 3 months");
});

test("waterfall: first series only, totals filtered to valid indexes, running sums", () => {
    const spec = win.chatChartSpec({
        type: "waterfall", labels: ["Start", "Salary", "Rent", "End", "Extra"],
        series: [{ name: "Cash", data: [100, 50, -30, 999, 10] }, { name: "ignored", data: [1, 1, 1, 1, 1] }],
        totals: [0, 3, 3, 9, -1, 1.5, "2"],
    });
    assert.equal(spec.series.length, 1);
    assert.deepEqual(Array.from(spec.totals), [0, 3]);
    const pts = win.chatWaterfallPoints(spec);
    assert.deepEqual(JSON.parse(JSON.stringify(pts)), [
        { start: 0, end: 100, value: 100, total: true },
        { start: 100, end: 150, value: 50, total: false },
        { start: 150, end: 120, value: -30, total: false },
        { start: 0, end: 120, value: 120, total: true },
        { start: 120, end: 130, value: 10, total: false },
    ]);
    const cfg = win.chatChartConfig(spec, colour, null, { tokens: { good: "G", bad: "B", neutral: "N" } });
    assert.equal(cfg.type, "bar");
    assert.deepEqual(JSON.parse(JSON.stringify(cfg.data.datasets[0].data[1])), [100, 150]);
    assert.deepEqual(Array.from(cfg.data.datasets[0].backgroundColor), ["N", "G", "B", "N", "G"]);
    assert.equal(cfg.options.plugins.legend.display, false);
});

test("waterfall without totals starts from zero", () => {
    const spec = win.chatChartSpec({ type: "waterfall", labels: ["a", "b"], series: [{ name: "x", data: [5, -2] }] });
    assert.deepEqual(Array.from(spec.totals), []);
    assert.equal(win.chatWaterfallPoints(spec)[1].end, 3);
});

test("titles and unit are trimmed strings; non-strings are dropped", () => {
    const spec = win.chatChartSpec({ ...ok, unit: " months ", x_title: 5, y_title: " Total " });
    assert.equal(spec.unit, "months");
    assert.equal(spec.x_title, "");
    assert.equal(spec.y_title, "Total");
});

test("existing limits still hold for the new types", () => {
    ["hbar", "area", "combo", "waterfall"].forEach(type => {
        assert.equal(win.chatChartSpec({ ...ok, type, labels: [] }), null);
        assert.equal(win.chatChartSpec({ ...ok, type, series: Array(7).fill({ name: "s", data: [1, 2] }) }), null);
        assert.equal(win.chatChartSpec({ ...ok, type, series: [{ name: "s", data: [1, Infinity] }] }), null);
    });
});

test("onPick is wired to clicks and hover sets the cursor", () => {
    const picked = [];
    const cfg = win.chatChartConfig(win.chatChartSpec(ok), colour, null, { onPick: (l, s) => picked.push([l, s]) });
    cfg.options.onClick({}, [{ index: 1, datasetIndex: 0 }]);
    cfg.options.onClick({}, []);
    assert.deepEqual(picked, [[1, 0]]);
    const target = { style: {} };
    cfg.options.onHover({ native: { target } }, [{}]);
    assert.equal(target.style.cursor, "pointer");
    cfg.options.onHover({ native: { target } }, []);
    assert.equal(target.style.cursor, "default");
    assert.equal(win.chatChartConfig(win.chatChartSpec(ok), colour).options.onClick, undefined);
});

test("chatChartDrillPrompt builds the question and refuses a bad index", () => {
    const spec = win.chatChartSpec({ ...ok, title: "Spending by category", labels: ["Food", "Rent"] });
    assert.equal(win.chatChartDrillPrompt(spec, 1, 0), 'Break down "Rent" from the chart "Spending by category" in more detail.');
    assert.equal(win.chatChartDrillPrompt(spec, 5, 0), "");
    assert.equal(win.chatChartDrillPrompt(spec, -1, 0), "");
    const untitled = win.chatChartSpec({ ...ok, title: "" });
    assert.equal(win.chatChartDrillPrompt(untitled, 0, 0), 'Break down "a" from the chart above in more detail.');
    const multi = win.chatChartSpec({ ...ok, title: "T", series: two });
    assert.match(win.chatChartDrillPrompt(multi, 0, 1), /^Break down "a" from the chart "T" in more detail\. Focus on the series "b"\.$/);
});

test("chatPrepareCharts accepts a render callback", () => {
    const md = "x\n\n```chart\n" + JSON.stringify(ok) + "\n```\n";
    const out = win.chatPrepareCharts(md, (spec, json) => `<i>${spec.type}:${json.length > 10}</i>`);
    assert.match(out, /<i>bar:true<\/i>/);
});
