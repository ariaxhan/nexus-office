import * as THREE from "three";
import * as BGU from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { toon } from "../kit.js";
import { needsHuman } from "../../ui/panel.js";

/**
 * the in-tray
 *
 * Open issues are a number in a panel. Here they are paper. One sheet per open
 * issue in a tray on the desk, so a drowning desk looks different from a clear
 * one from across the room, before anybody reads anything.
 *
 * Everything this needs is already on the station: `issues[]`, and `bot_last`
 * inside each issue. `needsHuman` comes from the panel because that is the
 * runner's own rule and there is exactly one of it; a second opinion here would
 * be a second answer to "is this waiting on a person", which is how a room
 * starts disagreeing with itself.
 */

export const id = "intray";
export const title = "the in-tray";
export const wall = false;

/* --------------------------------------------------------- the stack rule -- */

/**
 * The cap is 18 sheets, and the curve bends at 6.
 *
 * The first six issues get one sheet each, because at small counts the count IS
 * the picture and 4 has to read as four things. Past six the stack grows as the
 * square root of the overflow, so it keeps growing forever but slowly: 10 draws
 * 8, 27 draws 11, 100 draws 16. That keeps a bad desk clearly worse than a busy
 * one without letting one repo build a skyscraper through the ceiling.
 *
 * 18 sheets is 0.31 of a desk height, which clears the monitor and stays under
 * a seated villager's eyeline.
 *
 * The cap is a drawing limit and never a counting one. The panel says the real
 * number, always: a picture that made the true count unknowable would be the
 * exact failure this whole project exists to prevent.
 */
export const MAX_SHEETS = 18;
const KNEE = 6;

export function sheetCount(open) {
  const n = Math.floor(Number(open) || 0);
  if (n <= 0) return 0;
  if (n <= KNEE) return n;
  return Math.min(MAX_SHEETS, KNEE + Math.ceil(Math.sqrt(n - KNEE)));
}

/* ---------------------------------------------------------------- the tray -- */

// Left front corner of the desk top. The desk top is 1.7 x 0.85 with its
// surface at y 0.78; the monitor foot ends at z -0.11 and the mug sits at
// x +0.58. Everything below is sized so that even a capped pile stays on the
// wood and out of both.
const TRAY_X = -0.40;
const TRAY_Z = 0.16;
const TOP = 0.78;

const TRAY_W = 0.44;
const TRAY_D = 0.32;
const TRAY_H = 0.03;

const SHEET_W = 0.36;
const SHEET_D = 0.26;
const SHEET_H = 0.008;
const STEP = 0.018;

/**
 * A pile SPREADS as well as rises, and the spreading is the part that reads.
 *
 * The camera looks down at the room from about fifty degrees up, so at full-room
 * framing a desk is fifty pixels wide and a stack's height is worth about one
 * pixel per sheet. Height alone cannot tell four issues from twenty-seven from
 * across the office, which is the entire feature. Plan area can, so the sheets
 * scatter over a widening patch: neat in the tray at one, spilling over its
 * edges by six, a mess by eleven. Capped at 0.22 so the worst desk in the
 * building still keeps its paper on its own desk.
 */
const MAX_SPREAD = 0.22;
const spreadFor = (sheets) => Math.min(MAX_SPREAD, 0.03 * (sheets - 1));

const TRAY_COLOR = "#6b7684";
const PAPER = "#fbf7ec";
// The same red the room uses for "needs you" everywhere else.
const PAPER_HOT = "#d1495b";

/** Deterministic per-sheet scatter. Stable across rebuilds, so paper never dances. */
const wobble = (seed) => (((seed * 37) % 23) / 23) - 0.5;

