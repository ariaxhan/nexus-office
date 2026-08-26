import * as THREE from "three";
import { tagSprite, bubbleSprite, toon } from "../kit.js";

/**
 * The out-tray beside you, and the light that says the floor is working.
 *
 * This fixture exists because of one complaint: "I have no way of telling if and
 * when the work is being done. If I hit run I don't see anything except a little
 * popup." The panel half of that answer already shipped in the top bar. This is
 * the physical half, and the whole test of it is whether it reads from across
 * the room with nothing clicked.
 *
 * TWO OBJECTS, and they answer two different questions.
 *
 * ONE. The out-tray, on the floor beside the `you` pad that journey.js draws.
 * One slip per order that is still outstanding. A done order LEAVES the tray, so
 * the pile is what is still owed you rather than a growing museum of history. A
 * failed order does not leave and does not sit quietly in the stack either: it
 * spills onto the floor in red, in front of the tray, because a failure you
 * cannot see is the exact defect this whole feature is fixing.
 *
 * TWO. The working light, a beacon on a post at the door. Three states, and the
 * only rule that matters is that they never look alike:
 *
 *   running   green lens, a bright pool of light on the floor, and what it is doing
 *   idle      grey lens, dark floor, and when it next looks
 *   unknown   violet lens, a pulsing pool, and a "?" over the lamp
 *
 * Unknown is a real state, not an error and never a synonym for idle. It is what
 * you get when world.sections.pipeline is missing, unbuilt, unconfigured or
 * broken, and it is drawn LOUDER than idle rather than quieter, because "nobody
 * can say whether your work is running" is worse news than "nothing is running".
 * That difference in loudness is deliberate: the failure this repo exists to
 * kill is a silent unknown wearing the costume of a calm green room.
 */

export const id = "orders";
export const title = "the out-tray";
export const wall = false;

/* ----------------------------------------------------------- the pure part -- */

/** Enough waiting slips to read the size of the queue, few enough to stay a tray. */
export const MAX_PENDING_SLIPS = 8;

/**
 * What the pipeline section is allowed to CLAIM about the light.
 *
 * Anything other than a section that says `ok` is unknown. That includes the
 * section being absent entirely, which is the state the demo floor is in and the
 * state a fresh office is in before the source is built. Absent is not idle.
 */
export function lightFrom(pipe) {
  if (!pipe || typeof pipe !== "object") {
    return { state: "unknown", why: "This office has never reported a pipeline state." };
  }
  if (pipe.state === "unbuilt") {
    return { state: "unknown", why: "The pipeline source is not built yet, so nothing is watching the runner." };
  }
  if (pipe.state === "unconfigured") {
    return { state: "unknown", why: "No pipeline is configured for this office." };
  }
  // `off` landed in client/sources/pipeline.py while this was being built: the
  // kill switch is on, or launchd has the job disabled. That is not unknown in
  // the "nobody can say" sense, so it keeps the loud look and never the idle one
  // but says the true thing instead of shrugging.
  if (pipe.state === "off") {
    return {
      state: "unknown",
      stopped: true,
      why: `The pipeline will not run: ${pipe.detail || "it is switched off"}.`,
    };
  }
  if (pipe.state !== "ok") {
    return { state: "unknown", why: `Cannot tell what the pipeline is doing: ${pipe.detail || pipe.state}.` };
  }
  if (pipe.running) {
    return {
      state: "running",
      doing: pipe.doing || "",
      running_for: pipe.running_for || "",
      why: "A run is in flight right now.",
    };
  }
  return {
    state: "idle",
    next_in: pipe.next_in || "",
    why: pipe.next_in ? `Nothing running. The pipeline next looks in ${pipe.next_in}.` : "Nothing running.",
  };
}

/**
 * Decisions plus pipeline state, turned into exactly what gets drawn.
 *
 * Pure and exported because the geometry is not testable and this is where the
 * honesty lives. Two invariants have tests of their own:
 *
 *   - a failed decision is NEVER dropped, at any queue size. The cap trims
 *     waiting slips only, because a tray too full to show one more pending
 *     order still has to show the one that broke.
 *   - unknown never produces the same output as idle.
 */
