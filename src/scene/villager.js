import * as THREE from "three";
import { toon, face, tagSprite, bubbleSprite, blobShadow } from "./kit.js";

/**
 * One villager per repo.
 *
 * Their whole job is to be readable at a glance from across the room: what a
 * character is doing with its body has to mean the same thing every time, or the
 * office becomes decoration instead of a dashboard. The mapping is in STATES and
 * nowhere else.
 */

export const STATES = {
  waiting: { mood: "alert",   glyph: "?", color: "#d1495b", screen: "#f2b8c0", pose: "stand", label: "needs you" },
  refused: { mood: "worried", glyph: "!", color: "#e07a3f", screen: "#f3cba6", pose: "stand", label: "refused" },
  parked:  { mood: "sleepy",  glyph: "z", color: "#8d99ae", screen: "#5c6672", pose: "slump", label: "parked" },
  landed:  { mood: "happy",   glyph: "*", color: "#3f9e6a", screen: "#a8e6bd", pose: "cheer", label: "landed a PR" },
  working: { mood: "calm",    glyph: "",  color: "#5b8dd9", screen: "#a9c8f5", pose: "type",  label: "working" },
  idle:    { mood: "calm",    glyph: "",  color: "#b7ad9c", screen: "#3d4650", pose: "sit",   label: "all clear" },
  locked:  { mood: "sleepy",  glyph: "-", color: "#8d99ae", screen: "#3d4650", pose: "gone",  label: "no access" },
};

const BODY = new THREE.CapsuleGeometry(0.26, 0.2, 4, 12);
const HEAD = new THREE.SphereGeometry(0.34, 22, 16);
const EAR = new THREE.SphereGeometry(0.11, 10, 8);
const ARM = new THREE.CapsuleGeometry(0.075, 0.16, 3, 8);

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

    this.head = new THREE.Mesh(HEAD, new THREE.MeshToonMaterial({
      color: new THREE.Color(shade(resident.coat, 1.08)),
      map: face("calm"),
    }));
    // SphereGeometry puts u=0.5 on +X, so the painted face starts out looking
    // sideways. Turn the head once here rather than repainting every texture.
    this.head.rotation.y = -Math.PI / 2;
    this.headPivot.add(this.head);

    for (const s of [-1, 1]) {
      const ear = new THREE.Mesh(EAR, lighter);
      ear.position.set(s * 0.24, 0.24, -0.02);
      ear.scale.set(0.85, 1.3, 0.7);
      this.headPivot.add(ear);
    }

    this.arms = [];
    for (const s of [-1, 1]) {
      const arm = new THREE.Mesh(ARM, coat);
      arm.position.set(s * 0.29, 0.5, 0.06);
      arm.rotation.z = s * 0.35;
      this.root.add(arm);
      this.arms.push(arm);
    }

    // The villager is taller or shorter for life, so a crowd has a silhouette.
    this.root.scale.setScalar(resident.tall ? 1.06 : 0.93);

    this.shadow = blobShadow(0.5);
    this.root.add(this.shadow);

    this.nameTag = tagSprite(resident.name, { scale: 0.95 });
    this.nameTag.position.y = 1.42;
    this.root.add(this.nameTag);

    this.bubble = null;
    this.phase = (resident.seed % 1000) / 1000 * Math.PI * 2;

    // Raycasting against every limb is wasteful and picks up misses on the gaps
    // between them. One invisible capsule is the click target for the whole
    // character, and it is the only thing the picker ever sees.
    this.hit = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.44, 1.0, 3, 8),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    this.hit.position.y = 0.72;
    this.hit.userData.station = station;
    this.root.add(this.hit);

    this.setState(station.state);
  }

  setState(name) {
    const s = STATES[name] || STATES.idle;
    this.stateName = name;
    this.spec = s;
    this.head.material.map = face(s.mood);
    this.head.material.needsUpdate = true;

    if (this.bubble) {
      this.root.remove(this.bubble);
      this.bubble.material.map.dispose();
      this.bubble.material.dispose();
      this.bubble = null;
    }
    if (s.glyph) {
      this.bubble = bubbleSprite(s.glyph, s.color);
      this.bubble.position.set(0.34, 1.72, 0);
      this.root.add(this.bubble);
    }

    const gone = name === "locked";
    this.root.visible = !gone;
    this.baseY = s.pose === "stand" || s.pose === "cheer" ? 0 : -0.18;
  }

  /** t is seconds since load. Everything here is a cheap sine; nothing accumulates. */
  update(t, selected) {
    if (!this.root.visible) return;
    const p = t * 2 + this.phase;
    const pose = this.spec.pose;

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
    } else {
      bob = Math.sin(p * 0.9) * 0.016;
    }

    this.root.position.y = this.baseY + bob;
    this.body.rotation.x = lean * 0.5;
    this.headPivot.position.y = 0.78 - lean * 0.12;
    this.headPivot.rotation.x = lean * 0.35;
    this.arms[0].rotation.x = -armSwing;
    this.arms[1].rotation.x = -armSwing * (pose === "type" ? -1 : 1);

    if (this.bubble) this.bubble.position.y = 1.72 + Math.sin(p * 1.6) * 0.05;

    const wantTag = selected || this.stateName === "waiting";
    this.nameTag.material.opacity = wantTag ? 1 : 0.72;
    this.root.scale.setScalar(
      (this.station.resident.tall ? 1.06 : 0.93) * (selected ? 1.16 : 1)
    );
  }
}

function shade(hex, mul) {
  const c = new THREE.Color(hex);
  c.multiplyScalar(mul);
  return `#${c.getHexString()}`;
}
