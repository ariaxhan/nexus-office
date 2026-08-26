/**
 * Take pictures of the room, headless, so a change can be judged without a human
 * squinting at a browser.
 *
 *   npm run shot                  every framing into shots/
 *   npm run shot -- gate room     just those
 *
 * This exists because almost every defect this project has had was invisible in
 * source and obvious on screen: a lock screen sitting on top of a working office,
 * villagers with holes for heads, a face plate hidden inside its own skull, a
 * raised hand that read as a shrug. A build that passes proves nothing about a
 * room. A picture does.
 *
 * It runs against `?demo=1`, so it needs no account, no session, no pipeline and
 * no network. That is deliberate: a check that needs credentials is a check that
 * stops running.
 */

import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFile, mkdir, readdir, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// SHOT_DIST lets several lanes build and shoot at once without fighting over dist/.
const DIST = process.env.SHOT_DIST
  ? path.resolve(ROOT, process.env.SHOT_DIST)
  : path.join(ROOT, "dist");
const OUT = path.join(ROOT, "shots");

const TYPES = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
};

/** Serve dist/, falling back to index.html so the app's own routes resolve. */
function serve() {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    let file = path.join(DIST, url.pathname);
    if (!existsSync(file) || url.pathname.endsWith("/")) file = path.join(DIST, "index.html");
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404).end("no");
    }
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r(server)));
}

/**
 * Freeze the animation at a fixed time and render one frame.
 *
 * Two reasons. Poses are sine functions of elapsed time, so an un-frozen shot
 * differs run to run and every picture looks like a change. And a headless page
 * can be throttled, which leaves every animated property at its constructor
 * default and makes a correct pose look broken.
 */
const FREEZE = (t) => {
  const o = window.office;
  for (const v of o.villagers.values()) v.update(t, v.station.repo === o.selected);
  o.controls.update();
  o.renderer.render(o.scene, o.camera);
};

const ready = async (page) => {
  await page.waitForFunction(() => window.office?.villagers?.size > 0, { timeout: 15000 });
  await page.waitForTimeout(250);
};

/** Click a desk by repo, the way a person would, via the scene's own picker. */
const clickDesk = (repo) => {
  const o = window.office;
  o.scene.updateMatrixWorld(true);
  const pad = o.pickables.find((p) => p.userData.station.repo === repo);
  if (!pad) throw new Error("no desk for " + repo);
  const v = pad.position.clone().project(o.camera);
  const r = o.canvas.getBoundingClientRect();
  const x = Math.round((v.x * 0.5 + 0.5) * r.width);
  const y = Math.round((-v.y * 0.5 + 0.5) * r.height);
  const mk = (type) => new PointerEvent(type, {
    clientX: x, clientY: y, bubbles: true,
    pointerId: 1, pointerType: "mouse", isPrimary: true,
    button: 0, buttons: type === "pointerdown" ? 1 : 0,
  });
  o.canvas.dispatchEvent(mk("pointerdown"));
  o.canvas.dispatchEvent(mk("pointerup"));
};

/** Click a fixture (the clock, the chart, the mailroom) the same way. */
const clickFixture = (id) => {
  const o = window.office;
  o.scene.updateMatrixWorld(true);
  const hit = o.pickables.find((p) => p.userData.fixture?.id === id);
  // Not every fixture is clickable. The in-tray sits inside the desk's own pick
  // pad and deliberately owns no target, so its picture is the room it changed
  // rather than a panel. Returning false says "nothing to click", which is not
  // the same as the fixture being missing, and the caller reports which it was.
  if (!hit) return false;
  const v = new (hit.position.constructor)().setFromMatrixPosition(hit.matrixWorld).project(o.camera);
  const r = o.canvas.getBoundingClientRect();
  const x = Math.round((v.x * 0.5 + 0.5) * r.width);
  const y = Math.round((-v.y * 0.5 + 0.5) * r.height);
  const mk = (type) => new PointerEvent(type, {
    clientX: x, clientY: y, bubbles: true,
    pointerId: 1, pointerType: "mouse", isPrimary: true,
    button: 0, buttons: type === "pointerdown" ? 1 : 0,
  });
  o.canvas.dispatchEvent(mk("pointerdown"));
  o.canvas.dispatchEvent(mk("pointerup"));
  return true;
};

