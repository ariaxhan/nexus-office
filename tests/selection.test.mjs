import assert from "node:assert/strict";
import test from "node:test";
import { redrawIfChanged, selecting } from "../client/phone/office-selection.js";

// A node that owns some text children, and a page selection that can sit in one of them.
const text = {}, outside = {};
const node = { dataset: {}, contains: child => child === text };
const select = (anchor, collapsed = false) => {
  globalThis.getSelection = () => ({ isCollapsed: collapsed, rangeCount: 1, anchorNode: anchor, focusNode: anchor });
};

test("a poll with unchanged data does not redraw", () => {
  select(null, true); node.dataset = {};
  let draws = 0;
  assert.equal(redrawIfChanged(node, "a", () => draws++), true);
  assert.equal(redrawIfChanged(node, "a", () => draws++), false);
  assert.equal(draws, 1);
});

test("an active selection inside the node survives a changed poll, then it catches up", () => {
  node.dataset = { signature: "a" }; let draws = 0;
  select(text);
  assert.equal(selecting(node), true);
  assert.equal(redrawIfChanged(node, "b", () => draws++), false);
  select(text, true);
  assert.equal(redrawIfChanged(node, "b", () => draws++), true);
  assert.equal(draws, 1);
});

test("a selection elsewhere on the page does not freeze the node", () => {
  node.dataset = { signature: "a" }; select(outside);
  assert.equal(selecting(node), false);
  assert.equal(redrawIfChanged(node, "b", () => {}), true);
});
