// Pure helpers behind the chat's tables, sources, follow-ups, exports and
// the category essentiality selector.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("tableToCsv quotes commas, quotes and newlines", () => {
    const csv = win.tableToCsv([["Name", "Amount"], ['Say "hi", ok', "1,234.50 €"], ["a\nb", ""]]);
    assert.equal(csv, 'Name,Amount\r\n"Say ""hi"", ok","1,234.50 €"\r\n"a\nb",');
    assert.equal(win.tableToCsv([]), "");
});

test("chatParseNumber reads money in both conventions and rejects text", () => {
    const n = win.chatParseNumber;
    assert.equal(n("1,234.50 €"), 1234.5);
    assert.equal(n("€1.234,50"), 1234.5);
    assert.equal(n("$ 12"), 12);
    assert.equal(n("(250)"), -250);
    assert.equal(n("-12.5%"), -12.5);
    assert.equal(n("−12"), -12);
    assert.equal(n("+3"), 3);
    assert.equal(n("3 months"), 3);
    assert.equal(n("1.234.567"), 1234567);
    assert.equal(n("10.000"), 10000);
    assert.equal(n("0.500"), 0.5);
    assert.equal(n("12,5"), 12.5);
    assert.equal(n("1 234,5"), 1234.5);
    assert.ok(Number.isNaN(n("Q3 2025")));
    assert.ok(Number.isNaN(n("Groceries")));
    assert.ok(Number.isNaN(n("")));
    assert.ok(Number.isNaN(n("2025-11-03")));
});

test("chatSortCompare: numbers numerically, text naturally, empties last", () => {
    const sort = (arr, dir) => arr.slice().sort((a, b) => win.chatSortCompare(a, b, dir));
    assert.deepEqual(sort(["€1.200,00", "€90,50", "€1.000,00"], "asc"), ["€90,50", "€1.000,00", "€1.200,00"]);
    assert.deepEqual(sort(["10", "9", "100"], "desc"), ["100", "10", "9"]);
    assert.deepEqual(sort(["b", "", "a", "C"], "asc"), ["a", "b", "C", ""]);
    assert.deepEqual(sort(["b", "", "a", "C"], "desc"), ["C", "b", "a", ""]);
    assert.deepEqual(sort(["item 10", "item 2"], "asc"), ["item 2", "item 10"]);
    assert.equal(win.chatSortCompare("5", "5", "asc"), 0);
});

test("sources: only http(s) links, titles escaped, host as fallback title", () => {
    const html = win.chatSourcesHtml([
        { title: "A <b>page</b>", url: "https://example.com/a?x=1&y=2" },
        { title: "bad", url: "javascript:alert(1)" },
        { title: "", url: "http://news.example.org/x" },
        { title: "no url" },
        null,
    ]);
    assert.match(html, /target="_blank" rel="noopener noreferrer"/);
    assert.match(html, /A &lt;b&gt;page&lt;\/b&gt;/);
    assert.ok(!html.includes("javascript:"));
    assert.match(html, />news\.example\.org</);
    assert.equal((html.match(/<li>/g) || []).length, 2);
    assert.equal(win.chatSourcesHtml([]), "");
    assert.equal(win.chatSourcesHtml(undefined), "");
    assert.equal(win.chatSourcesHtml([{ title: "x", url: "ftp://a.b/c" }]), "");
});

test("sources markdown lists links and drops unsafe ones", () => {
    const md = win.chatSourcesMarkdown([{ title: "Doc [1]", url: "https://example.com/d" }, { title: "x", url: "data:text/html,hi" }]);
    assert.match(md, /\*\*Sources\*\*/);
    assert.match(md, /- \[Doc 1\]\(https:\/\/example\.com\/d\)/);
    assert.ok(!md.includes("data:"));
    assert.equal(win.chatSourcesMarkdown(null), "");
});

test("follow-ups: at most four non-empty strings, escaped in the chips", () => {
    const list = win.chatFollowupsClean(["a", " ", 5, "b", "c", "d", "e"]);
    assert.deepEqual(Array.from(list), ["a", "b", "c", "d"]);
    const html = win.chatFollowupsHtml(['Why <i>this</i>?']);
    assert.match(html, /class="chat-suggestion chat-followup"/);
    assert.match(html, /Why &lt;i&gt;this&lt;\/i&gt;\?/);
    assert.equal(win.chatFollowupsHtml([]), "");
    assert.equal(win.chatFollowupsHtml("nope"), "");
});

