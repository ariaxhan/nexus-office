/* The office, on a phone, through the same door.
 *
 * Everything here is a fetch at a relative path, so the request carries this
 * page's Host and Origin and passes the door's checks unchanged. Nothing is
 * loaded from anywhere else, ever: the page that answers a raised hand cannot
 * be allowed to depend on a network the phone might not have.
 *
 * The order on screen is a rule, not a layout preference. The raised hand is
 * first and nothing below it can push it off, because losing a blocked agent
 * behind a scroll is the one failure this surface is not allowed to have.
 *
 * The state rules are the few branches from `app/Office/Model/StateRules.swift`
 * that a phone actually draws, ported and not reinvented: the desk ordering,
 * whether an issue is waiting on a person, and whether what you are reading is
 * older than the snapshot it arrived in. If the two ever disagree about whether
 * something needs a person, this file is the one that is wrong.
 */

"use strict";

/* How often each half of the room asks. The gate is fast because a person is
 * standing still while it is up. The world is slow because every build of it
 * costs GitHub budget, and a phone polling hard would spend the hour's points
 * on a screen nobody is reading. */
const GATE_EVERY_MS = 3000;
const ARM_MS = 1500;  // how long a swapped question keeps its buttons disarmed
const WORLD_EVERY_MS = 30000;
const THREAD_EVERY_MS = 4000;

/* How many things to answer the band actually puts on screen. The office has
 * had 157 issues waiting at once, which is thirty-six thousand pixels of phone:
 * a list that long is not a list, it is a wall you scroll past. So the most
 * recently touched ones get cards and the rest get counted out loud, because
 * the failure to avoid is not a long band, it is a quiet one. */
const NEEDS_SHOWN = 20;

/* One snapshot interval of slack, exactly as StateRules has it: `fetched_at`
 * and `generated` are never equal even on a healthy pull, and a warning that is
 * always on is a warning nobody reads. */
const FRESHNESS_SLACK_MS = 120000;

const HEX = /^#[0-9a-fA-F]{6}$/;

const DESK_LABEL = {
  gated: "asking permission",
  waiting: "waiting on you",
  locked: "no push access",
  parked: "parked",
  refused: "refused",
  landed: "landed a PR",
  working: "working",
  idle: "quiet",
};

const TONES = ["ok", "warn", "bad", "dim", "plain"];

const state = {
  world: null,
  generated: "",
  github: null,
  gate: { state: "clear" },
  gateNotice: "",
  gateDrawn: "",
  bots: [],
  runtimeUp: false,
  at: "",
  open: null, // the bot whose thread is on screen
  turns: [],
  threadError: "",
  drafts: Object.create(null),
  commenting: Object.create(null),
  busy: Object.create(null),
};

let toastTimer = null;

/* ── small DOM ───────────────────────────────────────────────────────────── */

function el(tag, cls, words) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (words !== undefined && words !== null) node.textContent = String(words);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function head(title, count, wants) {
  const bar = el("div", "bandhead");
  bar.appendChild(el("span", "title", title));
  if (count) bar.appendChild(el("span", wants ? "count wants" : "count", count));
  return bar;
}

function button(label, cls, onTap) {
  const b = el("button", cls, label);
  b.type = "button";
  b.addEventListener("click", onTap);
  return b;
}

function say(words) {
  const toast = document.getElementById("toast");
  toast.textContent = words;
  toast.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { toast.hidden = true; }, 6000);
}

/* ── the door ────────────────────────────────────────────────────────────── */

async function read(path) {
  const answer = await fetch(path, { headers: { accept: "application/json" } });
  let body = {};
  try { body = await answer.json(); } catch (err) { body = {}; }
  return { code: answer.status, body: body };
}

/* Every write is JSON at a relative path, which is what the door asks for: it
 * arrives naming this host, from this origin, with a content type a plain HTML
 * form could never send. Nothing here loosens anything on the server. */
async function write(path, body) {
  const answer = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let out = {};
  try { out = await answer.json(); } catch (err) { out = {}; }
  return { code: answer.status, body: out };
}

/* ── clocks ──────────────────────────────────────────────────────────────── */

