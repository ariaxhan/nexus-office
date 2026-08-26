import * as THREE from "three";
import { toon, roundRect, tagSprite } from "../kit.js";

/**
 * the commissions board
 *
 * The runtime keeps a board of commissions and it is the closest thing to a plan
 * the whole setup has. This hangs it on the wall.
 *
 * Its data is `world.runtime.board`, not `world.sections.board`, because
 * `client/runtime.py` already fetches it as part of the runtime snapshot. That is
 * also why `demo()` returns null: the fake board for `?demo=1` lives in
 * `src/demo.js` under `runtime.board`, and a second copy here would be a second
 * truth to keep in step.
 *
 * Three rules this file exists to obey:
 *
 *   - "the runtime is down", "no runtime is configured" and "there are genuinely
 *     no commissions" are three different facts and never render the same. When
 *     the board is not up it draws the reason across itself rather than showing
 *     four empty columns, which would read as an idle floor.
 *   - `read_board` cuts `complete` to twelve and reduces `archived` to a count.
 *     Twelve cards that imply twelve is all of them is a lie, so every column
 *     carries the true number from `metrics` and says when it is showing fewer.
 *   - nothing here fetches. `GET /api/commission?state=&slug=` would give the
 *     per-card detail the issue asks for, but only the laptop can reach the local
 *     runtime and this browser holds no credentials for it. So the panel renders
 *     what the snapshot carries and says out loud what it cannot get.
 */

export const id = "board";
export const title = "the commissions board";
export const wall = true;

const COLUMNS = [
  { key: "running", label: "running now", accent: "#d98a2b" },
  { key: "active", label: "on the board", accent: "#5b8dd9" },
  { key: "complete", label: "done", accent: "#3f9e6a" },
  { key: "archived", label: "archived", accent: "#9a8f86" },
];

/* ------------------------------------------------------------------ time -- */

/** Accept an ISO string, epoch seconds or epoch millis. Anything else is null. */
function stamp(v) {
  if (v == null) return null;
  if (typeof v === "number" && Number.isFinite(v)) return v < 1e12 ? v * 1000 : v;
  const t = Date.parse(v);
  return Number.isFinite(t) ? t : null;
}

const firstStamp = (o, keys) => {
  for (const k of keys) {
    const t = stamp(o?.[k]);
    if (t != null) return t;
  }
  return null;
};

