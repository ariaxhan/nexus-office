import * as THREE from "three";
import * as BGU from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { toon, tagSprite } from "../kit.js";

/**
 * The library: what memory holds, as a thing you can walk up to.
 *
 * One bay per learning type, stacked so `failure` and `gotcha` land at eye
 * level, because those are the two that save you. Each bay holds one book per
 * learning ACTUALLY IN THE STORE, not one per learning in the snapshot: the
 * shelf is how you see 594 without reading 594, and a shelf sized from the
 * truncated list would make a store growing without bound look stable.
 *
 * Hit count is wear. A learning recalled 210 times is short, dark, tilted and
 * pulled half out of the shelf; one nobody has touched is tall, pale and flush.
 *
 * Pending review rides a separate cart in front, because those are the ones
 * asking for a human and they must not blend into the shelved ones.
 *
 * Search: the browser holds no credentials and cannot reach the local runtime,
 * so `/api/search` is not available from the room and a queued search that
 * answers in ten minutes is not search. The panel filters the snapshot instead,
 * and says out loud that it is filtering the shelved subset rather than the
 * whole store. A live search bar that silently searched 73 of 574 memories would
 * be worse than no search bar at all.
 */

export const id = "library";
export const title = "the library";
export const wall = true;

const CASE = "#b98253";
const BOARD = "#caa072";

// Spine colours, and the darker shade a well-thumbed copy fades to.
const SPINE = {
  failure: ["#d4736c", "#8e3b36"],
  gotcha: ["#e0a05a", "#9a6320"],
  pattern: ["#7fa8dd", "#3f6296"],
  preference: ["#86bd92", "#48804f"],
};
const SPINE_DEFAULT = ["#b9a8c9", "#6d5c80"];

const DEPTH = 0.34;
// Slim enough that a shelf of eighty still leaves visible space at its right
// end. Wider, and every shelf past sixty looks equally full, which is the one
// thing the shelves exist to tell apart.
const PITCH = 0.06;
// A shelf is one merged mesh, so the ceiling is about geometry weight, not draw
// calls. Past this the books are thinner than a pixel anyway.
const MAX_BOOKS = 400;

const wearOf = (hits) => Math.min(1, (Number(hits) || 0) / 80);

/**
 * One book. Wear makes it shorter, deeper (so it juts out) and slightly askew.
 *
 * The height and depth jitter is not decoration. Without it every spine tops out
 * at exactly the same line and a packed shelf renders as one flat coloured bar,
 * which is precisely how the first version of this shipped: correct in source,
 * a stacked bar chart on screen. Deterministic from the index, never random, so
 * two runs of the shot harness produce the same picture.
 */
function book(i, x, y, wear, pitch, lean) {
  const jig = ((i * 37) % 7) / 6;
  const w = Math.max(0.008, pitch - 0.016);
  const h = (0.31 - wear * 0.08) * (0.84 + jig * 0.16);
  const d = (0.19 + wear * 0.09) * (0.9 + (((i * 53) % 5) / 4) * 0.2);
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(0, h / 2, d / 2);
  if (lean) g.rotateZ(lean);
  g.translate(x, y, 0.07);
  return g;
}

/** The empty carcass. Drawn even when there is nothing to put in it. */
function carcass(w, h, rows) {
  const parts = [];
  const back = new THREE.BoxGeometry(w, h, 0.06);
  back.translate(0, h / 2, 0.03);
  parts.push(back);
  for (const s of [-1, 1]) {
    const side = new THREE.BoxGeometry(0.07, h, DEPTH);
    side.translate((s * (w - 0.07)) / 2, h / 2, DEPTH / 2);
    parts.push(side);
  }
  const boards = [];
  for (const y of rows) {
    const b = new THREE.BoxGeometry(w - 0.14, 0.05, DEPTH);
    b.translate(0, y, DEPTH / 2);
    boards.push(b);
  }
  return [
    new THREE.Mesh(BGU.mergeGeometries(parts), toon(CASE)),
    new THREE.Mesh(BGU.mergeGeometries(boards), toon(BOARD)),
  ];
}