function when(iso) {
  if (!iso) return null;
  const at = new Date(iso);
  return isNaN(at.getTime()) ? null : at;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function onTheClock(at) {
  return at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/* "5:42 PM", "5:42 PM yesterday", "5:42 PM on Aug 26". The clock is kept even
 * when the day is not today: "showing what we had at Yesterday" is not English. */
function moment(iso, now) {
  const at = when(iso);
  if (!at) return "";
  const today = now || new Date();
  if (sameDay(at, today)) return onTheClock(at);
  const yesterday = new Date(today.getTime());
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(at, yesterday)) return onTheClock(at) + " yesterday";
  return onTheClock(at) + " on "
    + at.toLocaleDateString([], { month: "short", day: "numeric" });
}

function stamp(iso, now) {
  const at = when(iso);
  if (!at) return "";
  const today = now || new Date();
  if (sameDay(at, today)) return onTheClock(at);
  const yesterday = new Date(today.getTime());
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(at, yesterday)) return "Yesterday";
  return at.toLocaleDateString([], { month: "short", day: "numeric" });
}

/* How long the hand has been up. A gate's whole value is the clock running. */
function waited(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total < 60) return total + "s";
  if (total < 3600) return Math.floor(total / 60) + "m " + (total % 60) + "s";
  return Math.floor(total / 3600) + "h " + Math.floor((total % 3600) / 60) + "m";
}

function line(raw, limit) {
  const flat = String(raw || "").split(/\s+/).filter(Boolean).join(" ");
  if (!limit || flat.length <= limit) return flat;
  return flat.slice(0, limit).trim() + "…";
}

/* ── the state rules a phone draws ───────────────────────────────────────── */

/* The runner's own rule: an issue is waiting on a person exactly when the bot
 * had the last word. Labels are hints a human types and never get a vote. */
function needsHuman(issue) {
  return issue && issue.bot_last === true;
}

function edge(character) {
  return !character || !/[a-z0-9]/i.test(character);
}

function wordIn(word, haystack) {
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(word, from);
    if (at < 0) return false;
    if (edge(haystack[at - 1]) && edge(haystack[at + word.length])) return true;
    from = at + word.length;
  }
}

/* Does the gate say this desk's name out loud? The full owner/name counts
 * anywhere; a bare short name has to stand alone as a word and be long enough
 * to mean something, so a desk called `northwind/api` does not claim every
 * command with "api" in it. */
function gateNames(gate, repo) {
  const haystack = ((gate.target || "") + " " + (gate.detail || "")).toLowerCase();
  const full = String(repo || "").toLowerCase();
  if (full && haystack.indexOf(full) >= 0) return true;
  const short = full.split("/").pop();
  if (short.length < 4) return false;
  return wordIn(short, haystack);
}

/* The one ordering: gate, waiting, locked, parked, refused, landed, working,
 * idle. A repo that both landed a PR and is blocked on a question is blocked. */
function deskState(desk, gate) {
  const issues = desk.issues || [];
  if (gate && gate.state === "pending" && gateNames(gate, desk.repo)) return "gated";
  if (issues.some(needsHuman)) return "waiting";
  if (desk.access === false) return "locked";
  if (desk.outcome === "parked") return "parked";
  if (desk.outcome === "refused") return "refused";
  if (desk.outcome === "landed") return "landed";
  if (issues.length) return "working";
  return "idle";
}

/* What went wrong reading this desk, said once. The two halves of a pull fail
 * together and report the same sentence twice. */
function problems(desk) {
  const seen = [];
  [desk.issues_error, desk.prs_error].forEach(function (raw) {
    const text = String(raw || "").trim();
    if (text && seen.indexOf(text) < 0) seen.push(text);
  });
  return seen;
}

/* Only a `fetched_at` we can actually read counts. "We do not know how old this
 * is" must never render as "this is current". */
function isStale(desk, generated) {
  const fetched = when(desk.fetched_at);
  const built = when(generated);
  if (!fetched || !built) return false;
  return built.getTime() - fetched.getTime() > FRESHNESS_SLACK_MS;
}

function asOf(desk, generated) {
  if (!isStale(desk, generated) && !problems(desk).length) return "";
  const had = moment(desk.fetched_at);
  return had ? "as of " + had : "";
}

/* One line, never two. A spent budget outranks the per-desk error because it
 * explains every desk at once, but only on a desk that is actually behind. */
