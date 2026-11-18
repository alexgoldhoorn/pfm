// Pure helpers behind cost control: comparison, unusual charges, tiers, pace,
// yearly funds and the cash-flow forecast. Invented merchants and round
// amounts only. Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const w = loadAppIntoContext();
// Objects built inside the vm context have another prototype: compare as JSON.
const plain = (v) => JSON.parse(JSON.stringify(v));

// ── money ───────────────────────────────────────────────────────────────────

test("ccMoney: a missing figure reads 'not available', never zero", () => {
    assert.equal(w.ccMoney(null), "not available");
    assert.equal(w.ccMoney(undefined), "not available");
    assert.equal(w.ccMoney(Number.NaN), "not available");
    assert.match(w.ccMoney(0), /0/);
    assert.notEqual(w.ccMoney(0), "not available");
});

test("ccSignedMoney: sign, zero and missing", () => {
    assert.match(w.ccSignedMoney(120), /^\+/);
    assert.match(w.ccSignedMoney(-120), /^−/);
    assert.ok(!/^[+−]/.test(w.ccSignedMoney(0)));
    assert.equal(w.ccSignedMoney(null), "not available");
});

// ── comparison ──────────────────────────────────────────────────────────────

const completeMonth = {
    month: "2026-10",
    status: "complete",
    data_through: "2026-11-02",
    baseline_months: ["2026-07", "2026-08", "2026-09"],
    warnings: [],
    totals: { actual_eur: 1200, baseline_eur: 1000, last_year_eur: 900, delta_eur: 200, delta_pct: 20, one_off_eur: 150, savings_eur: 300 },
    items: [
        { name: "Groceries", tier: "flexible", actual_eur: 500, baseline_eur: 400, last_year_eur: 380, delta_eur: 100, delta_pct: 25, one_off_eur: 0 },
        { name: "Hobbies", tier: "optional", actual_eur: 100, baseline_eur: 150, last_year_eur: null, delta_eur: -50, delta_pct: -33.3, one_off_eur: 0 },
        { name: "Repairs", tier: null, actual_eur: 300, baseline_eur: 0, last_year_eur: 0, delta_eur: 300, delta_pct: null, one_off_eur: 150 },
    ],
};

test("compareStatusMeta: only a complete month compares", () => {
    assert.equal(w.compareStatusMeta("complete").compares, true);
    for (const s of ["in_progress", "partial", "no_imports", "something_new"]) {
        assert.equal(w.compareStatusMeta(s).compares, false, s);
    }
});

test("compareDeltaClass: lower is green, higher red, unknown neutral", () => {
    assert.equal(w.compareDeltaClass(-10), "text-success");
    assert.equal(w.compareDeltaClass(10), "text-danger");
    assert.equal(w.compareDeltaClass(0), "text-muted");
    assert.equal(w.compareDeltaClass(null), "text-muted");
    assert.equal(w.compareDeltaClass(undefined), "text-muted");
});

test("compareRowsModel: a complete month shows deltas, colours and the savings line apart", () => {
    const m = w.compareRowsModel(completeMonth);
    assert.equal(m.compares, true);
    assert.equal(m.rows.length, 3);
    assert.equal(m.rows[0].deltaClass, "text-danger");
    assert.equal(m.rows[1].deltaClass, "text-success");
    assert.match(m.rows[0].deltaPct, /25\.0%/);
    // a null percentage (no baseline) is a dash, not 0%
    assert.equal(m.rows[2].deltaPct, "—");
    assert.equal(m.rows[1].lastYear, "not available");
    assert.ok(m.totals.savings);
    assert.ok(m.totals.oneOff);
});

