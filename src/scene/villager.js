import * as THREE from "three";
import { toon, facePlate, face, tagSprite, bubbleSprite, blobShadow } from "./kit.js";

/**
 * One villager per repo.
 *
 * Their whole job is to be readable at a glance from across the room: what a
 * character is doing with its body has to mean the same thing every time, or the
 * office becomes decoration instead of a dashboard. The mapping is in STATES and
 * nowhere else.
 */

export const STATES = {
  // A gate is its own state and outranks everything, including "needs you". Both
  // mean a human must act, but a gate has an agent SITTING THERE BLOCKED with a
  // clock running against it, and it must never be mistaken for an issue that can
  // wait until morning. Different pose, different glyph, different colour.
  gated:   { mood: "alert",   glyph: "!", color: "#c07c2c", screen: "#f7d9a8", pose: "hand",  label: "asking permission" },
  waiting: { mood: "alert",   glyph: "?", color: "#d1495b", screen: "#f2b8c0", pose: "stand", label: "needs you" },
  refused: { mood: "worried", glyph: "!", color: "#e07a3f", screen: "#f3cba6", pose: "stand", label: "refused" },
  parked:  { mood: "sleepy",  glyph: "z", color: "#8d99ae", screen: "#5c6672", pose: "slump", label: "parked" },
  landed:  { mood: "happy",   glyph: "*", color: "#3f9e6a", screen: "#a8e6bd", pose: "cheer", label: "landed a PR" },
  working: { mood: "calm",    glyph: "",  color: "#5b8dd9", screen: "#a9c8f5", pose: "type",  label: "working" },
  idle:    { mood: "calm",    glyph: "",  color: "#b7ad9c", screen: "#3d4650", pose: "sit",   label: "all clear" },
  locked:  { mood: "sleepy",  glyph: "-", color: "#8d99ae", screen: "#3d4650", pose: "gone",  label: "no access" },
};

// Seconds. A villager walks for STROLL_WALK of every STROLL_CYCLE, so at any
// moment roughly a third of the idle desks are empty, which is what an office
// looks like.
const STROLL_CYCLE = 34;
const STROLL_WALK = 11;

const BODY = new THREE.CapsuleGeometry(0.26, 0.2, 4, 12);
const HEAD = new THREE.SphereGeometry(0.34, 22, 16);
const EAR = new THREE.SphereGeometry(0.11, 10, 8);
// The arm hangs from its TOP, not its middle. A capsule centred on its own
// origin spins in place when you rotate it, which is why the raised hand read as
// a shrug: the geometry is shifted down so rotation pivots at the shoulder.
const ARM = new THREE.CapsuleGeometry(0.075, 0.16, 3, 8);
ARM.translate(0, -0.155, 0);

