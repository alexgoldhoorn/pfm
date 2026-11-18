// The installed app's manifest shortcuts and share target: launch parameters,
// the account guess, the result model and the service worker's share handler.
// Invented data. Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";
import { loadAppIntoContext } from "./helpers.mjs";

const WEB_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (name) => readFileSync(join(WEB_DIR, name), "utf8");
const w = loadAppIntoContext();
const plain = (v) => JSON.parse(JSON.stringify(v));

// ── Launch parameters ───────────────────────────────────────────────────────

test("parseLaunchParams: shortcut page and share flag, anything else ignored", () => {
    assert.deepEqual(plain(w.parseLaunchParams("?page=spending")), { page: "spending", shared: null });
    assert.deepEqual(plain(w.parseLaunchParams("?shared=file")), { page: null, shared: "file" });
    assert.deepEqual(plain(w.parseLaunchParams("")), { page: null, shared: null });
    assert.deepEqual(plain(w.parseLaunchParams(null)), { page: null, shared: null });
    // Not a plain page name: never reaches a selector.
    assert.equal(w.parseLaunchParams('?page=x"],[data-y="').page, null);
    assert.equal(w.parseLaunchParams("?page=Spending").page, null);
    assert.equal(w.parseLaunchParams("?shared=1").shared, null);
});

// ── Manifest ────────────────────────────────────────────────────────────────

test("manifest: every shortcut opens a page that exists", () => {
    const manifest = JSON.parse(read("manifest.json"));
    const html = read("index.html");
    assert.ok(manifest.shortcuts.length >= 1);
    for (const s of manifest.shortcuts) {
        const page = w.parseLaunchParams(s.url.slice(s.url.indexOf("?"))).page;
        assert.ok(page, `shortcut ${s.name} has no usable ?page=`);
        assert.ok(html.includes(`id="${page}Page"`), `no #${page}Page for shortcut ${s.name}`);
    }
});

test("manifest: the share target matches the service worker, nginx and the page", () => {
    const target = JSON.parse(read("manifest.json")).share_target;
    assert.equal(target.method, "POST");
    assert.equal(target.enctype, "multipart/form-data");
    assert.equal(target.params.files[0].name, "file");
    const sw = read("sw.js");
    assert.ok(sw.includes(`const SHARE_PATH = '${target.action}';`));
    assert.ok(read("nginx.conf").includes(`location = ${target.action} {`));
    // The page reads the cache entry the worker writes.
    const features = read("js/pfm_features.js");
    for (const name of ["SHARE_CACHE", "SHARE_KEY"]) {
        const line = sw.split("\n").find((l) => l.startsWith(`const ${name} = `));
        assert.ok(line, `${name} missing in sw.js`);
        assert.ok(features.includes(line), `${name} differs between sw.js and pfm_features.js`);
    }
});

// ── Account guess ───────────────────────────────────────────────────────────

const accounts = [
    { id: 1, name: "North Bank", account_type: "bank" },
    { id: 2, name: "North Savings", account_type: "bank" },
    { id: 3, name: "Example Broker", account_type: "brokerage" },
    { id: 4, name: "Río Capital", account_type: "brokerage" },
];

test("guessShareAccount: whole name first, then one unique word, else nothing", () => {
    assert.equal(w.guessShareAccount("north_bank_2026-10.csv", accounts), 1);
    assert.equal(w.guessShareAccount("NorthSavings-export.csv", accounts), 2);
    assert.equal(w.guessShareAccount("examplebroker.csv", accounts), 3);
    // One word of the name, accents and case ignored.
    assert.equal(w.guessShareAccount("RIO-movimientos.csv", accounts), null);
    assert.equal(w.guessShareAccount("capital_statement.csv", accounts), 4);
    assert.equal(w.guessShareAccount("broker-trades.csv", accounts), 3);
    // "north" fits two accounts: no guess.
    assert.equal(w.guessShareAccount("north.csv", accounts), null);
    assert.equal(w.guessShareAccount("movimientos.csv", accounts), null);
    assert.equal(w.guessShareAccount("", accounts), null);
    assert.equal(w.guessShareAccount("north_bank.csv", []), null);
});

// ── Result model ────────────────────────────────────────────────────────────

test("shareImportResultModel: bank statement", () => {
    const m = plain(w.shareImportResultModel(accounts[0], {
        parsed: 12, imported: 9, duplicates: 3, uncategorized: 2,
        date_from: "2026-10-01", date_to: "2026-10-31", balance_check: "ok", warnings: [],
    }));
    assert.equal(m.title, "Bank statement imported");
    assert.equal(m.level, "success");
    assert.deepEqual(m.chips.map((c) => [c.value, c.label]), [[9, "rows"], [3, "duplicates skipped"], [2, "to categorise"]]);
    assert.equal(m.facts[0].value, "North Bank");
    assert.ok(!m.lists.some((l) => l.title === "Warnings"));
});