function staleNotice(desk, github, generated) {
  const had = moment(desk.fetched_at);
  const showing = had ? "; showing what we had at " + had : "";
  const behind = isStale(desk, generated) || problems(desk).length > 0;
  const paused = github && String(github.paused_until || "").trim();
  if (paused && behind) {
    const until = moment(paused);
    return (until ? "GitHub is out of budget until " + until
                  : "GitHub is out of budget") + showing;
  }
  const found = problems(desk);
  if (!found.length) return "";
  return found.join("; ") + showing;
}

function mood(section) {
  if (section.needs > 0) return "needs";
  return section.state === "ok" ? "quiet" : "off";
}

/* `cost-ledger` reads as "Cost ledger". Not clever, just never blank. */
function nameFrom(key) {
  const words = String(key).split(/[-_.]/).filter(Boolean);
  if (!words.length) return key;
  return [words[0].charAt(0).toUpperCase() + words[0].slice(1)]
    .concat(words.slice(1)).join(" ");
}

/* The wall's order: what wants a person, then what is not ok, then the quiet
 * ones. A wall sorted alphabetically buries the one source that needed you. */
function wallSections(world) {
  const raw = (world && world.sections) || {};
  const rows = Object.keys(raw).map(function (key, index) {
    const value = raw[key] || {};
    const card = value.card || null;
    const title = (card && card.title) || nameFrom(key);
    const headline = (card && card.headline) || value.detail || value.state || "";
    return {
      id: key,
      order: index,
      state: value.state || "ok",
      detail: value.detail || "",
      card: card,
      title: title,
      headline: headline,
      needs: (card && Number(card.needs)) || 0,
      asOf: (card && card.as_of) || "",
    };
  });
  function rank(row) {
    if (row.needs > 0) return 0;
    return row.state === "ok" ? 2 : 1;
  }
  return rows.sort(function (a, b) {
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    const titles = [a.title.toLowerCase(), b.title.toLowerCase()];
    if (titles[0] !== titles[1]) return titles[0] < titles[1] ? -1 : 1;
    return a.order - b.order;
  });
}

/* ── the raised hand ─────────────────────────────────────────────────────── */

function drawGate() {
  const band = document.getElementById("gate");
  const gate = state.gate || {};
  if (gate.state !== "pending") {
    band.hidden = true;
    clear(band);
    state.gateDrawn = "";
    return;
  }

  band.hidden = false;
  clear(band);
  state.gateDrawn = String(gate.id || "");

  const card = el("div", "gate");

  const asking = el("div", "asking");
  asking.appendChild(el("span", "dot s-gated"));
  asking.appendChild(el("span", "title", "An agent is asking permission"));
  asking.appendChild(el("span", "waited", "waiting " + waited(gate.waiting_s)));
  card.appendChild(asking);

  if (gate.permission) card.appendChild(el("p", "permission", gate.permission));

  card.appendChild(el("p", "label", "it wants to run"));
  // Verbatim. Never summarised, never truncated into ambiguity.
  card.appendChild(el("pre", "target", gate.target || ""));

  if (gate.detail) card.appendChild(el("p", "detail", gate.detail));

  if (gate.bot) {
    const named = state.bots.filter(function (b) { return b.id === gate.bot; })[0];
    if (named) {
      const asked = el("div", "asked");
      asked.appendChild(faceFor(named));
      asked.appendChild(el("span", null, "asked by " + named.name));
      card.appendChild(asked);
    }
  }

  if (state.gateNotice) card.appendChild(el("p", "notice", state.gateNotice));

  const answers = el("div", "answers");
  const drawn = state.gateDrawn;
  const disarmed = Date.now() < (state.gateArmAt || 0);
  [["Deny", "deny", "deny", false],
   ["Allow always", "always", "allow", true],
   ["Allow once", "once", "allow", false]].forEach(function (row) {
    const b = button(row[0], row[1], function () { answer(drawn, row[2], row[3]); });
    b.disabled = disarmed;
    answers.appendChild(b);
  });
  card.appendChild(answers);

  band.appendChild(card);
}

/* The id of the gate this card DREW, not whatever is live when the tap lands.
 * Between a gate being shown and answered the agent can time out and a
 * different gate can open, and answering by position would approve a command
 * nobody ever saw. If the two have drifted apart nothing is posted at all. */