test("chatReportDefaultTitle: first heading, else first text line, trimmed", () => {
    assert.equal(win.chatReportDefaultTitle("Intro\n\n## Savings **plan**\nbody"), "Savings plan");
    assert.equal(win.chatReportDefaultTitle("Plain first line\nsecond"), "Plain first line");
    assert.equal(win.chatReportDefaultTitle("```chart\n{}\n```"), "Chat report");
    assert.equal(win.chatReportDefaultTitle(""), "Chat report");
    assert.equal(win.chatReportDefaultTitle("x".repeat(200)).length, 80);
});

test("the starter chips come in three labelled groups", () => {
    const html = win.chatStartersHtml();
    ["Portfolio", "Spending", "What if"].forEach(g => assert.match(html, new RegExp(`chat-starter-label">${g}<`)));
    assert.equal((html.match(/class="chat-suggestion"/g) || []).length, 10);
    assert.match(html, /What is my total P&amp;L\?/);
});

// ── Export with chart images ───────────────────────────────────────────
const PNG = "data:image/png;base64,iVBORw0KGgo=";
const chartMd = "Intro\n\n```chart\n" + JSON.stringify({ type: "bar", title: 'T "q"', labels: ["a"], series: [{ name: "s", data: [1] }] }) + "\n```\n\nafter";

test("export embeds chart images, falls back to a pre, rejects odd sources", () => {
    win.marked = { parse: (t) => t };
    win.DOMPurify = { sanitize: (h) => h };
    const withImg = win.chatMarkdownToExportHtml(chartMd, [PNG]);
    assert.match(withImg, /<figure class="chart"><img src="data:image\/png;base64,iVBORw0KGgo=" alt="T &quot;q&quot;"><\/figure>/);
    assert.ok(!withImg.includes("```chart"));
    const without = win.chatMarkdownToExportHtml(chartMd, [""]);
    assert.match(without, /<pre><code>/);
    assert.ok(!without.includes("<img"));
    assert.ok(!win.chatMarkdownToExportHtml(chartMd, ['data:text/html,<script>x</script>']).includes("<script>"));
    assert.match(win.chatMarkdownToExportHtml(chartMd), /<pre><code>/);
});

test("export images can be keyed by the chart json", () => {
    win.marked = { parse: (t) => t };
    win.DOMPurify = { sanitize: (h) => h };
    const json = JSON.stringify(win.chatChartSpec(chartMd.split("\n").slice(3, 4).join("")));
    const html = win.chatMarkdownToExportHtml(chartMd, { [json]: PNG });
    assert.match(html, /<img src=/);
});

test("chatSessionToHtml: images follow the charts across messages; sources included", () => {
    win.marked = { parse: (t) => t };
    win.DOMPurify = { sanitize: (h) => h };
    const msgs = [
        { role: "user", content: "q" },
        { role: "assistant", content: chartMd, sources: [{ title: "S", url: "https://example.com/s" }] },
        { role: "assistant", content: chartMd },
    ];
    const html = win.chatSessionToHtml("Thread", msgs, [PNG, ""]);
    assert.equal((html.match(/<img /g) || []).length, 1);
    assert.equal((html.match(/<pre><code>/g) || []).length, 1);
    assert.match(html, /class="chat-sources"/);
    assert.match(html, /href="https:\/\/example.com\/s"/);
    const md = win.chatSessionToMarkdown("Thread", msgs);
    assert.match(md, /\*\*Sources\*\*/);
});

test("chatExportPage wraps a body in a titled, escaped page", () => {
    const html = win.chatExportPage("A <b>", "<p>x</p>");
    assert.match(html, /<title>A &lt;b&gt;<\/title>/);
    assert.match(html, /<p>x<\/p>/);
});

// ── Category essentiality ──────────────────────────────────────────────
const TREE = [
    { id: 1, name: "Income", is_root: true, parent_name: null },
    { id: 2, name: "Spend", is_root: true, parent_name: null },
    { id: 3, name: "Housing", is_root: false, parent_name: "Spend" },
    { id: 4, name: "Mortgage", is_root: false, parent_name: "Housing" },
    { id: 5, name: "Salary", is_root: false, parent_name: "Income" },
    { id: 6, name: "Orphan", is_root: false, parent_name: "Nowhere" },
];