test("compareRowsModel: an in-progress month never shows a delta or a green figure", () => {
    const m = w.compareRowsModel({
        month: "2026-11",
        status: "in_progress",
        warnings: ["2026-11 is still in progress (data through 2026-11-10): no comparison is made."],
        totals: { actual_eur: 300, baseline_eur: 1000, last_year_eur: null, delta_eur: null, delta_pct: null, one_off_eur: 0, savings_eur: 0 },
        items: [{ name: "Groceries", tier: "flexible", actual_eur: 200, baseline_eur: 400, last_year_eur: null, delta_eur: null, delta_pct: null, one_off_eur: 0 }],
    });
    assert.equal(m.compares, false);
    assert.equal(m.totals.delta, "—");
    assert.equal(m.totals.deltaClass, "text-muted");
    assert.equal(m.rows[0].delta, "—");
    assert.equal(m.rows[0].deltaClass, "text-muted");
    assert.match(m.notice, /in progress/);
});

test("compareRowsModel: nothing imported has no figures at all", () => {
    const m = w.compareRowsModel({
        month: "2026-09", status: "no_imports", warnings: ["Nothing is imported for 2026-09: no figures are shown."],
        totals: { actual_eur: null, baseline_eur: 0, last_year_eur: null, delta_eur: null, delta_pct: null, one_off_eur: 0, savings_eur: null },
        items: [],
    });
    assert.equal(m.totals.actual, "not available");
    assert.equal(m.totals.savings, null);
    assert.equal(m.rows.length, 0);
});

test("compareRowsModel: the row limit reports what it hid", () => {
    const m = w.compareRowsModel(completeMonth, 2);
    assert.equal(m.rows.length, 2);
    assert.equal(m.hidden, 1);
    assert.equal(w.compareRowsModel(completeMonth, 0).hidden, 0);
});

test("month picker helpers: newest first, default is the month before", () => {
    const months = plain(w.ccMonthChoices("2026-11", 4));
    assert.deepEqual(months, ["2026-11", "2026-10", "2026-09", "2026-08"]);
    assert.equal(w.ccPreviousMonth("2026-01"), "2025-12");
    assert.equal(w.ccPreviousMonth("2026-11"), "2026-10");
});

// ── unusual charges ─────────────────────────────────────────────────────────

test("anomalyKindMeta: known kinds have labels, unknown ones still render", () => {
    assert.equal(w.anomalyKindMeta("large_charge").label, "Large charge");
    assert.equal(w.anomalyKindMeta("possible_duplicate").label, "Possible duplicate");
    assert.equal(w.anomalyKindMeta("brand_new").label, "brand_new");
});

test("anomalyVisibleItems: checked and active snoozes are hidden, expired ones are back", () => {
    const items = [{ id: "anomaly:large:1" }, { id: "anomaly:large:2" }, { id: "anomaly:large:3" }, { id: "anomaly:large:4" }];
    const acks = {
        "anomaly:large:1": { status: "checked" },
        "anomaly:large:2": { status: "snoozed", until: "2026-12-01" },
        "anomaly:large:3": { status: "snoozed", until: "2026-10-01" },
    };
    const ids = w.anomalyVisibleItems(items, acks, "2026-11-15").map((i) => i.id);
    assert.deepEqual(plain(ids), ["anomaly:large:3", "anomaly:large:4"]);
    assert.equal(w.anomalyVisibleItems(items, null, "2026-11-15").length, 4);
});

test("anomalyOneOffIds / label: a category spike has no rows to flag", () => {
    assert.equal(w.anomalyOneOffIds({ transaction_ids: [] }).length, 0);
    assert.equal(w.anomalyOneOffIds({}).length, 0);
    assert.deepEqual(plain(w.anomalyOneOffIds({ transaction_ids: [4, 9] })), [4, 9]);
    assert.equal(w.anomalyOneOffLabel({ transaction_ids: [4] }), "Mark one-off");
});

test("anomalyCanMarkOneOff: not for a possible duplicate, not without rows", () => {
    assert.equal(w.anomalyCanMarkOneOff({ kind: "large_charge", transaction_ids: [4] }), true);
    assert.equal(w.anomalyCanMarkOneOff({ kind: "new_merchant", transaction_ids: [4] }), true);
    assert.equal(w.anomalyCanMarkOneOff({ kind: "possible_duplicate", transaction_ids: [4, 9] }), false);
    assert.equal(w.anomalyCanMarkOneOff({ kind: "category_spike", transaction_ids: [] }), false);
    assert.equal(w.anomalyCanMarkOneOff(null), false);
});

