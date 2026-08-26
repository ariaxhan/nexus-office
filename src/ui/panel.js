import { STATES } from "../scene/villager.js";
import { sendDecision } from "../api.js";
import { renderInto } from "./markdown.js";
import { byId } from "../scene/fixtures/all.js";

/**
 * The panel is the half of this thing that does work.
 *
 * Every button here queues an intent and nothing more. It never claims the change
 * happened, because the browser has no credentials and cannot know: it says
 * "queued", and the next snapshot from home is what turns that into a fact.
 */

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const rel = (iso) => {
  if (!iso) return "at an unknown time";
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(m)) return "at an unknown time";
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
};

const NEEDS_HUMAN = /waiting on|needs.?(human|you|decision)|blocked|question/i;

/**
 * The runner's own rule, not a second opinion on it: an issue is waiting on a
 * person exactly when the bot had the last word. A label is a hint that can go
 * stale behind the truth; `bot_last` is computed from the comments themselves,
 * so it cannot. The label check survives only as a fallback for a snapshot old
 * enough to predate the field.
 */
export const needsHuman = (issue) =>
  issue.bot_last === true ||
  (issue.bot_last === undefined && (issue.labels || []).some((l) => NEEDS_HUMAN.test(l)));

function avatar(resident) {
  const c = document.createElement("canvas");
  c.width = c.height = 80;
  const g = c.getContext("2d");
  g.fillStyle = resident.coat;
  g.beginPath();
  g.arc(40, 40, 38, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "rgba(255,179,186,0.8)";
  for (const s of [-1, 1]) {
    g.beginPath();
    g.ellipse(40 + s * 21, 47, 8, 5, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = "#3a2f2b";
  for (const s of [-1, 1]) {
    g.beginPath();
    g.ellipse(40 + s * 12, 36, 4, 5.5, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.strokeStyle = "#3a2f2b";
  g.lineWidth = 3.4;
  g.lineCap = "round";
  g.beginPath();
  g.arc(40, 48, 8, Math.PI * 0.18, Math.PI * 0.82);
  g.stroke();
  const img = el("img", "p-face");
  img.src = c.toDataURL();
  img.alt = "";
  return img;
}

/** The idempotency marker is machinery, not prose. Nobody should ever see it. */
function stripMarker(text) {
  return text.replace(/<!--[\s\S]*?-->/g, "").trim() || "(no message)";
}

function relative(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return "never";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

export class Panel {
  constructor(node, { onToast, onRefresh, onHideRepo, onHideOwner }) {
    this.node = node;
    this.onToast = onToast;
    this.onRefresh = onRefresh;
    this.onHideRepo = onHideRepo;
    this.onHideOwner = onHideOwner;
    this.openIssue = null;
  }

  close() {
    this.node.hidden = true;
    this.node.replaceChildren();
    this.station = null;
  }

  /** The tray of everything that stopped because it needs a person. */
  showInbox(world) {
    const rows = [];
    for (const st of world.stations) {
      for (const i of st.issues || []) if (needsHuman(i)) rows.push({ st, i });
    }
    rows.sort((a, b) => (b.i.updatedAt || "").localeCompare(a.i.updatedAt || ""));
    this._frame({
      title: "Waiting on you",
      sub: `${rows.length} thing${rows.length === 1 ? "" : "s"} stopped until you answer`,
      color: rows.length ? "#d1495b" : "#3f9e6a",
    });
    const body = this.node.querySelector(".p-body");
    if (!rows.length) {
      body.append(el("p", "empty", "Nothing is waiting. Every villager has something it can do on its own."));
      return;
    }
    for (const { st, i } of rows) body.append(this._issueCard(st, i, { showRepo: true }));
  }

  /**
   * The panel for something that is not a desk: the clock, the chart, the
   * mailroom. The fixture renders its own body; the frame and the plumbing are
   * shared so a fixture never has to touch this file.
   */
  showFixture({ id, payload }, world) {
    const f = byId(id);
    this.station = null;
    this._frame({ title: f?.title || id, sub: "", color: "#5b8dd9" });
    const body = this.node.querySelector(".p-body");
    if (!f) return body.append(el("p", "empty", `No fixture called "${id}".`));
    const api = {
      el,
      md: (node, text) => renderInto(node, text),
      queue: (decision, btn) => this._queue(decision, btn),
      button: (label, cls, fn) => this._button(label, cls, fn),
      toast: (msg, bad) => this.onToast(msg, bad),
      refresh: () => this.onRefresh?.(),
    };
    try {
      const node = f.panel(payload, world, api);
      if (node) body.append(node);
    } catch (err) {
      console.error(`fixture "${id}" panel failed`, err);
      body.append(el("p", "empty", "That panel could not be drawn. The console has the reason."));
    }
  }

  /**
   * What you asked for, and what became of it.
   *
   * The Worker has always returned this queue and the browser used to drop it on
   * the floor, so pressing a button produced a toast and then silence. That is
   * the worst version of the failure, because a decision that FAILED looked
   * exactly like one that worked: the reason was already written to
   * `decision.result` and simply never shown to anyone.
   */
  showOrders(world) {
    this.station = null;
    const all = world.decisions || [];
    const pending = all.filter((d) => d.status === "pending");
    const done = all.filter((d) => d.status !== "pending");
    const failed = all.filter((d) => d.status === "failed");

    this._frame({
      title: "What you asked for",
      sub: pending.length
        ? `${pending.length} waiting for home to pick ${pending.length === 1 ? "it" : "them"} up`
        : "nothing is waiting",
      color: failed.length ? "#d1495b" : pending.length ? "#c98a2e" : "#3f9e6a",
    });
    const body = this.node.querySelector(".p-body");

    // The one fact that says whether the other end is alive at all. A queue that
    // is not moving because home is asleep must never look like a queue that is
    // not moving because the work is slow.
    const beat = world.heartbeat || world.at;
    const mins = beat ? Math.round((Date.now() - Date.parse(beat)) / 60000) : null;
    const line = el("p", mins != null && mins > 25 ? "log stale" : "log");
    line.textContent = mins == null
      ? "This office has never heard from home."
      : mins <= 1
        ? "Home checked in just now, so the queue is moving."
        : `Home last checked in ${mins}m ago.` +
          (mins > 25 ? " That is longer than it should be; the drain job may be down." : "");
    body.append(line);

    const pipe = world.sections?.pipeline;
    if (pipe) body.append(this._pipeline(pipe));

    if (!all.length) {
      body.append(el("p", "empty",
        "You have not asked for anything yet. Buttons on a desk queue an intent here, " +
        "and this is where you watch it happen."));
      return;
    }

    const card = (d) => {
      const box = el("div", `issue${d.status === "failed" ? " hot" : ""}`);
      const top = el("div", "issue-top");
      top.append(el("span", "issue-num", d.status === "pending" ? "queued"
        : d.status === "failed" ? "failed" : "done"));
      top.append(el("span", "issue-title",
        `${d.kind}${d.repo ? ` · ${d.repo}` : ""}${d.issue ? `#${d.issue}` : ""}`));
      box.append(top);

      const said = (d.payload && d.payload.body || "").trim();
      if (said) box.append(el("p", "p-detail", said.length > 220 ? said.slice(0, 220) + "…" : said));

      const when = el("p", "log");
      when.textContent = d.status === "pending"
        ? `asked ${rel(d.at)}, not picked up yet`
        : `asked ${rel(d.at)}, ${d.status} ${rel(d.applied_at)}`;
      box.append(when);

      // Verbatim, never summarised. This is the only place the reason a thing
      // did not happen is ever visible.
      if (d.result) box.append(el("pre", "gate-target", d.result));
      return box;
    };

    if (pending.length) {
      body.append(el("h3", null, `waiting to be picked up (${pending.length})`));
      for (const d of pending) body.append(card(d));
    }
    if (done.length) {
      body.append(el("h3", null, `already handled (${done.length})`));
      for (const d of done.slice(0, 20)) body.append(card(d));
    }
  }

  /** Is the pipeline actually working right now, and when does it next look. */
  _pipeline(pipe) {
    const box = el("div");
    // "Switched off" is NOT "cannot tell". The office knows perfectly well; it is
    // a decision somebody made. Reporting a deliberate stop as an unknown is the
    // same conflation the wall clock had to undo between OFF and STALE.
    if (pipe.state === "off") {
      box.append(el("p", "log stale",
        `The pipeline is switched off, so nothing will run: ${pipe.detail || "no reason given"}.`));
      return box;
    }
    if (pipe.state !== "ok") {
      box.append(el("p", "log stale",
        pipe.state === "unconfigured"
          ? "No pipeline is configured for this office."
          : `Cannot tell what the pipeline is doing: ${pipe.detail || pipe.state}.`));
      return box;
    }
    // A pid file left behind by a killed run is a claim that a run is alive.
    // Saying so is the difference between a quiet office and a lying one.
    if (pipe.stale_pid) {
      box.append(el("p", "log stale",
        `A run left its pid behind: ${pipe.stale_pid.why || "the process is gone"}.`));
    }
    if (pipe.running) {
      const p = el("p", "log");
      p.append(el("b", null, "A run is in flight right now"),
        pipe.running_for ? ` · started ${pipe.running_for} ago` : "");
      box.append(p);
      if (pipe.doing) box.append(el("pre", "gate-target", pipe.doing));
    } else {
      // Past its own schedule is its own sentence. launchd coalesces timers, so a
      // few late minutes are normal and a fake "0m" would hide a real stall.
      box.append(el("p", pipe.overdue ? "log stale" : "log", pipe.overdue
        ? `Nothing running, and the next look is ${pipe.late_by || "a while"} overdue.`
        : pipe.next_in
          ? `Nothing running. The pipeline next looks in ${pipe.next_in}.`
          : "Nothing running."));
    }
    return box;
  }

  showStation(station, world) {
    this.station = station;
    const spec = STATES[station.state] || STATES.idle;
    const res = station.resident;

    // The repo leads. The villager's name is how you recognise the character
    // across visits, not how you identify the work, so it sits underneath.
    this._frame({
      title: station.repo.split("/").pop(),
      repo: station.repo,
      sub: `${res.name} sits here`,
      color: spec.color,
      state: spec.label,
      avatarFor: res,
      detail: station.detail,
      lastSeen: station.at,
    });

    const body = this.node.querySelector(".p-body");

    // The gate goes FIRST, above everything, always. An agent is stopped and a
    // clock is running against it; nothing else on this panel is more urgent.
    if (station.gate) body.append(this._gate(station.gate));

    const issues = station.issues || [];
    const hot = issues.filter(needsHuman);
    const rest = issues.filter((i) => !needsHuman(i));

    if (hot.length) {
      body.append(el("h3", null, `needs you (${hot.length})`));
      for (const i of hot) body.append(this._issueCard(station, i));
    }
    if (rest.length) {
      body.append(el("h3", null, `open (${rest.length})`));
      for (const i of rest) body.append(this._issueCard(station, i));
    }
    if (!issues.length) {
      body.append(el("p", "empty", station.state === "locked"
        ? "No account we hold a token for can push here, so nobody sits at this desk."
        : "No open issues. This desk is clear."));
    }

    body.append(el("h3", null, "recent runs"));
    const runs = station.runs || [];
    if (!runs.length) body.append(el("p", "empty", "The runner has not reached this repo yet."));
    for (const r of runs.slice(0, 8)) {
      const line = el("p", "log");
      const cls = r.outcome === "landed" ? "ok"
        : r.outcome === "refused" ? "bad"
        : r.outcome === "deferred" ? "wait" : "";
      line.append(el("b", null, relative(r.at)), " ");
      line.append(el("span", cls, r.outcome));
      if (r.issue) line.append(` #${r.issue}`);
      if (r.detail) line.append(` — ${r.detail.slice(0, 110)}`);
      body.append(line);
    }

    const acts = el("div", "acts");
    if (station.state !== "locked") {
      acts.append(this._button("Work this repo next", "act act-go", () =>
        this._queue({ kind: "nudge", repo: station.repo })));
    }
    acts.append(this._button("Put this desk away", "act", () =>
      this.onHideRepo?.(station.repo)));
    acts.append(this._button(`Put away all of ${station.repo.split("/")[0]}`, "act", () =>
      this.onHideOwner?.(station.repo)));
    body.append(acts);
    void world;
  }

  _frame({ title, repo, sub, color, state, avatarFor, detail, lastSeen }) {
    this.node.hidden = false;
    this.node.replaceChildren();

    const head = el("div", "p-head");
    const close = el("button", "p-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "close");
    close.onclick = () => this.close();
    head.append(close);

    const who = el("div", "p-who");
    if (avatarFor) who.append(avatar(avatarFor));
    const stack = el("div");
    stack.append(el("div", "p-name", title));
    if (repo) stack.append(el("div", "p-repo", repo));
    if (sub) stack.append(el("div", "p-repo", sub));
    who.append(stack);
    head.append(who);

    if (state) {
      const badge = el("span", "p-state", state);
      badge.style.background = color;
      head.append(badge);
    }
    if (detail) head.append(el("p", "p-detail", detail.slice(0, 220)));
    if (lastSeen) head.append(el("p", "p-detail", `last run ${relative(lastSeen)}`));

    this.node.append(head, el("div", "p-body"));
  }

  /**
   * The permission gate.
   *
   * Rules that are not negotiable:
   *   - the target is shown VERBATIM, never summarised. A gate you approve
   *     without reading the literal command is not a gate, it is a rubber stamp
   *   - the question's id travels with the answer, so an approval can never land
   *     on a different question than the one displayed
   *   - "allow always" is visibly heavier than "allow once", because it writes a
   *     standing rule and the next one will not ask
   */
  _gate(gate) {
    const box = el("div", "gate-box");
    box.append(el("div", "gate-kicker", "an agent is waiting on you"));
    box.append(el("div", "gate-perm", gate.permission || "permission"));

    box.append(el("h3", null, "it wants to run"));
    box.append(el("pre", "gate-target", gate.target || "(no target given)"));
    if (gate.detail) box.append(el("p", "p-detail", gate.detail));

    if (gate.waiting_s != null) {
      box.append(el("p", "gate-clock",
        `waiting ${gate.waiting_s}s. Unanswered, it fails closed.`));
    }

    const acts = el("div", "acts");
    const send = (answer, always, btn) => this._queue({
      kind: "permit",
      question_id: gate.id,
      answer,
      always,
    }, btn);

    acts.append(this._button("Allow once", "act act-go", (b) => send("allow", false, b)));
    acts.append(this._button("Deny", "act act-warn", (b) => send("deny", false, b)));
    box.append(acts);

    const always = el("div", "acts");
    always.append(this._button("Allow always, stop asking", "act act-heavy",
      (b) => send("allow", true, b)));
    box.append(always);
    return box;
  }

  /** Say something to the floor. Deliberately asynchronous, and it says so. */
  showChat(world) {
    this._frame({
      title: "Talk to the floor",
      sub: "goes to the local runtime",
      color: "#5b8dd9",
    });
    const body = this.node.querySelector(".p-body");
    const board = world.runtime?.board;

    if (!board || board.state !== "up") {
      body.append(el("p", "empty",
        board?.state === "down"
          ? "The runtime is not running, so there is nobody to talk to. Start it and this comes alive."
          : "No runtime is configured for this office yet."));
      return;
    }

    const box = el("textarea", "reply");
    box.placeholder = "Ask the floor for something.";
    body.append(box);

    const acts = el("div", "acts");
    acts.append(this._button("Send", "act act-go", (b) => {
      const text = box.value.trim();
      if (!text) return this.onToast("Write something first", true);
      this._queue({ kind: "chat", body: text }, b);
      box.value = "";
    }));
    body.append(acts);

    // Never fake responsiveness. The push cycle is minutes, and pretending
    // otherwise makes a working system look broken.
    body.append(el("p", "p-detail",
      "This is queued, not live. Home picks it up within a minute or two and the " +
      "reply appears on the next snapshot."));

    const runs = board.runs || [];
    body.append(el("h3", null, `running now (${runs.length})`));
    if (!runs.length) body.append(el("p", "empty", "Nothing is running."));
    for (const r of runs.slice(0, 6)) {
      const line = el("p", "log");
      line.append(el("b", null, r.slug || r.id || "run"), " ", r.state || r.status || "");
      body.append(line);
    }
    if (runs.length) {
      const acts2 = el("div", "acts");
      acts2.append(this._button("Stop the current run", "act act-warn", (b) =>
        this._queue({ kind: "stop", run_id: runs[0].id || runs[0].slug || "" }, b)));
      body.append(acts2);
    }

    const m = board.metrics || {};
    if (m.total_cost != null) {
      body.append(el("h3", null, "ledger"));
      body.append(el("p", "log", `${m.runs ?? 0} runs, $${Number(m.total_cost).toFixed(2)} total`));
    }
  }

  _issueCard(station, issue, { showRepo = false } = {}) {
    const hot = needsHuman(issue);
    const card = el("div", `issue${hot ? " hot" : ""}`);
    const top = el("div", "issue-top");
    top.append(el("span", "issue-num", `#${issue.number}`));
    top.append(el("span", "issue-title", issue.title));
    card.append(top);
    if (showRepo) card.append(el("div", "issue-repo", station.repo));

    if (issue.labels?.length) {
      const tags = el("div", "tags");
      for (const l of issue.labels) tags.append(el("span", `tag${NEEDS_HUMAN.test(l) ? " hot" : ""}`, l));
      card.append(tags);
    }

    const key = `${station.repo}#${issue.number}`;
    const expand = () => {
      if (card.classList.contains("open")) return;
      card.classList.add("open");
      this.openIssue = key;
      card.append(this._issueDetail(station, issue));
    };
    card.onclick = (e) => {
      if (e.target.closest("button, textarea, a")) return;
      expand();
    };
    if (this.openIssue === key) expand();
    return card;
  }

  _issueDetail(station, issue) {
    const wrap = el("div");

    // What the runner said last is the actual question on the table, so it goes
    // above the issue description rather than behind another click.
    if (issue.last_word) {
      wrap.append(el("h3", null, "the runner's last word"));
      wrap.append(renderInto(el("div", "issue-body md last-word"), stripMarker(issue.last_word)));
      wrap.append(el("h3", null, "the issue"));
    }
    const body = (issue.body || "").trim();
    wrap.append(body
      ? renderInto(el("div", "issue-body md"), body)
      : el("div", "issue-body", "No description on this issue."));

    const reply = el("textarea", "reply");
    reply.placeholder = needsHuman(issue)
      ? "Answer it. This posts as a comment and clears the waiting label so the runner picks it up."
      : "Say something on this issue.";
    wrap.append(reply);

    const acts = el("div", "acts");
    const hot = needsHuman(issue);
    acts.append(this._button(hot ? "Answer and unblock" : "Comment", "act act-go", (btn) => {
      const body = reply.value.trim();
      if (!body) return this.onToast("Write something first", true);
      this._queue({
        kind: hot ? "unblock" : "comment",
        repo: station.repo,
        issue: issue.number,
        body,
      }, btn);
      reply.value = "";
    }));
    acts.append(this._button("Work this next", "act", (btn) =>
      this._queue({ kind: "nudge", repo: station.repo, issue: issue.number }, btn)));
    acts.append(this._button("Run it now", "act", (btn) =>
      this._queue({
        kind: "run",
        repo: station.repo,
        issue: issue.number,
        body: `Work ${station.repo}#${issue.number}: ${issue.title}`,
      }, btn)));
    acts.append(this._button("Close it", "act act-warn", (btn) =>
      this._queue({
        kind: "close",
        repo: station.repo,
        issue: issue.number,
        body: reply.value.trim(),
      }, btn)));
    wrap.append(acts);

    if (issue.url) {
      const a = el("a", "link", "open on GitHub");
      a.href = issue.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      wrap.append(a);
    }
    return wrap;
  }

  _button(text, cls, fn) {
    const b = el("button", cls, text);
    b.type = "button";
    b.onclick = () => fn(b);
    return b;
  }

  async _queue(decision, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "queueing…"; }
    try {
      await sendDecision(decision);
      // Points at the tray rather than making a promise. "Within the minute" was
      // a claim the browser had no way to keep or to check.
      this.onToast("Queued. Watch it in \u201cqueued\u201d, top right.");
      if (btn) btn.textContent = "queued";
      this.onRefresh?.();
    } catch (err) {
      this.onToast(err.message || "could not queue", true);
      if (btn) { btn.disabled = false; btn.textContent = "retry"; }
    }
  }
}
