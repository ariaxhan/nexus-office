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
/* An agent's status changes on the scale of a tool call: slower than the gate,
 * which is a question waiting on a person, and faster than the world, which is
 * a GitHub budget. It costs one local subprocess and no network at all. */
const SESSION_EVERY_MS = 6000;
/* One screen of a conversation. The whole run is a terminal's job. */
const SESSION_TURNS = 4;
const THREAD_EVERY_MS = 4000;
/* What is running on the machine, and the open transcript. Both are local file
 * reads, so they may be fast; neither touches GitHub or the harness. */
const LIVE_EVERY_MS = 5000;
/* One page of a transcript. The reader asks for the newest page, then only for
 * what arrived after it: a session with forty thousand lines must never make
 * this page carry forty thousand lines to show the last four. */
const READER_PAGE = 200;
/* A tool call and its output are one line each until they are tapped. A reader
 * that pastes every heredoc in full is a reader you cannot find the sentences
 * in. */
const TOOL_PREVIEW = 140;

/* The cap is on the DIM half only. The office has had 157 issues waiting at
 * once, which is thirty-six thousand pixels of phone, and a list that long is a
 * wall you scroll past. But a stated question is the whole reason this band
 * exists, so those are never cut: only the parks the pipeline has not turned
 * into a question yet, and what is cut is counted out loud, because the failure
 * to avoid is not a long band, it is a quiet one. */
const PARK_DESKS_SHOWN = 8;
const PARK_ISSUES_SHOWN = 3;

/* When this browser last looked at the queue. Local to the phone: nothing on
 * the server reads it and no other device sees it. */
const LAST_SEEN_KEY = "office.lastSeen";

/* A picture, on the way to a door that takes one attachment of at most half a
 * megabyte. A phone camera hands over four megabytes, so the page shrinks it
 * here rather than finding out on the send: the longest side comes down to 1200,
 * it goes out as a JPEG, and the quality drops a rung at a time until the base64
 * fits. 480 KB leaves the door's 512 KB some headroom for the JSON and for the
 * message going with it. All of it is canvas and FileReader, because nothing on
 * this page is ever allowed to load code from anywhere. */
const PHOTO_LONGEST = 1200;
const PHOTO_CEILING = 480 * 1024;
const PHOTO_LADDER = [
  { side: PHOTO_LONGEST, quality: 0.8 },
  { side: PHOTO_LONGEST, quality: 0.6 },
  { side: PHOTO_LONGEST, quality: 0.45 },
  { side: 1000, quality: 0.5 },
  { side: 800, quality: 0.5 },
  { side: 640, quality: 0.45 },
  { side: 480, quality: 0.4 },
  { side: 360, quality: 0.35 },
];

/* An iPhone hands the photo library's own HEIC straight to the page, and no
 * browser will decode one. The page says which door to use instead rather than
 * sending nothing and looking like it worked. */
const CANNOT_READ =
  "this phone gave a format the page cannot read; take the photo with the camera option";

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
  // Every hand in the air, oldest first. The card draws the oldest; the rest
  // are why it says how many are left instead of pretending it is the only one.
  gates: [],
  gateNotice: "",
  gateDrawn: "",
  bots: [],
  runtimeUp: false,
  at: "",
  open: null, // the bot whose thread is on screen
  turns: [],
  threadError: "",
  drafts: Object.create(null),
  /* A picture picked and not yet sent, under the same key as the draft: coming
   * back to a composer that kept the sentence and threw away the screenshot is
   * the draft bug wearing a different hat. */
  photos: Object.create(null),
  /* What this page sent a photo with, this visit. The mark on a turn is a fact
   * off the wire when the harness echoes `attachments`, and this when it does
   * not. The weaker claim is why the mark says a photo went and never offers to
   * show one: the bytes were carried and never written down. */
  sentPhotos: Object.create(null),
  commenting: Object.create(null),
  busy: Object.create(null),
  /* The agents running on the machine, and the one whose thread is open. Its
   * own poll, because it is measured by asking hcom rather than by building a
   * snapshot: it moves on the scale of a tool call, not of a GitHub budget. */
  sessions: null,
  /* When the roster last came back, and why it did not. The band keeps the last
   * one it had and says how old it is: an empty list is a claim that nothing is
   * running, and this page may only make that claim when the door said so. */
  sessionsAt: "",
  sessionsError: "",
  openSession: "",
  /* Every agent process on this machine, and the transcript of the one being
   * read. Its own poll and its own state, because it is a different question
   * from `sessions` above: that one is who can be answered, this one is who is
   * running. Nothing in here can be written to. */
  live: null,
  openLive: "",
  reader: null,
  readerBusy: false,
  /* Which tool lines have been tapped open, by their absolute position in the
   * transcript, so that loading an earlier page does not silently open a
   * different set of them. */
  readerOpen: Object.create(null),
  scripts: Object.create(null),
  /* A desk is a place, not a launch menu. Work owns GitHub and hcom; Context
   * owns the checkout's Markdown. */
  openDesk: "",
  deskTab: "work",
  contexts: Object.create(null),
  contextLoading: "",
  contextErrors: Object.create(null),
  launching: Object.create(null),
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

  // Quiet, and never a guess: how many are waiting and who is behind this one.
  const queue = gateQueueLine();
  if (queue) card.appendChild(el("p", "label queue", queue));

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

/* "1 of 3, North is next", or nothing at all when only one hand is up:
 * "1 of 1" is noise, and answering the question in front of you must never be
 * a guess about how many more there are. */
