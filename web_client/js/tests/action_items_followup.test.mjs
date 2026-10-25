// Pure helpers behind the server-side follow-up state (checked / snoozed),
// the Diagnostics overview and the "Ask AI" chat hand-offs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();
// Values built inside the app's VM context have another realm's prototypes,
// which strict deep equality rejects; compare their plain JSON instead.
const plain = (x) => JSON.parse(JSON.stringify(x));

const ACKS = {
    "dq:duplicates": { status: "checked", note: "partial fills", at: "2026-10-20T10:00:00+00:00" },
    "goals:3": { status: "snoozed", until: "2026-10-31" },
    "errors:price-update:9": { status: "open" },
};

test("ackIsActive: checked stays, snoozed lasts through its date, open never", () => {
    assert.equal(win.ackIsActive(ACKS["dq:duplicates"], "2027-01-01"), true);
    assert.equal(win.ackIsActive(ACKS["goals:3"], "2026-10-24"), true);
    assert.equal(win.ackIsActive(ACKS["goals:3"], "2026-10-31"), true);
    assert.equal(win.ackIsActive(ACKS["goals:3"], "2026-11-01"), false);
    assert.equal(win.ackIsActive(ACKS["errors:price-update:9"], "2026-10-24"), false);
    assert.equal(win.ackIsActive(null, "2026-10-24"), false);
});

test("activeAckIds / findingStatus follow the same rule", () => {
    assert.deepEqual(plain(win.activeAckIds(ACKS, "2026-10-24").sort()), ["dq:duplicates", "goals:3"]);
    assert.deepEqual(plain(win.activeAckIds(ACKS, "2026-12-01")), ["dq:duplicates"]);
    assert.equal(win.findingStatus(ACKS, "goals:3", "2026-10-24"), "snoozed");
    assert.equal(win.findingStatus(ACKS, "goals:3", "2026-12-01"), "open");
    assert.equal(win.findingStatus(ACKS, "nope", "2026-10-24"), "open");
});

test("annotateActionItems + filterActionItems: status drives the default view", () => {
    const items = [
        { id: "dq:duplicates", severity: "high", title: "dups" },
        { id: "goals:3", severity: "medium", title: "goal" },
        { id: "networth:bank", severity: "low", title: "bank" },
    ];
    const annotated = win.annotateActionItems(items, ACKS, "2026-10-24");
    assert.deepEqual(plain(annotated.map(i => i.status)), ["checked", "snoozed", "open"]);
    assert.equal(annotated[0].ack.note, "partial fills");
    // Input untouched (pure).
    assert.equal(items[0].status, undefined);

    assert.deepEqual(plain(win.filterActionItems(annotated).map(i => i.id)), ["networth:bank"]);
    assert.equal(win.filterActionItems(annotated, { showResolved: true }).length, 3);
    assert.deepEqual(plain(win.filterActionItems(annotated, { showResolved: true, severity: "high" }).map(i => i.id)), ["dq:duplicates"]);
    assert.deepEqual(plain(win.filterActionItems(annotated, { severity: "high" })), []);
    assert.equal(win.actionItemsSummaryText(annotated), "1 open · 1 checked · 1 snoozed");
    assert.equal(win.actionItemsSummaryText([]), "0 open");
});

test("ackLabel names the state and the date", () => {
    assert.match(win.ackLabel(ACKS["dq:duplicates"]), /^checked /);
    assert.match(win.ackLabel(ACKS["goals:3"]), /^snoozed until /);
    assert.equal(win.ackLabel(null), "");
});

test("localIsoDay uses the local calendar day", () => {
    const d = new Date(2026, 9, 24, 23, 30); // 24 Oct local, whatever the zone
    assert.equal(win.localIsoDay(d), "2026-10-24");
});

test("actionItemChatPrompt carries id, severity, title, detail and the earlier note", () => {
    const text = win.actionItemChatPrompt({
        id: "errors:price-update:9", severity: "high", category: "errors",
        title: "2 asset(s) failed to update prices", detail: "Last run: Example Corp (EXM)",
        ack: { status: "checked", note: "delisted" },
    });
    assert.match(text, /Id: errors:price-update:9/);
    assert.match(text, /Severity: high · Category: Errors/);
    assert.match(text, /2 asset\(s\) failed to update prices/);
    assert.match(text, /Detail: Last run: Example Corp \(EXM\)/);
    assert.match(text, /My earlier note: delisted/);
    assert.match(text, /set_action_item_status/);
});

test("actionItemsChatPrompt lists open items only and caps at 20", () => {
    const items = [];
    for (let n = 0; n < 23; n++) items.push({ id: `x:${n}`, severity: "low", title: `Item ${n}`, status: "open" });
    items.push({ id: "done", severity: "high", title: "Checked one", status: "checked" });
    const text = win.actionItemsChatPrompt(items);
    assert.match(text, /I have 23 open action items/);
    assert.match(text, /20\. \[low\] Item 19 \(id x:19\)/);
    assert.match(text, /… and 3 more/);
    assert.doesNotMatch(text, /Checked one/);
    assert.match(win.actionItemsChatPrompt([]), /list is empty/);
});

