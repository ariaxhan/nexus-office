/**
 * nexus-office Worker.
 *
 * It is deliberately dumb. It holds two things and no credentials:
 *   - the latest world snapshot (what the office looks like right now)
 *   - a queue of decisions someone made in the browser
 *
 * The local runner is the only process on earth that can act on GitHub. That is
 * the whole security model: a stolen view token lets someone queue an intent,
 * never execute one, and the runner re-checks every intent against the real repo
 * before touching it.
 */

const KINDS = new Set(["comment", "unblock", "close", "reopen", "label", "nudge"]);

const LOCKOUT_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 8;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });

/**
 * Compare without leaking where two strings diverge.
 *
 * The early `length` return is deliberate and safe for tokens, which are fixed
 * width. It is NOT safe for a password, so the loop runs over the longer of the
 * two either way and folds any length difference into the result rather than
 * returning on it.
 */
function tokenEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const n = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < n; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
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

      // ---- decision: somebody clicked something ----------------------------
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

      // ---- the front door ---------------------------------------------------
      //
      // One password, exchanged once for the view token the rest of the API uses.
      // The token is an implementation detail nobody should have to think about:
      // whoever is looking at this office types a password like they would
      // anywhere else, and the browser remembers the rest.
      if (p === "/api/login" && req.method === "POST") {
        const secret = env.PASSWORD || "";
        if (!secret) {
          return json({ error: "no password is set on this office yet" }, 503);
        }
        const ip = req.headers.get("cf-connecting-ip") || "unknown";
        const since = new Date(Date.now() - LOCKOUT_MS).toISOString();

        await env.DB.prepare("DELETE FROM login_attempt WHERE at < ?").bind(since).run();
        const failed = await env.DB.prepare(
          "SELECT COUNT(*) AS n FROM login_attempt WHERE ip = ? AND at >= ?"
        ).bind(ip, since).first();
        if (failed && failed.n >= MAX_ATTEMPTS) {
          return json({ error: "too many attempts, try again in fifteen minutes" }, 429);
        }

        const { password } = await readJson(req, 4096);
        if (typeof password !== "string" || !tokenEq(password, secret)) {
          await env.DB.prepare("INSERT INTO login_attempt (ip, at) VALUES (?, ?)")
            .bind(ip, nowIso()).run();
          return json({ error: "that is not the password" }, 401);
        }
        await env.DB.prepare("DELETE FROM login_attempt WHERE ip = ?").bind(ip).run();
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
