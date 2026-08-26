"""Whether the default branch of each desk's repo actually builds.

The runner opens PRs into these repos all day. A red default branch is the most
expensive thing not to notice, because nothing stops: the PRs keep stacking up
and every one of them is built on a broken base.

WHY THIS IS ONE QUERY AND NOT SEVENTY
-------------------------------------
Seventy repos times one REST call each, every ten minutes, is four hundred and
twenty calls an hour and a rate limit by lunchtime. So the head of each default
branch is asked for through GraphQL, aliased, `CHUNK` repos at a time: seventy
repos is two calls per push. The answer is then cached on disk for `ttl()`
seconds, so a push that comes round early spends nothing at all.

Because it is cached, the payload carries `fetched_at` and every row carries
`checked_at`. A green light with no age on it is the false-green this project
exists to kill.

`checked_at` comes from the check runs themselves and is null when none of them
reported a completion time. The commit's own date travels separately as
`commit_at` and is NEVER substituted: a commit time shown as a check time is an
estimate with a measurement's face on.

THE STATES, AND WHY THERE ARE SIX
---------------------------------
  passing  the rollup says success
  failing  the rollup says failure or error. The alarm
  never    there are workflow files, and the head commit has no rollup at all.
           Usually a workflow that is filtered out of every branch it should
           run on, which is a different afternoon from a build that broke
  none     no workflow files. A repo with no CI is a DECISION, never a fault,
           and dressing it as one is how a docs repo goes permanently red
  running  the rollup is pending. Folding an in-flight run into green or red
           is a lie either way, and it is the one that resolves itself
  unknown  the repo could not be read: no token we hold can see it, or GraphQL
           refused. "We did not look" must never render as "it is fine"

This runs inside the snapshot push on the laptop, which is the only place a
GitHub credential exists. It can never move into the browser.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

KEY = "ci"

# sections.read_all() hands the desk list to any source that asks for it. This
# is the only source that needs to know what the room is showing.
NEEDS_REPOS = True

# Forty aliases in one query. GitHub costs a GraphQL call by the nodes it
# returns rather than by the aliases in it, and forty repos' worth of rollup
# comes back well inside the point limit while keeping the document small
# enough to read in a log line.
CHUNK = 40

# The whole push has to finish. Two chunks at twenty-five seconds is still
# inside what a ten minute cadence can afford, and a hang is reported as a hang.
TIMEOUT_S = 25

DEFAULT_TTL_S = 600

# Alongside office-sync's own access cache, for the same reason: it is state,
# not configuration, and it must never land in the repo.
STATE = pathlib.Path.home() / ".local/state/nexus-office"


def cache_path() -> pathlib.Path:
    v = os.environ.get("OFFICE_CI_CACHE", "").strip()
    return pathlib.Path(v).expanduser() if v else STATE / "ci.json"


def ttl() -> int:
    try:
        return max(0, int(os.environ.get("OFFICE_CI_TTL_S", "").strip() or DEFAULT_TTL_S))
    except ValueError:
        return DEFAULT_TTL_S


# ── the query ────────────────────────────────────────────────────────────────

# One alias per repo. `object(expression: "HEAD:.github/workflows")` is what
# tells "no CI configured" apart from "CI configured and never ran", and those
# two have nothing in common except that neither is green.
_FRAGMENT = """
  r%d: repository(owner: %s, name: %s) {
    nameWithOwner
    defaultBranchRef {
      name
      target {
        ... on Commit {
          oid
          committedDate
          statusCheckRollup {
            state
            contexts(first: 30) {
              nodes {
                ... on CheckRun {
                  name conclusion status completedAt detailsUrl
                }
                ... on StatusContext {
                  context state targetUrl createdAt
                }
              }
            }
          }
        }
      }
    }
    workflows: object(expression: "HEAD:.github/workflows") {
      ... on Tree { entries { name } }
    }
  }
