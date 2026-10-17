// nexus-shadow (#310): the Cloudflare shadow of the Office record layer.
//   POST /ingest  (bearer)  record revisions from the Studio -> D1 + an R2 receipt + the queue
//   queue consumer          judges each revision with the shadow's own rules; launches nothing
//   GET  /parity  (bearer)  the running parity summary and the latest mismatches
//   cron                    the comparator: one parity_runs row per pass
// Read by nobody operationally. The Studio ledger is authoritative; nothing here writes back.

import { invalid, judgeDispatch, judgeFault, sha256 } from "./parity.js";

const MAX_BATCH = 100;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json({ ok: true, role: "shadow" });
    if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);
    if (url.pathname === "/ingest" && request.method === "POST") return ingest(request, env);
    if (url.pathname === "/parity" && request.method === "GET") return json(await readout(env, url));
    if (url.pathname === "/compare" && request.method === "POST") return json(await compare(env));
    return json({ error: "not found" }, 404);
  },

  async queue(batch, env) {
    for (const message of batch.messages) {
      await judge(env, message.body.event_id);
      message.ack();
    }
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(compare(env));
  },
};

async function authorized(request, env) {
  const given = (request.headers.get("authorization") || "").replace(/^Bearer /, "");
  if (!env.SHADOW_TOKEN || !given) return false;
  const [a, b] = await Promise.all([sha256(given), sha256(env.SHADOW_TOKEN)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function ingest(request, env) {
  if (!(request.headers.get("content-type") || "").startsWith("application/json"))
    return json({ error: "json only" }, 415);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  const envelopes = Array.isArray(body?.records) ? body.records : null;
  if (!envelopes || envelopes.length > MAX_BATCH) return json({ error: `records: 1..${MAX_BATCH}` }, 400);
  const accepted = [], refused = [];
  for (const e of envelopes) {
    const why = await refusal(e);
    if (why) refused.push({ event_id: e?.event_id ?? null, why });
    else accepted.push({ event_id: e.event_id, fresh: await store(env, e) });
  }
  return json({ accepted, refused }, refused.length && !accepted.length ? 400 : 200);
}

async function refusal(e) {
  return invalid(e) || ((await sha256(e.record_json)) !== e.sha256 ? "sha256" : null)
    || (isJson(e.record_json) ? null : "record_json");
}

// One revision into D1, its receipt into R2, its event id onto the queue. False when already held.
async function store(env, e) {
  const res = await env.DB.prepare(
    `INSERT OR IGNORE INTO records (event_id, namespace, record_id, revision, record_json, sha256, transitions, origin, observed_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)`)
    .bind(e.event_id, e.namespace, e.record_id, e.revision, e.record_json, e.sha256,
      JSON.stringify(e.transitions), e.origin, e.observed_at).run();
  if (res.meta.changes !== 1) return false;
  await env.RECEIPTS.put(`receipts/${e.namespace}/${e.record_id}/${e.revision}.json`, JSON.stringify({
    event_id: e.event_id, sha256: e.sha256, origin: e.origin, observed_at: e.observed_at,
    received_at: new Date().toISOString(), authority: "studio",
  }), { httpMetadata: { contentType: "application/json" } });
  await env.DISPATCH.send({ event_id: e.event_id });
  return true;
}

// The dispatch consumer: one revision judged against shadow state only.
async function judge(env, eventId) {
  const row = await env.DB.prepare("SELECT * FROM records WHERE event_id=?1").bind(eventId).first();
  if (!row || row.judged_at) return;
  const record = JSON.parse(row.record_json);
  const transitions = JSON.parse(row.transitions);
  let pairs;
  if (row.namespace === "office.dispatch") {
    pairs = judgeDispatch(record, transitions);
  } else {
    const prev = await env.DB.prepare(
      `SELECT record_json FROM records WHERE namespace=?1 AND record_id=?2 AND observed_at < ?3
       ORDER BY observed_at DESC LIMIT 1`).bind(row.namespace, row.record_id, row.observed_at).first();
    pairs = [judgeFault(prev ? JSON.parse(prev.record_json) : null, record, transitions[0]?.seq ?? 0)];
  }
  const writes = pairs.map((p) => env.DB.prepare(
    `INSERT OR REPLACE INTO outcome_pairs (event_id, seq, namespace, record_id, from_state, to_state,
       studio_outcome, shadow_outcome, studio_verdict, shadow_verdict, match)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)`)
    .bind(eventId, p.seq, row.namespace, row.record_id, p.from, p.to, p.studio_outcome,
      JSON.stringify(p.shadow_outcome), p.studio_verdict, p.shadow_verdict, p.match ? 1 : 0));
  for (const p of pairs) {
    if (row.namespace !== "office.dispatch") continue;
    // The launch the shadow agrees with, recorded instead of fired.
    const chosen = ["dispatch", "redispatch", "claim_owner"].includes(p.studio_outcome) ? p.studio_outcome : null;
    if (chosen && p.match) writes.push(env.DB.prepare(
      "INSERT OR IGNORE INTO simulated_invocations (record_id, seq, queue, decision) VALUES (?1, ?2, ?3, ?4)")
      .bind(row.record_id, p.seq, record.queue, chosen));
  }
  writes.push(env.DB.prepare("UPDATE records SET judged_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE event_id=?1")
    .bind(eventId));
  await env.DB.batch(writes);
}

// The comparator: judge whatever the queue has not, then write one summary row.
async function compare(env) {
  const { results: late } = await env.DB.prepare(
    "SELECT event_id FROM records WHERE judged_at IS NULL ORDER BY observed_at LIMIT 200").all();
  for (const r of late) await judge(env, r.event_id);
  const totals = await env.DB.prepare(
    `SELECT (SELECT count(*) FROM records) AS records,
            (SELECT count(*) FROM records WHERE judged_at IS NULL) AS unjudged,
            count(*) AS pairs, coalesce(sum(match), 0) AS matches, min(judged_at) AS window_start
     FROM outcome_pairs`).first();
  const { results: lenses } = await env.DB.prepare(
    `SELECT namespace, count(*) AS pairs, sum(match) AS matches,
            sum(studio_verdict = shadow_verdict) AS verdict_matches FROM outcome_pairs GROUP BY namespace`).all();
  const summary = { ...totals, mismatches: totals.pairs - totals.matches, by_namespace: lenses,
    authority: "studio", operational_readers: 0 };
  await env.DB.prepare(
    `INSERT INTO parity_runs (records, unjudged, pairs, matches, mismatches, window_start, summary)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)`).bind(summary.records, summary.unjudged, summary.pairs,
    summary.matches, summary.mismatches, summary.window_start, JSON.stringify(summary)).run();
  return summary;
}

async function readout(env, url) {
  const latest = await env.DB.prepare("SELECT * FROM parity_runs ORDER BY id DESC LIMIT 1").first();
  const runs = await env.DB.prepare("SELECT count(*) AS n, min(at) AS first FROM parity_runs").first();
  const { results: mismatches } = await env.DB.prepare(
    "SELECT * FROM outcome_pairs WHERE match=0 ORDER BY judged_at DESC LIMIT 20").all();
  const out = { latest: latest && { ...latest, summary: JSON.parse(latest.summary) }, runs, mismatches };
  const id = url.searchParams.get("record_id");
  if (id) {
    out.record = (await env.DB.prepare(
      "SELECT * FROM records WHERE record_id=?1 ORDER BY observed_at DESC LIMIT 1").bind(id).first());
    out.pairs = (await env.DB.prepare(
      "SELECT * FROM outcome_pairs WHERE record_id=?1 ORDER BY seq").bind(id).all()).results;
    out.simulated = (await env.DB.prepare(
      "SELECT * FROM simulated_invocations WHERE record_id=?1 ORDER BY seq").bind(id).all()).results;
  }
  return out;
}

function isJson(text) {
  try { JSON.parse(text); return true; } catch { return false; }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
