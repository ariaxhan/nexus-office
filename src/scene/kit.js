import * as THREE from "three";

/**
 * Shared art supplies. Everything in here is built once and handed out, because
 * seventy villagers sharing five face textures is the difference between a room
 * that runs at sixty frames and a room that stutters.
 */

/** Three flat bands of light. This is the whole cel-shaded look, in six pixels. */
export function toonGradient() {
  const data = new Uint8Array([120, 120, 120, 255, 200, 200, 200, 255, 255, 255, 255, 255]);
  const t = new THREE.DataTexture(data, 3, 1, THREE.RGBAFormat);
  t.minFilter = THREE.NearestFilter;
  t.magFilter = THREE.NearestFilter;
  t.needsUpdate = true;
  return t;
}

const GRADIENT = toonGradient();

const matCache = new Map();

export function toon(color) {
  const key = String(color);
  if (!matCache.has(key)) {
    matCache.set(key, new THREE.MeshToonMaterial({ color, gradientMap: GRADIENT }));
  }
  return matCache.get(key);
}

/* ------------------------------------------------------------------ faces -- */

const FACE_SIZE = 256;

/**
 * Faces are drawn, not modelled. A sphere with a painted face is one draw call
 * and reads far better at desk-scale than a pile of tiny eye geometry would, and
 * changing a mood becomes swapping a texture rather than rebuilding a head.
 */