function gateQueueLine() {
  const up = (state.gates || []).filter(function (g) { return g.state === "pending"; });
  if (up.length < 2) return "";
  const id = String(up[1].bot || "");
  const named = state.bots.filter(function (b) { return b.id === id; })[0];
  const who = (named && named.name) || id;
  return "1 of " + up.length + (who ? ", " + who + " is next" : ", another is next");
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

/* ── since you were last here ────────────────────────────────────────────── */

/* One line for the things that happened while nobody was looking, so the queue
 * below is a list of decisions and not also a changelog. The stamp is this
 * browser's own: it never leaves the phone and nothing on the server reads it. */

function lastSeen() {
  try {
    const at = when(window.localStorage.getItem(LAST_SEEN_KEY));
    return at ? at.getTime() : 0;
  } catch (err) {
    // Private mode throws on the read itself. A page that cannot remember
    // still draws; it just has nothing to say about "since".
    return 0;
  }
}

function markSeen() {
  try { window.localStorage.setItem(LAST_SEEN_KEY, new Date().toISOString()); }
  catch (err) { /* nothing to remember with */ }
}

/* The runner's receipts are already filtered to the ones naming an issue, so a
 * row here is a thing that happened TO something, never a sweep counting. */
function sinceThen(world, from) {
  const rows = ((world && world.automation) || {}).activity || [];
  const touched = new Set();
  let landed = 0, asked = 0;
  rows.forEach(function (row) {
    const at = when(row.at);
    if (!at || at.getTime() <= from) return;
    touched.add(row.repo + "#" + row.issue);
    if (row.outcome === "landed") landed += 1;
    if (row.outcome === "parked") asked += 1;
  });
  return { worked: touched.size, landed: landed, asked: asked };
}

function drawCatchup() {
  const band = document.getElementById("catchup");
  clear(band);
  const from = lastSeen();
  const card = el("div", "card catchup");
  card.appendChild(el("p", "when", from ? "since " + moment(new Date(from).toISOString())
                                        : "since you were last here"));

  if (from) {
    const got = sinceThen(state.world, from);
    card.appendChild(el("p", "count", got.worked + " issues worked"));
    card.appendChild(el("p", "count", got.landed + " landed"));
    card.appendChild(el("p", "count", got.asked + " asked you something"));
  }
  const waiting = queue(state.world).all.length
                + wallSections(state.world).filter(function (s) { return s.needs > 0; }).length;
  card.appendChild(el("p", "count wants", "needs you: " + waiting));

  // Tapping is the "I have seen this" gesture. Nothing else moves the stamp,
  // so opening the page in a pocket never marks the queue as read.
  card.addEventListener("click", function () {
    markSeen();
    drawCatchup();
  });
  band.hidden = false;
  band.appendChild(card);
}

/* ── needs you ───────────────────────────────────────────────────────────── */

/* The queue, in the order a person can act on it: a stated question with
 * buttons, then a fix waiting to be merged, then the parks the pipeline has not
 * turned into a question yet. The last group is still shown, because a park
 * nobody can see is a park nobody answers. */
function queue(world) {
  const decisions = [], landed = [], parks = [];
  ((world && world.stations) || []).forEach(function (desk) {
    (desk.issues || []).filter(needsHuman).forEach(function (issue) {
      const row = { repo: desk.repo, issue: issue };
      if (issue.decision && (issue.decision.options || []).length) decisions.push(row);
      else if (issue.landed_pr) landed.push(row);
      else parks.push(row);
    });
  });
  [decisions, landed, parks].forEach(function (group) { group.sort(newestFirst); });
  return { decisions: decisions, landed: landed, parks: parks,
           all: decisions.concat(landed, parks) };
}

function newestFirst(a, b) {
  const left = when(a.issue.updatedAt), right = when(b.issue.updatedAt);
  return (right ? right.getTime() : 0) - (left ? left.getTime() : 0);
}

function waitingIssues(world) {
  return queue(world).all;
}

function draftKey(repo, number) {
  return repo + "#" + number;
}

/* The two lines every card in this band starts with: which issue, on which
 * desk, last touched when. */
function issueHead(card, repo, issue) {
  const top = el("div", "head");
  top.appendChild(el("span", "num", "#" + issue.number));
  top.appendChild(el("span", "title", issue.title || ""));
  card.appendChild(top);
  card.appendChild(el("p", "where", repo + " · " + stamp(issue.updatedAt)));
}

/* The comment button and, when it is open, the box. Shared by every card here:
 * whatever else a card offers, typing a sentence is always still allowed. */
function commentToggle(acts, key, busy) {
  const open = state.commenting[key] === true;
  const talk = button(open ? "cancel" : "comment", null, function () {
    state.commenting[key] = !open;
    if (open) delete state.drafts[key];
    drawNeeds();
  });
  talk.disabled = busy;
  acts.appendChild(talk);
}

function commentBox(card, key, repo, number, busy) {
  if (state.commenting[key] !== true) return;
  const box = el("textarea");
  box.value = state.drafts[key] || "";
  box.placeholder = "Answer it. A reply without the bot's marker is what re-queues it.";
  box.addEventListener("input", function () { state.drafts[key] = box.value; });
  card.appendChild(box);

  const send = el("div", "acts");
  const go = button("send comment", "send", function () {
    decide(key, "comment", repo, number, box.value);
  });
  go.disabled = busy;
  send.appendChild(go);
  card.appendChild(send);
}

/* A stated question, as buttons. The recommended option is first and says so,
 * and every consequence is on screen: an option whose cost is behind a tap is
 * an option nobody read before they tapped it. */
function decisionCard(repo, issue) {
  const key = draftKey(repo, issue.number);
  const busy = state.busy[key] === true;
  const card = el("div", "card issue decision");
  issueHead(card, repo, issue);
  card.appendChild(el("p", "ask", issue.decision.question));

  const options = (issue.decision.options || []).slice().sort(function (a, b) {
    if (a.recommended !== b.recommended) return a.recommended ? -1 : 1;
    return a.n - b.n;
  });
  const grid = el("div", "options");
  options.forEach(function (option) {
    const b = el("button", "option" + (option.recommended ? " best" : ""));
    b.type = "button";
    b.disabled = busy;
    const pick = el("span", "pick", option.n + ". " + option.label);
    if (option.recommended) pick.appendChild(el("span", "tag", "recommended"));
    b.appendChild(pick);
    if (option.consequence) b.appendChild(el("span", "why", option.consequence));
    b.addEventListener("click", function () {
      decide(key, "choose", repo, issue.number, null,
             { n: option.n, label: option.label });
    });
    grid.appendChild(b);
  });
  card.appendChild(grid);

  const acts = el("div", "acts");
  commentToggle(acts, key, busy);
  card.appendChild(acts);
  commentBox(card, key, repo, issue.number, busy);
  return card;
}

/* A fix that is already written. The one button that matters is merge, and the
 * door re-reads the PR before it touches anything, so this is a request and
 * never a permission. */
function landedCard(repo, issue) {
  const key = draftKey(repo, issue.number);
  const busy = state.busy[key] === true;
  const card = el("div", "card issue landed");
  issueHead(card, repo, issue);
  // The runner writes markdown; this page draws text. Two asterisks read as
  // two asterisks on a phone, which is the runner's shouting leaking through.
  const word = line(String(issue.last_word || "").replace(/\*\*/g, ""), 200);
  if (word) card.appendChild(el("p", "word", word));

  const acts = el("div", "acts");
  const merge = button("merge PR #" + issue.landed_pr, "send", function () {
    decide(key, "merge", repo, issue.number, null, { pr: String(issue.landed_pr) });
  });
  merge.disabled = busy;
  acts.appendChild(merge);
  const shut = button("close issue", null, function () {
    decide(key, "close", repo, issue.number, null);
  });
  shut.disabled = busy;
  acts.appendChild(shut);
  commentToggle(acts, key, busy);
  card.appendChild(acts);
  commentBox(card, key, repo, issue.number, busy);
  return card;
}

/* A park with no question in it yet. Dim on purpose: there is nothing to decide
 * until the pipeline states the choice. Never hidden, because a park the office
 * does not draw is one nobody nudges. */
function parkCard(repo, rows) {
  const card = el("div", "card park");
  const top = el("div", "head");
  top.appendChild(el("span", "title", repo));
  top.appendChild(el("span", "badge", String(rows.length)));
  card.appendChild(top);
  card.appendChild(el("p", "under",
    rows.length + (rows.length === 1 ? " issue is" : " issues are")
    + " waiting for the pipeline to state the question"));
  rows.slice(0, PARK_ISSUES_SHOWN).forEach(function (row) {
    card.appendChild(parkLine(repo, row.issue));
  });
  if (rows.length > PARK_ISSUES_SHOWN) {
    card.appendChild(el("p", "empty",
      (rows.length - PARK_ISSUES_SHOWN) + " more on this desk, in its own pane"));
  }
  return card;
}

function parkLine(repo, issue) {
  const key = draftKey(repo, issue.number);
  const busy = state.busy[key] === true;
  const wrap = el("div", "parkline");
  const top = el("div", "head");
  top.appendChild(el("span", "num", "#" + issue.number));
  top.appendChild(el("span", "title", line(issue.title || "", 70)));
  wrap.appendChild(top);
  const acts = el("div", "acts");
  commentToggle(acts, key, busy);
  const nudge = button("nudge", null, function () {
    decide(key, "nudge", repo, issue.number, null);
  });
  nudge.disabled = busy;
  acts.appendChild(nudge);
  wrap.appendChild(acts);
  commentBox(wrap, key, repo, issue.number, busy);
  return wrap;
}

async function decide(key, kind, repo, number, body, extra) {
  if (kind === "comment" && !String(body || "").trim()) return;
  state.busy[key] = true;
  drawNeeds();
  const payload = { kind: kind, repo: repo, issue: String(number) };
  if (kind === "comment") payload.body = body;
  // A choice sends a number and a label; the door writes the sentence. A merge
  // sends the PR the runner named. Neither is free text from this page.
  if (extra) Object.keys(extra).forEach(function (k) { payload[k] = extra[k]; });
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
  const q = queue(state.world);
  const wanted = wallSections(state.world).filter(function (s) { return s.needs > 0; });
  clear(band);
  if (!q.all.length && !wanted.length) {
    band.hidden = true;
    return;
  }
  band.hidden = false;
  band.appendChild(head("needs you", String(q.all.length + wanted.length), true));

  // Every stated question, uncapped. This band exists for exactly these, and a
  // question cut off by a limit is a question nobody answers.
  q.decisions.forEach(function (row) {
    band.appendChild(decisionCard(row.repo, row.issue));
  });
  q.landed.forEach(function (row) {
    band.appendChild(landedCard(row.repo, row.issue));
  });
  drawParks(band, q.parks);

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

function drawParks(band, parks) {
  if (!parks.length) return;
  const byRepo = new Map();
  parks.forEach(function (row) {
    if (!byRepo.has(row.repo)) byRepo.set(row.repo, []);
    byRepo.get(row.repo).push(row);
  });
  const desks = Array.from(byRepo.keys());
  desks.slice(0, PARK_DESKS_SHOWN).forEach(function (repo) {
    band.appendChild(parkCard(repo, byRepo.get(repo)));
  });
  if (desks.length > PARK_DESKS_SHOWN) {
    // Counted, never dropped in silence. The desks band below still shows every
    // one of these repos as waiting on you.
    const rest = desks.slice(PARK_DESKS_SHOWN);
    let issues = 0;
    rest.forEach(function (repo) { issues += byRepo.get(repo).length; });
    band.appendChild(el("p", "empty", issues + " more waiting on you, on "
      + rest.length + (rest.length === 1 ? " desk" : " desks") + " further down"));
  }
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
  const row = el("button", "row");
  row.type = "button";
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
  row.addEventListener("click", function () { openDesk(desk.repo); });
  return row;
}

function deskLaunchers(desk) {
  const wrap = el("div", "deskstart");
  wrap.appendChild(el("span", "under", "run here"));
  const active = state.launching[desk.repo] || "";
  ["claude", "codex"].forEach(function (tool) {
    const label = active === tool ? "starting " + tool : tool;
    const launch = button(label, "chipout", function () {
      startDeskSession(desk.repo, tool);
    });
    launch.disabled = Boolean(active);
    wrap.appendChild(launch);
  });
  return wrap;
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
    const wrap = el("div", "awaydesk");
    wrap.appendChild(deskRow(desk));
    wrap.appendChild(button("bring back", "bringback", function () { bringBack(desk.repo); }));
    box.appendChild(wrap);
  });
  band.appendChild(box);
}

