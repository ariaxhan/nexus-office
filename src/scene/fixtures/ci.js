/**
 * the build lamps
 *
 * A lamp over every desk saying whether that repo's default branch actually
 * builds. The runner opens PRs into these repos all day, so a red default
 * branch is the most expensive thing in the room not to notice: nothing stops,
 * the PRs keep stacking, and every one of them is built on a broken base.
 *
 * WHY IT HANGS AT 2.7 AND NOT ON THE DESK
 * The desk's pick pad is a 2.4m-tall box centred at y 1.2, and it ENCLOSES
 * everything on the desk top. The in-tray gave up its click target to that box
 * and says so in its own file. This lamp has to be clickable — the issue asks
 * for the failing job and a link to the run — so it hangs above the pad, just
 * over head height, where a ray reaches it before anything else. No change to
 * office.js was needed for that, which is the point of the height.
 *
 * SIX STATES, NOT TWO
 * Passing and failing are the easy pair. `never` is a repo with workflow files
 * whose head commit carries no check at all, which is usually a workflow
 * filtered out of the branch it was meant to run on — a different afternoon
 * from a build that broke. `none` is a repo with no CI, which is a DECISION and
 * is drawn as a quiet grey ring rather than a fault. `running` is a rollup
 * still in flight, and calling it either colour would be a lie that resolves
 * itself in four minutes. `unknown` is "we could not look", which must never
 * render as "it is fine".
 */

import * as THREE from "three";
import * as BGU from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { toon } from "../kit.js";

export const id = "ci";
export const title = "the build lamps";
export const wall = false;

/**
 * One glyph and one colour per state, and one plain-language name.
 *
 * `size` is the whole loudness budget, and the first version of this file spent
 * it wrong: every lamp was a cream bubble with a small coloured glyph, so the
 * picture of the room showed twelve identical white dots and the failing build
 * — the entire reason this fixture exists — was invisible from across the
 * floor. So an alarm lamp is now a SOLID disc in its own colour with a white
 * glyph on it, and a quiet one stays cream. Colour does the shouting; the glyph
 * is only there for whoever cannot separate the two reds from the greens.
 *
 * A passing lamp is small and quiet because seventy shouting green ticks is a
 * room you stop reading. `none` is smaller still and grey: drawn, so a repo with
 * no CI is never confused with a repo this fixture forgot, and never loud, so a
 * deliberate absence of CI is never mistaken for a fault.
 */
const STATE = {
  failing: { glyph: "✕", dot: "#d1495b", name: "failing", size: 1.55, alarm: true },
  unknown: { glyph: "?", dot: "#d9a441", name: "could not look", size: 1.05, alarm: true },
  never: { glyph: "–", dot: "#7b5ea7", name: "never checked", size: 1.0, alarm: true },
  running: { glyph: "●", dot: "#5b8dd9", name: "running now", size: 0.62, alarm: false },
  passing: { glyph: "✓", dot: "#3f9e6a", name: "passing", size: 0.52, alarm: false },
  none: { glyph: "○", dot: "#a9a29b", name: "no CI at all", size: 0.44, alarm: false },
};
const spec = (s) => STATE[s] || STATE.unknown;

const CREAM = "#fffdf5";

/** Why the source could not answer, said out loud rather than drawn as green. */
const BROKEN = {
  "no-desks": "there are no desks, so there is nothing whose build to watch",
  unreadable: "nothing could be read about any repo's build",
  error: "reading the build checks failed",
  unbuilt: "this source has not been built yet",
};

/** Above the desk's 2.4m pick pad, and above a seated villager's head. */
const LAMP_Y = 2.72;

/**
 * The back right corner of the desk top, and not the middle of it.
 *
 * Dead centre was tried and photographed: the pole ran straight down the face
 * of the villager at the desk BEHIND, which is this project's oldest defect
 * shape and the reason the name plaque was moved once already. The desk top is
 * 1.7 x 0.85 centred on the station, the monitor foot sits at z -0.2 and the
 * mug at x +0.58, so this corner is the one piece of wood nothing else wants.
 */
