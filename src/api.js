/**
 * Talking to the office Worker.
 *
 * The view token lives in localStorage, bootstrapped once from the URL fragment
 * (`#k=...`) so the link Aria opens on a phone works and then cleans itself out
 * of the address bar. A fragment never reaches the server, so it never lands in
 * an access log the way a query string would.
 */

const KEY = "nexus-office-token";

export function bootstrapToken() {
  const m = /[#&]k=([A-Za-z0-9]+)/.exec(location.hash || "");
  if (m) {
    localStorage.setItem(KEY, m[1]);
    history.replaceState(null, "", location.pathname + location.search);
  }
  return localStorage.getItem(KEY) || "";
}

export function clearToken() {
  localStorage.removeItem(KEY);
}

function headers() {
  return {
    authorization: `Bearer ${localStorage.getItem(KEY) || ""}`,
    "content-type": "application/json",
  };
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