async function startDeskSession(repo, tool) {
  if (state.launching[repo]) return;
  state.launching[repo] = tool;
  drawDesk();
  let got = null;
  try {
    got = await write("/api/session/start", { tool: tool, repo: repo });
  } catch (err) {
    delete state.launching[repo];
    say("the office could not start " + tool);
    drawDesk();
    return;
  }
  delete state.launching[repo];
  if (got.code === 200) {
    say(tool + " started at " + repo);
    await pollSessions();
  } else {
    say(got.body.error || ("the door said " + got.code));
  }
  drawDesk();
}

function deskByRepo(repo) {
  return ((state.world && state.world.stations) || []).filter(function (desk) {
    return desk.repo === repo;
  })[0] || null;
}

function openDesk(repo) {
  state.openDesk = repo;
  state.deskTab = "work";
  state.openSession = "";
  drawDesk();
}

function closeDesk() {
  state.openDesk = "";
  state.openSession = "";
  drawDesk();
}

function chooseDeskTab(tab) {
  state.deskTab = tab;
  drawDesk();
  if (tab === "context" && !state.contexts[state.openDesk]
      && state.contextLoading !== state.openDesk) {
    loadDeskContext(state.openDesk, "");
  }
}

function drawDesk() {
  const panel = document.getElementById("desk");
  if (!state.openDesk) {
    panel.hidden = true;
    clear(panel);
    return;
  }
  const desk = deskByRepo(state.openDesk);
  panel.hidden = false;
  clear(panel);

  const bar = el("header", "top");
  bar.appendChild(button("back", null, closeDesk));
  bar.appendChild(el("p", "mark", state.openDesk));
  if (desk) {
    const kind = deskState(desk, state.gate);
    bar.appendChild(el("p", "stamp state c-" + kind, DESK_LABEL[kind]));
  }
  panel.appendChild(bar);

  const tabs = el("nav", "desktabs");
  [["work", "Work"], ["context", "Context"]].forEach(function (pair) {
    const tab = button(pair[1], state.deskTab === pair[0] ? "selected" : "", function () {
      chooseDeskTab(pair[0]);
    });
    tab.setAttribute("aria-pressed", state.deskTab === pair[0] ? "true" : "false");
    tabs.appendChild(tab);
  });
  panel.appendChild(tabs);

  const body = el("div", "deskbody");
  if (!desk) {
    body.appendChild(el("p", "empty", "this desk is not in the current Office snapshot"));
  } else if (state.deskTab === "context") {
    drawDeskContext(desk, body);
  } else {
    drawDeskWork(desk, body);
  }
  panel.appendChild(body);
}

function deskSection(title, count) {
  const section = el("section", "desksection");
  section.appendChild(head(title, count ? String(count) : ""));
  return section;
}

function drawDeskWork(desk, body) {
  const summary = el("div", "card deskstatus");
  const kind = deskState(desk, state.gate);
  const top = el("div", "head");
  top.appendChild(el("span", "dot s-" + kind));
  top.appendChild(el("span", "title", DESK_LABEL[kind]));
  summary.appendChild(top);
  if (desk.detail) summary.appendChild(el("p", "detail", desk.detail));
  const notice = staleNotice(desk, state.github, state.generated);
  if (notice) summary.appendChild(el("p", "notice", notice));
  summary.appendChild(deskLaunchers(desk));
  body.appendChild(summary);

  drawDeskSessions(desk, body);

  const issues = desk.issues || [];
  const issueSection = deskSection("issues", issues.length);
  if (!issues.length) issueSection.appendChild(el("p", "empty", "no open issues"));
  issues.forEach(function (issue) { issueSection.appendChild(deskIssueCard(issue)); });
  body.appendChild(issueSection);

  const prs = desk.prs || [];
  const prSection = deskSection("pull requests", prs.length);
  if (!prs.length) prSection.appendChild(el("p", "empty", "no open pull requests"));
  prs.forEach(function (pr) { prSection.appendChild(deskPrCard(pr)); });
  body.appendChild(prSection);
}

function deskIssueCard(issue) {
  const card = el("article", "card issue");
  const top = el("div", "head");
  top.appendChild(el("span", "num", "#" + issue.number));
  top.appendChild(el("span", "title", issue.title || ""));
  if (needsHuman(issue)) top.appendChild(el("span", "pill wants", "waiting on you"));
  card.appendChild(top);
  const labels = issue.labels || [];
  if (labels.length) card.appendChild(el("p", "asof", labels.join(" · ")));
  card.appendChild(detailReader("read issue", issue.body || ""));
  if (issue.url) card.appendChild(outLink(issue.url, "open on GitHub"));
  return card;
}

function deskPrCard(pr) {
  const card = el("article", "card issue");
  const top = el("div", "head");
  top.appendChild(el("span", "num", "#" + pr.number));
  top.appendChild(el("span", "title", pr.title || ""));
  if (pr.draft) top.appendChild(el("span", "pill", "draft"));
  if (pr.state) top.appendChild(el("span", "pill", String(pr.state).toLowerCase()));
  card.appendChild(top);
  const branch = [pr.head, pr.base].filter(Boolean).join(" → ");
  if (branch) card.appendChild(el("p", "asof", branch));
  card.appendChild(detailReader("read pull request", pr.body || ""));
  if (pr.url) card.appendChild(outLink(pr.url, "open on GitHub"));
  return card;
}

function detailReader(label, markdown) {
  const details = el("details", "reader");
  details.appendChild(el("summary", null, label));
  details.appendChild(markdownView(markdown));
  return details;
}

function outLink(href, label) {
  const link = el("a", "link", label);
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer";
  return link;
}

