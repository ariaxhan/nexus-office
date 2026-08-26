import { token, clearToken, login, getWorld } from "./api.js";
import { Office } from "./scene/office.js";
import { Panel, needsHuman } from "./ui/panel.js";
import * as view from "./ui/filters.js";
import { demoWorld } from "./demo.js";
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
let currentView = view.load();
let shownRepos = new Set();

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

/**
 * The top bar always describes the WHOLE world, never the filtered view.
 *
 * A filter that also filters the count is how you convince yourself there are
 * three things waiting when there are thirty. Only the desk count says
 * "shown/total", and it says so explicitly.
 */
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
    bit("desks", shownRepos.size === world.stations.length
      ? world.stations.length
      : `${shownRepos.size}/${world.stations.length}`),
    bit("open", open),
    bit("landed 24h", t.landed || 0),
    bit(`synced ${age.text}`, "", age.stale ? "stale" : "")
  );
  // The trailing bit has an empty value; drop its stray leading space.
  s.lastChild.firstChild.remove();

  const btn = $("needs");
  const gated = world.runtime?.gate?.state === "pending";
  btn.hidden = waiting === 0 && !gated;
  btn.textContent = gated
    ? "an agent is asking permission"
    : `${waiting} waiting on you`;
  btn.classList.toggle("pill-gate", gated);

  // The runtime's own health is a fact about the room, not an absence.
  const board = world.runtime?.board;
  if (board && board.state !== "up" && board.state !== "unconfigured") {
    s.append(bit("", board.state === "down" ? "runtime not running" : `runtime ${board.state}`, "stale"));
    s.lastChild.firstChild.remove();
  }
}

function stateOf(st) {
  // One ordering, written once. A repo that both landed a PR and is blocked on a
  // question is blocked: the thing a person has to do always wins the desk. A
  // GATE outranks even that, because an agent is sitting there with a clock
  // running while an unanswered issue simply waits.
  if (st.gate) return "gated";
  if ((st.issues || []).some(needsHuman)) return "waiting";
  if (st.access === false) return "locked";
  if (st.outcome === "parked") return "parked";
  if (st.outcome === "refused") return "refused";
  if (st.outcome === "landed") return "landed";
  if ((st.issues || []).length) return "working";
  return "idle";
}

function paintFilters() {
  const modes = $("modes");
  modes.replaceChildren();
  for (const m of view.MODES) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = m.label;
    b.setAttribute("aria-pressed", String(currentView.mode === m.id));
    b.onclick = () => setView({ ...currentView, mode: m.id });
    modes.append(b);
  }

  const un = $("unhide");
  const hand = currentView.repos.length + currentView.owners.length;
  const { hiddenByHand, hiddenByMode, waitingButHidden } = lastSplit;
  const total = hiddenByHand.length + hiddenByMode.length;
  un.hidden = total === 0;
  un.replaceChildren();
  if (total) {
    // Never a bare "12 hidden". If something you put away has started needing
    // you, that is the only number on this pill that matters.
    if (waitingButHidden) {
      const b = document.createElement("b");
      b.textContent = `${waitingButHidden} hidden need you`;
      un.append(b, " · show all");
    } else {
      un.append(`${total} hidden · show all`);
    }
    un.title = hand
      ? `${hand} put away by hand, ${hiddenByMode.length} filtered out`
      : `${hiddenByMode.length} filtered out`;
  }
}

let lastSplit = { hiddenByHand: [], hiddenByMode: [], waitingButHidden: 0 };

function setView(next) {
  currentView = next;
  view.save(currentView);
  if (world) applyWorld(world, { rebuild: true });
}