const FLAG_X = 0.72;
const FLAG_Z = -0.34;
const TOP = 0.78;

/* ---------------------------------------------------------------- the build -- */

const S = 192;

/**
 * The disc. An alarm is filled with its own colour and haloed, a quiet state is
 * cream with a coloured rim, and the halo is what makes a red lamp survive being
 * twenty-six pixels wide against a tan floor.
 */
function texture(state) {
  const sp = spec(state);
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  const cx = S / 2;

  if (sp.alarm) {
    const halo = g.createRadialGradient(cx, cx, S * 0.3, cx, cx, S * 0.5);
    halo.addColorStop(0, `${sp.dot}66`);
    halo.addColorStop(1, `${sp.dot}00`);
    g.fillStyle = halo;
    g.fillRect(0, 0, S, S);
  }

  g.fillStyle = sp.alarm ? sp.dot : CREAM;
  g.beginPath();
  g.arc(cx, cx, S * 0.33, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = sp.alarm ? "rgba(255,255,255,0.85)" : sp.dot;
  g.lineWidth = S * (sp.alarm ? 0.03 : 0.05);
  g.stroke();

  g.fillStyle = sp.alarm ? "#fff" : sp.dot;
  g.font = `800 ${Math.round(S * 0.36)}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(sp.glyph, cx, cx + S * 0.02);

  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// One sprite per state, cloned per desk. Sprite.clone() shares the material and
// therefore the canvas texture, so seventy lamps cost six textures.
const protos = new Map();
function lamp(state) {
  if (!protos.has(state)) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({
      map: texture(state), depthTest: false, transparent: true,
    }));
    s.scale.setScalar(0.62 * spec(state).size);
    // Over the walls and the furniture, under the banners that carry words.
    s.renderOrder = 9;
    protos.set(state, s);
  }
  return protos.get(state).clone();
}

export function build(ctx) {
  const sec = ctx.section;
  const stations = ctx.stations || [];
  if (!sec || sec.state === "no-desks" || !stations.length) return null;

  // The source is broken, so every desk gets the amber "we did not look" lamp
  // rather than no lamp. A room that draws nothing when its watcher dies is a
  // room that reports every broken build as green.
  const byRepo = new Map();
  for (const r of sec.repos || []) byRepo.set(r.repo, r);

  // Loudest first. Not for the drawing — a sprite's order does not change what
  // it looks like — but because the shot harness clicks the FIRST pickable a
  // fixture offers, and a picture of a passing desk is not a picture of what
  // this fixture is for.
  const order = ["failing", "unknown", "never", "running", "passing", "none"];
  const byLoudness = [...stations].sort((a, b) =>
    order.indexOf(byRepo.get(a.repo)?.ci ?? "unknown") -
    order.indexOf(byRepo.get(b.repo)?.ci ?? "unknown"));

  const group = new THREE.Group();

  // A pole, for the alarm lamps only.
  //
  // The lamp hangs level with the row behind it, so in the room shot a red
  // cross floats midway between two desks and you have to count to work out
  // which one it belongs to. A pole rising out of the desk answers that
  // instantly. Only faults get one: seventy poles for seventy green ticks is
  // the clutter this fixture is supposed to save you from.
  const poles = new Map();
  const POLE_H = LAMP_Y - 0.2 - TOP;
  const poleGeo = new THREE.CylinderGeometry(0.022, 0.022, POLE_H, 6);

  for (const st of byLoudness) {
    const r = byRepo.get(st.repo) || {
      repo: st.repo, ci: "unknown", failing: [], branch: "",
      detail: BROKEN[sec.state] || `the build source said "${sec.state}"`,
    };
    const s = lamp(r.ci);
    s.position.set(st.x + FLAG_X, LAMP_Y, st.z + FLAG_Z);
    // Every lamp is clickable, not only the red ones. The age of a green check
    // is the thing that tells a fresh pass from a stale one, and a state you
    // cannot click is a state you have to take on trust.
    s.userData.fixture = { id, payload: { ...sec, focus: r.repo } };
    group.add(s);

    const sp = spec(r.ci);
    if (sp.alarm) {
      if (!poles.has(sp.dot)) poles.set(sp.dot, []);
      poles.get(sp.dot).push(poleGeo.clone().applyMatrix4(
        new THREE.Matrix4().makeTranslation(st.x + FLAG_X, TOP + POLE_H / 2, st.z + FLAG_Z)
      ));
    }
  }
  for (const [colour, geos] of poles) {
    group.add(new THREE.Mesh(BGU.mergeGeometries(geos), toon(colour)));
  }
  return group.children.length ? group : null;
}

/* ---------------------------------------------------------------- the panel -- */

const relative = (iso) => {
  if (!iso) return null;
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return null;
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
};

function card(el, r) {
  const sp = spec(r.ci);
  const box = el("div", `issue${sp.alarm ? " hot" : ""}`);

  const top = el("div", "issue-top");
  top.append(el("span", "issue-title", r.repo));
  box.append(top);

  const tags = el("div", "tags");
  const state = el("span", `tag${sp.alarm ? " hot" : ""}`, sp.name);
  if (!sp.alarm) state.style.color = sp.dot;
  tags.append(state);
  if (r.branch) tags.append(el("span", "tag", r.branch));
  box.append(tags);

  if (r.detail) box.append(el("p", "p-detail", r.detail));

  // The age of the check, from the check runs themselves. When none of them
  // reported a time it says so: the commit's date is beside it, labelled as the
  // commit's date, and never quietly promoted into a check time.
  const line = el("p", "log");
  const checked = relative(r.checked_at);
  line.append(el("b", null, "checked"), " ");
  line.append(el("span", checked ? "" : "wait",
    checked || "no check reported a time"));
  const committed = relative(r.commit_at);
  if (committed) line.append(" · ", el("b", null, "head commit"), ` ${committed}`);
  box.append(line);

  for (const j of r.failing || []) {
    const row = el("p", "log");
    row.append(el("span", "bad", j.name || "an unnamed job"));
    if (j.url) {
      row.append(" ");
      const a = el("a", "link", "open the run");
      a.href = j.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      row.append(a);
    }
    box.append(row);
  }
  return box;
}

export function panel(payload, world, api) {
  const { el } = api;
  const wrap = el("div");
  const sec = payload || world?.sections?.ci || { state: "unbuilt" };

  if (sec.state !== "ok" && sec.state !== "cached") {
    wrap.append(el("p", "empty", BROKEN[sec.state] || `the build source said "${sec.state}"`));
    if (sec.detail) wrap.append(el("pre", "gate-target", sec.detail));
    return wrap;
  }

  const age = relative(sec.fetched_at);
  const sum = el("p", "log");
  sum.append(el("b", null, `${sec.checked} repos`), " · ");
  sum.append("asked GitHub ", el("b", null, age || "at an unknown time"));
  wrap.append(sum);

  // A cached board is the honest answer when the refresh failed, and it is only
  // honest while it is labelled. The age above is what makes it readable.
  if (sec.state === "cached") {
    wrap.append(el("p", "gate-clock",
      "this is the last board that came back. The refresh failed, so nothing " +
      "here has been checked since the time above."));
  }
  if (sec.detail) wrap.append(el("pre", "gate-target", sec.detail));

  const c = sec.counts || {};
  const counts = el("p", "log");
  for (const k of ["failing", "unknown", "never", "running", "passing", "none"]) {
    if (!c[k]) continue;
    counts.append(el("span", spec(k).alarm ? "bad" : k === "passing" ? "ok" : "",
      `${c[k]} ${spec(k).name}`), "  ");
  }
  wrap.append(counts);

  const rows = sec.repos || [];
  // The desk you clicked goes first, whatever its state. Clicking one lamp and
  // being handed a list to search is a click that did not answer anything.
  const focused = rows.find((r) => r.repo === sec.focus);
  if (focused) {
    wrap.append(el("h3", null, "the desk you clicked"));
    wrap.append(card(el, focused));
  }

  const groups = [
    ["failing", rows.filter((r) => r.ci === "failing" && r !== focused)],
    ["could not look", rows.filter((r) => r.ci === "unknown" && r !== focused)],
    ["never checked", rows.filter((r) => r.ci === "never" && r !== focused)],
    ["running now", rows.filter((r) => r.ci === "running" && r !== focused)],
    ["passing", rows.filter((r) => r.ci === "passing" && r !== focused)],
    ["no CI at all, on purpose", rows.filter((r) => r.ci === "none" && r !== focused)],
  ];
  for (const [label, list] of groups) {
    if (!list.length) continue;
    wrap.append(el("h3", null, `${label} (${list.length})`));
    for (const r of list) wrap.append(card(el, r));
  }
  if (!rows.length) {
    wrap.append(el("p", "empty", "GitHub answered, and no desk here has a repo to build."));
  }
  return wrap;
}

/* ----------------------------------------------------------------- the demo -- */

const iso = (h) => new Date(Date.now() - h * 3600000).toISOString().replace(/\.\d+Z$/, "Z");

/**
 * Every state, on purpose, against the demo floor's own repos. A demo that only
 * shows green lamps is a demo that lets the red one rot untested, and `none` and
 * `unknown` are the two most likely to be quietly got wrong.
 */
export function demo() {
  const r = (repo, ci, o = {}) => ({
    repo, ci, branch: "main", detail: "", failing: [], run_url: "",
    checked_at: iso(0.6), commit_at: iso(0.7), ...o,
  });
  const repos = [
    r("acme/storefront", "failing", {
      detail: "2 jobs failing on main",
      run_url: "https://github.com/acme/storefront/actions/runs/9001",
      failing: [
        { name: "test (node 22)", url: "https://github.com/acme/storefront/actions/runs/9001" },
        { name: "typecheck", url: "https://github.com/acme/storefront/actions/runs/9001" },
      ],
    }),
    r("northwind/api", "failing", {
      detail: "1 job failing on main", checked_at: iso(31), commit_at: iso(31),
      run_url: "https://github.com/northwind/api/actions/runs/4412",
      failing: [{ name: "integration", url: "https://github.com/northwind/api/actions/runs/4412" }],
    }),
    r("acme/mobile", "never", {
      detail: "there are workflow files, and the head of the default branch has no check on it at all",
      checked_at: null, commit_at: iso(3),
    }),
    r("northwind/warehouse", "running", { detail: "", checked_at: null, commit_at: iso(0.2) }),
    r("acme/website", "passing"),
    r("acme/billing", "passing", { checked_at: iso(90), commit_at: iso(90) }),
    r("northwind/analytics", "passing", { checked_at: iso(5) }),
    r("tiny/experiments", "passing", { checked_at: iso(200), commit_at: iso(200) }),
    r("acme/docs", "none", {
      detail: "no workflow files, so nothing here builds anything",
      checked_at: null, commit_at: iso(48),
    }),
    r("acme/legacy-import", "none", {
      detail: "no workflow files, so nothing here builds anything",
      checked_at: null, commit_at: iso(900),
    }),
    r("tiny/scratch", "none", {
      detail: "no workflow files, so nothing here builds anything",
      checked_at: null, commit_at: iso(1400),
    }),
    r("northwind/internal-tools", "unknown", {
      branch: "",
      detail: "the repository could not be read with the token we hold",
      checked_at: null, commit_at: null,
    }),
  ];
  const counts = { failing: 0, unknown: 0, never: 0, running: 0, passing: 0, none: 0 };
  for (const x of repos) counts[x.ci]++;
  return {
    state: "ok",
    detail: "",
    checked: repos.length,
    counts,
    alarm: counts.failing + counts.never + counts.unknown,
    fetched_at: iso(0.05),
    age_s: 180,
    ttl_s: 600,
    repos,
  };
}
