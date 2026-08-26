import { test } from "node:test";
import assert from "node:assert/strict";
import { boardModel, ownerOf, ageText } from "../src/scene/fixtures/board.js";

/**
 * The honesty tests are the point of this file.
 *
 * A board that renders "the runtime is not running" the same as "there are no
 * commissions", or shows twelve done cards and lets you believe that is all of
 * them, is wrong in a way no amount of layout fixes. Whether a card is 132 pixels
 * tall is a nicety; whether the room can tell down from idle is the job.
 */

const NOW = Date.parse("2026-08-25T12:00:00Z");
const ago = (mins) => new Date(NOW - mins * 60000).toISOString();

const STATIONS = [{ repo: "acme/storefront" }, { repo: "acme/billing" }];

const upBoard = (over = {}) => ({
  state: "up",
  root: "/demo/acme/storefront",
  runs: [{ id: "run-9f2", slug: "checkout-host-name", state: "running", started: ago(7) }],
  active: [{ slug: "a", state: "active", title: "Capture host_name on iOS", updated: ago(20) }],
  complete: [{ slug: "c", title: "Ship the doctor command", updated: ago(60), has_chronicle: true, has_transcript: false }],
  archived_count: 14,
  metrics: { active: 1, complete: 22, archived: 14, runs: 61, total_cost: 18.42, average_cache_read_ratio: 0.71 },
  ...over,
});

/* ------------------------------------------- down is not the same as empty -- */

for (const state of ["down", "error", "unconfigured"]) {
  test(`a ${state} runtime does not render as an idle board`, () => {
    const m = boardModel({ state, detail: "connection refused" }, STATIONS, NOW);
    assert.equal(m.up, false);
    assert.equal(m.state, state);
    // No columns at all, so nothing downstream can draw four zeroes.
    assert.deepEqual(m.columns, []);
    assert.equal(m.totals, null);
    assert.ok(m.note.length > 0, "a stopped board must say why it is stopped");
  });
}

test("down, error and unconfigured each say a different thing", () => {
  const notes = ["down", "error", "unconfigured"].map(
    (state) => boardModel({ state }, [], NOW).note);
  assert.equal(new Set(notes).size, 3, notes.join(" / "));
});

test("a genuinely idle runtime is up, with four columns of zero", () => {
  const m = boardModel({
    state: "up", root: "", runs: [], active: [], complete: [], archived_count: 0,
    metrics: { active: 0, complete: 0, archived: 0, runs: 0, total_cost: 0 },
  }, STATIONS, NOW);
  assert.equal(m.up, true);
  assert.equal(m.columns.length, 4);
  assert.deepEqual(m.columns.map((c) => c.total), [0, 0, 0, 0]);
  // Which is the whole point: idle carries columns, down carries none.
  assert.notDeepEqual(m.columns, boardModel({ state: "down" }, STATIONS, NOW).columns);
});

test("a missing board object is unconfigured, never up", () => {
  assert.equal(boardModel(undefined, STATIONS, NOW).up, false);
  assert.equal(boardModel(undefined, STATIONS, NOW).state, "unconfigured");
});

/* --------------------------------------------------- the columns, in order -- */

test("the columns are running now, on the board, done, archived", () => {
  const m = boardModel(upBoard(), STATIONS, NOW);
  assert.deepEqual(m.columns.map((c) => c.key), ["running", "active", "complete", "archived"]);
  assert.deepEqual(m.columns.map((c) => c.label),
    ["running now", "on the board", "done", "archived"]);
});

test("cards are newest first", () => {
  const m = boardModel(upBoard({
    complete: [
      { slug: "old", title: "old", updated: ago(900) },
      { slug: "new", title: "new", updated: ago(5) },
      { slug: "mid", title: "mid", updated: ago(120) },
    ],
    metrics: { complete: 3 },
  }), STATIONS, NOW);
  const done = m.columns.find((c) => c.key === "complete");
  assert.deepEqual(done.cards.map((k) => k.slug), ["new", "mid", "old"]);
});

test("a card with no timestamp sorts last and says its age is unknown", () => {
  const m = boardModel(upBoard({
    complete: [{ slug: "nostamp", title: "no stamp" }, { slug: "dated", title: "dated", updated: ago(300) }],
    metrics: { complete: 2 },
  }), STATIONS, NOW);
  const done = m.columns.find((c) => c.key === "complete");
  assert.deepEqual(done.cards.map((k) => k.slug), ["dated", "nostamp"]);
  assert.equal(done.cards[1].age, "age unknown");
});

/* --------------------------------------------------------- truncation ------ */