function drawFace(mood) {
  const c = document.createElement("canvas");
  c.width = c.height = FACE_SIZE;
  const g = c.getContext("2d");
  g.clearRect(0, 0, FACE_SIZE, FACE_SIZE);

  // Drawn to fill a small plane that floats just off the front of the head, so
  // one texture serves every coat colour and nothing multiplies into the skin.
  const cx = FACE_SIZE * 0.5;
  const cy = FACE_SIZE * 0.44;
  const eyeDX = FACE_SIZE * 0.155;
  const eyeR = FACE_SIZE * 0.062;

  g.fillStyle = "#ffb3ba";
  g.globalAlpha = 0.75;
  for (const s of [-1, 1]) {
    g.beginPath();
    g.ellipse(cx + s * eyeDX * 1.95, cy + eyeR * 1.7, eyeR * 1.25, eyeR * 0.85, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.globalAlpha = 1;

  g.fillStyle = "#3a2f2b";
  g.strokeStyle = "#3a2f2b";
  g.lineWidth = FACE_SIZE * 0.02;
  g.lineCap = "round";

  const eye = (s, kind) => {
    const x = cx + s * eyeDX;
    if (kind === "closed") {
      g.lineWidth = FACE_SIZE * 0.026;
      g.beginPath();
      g.arc(x, cy, eyeR * 1.15, Math.PI * 0.12, Math.PI * 0.88);
      g.stroke();
      g.lineWidth = FACE_SIZE * 0.02;
      return;
    }
    if (kind === "wide") {
      g.beginPath();
      g.ellipse(x, cy, eyeR * 1.15, eyeR * 1.45, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#fff";
      g.beginPath();
      g.arc(x + eyeR * 0.35, cy - eyeR * 0.5, eyeR * 0.4, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#3a2f2b";
      return;
    }
    g.beginPath();
    g.ellipse(x, cy, eyeR * 0.95, eyeR * 1.2, 0, 0, Math.PI * 2);
    g.fill();
  };

  const mouth = (kind) => {
    const my = cy + FACE_SIZE * 0.2;
    g.beginPath();
    if (kind === "smile") g.arc(cx, my - eyeR, eyeR * 1.5, Math.PI * 0.15, Math.PI * 0.85);
    else if (kind === "flat") g.moveTo(cx - eyeR, my), g.lineTo(cx + eyeR, my);
    else if (kind === "o") g.ellipse(cx, my, eyeR * 0.6, eyeR * 0.8, 0, 0, Math.PI * 2);
    else if (kind === "wave") {
      g.moveTo(cx - eyeR * 1.4, my);
      g.quadraticCurveTo(cx - eyeR * 0.5, my - eyeR * 0.8, cx, my);
      g.quadraticCurveTo(cx + eyeR * 0.5, my + eyeR * 0.8, cx + eyeR * 1.4, my);
    }
    kind === "o" ? g.fill() : g.stroke();
  };

  if (mood === "sleepy") { eye(-1, "closed"); eye(1, "closed"); mouth("flat"); }
  else if (mood === "alert") { eye(-1, "wide"); eye(1, "wide"); mouth("o"); }
  else if (mood === "worried") { eye(-1, "dot"); eye(1, "dot"); mouth("wave"); }
  else if (mood === "happy") { eye(-1, "closed"); eye(1, "closed"); mouth("smile"); }
  else { eye(-1, "dot"); eye(1, "dot"); mouth("smile"); }

  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const FACES = {};
export function face(mood) {
  if (!FACES[mood]) FACES[mood] = drawFace(mood);
  return FACES[mood];
}

const FACE_GEO = new THREE.PlaneGeometry(0.46, 0.46);

/** A flat decal hovering just off the head. Unlit, so a face never falls into shadow. */
export function facePlate(mood) {
  const m = new THREE.Mesh(FACE_GEO, new THREE.MeshBasicMaterial({
    map: face(mood), transparent: true, depthWrite: false,
  }));
  m.renderOrder = 2;
  return m;
}

/* ---------------------------------------------------------------- sprites -- */

/** A rounded speech tag, drawn to canvas and hung in the air as a sprite. */
export function tagSprite(text, { bg = "#fffdf5", fg = "#4a3b33", scale = 1 } = {}) {
  const pad = 26;
  const font = 44;
  const probe = document.createElement("canvas").getContext("2d");
  probe.font = `600 ${font}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
  const w = Math.ceil(probe.measureText(text).width) + pad * 2;
  const h = font + pad * 1.5;

  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const g = c.getContext("2d");
  g.font = probe.font;
  g.fillStyle = bg;
  roundRect(g, 3, 3, w - 6, h - 6, h * 0.42);
  g.fill();
  g.strokeStyle = "rgba(74,59,51,0.18)";
  g.lineWidth = 4;
  g.stroke();
  g.fillStyle = fg;
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.fillText(text, w / 2, h / 2 + 2);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.scale.set((w / h) * 0.42 * scale, 0.42 * scale, 1);
  s.renderOrder = 10;
  return s;
}

/** The round bubble that pops over a head: one big glyph, no words. */
export function bubbleSprite(glyph, color) {
  const S = 192;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");

  g.fillStyle = "#fffdf5";
  g.beginPath();
  g.arc(S / 2, S * 0.44, S * 0.36, 0, Math.PI * 2);
  g.fill();
  g.beginPath();
  g.arc(S * 0.42, S * 0.84, S * 0.07, 0, Math.PI * 2);
  g.arc(S * 0.52, S * 0.95, S * 0.04, 0, Math.PI * 2);
  g.fill();

  g.fillStyle = color;
  g.font = `700 ${Math.round(S * 0.42)}px ui-rounded, "SF Pro Rounded", Quicksand, system-ui, sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(glyph, S / 2, S * 0.46);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.scale.set(0.62, 0.62, 1);
  s.renderOrder = 11;
  return s;
}

export function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

/** The soft dark oval every cartoon character stands on. Cheaper than a shadow map. */
export function blobShadow(radius = 0.55) {
  const S = 128;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  grad.addColorStop(0, "rgba(90,70,60,0.42)");
  grad.addColorStop(0.6, "rgba(90,70,60,0.18)");
  grad.addColorStop(1, "rgba(90,70,60,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);
  const tex = new THREE.CanvasTexture(c);
  const m = new THREE.Mesh(
    new THREE.PlaneGeometry(radius * 2, radius * 2),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false })
  );
  m.rotation.x = -Math.PI / 2;
  m.position.y = 0.012;
  return m;
}
