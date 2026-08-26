/**
 * the wall clock
 *
 * What is scheduled on this machine, and which of it stopped firing.
 *
 * A job that stopped firing looks exactly like a job with nothing to do, so the
 * ONE thing this fixture has to do is make a stale job wrong from across the
 * room. Everything else on it is supporting detail. That is why the alarm is not
 * a small red dot: the rim goes red, a halo appears behind the whole clock, and
 * a banner sits over it saying how many. At room-wide zoom you cannot miss it,
 * and at room-wide zoom is where you will be standing.
 *
 * `off` is deliberately NOT red. It is a decision somebody made, and dressing a
 * decision as a fault is how a paused job becomes an outage nobody investigates.
 * `never fired` gets its own colour for the same reason: a job that has never run
 * usually has a bad path, not a bug, and that is a different afternoon.
 *
 * The face is one canvas texture rather than a pile of geometry, the same trick
 * kit.js uses for villager faces: one draw call, and legible text at a distance.
 */

import * as THREE from "three";
import { roundRect, toon, tagSprite } from "../kit.js";

export const id = "clock";
export const title = "the wall clock";
export const wall = true;

/** One colour per state, and one plain-language name. Used by both halves. */
const STATE = {
  stale: { dot: "#d1495b", name: "stale", alarm: true },
  failing: { dot: "#e07a3f", name: "failing", alarm: true },
  never: { dot: "#7b5ea7", name: "never fired", alarm: true },
  unknown: { dot: "#5b8dd9", name: "unknown", alarm: true },
  off: { dot: "#a9a29b", name: "switched off", alarm: false },
  ok: { dot: "#3f9e6a", name: "on time", alarm: false },
};
const spec = (s) => STATE[s] || STATE.unknown;

const CREAM = "#fffdf5";
const INK = "#4a3b33";
const QUIET_RIM = "#8a6f5e";
const UNWATCHED = "#d9a441";

/** Why the source could not answer, said out loud rather than drawn as zero. */
const BROKEN = {
  unconfigured: "no runtime root is configured, so nothing here knows what is scheduled",
  "missing-root": "the runtime root does not exist on this machine",
  missing: "there is no jobctl here to ask",
  timeout: "jobctl did not answer in time",
  unreadable: "jobctl answered with something that did not parse",
  error: "reading the job registry failed",
  unbuilt: "this source has not been built yet",
};

/** The headline, in the fewest words that are still true. */
function headline(sec) {
  const c = sec.counts || {};
  const bits = [];
  if (c.stale) bits.push(`${c.stale} stale`);
  if (c.failing) bits.push(`${c.failing} failing`);
  if (c.never) bits.push(`${c.never} never ran`);
  if (bits.length) return bits.join(", ");
  if (sec.unwatched) return `${sec.unwatched} unwatched`;
  return `${sec.checked || 0} jobs on time`;
}

/**
 * The same fact in two or three words, for the middle of the face.
 *
 * The banner above the clock carries the breakdown. What the face needs is the
 * one word you can read from the far side of the room, so it says how many and
 * stops.
 */
function shortHeadline(sec) {
  const c = sec.counts || {};
  const alarm = sec.alarm || 0;
  if (!alarm) return sec.unwatched ? `${sec.unwatched} unwatched` : "all on time";
  if (alarm === c.stale) return `${alarm} stale`;
  if (alarm === c.never) return `${alarm} never ran`;
  if (alarm === c.failing) return `${alarm} failing`;
  return `${alarm} need a look`;
}

/* ----------------------------------------------------------------- the face -- */

const S = 768;