// ── tiers ───────────────────────────────────────────────────────────────────

const tiers = [
    { month: "2026-08", has_imports: false, in_progress: false, fixed_eur: 0, flexible_eur: 0, optional_eur: 0, unclassified_eur: 0, total_eur: 0, savings_eur: 0, top_categories: {} },
    { month: "2026-09", has_imports: true, in_progress: false, fixed_eur: 800, flexible_eur: 400, optional_eur: 100, unclassified_eur: 50, total_eur: 1350, savings_eur: 200, top_categories: { fixed: [{ name: "Rent", amount_eur: 700 }], flexible: [{ name: "Groceries", amount_eur: 300 }], optional: [], unclassified: [] } },
    { month: "2026-10", has_imports: true, in_progress: true, fixed_eur: 100, flexible_eur: 50, optional_eur: 0, unclassified_eur: 0, total_eur: 150, savings_eur: 0, top_categories: {} },
];

test("tierChartModel: colour follows the tier and a month without imports is a gap", () => {
    const m = w.tierChartModel(tiers);
    assert.deepEqual(plain(m.datasets.map((d) => d.tier)), ["fixed", "flexible", "optional", "unclassified"]);
    assert.deepEqual(plain(m.datasets.map((d) => d.cssVar)), ["--viz-1", "--viz-3", "--viz-4", "--viz-neutral"]);
    assert.equal(m.datasets[0].data[0], null);
    assert.equal(m.datasets[0].data[1], 800);
    assert.match(m.labels[2], /\(so far\)/);
    assert.equal(m.hasData, true);
    assert.equal(w.tierChartModel(tiers.slice(0, 1)).hasData, false);
});

test("tierTopModel: prefers the latest complete month", () => {
    const top = w.tierTopModel(tiers);
    assert.equal(top.month, "2026-09");
    assert.equal(top.inProgress, false);
    assert.equal(top.columns[0].items[0].name, "Rent");
    assert.equal(w.tierTopModel(tiers.slice(0, 1)), null);
    assert.equal(w.tierTopModel(tiers.slice(1, 3).map((t, i) => (i === 0 ? { ...t, has_imports: false } : t))).inProgress, true);
});

test("tierTotalsModel drops empty tiers; tierBadgeHtml is empty without a tier", () => {
    const rows = w.tierTotalsModel({
        fixed: { planned_eur: 100, actual_eur: 90 },
        flexible: { planned_eur: 0, actual_eur: 0 },
        unclassified: { planned_eur: 0, actual_eur: 40 },
    });
    assert.deepEqual(plain(rows.map((r) => r.tier)), ["fixed", "unclassified"]);
    assert.equal(w.tierBadgeHtml(null), "");
    assert.equal(w.tierBadgeHtml("unclassified"), "");
    assert.match(w.tierBadgeHtml("fixed"), /Fixed/);
});

// ── pace, funds ─────────────────────────────────────────────────────────────

test("paceMeta: null without a pace, a status badge and sentence with one", () => {
    assert.equal(w.paceMeta(null), null);
    assert.equal(w.paceMeta(undefined), null);
    const ok = w.paceMeta({ status: "on_track", projected_month_end_eur: 380, limit_eur: 400, actual_to_date_eur: 150, data_through: "2026-11-10" });
    assert.equal(ok.label, "On track");
    assert.match(ok.badge, /success/);
    assert.match(ok.text, /Heading for/);
    const risk = w.paceMeta({ status: "at_risk", projected_month_end_eur: 520, limit_eur: 400, actual_to_date_eur: 250 });
    assert.match(risk.badge, /warning/);
    const over = w.paceMeta({ status: "over", projected_month_end_eur: 700, limit_eur: 400, actual_to_date_eur: 450 });
    assert.match(over.badge, /danger/);
    assert.match(over.text, /Already over/);
    assert.match(over.short, /450 spent, → .*700 of .*400/);
    assert.match(ok.short, /^→ .*380 of .*400$/);
    assert.doesNotMatch(ok.short, /Data through/);
});

