import { bootstrapToken, clearToken, getWorld } from "./api.js";
import { Office } from "./scene/office.js";
import { Panel, needsHuman } from "./ui/panel.js";
import { resident } from "./names.js";

/**
 * Glue. Pull the world, build the room, poll, and never lie about how old the
 * picture is: a stale snapshot presented as current is the exact failure this
 * whole pipeline exists to prevent, so the age is on screen at all times.
 */

const $ = (id) => document.getElementById(id);
const POLL_MS = 20000;

let office;
let panel;
let world = null;
let toastTimer;

function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}

function ago(iso) {
  if (!iso) return { text: "never", stale: true };
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return { text: "never", stale: true };
  const text = mins < 1 ? "just now" : mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
  return { text, stale: mins > 25 };
}

function paintStats() {
  const s = $("stats");
  s.replaceChildren();
  if (!world) return;
  const t = world.today || {};
  const waiting = world.stations.reduce(
    (n, st) => n + (st.issues || []).filter(needsHuman).length, 0);
  const open = world.stations.reduce((n, st) => n + (st.issues || []).length, 0);

  const bit = (label, value, cls) => {
    const span = document.createElement("span");
    if (cls) span.className = cls;
    const b = document.createElement("b");
    b.textContent = String(value);
    span.append(b, ` ${label}`);
    return span;
  };
  const age = ago(world.at);
  s.append(
    bit("desks", world.stations.length),
    bit("open", open),
    bit("landed 24h", t.landed || 0),
    bit(`synced ${age.text}`, "", age.stale ? "stale" : "")
  );
  // The trailing bit has an empty value; drop its stray leading space.
  s.lastChild.firstChild.remove();

  const btn = $("needs");
  btn.hidden = waiting === 0;
  btn.textContent = `${waiting} waiting on you`;
}

function stateOf(st) {
  // One ordering, written once. A repo that both landed a PR and is blocked on a
  // question is blocked: the thing a person has to do always wins the desk.
  if ((st.issues || []).some(needsHuman)) return "waiting";
  if (st.access === false) return "locked";
  if (st.outcome === "parked") return "parked";
  if (st.outcome === "refused") return "refused";
  if (st.outcome === "landed") return "landed";
  if ((st.issues || []).length) return "working";
  return "idle";
}

function applyWorld(next) {
  world = next;
  for (const st of world.stations) {
    st.state = stateOf(st);
    st.resident = resident(st.repo);
  }
  world.stations.sort((a, b) => a.repo.localeCompare(b.repo));

  if (!office.villagers.size) office.build(world.stations);
  else office.update(world.stations);

  paintStats();

  // Keep whatever the panel was showing pointed at the fresh object.
  if (panel.station) {
    const again = world.stations.find((s) => s.repo === panel.station.repo);
    if (again) panel.showStation(again, world);
  }
}

async function pull({ quiet = false } = {}) {
  try {
    const res = await getWorld();
    if (!res.world) {
      $("boot").textContent = "The office is built, but home has not sent a snapshot yet.";
      $("boot").hidden = false;
      return;
    }
    $("boot").hidden = true;
    applyWorld({ ...res.world, at: res.at });
  } catch (err) {
    if (err.unauthorized) return gate();
    if (!quiet) toast(err.message || "could not reach the office", true);
  }
}

function gate() {
  $("boot").hidden = true;
  $("gate").hidden = false;
  $("gate-go").onclick = () => {
    const v = $("gate-key").value.trim();
    if (!v) return;
    localStorage.setItem("nexus-office-token", v);
    $("gate").hidden = true;
    pull();
  };
  $("gate-key").onkeydown = (e) => { if (e.key === "Enter") $("gate-go").click(); };
}

/** `/pair/ABC123` trades a six-character code for the real key, once. */
async function claimPairing() {
  const m = /^\/pair\/([A-Z2-9]{6})$/.exec(location.pathname);
  if (!m) return false;
  history.replaceState(null, "", "/");
  try {
    const r = await fetch("/api/pair/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code: m[1] }),
    });
    const body = await r.json();
    if (!r.ok || !body.token) throw new Error(body.error || "pairing failed");
    localStorage.setItem("nexus-office-token", body.token);
    return true;
  } catch (err) {
    toast(err.message || "pairing failed", true);
    return false;
  }
}

async function boot() {
  await claimPairing();
  const token = bootstrapToken();
  office = new Office($("stage"));
  office.start();

  panel = new Panel($("panel"), { onToast: toast, onRefresh: () => pull({ quiet: true }) });

  office.onPick = (station) => {
    if (!station) { office.selected = null; return panel.close(); }
    office.focus(station.repo);
    panel.showStation(world.stations.find((s) => s.repo === station.repo) || station, world);
  };

  $("brand").onclick = () => {
    office.selected = null;
    panel.close();
    office.frameAll();
  };
  $("refresh").onclick = () => pull();
  $("needs").onclick = () => {
    office.selected = null;
    panel.showInbox(world);
  };

  // Reachable from the console on purpose. A 3D surface that can only be
  // inspected by squinting at screenshots is a surface nobody can debug.
  window.office = office;
  window.panel = panel;

  if (!token) return gate();
  pull();
  setInterval(() => pull({ quiet: true }), POLL_MS);
  addEventListener("visibilitychange", () => { if (!document.hidden) pull({ quiet: true }); });
}

// A token that stopped working should not leave someone stuck on a locked door.
addEventListener("unhandledrejection", (e) => {
  if (e.reason && e.reason.unauthorized) { clearToken(); gate(); }
});

boot();
