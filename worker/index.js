/**
 * nexus-office Worker.
 *
 * It is deliberately dumb. It holds two things and no credentials:
 *   - the latest world snapshot (what the office looks like right now)
 *   - a queue of decisions Aria made in the browser
 *
 * The local runner is the only process on earth that can act on GitHub. That is
 * the whole security model: a stolen view token lets someone queue an intent,
 * never execute one, and the runner re-checks every intent against the real repo
 * before touching it.
 */

const KINDS = new Set(["comment", "unblock", "close", "reopen", "label", "nudge"]);

const PAIR_TTL_MS = 10 * 60 * 1000;
// No I, O, 0, 1: this gets read off one screen and typed into another.
const PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function pairCode() {
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return [...bytes].map((b) => PAIR_ALPHABET[b % PAIR_ALPHABET.length]).join("");
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });

/** Constant-time-ish compare. Tokens are hex of equal length, so length leak is nil. */
function tokenEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function bearer(req) {
  const h = req.headers.get("authorization") || "";
  const m = /^Bearer\s+(.+)$/i.exec(h.trim());
  return m ? m[1].trim() : "";
}

/** "view" can read the world and queue intent. "push" is the local runner. */
function auth(req, env, need) {
  const t = bearer(req);
  if (!t) return false;
  if (need === "push") return tokenEq(t, env.PUSH_TOKEN || "");
  return tokenEq(t, env.VIEW_TOKEN || "") || tokenEq(t, env.PUSH_TOKEN || "");
}

async function readJson(req, limit = 256 * 1024) {
  const text = await req.text();
  if (text.length > limit) throw new Error("payload too large");
  return JSON.parse(text || "{}");
}

const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;

    if (!p.startsWith("/api/")) return env.ASSETS.fetch(req);

    try {
      // ---- world: everything the browser needs in one round trip -----------
      if (p === "/api/world" && req.method === "GET") {
        if (!auth(req, env, "view")) return json({ error: "unauthorized" }, 401);
        const snap = await env.DB.prepare("SELECT at, json FROM snapshot WHERE id = 1").first();
        const { results } = await env.DB.prepare(
          "SELECT id, at, kind, repo, issue, payload, status, applied_at, result " +
          "FROM decision ORDER BY id DESC LIMIT 40"
        ).all();
        return json({
          at: snap ? snap.at : null,
          world: snap ? JSON.parse(snap.json) : null,
          decisions: (results || []).map((r) => ({ ...r, payload: safeParse(r.payload) })),
          server_time: nowIso(),
        });
      }

      // ---- decision: Aria clicked something --------------------------------
      if (p === "/api/decision" && req.method === "POST") {
        if (!auth(req, env, "view")) return json({ error: "unauthorized" }, 401);
        const body = await readJson(req);
        const kind = String(body.kind || "");
        const repo = String(body.repo || "");
        if (!KINDS.has(kind)) return json({ error: `unknown kind ${kind}` }, 400);
        if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) return json({ error: "bad repo" }, 400);
        const issue = body.issue == null ? null : String(body.issue);
        if (issue !== null && !/^\d+$/.test(issue)) return json({ error: "bad issue" }, 400);
        if (kind !== "nudge" && issue === null) return json({ error: `${kind} needs an issue` }, 400);

        const payload = JSON.stringify({
          body: typeof body.body === "string" ? body.body.slice(0, 20000) : "",
          label: typeof body.label === "string" ? body.label.slice(0, 100) : "",
        });
        const res = await env.DB.prepare(
          "INSERT INTO decision (at, kind, repo, issue, payload) VALUES (?, ?, ?, ?, ?)"
        ).bind(nowIso(), kind, repo, issue, payload).run();
        return json({ ok: true, id: res.meta.last_row_id });
      }

      // ---- snapshot: the local pusher replaces the world --------------------
      if (p === "/api/snapshot" && req.method === "POST") {
        if (!auth(req, env, "push")) return json({ error: "unauthorized" }, 401);
        const body = await readJson(req, 2 * 1024 * 1024);
        await env.DB.prepare(
          "INSERT INTO snapshot (id, at, json) VALUES (1, ?, ?) " +
          "ON CONFLICT(id) DO UPDATE SET at = excluded.at, json = excluded.json"
        ).bind(nowIso(), JSON.stringify(body)).run();
        return json({ ok: true, at: nowIso() });
      }

      // ---- inbox: the local runner drains intent ---------------------------
      if (p === "/api/inbox" && req.method === "GET") {
        if (!auth(req, env, "push")) return json({ error: "unauthorized" }, 401);
        const { results } = await env.DB.prepare(
          "SELECT id, at, kind, repo, issue, payload FROM decision " +
          "WHERE status = 'pending' ORDER BY id LIMIT 50"
        ).all();
        return json({ pending: (results || []).map((r) => ({ ...r, payload: safeParse(r.payload) })) });
      }

      const doneMatch = /^\/api\/inbox\/(\d+)$/.exec(p);
      if (doneMatch && req.method === "POST") {
        if (!auth(req, env, "push")) return json({ error: "unauthorized" }, 401);
        const body = await readJson(req);
        const status = body.status === "failed" ? "failed" : "done";
        await env.DB.prepare(
          "UPDATE decision SET status = ?, applied_at = ?, result = ? WHERE id = ?"
        ).bind(status, nowIso(), String(body.result || "").slice(0, 2000), Number(doneMatch[1])).run();
        return json({ ok: true });
      }

      // ---- pairing: six characters instead of forty-eight -------------------
      if (p === "/api/pair" && req.method === "POST") {
        if (!auth(req, env, "push")) return json({ error: "unauthorized" }, 401);
        const code = pairCode();
        await env.DB.prepare("DELETE FROM pairing WHERE used = 1 OR at < ?")
          .bind(new Date(Date.now() - PAIR_TTL_MS).toISOString()).run();
        await env.DB.prepare("INSERT INTO pairing (code, at) VALUES (?, ?)")
          .bind(code, nowIso()).run();
        return json({ code, url: `${url.origin}/pair/${code}`, expires_in: PAIR_TTL_MS / 1000 });
      }

      // Unauthenticated on purpose: the code IS the credential, and it is single
      // use and short lived precisely so that being guessable-in-theory does not
      // matter. A used code is burned before the token is handed back, so two
      // browsers racing the same code cannot both win.
      if (p === "/api/pair/claim" && req.method === "POST") {
        const { code } = await readJson(req, 1024);
        if (typeof code !== "string" || !/^[A-Z2-9]{6}$/.test(code)) {
          return json({ error: "bad code" }, 400);
        }
        const cutoff = new Date(Date.now() - PAIR_TTL_MS).toISOString();
        const burn = await env.DB.prepare(
          "UPDATE pairing SET used = 1 WHERE code = ? AND used = 0 AND at >= ?"
        ).bind(code, cutoff).run();
        if (!burn.meta.changes) return json({ error: "that code is used or expired" }, 400);
        return json({ token: env.VIEW_TOKEN });
      }

      // Liveness, unauthenticated on purpose: it must be checkable from anywhere
      // and it reveals only that the office exists and when it last heard from home.
      if (p === "/api/health") {
        const snap = await env.DB.prepare("SELECT at FROM snapshot WHERE id = 1").first();
        return json({ ok: true, snapshot_at: snap ? snap.at : null, server_time: nowIso() });
      }

      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: String(err && err.message ? err.message : err) }, 500);
    }
  },
};

function safeParse(s) {
  try { return JSON.parse(s || "{}"); } catch { return {}; }
}
