// Pure helpers behind the "My own accounts" card and the transfer marker.
// Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

test("spTransferBadge: linked transfer shows only the Transfer badge", () => {
    const { spTransferBadge } = loadAppIntoContext();
    const html = spTransferBadge({
        is_transfer: true, transfer_link_id: 7, transfer_source: "match",
    });
    assert.match(html, /Transfer<\/span>/);
    assert.ok(!html.includes("no counterpart"));
});

test("spTransferBadge: not a transfer renders nothing", () => {
    const { spTransferBadge } = loadAppIntoContext();
    assert.equal(spTransferBadge({ is_transfer: false, transfer_link_id: null }), "");
});

test("spTransferBadge: pattern-flagged without link gets the own-accounts hint", () => {
    const { spTransferBadge } = loadAppIntoContext();
    const html = spTransferBadge({
        is_transfer: true, transfer_link_id: null, transfer_source: "pattern",
    });
    assert.match(html, /Transfer<\/span>/);
    assert.match(html, /no counterpart/);
    assert.match(html, /title="Flagged by your own-accounts list"/);
});

test("spTransferBadge: manual or missing source reads 'Marked by hand'", () => {
    const { spTransferBadge } = loadAppIntoContext();
    const manual = spTransferBadge({
        is_transfer: true, transfer_link_id: null, transfer_source: "manual",
    });
    assert.match(manual, /title="Marked by hand"/);
    const missing = spTransferBadge({ is_transfer: true, transfer_link_id: null });
    assert.match(missing, /title="Marked by hand"/);
    assert.match(missing, /no counterpart/);
});

test("spOwnPatternPreviewText: 0, 1 and many rows", () => {
    const { spOwnPatternPreviewText } = loadAppIntoContext();
    assert.equal(spOwnPatternPreviewText(0), "Would mark 0 rows");
    assert.equal(spOwnPatternPreviewText(1), "Would mark 1 row as a transfer");
    assert.equal(spOwnPatternPreviewText(5), "Would mark 5 rows as transfers");
});

test("spOwnPatternConfirmMessage: singular and plural", () => {
    const { spOwnPatternConfirmMessage } = loadAppIntoContext();
    assert.equal(
        spOwnPatternConfirmMessage(1),
        '1 row will be marked as a transfer. Their current categories will be replaced by "Transfer".'
    );
    assert.equal(
        spOwnPatternConfirmMessage(5),
        '5 rows will be marked as transfers. Their current categories will be replaced by "Transfer".'
    );
});

test("spShouldCreateRule: never for the Transfer category", () => {
    const { spShouldCreateRule } = loadAppIntoContext();
    assert.equal(spShouldCreateRule("Transfer"), false);
    assert.equal(spShouldCreateRule("  transfer "), false);
    assert.equal(spShouldCreateRule("Groceries"), true);
});
