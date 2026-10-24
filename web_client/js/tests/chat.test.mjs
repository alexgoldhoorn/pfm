// Pure helpers behind the AI Chat page: send key, tool chips, exports.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const MESSAGES = [
    { role: "user", content: "What is my P&L?", ts: "2026-10-23T10:00:00Z" },
    {
        role: "assistant", content: "You are **up 8%**.\n\n| a | b |\n|---|---|", ts: "2026-10-23T10:00:05Z",
        model: "claude-sonnet-5-5", tier: "main",
        tools_used: [{ name: "get_kpis", arguments: {}, ms: 12, ok: true }],
    },
];

test("chatIsSendKey: Shift+Enter and Ctrl/Cmd+Enter send, plain Enter does not", () => {
    assert.equal(win.chatIsSendKey({ key: "Enter", shiftKey: true }), true);
    assert.equal(win.chatIsSendKey({ key: "Enter", ctrlKey: true }), true);
    assert.equal(win.chatIsSendKey({ key: "Enter", metaKey: true }), true);
    assert.equal(win.chatIsSendKey({ key: "Enter" }), false);
    assert.equal(win.chatIsSendKey({ key: "a", shiftKey: true }), false);
});

test("chatToolsHtml: empty for no tools, a chip naming each tool otherwise", () => {
    assert.equal(win.chatToolsHtml([]), "");
    assert.equal(win.chatToolsHtml(undefined), "");
    const html = win.chatToolsHtml([
        { name: "get_kpis", arguments: {}, ms: 12, ok: true },
        { name: "get_holdings", arguments: { portfolio_id: "p1" }, ms: 40, ok: false },
    ]);
    assert.match(html, /Used 2 tools: get_kpis, get_holdings/);
    assert.match(html, /12 ms/);
    assert.match(html, /portfolio_id/);
    assert.match(html, /failed/);
    assert.match(html, /^<details class="chat-tools">/);
});

test("chatToolsHtml: singular wording and escaped names", () => {
    const html = win.chatToolsHtml([{ name: "<b>x</b>", arguments: { q: "<s>" } }]);
    assert.match(html, /Used 1 tool: &lt;b&gt;x&lt;\/b&gt;/);
    assert.ok(!html.includes("<s>"));
    assert.ok(!html.includes("ms</span>"), "no duration when ms is missing");
});

test("chatSessionToMarkdown: heading, one section per turn, model and tools", () => {
    const md = win.chatSessionToMarkdown("Dividend review", MESSAGES);
    assert.ok(md.startsWith("# Dividend review\n"));
    assert.match(md, /## You — /);
    assert.match(md, /## Assistant \(claude-sonnet-5-5\) — /);
    assert.match(md, /What is my P&L\?/);
    assert.match(md, /You are \*\*up 8%\*\*/);
    assert.match(md, /_Tools used: get_kpis_/);
});

test("chatSessionToHtml: a full page with escaped user text and the meta line", () => {
    const html = win.chatSessionToHtml("A <b>name</b>", [
        { role: "user", content: "<script>x</script>", ts: "2026-10-23T10:00:00Z" },
        MESSAGES[1],
    ]);
    assert.ok(html.startsWith("<!doctype html>"));
    assert.match(html, /<title>A &lt;b&gt;name&lt;\/b&gt;<\/title>/);
    assert.ok(!html.includes("<script>x</script>"));
    assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
    assert.match(html, /Assistant · claude-sonnet-5-5 · /);
    assert.match(html, /Tools used: get_kpis/);
    assert.match(html, /class="turn user"/);
});

test("chatExportFilename: slug from the name plus the date and extension", () => {
    const name = win.chatExportFilename("Research: EXA / Q3?", "md");
    assert.match(name, /^research-exa-q3-\d{4}-\d{2}-\d{2}\.md$/);
    assert.match(win.chatExportFilename("", "html"), /^chat-\d{4}-\d{2}-\d{2}\.html$/);
});