/** The trolley the pending-review items sit on. Deliberately not a shelf. */
function cart(state, count) {
  const g = new THREE.Group();
  const parts = [];
  for (const y of [0.28, 0.62]) {
    const tray = new THREE.BoxGeometry(0.72, 0.05, 0.44);
    tray.translate(0, y, 0);
    parts.push(tray);
  }
  for (const [sx, sz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const post = new THREE.CylinderGeometry(0.025, 0.025, 0.66, 6);
    post.translate(sx * 0.32, 0.33, sz * 0.18);
    parts.push(post);
  }
  g.add(new THREE.Mesh(BGU.mergeGeometries(parts), toon("#9aa7b4")));

  if (state === "up" && count > 0) {
    const stack = [];
    for (let i = 0; i < Math.min(count, 7); i++) {
      const p = new THREE.BoxGeometry(0.5, 0.035, 0.34);
      p.translate((i % 2) * 0.02 - 0.01, 0.67 + i * 0.038, 0);
      stack.push(p);
    }
    g.add(new THREE.Mesh(BGU.mergeGeometries(stack), toon("#f4e6b8")));
  }

  // "The review queue is unreachable" and "the review queue is empty" are
  // different facts and must not draw the same. Unknown gets a question mark.
  const label = state === "up"
    ? (count > 0 ? `review ${count}` : "review clear")
    : "review unknown ?";
  const tag = tagSprite(label, {
    bg: state === "up" ? (count > 0 ? "#c07c2c" : "#3f9e6a") : "#7a6a60",
    fg: "#fdf6e8",
    scale: 0.7,
  });
  // Below the shelf labels it stands beside, so the two never collide.
  tag.position.set(0, 0.82, 0);
  g.add(tag);
  return g;
}

export function build(ctx) {
  const slot = ctx.room?.wall;
  if (!slot) return null;
  const s = ctx.section;

  const group = new THREE.Group();
  group.position.set(slot.x, 0, slot.z);

  // The wall slot is far wider than a bookcase should be: at 9m by 2.2m the thing
  // reads as a bar chart bolted to the wall. Capped, and the leftover slot width
  // is where the review cart parks.
  const w = Math.max(1.6, Math.min(slot.w, 6));
  const h = slot.h;

  // A library with no shelves because the store is unreadable must not look like
  // a library with nothing in it. The carcass stands either way and says why.
  if (!s || s.state !== "ok") {
    const [box, boards] = carcass(w, h, [h - 0.05, h * 0.62, h * 0.36, 0.1]);
    group.add(box, boards);
    const why = {
      unconfigured: "no vault configured",
      absent: "no agentdb on this machine",
      error: "the store would not open",
      unbuilt: "not built yet",
    }[s?.state] || `memory unreadable (${s?.state || "no data"})`;
    const tag = tagSprite(why, { bg: "#a8443f", fg: "#fdf6e8", scale: 1.1 });
    tag.position.set(0, h + 0.4, DEPTH);
    group.add(tag);
    const pad = new THREE.Mesh(
      new THREE.BoxGeometry(w, h, 0.6),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    pad.position.set(0, h / 2, 0.3);
    pad.userData.fixture = { id, payload: { kind: "store" } };
    group.add(pad);
    return group;
  }

  const shelves = s.shelves || [];
  const n = Math.max(1, shelves.length);
  const bayH = (h - 0.1) / n;
  const rows = [];
  for (let i = 0; i <= n; i++) rows.push(0.1 + i * bayH);
  const [box, boards] = carcass(w, h, rows);
  group.add(box, boards);

  const usable = w - 0.32;
  shelves.forEach((shelf, i) => {
    // Top bay first, so the first shelf in the data (failure, then gotcha) is
    // the one at eye level rather than the one by your ankles.
    const base = 0.1 + (n - 1 - i) * bayH + 0.03;
    const count = Math.max(0, Math.min(Number(shelf.count) || 0, MAX_BOOKS));
    const [pale, worn] = SPINE[shelf.type] || SPINE_DEFAULT;

    // Books are drawn from the REAL count, not from the capped list. Under
    // capacity the shelf fills up as the store grows; past capacity the books
    // get thinner and cram, which is what unbounded growth should look like.
    const pitch = count ? Math.min(PITCH, usable / count) : PITCH;
    const items = shelf.items || [];
    const paleGeo = [];
    const wornGeo = [];
    for (let b = 0; b < count; b++) {
      const item = items[b];
      const wear = item ? wearOf(item.hits) : 0;
      const x = -usable / 2 + pitch * (b + 0.5);
      const lean = wear > 0.5 ? (b % 2 ? 0.075 : -0.075) : 0;
      (wear >= 0.35 ? wornGeo : paleGeo).push(book(b, x, base, wear, pitch, lean));
    }
    if (paleGeo.length) group.add(new THREE.Mesh(BGU.mergeGeometries(paleGeo), toon(pale)));
    if (wornGeo.length) group.add(new THREE.Mesh(BGU.mergeGeometries(wornGeo), toon(worn)));

    // Right-hand end: books fill from the left, so this is the emptiest part of
    // the shelf and the least interesting books when it is not. A label parked
    // over the worn end hides the one thing the shelf is trying to show.
    const tag = tagSprite(`${shelf.type} ${shelf.count}`, {
      bg: "#4a3b33", fg: "#fdf6e8", scale: 0.6,
    });
    tag.position.set(usable / 2 - 0.55, base + bayH - 0.15, DEPTH + 0.1);
    group.add(tag);

    const pad = new THREE.Mesh(
      new THREE.BoxGeometry(w, bayH, 0.62),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    pad.position.set(0, base + bayH / 2 - 0.03, 0.31);
    pad.userData.fixture = { id, payload: { kind: "shelf", type: shelf.type } };
    group.add(pad);
  });

  const rev = s.review || { state: "unknown" };
  const trolley = cart(rev.state, Number(rev.count) || 0);
  // Beside the case and clear of it. Parked in front, it read as another shelf,
  // which is the one thing the review queue must never look like.
  trolley.position.set(Math.min(w / 2 + 0.8, slot.w / 2 - 0.5), 0, DEPTH + 0.35);
  const cartPad = new THREE.Mesh(
    new THREE.BoxGeometry(0.9, 1.3, 0.7),
    new THREE.MeshBasicMaterial({ visible: false })
  );
  cartPad.position.set(0, 0.65, 0);
  cartPad.userData.fixture = { id, payload: { kind: "review" } };
  trolley.add(cartPad);
  group.add(trolley);

  return group;
}

/* ------------------------------------------------------------------ panel -- */

const WHEN = (iso) => (iso ? String(iso).slice(0, 10) : "");

/**
 * One memory. Content and provenance, always both.
 *
 * The insight is untrusted text: it was written by whatever agent learned it,
 * into a page that holds a session token. It goes through `api.md`, which escapes
 * every character before it generates any markup. Provenance goes in through
 * textContent, which cannot produce markup at all.
 */
function memoryCard(item, api) {
  const card = api.el("div", "issue");
  const top = api.el("div", "issue-top");
  top.append(api.el("span", "issue-num", item.type || "memory"));
  top.append(api.el("span", "issue-title",
    item.hits ? `recalled ${item.hits}×` : "never recalled"));
  card.append(top);

  api.md(card.appendChild(api.el("div", "issue-body md")), item.insight || "(no content)");

  const p = item.provenance || {};
  const line = api.el("p", "log");
  if (p.sourced) {
    line.append(api.el("b", null, "where this came from"), " ");
    line.append(p.evidence);
  } else {
    // A memory you cannot source is a rumour, and it says so rather than
    // borrowing the authority of the ones that can be sourced.
    line.append(api.el("b", null, "no source recorded"), " ");
    line.append("this one is a rumour: nothing was written down about where it came from");
  }
  card.append(line);

  const foot = api.el("p", "log");
  const bits = [
    p.domain && `in ${p.domain}`,
    p.learned_at && `learned ${WHEN(p.learned_at)}`,
    p.last_hit && `last recalled ${WHEN(p.last_hit)}`,
    p.record,
  ].filter(Boolean);
  foot.textContent = bits.join(" · ");
  card.append(foot);
  return card;
}

function shape(store, api) {
  const p = api.el("p", "p-detail");
  const mb = (store.bytes / 1048576).toFixed(1);
  p.textContent =
    `${store.live} learnings on the shelves, ${store.archived} retired, ` +
    `${store.hits} recalls across ${store.domains} domains. ` +
    `${mb}MB on disk, first written ${WHEN(store.oldest)}, last ${WHEN(store.newest)}.`;
  return p;
}

export function panel(payload, world, api) {
  const s = world?.sections?.[id];
  const wrap = api.el("div");

  if (!s || s.state !== "ok") {
    const why = {
      unconfigured: "No vault root is configured, so nothing here knows where memory lives.",
      absent: "There is no agentdb on the machine that pushes this snapshot.",
      unbuilt: "This fixture has no data yet.",
    }[s?.state] || "The memory store could not be read.";
    wrap.append(api.el("p", "empty", why));
    if (s?.detail) wrap.append(api.el("p", "log", s.detail));
    wrap.append(api.el("p", "p-detail",
      "This is not an empty library. It is a library nobody could open."));
    return wrap;
  }

  wrap.append(shape(s.store || {}, api));

  const kind = payload?.kind || "shelf";

  if (kind === "review") {
    const rev = s.review || {};
    wrap.append(api.el("h3", null, "waiting for review"));
    if (rev.state !== "up") {
      wrap.append(api.el("p", "empty",
        rev.state === "down"
          ? "The memory runtime is not running, so what is pending review is unknown. It is not zero; it is unread."
          : `The review queue could not be read (${rev.state}).`));
      if (rev.detail) wrap.append(api.el("p", "log", rev.detail));
      return wrap;
    }
    if (!rev.count) {
      wrap.append(api.el("p", "empty", "Nothing is waiting for review. The cart is genuinely empty."));
      return wrap;
    }
    if (rev.shown < rev.count) {
      wrap.append(api.el("p", "p-detail",
        `${rev.shown} of ${rev.count} shown. The rest are on the cart but not in this snapshot.`));
    }
    for (const it of rev.items || []) wrap.append(memoryCard(it, api));
    return wrap;
  }

  const shelves = s.shelves || [];
  const shelf = shelves.find((x) => x.type === payload?.type) || shelves[0];
  if (!shelf) {
    wrap.append(api.el("p", "empty", "This library has no shelves in it yet."));
    return wrap;
  }

  wrap.append(api.el("h3", null, `${shelf.type} · ${shelf.count} on this shelf`));

  // A truncated list is never presented as a complete one.
  if (shelf.shown < shelf.count) {
    wrap.append(api.el("p", "p-detail",
      `Showing the ${shelf.shown} most-recalled. ${shelf.count - shelf.shown} more are on ` +
      `the shelf and were left out of the snapshot to keep it small.`));
  }

  // Filters the snapshot, not the store, and says so. The page cannot reach the
  // local runtime, so a box promising a real search would be promising a lie.
  const box = api.el("input", "reply");
  box.type = "search";
  box.placeholder = `filter these ${shelf.shown}`;
  box.style.minHeight = "0";
  wrap.append(box);
  const scope = api.el("p", "p-detail");
  const total = (s.store || {}).live || 0;
  scope.textContent =
    `This filters the ${shelf.shown} memories carried in this snapshot, not all ${total} ` +
    `in the store. Full-text search needs the runtime, which the browser cannot reach.`;
  wrap.append(scope);

  const list = api.el("div");
  wrap.append(list);
  const draw = () => {
    const q = box.value.trim().toLowerCase();
    list.replaceChildren();
    const hits = (shelf.items || []).filter((it) =>
      !q || `${it.insight} ${it.provenance?.evidence || ""} ${it.provenance?.domain || ""}`
        .toLowerCase().includes(q));
    if (!hits.length) {
      list.append(api.el("p", "empty",
        `Nothing on this shelf matches "${box.value.trim()}" among the ${shelf.shown} carried here.`));
      return;
    }
    for (const it of hits) list.append(memoryCard(it, api));
  };
  box.oninput = draw;
  draw();

  const rev = s.review || {};
  wrap.append(api.el("h3", null, "the review cart"));
  wrap.append(api.el("p", "log",
    rev.state === "up"
      ? (rev.count ? `${rev.count} waiting. Click the cart in the room.` : "Nothing waiting.")
      : "Unknown: the memory runtime is not running."));
  return wrap;
}

/**
 * The ugly cases, on purpose: four types, a genuinely well-thumbed item, one
 * memory with no provenance at all, a review cart with something on it, and a
 * cap that bites so the truncation notice is always on screen.
 */
export function demo() {
  const mem = (type, insight, hits, evidence, domain) => ({
    id: `LRN-demo-${type}-${hits}`,
    type,
    insight,
    hits,
    loads: hits * 3,
    provenance: {
      sourced: !!evidence,
      evidence: evidence || "",
      domain: domain || "Vaults",
      learned_at: "2026-06-12T07:36:44.398Z",
      last_hit: "2026-08-11T05:55:33.380Z",
      record: `LRN-demo-${type}-${hits}`,
    },
  });

  const shelves = [
    {
      type: "failure", count: 83, shown: 3, items: [
        mem("failure", "`agentdb recall` returned **(no matching learnings)** on good data: sqlite 3.51 escapes control characters, so the `char(31)` delimiter arrived as a literal `^_` and the split produced nothing.", 79,
          "kernel-claude 231db9c; 0/10 to 10/10 on the same queries"),
        mem("failure", "A stale Vercel deployment was reported to a client as a production P0 while the real site, served from a different repo, was healthy the whole time.", 41,
          "chronicle 2026-08-24; the serving domain was never established"),
        mem("failure", "Someone said the fix shipped. Nobody wrote down where they got that.", 4, ""),
      ],
    },
    {
      type: "gotcha", count: 234, shown: 3, items: [
        mem("gotcha", "Per-commit autopush is disabled. Push explicitly or the work is stranded on a branch nobody will look at again.", 210,
          "near-miss caught at PreToolUse on a commit that was never pushed"),
        mem("gotcha", "Committed, pushed, deployed and working are four different states. Say which one you actually verified.", 205,
          "chronicle 2026-07-11; a green build reported as a live fix"),
        mem("gotcha", "`[hidden] { display: none !important }` is load bearing: an id selector with a `display` outranks the user agent rule, so the DOM says hidden while the element sits there in full view.", 46,
          "nexus-office; a lock screen covering a working office"),
      ],
    },
    {
      type: "pattern", count: 244, shown: 2, items: [
        mem("pattern", "Measure instead of predicting: twenty iterations of a matrix multiply cost nothing on a camera move, and every closed-form version of the framing maths was wrong on some window.", 69,
          "office.js frameAll; a wing rendered three times closer than the other", "CodingVault"),
        mem("pattern", "Write the CHECK line at plan time. Work planned together with its verification comes out shaped for checking.", 33,
          "arXiv 2608.09277, joint program-and-proof planning"),
      ],
    },
    {
      type: "preference", count: 13, shown: 1, items: [
        mem("preference", "Do not overengineer. A readable 150-line fixture beats a framework.", 5,
          "said out loud, 2026-08-25"),
      ],
    },
  ];

  const shown = shelves.reduce((a, s) => a + s.shown, 0);
  const count = shelves.reduce((a, s) => a + s.count, 0);

  return {
    state: "ok",
    store: {
      path: "/demo/Vaults/_meta/agentdb/agent.db",
      bytes: 16785408,
      total: 594,
      live: count,
      archived: 20,
      hits: 4593,
      domains: 23,
      oldest: "2026-05-29T17:55:18.577Z",
      newest: "2026-08-26T04:20:04.525Z",
    },
    shelves,
    capped: {
      per_type: 20,
      shown,
      omitted: count - shown,
      insight_chars: 420,
      note: `the most-recalled of each type are carried in full; ${count - shown} more are on the shelf and counted but not in this snapshot`,
    },
    review: {
      state: "up",
      count: 3,
      shown: 3,
      items: [
        mem("review", "Proposed: treat a shot nobody opened as a failed check, not a passed one.", 0,
          "raised by the shot harness after three green runs hid a blank room"),
        mem("review", "Proposed: the verifier always receives the acceptance record.", 0,
          "review-termination-protocol.md"),
        mem("review", "Proposed: something nobody wrote a source for.", 0, ""),
      ],
    },
    semantic: { state: "down", detail: "Connection refused" },
    url: "http://127.0.0.1:8787",
  };
}