export function plan(decisions, pipe) {
  const all = Array.isArray(decisions) ? decisions : [];
  const slip = (d) => ({
    id: d.id,
    kind: d.kind || "order",
    repo: d.repo || "",
    issue: d.issue || null,
    at: d.at || "",
    status: d.status,
    result: d.result || "",
  });

  // Every failure, always, in full. Then as many waiting slips as the tray holds.
  const failed = all.filter((d) => d.status === "failed").map(slip);
  const waiting = all.filter((d) => d.status === "pending").map(slip);
  const gone = all.filter((d) => d.status !== "failed" && d.status !== "pending").length;

  return {
    waiting: waiting.slice(0, MAX_PENDING_SLIPS),
    waitingTotal: waiting.length,
    failed,
    gone,
    light: lightFrom(pipe),
  };
}

/* ------------------------------------------------------------------- look -- */

const LOOK = {
  running: { lens: "#43c46e", pool: "#7ef0a8", bg: "#1f7a44", fg: "#eafaf0", glow: true },
  idle: { lens: "#97a4b4", pool: null, bg: "#fffdf5", fg: "#4a3b33", glow: false },
  unknown: { lens: "#b25fd1", pool: "#d79bef", bg: "#54246e", fg: "#f8ecff", glow: true },
};

const CUT = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function lightLabel(light) {
  if (light.state === "running") {
    const what = light.doing ? ` · ${CUT(light.doing, 34)}` : "";
    const forr = light.running_for ? ` · ${light.running_for}` : "";
    return `WORKING NOW${what}${forr}`;
  }
  if (light.state === "idle") {
    return light.next_in ? `idle · next look in ${light.next_in}` : "idle · nothing running";
  }
  if (light.stopped) return "STOPPED · the pipeline will not run at all";
  return "UNKNOWN · nobody can say if work is running";
}

/* ------------------------------------------------------------------ build -- */

const SLIP = new THREE.BoxGeometry(1.9, 0.05, 1.15);

function tray(x, z, waiting, waitingTotal) {
  const g = new THREE.Group();
  const wood = toon("#b98a5a");

  const base = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.1, 1.6), wood);
  base.position.set(0, 0.05, 0);
  base.userData.fixture = { id, payload: { kind: "tray" } };
  g.add(base);

  const rail = (w, h, d, rx, ry, rz) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), wood);
    m.position.set(rx, ry, rz);
    m.userData.fixture = { id, payload: { kind: "tray" } };
    g.add(m);
  };
  rail(2.4, 0.34, 0.12, 0, 0.27, -0.74);
  rail(0.12, 0.34, 1.6, -1.14, 0.27, 0);
  rail(0.12, 0.34, 1.6, 1.14, 0.27, 0);
  rail(2.4, 0.14, 0.12, 0, 0.17, 0.74);

  waiting.forEach((s, i) => {
    const m = new THREE.Mesh(SLIP, new THREE.MeshBasicMaterial({ color: "#f6e6bf" }));
    m.position.set(0, 0.14 + i * 0.075, 0);
    m.rotation.y = ((i % 3) - 1) * 0.045;
    m.userData.fixture = { id, payload: { kind: "tray" } };
    g.add(m);
  });

  const head = tagSprite(
    waitingTotal ? `out-tray · ${waitingTotal} waiting` : "out-tray · empty",
    { bg: "#4a3b33", fg: "#fdf6e8", scale: 1.05 }
  );
  head.position.set(0, 1.35, 0);
  g.add(head);

  g.position.set(x, 0, z);
  return g;
}

/**
 * Failures do not go in the stack. They lie on the floor in front of the tray in
 * red, one per failure, with a count over them. An order that failed used to
 * look exactly like one that worked; on the floor it looks like a mess, which is
 * what it is.
 */
function spill(x, z, failed) {
  const g = new THREE.Group();
  const red = new THREE.MeshBasicMaterial({ color: "#d1495b" });

  failed.forEach((s, i) => {
    const m = new THREE.Mesh(SLIP, red);
    m.position.set((i % 3) * 0.75 - 0.6, 0.03 + i * 0.012, Math.floor(i / 3) * 0.7);
    m.rotation.set(0, 0.5 + i * 0.7, 0);
    m.userData.fixture = { id, payload: { kind: "failed", slip: s } };
    g.add(m);
  });

  const tag = tagSprite(
    failed.length === 1 ? "1 FAILED" : `${failed.length} FAILED`,
    { bg: "#d1495b", fg: "#fff6f7", scale: 1.15 }
  );
  tag.position.set(0, 1.0, 0.2);
  g.add(tag);

  g.position.set(x, 0, z);
  return g;
}

