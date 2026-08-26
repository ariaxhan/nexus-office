import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import * as BGU from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { toon, tagSprite, blobShadow } from "./kit.js";
import { Villager, STATES } from "./villager.js";
import { resident } from "../names.js";

/**
 * The room, and the rule that decides where everything stands.
 *
 * Desks are laid out in pods, one pod per GitHub owner, because that is the real
 * coordination boundary in the pipeline: an owner is one identity, one token, one
 * set of permissions. Making it a physical wing of the office means the layout
 * cannot drift from the thing it describes.
 */

const PITCH_X = 3.2;
const PITCH_Z = 3.8;
const POD_GAP = 2.6;
const MAX_ROW_WIDTH = 46;

const OWNER_TINT = ["#f6e7d0", "#e3efe1", "#e6e6f4", "#f7e6ea", "#e2eff4", "#f2eede"];

export function layout(stations) {
  const byOwner = new Map();
  for (const s of stations) {
    const owner = s.repo.split("/")[0];
    if (!byOwner.has(owner)) byOwner.set(owner, []);
    byOwner.get(owner).push(s);
  }

  const pods = [...byOwner.entries()]
    .map(([owner, list]) => {
      list.sort((a, b) => a.repo.localeCompare(b.repo));
      // Slightly wider than square, because the camera looks down a room that is
      // wider than it is deep, and a tall narrow pod runs straight off the screen.
      const cols = Math.min(8, Math.max(1, Math.ceil(Math.sqrt(list.length * 1.7))));
      const rows = Math.ceil(list.length / cols);
      return { owner, list, cols, rows, w: cols * PITCH_X, d: rows * PITCH_Z };
    })
    .sort((a, b) => b.list.length - a.list.length);

  // Shelf packing. Not optimal, and it does not need to be: it only has to be
  // stable, so a repo does not teleport across the office between two refreshes.
  const shelves = [];
  let shelf = { pods: [], w: 0, d: 0 };
  for (const pod of pods) {
    if (shelf.pods.length && shelf.w + pod.w + POD_GAP > MAX_ROW_WIDTH) {
      shelves.push(shelf);
      shelf = { pods: [], w: 0, d: 0 };
    }
    shelf.pods.push(pod);
    shelf.w += pod.w + POD_GAP;
    shelf.d = Math.max(shelf.d, pod.d);
  }
  if (shelf.pods.length) shelves.push(shelf);

  const totalW = Math.max(...shelves.map((s) => s.w)) - POD_GAP;
  const totalD = shelves.reduce((a, s) => a + s.d + POD_GAP, 0) - POD_GAP;

  let z = -totalD / 2;
  let tint = 0;
  const placed = [];
  for (const s of shelves) {
    // Each shelf is centred on its own width. Left-aligning them against the
    // widest shelf is what left half the office as empty floor.
    let x = -(s.w - POD_GAP) / 2;
    for (const pod of s.pods) {
      pod.originX = x;
      pod.originZ = z;
      pod.tint = OWNER_TINT[tint++ % OWNER_TINT.length];
      pod.list.forEach((st, i) => {
        st.x = x + (i % pod.cols) * PITCH_X + PITCH_X / 2;
        st.z = z + Math.floor(i / pod.cols) * PITCH_Z + PITCH_Z / 2;
        st.facing = 0;
        st.owner = pod.owner;
        placed.push(st);
      });
      x += pod.w + POD_GAP;
    }
    z += s.d + POD_GAP;
  }
  return { stations: placed, pods: shelves.flatMap((s) => s.pods), width: totalW, depth: totalD };
}

/* ------------------------------------------------------------- furniture -- */

function deskGeometry() {
  const top = new THREE.BoxGeometry(1.7, 0.08, 0.85);
  top.translate(0, 0.74, 0);
  const ped = new THREE.BoxGeometry(0.52, 0.7, 0.72);
  ped.translate(-0.5, 0.35, 0);
  const leg = new THREE.BoxGeometry(0.08, 0.7, 0.08);
  leg.translate(0.75, 0.35, 0);
  return BGU.mergeGeometries([top, ped, leg]);
}

function chairGeometry() {
  const seat = new THREE.BoxGeometry(0.6, 0.09, 0.58);
  seat.translate(0, 0.44, 0);
  const back = new THREE.BoxGeometry(0.6, 0.62, 0.09);
  back.translate(0, 0.75, 0.28);
  const post = new THREE.CylinderGeometry(0.06, 0.09, 0.42, 8);
  post.translate(0, 0.22, 0);
  return BGU.mergeGeometries([seat, back, post]);
}

function monitorGeometry() {
  const panel = new THREE.BoxGeometry(0.62, 0.38, 0.05);
  panel.translate(0, 1.02, -0.2);
  const neck = new THREE.CylinderGeometry(0.04, 0.04, 0.12, 6);
  neck.translate(0, 0.83, -0.2);
  const foot = new THREE.BoxGeometry(0.3, 0.03, 0.18);
  foot.translate(0, 0.79, -0.2);
  const mug = new THREE.CylinderGeometry(0.07, 0.06, 0.13, 10);
  mug.translate(0.58, 0.85, 0.18);
  return BGU.mergeGeometries([panel, neck, foot, mug]);
}

