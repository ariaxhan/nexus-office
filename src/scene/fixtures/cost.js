/**
 * the cost chart
 *
 * NOT BUILT YET. See the fixture contract in ./all.js, and the issue this
 * belongs to. Returning null is the honest stub: the room simply has no cost in
 * it, rather than an empty one that looks built.
 */

export const id = "cost";
export const title = "the cost chart";
export const wall = true;

export function build(ctx) {
  return null;
}

export function panel(payload, world, api) {
  return api.el("p", "empty", "Not built yet.");
}

export function demo() {
  return null;
}
