/**
 * Talking to the office.
 *
 * The office is served from the machine it runs on, at the same origin as this
 * page, so there is nothing to log in to and no token to hold. The bind address
 * is the whole door: reaching the port at all means being on that machine.
 */

function headers() {
  return { "content-type": "application/json" };
}

export async function getWorld() {
  const r = await fetch("/api/world", { headers: headers(), cache: "no-store" });
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
  // A decision is applied on the spot now, so the answer carries what happened
  // rather than a queue position. A refusal is the server's own words: 409 in
  // particular means the gate moved on, which is never a failure to reach it.
  if (!r.ok) throw new Error(body.error || body.result || `decision: ${r.status}`);
  return body;
}
