// Test helpers — sandbox setup and shared utilities for fund profile tests
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");

// Must match the load order in index.html.
const FILES = ["pfm_core.js", "pfm_pages.js", "pfm_analytics.js", "pfm_features.js"];

// Build a sandbox with just enough browser surface for the files' top-level
// code (window.PREFS from localStorage, applyTheme() reading documentElement,
// the DOMContentLoaded registration in pfm_features). Runtime-only globals
// (bootstrap, Chart, marked, fetch) aren't touched at load.
export function loadAppIntoContext() {
    const noop = () => {};
    const elementStub = {
        setAttribute: noop,
        getAttribute: () => null,
        addEventListener: noop,
        classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        style: {},
        appendChild: noop,
    };
    const store = {};
    const sandbox = {
        console,
        setTimeout,
        clearTimeout,
        localStorage: {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => {
                store[k] = String(v);
            },
            removeItem: (k) => {
                delete store[k];
            },
        },
        navigator: { language: "en-US" },
        document: {
            documentElement: elementStub,
            body: elementStub,
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            createElement: () => ({ ...elementStub }),
            addEventListener: noop,
        },
        matchMedia: () => ({ matches: false, addEventListener: noop }),
        bootstrap: {},
        fetch: () => Promise.resolve({ ok: true, status: 200, json: async () => ({}) }),
    };
    // window === the global object, mirroring a classic browser script.
    sandbox.window = sandbox;
    vm.createContext(sandbox);

    const source = FILES.map((f) => readFileSync(join(JS_DIR, f), "utf8")).join("\n;\n");
    vm.runInContext(source, sandbox, { filename: "pfm_app_concat.js" });
    return sandbox;
}