test("the true total survives the twelve-item truncation", () => {
  const complete = Array.from({ length: 12 }, (_, i) => ({
    slug: `s${i}`, title: `commission ${i}`, updated: ago(i * 10),
  }));
  const m = boardModel(upBoard({ complete }), STATIONS, NOW);
  const done = m.columns.find((c) => c.key === "complete");
  assert.equal(done.cards.length, 12);
  assert.equal(done.total, 22, "metrics.complete is the truth, not the list length");
  assert.equal(done.truncated, true);
  assert.equal(m.totals.complete, 22);
});

test("archived is a count with no cards, and the count is still the truth", () => {
  const m = boardModel(upBoard(), STATIONS, NOW);
  const arch = m.columns.find((c) => c.key === "archived");
  assert.deepEqual(arch.cards, []);
  assert.equal(arch.total, 14);
  assert.equal(arch.truncated, true, "14 archived and 0 cards is a truncated column");
});

test("archived falls back to archived_count when metrics are missing", () => {
  const m = boardModel(upBoard({ archived_count: 9, metrics: {} }), STATIONS, NOW);
  assert.equal(m.columns.find((c) => c.key === "archived").total, 9);
});

test("an untruncated column does not claim to be truncated", () => {
  const m = boardModel(upBoard({ metrics: { active: 1, complete: 1, archived: 0 } }), STATIONS, NOW);
  assert.equal(m.columns.find((c) => c.key === "complete").truncated, false);
  assert.equal(m.columns.find((c) => c.key === "active").truncated, false);
});

test("running counts itself, never the lifetime run total", () => {
  const m = boardModel(upBoard(), STATIONS, NOW);
  const running = m.columns.find((c) => c.key === "running");
  assert.equal(running.total, 1);
  assert.equal(running.truncated, false);
  assert.equal(m.totals.runs, 61, "the lifetime figure still shows up as a total");
});

/* ------------------------------------------------------------ attachments -- */

test("a missing chronicle is false, and an absent field is unknown", () => {
  const m = boardModel(upBoard({
    complete: [
      { slug: "a", title: "explained itself", updated: ago(1), has_chronicle: true, has_transcript: true },
      { slug: "b", title: "did not explain itself", updated: ago(2), has_chronicle: false, has_transcript: false },
      { slug: "c", title: "an older snapshot", updated: ago(3) },
    ],
    metrics: { complete: 3 },
  }), STATIONS, NOW);
  const [a, b, c] = m.columns.find((k) => k.key === "complete").cards;
  assert.equal(a.chronicle, true);
  assert.equal(b.chronicle, false);
  assert.equal(c.chronicle, null, "a field the snapshot never carried is unknown, not missing");
  assert.equal(c.transcript, null);
});

/* ------------------------------------------------------------------ time --- */

test("a running card carries elapsed time", () => {
  const m = boardModel(upBoard(), STATIONS, NOW);
  const run = m.columns.find((c) => c.key === "running").cards[0];
  assert.equal(run.running, true);
  assert.equal(run.elapsed, "7m");
});

test("elapsed_s is used when the runtime reports it directly", () => {
  const m = boardModel(upBoard({ runs: [{ slug: "x", elapsed_s: 45 }] }), STATIONS, NOW);
  assert.equal(m.columns.find((c) => c.key === "running").cards[0].elapsed, "45s");
});

test("epoch seconds and ISO strings both parse", () => {
  const secs = boardModel(upBoard({
    complete: [{ slug: "s", title: "s", updated: NOW / 1000 - 3600 }], metrics: {},
  }), STATIONS, NOW);
  assert.equal(secs.columns.find((c) => c.key === "complete").cards[0].age, "1h");
});

test("ages read as a person would say them", () => {
  assert.equal(ageText(20 * 1000), "just now");
  assert.equal(ageText(12 * 60000), "12m");
  assert.equal(ageText(5 * 3600000), "5h");
  assert.equal(ageText(3 * 86400000), "3d");
  assert.equal(ageText(null), "age unknown");
});

/* ----------------------------------------------------------------- owner --- */

test("root matches the desk that owns it", () => {
  assert.equal(ownerOf("/demo/acme/storefront", STATIONS), "acme/storefront");
  assert.equal(ownerOf("/Users/x/code/acme/billing/", STATIONS), "acme/billing");
});

test("a root with no desk in the room resolves to nothing, not to a guess", () => {
  assert.equal(ownerOf("/demo/somewhere/else", STATIONS), null);
  assert.equal(ownerOf("", STATIONS), null);
  assert.equal(ownerOf(null, STATIONS), null);
});

test("every card carries the owning desk", () => {
  const m = boardModel(upBoard(), STATIONS, NOW);
  assert.equal(m.owner, "acme/storefront");
  for (const col of m.columns) {
    for (const k of col.cards) assert.equal(k.owner, "acme/storefront");
  }
});
