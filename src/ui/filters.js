/**
 * Which desks are in the room right now.
 *
 * Seventy one desks is the honest number and it is also too many to look at when
 * you care about four of them. This decides what gets built, so a hidden desk is
 * genuinely absent from the layout and the room re-packs around what is left.
 *
 * Two rules it must never break:
 *
 *   1. Hiding is a VIEW. It never touches what the runner does. A hidden repo is
 *      still worked, still surveyed, still filed against.
 *   2. Hidden is never silent. The count is always on screen and always one click
 *      from coming back, because a desk that vanished with no trace is
 *      indistinguishable from a desk that stopped existing.
 */

const KEY = "nexus-office-view";

const DEFAULTS = { mode: "all", repos: [], owners: [] };

export const MODES = [
  { id: "all", label: "all" },
  { id: "needs", label: "needs me" },
  { id: "today", label: "active today" },
];

// Outcomes that describe the RUN rather than the repo. A desk whose only news is
// "not reached this run" was not active today in any sense a person means it.
const IDLE_OUTCOMES = new Set(["survey", "deferred", "caught-up", "no-issues", "dry-run"]);

export function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      mode: MODES.some((m) => m.id === raw.mode) ? raw.mode : DEFAULTS.mode,
      repos: Array.isArray(raw.repos) ? raw.repos : [],
      owners: Array.isArray(raw.owners) ? raw.owners : [],
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function save(view) {
  try {
    localStorage.setItem(KEY, JSON.stringify(view));
  } catch {
    // A private window with storage disabled still gets a working room; it just
    // forgets the view on reload. That is a worse experience, not a broken one.
  }
}

const ownerOf = (repo) => String(repo).split("/")[0];

function activeToday(station) {
  if (!IDLE_OUTCOMES.has(station.outcome)) return true;
  // Fall back to the runs list: a repo can have been genuinely worked earlier in
  // the day and have a quiet headline by the time we look.
  return (station.runs || []).some((r) => !IDLE_OUTCOMES.has(r.outcome));
}

/**
 * Split the world. Returns what to build and, separately, what was withheld and
 * why, because the caller has to be able to say so out loud.
 */
export function apply(stations, view, needsHuman) {
  const shown = [];
  const hiddenByMode = [];
  const hiddenByHand = [];

  for (const s of stations) {
    if (view.repos.includes(s.repo) || view.owners.includes(ownerOf(s.repo))) {
      hiddenByHand.push(s);
      continue;
    }
    if (view.mode === "needs" && !(s.issues || []).some(needsHuman)) {
      hiddenByMode.push(s);
      continue;
    }
    if (view.mode === "today" && !activeToday(s)) {
      hiddenByMode.push(s);
      continue;
    }
    shown.push(s);
  }

  // A repo you put away that starts needing you is the one case where hiding
  // could genuinely cost something, so it is counted and reported separately.
  const waitingButHidden = hiddenByHand.filter(
    (s) => (s.issues || []).some(needsHuman)
  ).length;

  return { shown, hiddenByHand, hiddenByMode, waitingButHidden };
}

export function hideRepo(view, repo) {
  return { ...view, repos: [...new Set([...view.repos, repo])] };
}

export function hideOwner(view, repo) {
  return { ...view, owners: [...new Set([...view.owners, ownerOf(repo)])] };
}

export function showEverything(view) {
  return { ...view, mode: "all", repos: [], owners: [] };
}

export { ownerOf };