test("paceMeta: too_early has no projection and says so", () => {
    const m = w.paceMeta({ status: "too_early", projected_month_end_eur: null, limit_eur: 400, actual_to_date_eur: 150, data_through: "2026-11-03" });
    assert.equal(m.label, "Too early");
    assert.match(m.badge, /secondary/);
    assert.equal(m.projected, "—");
    assert.match(m.text, /Too early to project: .*150 spent so far of .*400/);
    assert.doesNotMatch(m.text, /Heading for/);
});

test("paceMeta: an unknown projection reads 'not available'", () => {
    const m = w.paceMeta({ status: "on_track", projected_month_end_eur: null, limit_eur: 400 });
    assert.match(m.text, /not available/);
});

test("fundText: the rolling 12 months, spent of the yearly amount and what is left", () => {
    assert.equal(w.fundText(null), null);
    const t = w.fundText({ yearly_amount_eur: 500, spent_eur: 200, remaining_eur: 300, window_start: "2025-12", window_end: "2026-11" });
    assert.match(t, /^Last 12 months: spent .*200.* of .*500.*, .*300.* left$/);
});

test("budgetLinePlan: available when the line carries, planned otherwise", () => {
    assert.equal(w.budgetLinePlan({ planned_total: 100, available_total: 160 }), 160);
    assert.equal(w.budgetLinePlan({ planned_total: 100, available_total: null }), 100);
    assert.equal(w.budgetLinePlan({ planned_total: 100 }), 100);
    assert.equal(w.budgetPlanDiffers({ planned_total: 100, available_total: 160 }), true);
    assert.equal(w.budgetPlanDiffers({ planned_total: 100, available_total: 100 }), false);
});

test("yearly amounts: a twelfth a month, labelled per year", () => {
    assert.equal(w.budgetMonthlyShare(1200, "yearly"), 100);
    assert.equal(w.budgetMonthlyShare(1200, "monthly"), 1200);
    assert.equal(w.budgetMonthlyShare(1200, undefined), 1200);
    assert.equal(w.budgetAmountSuffix("yearly"), "per year");
    assert.equal(w.budgetAmountSuffix("monthly"), "per month");
});

test("budgetFundControlsAllowed: spending and debt only", () => {
    assert.equal(w.budgetFundControlsAllowed("spending"), true);
    assert.equal(w.budgetFundControlsAllowed("debt"), true);
    assert.equal(w.budgetFundControlsAllowed("income"), false);
    assert.equal(w.budgetFundControlsAllowed("investment"), false);
});

test("expandBudgetLineMonths: a yearly line previews a twelfth, an override replaces it", () => {
    const months = ["2026-01", "2026-02"];
    const out = plain(w.expandBudgetLineMonths({ monthly_amount: 1200, period: "yearly", overrides: { "2026-02": 0 } }, months));
    assert.deepEqual(out, { "2026-01": 100, "2026-02": 0 });
    const monthly = plain(w.expandBudgetLineMonths({ monthly_amount: 50, overrides: {} }, months));
    assert.deepEqual(monthly, { "2026-01": 50, "2026-02": 50 });
});

// ── forecast ────────────────────────────────────────────────────────────────

