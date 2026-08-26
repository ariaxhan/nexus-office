import { test } from "node:test";
import assert from "node:assert/strict";
import { sheetCount, MAX_SHEETS } from "../src/scene/fixtures/intray.js";

/**
 * The geometry is not unit testable and the picture is what checks it. The stack
 * rule is, and it is where the cap bug lives: a curve that flattens too early
 * makes a drowning desk look like a busy one, and one that never flattens grows
 * a tower through the ceiling.
 */

test("no issues draws no sheets, so a clear desk gets no tray", () => {
  assert.equal(sheetCount(0), 0);
  assert.equal(sheetCount(undefined), 0);
  assert.equal(sheetCount(null), 0);
  assert.equal(sheetCount(-3), 0);
});

test("small counts are drawn exactly, because the count is the picture", () => {
  assert.equal(sheetCount(1), 1);
  assert.equal(sheetCount(4), 4);
  assert.equal(sheetCount(6), 6);
});

test("a drowning desk is visibly worse than a busy one", () => {
  assert.equal(sheetCount(27), 11);
  assert.ok(sheetCount(27) > sheetCount(4));
  assert.ok(sheetCount(27) > sheetCount(10));
});

test("the absurd case caps instead of building a skyscraper", () => {
  assert.equal(sheetCount(100000), MAX_SHEETS);
  assert.equal(sheetCount(Number.MAX_SAFE_INTEGER), MAX_SHEETS);
  assert.ok(sheetCount(100000) <= MAX_SHEETS);
});

test("the curve never goes backwards", () => {
  let last = 0;
  for (let n = 0; n <= 500; n++) {
    const s = sheetCount(n);
    assert.ok(s >= last, `sheetCount(${n}) = ${s} dropped below ${last}`);
    assert.ok(s <= MAX_SHEETS, `sheetCount(${n}) = ${s} broke the cap`);
    last = s;
  }
});

test("a fractional or junk count cannot produce a fractional stack", () => {
  assert.equal(sheetCount(3.7), 3);
  assert.equal(sheetCount("5"), 5);
  assert.equal(sheetCount(NaN), 0);
  assert.equal(sheetCount("bananas"), 0);
});
