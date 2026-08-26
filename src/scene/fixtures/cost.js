/**
 * the cost chart
 *
 * A spend chart hung on the back wall: fourteen daily bars, the lifetime total
 * over them, and the ugly cases said out loud instead of swept up.
 *
 * Drawn to a canvas and hung on a plane rather than modelled as fourteen boxes.
 * A chart is mostly text, and text is the thing three.js is worst at: one canvas
 * gives labels, an axis and a hatch pattern for a single draw call, which is the
 * same trade `kit.js` already makes for faces and name plaques.
 *
 * The one rule that shapes everything here: **estimated money never draws like
 * measured money.** Measured is a solid block. Estimated is hatched, in another
 * colour, under a dashed lid. Rows written before the `estimate` flag existed
 * are a third thing again, drawn hollow, because "we do not know" is not the
 * same claim as "we measured it".
 *
 * Data comes from `client/sources/cost.py`. See that file for the schema census
 * behind all of this: the ledger is nine schemas in a trench coat.
 */

import * as THREE from "three";

export const id = "cost";
export const title = "the cost chart";
export const wall = true;

const INK = "#4a3b33";
const PAPER = "#fffdf5";
const MEASURED = "#2f6f6b";
const ESTIMATED = "#d98c3f";
const UNFLAGGED = "#9a8f86";

/* ------------------------------------------------------------- formatting -- */