export class Villager {
  constructor(station) {
    this.station = station;
    const { resident } = station;

    this.root = new THREE.Group();
    this.root.position.set(station.x, 0, station.z);
    this.root.rotation.y = station.facing;

    const coat = toon(resident.coat);
    const lighter = toon(shade(resident.coat, 1.14));

    this.body = new THREE.Mesh(BODY, coat);
    this.body.position.y = 0.42;
    this.root.add(this.body);

    this.headPivot = new THREE.Group();
    this.headPivot.position.y = 0.78;
    this.root.add(this.headPivot);

    this.head = new THREE.Mesh(HEAD, toon(shade(resident.coat, 1.08)));
    this.headPivot.add(this.head);

    this.face = facePlate("calm");
    // Every number here is load bearing. The head is a sphere of radius 0.34, so
    // a plate any closer than that is INSIDE it and the sphere wins the depth
    // test: the villagers came out faceless. Tilt trades the same way, because
    // tipping the plate back swings its lower edge into the skull and eats the
    // mouth. 0.36 out and 0.3 back is the pair that clears at both ends.
    this.face.position.set(0, 0.03, 0.36);
    this.face.rotation.x = -0.3;
    this.headPivot.add(this.face);

    for (const s of [-1, 1]) {
      const ear = new THREE.Mesh(EAR, lighter);
      ear.position.set(s * 0.24, 0.24, -0.02);
      ear.scale.set(0.85, 1.3, 0.7);
      this.headPivot.add(ear);
    }

    this.arms = [];
    for (const side of [-1, 1]) {
      const arm = new THREE.Mesh(ARM, coat);
      arm.position.set(side * 0.28, 0.62, 0.02);
      arm.rotation.z = side * 0.3;
      arm.userData.rest = side * 0.3;
      this.root.add(arm);
      this.arms.push(arm);
    }

    // The villager is taller or shorter for life, so a crowd has a silhouette.
    this.root.scale.setScalar(resident.tall ? 1.06 : 0.93);

    this.shadow = blobShadow(0.5);
    this.root.add(this.shadow);

    // The PLAQUE CARRIES THE REPO, not the villager's name. A character you
    // recognise is easier to track across visits than a string, so the villager
    // keeps its name in the panel; but the thing you are scanning the room for is
    // always a repo, and "Pumpkin" does not help you find storefront.
    //
    // Short name only. The owner is already the wing you are standing in, so
    // repeating it on seventy plaques is noise.
    this.nameTag = tagSprite(shortName(station.repo), { scale: 0.62 });
    // Down at knee height and well forward, like a plaque on the floor. Overhead
    // it lands on its own head from above; at chest height it lands on the face.
    // There is no altitude that works, so the tag leaves the head alone entirely.
    this.nameTag.position.set(0, 0.2, 1.15);
    this.root.add(this.nameTag);

    this.bubble = null;
    this.phase = (resident.seed % 1000) / 1000 * Math.PI * 2;
    // Where this villager belongs. A stroll is an offset from here and always
    // returns, so nobody drifts across the floor over an afternoon.
    this.home = this.root.position.clone();
    // Each villager strolls on its own clock. Without the offset the whole floor
    // stands up at once, which reads as a fire drill rather than an office.
    this.strollOffset = (resident.seed % STROLL_CYCLE);

    this.setState(station.state);
  }

  setState(name) {
    const s = STATES[name] || STATES.idle;
    this.stateName = name;
    this.spec = s;
    this.face.material.map = face(s.mood);
    this.face.material.needsUpdate = true;

    if (this.bubble) {
      this.root.remove(this.bubble);
      this.bubble.material.map.dispose();
      this.bubble.material.dispose();
      this.bubble = null;
    }
    if (s.glyph) {
      this.bubble = bubbleSprite(s.glyph, s.color);
      this.bubble.position.set(0.36, 1.98, 0);
      this.root.add(this.bubble);
    }

    const gone = name === "locked";
    this.root.visible = !gone;
    this.baseY = s.pose === "stand" || s.pose === "cheer" ? 0 : -0.18;
  }

  /**
   * Can this one leave its desk? Only if nothing is waiting on it.
   *
   * A villager with its hand up or a question over its head must stay put and
   * stay findable. Movement is for the ones with nothing to report.
   */
  get canStroll() {
    return this.stateName === "idle" || this.stateName === "landed";
  }

