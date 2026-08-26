import { test } from "node:test";
import assert from "node:assert/strict";
import { hopsFrom, laneSegments, HOP_DIR, MOVING_MIN } from "../src/scene/fixtures/journey.js";

/**
 * The geometry is not unit testable and never will be: a picture is the only
 * honest check on a room. The receipt-to-hop mapping is a different animal. It
 * is the rule that decides what the room is allowed to CLAIM, and every defect
 * in it is a lie drawn at sixty frames a second, so it is tested here.
 */

const ENDS = { door: { x: 0, z: 10 }, exit: { x: 0, z: -10 } };
const NOW = Date.parse("2026-08-25T12:00:00Z");
const ago = (mins) => new Date(NOW - mins * 60000).toISOString().replace(/\.\d+Z$/, "Z");

const station = (repo, runs, x = 3, z = -2) => ({ repo, x, z, runs });

test("an outcome nobody mapped produces no hop at all", () => {
  const hops = hopsFrom([station("a/b", [{ at: ago(5), outcome: "teleported" }])], NOW, ENDS);
  assert.deepEqual(hops, []);
});

test("the outcomes that say nothing moved draw nothing", () => {
  // deferred: the issues were never reached. caught-up: there was nothing to
  // reach. parked: the pipeline declined to touch the repo. None of the three
  // is a journey, and drawing one would be an inference.
  for (const outcome of ["deferred", "caught-up", "no-issues", "dry-run", "parked"]) {
    const hops = hopsFrom([station("a/b", [{ at: ago(5), outcome }])], NOW, ENDS);
    assert.deepEqual(hops, [], `${outcome} should not move`);
    assert.equal(HOP_DIR[outcome], undefined);
  }
});

test("a refusal bounces off the desk back toward the human", () => {
  const [hop] = hopsFrom(
    [station("acme/billing", [{ at: ago(9), outcome: "refused", issue: "58", detail: "needs a human" }])],
    NOW, ENDS
  );
  assert.equal(hop.dir, "back");
  assert.deepEqual(hop.from, { x: 3, z: -2 }, "it starts at the desk");
  assert.deepEqual(hop.to, ENDS.door, "it ends where the human stands");
  assert.ok(hop.to.z > hop.from.z, "the human is on the near side, so a refusal travels toward +z");
});

test("a report-only run also comes back at the human", () => {
  const [hop] = hopsFrom([station("tiny/scratch", [{ at: ago(9), outcome: "report-only" }])], NOW, ENDS);
  assert.equal(hop.dir, "back");
  assert.deepEqual(hop.to, ENDS.door);
});

test("a landed run leaves the desk for the exit, not for the human", () => {
  const [hop] = hopsFrom([station("acme/mobile", [{ at: ago(9), outcome: "landed" }])], NOW, ENDS);
  assert.equal(hop.dir, "out");
  assert.deepEqual(hop.from, { x: 3, z: -2 });
  assert.deepEqual(hop.to, ENDS.exit);
  assert.ok(hop.to.z < hop.from.z);
});

test("a survey brings work in through the door and onto the desk", () => {
  const [hop] = hopsFrom([station("northwind/api", [{ at: ago(9), outcome: "survey" }])], NOW, ENDS);
  assert.equal(hop.dir, "in");
  assert.deepEqual(hop.from, ENDS.door);
  assert.deepEqual(hop.to, { x: 3, z: -2 });
});

test("age buckets a token into moving or trail, on the hour", () => {
  const runs = [
    { at: ago(1), outcome: "landed" },
    { at: ago(MOVING_MIN), outcome: "landed" },
    { at: ago(MOVING_MIN + 1), outcome: "landed" },
    { at: ago(1400), outcome: "landed" },
  ];
  const hops = hopsFrom([station("a/b", runs)], NOW, ENDS);
  assert.deepEqual(hops.map((h) => h.moving), [true, true, false, false]);
  assert.ok(hops[0].ageMin < hops[3].ageMin, "hops come back youngest first");
});

test("a receipt with an unreadable timestamp is trail, never a phantom mover", () => {
  const [hop] = hopsFrom([station("a/b", [{ at: "whenever", outcome: "refused" }])], NOW, ENDS);
  assert.equal(hop.moving, false);
  assert.equal(hop.ageMin, Infinity);
});

test("the hop carries the receipt that justifies it, verbatim", () => {
  const [hop] = hopsFrom(
    [station("acme/storefront", [{ at: ago(22), outcome: "landed", issue: "213", detail: "pipeline/auto/push" }])],
    NOW, ENDS
  );
  assert.equal(hop.repo, "acme/storefront");
  assert.equal(hop.at, ago(22));
  assert.equal(hop.issue, "213");
  assert.equal(hop.detail, "pipeline/auto/push");
});

test("a station with no runs contributes nothing", () => {
  assert.deepEqual(hopsFrom([station("a/b", [])], NOW, ENDS), []);
  assert.deepEqual(hopsFrom([{ repo: "a/b", x: 0, z: 0 }], NOW, ENDS), []);
  assert.deepEqual(hopsFrom(undefined, NOW, ENDS), []);
});

test("the throughput lane keeps every count and flatters none of them", () => {
  const { rows, total } = laneSegments({ survey: 41, deferred: 22, landed: 6, refused: 4, nonsense: 9 });
  assert.equal(total, 73, "an outcome the lane does not know about is not silently counted");
  assert.deepEqual(rows.map((r) => r.name), ["survey", "deferred", "refused", "landed"]);
  const share = (n) => rows.find((r) => r.name === n).n / total;
  assert.ok(share("deferred") > share("landed") * 3, "deferred dwarfing landed has to survive to the drawing");
});

test("an outcome with a zero count gets no segment, rather than a sliver", () => {
  const { rows } = laneSegments({ survey: 3, landed: 0 });
  assert.deepEqual(rows.map((r) => r.name), ["survey"]);
});

test("no counts at all is an empty lane, not a fake one", () => {
  assert.deepEqual(laneSegments(undefined), { rows: [], total: 0 });
  assert.deepEqual(laneSegments({}), { rows: [], total: 0 });
});