async function answer(drawn, verdict, always) {
  const live = String((state.gate || {}).id || "");
  if (Date.now() < (state.gateArmAt || 0)) {
    state.gateNotice = "a different question arrived; read it again";
    drawGate();
    return;
  }
  if (!drawn || drawn !== live) {
    state.gateNotice = "that question moved on";
    drawGate();
    return;
  }
  const answers = document.querySelectorAll("#gate .answers button");
  answers.forEach(function (b) { b.disabled = true; });
  const sent = await write("/api/gate", {
    question_id: drawn,
    answer: verdict,
    always: always === true,
  });
  state.gateNotice = String(sent.body.message || sent.body.error || "");
  say(state.gateNotice || (sent.code === 200 ? "answered" : "the door said no"));
  await pollGate();
}

/* ── needs you ───────────────────────────────────────────────────────────── */

function waitingIssues(world) {
  const out = [];
  ((world && world.stations) || []).forEach(function (desk) {
    (desk.issues || []).filter(needsHuman).forEach(function (issue) {
      out.push({ repo: desk.repo, issue: issue });
    });
  });
  return out.sort(function (a, b) {
    const left = when(a.issue.updatedAt), right = when(b.issue.updatedAt);
    return (right ? right.getTime() : 0) - (left ? left.getTime() : 0);
  });
}

function draftKey(repo, number) {
  return repo + "#" + number;
}

function issueCard(repo, issue) {
  const key = draftKey(repo, issue.number);
  const card = el("div", "card issue");

  const top = el("div", "head");
  top.appendChild(el("span", "num", "#" + issue.number));
  top.appendChild(el("span", "title", issue.title || ""));
  card.appendChild(top);

  card.appendChild(el("p", "where", repo + " · " + stamp(issue.updatedAt)));

  const word = line(issue.last_word || "", 260);
  if (word) card.appendChild(el("p", "word", word));

  const acts = el("div", "acts");
  const open = state.commenting[key] === true;
  const busy = state.busy[key] === true;

  const talk = button(open ? "cancel" : "comment", null, function () {
    state.commenting[key] = !open;
    if (open) delete state.drafts[key];
    drawNeeds();
  });
  talk.disabled = busy;
  acts.appendChild(talk);

  ["nudge", "close"].forEach(function (kind) {
    const b = button(kind, null, function () { decide(key, kind, repo, issue.number, null); });
    b.disabled = busy;
    acts.appendChild(b);
  });
  card.appendChild(acts);

  if (open) {
    const box = el("textarea");
    box.value = state.drafts[key] || "";
    box.placeholder = "Answer it. A reply without the bot's marker is what re-queues it.";
    box.addEventListener("input", function () { state.drafts[key] = box.value; });
    card.appendChild(box);

    const send = el("div", "acts");
    const go = button("send comment", "send", function () {
      decide(key, "comment", repo, issue.number, box.value);
    });
    go.disabled = busy;
    send.appendChild(go);
    card.appendChild(send);
  }

  return card;
}

async function decide(key, kind, repo, number, body) {
  if (kind === "comment" && !String(body || "").trim()) return;
  state.busy[key] = true;
  drawNeeds();
  const payload = { kind: kind, repo: repo, issue: String(number) };
  if (kind === "comment") payload.body = body;
  const sent = await write("/api/decision", payload);
  delete state.busy[key];
  const words = String(sent.body.result || sent.body.error || "");
  say(kind + " " + repo + "#" + number + ": " + (words || (sent.code === 200 ? "done" : "refused")));
  // The draft only goes once the server has taken it. A comment cleared by a
  // refused write is a sentence a person has to type twice.
  if (sent.code === 200 && sent.body.ok) {
    delete state.drafts[key];
    state.commenting[key] = false;
  }
  drawNeeds();
  await pollWorld(false);
}

