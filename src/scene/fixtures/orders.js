/**
 * The out-tray beside you, and the light that says the floor is working.
 *
 * NOT BUILT YET. See the fixture contract in ./all.js.
 */

export const id = "orders";
export const title = "the out-tray";
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