export function ageText(ms) {
  if (ms == null || !Number.isFinite(ms)) return "age unknown";
  const mins = Math.round(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

function elapsedText(ms) {
  if (ms == null || !Number.isFinite(ms)) return null;
  const secs = Math.round(ms / 1000);
  if (secs < 90) return `${secs}s`;
  const mins = Math.round(secs / 60);
  if (mins < 90) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

/* ------------------------------------------------------------------ model -- */

/**
 * Which desk owns this board.
 *
 * `root` is one filesystem path for the whole runtime, not a field on each
 * commission, so every card on the board belongs to the same desk. Matching is
 * done on the path tail so `/Users/x/code/acme/storefront` finds `acme/storefront`,
 * and falls back to the last path segment matching a repo name.
 */
export function ownerOf(root, stations = []) {
  const path = String(root || "").replace(/\/+$/, "");
  if (!path) return null;
  const repos = stations.map((s) => s.repo).filter(Boolean);
  const full = repos.find((r) => path === r || path.endsWith("/" + r));
  if (full) return full;
  const leaf = path.split("/").pop();
  return repos.find((r) => r.split("/").pop() === leaf) || null;
}

function card(item, { owner, now, running = false }) {
  const when = firstStamp(item, ["updated", "updated_at", "at", "started", "started_at", "created"]);
  const started = running
    ? firstStamp(item, ["started", "started_at", "at", "created", "updated"])
    : null;
  let elapsed = null;
  if (running) {
    const secs = Number(item.elapsed_s ?? item.elapsed);
    if (Number.isFinite(secs)) elapsed = elapsedText(secs * 1000);
    else if (started != null) elapsed = elapsedText(now - started);
  }
  return {
    slug: item.slug || item.id || "",
    title: item.title || item.slug || item.id || "(untitled)",
    state: item.state || item.status || "",
    step: item.step || item.current_step || "",
    when,
    age: ageText(when == null ? null : now - when),
    owner,
    running,
    elapsed,
    // undefined is not false. A field the snapshot never carried is unknown, and
    // saying "no chronicle" about it would invent a defect.
    chronicle: item.has_chronicle === undefined ? null : !!item.has_chronicle,
    transcript: item.has_transcript === undefined ? null : !!item.has_transcript,
  };
}

const newestFirst = (a, b) => (b.when ?? -Infinity) - (a.when ?? -Infinity);

const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);

/**
 * The whole board as data. Pure, so it can be tested without a canvas.
 *
 * Returns { up, state, note, detail, totals, columns } where every column knows
 * both what it is showing and how many there really are.
 */
export function boardModel(board, stations = [], now = Date.now()) {
  const state = board?.state || "unconfigured";
  const notes = {
    down: "the runtime is not running",
    error: "the runtime answered with an error",
    unconfigured: "no runtime is configured for this office",
  };

  if (state !== "up") {
    return {
      up: false,
      state,
      note: notes[state] || `the runtime is ${state}`,
      detail: board?.detail || "",
      totals: null,
      columns: [],
    };
  }

  const m = board.metrics || {};
  const owner = ownerOf(board.root, stations);
  const runs = (board.runs || []).map((r) => card(r, { owner, now, running: true })).sort(newestFirst);
  const active = (board.active || []).map((a) => card(a, { owner, now })).sort(newestFirst);
  const complete = (board.complete || []).map((c) => card(c, { owner, now })).sort(newestFirst);

  const archivedTotal = num(m.archived) ?? num(board.archived_count) ?? 0;

  const col = (key, cards, total) => {
    const spec = COLUMNS.find((c) => c.key === key);
    const real = total == null ? cards.length : total;
    return {
      ...spec,
      cards,
      total: real,
      // Showing fewer than there are is fine. Showing fewer than there are and
      // not saying so is the lie this flag exists to prevent.
      truncated: real > cards.length,
    };
  };

  return {
    up: true,
    state,
    note: "",
    detail: "",
    root: board.root || "",
    owner,
    totals: {
      active: num(m.active) ?? active.length,
      complete: num(m.complete) ?? complete.length,
      archived: archivedTotal,
      runs: num(m.runs) ?? runs.length,
      cost: num(m.total_cost),
      cache: num(m.average_cache_read_ratio),
    },
    columns: [
      // metrics.runs is every run ever, not the ones happening now, so this
      // column counts itself. Borrowing the lifetime number here would put 61
      // over a column holding one card.
      col("running", runs, null),
      col("active", active, num(m.active)),
      col("complete", complete, num(m.complete)),
      // The snapshot reduces archived to a bare count, so this column can never
      // have cards. It says the number rather than pretending to be empty.
      col("archived", [], archivedTotal),
    ],
  };
}

/* ------------------------------------------------------------------ paint -- */

const FONT = (size, weight = 600) =>
  `${weight} ${size}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;

/** Draw text, cut with an ellipsis rather than spilling out of its column. */
function clipText(g, text, x, y, max) {
  let s = String(text);
  if (g.measureText(s).width <= max) return g.fillText(s, x, y);
  while (s.length > 1 && g.measureText(s + "…").width > max) s = s.slice(0, -1);
  g.fillText(s + "…", x, y);
}

/** A tag for an attachment. Present is solid, missing is a red outline. */
function chip(g, label, x, y, kind) {
  g.font = FONT(24, 700);
  const w = g.measureText(label).width + 26;
  const h = 34;
  if (kind === "have") {
    g.fillStyle = "rgba(74,59,51,0.14)";
    roundRect(g, x, y, w, h, 17);
    g.fill();
    g.fillStyle = "#4a3b33";
  } else {
    g.strokeStyle = kind === "missing" ? "#d1495b" : "rgba(125,106,95,0.55)";
    g.lineWidth = 3;
    g.setLineDash(kind === "missing" ? [] : [7, 6]);
    roundRect(g, x + 1.5, y + 1.5, w - 3, h - 3, 16);
    g.stroke();
    g.setLineDash([]);
    g.fillStyle = kind === "missing" ? "#d1495b" : "#7d6a5f";
  }
  g.textBaseline = "middle";
  g.fillText(label, x + 13, y + h / 2 + 1);
  g.textBaseline = "alphabetic";
  return w + 8;
}

const W = 2400;

function paint(model) {
  const c = document.createElement("canvas");
  c.width = W;
  c.height = Math.round(W / 3.25);
  const g = c.getContext("2d");
  const H = c.height;

  g.fillStyle = "#fdf6e8";
  g.fillRect(0, 0, W, H);
  g.strokeStyle = "rgba(74,59,51,0.18)";
  g.lineWidth = 6;
  g.strokeRect(3, 3, W - 6, H - 6);

  const PAD = 34;

  if (!model.up) {
    // The board draws its own absence. Deliberately nothing like an idle board:
    // no columns, no zeroes, a wash and a sentence.
    g.fillStyle = model.state === "error" ? "rgba(209,73,91,0.12)" : "rgba(74,59,51,0.10)";
    g.fillRect(0, 0, W, H);
    g.textAlign = "center";
    g.fillStyle = "#7d6a5f";
    g.font = FONT(52, 700);
    g.fillText("the commissions board", W / 2, H * 0.28);
    g.fillStyle = model.state === "error" ? "#d1495b" : "#4a3b33";
    g.font = FONT(118, 800);
    clipText(g, model.note, W / 2, H * 0.58, W - PAD * 2);
    g.fillStyle = "#7d6a5f";
    g.font = FONT(46, 600);
    clipText(g, model.detail || "this is not an empty board, it is an unknown one",
      W / 2, H * 0.80, W - PAD * 2);
    g.textAlign = "left";
    return c;
  }

  // Title strip: the true numbers, in the one place they cannot be missed.
  g.fillStyle = "#4a3b33";
  g.font = FONT(44, 800);
  g.fillText("commissions", PAD, PAD + 40);
  const t = model.totals;
  const bits = [`${t.active} active`, `${t.complete} done`, `${t.archived} archived`, `${t.runs} runs`];
  if (t.cost != null) bits.push(`$${t.cost.toFixed(2)}`);
  g.textAlign = "right";
  g.fillStyle = "#7d6a5f";
  g.font = FONT(40, 700);
  clipText(g, bits.join("  ·  "), W - PAD, PAD + 40, W * 0.62);
  g.textAlign = "left";

  const top = PAD + 62;
  g.strokeStyle = "rgba(74,59,51,0.16)";
  g.lineWidth = 3;
  g.beginPath();
  g.moveTo(PAD, top);
  g.lineTo(W - PAD, top);
  g.stroke();

  const gap = 16;
  const colW = (W - PAD * 2 - gap * 3) / 4;

  model.columns.forEach((col, i) => {
    const x = PAD + i * (colW + gap);
    let y = top + 18;

    // The header band is deliberately the biggest thing on the board. A 2.2m
    // board across a 46m room is about seventy screen pixels tall, so anything
    // taking less than a fifth of that height cannot be read from the door.
    const headH = 148;
    g.fillStyle = col.accent;
    roundRect(g, x, y, colW, headH, 20);
    g.fill();
    g.fillStyle = "#fffdf5";
    g.font = FONT(50, 800);
    clipText(g, col.label, x + 20, y + 50, colW - 40);
    g.font = FONT(92, 800);
    g.fillText(String(col.total), x + 20, y + headH - 14);
    if (col.truncated) {
      g.textAlign = "right";
      g.fillStyle = "rgba(255,253,245,0.92)";
      g.font = FONT(30, 700);
      clipText(g, `${col.cards.length} shown`, x + colW - 20, y + headH - 22, colW * 0.5);
      g.textAlign = "left";
    }
    y += headH + 14;

    if (col.key === "archived") {
      g.fillStyle = "#7d6a5f";
      g.font = FONT(28, 600);
      clipText(g, "count only.", x + 6, y + 26, colW - 12);
      clipText(g, "no titles in the", x + 6, y + 60, colW - 12);
      clipText(g, "snapshot.", x + 6, y + 94, colW - 12);
      return;
    }

    if (!col.cards.length) {
      g.fillStyle = "#7d6a5f";
      g.font = FONT(28, 600);
      // A column that counts twenty-two and shows none is not an empty column.
      clipText(g, col.total ? "none of them are" : "nothing here.", x + 6, y + 26, colW - 12);
      if (col.total) clipText(g, "in this snapshot.", x + 6, y + 60, colW - 12);
      return;
    }

    const room = H - PAD - y;
    const cardH = 130;
    const fit = Math.max(1, Math.floor((room + 10) / (cardH + 10)));
    const shown = col.cards.slice(0, fit);

    for (const k of shown) {
      g.fillStyle = "#fff";
      roundRect(g, x, y, colW, cardH, 14);
      g.fill();
      g.strokeStyle = "rgba(74,59,51,0.18)";
      g.lineWidth = 3;
      g.stroke();
      g.fillStyle = col.accent;
      roundRect(g, x, y, 9, cardH, 5);
      g.fill();

      g.fillStyle = "#4a3b33";
      g.font = FONT(36, 700);
      clipText(g, k.title, x + 20, y + 40, colW - 34);

      g.fillStyle = "#7d6a5f";
      g.font = FONT(26, 600);
      const meta = k.running
        ? `${k.elapsed ? "running " + k.elapsed : "running"}${k.step ? " · " + k.step : ""}`
        : k.age;
      clipText(g, `${meta}${k.owner ? " · " + k.owner : ""}`, x + 20, y + 74, colW - 34);

      let cx = x + 20;
      if (k.chronicle !== null) {
        cx += chip(g, k.chronicle ? "chronicle" : "no chronicle", cx, y + 88,
          k.chronicle ? "have" : "missing");
      }
      if (k.transcript !== null) {
        chip(g, k.transcript ? "transcript" : "no transcript", cx, y + 88,
          k.transcript ? "have" : "missing");
      }
      y += cardH + 10;
    }

    if (col.cards.length > shown.length) {
      g.fillStyle = "#7d6a5f";
      g.font = FONT(26, 700);
      clipText(g, `+${col.cards.length - shown.length} more, in the panel`, x + 6, y + 26, colW - 12);
    }
  });

  return c;
}

/* ------------------------------------------------------------------ build -- */

export function build(ctx) {
  const slot = ctx.room?.wall;
  if (!slot) return null;

  const board = ctx.world?.runtime?.board;
  const model = boardModel(board, ctx.stations || [], Date.now());

  const group = new THREE.Group();

  // Leaned back at the top, like a big board propped against the wall. The camera
  // looks down at about fifty-six degrees, so a dead-vertical face keeps barely a
  // third of its height on screen and the columns collapse into a stripe. A
  // NEGATIVE rotation about x swings the face upward, toward the camera. Positive
  // tilts it toward the floor, which is how it shipped the first time.
  const tilt = 0.62;
  group.position.set(slot.x, 0.44 + slot.h / 2, slot.z + (slot.h / 2) * Math.sin(tilt));
  group.rotation.x = -tilt;

  const frame = new THREE.Mesh(
    new THREE.BoxGeometry(slot.w + 0.16, slot.h + 0.16, 0.08),
    toon("#a8703f")
  );
  group.add(frame);

  const tex = new THREE.CanvasTexture(paint(model));
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const face = new THREE.Mesh(
    new THREE.PlaneGeometry(slot.w, slot.h),
    new THREE.MeshBasicMaterial({ map: tex })
  );
  face.position.z = 0.05;
  face.userData.fixture = { id, payload: board || null };
  group.add(face);

  // Sprites, for the same reason the mailroom and the clock use them: a sprite
  // always faces the camera, so it is the only text in this room that is legible
  // from the door. The board face carries the detail for anyone who orbits in;
  // these two carry the headline for everyone who does not.
  const label = tagSprite("the commissions board", { bg: "#4a3b33", fg: "#fdf6e8", scale: 1.15 });
  label.position.set(slot.x, 3.5, slot.z + 0.4);

  const line = model.up
    ? `${model.columns[0].total} running · ${model.columns[1].total} on the board · ` +
      `${model.totals.complete} done · ${model.totals.archived} archived`
    : model.note;
  // Red for down and error, plain for unconfigured. That is the same line
  // `paintStats` draws in the top bar: a stopped runtime is a fault worth
  // shouting about, an office that never had one is only a fact.
  const pill = model.up
    ? tagSprite(line, { scale: 1.05 })
    : model.state === "unconfigured"
      ? tagSprite(model.note, { scale: 1.15 })
      : tagSprite(model.note, { bg: "#d1495b", fg: "#fff", scale: 1.3 });
  pill.position.set(slot.x, 3.02, slot.z + 0.4);

  const holder = new THREE.Group();
  holder.add(group, label, pill);
  return holder;
}

/* ------------------------------------------------------------------ panel -- */

export function panel(payload, world, api) {
  const { el } = api;
  const wrap = el("div");
  const board = world?.runtime?.board ?? payload;
  const model = boardModel(board, world?.stations || [], Date.now());

  if (!model.up) {
    wrap.append(el("h3", null, model.note));
    wrap.append(el("p", "empty", model.state === "unconfigured"
      ? "This office has no local runtime pointed at it, so there is no board to read. That is not the same as a board with nothing on it."
      : "There is no board to read while the runtime is stopped. This is not an empty board: it is an unknown one."));
    if (model.detail) wrap.append(el("p", "log", model.detail));
    return wrap;
  }

  const t = model.totals;
  wrap.append(el("h3", null, "the real numbers"));
  const totals = el("p", "log");
  totals.append(
    el("b", null, String(t.active)), " active · ",
    el("b", null, String(t.complete)), " done · ",
    el("b", null, String(t.archived)), " archived · ",
    el("b", null, String(t.runs)), " runs"
  );
  wrap.append(totals);
  if (t.cost != null || t.cache != null) {
    const line = el("p", "log");
    if (t.cost != null) line.append(`$${t.cost.toFixed(2)} spent`);
    if (t.cost != null && t.cache != null) line.append(" · ");
    if (t.cache != null) line.append(`${Math.round(t.cache * 100)}% of reads came from cache`);
    wrap.append(line);
  }
  if (model.root) {
    wrap.append(el("p", "log", model.owner
      ? `${model.root} · the ${model.owner} desk`
      : `${model.root} · no desk in this room matches that path`));
  }

  for (const col of model.columns) {
    wrap.append(el("h3", null, `${col.label} (${col.total})`));

    if (col.key === "archived") {
      wrap.append(el("p", "empty", t.archived
        ? `${t.archived} archived commissions. The snapshot carries the count and nothing else, so their titles are not in this room.`
        : "Nothing has been archived."));
      continue;
    }
    if (!col.cards.length) {
      // A column counting twenty-two and holding no cards is not empty, and
      // saying "nothing here" over that number would be the same lie as
      // showing twelve and implying twelve is all of them.
      wrap.append(el("p", "empty", col.total
        ? `${col.total} of them, and none came through in this snapshot.`
        : col.key === "running"
          ? "Nothing is running right now."
          : "Nothing on this column."));
      continue;
    }
    for (const k of col.cards) wrap.append(cardNode(k, col, el));
    if (col.truncated) {
      wrap.append(el("p", "log",
        `Showing ${col.cards.length} of ${col.total}. The snapshot only carries the newest twelve.`));
    }
  }

  // Said plainly rather than faked. The issue asks for a per-card fetch of
  // /api/commission; only the laptop can reach the local runtime, and this page
  // holds no credentials for it.
  wrap.append(el("p", "p-detail",
    "This is everything the snapshot carries. The chronicle and the receipt for a " +
    "single commission live behind the local runtime, which only the laptop can reach, " +
    "so they are not in this room."));
  return wrap;
}

function cardNode(k, col, el) {
  const node = el("div", `issue${k.chronicle === false ? " hot" : ""}`);
  const top = el("div", "issue-top");
  top.append(el("span", "issue-title", k.title));
  node.append(top);
  if (k.slug && k.slug !== k.title) node.append(el("div", "issue-repo", k.slug));

  const meta = el("div", "issue-repo");
  meta.textContent = k.running
    ? `${k.elapsed ? `running for ${k.elapsed}` : "running"}${k.step ? ` · ${k.step}` : ""}`
    : k.age;
  if (k.owner) meta.textContent += ` · ${k.owner}`;
  node.append(meta);

  const tags = el("div", "tags");
  if (k.state) tags.append(el("span", "tag", k.state));
  if (k.chronicle !== null) {
    tags.append(el("span", `tag${k.chronicle ? "" : " hot"}`,
      k.chronicle ? "chronicle" : "no chronicle"));
  }
  if (k.transcript !== null) {
    tags.append(el("span", `tag${k.transcript ? "" : " hot"}`,
      k.transcript ? "transcript" : "no transcript"));
  }
  if (tags.childNodes.length) node.append(tags);
  void col;
  return node;
}

/**
 * Null on purpose. This fixture reads `world.runtime.board`, and the fake for
 * `?demo=1` is already written in `src/demo.js` under `runtime.board`. A second
 * fake here would be a second thing to keep true.
 */
export function demo() {
  return null;
}