function drawNeeds() {
  const band = document.getElementById("needs");
  // Never redraw the sentence somebody is in the middle of typing.
  if (band.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") {
    return;
  }
  const issues = waitingIssues(state.world);
  const wanted = wallSections(state.world).filter(function (s) { return s.needs > 0; });
  clear(band);
  if (!issues.length && !wanted.length) {
    band.hidden = true;
    return;
  }
  band.hidden = false;
  band.appendChild(head("needs you", String(issues.length + wanted.length), true));
  issues.slice(0, NEEDS_SHOWN).forEach(function (row) {
    band.appendChild(issueCard(row.repo, row.issue));
  });
  if (issues.length > NEEDS_SHOWN) {
    // Counted, never dropped in silence. The desks band below still shows every
    // one of these repos as waiting on you.
    const rest = issues.slice(NEEDS_SHOWN);
    const desks = new Set(rest.map(function (row) { return row.repo; }));
    band.appendChild(el("p", "empty", rest.length + " more waiting on you, on "
      + desks.size + (desks.size === 1 ? " desk" : " desks")
      + " further down"));
  }
  wanted.forEach(function (section) {
    const row = el("div", "row");
    row.appendChild(el("span", "dot m-needs"));
    const body = el("div", "body");
    body.appendChild(el("p", "name", section.title));
    body.appendChild(el("p", "under", line(section.headline, 64)));
    row.appendChild(body);
    row.appendChild(el("span", "badge", String(section.needs)));
    band.appendChild(row);
  });
}

/* ── bots ────────────────────────────────────────────────────────────────── */

function faceFor(bot) {
  const face = el("span", "face" + (bot.busy ? " busy" : ""));
  const hex = String(bot.color || "");
  if (HEX.test(hex)) face.style.setProperty("--face", hex);
  return face;
}

function botSubtitle(bot) {
  const last = line((bot.last && (bot.last.content || bot.last.text)) || "", 64);
  return last || line(bot.purpose || "", 64);
}

function drawBots() {
  const band = document.getElementById("bots");
  clear(band);
  band.appendChild(head("bots", state.runtimeUp ? "" : "the harness is not running"));
  if (!state.bots.length) {
    band.appendChild(el("p", "empty", "no bots on this root"));
    return;
  }
  state.bots.forEach(function (bot) {
    const row = el("button", "row");
    row.type = "button";
    row.appendChild(faceFor(bot));
    const body = el("div", "body");
    body.appendChild(el("p", "name", bot.name));
    body.appendChild(el("p", "under", botSubtitle(bot)));
    row.appendChild(body);
    if (bot.busy) row.appendChild(el("span", "state c-working", "working"));
    else if (bot.last && bot.last.at) row.appendChild(el("span", "asof", stamp(bot.last.at)));
    row.addEventListener("click", function () { openThread(bot.id); });
    band.appendChild(row);
  });
}

/* ── desks ───────────────────────────────────────────────────────────────── */

function deskRow(desk) {
  const kind = deskState(desk, state.gate);
  const row = el("div", "row");
  row.appendChild(el("span", "dot s-" + kind));
  const body = el("div", "body");
  body.appendChild(el("p", "name", desk.repo));
  const under = el("p", "under");
  under.appendChild(el("span", "state c-" + kind, DESK_LABEL[kind]));
  if (desk.detail) under.appendChild(el("span", null, " · " + desk.detail));
  body.appendChild(under);
  row.appendChild(body);
  const old = asOf(desk, state.generated);
  if (old) row.appendChild(el("span", "asof", old));
  else row.appendChild(el("span", "under", stamp(desk.at)));
  return row;
}

function drawDesks() {
  const band = document.getElementById("desks");
  clear(band);
  const all = ((state.world && state.world.stations) || []);
  const shown = all.filter(function (d) { return !d.hidden; });
  const away = all.filter(function (d) { return d.hidden; });
  band.appendChild(head("desks", shown.length + " of " + all.length + " polled"));

  if (!shown.length && !away.length) {
    band.appendChild(el("p", "empty", "no desks yet"));
    return;
  }

  shown.forEach(function (desk) {
    band.appendChild(deskRow(desk));
    const notice = staleNotice(desk, state.github, state.generated);
    if (notice) band.appendChild(el("p", "notice", notice));
  });

  if (!away.length) return;

  // Hidden is never silent: if something put away needs a person the header
  // says so with the section still shut.
  const wants = away.filter(function (d) {
    const kind = deskState(d, state.gate);
    return kind === "gated" || kind === "waiting";
  }).length;

  const box = el("details", "away");
  const summary = el("summary");
  summary.appendChild(el("span", null, "put away (" + away.length + ")"));
  if (wants) summary.appendChild(el("span", "wants", " · " + wants + " needs you"));
  box.appendChild(summary);
  away.forEach(function (desk) {
    const row = deskRow(desk);
    row.appendChild(button("bring back", null, function () { bringBack(desk.repo); }));
    box.appendChild(row);
  });
  band.appendChild(box);
}

