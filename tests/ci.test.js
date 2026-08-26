/**
 * The build lamps, on the browser side.
 *
 * The python tests cover the classification. The one thing they cannot cover is
 * the thing this repo cares most about: whether every state has a PICTURE. The
 * demo floor is what `npm run shot` renders, so a state the demo never produces
 * is a lamp nobody has ever looked at, and the drawing for it can rot for
 * months without a single test going red.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import * as ci from "../src/scene/fixtures/ci.js";

const STATES = ["failing", "unknown", "never", "running", "passing", "none"];

test("the demo floor shows every state, so every lamp has a picture", () => {
  const sec = ci.demo();
  const seen = new Set(sec.repos.map((r) => r.ci));
  for (const s of STATES) assert.ok(seen.has(s), `no demo repo is ${s}`);
});

test("no CI at all is never counted as an alarm", () => {
  const sec = ci.demo();
  const quiet = sec.repos.filter((r) => r.ci === "none" || r.ci === "passing"
    || r.ci === "running").length;
  assert.equal(sec.alarm, sec.repos.length - quiet);
  assert.ok(sec.counts.none > 0, "the demo has to include a repo with no CI");
});

test("a repo nobody could look at never reports a check time", () => {
  for (const r of ci.demo().repos) {
    if (r.ci === "unknown" || r.ci === "never") {
      assert.equal(r.checked_at, null, `${r.repo} claims a check it never had`);
    }
  }
});

test("every failing repo names its jobs and links the run", () => {
  const failing = ci.demo().repos.filter((r) => r.ci === "failing");
  assert.ok(failing.length, "the demo has to include a failing build");
  for (const r of failing) {
    assert.ok(r.failing.length, `${r.repo} is failing with no job named`);
    for (const j of r.failing) {
      assert.ok(j.name, `${r.repo} has an unnamed failing job`);
      assert.ok(j.url, `${r.repo}'s job ${j.name} has no link to its run`);
    }
  }
});

test("the counts add up to the repos, so nothing is dropped on the floor", () => {
  const sec = ci.demo();
  const total = STATES.reduce((a, k) => a + (sec.counts[k] || 0), 0);
  assert.equal(total, sec.repos.length);
  assert.equal(sec.checked, sec.repos.length);
});