/**
 * The beacon. A post you could not mistake for furniture, a lens you can read
 * the colour of from the far wall, and a pool of light on the floor that is only
 * there when there is something to say.
 */
function beacon(x, z, light) {
  const look = LOOK[light.state];
  const g = new THREE.Group();

  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, 2.8, 10), toon("#6b5a4c"));
  post.position.set(0, 1.4, 0);
  g.add(post);

  // The lens sits ON TOP of the collar, not under it. Under it was the first
  // build, and the room camera looks down at about fifty degrees, so the collar
  // covered the one part of the lamp whose colour carries the answer.
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.28, 0.16, 14), toon("#4a3b33"));
  cap.position.set(0, 3.0, 0);
  g.add(cap);

  // Unlit on purpose: a lamp that falls into shadow is a lamp that lies.
  const lens = new THREE.Mesh(
    new THREE.SphereGeometry(0.5, 18, 14),
    new THREE.MeshBasicMaterial({ color: look.lens })
  );
  lens.position.set(0, 3.5, 0);
  lens.userData.fixture = { id, payload: { kind: "light", light } };
  g.add(lens);

  // The click target has to be worth aiming at from across the room, and a
  // 0.44 sphere is not. An invisible sleeve around the post catches the rest.
  const grab = new THREE.Mesh(
    new THREE.CylinderGeometry(0.55, 0.55, 3.4, 8),
    new THREE.MeshBasicMaterial({ visible: false })
  );
  grab.position.set(0, 1.7, 0);
  grab.userData.fixture = { id, payload: { kind: "light", light } };
  g.add(grab);

  if (look.pool) {
    const pool = new THREE.Mesh(
      new THREE.CircleGeometry(1.5, 26),
      new THREE.MeshBasicMaterial({ color: look.pool, transparent: true, opacity: 0.55 })
    );
    pool.rotation.x = -Math.PI / 2;
    pool.position.set(0, 0.015, 0);
    pool.frustumCulled = false;
    const t0 = performance.now();
    /**
     * The room's render loop ticks villagers and nothing else, so a fixture that
     * wants to move moves itself. onBeforeRender fires from the renderer, which
     * means it still runs when the shot harness freezes the room and renders one
     * frame by hand, and updateMatrixWorld here is load bearing: the renderer
     * builds modelViewMatrix after this hook, and picking reads matrixWorld.
     *
     * The pulse never reaches zero. A beacon whose whole message can be missing
     * at the exact instant you look at it is a beacon that cannot be trusted, and
     * a frozen frame is exactly that instant.
     */
    pool.onBeforeRender = () => {
      const t = (performance.now() - t0) / 1000;
      const u = 0.5 + 0.5 * Math.sin(t * 2.1);
      pool.material.opacity = 0.4 + u * 0.32;
      pool.scale.setScalar(0.88 + u * 0.18);
      pool.updateMatrixWorld(true);
    };
    pool.onBeforeRender();
    g.add(pool);
  }

  // Shape, not only colour. Unknown carries a mark idle cannot accidentally wear.
  // It sits ON the lens rather than above it: the first version floated it over
  // the label and the bubble covered half the sentence it was there to reinforce.
  if (light.state === "unknown") {
    const q = bubbleSprite("?", "#b25fd1");
    q.scale.set(1.0, 1.0, 1);
    q.position.set(0, 3.55, 0);
    g.add(q);
  }

  // High enough to clear the "?" that sits on the lens. They were 3.7 and 4.0
  // the other way round at first and the mark covered the sentence it exists to
  // reinforce, which the room shot showed and the source did not.
  const tag = tagSprite(lightLabel(light), { bg: look.bg, fg: look.fg, scale: 1.25 });
  tag.position.set(0, 4.5, 0);
  g.add(tag);

  g.position.set(x, 0, z);
  return g;
}