function applyWorld(next, { rebuild = false } = {}) {
  world = next;

  // The runtime is not a repo, so an open gate has to be given a desk to stand
  // at. It attaches to the station whose repo matches the runtime's root when
  // there is one, and otherwise gets a desk of its own rather than being dropped.
  const gate = world.runtime?.gate?.state === "pending" ? world.runtime.gate : null;
  const rootRepo = (world.runtime?.root || "").split("/").pop();
  let host = gate && world.stations.find((s) => s.repo.split("/").pop() === rootRepo);
  if (gate && !host) {
    host = {
      repo: rootRepo ? `runtime/${rootRepo}` : "runtime/agent",
      access: true, outcome: "", detail: "the local agent runtime",
      at: "", runs: [], issues: [], synthetic: true,
    };
    world.stations = [...world.stations, host];
  }
  for (const st of world.stations) st.gate = st === host ? gate : null;

  for (const st of world.stations) {
    st.state = stateOf(st);
    st.resident = resident(st.repo);
  }
  world.stations.sort((a, b) => a.repo.localeCompare(b.repo));

  const split = view.apply(world.stations, currentView, needsHuman);
  // A gate is never hidden, by any filter, by any hand. Losing a blocked agent
  // behind a view is the one failure this surface cannot be allowed to have.
  for (const s of [...split.hiddenByHand, ...split.hiddenByMode]) {
    if (s.gate) split.shown.push(s);
  }
  lastSplit = split;
  const shown = split.shown;
  const changed = shown.length !== shownRepos.size ||
    shown.some((s) => !shownRepos.has(s.repo));
  shownRepos = new Set(shown.map((s) => s.repo));

  if (rebuild || changed || !office.villagers.size) office.build(shown);
  else office.update(shown);

  paintStats();
  paintFilters();

  // Keep whatever the panel was showing pointed at the fresh object, unless the
  // filter just took that desk out of the room.
  if (panel.station) {
    const again = world.stations.find((s) => s.repo === panel.station.repo);
    if (again && shownRepos.has(again.repo)) panel.showStation(again, world);
    else if (!shownRepos.has(panel.station.repo)) panel.close();
  }
}

// `?demo=1` runs the room on a fabricated floor: no account, no session, no
// pipeline. It is how a stranger sees what this is before setting anything up,
// and how the room itself gets worked on without a live snapshot.
const DEMO = new URLSearchParams(location.search).has("demo");

async function pull({ quiet = false } = {}) {
  if (DEMO) {
    $("boot").hidden = true;
    applyWorld(demoWorld());
    return;
  }
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
    if (err.unauthorized) { clearToken(); return gate("Your session expired. Password again?"); }
    if (!quiet) toast(err.message || "could not reach the office", true);
  }
}

function gate(message) {
  $("boot").hidden = true;
  $("gate").hidden = false;
  const note = $("gate-note");
  const field = $("gate-key");
  const go = $("gate-go");
  note.textContent = message || "";
  note.hidden = !message;

  const submit = async () => {
    const v = field.value;
    if (!v) return;
    go.disabled = true;
    go.textContent = "checking";
    try {
      await login(v);
      field.value = "";
      $("gate").hidden = true;
      note.hidden = true;
      pull();
    } catch (err) {
      note.textContent = err.message || "that did not work";
      note.hidden = false;
      field.select();
    } finally {
      go.disabled = false;
      go.textContent = "Open the office";
    }
  };
  go.onclick = submit;
  field.onkeydown = (e) => { if (e.key === "Enter") submit(); };
  field.focus();
}

function boot() {
  office = new Office($("stage"));
  office.start();

  panel = new Panel($("panel"), {
    onToast: toast,
    onRefresh: () => pull({ quiet: true }),
    onHideRepo: (repo) => {
      setView(view.hideRepo(currentView, repo));
      toast(`${repo} put away. "show all" in the top bar brings it back.`);
    },
    onHideOwner: (repo) => {
      const owner = view.ownerOf(repo);
      setView(view.hideOwner(currentView, repo));
      toast(`the whole ${owner} wing is put away.`);
    },
  });

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
  $("talk").onclick = () => {
    office.selected = null;
    panel.showChat(world || {});
  };
  $("refresh").onclick = () => pull();
  $("unhide").onclick = () => {
    setView(view.showEverything(currentView));
    toast("everything is back.");
  };
  $("needs").onclick = () => {
    // A gate jumps straight to the agent asking. Making someone hunt for a
    // blocked agent through a list defeats the point of it standing up.
    const gate = world?.runtime?.gate?.state === "pending";
    if (gate) {
      const host = world.stations.find((s) => s.gate);
      if (host) {
        office.focus(host.repo);
        return panel.showStation(host, world);
      }
    }
    office.selected = null;
    // The tray shows everything waiting, including desks the filter hid. Being
    // able to lose a blocked issue behind a view is exactly the failure this
    // whole surface exists to prevent.
    panel.showInbox(world);
  };

  // Reachable from the console on purpose. A 3D surface that can only be
  // inspected by squinting at screenshots is a surface nobody can debug.
  window.office = office;
  window.panel = panel;

  if (DEMO) {
    document.body.classList.add("is-demo");
    pull();
    return;
  }

  if (!token()) return gate();
  pull();
  setInterval(() => pull({ quiet: true }), POLL_MS);
  addEventListener("visibilitychange", () => { if (!document.hidden) pull({ quiet: true }); });
}

// A session that stopped working should not leave anyone at a dead door.
addEventListener("unhandledrejection", (e) => {
  if (e.reason && e.reason.unauthorized) { clearToken(); gate("Your session expired. Password again?"); }
});

boot();
