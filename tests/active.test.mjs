import assert from "node:assert/strict";
import test from "node:test";
import { viewer } from "../client/phone/office-active.js";

const doc = { repo: "ariaxhan/nexus-office", path: "docs/work.md" };
function recorder() {
  const sent = [];
  return { sent, active: viewer(async body => { sent.push(`${body.state} ${body.path}`); }) };
}

test("reopening the same document acks it again", async () => {
  const { sent, active } = recorder();
  active.show(doc); await active.show(doc);
  assert.deepEqual(sent, ["open docs/work.md", "open docs/work.md"]);
});

test("a sheet that is not the document (Ask an agent) reports it closed", async () => {
  const { sent, active } = recorder();
  active.show(doc); await active.show(null);
  assert.deepEqual(sent, ["open docs/work.md", "closed docs/work.md"]);
});

test("closing during the open handshake drops the late response", async () => {
  const { sent, active } = recorder();
  const claimed = active.claim();
  await active.show(null);            // the viewer closed while the file was loading
  assert.equal(active.current(claimed), false);
  assert.deepEqual(sent, []);
});

test("a response after navigation is stale", () => {
  const { active } = recorder();
  const first = active.claim(); active.claim();
  assert.equal(active.current(first), false);
});

test("Next part keeps the document open, never closed", async () => {
  const { sent, active } = recorder();
  active.show(doc);
  const claimed = active.claim();
  assert.ok(active.current(claimed));
  await active.show(doc);
  assert.deepEqual(sent, ["open docs/work.md", "open docs/work.md"]);
});

test("a missing document is reported under the requested names and clears the active one", async () => {
  const { sent, active } = recorder();
  active.show(doc); active.missing({ repo: doc.repo, path: "gone.md" }, "gone.md no longer exists");
  await active.show(null);
  assert.deepEqual(sent, ["open docs/work.md", "missing gone.md"]);
});

test("acks arrive in order even when a post is slow", async () => {
  const sent = [];
  const active = viewer(body => new Promise(done => setTimeout(() => { sent.push(body.state); done(); }, body.state === "open" ? 20 : 0)));
  active.show(doc); await active.show(null);
  assert.deepEqual(sent, ["open", "closed"]);
});

test("a located id reports the requested repo name", () => {
  const { active } = recorder();
  active.name("id1", doc);
  assert.deepEqual(active.label("id1", { repo: "other", path: "x" }), doc);
  assert.deepEqual(active.label("id2", { repo: "other", path: "x" }), { repo: "other", path: "x" });
});
