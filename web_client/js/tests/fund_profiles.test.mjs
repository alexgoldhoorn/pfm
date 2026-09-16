// Pure helpers behind the fund profile editor and the overlap card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("regionLabel renders a human label", () => {
    assert.equal(win.regionLabel("north_america"), "North America");
    assert.equal(win.regionLabel("europe_ex_uk"), "Europe ex-UK");
    assert.equal(win.regionLabel("unknown"), "Unclassified");
});

test("regionLabel passes an unexpected key through", () => {
    assert.equal(win.regionLabel("mars"), "mars");
});

test("overlapGroupLabel names each kind", () => {
    assert.equal(win.overlapGroupLabel("consolidation_candidate"), "Consolidation candidate");
    assert.equal(win.overlapGroupLabel("informational"), "Nested (informational)");
    assert.equal(win.overlapGroupLabel("similar"), "Similar exposure");
});

test("normalizeWeights turns percentages into fractions summing to 1", () => {
    const out = win.normalizeWeights({ north_america: 70, japan: 30 });
    assert.equal(out.north_america, 0.7);
    assert.equal(out.japan, 0.3);
});

test("normalizeWeights drops zero and blank entries", () => {
    const out = win.normalizeWeights({ north_america: 100, japan: 0, uk: "" });
    assert.deepEqual(Object.keys(out), ["north_america"]);
});

test("fundProfileValidate accepts a complete profile", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 0.7, japan: 0.3 },
        asset_class: { equity: 1 },
        sectors: { Technology: 1 },
    });
    assert.deepEqual([...problems], []);
});

test("fundProfileValidate rejects regions that miss 100%", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 0.5 },
        asset_class: { equity: 1 },
        sectors: {},
    });
    assert.equal(problems.length, 1);
    assert.match(problems[0], /region/i);
});

test("fundProfileValidate rejects an empty asset class", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 1 },
        asset_class: {},
        sectors: {},
    });
    assert.match(problems[0], /asset class/i);
});

test("fundProfileValidate allows empty sectors", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 1 },
        asset_class: { bond: 1 },
        sectors: {},
    });
    assert.deepEqual([...problems], []);
});

test("fundProfileValidate tolerates rounding within half a point", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 0.333, europe_ex_uk: 0.333, japan: 0.334 },
        asset_class: { equity: 1 },
        sectors: {},
    });
    assert.deepEqual([...problems], []);
});
