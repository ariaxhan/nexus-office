// The shadow's own rules (#310): an independent re-implementation of what the Studio
// decides, so the comparator can ask "would the shadow have done the same thing?".
// Pure: no bindings, no clock. Studio stays authoritative; a MISMATCH is data, never a fix.

export const SCHEMA = "nexus.shadow.record/v1";
export const NAMESPACES = ["office.dispatch", "office.fault"];
const MAX_ATTEMPTS = 3; // nexus/dispatch.py MAX_ATTEMPTS

// nexus/dispatch.py TRANSITIONS, restated, not imported: drift is what parity exists to catch.
const DISPATCH_ARROWS = new Set([
  "filed>claimed", "claimed>claimed", "claimed>receipt", "receipt>verified", "receipt>filed",
  "verified>closed", "filed>escalated", "claimed>escalated", "receipt>escalated",
]);

// nexus/faults.py record() arrows. mechanism-stopped faults escalate without a repair.
const FAULT_ARROWS = new Set([
  "detected>repairing", "detected>recovered", "repairing>repairing", "repairing>verified_fixed",
  "repairing>escalated", "repairing>recovered", "detected>verified_fixed", "detected>escalated",
]);

// What the Studio poller may legally do next from one record state. A set, because the
// choice between some branches rests on facts outside the record (a registered worker,
// elapsed time); the shadow names every outcome its rules allow and nothing else.
export function decide(input) {
  const { state, kind, attempts = 0, receipt_outcome: outcome = null } = input;
  const commitment = kind === "commitment";
  if (state === "filed") {
    if (commitment) return ["claim_owner"];
    return attempts >= MAX_ATTEMPTS ? ["escalate:hands"] : ["dispatch", "escalate:config"];
  }
  if (state === "claimed") {
    if (commitment) return ["receipt", "escalate:hands"];
    const out = ["launched", "receipt", "escalate:hands"];
    if (attempts < MAX_ATTEMPTS) out.push("redispatch");
    return out;
  }
  if (state === "receipt") {
    if (outcome === "blocked") return ["escalate:decision"];
    if (outcome === "done") return ["verify_pass", "refile"];
    if (outcome) return ["refile"];
    return ["verify_pass", "refile", "escalate:decision"]; // receipt cleared before the shadow saw it
  }
  if (state === "verified") return ["close"];
  return []; // closed and escalated are terminal
}

// Replays a dispatch record's transitions into decision inputs, then judges each step.
export function judgeDispatch(record, transitions) {
  const pairs = [];
  let attempts = 0;
  for (const t of transitions) {
    const input = { state: t.from, kind: record.kind, attempts,
      receipt_outcome: t.from === "receipt" ? receiptOutcome(record, t, transitions) : null };
    if (t.from === null) { // creation
      pairs.push(pair("dispatch", t, t.studio_outcome, ["filed"], t.to === "filed"));
      continue;
    }
    const allowed = decide(input);
    pairs.push(pair("dispatch", t, t.studio_outcome, allowed, DISPATCH_ARROWS.has(`${t.from}>${t.to}`)
      && dispatchFieldsHold(record, t, transitions)));
    if (Number.isInteger(t.attempts)) attempts = t.attempts;
  }
  return pairs;
}

function receiptOutcome(record, t, transitions) {
  // The snapshot holds the latest receipt only; it answers this step if no later receipt replaced it.
  if (!record.receipt || transitions.some((x) => x.seq > t.seq && x.to === "receipt")) return null;
  return record.receipt.outcome;
}

// The trigger rules the Studio ledger enforces, checked on the snapshot when it is the
// state this step arrived in.
function dispatchFieldsHold(record, t, transitions) {
  const final = t === transitions[transitions.length - 1];
  if (!final) return true;
  if (["receipt", "verified", "closed"].includes(t.to)
      && !(record.receipt && record.receipt.outcome && record.receipt.verification)) return false;
  if (["verified", "closed"].includes(t.to)
      && !(record.verification?.result === "pass" && record.receipt?.outcome === "done")) return false;
  if (t.to === "escalated" && !record.escalation?.act_needed) return false;
  return true;
}

// A fault revision against the previous one the shadow holds for it.
export function judgeFault(prev, record, seq) {
  const t = { seq, from: prev ? prev.state : null, to: record.state };
  let ok = !!record.fault_id && !!record.signature
    && (!prev || prev.state === record.state || FAULT_ARROWS.has(`${prev.state}>${record.state}`));
  if (record.state === "verified_fixed")
    ok = ok && ["pass", "released"].includes(record.verification?.result);
  if (record.state === "escalated") ok = ok && !!record.escalation?.act_needed;
  if (record.state === "repairing") ok = ok && !!record.repair;
  return pair("fault", t, "allow", ["allow"], ok);
}

function pair(lens, t, studio, allowed, legal) {
  return {
    lens, seq: t.seq, from: t.from, to: t.to,
    studio_outcome: studio, shadow_outcome: allowed,
    studio_verdict: "allow", shadow_verdict: legal ? "allow" : "deny",
    match: allowed.includes(studio) && legal,
  };
}

export async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// One envelope's shape, refused before anything is stored.
export function invalid(env) {
  if (!env || env.schema !== SCHEMA) return "schema";
  if (!NAMESPACES.includes(env.namespace)) return "namespace";
  for (const k of ["event_id", "record_id", "revision", "record_json", "sha256", "origin"])
    if (typeof env[k] !== "string" || !env[k]) return k;
  if (env.event_id !== `${env.namespace}:${env.record_id}:${env.revision}`) return "event_id";
  if (typeof env.observed_at !== "number") return "observed_at";
  if (!Array.isArray(env.transitions)) return "transitions";
  return null;
}
