// Export the web client's help text (web_client/js/help_text.js) to
// portf_server/data/app_help.json, plain text, for the AI chat's
// get_app_help tool. help_text.js stays the single source; the JS test
// web_client/js/tests/app_help.test.mjs fails when the JSON is stale.
//
//   node scripts/export_app_help.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const HELP_JS = join(ROOT, "web_client", "js", "help_text.js");
export const HELP_JSON = join(ROOT, "portf_server", "data", "app_help.json");

const ENTITIES = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
    rarr: "→", larr: "←", mdash: "—", ndash: "–", hellip: "…", times: "×",
    middot: "·", euro: "€", le: "≤", ge: "≥", asymp: "≈", minus: "−",
};

// Strip tags and decode the entities help_text.js uses, then collapse spaces.
export function htmlToText(html) {
    return String(html || "")
        .replace(/<li[^>]*>/gi, " • ")
        .replace(/<[^>]+>/g, " ")
        .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
        .replace(/&([a-z]+);/gi, (m, name) => ENTITIES[name.toLowerCase()] ?? m)
        .replace(/\s+/g, " ")
        .trim();
}

// Build the JSON document from help_text.js's globals.
export function buildAppHelp(source = readFileSync(HELP_JS, "utf8")) {
    const sandbox = { window: {} };
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox, { filename: "help_text.js" });
    const w = sandbox.window;
    return {
        generated_from: "web_client/js/help_text.js",
        glossary: (w.HELP_GLOSSARY || []).map(e => ({
            term: e.term, category: e.cat || "Other", definition: e.def,
        })),
        pages: Object.entries(w.PAGE_HELP || {}).map(([key, h]) => ({
            key, title: h.title || key, text: htmlToText(h.body),
        })),
        metrics: Object.entries(w.METRIC_HELP || {}).map(([key, text]) => ({
            key, text: htmlToText(text),
        })),
    };
}

export function renderAppHelp(doc = buildAppHelp()) {
    return JSON.stringify(doc, null, 1) + "\n";
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
    writeFileSync(HELP_JSON, renderAppHelp());
    console.log(`wrote ${HELP_JSON}`);
}