function faceTexture(sec) {
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  const R = S * 0.46;
  const cx = S / 2, cy = S / 2;

  const broken = sec.state !== "ok";
  const alarm = !broken && (sec.alarm || 0) > 0;
  const rim = broken ? UNWATCHED : alarm ? STATE.stale.dot : QUIET_RIM;

  g.clearRect(0, 0, S, S);

  g.fillStyle = CREAM;
  g.beginPath();
  g.arc(cx, cy, R, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = rim;
  g.lineWidth = alarm || broken ? S * 0.055 : S * 0.03;
  g.stroke();

  // The hour ticks. Enough clock to read as a clock, and no more.
  g.strokeStyle = "rgba(74,59,51,0.45)";
  g.lineCap = "round";
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2 - Math.PI / 2;
    const long = i % 3 === 0;
    g.lineWidth = long ? S * 0.016 : S * 0.009;
    g.beginPath();
    g.moveTo(cx + Math.cos(a) * R * 0.9, cy + Math.sin(a) * R * 0.9);
    g.lineTo(cx + Math.cos(a) * R * (long ? 0.8 : 0.84), cy + Math.sin(a) * R * (long ? 0.8 : 0.84));
    g.stroke();
  }

  if (broken) {
    g.fillStyle = UNWATCHED;
    g.font = `800 ${Math.round(S * 0.3)}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText("?", cx, cy - S * 0.03);
    wrapped(g, BROKEN[sec.state] || sec.state, cx, cy + S * 0.16, R * 1.5, S * 0.045, INK);
    return new THREE.CanvasTexture(c);
  }

  // One bead per job, around the rim. Forty of them read as a band of colour,
  // which is exactly the summary you want from ten feet away: how much of the
  // ring is green, and whether any of it is red.
  const jobs = sec.jobs || [];
  const beadR = R * 0.72;
  for (let i = 0; i < jobs.length; i++) {
    const j = jobs[i];
    const sp = spec(j.state);
    const a = (i / Math.max(jobs.length, 1)) * Math.PI * 2 - Math.PI / 2;
    const x = cx + Math.cos(a) * beadR;
    const y = cy + Math.sin(a) * beadR;
    const r = sp.alarm ? S * 0.032 : S * 0.017;
    g.fillStyle = sp.dot;
    g.globalAlpha = j.state === "off" ? 0.55 : 1;
    g.beginPath();
    g.arc(x, y, r, 0, Math.PI * 2);
    g.fill();
    g.globalAlpha = 1;
    // Nothing is watching this one. A hollow amber collar, so it is legible
    // as a marker and never mistaken for a fault colour.
    if (j.unwatched) {
      g.strokeStyle = UNWATCHED;
      g.lineWidth = S * 0.009;
      g.beginPath();
      g.arc(x, y, r + S * 0.014, 0, Math.PI * 2);
      g.stroke();
    }
  }

  // Real hands, at the real time. This is a clock, and a clock that does not
  // tell the time is a pie chart wearing a hat.
  const now = new Date();
  const hand = (angle, len, width, color) => {
    g.strokeStyle = color;
    g.lineWidth = width;
    g.beginPath();
    g.moveTo(cx, cy);
    g.lineTo(cx + Math.cos(angle) * len, cy + Math.sin(angle) * len);
    g.stroke();
  };
  const mins = now.getMinutes() + now.getSeconds() / 60;
  const hours = (now.getHours() % 12) + mins / 60;
  hand((hours / 12) * Math.PI * 2 - Math.PI / 2, R * 0.34, S * 0.022, INK);
  hand((mins / 60) * Math.PI * 2 - Math.PI / 2, R * 0.5, S * 0.015, INK);
  g.fillStyle = INK;
  g.beginPath();
  g.arc(cx, cy, S * 0.017, 0, Math.PI * 2);
  g.fill();

  // The headline sits under the hands, big enough to read across the room. It
  // gets its own plate: the bead ring runs behind it, and a count you have to
  // pick out of a row of dots is a count you will misread.
  const alarming = (sec.alarm || 0) > 0;
  const text = shortHeadline(sec).toUpperCase();
  const size = Math.round(S * (alarming ? 0.085 : 0.058));
  g.font = `800 ${size}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  const ty = cy + R * 0.54;
  const tw = g.measureText(text).width;
  g.fillStyle = CREAM;
  roundRect(g, cx - tw / 2 - size * 0.4, ty - size * 0.72,
    tw + size * 0.8, size * 1.44, size * 0.5);
  g.fill();
  g.fillStyle = alarming ? STATE.stale.dot : INK;
  g.fillText(text, cx, ty);

  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function wrapped(g, text, cx, y, maxW, size, color) {
  g.fillStyle = color;
  g.font = `600 ${Math.round(size)}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  let line = "";
  const lines = [];
  for (const w of String(text).split(" ")) {
    const next = line ? `${line} ${w}` : w;
    if (g.measureText(next).width > maxW && line) { lines.push(line); line = w; }
    else line = next;
  }
  if (line) lines.push(line);
  lines.slice(0, 4).forEach((l, i) => g.fillText(l, cx, y + i * size * 1.35));
}

/* ---------------------------------------------------------------- the build -- */

export function build(ctx) {
  const slot = ctx.room?.wall;
  if (!slot) return null;

  // No section at all is still a clock, drawn as an honest question mark. A
  // fixture that vanishes when its data does is a fixture that lies.
  const sec = ctx.section || { state: "unbuilt" };
  const broken = sec.state !== "ok";
  const alarm = !broken && (sec.alarm || 0) > 0;

  const D = Math.min(slot.w - 0.2, slot.h, 2.2);
  if (D <= 0.2) return null;

  const group = new THREE.Group();
  group.position.set(slot.x, 1.3, slot.z + 0.09);

  // The halo. Only when something is wrong, and big enough that the wall itself
  // changes colour behind the clock. This is the across-the-room signal.
  if (alarm || broken) {
    const halo = new THREE.Mesh(
      new THREE.CircleGeometry(D * 0.58, 40),
      new THREE.MeshBasicMaterial({
        color: broken ? UNWATCHED : STATE.stale.dot,
        transparent: true,
        opacity: 0.28,
        depthWrite: false,
      })
    );
    halo.position.z = -0.02;
    group.add(halo);
  }

  const back = new THREE.Mesh(
    new THREE.CircleGeometry(D * 0.56, 44),
    toon(broken ? UNWATCHED : alarm ? STATE.stale.dot : "#6f5648")
  );
  back.position.z = -0.008;
  group.add(back);

  const face = new THREE.Mesh(
    new THREE.CircleGeometry(D * 0.5, 48),
    new THREE.MeshBasicMaterial({ map: faceTexture(sec), transparent: true })
  );
  face.userData.fixture = { id, payload: sec };
  group.add(face);

  const banner = tagSprite(
    broken ? "jobs: no data" : alarm ? headline(sec) : `${sec.checked || 0} jobs, all on time`,
    alarm || broken
      ? { bg: broken ? UNWATCHED : STATE.stale.dot, fg: "#fff", scale: 1.9 }
      : { scale: 1.3 }
  );
  banner.position.set(0, D * 0.66, 0.05);
  group.add(banner);

  return group;
}

/* ---------------------------------------------------------------- the panel -- */

const relative = (iso) => {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return "never";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
};

function card(el, j) {
  const sp = spec(j.state);
  const box = el("div", `issue${sp.alarm ? " hot" : ""}`);

  const top = el("div", "issue-top");
  top.append(el("span", "issue-title", j.id));
  box.append(top);

  const tags = el("div", "tags");
  const state = el("span", `tag${sp.alarm ? " hot" : ""}`, sp.name);
  if (!sp.alarm) state.style.color = sp.dot;
  tags.append(state);
  tags.append(el("span", "tag", j.schedule || "unscheduled"));
  tags.append(el("span", "tag", j.watch === "nothing" ? "unwatched" : `watched: ${j.watch}`));
  if (j.owner) tags.append(el("span", "tag", j.owner));
  box.append(tags);

  if (j.detail) box.append(el("p", "p-detail", j.detail));

  const line = el("p", "log");
  line.append(el("b", null, "last success"), " ");
  line.append(el("span", j.last_success ? "" : "bad", relative(j.last_success)));
  line.append(" · ", el("b", null, "last attempt"), ` ${relative(j.last_attempt)}`);
  line.append(" · ", el("b", null, "exit"), " ");
  line.append(el("span", j.last_rc === 0 ? "ok" : j.last_rc == null ? "" : "bad",
    j.last_rc == null ? "no run recorded" : String(j.last_rc)));
  box.append(line);

  // Verbatim, like the gate's target. Summarising the command is how you end up
  // reading a job that is not the one you are looking at.
  box.append(el("pre", "gate-target", j.command || "(no command in the registry)"));
  if (j.note) box.append(el("p", "p-detail", j.note));
  return box;
}

export function panel(payload, world, api) {
  const { el } = api;
  const wrap = el("div");
  const sec = payload || world?.sections?.clock || { state: "unbuilt" };

  if (sec.state !== "ok") {
    wrap.append(el("p", "empty", BROKEN[sec.state] || `the job source said "${sec.state}"`));
    if (sec.detail) wrap.append(el("pre", "gate-target", sec.detail));
    return wrap;
  }

  const c = sec.counts || {};
  const sum = el("p", "log");
  sum.append(el("b", null, `${sec.checked} scheduled`), " · ");
  for (const [k, label] of [["ok", "on time"], ["stale", "stale"], ["failing", "failing"],
    ["never", "never fired"], ["off", "switched off"]]) {
    if (!c[k]) continue;
    sum.append(el("span", spec(k).alarm ? "bad" : k === "ok" ? "ok" : "", `${c[k]} ${label}`), "  ");
  }
  wrap.append(sum);

  if (sec.unwatched) {
    wrap.append(el("p", "gate-clock",
      `${sec.unwatched} job${sec.unwatched === 1 ? " has" : "s have"} no staleness budget. ` +
      "Nothing is watching them, so they can stop firing without ever going red."));
  }
  for (const bad of sec.registry_bad || []) {
    wrap.append(el("p", "gate-clock", `the registry has a line nobody can read: ${bad}`));
  }
  for (const id2 of sec.unregistered || []) {
    wrap.append(el("p", "gate-clock", `${id2} is running but is not declared in the registry`));
  }

  const jobs = sec.jobs || [];
  const groups = [
    ["needs a look", jobs.filter((j) => spec(j.state).alarm)],
    ["switched off, on purpose", jobs.filter((j) => j.state === "off")],
    ["on time", jobs.filter((j) => j.state === "ok")],
  ];
  for (const [label, rows] of groups) {
    if (!rows.length) continue;
    wrap.append(el("h3", null, `${label} (${rows.length})`));
    for (const j of rows) wrap.append(card(el, j));
  }
  if (!jobs.length) {
    wrap.append(el("p", "empty",
      "jobctl answered, and there is genuinely nothing scheduled on this machine."));
  }
  return wrap;
}

/* ----------------------------------------------------------------- the demo -- */

const iso = (h) => new Date(Date.now() - h * 3600000).toISOString().replace(/\.\d+Z$/, "Z");

/**
 * Every state, on purpose, including the ugly ones. A demo that only shows jobs
 * running on time is a demo that lets the alarm rot untested.
 */
export function demo() {
  const j = (o) => ({
    owner: "aria", note: "", in_registry: true, unwatched: !o.budget_h,
    watch: o.budget_h ? `${o.budget_h}h` : "nothing", ...o,
  });
  const jobs = [
    j({ id: "com.acme.money-swarm", state: "stale", detail: "last success 226h ago, budget 18h",
      schedule: "at 00:07, 06:07, 12:07, 18:07", budget_h: 18,
      command: "/bin/bash /Users/demo/swarm/automation/run-scheduled.sh",
      last_attempt: iso(0.4), last_success: iso(226), last_rc: 2 }),
    j({ id: "com.acme.inbox-fill", state: "stale", detail: "no successful run on record, budget 72h",
      schedule: "at 07:30", budget_h: 72,
      command: "/Users/demo/services/planning-runner.sh",
      last_attempt: iso(11), last_success: null, last_rc: 1 }),
    j({ id: "com.acme.board-brief", state: "never", detail: "no successful run on record, budget 6h",
      schedule: "every 3h", budget_h: 6,
      command: "/bin/bash /Users/demo/buzz/gh-sync.sh --post",
      last_attempt: null, last_success: null, last_rc: null,
      note: "posts a plain-language board brief. Nobody has ever seen one." }),
    j({ id: "com.acme.nightly-index", state: "failing", detail: "rc=127 no such file or directory",
      schedule: "at 03:00", budget_h: 36,
      command: "/Users/demo/services/index.sh --full",
      last_attempt: iso(2), last_success: iso(9), last_rc: 127 }),
    j({ id: "com.acme.buzz-listener", state: "ok", detail: "", schedule: "every 5m",
      budget_h: null, command: "/Users/demo/buzz/listen.py --forever",
      last_attempt: iso(0.1), last_success: iso(0.1), last_rc: 0,
      note: "no staleness budget: this one can die quietly and nothing goes red." }),
    j({ id: "com.acme.augur-tick", state: "off",
      detail: "switched off in launchd's disabled list (not a fault)",
      schedule: "every 5m", budget_h: 2,
      command: "/bin/bash /Users/demo/augur/augur-runner.sh tick",
      last_attempt: iso(19), last_success: iso(19), last_rc: 0 }),
    j({ id: "com.acme.augur-watchdog", state: "off",
      detail: "switched off in launchd's disabled list (not a fault)",
      schedule: "every 1m", budget_h: 2,
      command: "/bin/bash /Users/demo/augur/augur-runner.sh watchdog",
      last_attempt: iso(19), last_success: iso(19), last_rc: 0 }),
  ];
  for (let i = 0; i < 12; i++) {
    jobs.push(j({
      id: `com.acme.chore-${String(i + 1).padStart(2, "0")}`, state: "ok", detail: "",
      schedule: i % 2 ? "every 1h" : "at 06:00, 18:00", budget_h: i % 2 ? 3 : 36,
      command: `/Users/demo/services/chore-${i + 1}.sh`,
      last_attempt: iso(0.5), last_success: iso(0.5), last_rc: 0,
    }));
  }
  const counts = { ok: 0, stale: 0, failing: 0, never: 0, off: 0, unknown: 0 };
  for (const x of jobs) counts[x.state]++;
  return {
    state: "ok",
    checked: jobs.length,
    counts,
    alarm: counts.stale + counts.failing + counts.never,
    unwatched: jobs.filter((x) => x.unwatched).length,
    registry_bad: [],
    unregistered: [],
    jobs,
  };
}