async function bringBack(repo) {
  const sent = await write("/api/desks", { repo: repo, hidden: false });
  say(sent.code === 200 ? repo + " is back" : String(sent.body.error || "refused"));
  await pollWorld(false);
}

/* ── the wall ────────────────────────────────────────────────────────────── */

function drawWall() {
  const band = document.getElementById("wall");
  clear(band);
  const sections = wallSections(state.world);
  const total = sections.reduce(function (sum, s) { return sum + s.needs; }, 0);
  band.appendChild(head("wall", total ? "the wall needs " + total : "", total > 0));

  if (!sections.length) {
    band.appendChild(el("p", "empty", "nothing on the wall"));
    return;
  }

  sections.forEach(function (section) {
    const card = el("div", "card wallcard");
    const top = el("div", "head");
    top.appendChild(el("span", "dot m-" + mood(section)));
    top.appendChild(el("span", "title", section.title));
    if (section.state !== "ok") top.appendChild(el("span", "pill", section.state));
    if (section.needs > 0) {
      top.appendChild(el("span", "pill wants", section.needs + " need you"));
    }
    card.appendChild(top);

    card.appendChild(el("p", "headline" + (section.state === "ok" ? "" : " off"),
                        section.headline));

    // Every word below came out of the card its source wrote. There is no
    // branch here for any particular source, and there must never be one.
    const facts = (section.card && section.card.facts) || [];
    if (facts.length) {
      const list = el("div", "facts");
      facts.forEach(function (fact) {
        const raw = String(fact.tone || "").trim().toLowerCase();
        const tone = TONES.indexOf(raw) >= 0 ? raw : "plain";
        const row = el("div", "fact");
        row.appendChild(el("span", "label", fact.label || ""));
        row.appendChild(el("span", "value t-" + tone, fact.value || ""));
        list.appendChild(row);
      });
      card.appendChild(list);
    }

    if (section.detail && section.detail !== section.headline) {
      card.appendChild(el("p", "detail", section.detail));
    }
    const old = moment(section.asOf);
    if (old) card.appendChild(el("p", "asof", "as of " + old));
    band.appendChild(card);
  });
}

/* ── a thread ────────────────────────────────────────────────────────────── */

function openThread(id) {
  state.open = id;
  state.turns = [];
  state.threadError = "";
  drawThread();
  pollThread();
}

function closeThread() {
  state.open = null;
  drawThread();
}

function drawThread() {
  const panel = document.getElementById("thread");
  if (!state.open) {
    panel.hidden = true;
    clear(panel);
    return;
  }
  const bot = state.bots.filter(function (b) { return b.id === state.open; })[0]
    || { id: state.open, name: state.open, purpose: "", color: "" };
  const key = "bot:" + bot.id;

  // Keep the half-written message: the panel is rebuilt on every poll and a
  // sentence a person is in the middle of is work, not a view detail.
  const focused = panel.contains(document.activeElement)
    && document.activeElement.tagName === "TEXTAREA";
  if (focused) state.drafts[key] = document.activeElement.value;

  panel.hidden = false;
  clear(panel);

  const bar = el("header", "top");
  bar.appendChild(button("back", null, closeThread));
  bar.appendChild(el("p", "mark", bot.name));
  bar.appendChild(el("p", "stamp", bot.busy ? "working" : (bot.purpose || "")));
  panel.appendChild(bar);

  const turns = el("div", "turns");
  if (state.threadError) turns.appendChild(el("p", "empty", state.threadError));
  if (!state.turns.length && !state.threadError) {
    if (bot.purpose) turns.appendChild(el("p", "empty", bot.purpose));
    turns.appendChild(el("p", "empty", "Message " + bot.name + " to start."));
  }
  state.turns.forEach(function (turn) {
    const mine = turn.role === "user";
    turns.appendChild(el("p", "bubble " + (mine ? "me" : "them"),
                         turn.content || turn.text || ""));
  });
  if (bot.busy) turns.appendChild(el("p", "working", bot.name + " is working"));
  panel.appendChild(turns);

  const composer = el("div", "composer");
  const box = el("textarea");
  box.value = state.drafts[key] || "";
  box.placeholder = bot.busy ? "busy with a turn" : "Message " + bot.name;
  box.addEventListener("input", function () { state.drafts[key] = box.value; });
  composer.appendChild(box);
  const send = button("send", "send", function () { sayTo(bot.id, box.value); });
  send.disabled = bot.busy === true;
  composer.appendChild(send);
  panel.appendChild(composer);

  if (focused) {
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  } else {
    turns.scrollTop = turns.scrollHeight;
  }
}