const forecast = {
    as_of: "2026-11-15",
    data_through: "2026-11-10",
    accounts: [
        { portfolio_id: 1, name: "Example Bank", data_through: "2026-11-10", days_behind: 5, stale: false, balance_eur: 2000, balance_date: "2026-11-10" },
        { portfolio_id: 2, name: "Old Account", data_through: null, days_behind: null, stale: true, balance_eur: null, balance_date: null },
    ],
    this_month: {
        month: "2026-11", status: "ok", data_through: "2026-11-10", elapsed_share: 0.333,
        spent_to_date_eur: 700, income_to_date_eur: 2500, recurring_remaining_eur: 300, variable_remaining_eur: 500,
        income_remaining_eur: 0, projected_spending_eur: 1500, projected_income_eur: 2500, projected_net_eur: 1000,
        range: { spending_low_eur: 1400, spending_high_eur: 1700, net_low_eur: 800, net_high_eur: 1100 },
        projected_balance_end_eur: 2600,
    },
    next_months: [
        { month: "2026-12", income_eur: 2500, recurring_eur: 800, variable_eur: 700, savings_eur: 200, net_eur: 1000, projected_balance_eur: 3400, balance_below_zero: false, known_bill_count: 3 },
        { month: "2027-01", income_eur: null, recurring_eur: 800, variable_eur: null, savings_eur: null, net_eur: null, projected_balance_eur: null, balance_below_zero: false, known_bill_count: 2 },
        { month: "2027-02", income_eur: 2500, recurring_eur: 800, variable_eur: 700, savings_eur: 0, net_eur: -50, projected_balance_eur: -20, balance_below_zero: true, known_bill_count: 1 },
    ],
    known_bills: [
        { date: "2026-11-20", merchant: "Example Telecom", amount_eur: 40, cadence: "monthly", account_name: "Example Bank", kind: "spending" },
        { date: "2026-12-01", merchant: "Example Fund Plan", amount_eur: 200, cadence: "monthly", account_name: "Example Bank", kind: "savings" },
    ],
    first_below_zero: "2027-02",
    warnings: [],
    assumptions: ["Amounts are in EUR."],
};

test("forecastAccountsModel: stale flag and the data-through text", () => {
    const rows = w.forecastAccountsModel(forecast);
    assert.equal(rows.length, 2);
    assert.match(rows[0].text, /data through .* \(5 days ago\)/);
    assert.equal(rows[0].stale, false);
    assert.equal(rows[1].stale, true);
    assert.equal(rows[1].text, "no statement imported");
    assert.equal(rows[1].balance, "not available");
});

test("forecastThisMonthModel: figures, range and a banner for the odd statuses", () => {
    const ok = w.forecastThisMonthModel(forecast);
    assert.equal(ok.banner, null);
    assert.equal(ok.lines.length, 7);
    assert.ok(ok.rangeText);
    assert.equal(ok.netClass, "text-success");

    const none = w.forecastThisMonthModel({ this_month: { month: "2026-11", status: "no_imports", projected_spending_eur: null, range: {} } });
    assert.match(none.banner, /Nothing is imported/);
    assert.equal(none.lines.find((l) => l.label === "Projected spending").text, "not available");
    assert.equal(none.lines.find((l) => l.label === "Projected spending").missing, true);
    assert.equal(none.rangeText, null);

    const base = w.forecastThisMonthModel({ this_month: { month: "2026-11", status: "no_baseline", range: {} } });
    assert.match(base.banner, /no baseline/);
});

test("forecastTableRows: null months read 'not available' and a negative balance is flagged", () => {
    const rows = w.forecastTableRows(forecast);
    assert.equal(rows.length, 3);
    assert.equal(rows[0].below, false);
    assert.equal(rows[1].income, "not available");
    assert.equal(rows[1].net, "not available");
    assert.equal(rows[1].balance, "not available");
    assert.equal(rows[1].netClass, "text-muted");
    assert.equal(rows[2].below, true);
    assert.equal(rows[2].netClass, "text-danger");
    assert.equal(rows[0].billCount, 3);
});

test("forecastBillRows: caps and counts the rest, marks savings", () => {
    const all = w.forecastBillRows(forecast, 0);
    assert.equal(all.rows.length, 2);
    assert.equal(all.hidden, 0);
    assert.equal(all.rows[1].savings, true);
    const capped = w.forecastBillRows(forecast, 1);
    assert.equal(capped.rows.length, 1);
    assert.equal(capped.hidden, 1);
    assert.equal(w.forecastBillRows({}, 5).rows.length, 0);
});