function markdownView(raw) {
  const view = el("div", "markdown");
  const lines = String(raw || "").replace(/\r\n/g, "\n").split("\n");
  let code = null;
  let paragraph = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    const node = el("p");
    inlineMarkdown(node, paragraph.join(" "));
    view.appendChild(node);
    paragraph = [];
  }

  for (let at = 0; at < lines.length; at++) {
    const lineText = lines[at];
    if (/^```/.test(lineText)) {
      flushParagraph();
      if (code) {
        view.appendChild(code);
        code = null;
      } else {
        code = el("pre");
      }
      continue;
    }
    if (code) {
      code.textContent += (code.textContent ? "\n" : "") + lineText;
      continue;
    }
    if (!lineText.trim()) {
      flushParagraph();
      continue;
    }
    const heading = lineText.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      const title = el("h" + Math.min(heading[1].length + 1, 6));
      inlineMarkdown(title, heading[2]);
      view.appendChild(title);
      continue;
    }
    const item = lineText.match(/^\s*(?:[-*+]|\d+\.)\s+(.*)$/);
    if (item) {
      flushParagraph();
      const bullet = el("p", "mditem");
      bullet.appendChild(document.createTextNode("• "));
      inlineMarkdown(bullet, item[1]);
      view.appendChild(bullet);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(lineText)) {
      flushParagraph();
      const table = el("pre", "mdtable");
      const rows = [];
      while (at < lines.length && /^\s*\|.*\|\s*$/.test(lines[at])) {
        rows.push(lines[at].trim());
        at++;
      }
      at--;
      table.textContent = rows.join("\n");
      view.appendChild(table);
      continue;
    }
    paragraph.push(lineText.trim());
  }
  flushParagraph();
  if (code) view.appendChild(code);
  return view;
}

function inlineMarkdown(node, raw) {
  const text = String(raw || "");
  const token = /(\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)/g;
  let from = 0;
  let match = null;
  while ((match = token.exec(text)) !== null) {
    if (match.index > from) node.appendChild(document.createTextNode(text.slice(from, match.index)));
    if (match[2]) {
      const href = String(match[3] || "");
      const link = el("a", null, match[2]);
      if (/^(?:https?:\/\/|#)/i.test(href)) {
        link.href = href;
        if (href.charAt(0) !== "#") {
          link.target = "_blank";
          link.rel = "noreferrer";
        }
      }
      node.appendChild(link);
    } else if (match[4]) {
      node.appendChild(el("strong", null, match[4]));
    } else if (match[5]) {
      node.appendChild(el("code", null, match[5]));
    } else {
      node.appendChild(el("em", null, match[6]));
    }
    from = token.lastIndex;
  }
  if (from < text.length) node.appendChild(document.createTextNode(text.slice(from)));
}

function drawDeskContext(desk, body) {
  const context = state.contexts[desk.repo] || null;
  const error = state.contextErrors[desk.repo] || "";
  if (error) body.appendChild(el("p", "notice", error));
  if (!context) {
    body.appendChild(el("p", "empty", state.contextLoading === desk.repo
      ? "opening this checkout" : "no context loaded"));
    return;
  }

  if (context.path) {
    const documentSection = deskSection(context.title || context.path, "");
    documentSection.appendChild(el("p", "asof", context.path));
    documentSection.appendChild(markdownView(context.text));
    body.appendChild(documentSection);
  }

  const files = context.files || [];
  const index = deskSection("Markdown", files.length);
  index.appendChild(button("refresh", "chipout", function () {
    loadDeskContext(desk.repo, "");
  }));
  if (context.capped) index.appendChild(el("p", "notice", "this checkout has more Markdown than the Office index can show"));
  if (!files.length) index.appendChild(el("p", "empty", "no Markdown in this checkout"));
  const groups = Object.create(null);
  files.forEach(function (file) {
    const group = file.group || "root";
    if (!groups[group]) groups[group] = [];
    groups[group].push(file);
  });
  Object.keys(groups).forEach(function (group) {
    const box = el("details", "contextgroup");
    if (group === "root" || groups[group].some(function (file) { return file.path === context.path; })) {
      box.open = true;
    }
    box.appendChild(el("summary", null, group));
    groups[group].forEach(function (file) {
      const row = button(file.name, file.path === context.path ? "contextfile selected" : "contextfile", function () {
        loadDeskContext(desk.repo, file.path);
      });
      row.appendChild(el("span", "asof", readable(file.bytes || 0)));
      box.appendChild(row);
    });
    index.appendChild(box);
  });
  body.appendChild(index);
}

async function loadDeskContext(repo, path) {
  state.contextLoading = repo;
  delete state.contextErrors[repo];
  drawDesk();
  let target = "/api/context?repo=" + encodeURIComponent(repo);
  if (path) target += "&path=" + encodeURIComponent(path);
  const got = await read(target);
  state.contextLoading = "";
  if (got.code !== 200) {
    state.contextErrors[repo] = got.body.error || ("the door said " + got.code);
    drawDesk();
    return;
  }
  const existing = state.contexts[repo] || null;
  if (path && existing && (!got.body.files || !got.body.files.length)) {
    got.body.files = existing.files || [];
    got.body.root = got.body.root || existing.root || "";
    got.body.capped = !!existing.capped;
  }
  state.contexts[repo] = got.body;
  if (!path && got.body.files && got.body.files.length) {
    loadDeskContext(repo, got.body.files[0].path);
    return;
  }
  drawDesk();
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

/* ── the automation, as one band ─────────────────────────────────────────── */

/* The phone's answer to "there is an hourly cron; show me how it works, when it
 * is processing, which issues, and links to the comments".
 *
 * Every word here was measured by the server and arrives inside the world
 * snapshot. Nothing on this page derives a state, picks a headline or decides
 * whether a number is bad: the Mac app draws the same fields, and two renderers
 * each deciding "is this fine" is two places for it to go wrong, in two
 * languages, that drift the first time a state is added.
 */
function drawAutomation() {
  const band = document.getElementById("automation");
  clear(band);
  const page = (state.world && state.world.automation) || null;
  if (!page || !page.headline) {
    band.hidden = true;
    return;
  }
  band.hidden = false;

  const hurt = automationNeedsSomebody(page);
  band.appendChild(head("automation", hurt ? "needs you" : "", hurt));

  const card = el("div", "card");
  card.appendChild(el("p", "headline" + (hurt ? " off" : ""), page.headline));

  const sched = page.schedule || {};
  const now = page.now || {};
  const reach = page.reached || {};
  const rows = el("div", "facts");
  rows.appendChild(fact("schedule", automationSchedule(sched), sched.overdue ? "warn" : "plain"));
  rows.appendChild(fact("last full sweep", automationSweep(sched, reach),
                        reach.repos ? "dim" : "warn"));
  rows.appendChild(fact("doing",
                        now.running ? (now.doing || "a run is in flight")
                                    : (now.last_said ? "last: " + now.last_said : "nothing"),
                        now.running ? "ok" : "dim"));
  if (sched.deferring) rows.appendChild(fact("power", "on battery, deferring every run", "warn"));
  card.appendChild(rows);
  card.appendChild(automationDelivery(page.delivery || {}));

  /* The webhook path, and above all WHY nothing is arriving when nothing is.
   * "Quiet" and "nothing can reach us" read identically from inside a quiet
   * room, and the second one lasts for weeks. */
  const trig = page.trigger || {};
  const blocked = String(trig.blocked_by || "");
  const hook = el("div", "card sub");
  const hookHead = el("div", "head");
  hookHead.appendChild(el("span", "title", "webhooks"));
  hookHead.appendChild(el("span", "pill" + (blocked ? " wants" : ""), trig.state || "unknown"));
  if (trig.queued > 0) hookHead.appendChild(el("span", "pill", trig.queued + " queued"));
  hook.appendChild(hookHead);
  hook.appendChild(el("p", blocked ? "headline off" : "detail",
                      blocked || automationTriggerLine(trig)));
  card.appendChild(hook);

  band.appendChild(card);
  band.appendChild(automationActivity(page));
}

function automationDelivery(delivery) {
  const wrap = el("div", "card sub");
  const bar = el("div", "head");
  bar.appendChild(el("span", "title", "delivery conveyor"));
  bar.appendChild(el("span", "pill" + (delivery.pipeline_health !== "ok" ? " wants" : ""),
                          delivery.pipeline_health || "unknown"));
  wrap.appendChild(bar);
  [["running now", delivery.running_now], ["next up", delivery.next_up],
   ["blocked", delivery.blocked], ["completed recently", delivery.completed_recently]]
    .forEach(function (group) {
      (group[1] || []).forEach(function (row) {
        const detail = ((row.problems || [])[0]) || row.next || row.phase || "review";
        wrap.appendChild(el("p", "detail", group[0] + ": " + row.repo + "#" + row.pr + " · " + detail));
      });
    });
  return wrap;
}

function automationNeedsSomebody(page) {
  const sched = page.schedule || {};
  const now = page.now || {};
  return Boolean(sched.overdue || sched.deferring || now.stale_pid
                 || (page.trigger && page.trigger.blocked_by)
                 || (page.delivery && page.delivery.pipeline_health !== "ok")
                 || (page.delivery && page.delivery.blocked && page.delivery.blocked.length)
                 || page.state === "unreadable");
}

function automationSchedule(sched) {
  const parts = [];
  if (sched.every) parts.push("every " + sched.every);
  if (sched.overdue) parts.push("overdue by " + (sched.late_by || "a while"));
  else if (sched.next_in) parts.push("next look " + sched.next_in);
  return parts.join(", ") || "unknown";
}

function automationSweep(sched, reach) {
  if (reach.repos === null || reach.repos === undefined) {
    return "receipts " + (reach.state || "unknown");
  }
  const said = moment(sched.last_full_run) || "never";
  return said + ", " + reach.repos + " repos in " + (reach.window || "24h");
}

function automationTriggerLine(trig) {
  const today = trig.today || 0;
  if (trig.last_age_s === null || trig.last_age_s === undefined) {
    return today + " today; nothing has ever arrived";
  }
  return today + " today, last " + waited(trig.last_age_s) + " ago, "
         + (trig.runs_today || 0) + " runs";
}

/* What the runner touched, and the way to what it said about it.
 *
 * The label on each link says WHICH link it is. "read the comment" when the
 * office knows exactly where the runner's words are; "open the issue" when a
 * human has replied since, which moves the last comment and would make a deep
 * link point at somebody else's words wearing the runner's label. */
function automationActivity(page) {
  const wrap = el("div", "card");
  const bar = el("div", "head");
  bar.appendChild(el("span", "title", "what it touched"));
  /* Never a silent cap: a list that quietly stops reads as "that is everything
   * that happened". */
  if (page.activity_dropped > 0) {
    bar.appendChild(el("span", "count",
                       "newest " + page.activity.length + " of "
                       + (page.activity.length + page.activity_dropped)));
  }
  wrap.appendChild(bar);

  const rows = page.activity || [];
  if (!rows.length) {
    wrap.appendChild(el("p", "empty",
      "no issue touched in the last day. The sweeps that only counted open "
      + "issues are not listed here."));
    return wrap;
  }
  rows.forEach(function (row) {
    const item = el("div", "run");
    const top = el("div", "head");
    top.appendChild(el("span", "title", row.repo + "#" + row.issue));
    const raw = String(row.tone || "").trim().toLowerCase();
    top.appendChild(el("span", "pill t-" + (TONES.indexOf(raw) >= 0 ? raw : "plain"),
                       row.outcome || ""));
    top.appendChild(el("span", "count", row.ago || ""));
    item.appendChild(top);
    if (row.title) item.appendChild(el("p", "detail", row.title));
    item.appendChild(el("p", "asof", row.detail || row.means || ""));
    const href = row.comment_url || row.issue_url;
    if (href) {
      const link = el("a", "link", row.comment_url ? "read the comment" : "open the issue");
      link.href = href;
      link.target = "_blank";
      link.rel = "noreferrer";
      item.appendChild(link);
    }
    wrap.appendChild(item);
  });
  return wrap;
}

function fact(label, value, tone) {
  const row = el("div", "fact");
  row.appendChild(el("span", "label", label));
  row.appendChild(el("span", "value t-" + (tone || "plain"), String(value)));
  return row;
}

/* ── the agents running on this machine ──────────────────────────────────── */

/* Read and answer a live Claude Code or Codex session without opening a
 * terminal.
 *
 * A reply is a MESSAGE, never a keystroke. It lands in the agent's queue and it
 * reads it at its next hook. Nothing on this page types into a live terminal:
 * a message arriving mid-prompt would be submitted into whatever was
 * half-typed there.
 *
 * The office cannot see a session that never joined hcom, and says so. `canSee`
 * false means "we do not know", which draws as its own sentence rather than as
 * an empty list, because an empty list is a claim that nothing is running. */
/* ── the roster, above every desk ────────────────────────────────────────── */

/* The same roster the desks draw from, drawn once for the whole machine.
 *
 * A session belongs to a desk; the question "what is running right now" does
 * not, and answering it only inside one desk's Work tab means it is never
 * answered at all. So this band is the top-level list: every agent, grouped by
 * the repo it sits in, with its tool, its status and how long it has been up.
 *
 * READ ONLY: tapping a row opens that repo's desk, where the composer already
 * lives. Nothing is sent from here, so nothing here can send to the wrong
 * session.
 *
 * The band never empties itself on a failed poll. The last roster stays and the
 * head says it is not answering and how old the picture is. */

const AGENT_ORDER = { blocked: 0, active: 1, listening: 2 };

function agentRank(session) {
  const known = AGENT_ORDER[session.status];
  return known === undefined ? 3 : known;
}

/* Grouped by repo, repos in the order their first agent appears sorted by name,
 * so fourteen rows read as three short lists rather than one long one. */
function agentGroups(rows) {
  const byRepo = Object.create(null);
  const order = [];
  rows.slice().sort(function (a, b) {
    return agentRank(a) - agentRank(b)
      || String(a.name).localeCompare(String(b.name));
  }).forEach(function (session) {
    const key = session.repo || session.directory || "no repo";
    if (!byRepo[key]) {
      byRepo[key] = [];
      order.push(key);
    }
    byRepo[key].push(session);
  });
  return order.map(function (key) { return { repo: key, rows: byRepo[key] }; });
}

function drawAgents() {
  const band = document.getElementById("agents");
  const roster = state.sessions;
  clear(band);
  if (!roster) {
    band.hidden = true;
    return;
  }
  band.hidden = false;
  const rows = roster.sessions || [];
  const wants = rows.filter(function (s) { return s.status === "blocked"; }).length;
  band.appendChild(head("agents", String(rows.length), false));
  if (wants) {
    band.appendChild(el("p", "agentnotice", wants + " waiting on you"));
  }
  /* Two different failures, both of which must show as a state rather than as
   * an empty list: the door answered badly, or this page could not reach it. */
  if (roster.state !== "ok" && roster.state !== "empty") {
    band.appendChild(el("p", "empty",
      roster.detail || "the office cannot see the sessions on this machine right now"));
  } else if (state.sessionsError) {
    band.appendChild(el("p", "empty",
      state.sessionsError + " · showing the last roster, read "
      + (since(state.sessionsAt) || "a while") + " ago"));
  }
  if (!rows.length) {
    if (roster.state === "ok" || roster.state === "empty") {
      band.appendChild(el("p", "empty", "no agents running on this machine"));
    }
    return;
  }
  agentGroups(rows).forEach(function (group) {
    band.appendChild(el("p", "agentrepo", group.repo));
    group.rows.forEach(function (session) {
      band.appendChild(agentRow(session));
    });
  });
}

function agentRow(session) {
  const row = el("div", "row agentrow");
  row.appendChild(el("span", "dot m-" + sessionMood(session)));
  const body = el("div", "body");

  const top = el("div", "head");
  top.appendChild(el("span", "name", session.name));
  if (session.tool) top.appendChild(el("span", "pill", session.tool));
  if (session.status === "blocked") top.appendChild(el("span", "pill wants", "waiting on you"));
  /* Not reachable is not the same as not running, and the row says which. */
  if (!session.reachable) top.appendChild(el("span", "pill", "cannot be answered"));
  if (session.unread > 0) top.appendChild(el("span", "pill", session.unread + " unread"));
  body.appendChild(top);
  body.appendChild(el("p", "under", session.doing || session.status));
  row.appendChild(body);

  /* How long it has been up, from its own start time. Never a guess: a session
   * the door gave no start time for gets no age rather than a made-up one. */
  const up = since(session.started_at);
  if (up) row.appendChild(el("span", "asof", up === "now" ? "just started" : "up " + up));

  row.addEventListener("click", function () {
    if (!deskByRepo(session.repo)) {
      say(session.name + " is at " + (session.repo || session.directory)
          + ", which has no desk here");
      return;
    }
    openDesk(session.repo);
    openSessionThread(session.name);
  });
  return row;
}

function sessionsForDesk(repo) {
  const roster = state.sessions;
  if (!roster) return [];
  return (roster.sessions || []).filter(function (session) {
    return session.repo === repo;
  });
}

function drawDeskSessions(desk, body) {
  const roster = state.sessions;
  const rows = sessionsForDesk(desk.repo);
  const section = deskSection("sessions", rows.length);
  if (roster && roster.state !== "ok" && roster.state !== "empty") {
    section.appendChild(el("p", "empty",
      roster.detail || "the office cannot see the sessions on this machine right now"));
  } else if (!rows.length) {
    section.appendChild(el("p", "empty", "nothing running at this desk"));
  } else {
    rows.forEach(function (session) { section.appendChild(sessionCard(session)); });
  }
  body.appendChild(section);
}

function sessionCard(session) {
  const open = state.openSession === session.name;
  const card = el("div", "card");

  const top = el("div", "head");
  top.appendChild(el("span", "dot m-" + sessionMood(session)));
  top.appendChild(el("span", "title", session.name));
  if (session.tool) top.appendChild(el("span", "pill", session.tool));
  if (session.status === "blocked") top.appendChild(el("span", "pill wants", "waiting on you"));
  if (session.unread > 0) top.appendChild(el("span", "pill", session.unread + " unread"));
  card.appendChild(top);

  card.appendChild(el("p", "detail", session.repo || session.directory || ""));
  card.appendChild(el("p", "asof", session.doing || session.status));

  card.appendChild(button(open ? "hide" : "open", "chipout", function () {
    if (open) {
      state.openSession = "";
      drawDesk();
    } else {
      openSessionThread(session.name);
    }
  }));

  if (open) {
    const script = state.scripts[session.name];
    const turns = (script && script.exchanges) || [];
    if (turns.length) {
      const thread = el("div", "card sub");
      turns.slice(-SESSION_TURNS).forEach(function (turn) {
        if (turn.you) thread.appendChild(el("p", "headline", turn.you));
        if (turn.them) thread.appendChild(el("p", "detail", turn.them));
      });
      card.appendChild(thread);
    } else if (script) {
      card.appendChild(el("p", "empty", "nothing said yet"));
    }
    card.appendChild(sessionComposer(session));
  }
  return card;
}

/* The page's own three-word mood vocabulary, not a fourth one invented here:
 * `needs` is amber and means a person, `quiet` is working, `off` is not. */
function sessionMood(session) {
  if (session.status === "blocked") return "needs";
  if (session.status === "active" || session.status === "listening") return "quiet";
  return "off";
}

/* The box. Absent, with the reason written instead, when the agent would never
 * read what was typed: a send button over a dead session is a button that lies
 * about where the words went. */
function sessionComposer(session) {
  const key = "session:" + session.name;
  const wrap = el("div", "composer");
  if (!session.reachable) {
    wrap.appendChild(el("p", "empty",
      session.name + " is " + session.status + ", so it would never read this"));
    return wrap;
  }
  const box = el("textarea", "");
  box.rows = 2;
  box.placeholder = "answer " + session.name;
  box.value = state.drafts[key] || "";
  box.addEventListener("input", function () { state.drafts[key] = box.value; });
  wrap.appendChild(box);
  wrap.appendChild(button("send", "primary", function () {
    replyToSession(session.name, state.drafts[key] || "");
  }));
  return wrap;
}

function openSessionThread(name) {
  state.openSession = name;
  drawDesk();
  loadSessionScript(name);
}

async function loadSessionScript(name) {
  const got = await read("/api/session?name=" + encodeURIComponent(name));
  if (got.code === 200) {
    state.scripts[name] = got.body;
    drawDesk();
  } else if (!state.scripts[name]) {
    /* Whatever was last read stays on screen. A conversation that empties
     * itself because hcom blinked is a lie about what was said. */
    say(got.body.error || "could not read that session");
  }
}

async function replyToSession(name, text) {
  const words = String(text || "").trim();
  if (!words) return;
  const key = "session:" + name;
  state.drafts[key] = "";
  drawDesk();
  const got = await write("/api/session/say", { name: name, text: words });
  if (got.code === 200) {
    say("sent to " + name);
    await loadSessionScript(name);
    await pollSessions();
  } else {
    /* Put the words back in the box. A refused message that also vanished is a
     * message a person has to retype from memory. */
    state.drafts[key] = words;
    say(got.body.error || ("the door said " + got.code));
    drawDesk();
  }
}

async function pollSessions() {
  try {
    const got = await read("/api/sessions");
    if (got.code === 200) {
      state.sessions = got.body;
      state.sessionsAt = new Date().toISOString();
      state.sessionsError = "";
      if (state.openDesk && state.deskTab === "work") drawDesk();
    } else {
      state.sessionsError = "the door said " + got.code;
    }
  } catch (err) {
    /* The last roster stays. The office not being able to ask is not the same
     * as nothing running, and it must not empty a list a person is reading. So
     * the failure is written into the band as a state instead. */
    state.sessionsError = "the office could not reach the door";
  }
  drawAgents();
}

/* ── a picture, made small enough to send ────────────────────────────────── */

/* Bytes to base64 is four characters for every three, rounded up. Checking the
 * raw length instead is how a payload gets a third bigger than the ceiling it
 * was measured against. */
function base64Length(bytes) {
  return Math.ceil(bytes / 3) * 4;
}

function readable(bytes) {
  if (bytes < 1024) return bytes + " bytes";
  const kb = bytes / 1024;
  if (kb < 1000) return Math.round(kb) + " KB";
  return (kb / 1024).toFixed(1) + " MB";
}

function asBlob(canvas, quality) {
  return new Promise(function (done) {
    canvas.toBlob(function (blob) { done(blob); }, "image/jpeg", quality);
  });
}

/* FileReader rather than fetch, because the answer wanted is base64 and this is
 * the one API that hands it over without a round trip through a URL. The prefix
 * comes off: the door takes strict base64 and nothing else. */
function asBase64(blob) {
  return new Promise(function (done, fail) {
    const reader = new FileReader();
    reader.onload = function () {
      const text = String(reader.result || "");
      const comma = text.indexOf(",");
      done(comma >= 0 ? text.slice(comma + 1) : "");
    };
    reader.onerror = function () { fail(new Error("unreadable")); };
    reader.readAsDataURL(blob);
  });
}

/* (photo, why not). Exactly one is filled in.
 *
 * `budget` is what the base64 may weigh once the message going with it has had
 * its share, because the door measures the whole request and not the picture. */
async function shrink(file, budget) {
  let bitmap = null;
  try {
    bitmap = await createImageBitmap(file);
  } catch (err) {
    // A HEIC off the camera roll lands here, and so does anything else no
    // browser can decode. Saying so is the whole point: a picture that silently
    // did not go is worse than one that refused out loud.
    return { photo: null, why: CANNOT_READ };
  }
  const room = Math.max(4096, budget);
  const canvas = document.createElement("canvas");
  const pen = canvas.getContext("2d");
  for (let i = 0; i < PHOTO_LADDER.length; i++) {
    const rung = PHOTO_LADDER[i];
    const longest = Math.max(bitmap.width, bitmap.height) || 1;
    const scale = Math.min(1, rung.side / longest);
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    // A JPEG has no alpha, so a transparent PNG turned into one picks a
    // background whether or not anybody chose it. White is the one a person
    // would have chosen.
    pen.fillStyle = "#ffffff";
    pen.fillRect(0, 0, canvas.width, canvas.height);
    pen.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await asBlob(canvas, rung.quality);
    if (!blob) continue;
    if (base64Length(blob.size) > room) continue;
    let data = "";
    try {
      data = await asBase64(blob);
    } catch (err) {
      return { photo: null, why: "that picture could not be read" };
    }
    if (!data || data.length > room) continue;
    return {
      photo: {
        name: photoName(file && file.name),
        mime_type: "image/jpeg",
        data_base64: data,
        bytes: blob.size,
        width: canvas.width,
        height: canvas.height,
      },
      why: "",
    };
  }
  return { photo: null, why: "that picture will not fit, even shrunk" };
}

/* A file name is something typed on some other machine and it ends up in a JSON
 * body. What is left is a plain name with the extension of what was actually
 * encoded, and something with no name left is called photo. */
function photoName(raw) {
  const stem = String(raw || "").split("/").pop().split("\\").pop();
  const dot = stem.lastIndexOf(".");
  const dropped = dot > 0 ? stem.slice(0, dot) : stem;
  const kept = dropped.replace(/[^A-Za-z0-9 _-]/g, "").trim().slice(0, 64);
  return (kept || "photo") + ".jpg";
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
  const cadence = bot.frequency ? bot.frequency + " · " : "";
  bar.appendChild(el("p", "stamp", bot.busy ? "working" : cadence + (bot.purpose || "")));
  panel.appendChild(bar);

  const turns = el("div", "turns");
  if (state.threadError) turns.appendChild(el("p", "empty", state.threadError));
  if (!state.turns.length && !state.threadError) {
    if (bot.purpose) turns.appendChild(el("p", "empty", bot.purpose));
    turns.appendChild(el("p", "empty", "Message " + bot.name + " to start."));
  }
  state.turns.forEach(function (turn) {
    const mine = turn.role === "user";
    const words = turn.content || turn.text || "";
    const photo = carriedPhoto(turn, bot.id);
    if (words || !photo) {
      turns.appendChild(el("p", "bubble " + (mine ? "me" : "them"), words));
    }
    // The picture itself is gone. The office carried the bytes and nothing
    // wrote them down, so this says a photo went and stops there rather than
    // offering to show one it cannot produce.
    if (photo) {
      turns.appendChild(el("p", "photomark " + (mine ? "me" : "them"), "with a photo"));
    }
  });
  if (bot.busy) turns.appendChild(el("p", "working", bot.name + " is working"));
  panel.appendChild(turns);

  // A picture picked and not yet sent, above the box: it has a name, a size
  // after the shrink and a way out, and all three have to be legible before a
  // send that cannot be taken back.
  const picked = state.photos[key];
  if (picked) {
    const chip = el("div", "chip");
    chip.appendChild(el("span", "chipname", picked.name));
    chip.appendChild(el("span", "chipsize", readable(picked.bytes) + " after downscale"));
    chip.appendChild(button("remove", "chipout", function () {
      delete state.photos[key];
      drawThread();
    }));
    panel.appendChild(chip);
  }

  const composer = el("div", "composer");
  // Created here rather than sitting in the HTML: the page has one file input
  // and it belongs to whichever thread is open. `accept="image/*"` is what puts
  // the photo library and the camera in the same sheet on a phone.
  const library = photoPicker(key, false);
  const camera = photoPicker(key, true);
  composer.appendChild(library);
  composer.appendChild(camera);
  composer.appendChild(button("photo", "attach", function () { library.click(); }));
  composer.appendChild(button("camera", "attach", function () { camera.click(); }));
  const box = el("textarea");
  box.value = state.drafts[key] || "";
  box.placeholder = bot.busy ? "busy with a turn" : "Message " + bot.name;
  box.addEventListener("input", function () { state.drafts[key] = box.value; });
  composer.appendChild(box);
  const send = button("send", "send", function () {
    sayTo(bot.id, box.value, state.photos[key]);
  });
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

/* One picture at a time, hidden, and rebuilt with the panel. `capture` is what
 * separates the two buttons: without it the sheet offers the photo library, with
 * it the camera opens straight away, which is the door the HEIC message points
 * a person at when the library hands over something no browser can read. */
function photoPicker(key, capture) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  if (capture) input.capture = "environment";
  input.className = "hiddenfile";
  input.addEventListener("change", function () {
    const file = input.files && input.files[0];
    input.value = "";
    if (file) takePhoto(key, file);
  });
  return input;
}

async function takePhoto(key, file) {
  const typed = new TextEncoder().encode(state.drafts[key] || "").length;
  const got = await shrink(file, PHOTO_CEILING - typed);
  if (!got.photo) {
    say(got.why);
    return;
  }
  // A second picture replaces the first rather than queueing behind it: the
  // door takes exactly one, and a queue that can only ever be one deep is a lie
  // about what is going to be sent.
  state.photos[key] = got.photo;
  drawThread();
}

/* Whether a turn carried a picture. The harness's own echo first; this page's
 * memory of what it sent only when the harness says nothing either way. */
function carriedPhoto(turn, botId) {
  if (!turn) return false;
  const listed = turn.attachments;
  if (Array.isArray(listed) && listed.length) return true;
  if (turn.has_photo === true || turn.photo === true) return true;
  if (turn.role !== "user") return false;
  const mine = state.sentPhotos[botId];
  return !!mine && mine.indexOf(String(turn.content || turn.text || "")) >= 0;
}

async function sayTo(id, message, photo) {
  // A picture on its own is a whole thing to say, so an empty box is only empty
  // when nothing is going with it.
  if (!String(message || "").trim() && !photo) return;
  const turn = { message: message, bot: id };
  if (photo) {
    turn.attachments = [{
      name: photo.name,
      mime_type: photo.mime_type,
      data_base64: photo.data_base64,
    }];
  }
  const sent = await write("/api/chat", turn);
  if (sent.code === 202) {
    delete state.drafts["bot:" + id];
    delete state.photos["bot:" + id];
    if (photo) {
      const said = String(message || "").trim();
      state.sentPhotos[id] = (state.sentPhotos[id] || []).concat([said]);
    }
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

/* ── what is running on this machine, and its transcript ─────────────────── */

/* The band above the queue, and the reader behind it.
 *
 * READ ONLY, by construction and not by convention: there is no composer in
 * this panel and no write anywhere in this section. The office can see far more
 * sessions than it can reach, and the honest way to show one it cannot answer
 * is to show it without a send button rather than to hide it.
 *
 * The band never empties itself on a failed poll. `state: unreadable` draws its
 * own sentence, because an empty list is a claim that nothing is running and
 * this page may only make that claim when the door actually said so. */

function since(iso) {
  const at = when(iso);
  if (!at) return "";
  const secs = Math.max(0, Math.round((Date.now() - at.getTime()) / 1000));
  if (secs < 60) return "now";
  if (secs < 3600) return Math.floor(secs / 60) + "m";
  if (secs < 86400) return Math.floor(secs / 3600) + "h";
  return Math.floor(secs / 86400) + "d";
}

/* A desk if it has one, otherwise the last two path parts: "wt/matra.10630.191"
 * says which worktree, where the whole path says nothing you can read on a
 * phone. */
function liveWhere(session) {
  if (session.repo) return session.repo;
  const bits = String(session.cwd || "").split("/").filter(Boolean);
  return bits.slice(-2).join("/") || "somewhere with no path";
}

function drawLive() {
  const band = document.getElementById("live");
  const roster = state.live;
  clear(band);
  if (!roster) {
    band.hidden = true;
    return;
  }
  band.hidden = false;
  const rows = roster.sessions || [];
  band.appendChild(head("running now", String(rows.length)));
  if (roster.state === "unreadable") {
    band.appendChild(el("p", "empty",
      roster.detail || "this machine could not be asked what is running"));
    return;
  }
  if (!rows.length) {
    band.appendChild(el("p", "empty", "no agents running on this machine"));
    return;
  }
  rows.forEach(function (session) { band.appendChild(liveRow(session)); });
}

function liveRow(session) {
  const row = el("div", "row liverow");
  row.appendChild(el("span", "dot " + (session.state === "working" ? "s-working" : "s-idle")));
  const body = el("div", "body");

  const top = el("div", "head");
  top.appendChild(el("span", "name", liveWhere(session)));
  top.appendChild(el("span", "pill", session.engine));
  if (session.state === "unknown") top.appendChild(el("span", "pill", "no transcript"));
  body.appendChild(top);

  if (session.title) body.appendChild(el("p", "under", session.title));
  if (session.last_line) body.appendChild(el("p", "lastline", session.last_line));
  body.appendChild(el("p", "asof", [
    session.state,
    session.turns ? session.turns + " turns" : "",
    since(session.last_activity),
  ].filter(Boolean).join(" · ")));

  row.appendChild(body);
  row.addEventListener("click", function () { openReader(session.key); });
  return row;
}

/* ── the reader ──────────────────────────────────────────────────────────── */

function openReader(key) {
  state.openLive = key;
  state.reader = null;
  state.readerOpen = Object.create(null);
  setHash("live=" + key);
  drawReader();
  loadReaderPage(-1, "replace");
}

function closeReader() {
  state.openLive = "";
  state.reader = null;
  setHash("");
  drawReader();
}

/* The deep link. The Mac app and a notification both want to open one session
 * straight from a click, and a hash is the only address this page has. */
function setHash(value) {
  const want = value ? "#" + value : "";
  if (window.location.hash === want) return;
  if (want) window.location.hash = want;
  else history.replaceState(null, "", window.location.pathname);
}

function readHash() {
  const raw = String(window.location.hash || "").replace(/^#/, "");
  if (raw.indexOf("live=") !== 0) {
    if (state.openLive) closeReader();
    return;
  }
  const key = raw.slice(5);
  if (key && key !== state.openLive) openReader(key);
}

/* One page, and only ever one in flight: a reader that fires a fetch on every
 * poll while the last one is still out would append the same lines twice. */
async function loadReaderPage(offset, how) {
  if (state.readerBusy || !state.openLive) return;
  state.readerBusy = true;
  const key = state.openLive;
  try {
    const got = await read("/api/live/transcript?key=" + encodeURIComponent(key)
                           + "&offset=" + offset + "&limit=" + READER_PAGE);
    if (key !== state.openLive) return;
    if (got.code !== 200) {
      if (!state.reader) state.reader = { key: key, lines: [], total: 0, offset: 0 };
      state.reader.error = String(got.body.error || ("the door said " + got.code));
      drawReader();
      return;
    }
    const body = got.body;
    const lines = body.lines || [];
    if (how === "replace" || !state.reader || state.reader.key !== key) {
      state.reader = { key: key, lines: lines, total: body.total || 0,
                       offset: body.offset || 0, title: body.title || "",
                       cwd: body.cwd || "", repo: body.repo || "",
                       engine: body.engine || "", state: body.state || "", error: "" };
    } else if (how === "earlier") {
      state.reader.lines = lines.concat(state.reader.lines);
      state.reader.offset = body.offset || 0;
      state.reader.total = body.total || state.reader.total;
      state.reader.error = "";
    } else {
      state.reader.lines = state.reader.lines.concat(lines);
      state.reader.total = body.total || state.reader.total;
      state.reader.state = body.state || state.reader.state;
      state.reader.error = "";
      trimReader(state.reader);
    }
    drawReader();
  } catch (err) {
    /* Whatever was read stays on the screen. */
  } finally {
    state.readerBusy = false;
  }
}

/* Only what arrived since the last page: the window this reader holds ends at
 * offset + length, so that is where the next read starts. */
function pollReader() {
  const held = state.reader;
  if (!state.openLive || !held || held.key !== state.openLive) return;
  loadReaderPage(held.offset + held.lines.length, "append");
}

/* A reader left open on a working session grows by a page every few seconds.
 * The window is capped from the front, and the offset moves with it, so "load
 * earlier" still asks for exactly what was dropped. */
const READER_WINDOW = 2000;

function trimReader(held) {
  const over = held.lines.length - READER_WINDOW;
  if (over <= 0) return;
  held.lines = held.lines.slice(over);
  held.offset += over;
}

function drawReader() {
  const panel = document.getElementById("reader");
  if (!state.openLive) {
    panel.hidden = true;
    clear(panel);
    return;
  }
  const held = state.reader;
  /* Keep the reading position. The scroller is `.turns`, not the panel: the
   * panel is a column with a fixed bar at each end. A view rebuilt under a
   * thumb that has scrolled back forty lines must not throw away what a person
   * was reading; one already at the bottom follows the session instead. */
  const old = panel.querySelector(".turns");
  const wasBottom = !old
    || old.scrollHeight - old.scrollTop - old.clientHeight < 60;
  const wasTop = old ? old.scrollTop : 0;
  const wasHeight = old ? old.scrollHeight : 0;

  panel.hidden = false;
  clear(panel);

  const bar = el("header", "top");
  bar.appendChild(button("back", null, closeReader));
  bar.appendChild(el("p", "mark", (held && (held.repo || held.cwd)) || state.openLive));
  bar.appendChild(el("p", "stamp", (held && held.engine) || ""));
  panel.appendChild(bar);

  const turns = el("div", "turns reading");
  if (held && held.title) turns.appendChild(el("p", "readertitle", held.title));
  if (!held) {
    turns.appendChild(el("p", "empty", "reading"));
  } else if (held.error) {
    turns.appendChild(el("p", "empty", held.error));
  }
  if (held && held.offset > 0) {
    turns.appendChild(button("load earlier (" + held.offset + " above)", "chipout",
      function () { loadReaderPage(Math.max(0, held.offset - READER_PAGE), "earlier"); }));
  }
  if (held) {
    held.lines.forEach(function (line, index) {
      turns.appendChild(readerLine(line, held.offset + index));
    });
    if (!held.lines.length && !held.error) {
      turns.appendChild(el("p", "empty", "nothing said yet"));
    }
  }
  panel.appendChild(turns);

  const foot = el("div", "readerfoot");
  foot.appendChild(el("p", "note",
    "read only · this office cannot answer this session"));
  foot.appendChild(button("newest", "chipout", function () {
    loadReaderPage(-1, "replace");
  }));
  panel.appendChild(foot);

  if (wasBottom) turns.scrollTop = turns.scrollHeight;
  else turns.scrollTop = wasTop + (turns.scrollHeight - wasHeight);
}

function readerLine(line, at) {
  const kind = line.kind || "text";
  if (kind === "tool" || kind === "result") return toolLine(line, at, kind);
  const who = line.who === "user" ? "user" : (line.who === "system" ? "system" : "agent");
  const block = el("div", "rline r-" + who + (kind === "thinking" ? " thinking" : ""));
  const label = kind === "thinking" ? "thinking" : who;
  block.appendChild(el("p", "rwho", label));
  block.appendChild(el("p", "rtext", line.text + (line.truncated ? " …[clipped]" : "")));
  return block;
}

/* One line, tapped open for the whole thing. The preview is the head of it,
 * because the first forty characters of a tool call are its name and its
 * target and that is what a person is scanning for. */
function toolLine(line, at, kind) {
  const open = !!state.readerOpen[at];
  const block = el("div", "rline r-" + kind + (open ? " open" : ""));
  const text = String(line.text || "");
  const shown = open ? text : text.slice(0, TOOL_PREVIEW).replace(/\s+/g, " ");
  const tap = button((kind === "tool" ? "» " : "« ") + (shown || "(empty)"), "rtool",
    function () {
      state.readerOpen[at] = !open;
      drawReader();
    });
  if (!open && text.length > TOOL_PREVIEW) tap.title = "tap to expand";
  block.appendChild(tap);
  return block;
}

async function pollLive() {
  try {
    const got = await read("/api/live");
    if (got.code !== 200) return;
    state.live = got.body;
    drawLive();
  } catch (err) {
    /* The last roster stays up. */
  }
}

/* ── the polls ───────────────────────────────────────────────────────────── */

async function pollGate() {
  try {
    // The whole room, oldest first. A door that answers one hand cannot say
    // whether a second one is up behind it, and a hand nobody can see is the
    // one failure this surface is not allowed to have.
    const got = await read("/api/gates");
    if (got.code !== 200) return;
    const listed = ((got.body || {}).gates || []).filter(function (g) {
      return g && g.state === "pending";
    });
    const was = String((state.gate || {}).id || "");
    state.gates = listed;
    state.gate = listed[0] || { state: "clear" };
    const now = String(state.gate.id || "");
    if (now !== was) {
      state.gateNotice = "";
      // A different question arrived under the same card. The buttons stay
      // disarmed for a beat so a thumb already on its way down cannot approve
      // a command nobody read; the redraw at the end of the beat re-arms them.
      if (was && now) {
        state.gateArmAt = Date.now() + ARM_MS;
        state.gateNotice = "a different question arrived; read it again";
        setTimeout(function () {
          if (state.gateNotice === "a different question arrived; read it again") state.gateNotice = "";
          drawGate();
        }, ARM_MS + 20);
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
      drawCatchup();
      drawNeeds();
      drawDesks();
      drawAutomation();
      drawWall();
      if (state.openDesk && state.deskTab === "work") drawDesk();
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
  pollSessions();
  pollLive();
  /* A link straight to one session's transcript, read on load and on every
   * change: `#live=claude-4821`. It is how the Mac app and a notification open
   * this reader without a second surface knowing anything about the page. */
  window.addEventListener("hashchange", readHash);
  readHash();
  setInterval(function () {
    pollLive();
    if (state.openLive) pollReader();
  }, LIVE_EVERY_MS);
  setInterval(pollGate, GATE_EVERY_MS);
  setInterval(function () {
    /* Only while a session is open does its conversation reload; the roster
     * always does. Sessions draw inside the desk they belong to. */
    pollSessions();
    if (state.openSession) loadSessionScript(state.openSession);
  }, SESSION_EVERY_MS);
  setInterval(function () { pollWorld(false); pollBots(); }, WORLD_EVERY_MS);
  setInterval(pollThread, THREAD_EVERY_MS);
}

start();