test("shareImportResultModel: warnings are listed and lower the level", () => {
    const m = plain(w.shareImportResultModel(accounts[0], {
        imported: 4, duplicates: 0, uncategorized: 0, date_from: "2026-10-01", date_to: "2026-10-05",
        warnings: ["1 stated balance(s) do not add up: rows may be missing"],
    }));
    assert.equal(m.level, "warning");
    const list = m.lists.find((l) => l.title === "Warnings");
    assert.equal(list.items.length, 1);
    assert.equal(list.open, true);
});

test("shareImportResultModel: broker file", () => {
    const m = plain(w.shareImportResultModel(accounts[2], {
        portfolio_name: "Example Broker", imported: 5, bookings_imported: 2, duplicates_skipped: 1,
        bookings_to_review: 1, new_assets: 2, first_date: "2026-09-01", last_date: "2026-09-30", warnings: [],
    }));
    assert.equal(m.title, "Import complete");
    assert.deepEqual(m.chips.map((c) => [c.value, c.label]), [
        [5, "transactions"], [2, "cash movements"], [1, "duplicates skipped"],
        [2, "new assets"], [1, "cash movements to review"],
    ]);
    assert.ok(m.facts.some((f) => f.label === "Account" && f.value === "Example Broker"));
    assert.match(m.note, /New assets/);
});

test("shareImportResultModel: everything already stored", () => {
    const m = plain(w.shareImportResultModel(accounts[2], { imported: 0, duplicates_skipped: 7, warnings: [] }));
    assert.equal(m.title, "Nothing new to import");
    assert.equal(m.level, "info");
});

// ── Service worker ──────────────────────────────────────────────────────────

function loadServiceWorker() {
    const listeners = {};
    const stored = new Map();
    const passed = [];
    const sandbox = {
        URL, Response, Request, FormData, File,
        self: {
            location: { origin: "https://pfm.example" },
            addEventListener: (type, fn) => { listeners[type] = fn; },
            skipWaiting: () => {},
            clients: { claim: async () => {} },
        },
        caches: { open: async () => ({ put: async (key, resp) => { stored.set(key, resp); } }) },
        fetch: (req) => { passed.push(req); return Promise.resolve(new Response("passed")); },
    };
    vm.createContext(sandbox);
    vm.runInContext(read("sw.js"), sandbox, { filename: "sw.js" });
    const dispatch = async (request) => {
        let answer;
        listeners.fetch({ request, respondWith: (p) => { answer = p; } });
        return answer;
    };
    return { dispatch, stored, passed };
}

function shareRequest(files) {
    const form = new FormData();
    files.forEach((f) => form.append("file", f));
    return new Request("https://pfm.example/share-target", { method: "POST", body: form });
}

test("service worker: a shared file is parked and the app opened with ?shared=file", async () => {
    const sw = loadServiceWorker();
    const csv = "date,description,amount\n2026-10-01,Example shop,-10\n";
    const resp = await sw.dispatch(shareRequest([
        new File([csv], "north bank öct.csv", { type: "text/csv" }),
        new File(["x"], "second.csv", { type: "text/csv" }),
    ]));
    assert.equal(resp.status, 303);
    assert.equal(resp.headers.get("location"), "https://pfm.example/?shared=file");
    const parked = sw.stored.get("/_shared-file");
    assert.equal(decodeURIComponent(parked.headers.get("X-Share-Name")), "north bank öct.csv");
    assert.equal(parked.headers.get("X-Share-Count"), "2");
    assert.equal(await parked.text(), csv);
    assert.equal(sw.passed.length, 0);
});

test("service worker: a share without a file stores nothing", async () => {
    const sw = loadServiceWorker();
    const resp = await sw.dispatch(shareRequest([]));
    assert.equal(resp.headers.get("location"), "https://pfm.example/?shared=none");
    assert.equal(sw.stored.size, 0);
});

test("service worker: everything else goes to the network untouched", async () => {
    const sw = loadServiceWorker();
    const get = new Request("https://pfm.example/api/v1/portfolios/");
    assert.equal(await (await sw.dispatch(get)).text(), "passed");
    // A POST to the same path on another origin is not a share.
    const other = new Request("https://other.example/share-target", { method: "POST", body: "x" });
    await sw.dispatch(other);
    assert.deepEqual(sw.passed, [get, other]);
});