test("forecastBalancePoints: this month's end first, unknown balances stay null", () => {
    const pts = w.forecastBalancePoints(forecast);
    assert.equal(pts.length, 4);
    assert.match(pts[0].label, /\(end\)/);
    assert.equal(pts[0].value, 2600);
    assert.equal(pts[2].value, null);
    assert.deepEqual(plain(w.forecastBalancePoints(null)), []);
    assert.equal(w.forecastBalancePoints({ this_month: { month: "2026-11" }, next_months: [] })[0].value, null);
});

test("forecastBelowZeroText: only when the backend names a month", () => {
    assert.match(w.forecastBelowZeroText(forecast), /below zero/);
    assert.equal(w.forecastBelowZeroText({ first_below_zero: null }), null);
    assert.equal(w.forecastBelowZeroText(null), null);
});

// ── one-off flag in the Transactions table ──────────────────────────────────

test("spOneOffBadgeHtml / spOneOffButtonHtml reflect the flag", () => {
    assert.equal(w.spOneOffBadgeHtml({ one_off: false }), "");
    assert.match(w.spOneOffBadgeHtml({ one_off: true }), /one-off/);
    assert.match(w.spOneOffButtonHtml({ id: 7, one_off: true }), /data-id="7"[^>]*>|data-id="7"/);
    assert.match(w.spOneOffButtonHtml({ id: 7, one_off: true }), /bi-flag-fill/);
    assert.ok(!/bi-flag-fill/.test(w.spOneOffButtonHtml({ id: 7, one_off: false })));
});

test("serverTextHtml: server EUR amounts take the page locale and the privacy blur", () => {
    const prev = w.PREFS.numberLocale;
    w.PREFS.numberLocale = "de-DE";
    try {
        const money = (v, d) => `<span class="pfm-amt">${w.Fmt.money(v, "EUR", d)}</span>`;
        assert.equal(
            w.serverTextHtml("1,234.56 EUR at Shop; the usual charge there is 27.68 EUR."),
            `${money(1234.56, 2)} at Shop; the usual charge there is ${money(27.68, 2)}.`,
        );
        assert.match(w.serverTextHtml("27.68 EUR"), /27,68/);
        // Signed whole amounts ({:+,.0f}) keep the plus; minus becomes a negative amount.
        assert.equal(w.serverTextHtml("changed +12,000 EUR"), `changed <span class="pfm-amt">+${w.Fmt.money(12000, "EUR", 0)}</span>`);
        assert.equal(w.serverTextHtml("-350 EUR"), money(-350, 0));
        // Text is still escaped; numbers that are not EUR amounts are left alone.
        assert.equal(w.serverTextHtml("<b>v2.5 EUR</b> 10 USD"), "&lt;b&gt;v2.5 EUR&lt;/b&gt; 10 USD");
        assert.equal(w.serverTextHtml(null), "");
    } finally {
        w.PREFS.numberLocale = prev;
    }
});

test("serverTextHtml: ISO dates follow the date setting, months are spelled out", () => {
    const prev = [w.PREFS.dateFormat, w.PREFS.numberLocale];
    w.PREFS.dateFormat = "dmy";
    w.PREFS.numberLocale = "en-GB";
    try {
        assert.equal(
            w.serverTextHtml("Shop on 2026-11-05 and again on 2026-11-08."),
            "Shop on 05-11-2026 and again on 08-11-2026.",
        );
        assert.equal(w.serverTextHtml("Groceries came to 10 EUR in 2026-10;").replace(/<[^>]+>/g, ""),
            `Groceries came to ${w.Fmt.money(10, "EUR", 0)} in October 2026;`);
        // Not a date: ids, out-of-range months, longer digit runs.
        assert.equal(w.serverTextHtml("id dq:2026-13 x12026-11-05 2026-11-05T10"), "id dq:2026-13 x12026-11-05 2026-11-05T10");
    } finally {
        [w.PREFS.dateFormat, w.PREFS.numberLocale] = prev;
    }
});