function plantMesh() {
  const g = new THREE.Group();
  const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.26, 0.2, 0.36, 10), toon("#c97b52"));
  pot.position.y = 0.18;
  g.add(pot);
  for (let i = 0; i < 5; i++) {
    const leaf = new THREE.Mesh(new THREE.SphereGeometry(0.22, 8, 6), toon("#5aa86e"));
    const a = (i / 5) * Math.PI * 2;
    leaf.position.set(Math.cos(a) * 0.16, 0.52 + (i % 2) * 0.16, Math.sin(a) * 0.16);
    leaf.scale.set(1, 1.5, 0.6);
    leaf.rotation.y = a;
    g.add(leaf);
  }
  g.add(blobShadow(0.4));
  return g;
}

/* ------------------------------------------------------------------ world -- */

export class Office {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color("#cfe7f2");
    this.scene.fog = new THREE.Fog("#cfe7f2", 60, 130);

    this.camera = new THREE.PerspectiveCamera(34, 1, 0.5, 300);
    this.camera.position.set(0, 26, 34);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.46;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 90;

    const sun = new THREE.DirectionalLight("#fff3dd", 2.1);
    sun.position.set(14, 26, 12);
    this.scene.add(sun);
    this.scene.add(new THREE.HemisphereLight("#eaf4ff", "#d8bfa5", 1.5));

    this.villagers = new Map();
    this.screens = new Map();
    this.pickables = [];
    this.selected = null;
    this.raycaster = new THREE.Raycaster();
    this.clock = new THREE.Clock();
    this.onPick = () => {};

    this.staticRoot = new THREE.Group();
    this.scene.add(this.staticRoot);