test("dqFindingChatPrompt: duplicate pair, suspicious row and stale price", () => {
    const dup = win.dqFindingChatPrompt("dup", {
        key: "dup:12:15", label: "likely",
        tx_a: { id: 12, date: "2026-03-01", type: "buy", quantity: 10, price: 100, asset: "EXM", asset_name: "Example Corp", portfolio: "Broker A" },
        tx_b: { id: 15, date: "2026-03-01", type: "buy", quantity: 10, price: 100, asset: "EXM", portfolio: "Broker A" },
    });
    assert.match(dup, /LIKELY/);
    assert.match(dup, /Finding id: dq:dup:12:15/);
    assert.match(dup, /#12 2026-03-01 buy/);
    assert.match(dup, /get_transactions\(symbol="EXM"\)/);

    const susp = win.dqFindingChatPrompt("susp", {
        key: "susp:40:zero_price", severity: "warning", asset: "EXM", asset_name: "Example Corp",
        description: "buy at price 0", date: "2026-03-02", type: "buy", transaction_id: 40,
    });
    assert.match(susp, /Suspicious transaction pattern \(warning\)/);
    assert.match(susp, /transaction #40/);
    assert.match(susp, /Finding id: dq:susp:40:zero_price/);

    const stale = win.dqFindingChatPrompt("stale", { symbol: "EXM", name: "Example Corp", reason: "stale price", price_date: "2026-10-01", age_days: 23 });
    assert.match(stale, /Example Corp \(EXM\) is stale price: last price 2026-10-01, 23 days old/);
});

const STATE = {
    fresh: { refresh_age_hours: 3, stale_count: 2, stale: [{ symbol: "EXM", name: "Example Corp", reason: "stale price", age_days: 6 }] },
    runs: [{ finished_at: "2026-10-23 20:05:00", updated_count: 40, error_count: 1, error_symbols: ["EXM"], api_errors: ["EXM: no data"] }],
    dups: [{ key: "dup:1:2", label: "likely" }, { key: "dup:3:4", label: "possible" }],
    susp: [{ key: "susp:9:zero_price", severity: "warning" }],
    logs: [
        { created_at: "2026-10-24T08:00:00", level: "ERROR" },
        { created_at: "2026-10-24T07:00:00", level: "WARNING" },
        { created_at: "2026-10-20T07:00:00", level: "ERROR" },
    ],
    acks: { "dq:dup:1:2": { status: "checked" } },
};
const NOW = new Date("2026-10-24T12:00:00Z");

test("diagOverviewModel: one chip per area, badges count what is open", () => {
    const { chips, badges } = win.diagOverviewModel(STATE, NOW);
    assert.deepEqual(plain(chips.map(c => c.key)), ["prices", "run", "dq", "logs"]);
    const by = Object.fromEntries(chips.map(c => [c.key, c]));
    assert.equal(by.prices.tone, "warning");
    assert.match(by.prices.value, /2 stale/);
    assert.equal(by.run.tone, "danger");
    assert.match(by.run.value, /40 updated · 1 failed/);
    // one of three findings checked → two open, the open LIKELY dup makes it serious
    assert.equal(by.dq.tone, "danger");
    assert.equal(by.dq.value, "2 open findings · 1 checked");
    assert.equal(by.logs.tone, "danger");
    assert.equal(by.logs.value, "1 error · 1 warning");
    assert.deepEqual(plain(badges), { price: 3, dq: 2, logs: 2 });
});

test("diagOverviewModel: quiet state is all green, missing data is grey", () => {
    const quiet = {
        fresh: { refresh_age_hours: 5, stale_count: 0, stale: [] },
        runs: [{ finished_at: "2026-10-23 20:05:00", updated_count: 40, error_count: 0 }],
        dups: [{ key: "dup:1:2", label: "likely" }], susp: [], logs: [],
        acks: { "dq:dup:1:2": { status: "snoozed", until: "2026-12-01" } },
    };
    const { chips, badges } = win.diagOverviewModel(quiet, NOW);
    assert.ok(chips.every(c => c.tone === "success"), JSON.stringify(chips));
    assert.equal(chips[2].value, "all 1 checked");
    assert.deepEqual(plain(badges), { price: 0, dq: 0, logs: 0 });

    const empty = win.diagOverviewModel({}, NOW);
    assert.equal(empty.chips[0].tone, "secondary");
    assert.equal(empty.chips[1].value, "no runs yet");
    assert.equal(empty.chips[2].value, "no findings");
});

test("utcDate treats a bare app_logs timestamp as UTC", () => {
    assert.equal(win.utcDate("2026-10-24T08:00:00").toISOString(), "2026-10-24T08:00:00.000Z");
    assert.equal(win.utcDate("2026-10-24 08:00:00").toISOString(), "2026-10-24T08:00:00.000Z");
    assert.equal(win.utcDate("2026-10-24T08:00:00+02:00").toISOString(), "2026-10-24T06:00:00.000Z");
    assert.equal(win.utcDate(""), null);
});

test("diagnosticsChatPrompt summarises the chips, stale holdings and failed symbols", () => {
    const text = win.diagnosticsChatPrompt(STATE, NOW);
    assert.match(text, /- Prices aging: /);
    assert.match(text, /- Data quality: 2 open findings · 1 checked/);
    assert.match(text, /Example Corp \(EXM\): stale price, 6 days/);
    assert.match(text, /Failed in the last price update: EXM/);
    assert.match(text, /EXM: no data/);
    assert.match(text, /get_diagnostics/);
});
