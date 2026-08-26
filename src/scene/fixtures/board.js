/**
 * the commissions board
 *
 * NOT BUILT YET. See the fixture contract in ./all.js, and the issue this
 * belongs to. Returning null is the honest stub: the room simply has no board in
 * it, rather than an empty one that looks built.
 */

export const id = "board";
export const title = "the commissions board";
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