async function sayTo(id, message) {
  if (!String(message || "").trim()) return;
  const sent = await write("/api/chat", { message: message, bot: id });
  if (sent.code === 202) {
    delete state.drafts["bot:" + id];
    say("sent; a turn is a whole agent run");
  } else if (sent.code === 409) {
    say("busy with a turn already");
  } else {
    say(String(sent.body.error || "the door said no"));
  }
  await pollBots();
  await pollThread();
}

async function pollThread() {
  if (!state.open) return;
  const got = await read("/api/chat?bot=" + encodeURIComponent(state.open));
  if (got.code === 200) {
    state.turns = got.body.turns || [];
    state.threadError = "";
  } else {
    state.turns = [];
    state.threadError = String(got.body.error || "the harness said " + got.code);
  }
  drawThread();
}

/* ── the polls ───────────────────────────────────────────────────────────── */

async function pollGate() {
  try {
    const got = await read("/api/gate");
    if (got.code !== 200) return;
    const was = String((state.gate || {}).id || "");
    state.gate = got.body || { state: "clear" };
    const now = String(state.gate.id || "");
    if (now !== was) {
      state.gateNotice = "";
      // A different question arrived under the same card. The buttons stay
      // disarmed for a beat so a thumb already on its way down cannot approve
      // a command nobody read; the redraw at the end of the beat re-arms them.
      if (was && now) {
        state.gateArmAt = Date.now() + ARM_MS;
        state.gateNotice = "a different question arrived; read it again";
        setTimeout(drawGate, ARM_MS + 20);
      }
    }
    drawGate();
  } catch (err) {
    // A poll that could not reach the door leaves the last picture up rather
    // than blanking the room. The stamp is what says how old it is.
  }
}

async function pollBots() {
  try {
    const got = await read("/api/bots");
    if (got.code !== 200) return;
    state.bots = got.body.bots || [];
    state.runtimeUp = got.body.runtime === "up";
    drawBots();
    if (state.open) drawThread();
  } catch (err) { /* the roster stays as it was */ }
}

function drawStamp() {
  const parts = [];
  if (state.at) parts.push("as of " + moment(state.at));
  const budget = state.github || {};
  if (String(budget.paused_until || "").trim()) parts.push("GitHub is out of budget");
  document.getElementById("stamp").textContent = parts.join(" · ");
}

async function pollWorld(nowPlease) {
  const refresh = document.getElementById("refresh");
  try {
    let path = "/api/world";
    if (nowPlease) {
      // The one place the whole page is allowed to spend GitHub budget on
      // purpose. Never on a timer: a phone in a pocket must not be able to
      // burn the hour's points on a screen nobody is reading.
      path = "/api/world?fresh=1";
      refresh.disabled = true;
      refresh.textContent = "asking";
    }
    const got = await read(path);
    if (got.code === 200) {
      state.world = got.body.world || null;
      state.at = got.body.at || "";
      state.generated = (state.world && state.world.generated) || "";
      state.github = (state.world && state.world.github) || null;
      if (nowPlease && got.body.fresh === false) say("asked less than a minute ago; this is the cache");
      drawStamp();
      drawNeeds();
      drawDesks();
      drawWall();
    } else {
      // The door answered, but not with a world. The last picture stays up
      // and the stamp says why it is not moving.
      document.getElementById("stamp").textContent =
        "the door is not answering (" + got.code + ")";
    }
  } catch (err) {
    document.getElementById("stamp").textContent = "the door is not answering";
  } finally {
    refresh.disabled = false;
    refresh.textContent = "refresh";
  }
}

function start() {
  document.getElementById("refresh").addEventListener("click", function () {
    pollWorld(true);
  });
  pollGate();
  pollWorld(false);
  pollBots();
  setInterval(pollGate, GATE_EVERY_MS);
  setInterval(function () { pollWorld(false); pollBots(); }, WORLD_EVERY_MS);
  setInterval(pollThread, THREAD_EVERY_MS);
}

start();
