/**
 * Talking to the office Worker.
 *
 * Whoever opens this types a password, once, and the browser keeps a session
 * token from then on. The token is an implementation detail: it is never shown,
 * never pasted, and never has to be understood by the person using the office.
 */

const KEY = "nexus-office-token";

export function token() {
  return localStorage.getItem(KEY) || "";
}

export function clearToken() {
  localStorage.removeItem(KEY);
}

/** Trade the password for a session. Throws with a readable reason if it fails. */
export async function login(password) {
  const r = await fetch("/api/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok || !body.token) throw new Error(body.error || `login failed (${r.status})`);
  localStorage.setItem(KEY, body.token);
}

function headers() {
  return { authorization: `Bearer ${token()}`, "content-type": "application/json" };
}

export async function getWorld() {
  const r = await fetch("/api/world", { headers: headers(), cache: "no-store" });
  if (r.status === 401) throw Object.assign(new Error("unauthorized"), { unauthorized: true });
  if (!r.ok) throw new Error(`world: ${r.status}`);
  return r.json();
}

export async function sendDecision(d) {
  const r = await fetch("/api/decision", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(d),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `decision: ${r.status}`);
  return body;
}