    canvas.addEventListener("pointerdown", (e) => this._down(e));
    canvas.addEventListener("pointerup", (e) => this._up(e));
    addEventListener("resize", () => this.resize());
    this.resize();
  }

  resize() {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  /** Tear down and rebuild. The world arrives whole every time, so this is the
   *  only honest way to apply it: a partial patch would leave a desk standing for
   *  a repo that no longer exists, which is exactly the stale-green failure the
   *  whole pipeline is built to avoid. */
  build(stations) {
    for (const v of this.villagers.values()) this.scene.remove(v.root);
    this.villagers.clear();
    this.screens.clear();
    this.pickables.length = 0;
    disposeGroup(this.staticRoot);

    const { stations: placed, pods } = layout(stations);

    // The room is sized from where the desks actually ended up, not from the
    // layout's own arithmetic. The two agreed until they didn't, and the second
    // wing spent a deploy floating off the edge of the floor.
    const xs = placed.map((s) => s.x);
    const zs = placed.map((s) => s.z);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minZ = Math.min(...zs), maxZ = Math.max(...zs);
    this.centre = new THREE.Vector3((minX + maxX) / 2, 0, (minZ + maxZ) / 2 + 0.5);

    const floorW = maxX - minX + 12;
    const floorD = maxZ - minZ + 12;
    // The eight corners the camera has to keep on screen, villager height included.
    this.corners = [];
    for (const x of [minX - 3, maxX + 3])
      for (const y of [0, 2.2])
        for (const z of [minZ - 3, maxZ + 3])
          this.corners.push(new THREE.Vector3(x, y, z));
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(floorW, floorD), toon("#f0dfc4"));
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(this.centre.x, 0, this.centre.z);
    this.staticRoot.add(floor);

    for (const pod of pods) {
      const rug = new THREE.Mesh(
        new THREE.PlaneGeometry(pod.w - 0.4, pod.d - 0.4),
        new THREE.MeshBasicMaterial({ color: pod.tint })
      );
      rug.rotation.x = -Math.PI / 2;
      rug.position.set(pod.originX + pod.w / 2, 0.005, pod.originZ + pod.d / 2);
      this.staticRoot.add(rug);

      const sign = tagSprite(pod.owner, { bg: "#4a3b33", fg: "#fdf6e8", scale: 1.5 });
      sign.position.set(pod.originX + pod.w / 2, 3.4, pod.originZ - 0.4);
      this.staticRoot.add(sign);
    }

    const deskGeo = [];
    const chairGeo = [];
    const monGeo = [];
    const dg = deskGeometry();
    const cg = chairGeometry();
    const mg = monitorGeometry();
    for (const st of placed) {
      const m = new THREE.Matrix4().makeTranslation(st.x, 0, st.z);
      deskGeo.push(dg.clone().applyMatrix4(m));
      monGeo.push(mg.clone().applyMatrix4(m));
      chairGeo.push(cg.clone().applyMatrix4(
        new THREE.Matrix4().makeTranslation(st.x, 0, st.z + 0.95)
      ));
    }
    if (deskGeo.length) {
      this.staticRoot.add(new THREE.Mesh(BGU.mergeGeometries(deskGeo), toon("#d9a273")));
      this.staticRoot.add(new THREE.Mesh(BGU.mergeGeometries(chairGeo), toon("#8fa8bf")));
      this.staticRoot.add(new THREE.Mesh(BGU.mergeGeometries(monGeo), toon("#4a4f57")));
    }

    // Screens stay individual: their colour is the per-repo state readout, and it
    // is the one thing on a desk that has to change without rebuilding the room.
    const screenGeo = new THREE.PlaneGeometry(0.55, 0.31);
    for (const st of placed) {
      const scr = new THREE.Mesh(
        screenGeo,
        new THREE.MeshBasicMaterial({ color: (STATES[st.state] || STATES.idle).screen })
      );
      scr.position.set(st.x, 1.02, st.z - 0.17);
      this.staticRoot.add(scr);
      this.screens.set(st.repo, scr);
    }

    for (const st of placed) {
      st.resident = resident(st.repo);
      const v = new Villager({ ...st, z: st.z + 0.95 });
      v.station = st;
      v.hit.userData.station = st;
      this.scene.add(v.root);
      this.villagers.set(st.repo, v);
      this.pickables.push(v.hit);
    }

    const wallH = 2.6;
    const wallGeo = [];
    const cx = this.centre.x, cz = this.centre.z;
    for (const [w, d, x, z] of [
      [floorW, 0.3, cx, cz - floorD / 2],
      [0.3, floorD, cx - floorW / 2, cz],
      [0.3, floorD, cx + floorW / 2, cz],
    ]) {
      const g = new THREE.BoxGeometry(w, wallH, d);
      g.translate(x, wallH / 2, z);
      wallGeo.push(g);
    }
    this.staticRoot.add(new THREE.Mesh(BGU.mergeGeometries(wallGeo), toon("#f6e3cd")));

    for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const p = plantMesh();
      p.position.set(cx + (floorW / 2 - 1.4) * dx, 0, cz + (floorD / 2 - 1.4) * dz);
      this.staticRoot.add(p);
    }

    this.frameAll();
  }

  update(stations) {
    for (const st of stations) {
      const v = this.villagers.get(st.repo);
      if (!v) return this.build(stations);
      if (v.stateName !== st.state) {
        v.setState(st.state);
        const scr = this.screens.get(st.repo);
        if (scr) scr.material.color.set((STATES[st.state] || STATES.idle).screen);
      }
      Object.assign(v.station, st);
    }
  }

  /**
   * Pull back until the room fits, by actually projecting its corners.
   *
   * Every closed-form version of this was wrong on some window: a tall phone and
   * a 2.6-aspect display are bound by different edges of the frustum, and the
   * first attempt put the near wing three times closer to the lens than the far
   * one. Measuring beats predicting, and twenty iterations of a matrix multiply
   * costs nothing on a camera move.
   */
  frameAll() {
    const c = this.centre || new THREE.Vector3();
    const corners = this.corners || [];
    const tilt = 0.98;
    const dir = new THREE.Vector3(0, Math.sin(tilt), Math.cos(tilt));
    this.controls.target.copy(c).setY(0.8);

    let dist = 40;
    for (let i = 0; i < 20; i++) {
      this.camera.position.copy(this.controls.target).addScaledVector(dir, dist);
      this.camera.lookAt(this.controls.target);
      this.camera.updateMatrixWorld(true);
      let worst = 0;
      for (const p of corners) {
        const q = p.clone().project(this.camera);
        worst = Math.max(worst, Math.abs(q.x), Math.abs(q.y));
      }
      if (!worst) break;
      if (worst > 0.97 || worst < 0.86) dist *= worst / 0.92;
      else break;
    }
    this.controls.update();
  }

  focus(repo) {
    const v = this.villagers.get(repo);
    if (!v) return;
    this.selected = repo;
    const p = v.root.position;
    this.controls.target.set(p.x, 1.0, p.z - 0.6);
    this.camera.position.set(p.x + 2.6, 3.0, p.z + 4.2);
    this.controls.update();
  }

  _down(e) {
    this._downAt = { x: e.clientX, y: e.clientY, t: performance.now() };
  }

  _up(e) {
    const d = this._downAt;
    if (!d) return;
    // Orbiting is a drag and selecting is a click, and telling them apart by
    // distance keeps a slow careful camera move from opening a random panel.
    const moved = Math.hypot(e.clientX - d.x, e.clientY - d.y);
    if (moved > 6 || performance.now() - d.t > 600) return;
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObjects(this.pickables, false)[0];
    this.onPick(hit ? hit.object.userData.station : null);
  }

  start() {
    const tick = () => {
      const t = this.clock.getElapsedTime();
      for (const v of this.villagers.values()) v.update(t, v.station.repo === this.selected);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(tick);
    };
    tick();
  }
}

function disposeGroup(g) {
  for (const child of [...g.children]) {
    g.remove(child);
    child.traverse?.((o) => {
      if (o.geometry) o.geometry.dispose();
    });
  }
}