export function build(ctx) {
  const stations = ctx.stations || [];
  const trayGeo = [];
  const paperGeo = [];
  const hotGeo = [];
  const picks = [];

  const trayBox = new THREE.BoxGeometry(TRAY_W, TRAY_H, TRAY_D);
  const sheetBox = new THREE.BoxGeometry(SHEET_W, SHEET_H, SHEET_D);
  // An invisible click volume per desk, the same trick office.js uses for the
  // workstation pad: real geometry stays merged and cheap, and the thing that
  // carries the repo is a box nobody has to render.
  const pickBox = new THREE.BoxGeometry(TRAY_W + 0.06, 0.4, TRAY_D + 0.06);
  const pickMat = new THREE.MeshBasicMaterial({ visible: false });

  for (const st of stations) {
    const issues = st.issues || [];
    // A clear desk gets no tray at all. An empty tray is furniture that looks
    // like a state, and "nothing open" is not a state you should have to read.
    if (!issues.length) continue;

    const sheets = sheetCount(issues.length);
    const hot = Math.min(sheets, sheetCount(issues.filter(needsHuman).length));
    const spread = spreadFor(sheets);

    trayGeo.push(trayBox.clone().applyMatrix4(
      new THREE.Matrix4().makeTranslation(st.x + TRAY_X, TOP + TRAY_H / 2, st.z + TRAY_Z)
    ));

    for (let i = 0; i < sheets; i++) {
      // The ones needing a human sit on top, where a hand would reach first.
      const isHot = i >= sheets - hot;
      const m = new THREE.Matrix4().makeRotationY(wobble(i * 7 + 3) * 2 * (0.08 + spread));
      m.setPosition(
        st.x + TRAY_X + wobble(i * 3 + 1) * 2 * spread,
        TOP + TRAY_H + SHEET_H / 2 + i * STEP,
        st.z + TRAY_Z + wobble(i * 5 + 2) * 0.7 * spread
      );
      (isHot ? hotGeo : paperGeo).push(sheetBox.clone().applyMatrix4(m));
    }

    const pick = new THREE.Mesh(pickBox, pickMat);
    pick.position.set(st.x + TRAY_X, TOP + 0.2, st.z + TRAY_Z);
    pick.userData.fixture = { id, payload: { repo: st.repo } };
    picks.push(pick);
  }

  if (!trayGeo.length) return null;

  const group = new THREE.Group();
  group.add(new THREE.Mesh(BGU.mergeGeometries(trayGeo), toon(TRAY_COLOR)));
  if (paperGeo.length) group.add(new THREE.Mesh(BGU.mergeGeometries(paperGeo), toon(PAPER)));
  if (hotGeo.length) group.add(new THREE.Mesh(BGU.mergeGeometries(hotGeo), toon(PAPER_HOT)));
  for (const p of picks) group.add(p);
  return group;
}

/* --------------------------------------------------------------- the panel -- */

export function panel(payload, world, api) {
  const { el } = api;
  const wrap = el("div");
  const repo = payload?.repo;
  const st = (world?.stations || []).find((s) => s.repo === repo);

  if (!st) return el("p", "empty", `No desk for ${repo || "that tray"} in this room.`);

  const issues = st.issues || [];
  const hot = issues.filter(needsHuman);
  const drawn = sheetCount(issues.length);

  wrap.append(el("div", "p-repo", st.repo));
  wrap.append(el("p", "log",
    `${issues.length} open, ${hot.length} waiting on you.`));
  // A capped stack must never make the true count unknowable, so the panel says
  // out loud when the drawing is smaller than the pile.
  if (drawn < issues.length) {
    wrap.append(el("p", "log",
      `The tray draws ${drawn} sheets. The real number is ${issues.length}.`));
  }

  const list = (rows, heading) => {
    if (!rows.length) return;
    wrap.append(el("h3", null, `${heading} (${rows.length})`));
    for (const i of rows) {
      const card = el("div", `issue${needsHuman(i) ? " hot" : ""}`);
      const top = el("div", "issue-top");
      top.append(el("span", "issue-num", `#${i.number}`));
      top.append(el("span", "issue-title", i.title || "(no title)"));
      card.append(top);
      if (i.url) {
        const a = el("a", "link", "open on GitHub");
        a.href = i.url;
        a.target = "_blank";
        a.rel = "noreferrer";
        card.append(a);
      }
      wrap.append(card);
    }
  };
  list(hot, "needs you");
  list(issues.filter((i) => !needsHuman(i)), "open");

  if (!issues.length) wrap.append(el("p", "empty", "This desk is clear."));
  wrap.append(el("p", "p-detail",
    "Click the desk itself to reply, run one, or close it."));
  return wrap;
}

/**
 * Null on purpose, and it is not a stub.
 *
 * The in-tray reads `ctx.stations`, which the demo floor already fills with real
 * issue lists. It owns no `world.sections` entry, so there is nothing for it to
 * fake.
 */
export function demo() {
  return null;
}
