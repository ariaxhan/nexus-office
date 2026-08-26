import * as THREE from "three";
import { tagSprite } from "../kit.js";

/**
 * work in motion
 *
 * Two halves, both drawn from receipts and nothing else.
 *
 * ONE. The journey. A work item is a token that physically moves: in through the
 * door, onto a desk, out the back as a PR, or straight back at you as a refusal.
 * Every token on the floor is a claim that one specific line exists in the
 * receipt log, and a hop nobody wrote down is a hop that is not drawn. This
 * matters more here than it looks: the issue this belongs to (#9) started life as
 * "envelopes flying between agents", and that was cut because there is no
 * agent-to-agent messaging anywhere in this system. No mailboxes, no blackboard,
 * no messages. An envelope between two desks would be an animation of a
 * relationship we invented, which is worse than nothing because it looks like
 * telemetry. So: receipts only.
 *
 * TWO. The throughput lane. Today's counts as a strip along the front of the
 * floor, each outcome as long as its share of the day. Length is the honest
 * encoding, so twenty two deferred beside six landed looks like what it is. The
 * lane is not allowed to flatter.
 *
 * demo() returns null on purpose. This fixture reads ctx.stations (the desks as
 * placed, which is where a token travels to and from) and world.today (the
 * counts). Neither lives in world.sections, so there is no fake section to
 * supply, and inventing one would only shadow the real thing.
 */

export const id = "journey";
export const title = "work in motion";
export const wall = false;

/* ------------------------------------------------------ receipts to hops -- */

/**
 * What each receipt outcome asserts about something MOVING.
 *
 *   in    the lane took work up at this desk
 *   out   a branch left the desk
 *   back  it came back at the human, because only a person can move it on
 *
 * Everything absent from this map draws nothing, and that absence is the
 * interesting part. `deferred` means the issues were never reached, `caught-up`
 * means there was nothing to reach, `parked` means the pipeline declined to
 * touch the repo at all. In all three the receipt says explicitly that nothing
 * travelled, so nothing travels here. An unknown outcome is treated the same
 * way rather than guessed at.
 */
export const HOP_DIR = {
  survey: "in",
  landed: "out",
  refused: "back",
  "report-only": "back",
};

/** Minutes. Inside this, a token is a full bright mover; outside, it is trail. */
export const MOVING_MIN = 60;

/** Enough to read the shape of a day, few enough to stay a room and not a swarm. */
const MAX_TOKENS = 60;

const DIR_COLOR = { in: "#5b8dd9", out: "#3f9e6a", back: "#e07a3f" };

/**
 * Every receipt that says something moved, turned into a hop with two ends.
 *
 * Pure, and separated from the geometry on purpose: the drawing is not testable
 * but this is, and this is where the honesty lives.
 *
 * ends = { door, exit }, each { x, z }. The door is the near edge of the floor,
 * which is where you are standing. The exit is the back wall, which is where a
 * branch goes.
 */
export function hopsFrom(stations, now, ends) {
  const out = [];
  for (const st of stations || []) {
    for (const run of st.runs || []) {
      const dir = HOP_DIR[run.outcome];
      if (!dir) continue;
      const desk = { x: st.x, z: st.z };
      const ms = Date.parse(run.at);
      const ageMin = Number.isFinite(ms) ? (now - ms) / 60000 : Infinity;
      out.push({
        repo: st.repo,
        outcome: run.outcome,
        at: run.at,
        issue: run.issue || "",
        detail: run.detail || "",
        dir,
        from: dir === "in" ? ends.door : desk,
        to: dir === "out" ? ends.exit : dir === "back" ? ends.door : desk,
        ageMin,
        moving: ageMin <= MOVING_MIN,
      });
    }
  }
  out.sort((a, b) => a.ageMin - b.ageMin);
  return out.slice(0, MAX_TOKENS);
}

/* ---------------------------------------------------------------- the day -- */

/** Left to right is the flow: looked at, then everything that stopped, then landed. */
const LANE_ORDER = [
  ["survey", "#5b8dd9"],
  ["deferred", "#c9b99a"],
  ["dry-run", "#a9b7c6"],
  ["caught-up", "#8fa8bf"],
  ["no-issues", "#b7ad9c"],
  ["parked", "#8d99ae"],
  ["report-only", "#c08fb8"],
  ["refused", "#e07a3f"],
  ["landed", "#3f9e6a"],
];

export function laneSegments(today) {
  const rows = LANE_ORDER
    .map(([name, color]) => ({ name, color, n: Number(today?.[name]) || 0 }))
    .filter((r) => r.n > 0);
  const total = rows.reduce((a, r) => a + r.n, 0);
  return { rows, total };
}

/* ----------------------------------------------------------------- build -- */

const TOKEN = new THREE.BoxGeometry(0.66, 0.12, 0.46);

