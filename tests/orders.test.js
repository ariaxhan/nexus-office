import { test } from "node:test";
import assert from "node:assert/strict";
import { plan, lightFrom, lightLabel, MAX_PENDING_SLIPS } from "../src/scene/fixtures/orders.js";

/**
 * The geometry is not unit testable and never will be: a picture is the only
 * honest check on a room. What IS testable is the rule that decides what the
 * room is allowed to claim, and this fixture makes exactly two claims that
 * matter, so both get a test that fails loudly if they ever stop being true.
 *
 *   1. an unknown pipeline never renders as an idle one
 *   2. a failed decision is never dropped, at any queue size
 */

const dec = (id, status, extra = {}) => ({
  id, status, kind: "nudge", repo: "acme/demo", issue: null,
  at: "2026-08-25T12:00:00Z", payload: {}, applied_at: null, result: null, ...extra,
});

/* ------------------------------------------------------------ the light -- */

test("no pipeline section at all is unknown, never idle", () => {
  for (const missing of [undefined, null, "", 0]) {
    assert.equal(lightFrom(missing).state, "unknown", String(missing));
  }
});

test("an unbuilt source is unknown, and says so rather than shrugging", () => {
  const l = lightFrom({ state: "unbuilt" });
  assert.equal(l.state, "unknown");
  assert.match(l.why, /not built/);
});

test("unconfigured and broken are both unknown, not idle", () => {
  assert.equal(lightFrom({ state: "unconfigured" }).state, "unknown");
  assert.equal(lightFrom({ state: "error", detail: "the runner is not answering" }).state, "unknown");
  assert.match(lightFrom({ state: "error", detail: "the runner is not answering" }).why, /not answering/);
});

test("unknown and idle never produce the same drawing", () => {
  const unknown = plan([], { state: "unbuilt" });
  const idle = plan([], { state: "ok", running: false, next_in: "4m" });

  assert.notEqual(unknown.light.state, idle.light.state);
  assert.notEqual(lightLabel(unknown.light), lightLabel(idle.light));
  assert.notDeepEqual(unknown.light, idle.light);
  // The two must differ in the words as well as the enum, because the label is
  // the half of the answer a person actually reads from across the room.
  assert.match(lightLabel(unknown.light), /UNKNOWN/);
  assert.doesNotMatch(lightLabel(idle.light), /UNKNOWN/i);
  assert.match(lightLabel(idle.light), /idle/);
  assert.doesNotMatch(lightLabel(unknown.light), /idle/i);
});

test("a run in flight is running, and carries what it is doing", () => {
  const l = lightFrom({ state: "ok", running: true, doing: "acme/storefront #214", running_for: "2m" });
  assert.equal(l.state, "running");
  assert.equal(l.doing, "acme/storefront #214");
  assert.match(lightLabel(l), /WORKING NOW/);
  assert.match(lightLabel(l), /acme\/storefront #214/);
});

test("running is not confusable with either of the other two", () => {
  const labels = [
    lightLabel(lightFrom({ state: "ok", running: true, doing: "x" })),
    lightLabel(lightFrom({ state: "ok", running: false, next_in: "4m" })),
    lightLabel(lightFrom({ state: "unbuilt" })),
  ];
  assert.equal(new Set(labels).size, 3, labels.join(" | "));
});

test("an ok pipeline with nothing running is idle, and says when it next looks", () => {
  const l = lightFrom({ state: "ok", running: false, next_in: "4m" });
  assert.equal(l.state, "idle");
  assert.match(lightLabel(l), /4m/);
  // No next_in is still idle, not unknown: the section said ok and said nothing
  // is running, which is an answer.
  assert.equal(lightFrom({ state: "ok", running: false }).state, "idle");
});

/* -------------------------------------------------------------- the tray -- */

test("a failed decision is never dropped, however long the queue gets", () => {
  const many = [
    dec(1, "failed", { result: "the runtime did not take it" }),
    ...Array.from({ length: 40 }, (_, i) => dec(100 + i, "pending")),
    dec(2, "failed", { result: "connection refused" }),
  ];
  const p = plan(many, { state: "unbuilt" });
  assert.equal(p.failed.length, 2, "both failures survive a queue far past the slip cap");
  assert.deepEqual(p.failed.map((f) => f.id), [1, 2]);
  assert.equal(p.waiting.length, MAX_PENDING_SLIPS, "only waiting slips are trimmed");
  assert.equal(p.waitingTotal, 40, "and the real count is still reported");
});

test("a failure keeps its verbatim reason, so the floor can show it", () => {
  const p = plan([dec(9, "failed", { result: "<urlopen error [Errno 61] Connection refused>" })], null);
  assert.equal(p.failed[0].result, "<urlopen error [Errno 61] Connection refused>");
});

test("a done order leaves the tray but is still counted", () => {
  const p = plan([dec(1, "done"), dec(2, "done"), dec(3, "pending")], null);
  assert.equal(p.gone, 2);
  assert.equal(p.waiting.length, 1);
  assert.equal(p.failed.length, 0);
});

test("failed and pending are never mixed into one pile", () => {
  const p = plan([dec(1, "pending"), dec(2, "failed"), dec(3, "done")], null);
  assert.deepEqual(p.waiting.map((s) => s.id), [1]);
  assert.deepEqual(p.failed.map((s) => s.id), [2]);
});

test("no decisions at all is an empty tray, and the light still has an answer", () => {
  for (const empty of [undefined, null, [], "nonsense"]) {
    const p = plan(empty, undefined);
    assert.deepEqual(p.waiting, []);
    assert.deepEqual(p.failed, []);
    assert.equal(p.gone, 0);
    assert.equal(p.light.state, "unknown", "an empty tray is not evidence the floor is fine");
  }
});

test("the demo floor's three orders draw as one waiting, one spilled, one gone", () => {
  const p = plan([dec(31, "pending"), dec(30, "failed", { result: "refused" }), dec(29, "done")], undefined);
  assert.equal(p.waitingTotal, 1);
  assert.equal(p.failed.length, 1);
  assert.equal(p.gone, 1);
});

test("a switched-off pipeline is loud, never idle, and says the true thing", () => {
  // `off` arrived in client/sources/pipeline.py after this fixture was designed:
  // kill switch on, or the launchd job disabled. We DO know what is happening, so
  // it keeps the loud look but drops the "nobody can say" wording.
  const off = lightFrom({ state: "off", detail: "the kill switch is on" });
  assert.equal(off.state, "unknown", "it never renders as idle");
  assert.equal(off.stopped, true);
  assert.match(off.why, /kill switch/);
  assert.match(lightLabel(off), /STOPPED/);
  assert.notEqual(lightLabel(off), lightLabel(lightFrom({ state: "unbuilt" })));
  assert.notEqual(lightLabel(off), lightLabel(lightFrom({ state: "ok", running: false, next_in: "4m" })));
  assert.doesNotMatch(lightLabel(off), /idle/i);
});
