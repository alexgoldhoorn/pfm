// Pure renderers behind the Net Worth balance sheet.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

const line = (over) => ({
    id: "l1", label: "Example Account", group: "cash", amount_eur: 1000,
    amount: 1000, currency: "EUR", source: "manual", as_of: "2026-10-01",
    age_days: 3, max_age_days: 90, stale: false, note: null,
    edit: { kind: "manual", id: 7 }, in_total: true, ...over,
});

const sheet = () => ({
    as_of: "2026-10-18",
    totals: {
        assets_eur: 11000, liabilities_eur: 2000, net_worth_eur: 9000,
        financial_assets_eur: 6000, liquid_eur: 1000, stale_lines: 1,
    },
    groups: [
        { key: "cash", label: "Cash", total_eur: 1000, lines: [line({})] },
        {
            key: "liabilities", label: "Liabilities", total_eur: -2000,
            lines: [line({
                id: "l2", label: "Example Loan", group: "liabilities",
                amount_eur: -2000, source: "loan_schedule",
                edit: { kind: "loan", id: 3 },
            })],
        },
    ],
});

test("renders group subtotals and totals footer", () => {
    const rows = win.renderBalanceSheetRows(sheet());
    assert.match(rows, /Cash/);
    assert.match(rows, /Liabilities/);
    assert.match(rows, /Example Loan/);
    assert.match(rows, /editLoan\(3\)/);
    assert.match(rows, /editManualAssetAmount\(7\)/);
    const foot = win.renderBalanceSheetTotals(sheet().totals);
    assert.match(foot, /Financial assets/);
    assert.match(foot, /Net worth/);
});

test("stale line gets table-warning and its note", () => {
    const s = sheet();
    s.groups[0].lines[0] = line({ stale: true, note: "Last updated 120 days ago" });
    const rows = win.renderBalanceSheetRows(s);
    assert.match(rows, /table-warning/);
    assert.match(rows, /title="Last updated 120 days ago"/);
});

test("bank line without amount shows n/a not 0", () => {
    const s = sheet();
    s.groups[0].lines[0] = line({
        label: "Example Bank", amount_eur: null, amount: null,
        source: "bank_statement", edit: { kind: "bank", id: 2 },
    });
    const rows = win.renderBalanceSheetRows(s);
    assert.match(rows, /n\/a/);
    assert.doesNotMatch(rows, /Example Bank[\s\S]*€\s?0/);
});

test("escapes labels", () => {
    const s = sheet();
    s.groups[0].lines[0] = line({ label: "<b>x</b>", note: '"><img src=x>' });
    const rows = win.renderBalanceSheetRows(s);
    assert.match(rows, /&lt;b&gt;/);
    assert.doesNotMatch(rows, /<b>x/);
    assert.doesNotMatch(rows, /<img/);
});

test("not-in-total lines are marked", () => {
    const s = sheet();
    s.groups[0].lines[0] = line({ in_total: false });
    assert.match(win.renderBalanceSheetRows(s), /not in total/);
});