"""


def query_for(chunk: list[str]) -> str:
    parts = []
    for i, nwo in enumerate(chunk):
        owner, _, name = nwo.partition("/")
        parts.append(_FRAGMENT % (i, json.dumps(owner), json.dumps(name)))
    return "query {" + "".join(parts) + "}"


# ── reading one repo's answer ────────────────────────────────────────────────

# A check run that ended in any of these is a job somebody has to look at.
# `cancelled` is in here on purpose: a cancelled required job blocks a merge
# exactly as hard as a failed one, and calling it "not a failure" is how a
# branch sits red for a week with nobody assigned to it.
BAD_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
BAD_STATUSES = {"FAILURE", "ERROR"}

ROLLUP = {
    "SUCCESS": "passing",
    "FAILURE": "failing",
    "ERROR": "failing",
    "PENDING": "running",
    "EXPECTED": "running",
}


def _contexts(rollup: dict) -> list[dict]:
    nodes = ((rollup or {}).get("contexts") or {}).get("nodes") or []
    return [n for n in nodes if isinstance(n, dict) and n]


def _jobs(rollup: dict) -> tuple[list[dict], str | None]:
    """The failing jobs by name and link, plus the newest completion time seen.

    Both halves come out of the same walk because they read the same nodes, and
    the time is the honest age of the check rather than of the commit.
    """
    failing = []
    latest = None
    for n in _contexts(rollup):
        # A CheckRun carries `name`; a StatusContext carries `context`. The two
        # shapes arrive interleaved in one list and neither is optional.
        if "context" in n:
            name = str(n.get("context") or "")
            bad = str(n.get("state") or "").upper() in BAD_STATUSES
            url = n.get("targetUrl") or ""
            at = n.get("createdAt")
        else:
            name = str(n.get("name") or "")
            bad = str(n.get("conclusion") or "").upper() in BAD_CONCLUSIONS
            url = n.get("detailsUrl") or ""
            at = n.get("completedAt")
        if at and (latest is None or str(at) > latest):
            latest = str(at)
        if bad:
            failing.append({"name": name[:120], "url": str(url or "")[:400]})
    return failing[:20], latest


def _has_workflows(node: dict) -> bool:
    entries = (node or {}).get("entries")
    if not isinstance(entries, list):
        return False
    return any(str(e.get("name") or "").endswith((".yml", ".yaml")) for e in entries)


def row(nwo: str, node: dict | None, why: str = "") -> dict:
    """One desk's line, from one repository node. `node` None means we could not look."""
    base = {"repo": nwo, "ci": "unknown", "branch": "", "detail": why[:200],
            "failing": [], "checked_at": None, "commit_at": None, "run_url": ""}
    if not node:
        base["detail"] = why[:200] or "the repository could not be read with the token we hold"
        return base

    ref = node.get("defaultBranchRef") or {}
    base["branch"] = str(ref.get("name") or "")
    target = ref.get("target") or {}
    base["commit_at"] = target.get("committedDate")

    rollup = target.get("statusCheckRollup")
    workflows = _has_workflows(node.get("workflows"))

    if not ref:
        base["detail"] = "the repository has no default branch, so there is nothing to build"
        base["ci"] = "none"
        return base

    if not rollup:
        base["ci"] = "never" if workflows else "none"
        base["detail"] = ("there are workflow files, and the head of the default branch "
                          "has no check on it at all"
                          if workflows else "no workflow files, so nothing here builds anything")
        return base

    failing, checked_at = _jobs(rollup)
    base["failing"] = failing
    base["checked_at"] = checked_at
    base["ci"] = ROLLUP.get(str(rollup.get("state") or "").upper(), "unknown")
    if base["ci"] == "unknown":
        base["detail"] = f"the check rollup said {rollup.get('state')!r}, which nothing here knows"
    if failing:
        base["run_url"] = failing[0]["url"]
        base["detail"] = (f"{len(failing)} job{'' if len(failing) == 1 else 's'} "
                          f"failing on {base['branch'] or 'the default branch'}")
    return base


# ── asking GitHub ────────────────────────────────────────────────────────────