test("spendingCategoryIsSpend: below the Spend root only", () => {
    const by = n => TREE.find(c => c.name === n);
    assert.equal(win.spendingCategoryIsSpend(TREE, by("Housing")), true);
    assert.equal(win.spendingCategoryIsSpend(TREE, by("Mortgage")), true);
    assert.equal(win.spendingCategoryIsSpend(TREE, by("Salary")), false);
    assert.equal(win.spendingCategoryIsSpend(TREE, by("Spend")), false);
    assert.equal(win.spendingCategoryIsSpend(TREE, by("Orphan")), false);
    const loop = [{ id: 1, name: "A", parent_name: "B" }, { id: 2, name: "B", parent_name: "A" }];
    assert.equal(win.spendingCategoryIsSpend(loop, loop[0]), false);
});

test("spendingEssentialityOptions selects the current level", () => {
    const html = win.spendingEssentialityOptions("flexible");
    assert.match(html, /<option value="">—<\/option>/);
    assert.match(html, /<option value="flexible" selected>Flexible<\/option>/);
    assert.ok(!/value="fixed" selected/.test(html));
    assert.ok(!/selected/.test(win.spendingEssentialityOptions(null)));
});

test("spendingEssentialitySuggestions keeps valid, known, unique Spend rows", () => {
    const out = win.spendingEssentialitySuggestions([
        { name: "Mortgage", essentiality: "fixed", reason: "contractual" },
        { name: "Mortgage", essentiality: "optional" },
        { name: "Salary", essentiality: "fixed" },
        { name: "Housing", essentiality: "luxury" },
        { name: "Unknown", essentiality: "fixed" },
        { name: "Housing", essentiality: "flexible" },
        null,
    ], TREE);
    assert.deepEqual(JSON.parse(JSON.stringify(out)), [
        { name: "Mortgage", essentiality: "fixed", reason: "contractual" },
        { name: "Housing", essentiality: "flexible", reason: "" },
    ]);
    assert.deepEqual(Array.from(win.spendingEssentialitySuggestions(undefined, TREE)), []);
});

test("safeMarkdownHtml shows escaped text when DOMPurify is missing", () => {
    const saved = win.DOMPurify;
    win.DOMPurify = undefined;
    const out = win.safeMarkdownHtml("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>");
    win.DOMPurify = saved;
    assert.ok(!out.includes("<img"));
    assert.ok(out.includes("&lt;img"));
});

test("safeMarkdownHtml sanitises, drops remote images and hardens links", () => {
    const saved = win.DOMPurify;
    const hooks = {};
    let seen = null;
    win.DOMPurify = {
        addHook: (name, fn) => { hooks[name] = fn; },
        sanitize: (html, config) => { seen = config; return `clean:${html}`; },
    };
    assert.equal(win.safeMarkdownHtml("<b>x</b>", "x"), "clean:<b>x</b>");
    assert.ok(seen.FORBID_TAGS.includes("iframe") && seen.FORBID_TAGS.includes("form"));
    assert.ok(seen.FORBID_ATTR.includes("style"));
    const hook = hooks.afterSanitizeAttributes;
    const node = (tag, attrs) => ({
        tagName: tag, attrs: { ...attrs }, removed: false,
        getAttribute(k) { return this.attrs[k] ?? null; },
        setAttribute(k, v) { this.attrs[k] = v; },
        removeAttribute(k) { delete this.attrs[k]; },
        remove() { this.removed = true; },
    });
    const remote = node("IMG", { src: "https://example.com/p.png?leak=1" });
    hook(remote);
    assert.equal(remote.removed, true);
    const inline = node("IMG", { src: "data:image/png;base64,AAAA" });
    hook(inline);
    assert.equal(inline.removed, false);
    const link = node("A", { href: "https://example.com/" });
    hook(link);
    assert.equal(link.attrs.target, "_blank");
    assert.equal(link.attrs.rel, "noopener noreferrer");
    const script = node("A", { href: "javascript:alert(1)" });
    hook(script);
    assert.equal(script.attrs.href, undefined);
    win.DOMPurify = saved;
});