  /** t is seconds since load. Everything here is a cheap sine; nothing accumulates. */
  update(t, selected) {
    if (!this.root.visible) return;
    const p = t * 2 + this.phase;
    let pose = this.spec.pose;

    // ── strolling ────────────────────────────────────────────────────────────
    // A lap around the desk on a fixed cycle: out, around, back to the chair.
    // Deliberately not pathfinding. Seventy characters have to hold 60fps, and a
    // sine loop that never leaves its own square metre cannot collide with
    // anything or get lost.
    let sx = 0;
    let sz = 0;
    let strolling = false;
    if (this.canStroll && !selected) {
      const local = (t + this.strollOffset) % STROLL_CYCLE;
      if (local < STROLL_WALK) {
        const u = local / STROLL_WALK;
        const ease = Math.sin(u * Math.PI);
        sx = Math.sin(u * Math.PI * 2) * 1.15;
        sz = ease * 1.25;
        strolling = ease > 0.06;
      }
    }

    if (strolling) {
      pose = "walk";
      // Face where you are going. Differentiating the sine gives the heading
      // without storing a previous position and drifting out of sync with it.
      const u = ((t + this.strollOffset) % STROLL_CYCLE) / STROLL_WALK;
      const dx = Math.cos(u * Math.PI * 2) * Math.PI * 2 * 1.15;
      const dz = Math.cos(u * Math.PI) * Math.PI * 1.25;
      this.root.rotation.y = Math.atan2(dx, dz);
    } else {
      this.root.rotation.y = this.station.facing || 0;
    }
    this.root.position.x = this.home.x + sx;
    this.root.position.z = this.home.z + sz;

    let bob = 0;
    let armSwing = 0;
    let lean = 0;

    if (pose === "type") {
      bob = Math.sin(p * 1.5) * 0.012;
      armSwing = Math.sin(p * 7) * 0.22;
      lean = 0.16;
    } else if (pose === "stand") {
      bob = Math.sin(p * 1.2) * 0.03;
      armSwing = Math.sin(p * 2.2) * 0.5 + 0.5;
    } else if (pose === "cheer") {
      bob = Math.abs(Math.sin(p * 2.4)) * 0.12;
      armSwing = -1.1;
    } else if (pose === "slump") {
      bob = Math.sin(p * 0.45) * 0.008;
      lean = 0.5;
    } else if (pose === "hand") {
      // One arm straight up and held there. Waving reads as a greeting; a raised
      // hand reads as a question, and from across the room that difference is the
      // entire feature.
      bob = Math.sin(p * 1.1) * 0.02;
      armSwing = -2.15;
    } else if (pose === "walk") {
      bob = Math.abs(Math.sin(p * 3.4)) * 0.06;
      armSwing = Math.sin(p * 3.4) * 0.7;
      lean = 0.1;
    } else {
      bob = Math.sin(p * 0.9) * 0.016;
    }

    // Standing up to walk means standing up: the sitting offset is dropped.
    this.root.position.y = (strolling ? 0 : this.baseY) + bob;
    this.body.rotation.x = lean * 0.5;
    this.headPivot.position.y = 0.78 - lean * 0.12;
    this.headPivot.rotation.x = lean * 0.35;
    // A raised hand is ONE arm swung out sideways past vertical. Both arms up is
    // a cheer, which is the opposite message, so the second arm stays down.
    if (pose === "hand") {
      this.arms[0].rotation.x = 0;
      this.arms[0].rotation.z = -2.5 + Math.sin(p * 1.4) * 0.12;
      this.arms[1].rotation.x = 0;
      this.arms[1].rotation.z = this.arms[1].userData.rest;
    } else {
      for (const [i, arm] of this.arms.entries()) {
        arm.rotation.z = arm.userData.rest + (pose === "cheer" ? -arm.userData.rest * 4 : 0);
        arm.rotation.x = -armSwing * (pose === "type" && i === 1 ? -1 : 1);
      }
    }

    if (this.bubble) this.bubble.position.y = 1.98 + Math.sin(p * 1.6) * 0.05;

    const wantTag = selected || this.stateName === "waiting";
    this.nameTag.material.opacity = wantTag ? 1 : 0.72;
    this.root.scale.setScalar(
      (this.station.resident.tall ? 1.06 : 0.93) * (selected ? 1.16 : 1)
    );
  }
}

/** `owner/name` -> `name`. A bare repo with no owner is left alone. */
function shortName(repo) {
  const parts = String(repo || "").split("/");
  return parts[parts.length - 1] || repo;
}

function shade(hex, mul) {
  const c = new THREE.Color(hex);
  c.multiplyScalar(mul);
  return `#${c.getHexString()}`;
}
