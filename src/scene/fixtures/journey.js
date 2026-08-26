/**
 * work in motion
 *
 * NOT BUILT YET. See the fixture contract in ./all.js, and the issue this
 * belongs to. Returning null is the honest stub: the room simply has no journey in
 * it, rather than an empty one that looks built.
 */

export const id = "journey";
export const title = "work in motion";
export const wall = false;

export function build(ctx) {
  return null;
}

export function panel(payload, world, api) {
  return api.el("p", "empty", "Not built yet.");
}

export function demo() {
  return null;
}