/** Deterministic per-token phase, so no two tokens ever share a starting gun and
 *  no frame, frozen or live, catches the whole floor parked at the door. */
function phaseOf(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 1000) / 1000;
}

function pathLine(hop, color, opacity) {
  const pts = [];
  for (let i = 0; i <= 14; i++) {
    const u = i / 14;
    pts.push(new THREE.Vector3(
      hop.from.x + (hop.to.x - hop.from.x) * u,
      0.03,
      hop.from.z + (hop.to.z - hop.from.z) * u
    ));
  }
  return new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity })
  );
}

function marker(x, z, label, color) {
  const g = new THREE.Group();
  const pad = new THREE.Mesh(
    new THREE.CircleGeometry(0.9, 20),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55 })
  );
  pad.rotation.x = -Math.PI / 2;
  pad.position.set(x, 0.02, z);
  g.add(pad);
  const tag = tagSprite(label, { bg: "#4a3b33", fg: "#fdf6e8", scale: 0.8 });
  tag.position.set(x, 1.0, z);
  g.add(tag);
  return g;
}

export function build(ctx) {
  const { stations, room, world } = ctx;
  if (!stations?.length) return null;

  // No wall is built on the near edge and it is the side the camera sits on, so
  // that end of the room is the door, and it is also you.
  const farZ = room.centre.z - room.floorD / 2;
  // Both ends sit INSIDE the box the camera promises to keep on screen, which is
  // the desks plus three, not the floor. Put the door on the floor's real near
  // edge and half the journey happens below the bottom of the picture.
  const ends = {
    door: { x: room.centre.x, z: room.maxZ + 3.0 },
    exit: { x: room.centre.x, z: farZ + 1.4 },
  };

  const hops = hopsFrom(stations, Date.now(), ends);
  const { rows, total } = laneSegments(world?.today);
  if (!hops.length && !total) return null;

  const g = new THREE.Group();

  /* the lane, added first so it is the steadiest click target in the fixture */
  if (total) {
    const laneZ = room.maxZ + 4.6;
    // Sized to the framed floor, not the whole floor. The first version was as
    // wide as the room and ran off both sides of the picture, which turns a
    // proportional bar into a bar that lies about its proportions.
    const laneW = room.maxX - room.minX + 5;
    const laneX0 = room.centre.x - laneW / 2;
    let x = laneX0;
    rows.forEach((r, i) => {
      const w = (r.n / total) * laneW;
      const seg = new THREE.Mesh(
        new THREE.PlaneGeometry(Math.max(w - 0.06, 0.05), 1.05),
        new THREE.MeshBasicMaterial({ color: r.color })
      );
      seg.rotation.x = -Math.PI / 2;
      seg.position.set(x + w / 2, 0.02, laneZ);
      seg.userData.fixture = { id, payload: { kind: "lane", today: world?.today || {}, focus: r.name } };
      g.add(seg);

      const tag = tagSprite(`${r.name} ${r.n}`, { bg: "#fffdf5", fg: "#4a3b33", scale: 0.62 });
      tag.position.set(x + w / 2, 0.55, laneZ + (i % 2 ? 0.95 : -0.95));
      g.add(tag);
      x += w;
    });

    const head = tagSprite(`today  ${total} runs`, { bg: "#4a3b33", fg: "#fdf6e8", scale: 0.8 });
    head.position.set(laneX0 - 0.4, 1.05, laneZ);
    g.add(head);
  }

  if (!hops.length) return g;

  g.add(marker(ends.door.x, ends.door.z, "you", "#e0b28a"));
  g.add(marker(ends.exit.x, ends.exit.z, "PR", "#9fd6b3"));

  for (const hop of hops) {
    const color = DIR_COLOR[hop.dir];
    g.add(pathLine(hop, color, hop.moving ? 0.42 : 0.16));

    const base = hop.moving ? 1 : 0.5;
    const mesh = new THREE.Mesh(TOKEN, new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: base,
    }));
    // Recent work is loud and quick; the rest of the day is a slow faint drift
    // along the same route, which is the trail. Both are real receipts.
    const dur = hop.moving ? 5.2 : 13;
    const scale = hop.moving ? 1 : 0.72;
    const phase = phaseOf(hop.repo + hop.at + hop.outcome);
    const yaw = Math.atan2(hop.to.x - hop.from.x, hop.to.z - hop.from.z);
    mesh.scale.setScalar(scale);
    mesh.userData.fixture = { id, payload: { kind: "hop", hop } };
    // Culling is decided from the matrix of the previous frame, and these move.
    mesh.frustumCulled = false;

    const t0 = performance.now();
    /**
     * The render loop in Office.start() ticks villagers and nothing else, so a
     * fixture that wants to move has to move itself. onBeforeRender is called by
     * the renderer just before this mesh is drawn, which keeps the whole thing
     * inside this file and, more usefully, means it still runs when the shot
     * harness freezes the room and renders one frame by hand.
     *
     * updateMatrixWorld right here is load bearing twice over. The renderer
     * builds modelViewMatrix AFTER this hook, so without it the token draws one
     * frame behind where it thinks it is, and picking reads matrixWorld too.
     */
    mesh.onBeforeRender = () => {
      const t = (performance.now() - t0) / 1000;
      const u = ((t / dur) + phase) % 1;
      mesh.position.set(
        hop.from.x + (hop.to.x - hop.from.x) * u,
        0.34 + Math.sin(Math.PI * u) * 0.8,
        hop.from.z + (hop.to.z - hop.from.z) * u
      );
      mesh.rotation.set(Math.sin(t * 1.7 + phase * 6) * 0.25, yaw, 0.35);
      // Fade at both ends so the loop back to the door is a departure and an
      // arrival, never a token teleporting across the room.
      mesh.material.opacity = base * Math.min(1, Math.sin(Math.PI * u) * 2.4);
      mesh.updateMatrixWorld(true);
    };
    // Set once up front as well, so a click that lands before the first render
    // is tested against a real position rather than the origin.
    mesh.onBeforeRender();
    g.add(mesh);
  }

  return g;
}