const SHOTS = {
  // The whole floor. Layout, pods, plaques, who is standing and who is not.
  room: {
    viewport: { width: 1600, height: 900 },
    async run(page) { await ready(page); },
  },

  // One desk close up: face, pose, chair, where the name plaque lands.
  desk: {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      await page.evaluate(clickDesk, "acme/website");
      await page.waitForTimeout(400);
    },
  },

  // The single most important state in the room, and the easiest to leave
  // untested because it only appears when something is genuinely stuck.
  gate: {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      await page.click("#needs");
      await page.waitForTimeout(500);
      await page.waitForSelector(".gate-box", { timeout: 5000 });
    },
  },

  // Rendered issue markdown: headings, tables, code, checkboxes.
  markdown: {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      await page.evaluate(clickDesk, "acme/storefront");
      await page.waitForTimeout(400);
      await page.evaluate(() => {
        const c = [...document.querySelectorAll("#panel .issue")]
          .find((x) => x.textContent.includes("host name"));
        if (!c) throw new Error("no markdown-rich issue on this desk");
        c.click();
      });
      await page.waitForSelector(".issue-body.md table", { timeout: 5000 });
    },
  },

  // Hiding: the room re-packs, and the pill says what went away.
  filtered: {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      await page.evaluate(() =>
        [...document.querySelectorAll("#modes button")]
          .find((b) => b.textContent === "needs me").click());
      await page.waitForTimeout(600);
    },
  },

  // Finished work waiting only on a merge, including one that cannot merge.
  // This is the state that stranded forty-one landed issues.
  merges: {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      await page.click("#tomerge");
      await page.waitForSelector("#panel:not([hidden])", { timeout: 5000 });
      await page.waitForTimeout(300);
    },
  },

  // What you asked for, and what became of it. The state that used to be
  // invisible: a queued order, and one that failed with its reason.
  orders: {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      await page.click("#orders");
      await page.waitForSelector("#panel:not([hidden])", { timeout: 5000 });
      await page.waitForTimeout(300);
    },
  },

  // The working light's other two states. The room shot covers "running",
  // because that is the demo floor's default; these are the ones that used to
  // have no picture at all, and "unknown" is the one that must never be mistaken
  // for a quiet office.
  "light-idle": {
    viewport: { width: 1600, height: 900 },
    query: "&pipeline=idle",
    async run(page) { await ready(page); },
  },
  "light-unknown": {
    viewport: { width: 1600, height: 900 },
    query: "&pipeline=unknown",
    async run(page) { await ready(page); },
  },

  // It has to work on a phone, which is most of why it is on the internet.
  phone: {
    viewport: { width: 390, height: 844 },
    async run(page) {
      await ready(page);
      await page.click("#needs");
      await page.waitForTimeout(500);
    },
  },
};

// One framing per fixture, discovered from the directory rather than listed, so
// a new fixture cannot ship without a picture of it. Each fails until its
// fixture is built, which is the point: red means not done.
for (const f of await readdir(path.join(ROOT, "src/scene/fixtures"))) {
  if (!f.endsWith(".js") || f === "all.js") continue;
  const id = f.replace(/\.js$/, "");
  SHOTS[id] = {
    viewport: { width: 1600, height: 900 },
    async run(page) {
      await ready(page);
      const clicked = await page.evaluate(clickFixture, id);
      if (clicked) await page.waitForSelector("#panel:not([hidden])", { timeout: 5000 });
      else console.log(`  ${id} has no click target; framing the room instead`);
      await page.waitForTimeout(300);
    },
  };
}

const wanted = process.argv.slice(2).filter((a) => !a.startsWith("-"));
const names = wanted.length ? wanted : Object.keys(SHOTS);
for (const n of names) if (!SHOTS[n]) throw new Error(`unknown shot ${n}. have: ${Object.keys(SHOTS).join(", ")}`);

if (!existsSync(DIST)) {
  console.error("no dist/. run `npm run build` first.");
  process.exit(2);
}

await mkdir(OUT, { recursive: true });
// Stale pictures are worse than none: they get read as current.
for (const f of await readdir(OUT)) {
  if (f.endsWith(".png") && names.includes(f.replace(/^\d+-|\.png$/g, ""))) {
    await unlink(path.join(OUT, f));
  }
}

const server = await serve();
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch();
let failures = 0;

// Numbered by position in the full set, never by position in this run. A
// partial `npm run shot -- desk` that wrote 01-desk.png beside a 01-room.png
// from a full run is how two different pictures end up looking like the same one.
const ALL = Object.keys(SHOTS);
for (const name of names) {
  const i = ALL.indexOf(name);
  const shot = SHOTS[name];
  const page = await browser.newPage({ viewport: shot.viewport, deviceScaleFactor: 2 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  try {
    await page.goto(`${base}/?demo=1${shot.query || ""}`, { waitUntil: "load" });
    await shot.run(page);
    await page.evaluate(FREEZE, 6.0);
    const file = path.join(OUT, `${String(i + 1).padStart(2, "0")}-${name}.png`);
    await page.screenshot({ path: file });
    // A console error during a shot is a defect even when the picture looks
    // fine, so it is reported next to the file rather than swallowed.
    console.log(`${errors.length ? "!" : "."} ${path.relative(ROOT, file)}` +
      (errors.length ? `  ${errors.length} console error(s): ${errors[0].slice(0, 120)}` : ""));
    if (errors.length) failures++;
  } catch (err) {
    console.error(`x ${name}: ${err.message.split("\n")[0]}`);
    failures++;
  } finally {
    await page.close();
  }
}

await browser.close();
server.close();

if (failures) {
  console.error(`\n${failures} shot(s) failed or logged errors.`);
  process.exit(1);
}
console.log(`\n${names.length} shot(s) in shots/. Look at them.`);
