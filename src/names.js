/**
 * Every repo gets a resident, and the resident keeps its name forever.
 *
 * The name is a pure function of the repo path, so the same desk belongs to the
 * same character across every reload and every machine, with nothing stored
 * anywhere. There is no roster file to drift out of sync with reality: if a repo
 * appears in the pipeline it has a villager, and if it stops appearing it does not.
 */

const NAMES = [
  "Pumpkin", "Biscuit", "Marlow", "Tansy", "Juniper", "Waffles", "Pepper", "Mochi",
  "Clove", "Barley", "Fennel", "Tuppence", "Nutmeg", "Bramble", "Olive", "Cinder",
  "Poppy", "Dumpling", "Sorrel", "Hazel", "Pickle", "Maple", "Cricket", "Thistle",
  "Wren", "Custard", "Bunty", "Sage", "Pip", "Gumdrop", "Rosemary", "Toast",
  "Marzipan", "Beetle", "Ferngully", "Plum", "Nibs", "Cardamom", "Truffle", "Bo",
  "Perry", "Quill", "Saffron", "Bumble", "Cobweb", "Doodle", "Elmer", "Fig",
  "Gruff", "Halo", "Inky", "Jamboree", "Kipper", "Lolly", "Mittens", "Noodle",
  "Otto", "Parsnip", "Quince", "Rhubarb", "Scout", "Tilly", "Umber", "Violet",
  "Whisk", "Yarrow", "Zephyr", "Apricot", "Bandit", "Comfrey", "Dandy", "Ember",
];

// Warm, saturated, distinguishable at small size. No two adjacent hues.
const COATS = [
  "#f2946b", "#7fc7a4", "#f4c95d", "#9db6f0", "#e58a9e", "#a8d38d", "#c9a2e0",
  "#6fc4d6", "#f0a3c0", "#d9b382", "#8fb3e0", "#eda36f", "#8fd6c0", "#dda0dd",
];

/** FNV-1a. Small, stable, and identical in every JS runtime, which is the point. */
function hash(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

export function resident(repo) {
  const h = hash(repo);
  return {
    name: NAMES[h % NAMES.length],
    coat: COATS[(h >>> 8) % COATS.length],
    // A second draw so two villagers sharing a name never share a silhouette.
    tall: ((h >>> 16) & 1) === 1,
    seed: h,
  };
}