/* ----------------------------------------------------------------- panel -- */

const WHEN = (iso) => {
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(m)) return "at an unknown time";
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (m < 60 * 36) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
};

const SAYS = {
  in: "came in through the door and onto this desk",
  out: "left this desk as a branch",
  back: "came back off this desk toward you, because only a person can move it on",
};

function hopPanel(hop, world, api) {
  const { el } = api;
  const box = el("div", "fx-journey");

  box.append(el("h3", null, `${hop.outcome}  ${hop.repo}`));
  box.append(el("p", null, `This token ${SAYS[hop.dir]}. It moved ${WHEN(hop.at)}.`));
  box.append(el("p", "empty", hop.moving
    ? "Inside the last hour, so it is drawn at full weight."
    : "Older than an hour, so it is drawn faint: part of the trail rather than the traffic."));

  const st = (world?.stations || []).find((s) => s.repo === hop.repo);
  const issue = (st?.issues || []).find((i) => String(i.number) === String(hop.issue));
  box.append(el("h3", null, "the issue"));
  if (issue) {
    const a = el("a", "link", `#${issue.number}  ${issue.title}`);
    a.href = issue.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    box.append(a);
  } else if (hop.issue) {
    box.append(el("p", "empty", `#${hop.issue}, which is no longer open on this desk.`));
  } else {
    box.append(el("p", "empty", "This receipt names no issue. It is a run against the repo as a whole."));
  }

  box.append(el("h3", null, "the receipt that moved it"));
  const pre = el("pre", "fx-receipt");
  pre.style.whiteSpace = "pre-wrap";
  pre.style.fontSize = "12px";
  pre.textContent = [
    `at      ${hop.at}`,
    `outcome ${hop.outcome}`,
    `issue   ${hop.issue || "(none)"}`,
    `detail  ${hop.detail || "(none)"}`,
  ].join("\n");
  box.append(pre);
  box.append(el("p", "empty", "No receipt, no hop. Nothing here is inferred."));
  return box;
}

function lanePanel(today, focus, api) {
  const { el } = api;
  const box = el("div", "fx-journey");
  const { rows, total } = laneSegments(today);
  box.append(el("p", null, total
    ? `${total} runs today, drawn along the floor at their real proportions.`
    : "Nothing has run today."));

  for (const r of rows) {
    const row = el("div", "fx-row");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "8px";
    row.style.margin = "4px 0";
    row.style.opacity = focus && focus !== r.name ? "0.5" : "1";

    const name = el("span", null, r.name);
    name.style.flex = "0 0 92px";
    name.style.fontSize = "12px";

    const bar = el("span");
    bar.style.height = "10px";
    bar.style.borderRadius = "5px";
    bar.style.background = r.color;
    bar.style.width = `${Math.max((r.n / total) * 100, 1.5)}%`;

    const n = el("span", null, String(r.n));
    n.style.fontSize = "12px";

    row.append(name, bar, n);
    box.append(row);
  }
  box.append(el("p", "empty",
    "Length is the count. A day where deferred dwarfs landed is drawn as a day where deferred dwarfs landed."));
  return box;
}

export function panel(payload, world, api) {
  if (payload?.kind === "hop") return hopPanel(payload.hop, world, api);
  if (payload?.kind === "lane") return lanePanel(payload.today, payload.focus, api);
  return api.el("p", "empty", "Nothing on the floor to explain.");
}

/** Null on purpose: this fixture reads ctx.stations and world.today, never
 *  world.sections, so it has no fake section of its own to hand the demo. */
export function demo() {
  return null;
}