def _gh(chunk: list[str]) -> tuple[dict, str]:
    """One GraphQL call. Returns (data-by-alias, error). Both can be non-empty:
    GraphQL answers a partly-unreadable query with data AND errors, and throwing
    the readable half away because one repo is private would be silly."""
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", "query=" + query_for(chunk)],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {}, f"gh did not answer in {TIMEOUT_S}s"
    except FileNotFoundError:
        return {}, "there is no gh on this machine to ask"
    except OSError as exc:
        return {}, f"{type(exc).__name__}: {exc}"[:200]

    try:
        body = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:200]
        return {}, detail or "gh printed nothing that parsed"

    data = body.get("data") or {}
    errs = body.get("errors") or []
    msg = "; ".join(str(e.get("message") or "")[:120] for e in errs[:3])
    if not data and not msg:
        msg = "gh answered with neither data nor an error"
    return data, msg


def fetch(repos: list[str]) -> tuple[list[dict], str]:
    rows = []
    problems = []
    for i in range(0, len(repos), CHUNK):
        chunk = repos[i:i + CHUNK]
        data, err = _gh(chunk)
        if err:
            problems.append(err)
        if not data:
            # The whole chunk is unreadable. Every desk in it says so, rather
            # than quietly not appearing on the board.
            rows.extend(row(nwo, None, err) for nwo in chunk)
            continue
        for n, nwo in enumerate(chunk):
            rows.append(row(nwo, data.get(f"r{n}"), err))
    return rows, "; ".join(problems)[:300]


# ── the cache ────────────────────────────────────────────────────────────────

def load_cache() -> dict | None:
    try:
        body = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) and isinstance(body.get("rows"), list) else None


def save_cache(body: dict) -> None:
    try:
        p = cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body), encoding="utf-8")
    except OSError:
        # A cache that cannot be written costs an API call, not a snapshot.
        pass


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ── the section ──────────────────────────────────────────────────────────────

STATES = ("failing", "never", "running", "passing", "none", "unknown")

# Faults first, then the ones nobody has to do anything about. `none` is not an
# alarm and never leads.
ORDER = {"failing": 0, "unknown": 1, "never": 2, "running": 3, "passing": 4, "none": 5}


def summarise(rows: list[dict], fetched: float, state: str, detail: str) -> dict:
    counts = {k: 0 for k in STATES}
    for r in rows:
        counts[r["ci"]] = counts.get(r["ci"], 0) + 1
    rows = sorted(rows, key=lambda r: (ORDER.get(r["ci"], 9), r["repo"]))
    return {
        "state": state,
        "detail": detail[:300],
        "checked": len(rows),
        "counts": counts,
        # `unknown` counts as an alarm. Not looking is not the same as being
        # fine, and only one of those two deserves a quiet room.
        "alarm": counts["failing"] + counts["never"] + counts["unknown"],
        "fetched_at": _iso(fetched),
        "age_s": max(0, int(time.time() - fetched)),
        "ttl_s": ttl(),
        "repos": rows,
    }


def read(repos=()) -> dict:
    repos = sorted({r for r in (repos or []) if isinstance(r, str) and "/" in r})
    if not repos:
        return {"state": "no-desks",
                "detail": "there are no repos in the snapshot, so there is no build to watch"}

    cached = load_cache()
    now = time.time()
    if cached and now - float(cached.get("at") or 0) < ttl() \
            and sorted(r["repo"] for r in cached["rows"]) == repos:
        return summarise(cached["rows"], float(cached["at"]), "ok", "")

    rows, err = fetch(repos)
    unreadable = all(r["ci"] == "unknown" for r in rows)
    if unreadable and cached:
        # The refresh failed outright and we still hold a board. Showing the old
        # one WITH its age and the reason beats showing nothing: the age is
        # already on every row, so nobody can mistake it for fresh.
        return summarise(cached["rows"], float(cached.get("at") or 0), "cached", err)
    if unreadable:
        return {"state": "unreadable",
                "detail": err[:300] or "nothing could be read about any repo"}

    save_cache({"at": now, "rows": rows})
    return summarise(rows, now, "ok", err)