const money = (n) => {
  const v = Number(n) || 0;
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 0 : abs >= 1 ? 2 : abs > 0 ? 3 : 2;
  return "$" + v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

const pct = (f) => `${(Number(f) * 100).toFixed(f > 0 && f < 0.001 ? 3 : 1)}%`;

const dayTotal = (d) => (d.measured || 0) + (d.estimated || 0) + (d.unflagged || 0);

/** "08-25", which is all the room can read at this size anyway. */
const shortDay = (iso) => String(iso || "").slice(5);

/** A breakdown key, with the missing bucket named rather than dumped in. */
const keyLabel = (row, field) => (row.missing ? `no ${field} recorded` : row.key);

/**
 * What the board says when there is no chart to draw. Each state gets its own
 * words on purpose: "nothing is configured", "the ledger is not there", "the
 * ledger is broken" and "the ledger says zero" are four different facts, and a
 * room that renders them identically is lying about three of them.
 */
function excuse(section) {
  const s = section?.state;
  if (!section || s === undefined) return ["no ledger reaching this room", "nothing has sent a cost section yet"];
  if (s === "unconfigured") return ["no ledger configured", "set OFFICE_RUNTIME_ROOT and this fills in"];
  if (s === "missing-root") return ["the vault root is not there", section.detail || ""];
  if (s === "missing-ledger") return ["no ledger on disk", "nothing has written costs.jsonl yet"];
  if (s === "empty") return ["the ledger is empty", "zero rows, which is not the same as zero spend"];
  if (s === "error" || s === "unbuilt") return ["the ledger could not be read", section.detail || ""];
  return null;
}

/* ---------------------------------------------------------------- drawing -- */

/** Diagonal stripes. This is the whole "do not read me as a measurement" signal. */
function hatch(colour) {
  const c = document.createElement("canvas");
  c.width = c.height = 14;
  const g = c.getContext("2d");
  g.fillStyle = PAPER;
  g.fillRect(0, 0, 14, 14);
  g.strokeStyle = colour;
  g.lineWidth = 5;
  g.beginPath();
  g.moveTo(-7, 7);
  g.lineTo(7, -7);
  g.moveTo(0, 14);
  g.lineTo(14, 0);
  g.moveTo(7, 21);
  g.lineTo(21, 7);
  g.stroke();
  return g.createPattern(c, "repeat");
}

/**
 * Pixels per world unit, and the widest the board is allowed to get.
 *
 * The room camera looks DOWN at the back wall, which foreshortens height by
 * roughly half. A board that uses its whole slot ends up a seven-to-one letterbox
 * on screen with type too small to read, which is how the first version shipped.
 * Narrower and full height is the fix: same slot, squarer picture, bigger words.
 */
const PPU = 340;
const MAX_W = 5.0;

function font(g, size, weight = 600) {
  g.font = `${weight} ${size}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
}

/** A legend chip: the fill, then the words, in the fill's own idiom. */
function chip(g, x, y, size, kind, colour) {
  if (kind === "solid") {
    g.fillStyle = colour;
    g.fillRect(x, y, size, size);
  } else if (kind === "hatch") {
    g.fillStyle = hatch(colour);
    g.fillRect(x, y, size, size);
    g.strokeStyle = colour;
    g.lineWidth = size * 0.14;
    g.setLineDash([size * 0.26, size * 0.22]);
    g.strokeRect(x, y, size, size);
    g.setLineDash([]);
  } else {
    g.fillStyle = PAPER;
    g.fillRect(x, y, size, size);
    g.strokeStyle = colour;
    g.lineWidth = size * 0.14;
    g.strokeRect(x, y, size, size);
  }
}

function chartTexture(section, w, h) {
  const cw = Math.round(w * PPU);
  const ch = Math.round(h * PPU);
  const c = document.createElement("canvas");
  c.width = cw;
  c.height = ch;
  const g = c.getContext("2d");

  g.fillStyle = PAPER;
  g.fillRect(0, 0, cw, ch);

  const pad = ch * 0.075;
  const why = excuse(section);
  if (why) {
    g.textAlign = "center";
    font(g, ch * 0.13, 700);
    g.fillStyle = "#8a7a6f";
    g.fillText("the cost chart", cw / 2, ch * 0.3);
    font(g, ch * 0.155, 800);
    g.fillStyle = "#b4544f";
    g.fillText(why[0], cw / 2, ch * 0.56);
    if (why[1]) {
      font(g, ch * 0.085, 500);
      g.fillStyle = INK;
      g.fillText(String(why[1]).slice(0, 70), cw / 2, ch * 0.78);
    }
    return new THREE.CanvasTexture(c);
  }

  const days = section.days || [];
  const life = section.lifetime || {};
  const undated = section.undated || { rows: 0, value: 0 };
  const hasUnflagged = (life.unflagged || 0) > 0;

  /* -- the headline half: one number, as big as the board allows ----------- */
  // Every vertical position below is a fraction of ch, budgeted once so the
  // legend, the axis labels and the footnote cannot grow into each other. They
  // did, and the first legible version had three lines stacked on one.
  const colW = cw * 0.44;
  g.textAlign = "left";
  g.textBaseline = "alphabetic";

  font(g, ch * 0.085, 700);
  g.fillStyle = "#a2938a";
  g.fillText("SPEND, ALL TIME", pad, pad + ch * 0.08);

  font(g, ch * 0.26, 800);
  g.fillStyle = INK;
  g.fillText(money(life.total), pad, ch * 0.40);

  // Measured and estimated, each beside its own fill. This IS the legend: two
  // numbers that also teach the two textures on the bars.
  const sw = ch * 0.085;
  let ly = ch * 0.46;
  const row = (value, label, kind, colour) => {
    chip(g, pad, ly, sw, kind, colour);
    font(g, ch * 0.095, 800);
    g.fillStyle = INK;
    g.fillText(money(value), pad + sw * 1.5, ly + sw * 0.86);
    const vw = g.measureText(money(value)).width;
    font(g, ch * 0.072, 600);
    g.fillStyle = "#8a7a6f";
    g.fillText(label, pad + sw * 1.5 + vw + sw * 0.4, ly + sw * 0.86);
    ly += sw * 1.45;
  };
  row(life.measured, "measured", "solid", MEASURED);
  row(life.estimated, `estimated, ${pct(section.estimated_fraction || 0)}`, "hatch", ESTIMATED);
  if (hasUnflagged) row(life.unflagged, "never flagged", "hollow", UNFLAGGED);

  /* -- the plot half ------------------------------------------------------- */
  const x0 = colW + pad * 0.3;
  const x1 = cw - pad;
  const top = ch * 0.22;
  const base = ch * 0.80;
  const plotH = base - top;
  const peak = Math.max(...days.map(dayTotal), 0);

  g.textAlign = "right";
  font(g, ch * 0.075, 700);
  g.fillStyle = "#a2938a";
  g.fillText(`LAST ${days.length} DAYS` + (peak > 0 ? `  ·  PEAK ${money(peak)}` : ""),
    x1, pad + ch * 0.08);

  g.strokeStyle = "rgba(74,59,51,0.3)";
  g.lineWidth = Math.max(2, ch * 0.008);
  g.beginPath();
  g.moveTo(x0, base);
  g.lineTo(x1, base);
  g.stroke();

  if (peak <= 0) {
    g.textAlign = "center";
    font(g, ch * 0.1, 700);
    g.fillStyle = "#8a7a6f";
    g.fillText("no spend in this window", (x0 + x1) / 2, top + plotH * 0.6);
  } else {
    const slot = (x1 - x0) / days.length;
    const bw = slot * 0.7;
    const hatchFill = hatch(ESTIMATED);

    days.forEach((d, i) => {
      const bx = x0 + slot * i + (slot - bw) / 2;
      let y = base;
      for (const [v, colour, kind] of [
        [d.measured || 0, MEASURED, "solid"],
        [d.estimated || 0, ESTIMATED, "hatch"],
        [d.unflagged || 0, UNFLAGGED, "hollow"],
      ]) {
        if (v <= 0) continue;
        const bh = Math.max((v / peak) * plotH, ch * 0.012);
        y -= bh;
        if (kind === "solid") {
          g.fillStyle = colour;
          g.fillRect(bx, y, bw, bh);
        } else if (kind === "hatch") {
          g.fillStyle = hatchFill;
          g.fillRect(bx, y, bw, bh);
          g.strokeStyle = colour;
          g.lineWidth = ch * 0.012;
          g.setLineDash([ch * 0.03, ch * 0.022]);
          g.strokeRect(bx + 1, y + 1, bw - 2, bh - 2);
          g.setLineDash([]);
        } else {
          g.fillStyle = PAPER;
          g.fillRect(bx, y, bw, bh);
          g.strokeStyle = colour;
          g.lineWidth = ch * 0.012;
          g.strokeRect(bx + 1, y + 1, bw - 2, bh - 2);
        }
      }
    });

    // Direction, stated, not left to a convention. Three labels rather than
    // fourteen: at this size fourteen dates are a grey smear.
    const lastX = x0 + slot * (days.length - 1) + slot / 2;
    g.textAlign = "left";
    font(g, ch * 0.07, 600);
    g.fillStyle = "#a2938a";
    g.fillText(shortDay(days[0].date), x0, base + ch * 0.095);
    g.textAlign = "center";
    font(g, ch * 0.08, 800);
    g.fillStyle = INK;
    g.fillText("today", lastX, base + ch * 0.095);
  }

  /* -- the rows that could not be drawn, said out loud --------------------- */
  const notes = [];
  if (undated.rows) notes.push(`${undated.rows} undated row${undated.rows === 1 ? "" : "s"}, ${money(undated.value)}, on no bar`);
  if (section.unparseable) notes.push(`${section.unparseable} unreadable line${section.unparseable === 1 ? "" : "s"}`);
  if (notes.length) {
    g.textAlign = "left";
    font(g, ch * 0.072, 700);
    g.fillStyle = "#b4544f";
    g.fillText(notes.join("   ·   "), pad, ch * 0.955);
  }

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

/* ------------------------------------------------------------------ build -- */

export function build(ctx) {
  const slot = ctx.room?.wall;
  if (!slot) return null;

  const section = ctx.section;
  const w = Math.min(slot.w, MAX_W);
  const h = slot.h;
  const group = new THREE.Group();

  // A dark surround, because a cream chart on a cream wall has no edges.
  const frame = new THREE.Mesh(
    new THREE.BoxGeometry(w, h, 0.09),
    new THREE.MeshBasicMaterial({ color: INK })
  );
  const face = new THREE.Mesh(
    new THREE.PlaneGeometry(w - 0.14, h - 0.14),
    new THREE.MeshBasicMaterial({ map: chartTexture(section, w - 0.14, h - 0.14) })
  );
  face.position.z = 0.055;

  const payload = section ?? null;
  frame.userData.fixture = { id, payload };
  face.userData.fixture = { id, payload };

  group.add(frame, face);
  group.position.set(slot.x, 0.25 + h / 2, slot.z);
  return group;
}

/* ------------------------------------------------------------------ panel -- */

export function panel(payload, world, api) {
  const { el } = api;
  const wrap = el("div");
  void world;

  const why = excuse(payload);
  if (why) {
    wrap.append(el("p", "empty", `${why[0]}. ${why[1] || ""}`.trim()));
    if (payload?.path) wrap.append(el("p", "p-detail", payload.path));
    return wrap;
  }

  const life = payload.lifetime || {};
  const undated = payload.undated || { rows: 0, value: 0 };

  const line = (label, value, cls) => {
    const p = el("p", cls || "log");
    p.append(el("b", null, value), " ", label);
    return p;
  };

  wrap.append(el("h3", null, "lifetime"));
  wrap.append(line(`over ${payload.rows} rows`, money(life.total)));
  wrap.append(line("measured", money(life.measured)));

  // The estimated split leads with its fraction, because the fraction is the
  // number that decides whether the total above can be trusted.
  const est = el("p", "log");
  est.append(el("b", null, money(life.estimated)), ` estimated, ${pct(payload.estimated_fraction || 0)} of the total`);
  wrap.append(est);
  if ((life.estimated || 0) === 0) {
    wrap.append(el("p", "p-detail",
      "No row in this ledger is flagged as an estimate, so every dollar above claims to be measured."));
  }

  if ((life.unflagged || 0) > 0 || payload.unpriced) {
    wrap.append(line("on rows written before the estimate flag existed, so neither measured nor estimated", money(life.unflagged)));
  }
  if (payload.unpriced) {
    wrap.append(line("rows carry no money field at all", payload.unpriced));
  }

  wrap.append(el("h3", null, `the last ${payload.window_days} days`));
  wrap.append(line(`through ${payload.today}`, money(payload.window_total)));
  if (payload.latest_day && payload.latest_day !== payload.today) {
    wrap.append(el("p", "p-detail", `The newest dated row is ${payload.latest_day}. Nothing has been written since.`));
  }

  // The whole point of the fixture. Undated rows have real money in them, they
  // are inside the lifetime total, and they cannot be plotted. All three facts
  // get said rather than one.
  wrap.append(el("h3", null, "rows with no date"));
  if (!undated.rows) {
    wrap.append(el("p", "empty", "Every row carries a usable timestamp."));
  } else {
    const p = el("p", "log");
    p.append(el("b", null, `${undated.rows} row${undated.rows === 1 ? "" : "s"}`),
      ` worth ${money(undated.value)}. Counted in the lifetime total, absent from every bar, because there is no day to put them on.`);
    wrap.append(p);
  }
  if (payload.unparseable) {
    wrap.append(line("line(s) in the ledger did not parse and hold an unknown amount", payload.unparseable, "log"));
  }

  for (const [heading, rows, field] of [
    ["by family", payload.by_family || [], "family"],
    ["by source", payload.by_source || [], "source"],
  ]) {
    wrap.append(el("h3", null, heading));
    if (!rows.length) {
      wrap.append(el("p", "empty", "Nothing recorded."));
      continue;
    }
    for (const r of rows.slice(0, 12)) {
      const p = el("p", "log");
      p.append(el("b", null, money(r.value)), " ");
      p.append(el("span", r.missing ? "bad" : null, keyLabel(r, field)));
      p.append(` · ${r.rows} row${r.rows === 1 ? "" : "s"}`);
      wrap.append(p);
    }
  }

  wrap.append(el("p", "p-detail", payload.path || ""));
  return wrap;
}

/* ------------------------------------------------------------------- demo -- */

/**
 * The demo floor exists so the ugly cases get exercised on every screenshot, not
 * only when the real ledger happens to be misbehaving. So this fake carries a
 * fat estimated band, a pre-flag unflagged row, and undated money.
 */
export function demo() {
  const today = new Date();
  const iso = (back) => {
    const d = new Date(today);
    d.setDate(d.getDate() - back);
    return d.toISOString().slice(0, 10);
  };
  const shape = [
    [4.1, 0, 0], [6.8, 0, 0], [2.2, 3.9, 0], [9.4, 0, 0.31],
    [0, 0, 0], [5.6, 1.2, 0], [12.7, 0, 0], [7.3, 4.8, 0],
    [1.4, 0, 0], [0, 0, 0], [8.9, 0, 0], [14.2, 6.1, 0],
    [3.7, 0, 0], [11.5, 2.4, 0],
  ];
  const days = shape.map(([m, e, u], i) => ({
    date: iso(shape.length - 1 - i),
    measured: m, estimated: e, unflagged: u,
    rows: Math.round(m + e + u) + 1,
  }));
  const sum = (k) => days.reduce((a, d) => a + d[k], 0);
  const measured = sum("measured") + 61.4;
  const estimated = sum("estimated") + 9.2;
  const unflagged = sum("unflagged") + 0.87;
  const total = measured + estimated + unflagged;

  return {
    state: "ok",
    path: "/demo/_meta/logs/costs.jsonl",
    currency: "USD",
    rows: 1183,
    unparseable: 1,
    unpriced: 0,
    window_days: 14,
    today: iso(0),
    latest_day: iso(0),
    days,
    window_total: sum("measured") + sum("estimated") + sum("unflagged"),
    lifetime: {
      total, measured, estimated, unflagged,
    },
    estimated_fraction: estimated / total,
    undated: { rows: 7, value: 2.44 },
    by_family: [
      { key: "opus", value: total * 0.61, rows: 604, missing: false },
      { key: "sonnet", value: total * 0.24, rows: 331, missing: false },
      { key: "haiku", value: total * 0.09, rows: 238, missing: false },
      { key: null, value: total * 0.06, rows: 10, missing: true },
    ],
    by_source: [
      { key: "lane", value: total * 0.58, rows: 641, missing: false },
      { key: "transcript", value: total * 0.36, rows: 528, missing: false },
      { key: null, value: total * 0.06, rows: 14, missing: true },
    ],
  };
}