export function build(ctx) {
  const { world, room } = ctx;
  const p = plan(world?.decisions, world?.sections?.pipeline);

  // The near edge of the floor is the door and it is also where you stand, which
  // is how journey.js places its `you` pad. Take the same edge from ctx.room
  // rather than restating its arithmetic, and sit inside the box the camera
  // promises to keep on screen (the desks plus three) so this is never framed out.
  const z = room.maxZ + 2.7;
  const clamp = (x) => Math.min(Math.max(x, room.minX - 2), room.maxX + 2);

  const g = new THREE.Group();
  // The light first: it is the single most valuable object here, so it is never
  // conditional on there being orders and never returns null.
  g.add(beacon(clamp(room.centre.x - 4.6), z, p.light));
  g.add(tray(clamp(room.centre.x + 3.2), z, p.waiting, p.waitingTotal));
  // Beside the tray, not in front of it: journey.js runs its throughput lane along
  // the floor a little further out, and spilled paper lying across it read as a
  // segment of someone else's chart.
  if (p.failed.length) g.add(spill(clamp(room.centre.x + 6.0), z + 0.5, p.failed));
  return g;
}

/* ------------------------------------------------------------------ panel -- */

const WHEN = (iso) => {
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(m)) return "at an unknown time";
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (m < 60 * 36) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
};

const READS = {
  running: "green lens, lit floor: a run is in flight.",
  idle: "grey lens, dark floor: nothing is running, and the next look is known.",
  unknown: "violet lens, a question mark, a pulsing floor: this is not a quiet floor.",
};

/**
 * NOT a second copy of the top bar.
 *
 * `showOrders` already lists every decision with its verbatim result, and
 * duplicating that here would mean two places to keep honest. What the top bar
 * cannot give you is the thing you actually clicked: what this object in the room
 * means, why the light is the colour it is, and what to do about it. So this
 * panel explains the object and then points at the list.
 */
export function panel(payload, world, api) {
  const { el } = api;
  const box = el("div", "fx-orders");
  const p = plan(world?.decisions, world?.sections?.pipeline);
  const light = p.light;

  if (payload?.kind === "failed" && payload.slip) {
    const s = payload.slip;
    box.append(el("h3", null, `failed · ${s.kind}${s.repo ? ` · ${s.repo}` : ""}`));
    box.append(el("p", null, `You asked for this ${WHEN(s.at)} and it did not happen.`));
    if (s.result) {
      const pre = el("pre", "gate-target");
      pre.textContent = s.result;
      box.append(pre);
    }
    box.append(el("p", "empty",
      "It stays on the floor until it is dealt with. Nothing in this room quietly clears a failure."));
    return box;
  }

  box.append(el("h3", null, "the working light"));
  const line = el("p", light.state === "unknown" ? "log stale" : "log");
  line.append(el("b", null, light.state === "running" ? "working now"
    : light.state === "idle" ? "idle"
      : light.stopped ? "stopped" : "unknown"), `: ${light.why}`);
  box.append(line);
  if (light.state === "running" && light.doing) {
    const pre = el("pre", "gate-target");
    pre.textContent = light.doing;
    box.append(pre);
  }
  box.append(el("p", "empty", READS[light.state]));
  if (light.state === "unknown") {
    box.append(el("p", "empty", light.stopped
      ? "A stopped pipeline is drawn as loudly as an unknown one, and never as an idle one. " +
        "Nothing will run until it is switched back on."
      : "Unknown is never drawn as idle. The office is not claiming the floor is quiet; " +
        "it is saying it has no idea, which is the worse of the two and is drawn louder."));
  }

  box.append(el("h3", null, "the out-tray"));
  box.append(el("p", null, p.waitingTotal
    ? `${p.waitingTotal} order${p.waitingTotal === 1 ? "" : "s"} still waiting to be picked up.`
    : "Nothing is waiting to be picked up."));
  if (p.failed.length) {
    box.append(el("p", "log stale",
      `${p.failed.length} failed and ${p.failed.length === 1 ? "is" : "are"} on the floor in red. ` +
      "Click one to see the reason it gave."));
  }
  if (p.gone) {
    box.append(el("p", "empty",
      `${p.gone} handled order${p.gone === 1 ? " has" : "s have"} left the tray. ` +
      "The tray shows what is outstanding, not a pile of history."));
  }
  box.append(el("p", "empty",
    "“What you asked for” in the top bar has the full list, each with the verbatim result."));
  return box;
}

/**
 * Null on purpose: this fixture reads world.decisions and world.sections.pipeline,
 * and neither is its own section. There is no shape for it to supply, and
 * inventing one would only shadow the real thing.
 */
export function demo() {
  return null;
}
