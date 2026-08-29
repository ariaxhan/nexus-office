"""Build the office's world, and apply what somebody clicked in it.

This is a library now. `client/serve.py` is the only caller: it serves the room
on this machine, builds the snapshot in process, and applies each decision the
moment it arrives instead of queueing it for a drain.

Everything that touches GitHub happens here, on the machine that already has a
`gh` login, and every intent is re-derived from its own fields and re-probed for
push access before it is acted on. The server binds loopback only, so the door is
the machine; this file is the second lock behind that door.

Configuration, all from the environment so nothing personal lives in this file:

  OFFICE_RECEIPTS   a JSONL of pipeline receipts         (optional)
  OFFICE_OWNERS     comma separated GitHub owners        (optional; used when
                    there is no receipts file, so every repo you can push to
                    under those owners gets a desk)
  OFFICE_HEARTBEAT  file holding your runner's last-success stamp  (optional)
  OFFICE_KILLSWITCH file whose existence means "the runner is halted" (optional)
  OFFICE_MARKER     the comment marker your bot leaves    (default: pipeline-bot)
  OFFICE_WAITING    the label meaning a human must look   (default: waiting on human)
  OFFICE_RUNTIME_ROOT  a local agent runtime's repo root   (optional; enables gates)
  OFFICE_RUNTIME_URL   that runtime's dashboard            (default 127.0.0.1:8787)
  OFFICE_GH_RESERVE the GraphQL points to leave unspent    (default 1000)

Two files on disk under ~/.local/state/nexus-office hold what the office knows
between builds: `desks.json` is the last good answer for every desk, so GitHub
saying no leaves a stale room rather than an empty one, and `hidden.json` is the
desks you put away, which are never fetched at all.

A receipt is one JSON object per line:

  {"at": "2026-08-25T23:14:38Z", "repo": "owner/name",
   "issue": "42", "outcome": "landed", "detail": "opened a PR"}

Outcome is free text. The values in NOISE below describe a RUN rather than a
repo, and are skipped when picking the headline state for a desk.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import runtime as rt  # noqa: E402  (needs the path above)
import automation  # noqa: E402  (needs the path above)
import sections as sections_mod  # noqa: E402  (needs the path above)

def _env_path(name):
    v = os.environ.get(name, "").strip()
    return pathlib.Path(v).expanduser() if v else None


RECEIPTS = _env_path("OFFICE_RECEIPTS")
HEARTBEAT = _env_path("OFFICE_HEARTBEAT")
KILLSWITCH = _env_path("OFFICE_KILLSWITCH")
OWNERS = [o.strip() for o in os.environ.get("OFFICE_OWNERS", "").split(",") if o.strip()]

STATE = pathlib.Path.home() / ".local/state/nexus-office"
ACCESS_CACHE = STATE / "access.json"
ACCESS_TTL = 6 * 3600

BOT_MARKER = os.environ.get("OFFICE_MARKER", "pipeline-bot")
WAITING_LABEL = os.environ.get("OFFICE_WAITING", "waiting on human")

# THE SAFETY BOUNDARY FOR MERGING.
#
# The office may merge a PR the PIPELINE opened, and nothing else. Anything that
# reaches the local port can ask for any intent it likes; this is the line that
# decides which of them can touch code. Every branch the runner creates starts
# with this prefix, so a PR whose head does not is somebody's own work and is
# refused here, after re-reading the PR from GitHub. The caller's claim about a
# PR is never trusted: only this check counts.
PR_PREFIX = os.environ.get("OFFICE_PR_PREFIX", "pipeline/")


def _pipeline_branch(head: str) -> bool:
    """Whether this branch is inside the configured merge boundary.

    An explicitly empty or whitespace-only prefix fails closed. Python treats
    every string as starting with ``""``, which must never become permission.
    """
    return bool(PR_PREFIX and PR_PREFIX.strip()) and head.startswith(PR_PREFIX)

NOW = datetime.now(timezone.utc)
ISO = "%Y-%m-%dT%H:%M:%SZ"


def log(msg: str) -> None:
    print(f"[office-sync] {msg}", flush=True)


def sh(cmd, timeout=45, env=None, check=False):
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"exit {p.returncode}")
    return p.returncode, p.stdout, p.stderr


# ── who can act where ────────────────────────────────────────────────────────
# Exactly the runner's rule, restated: an account is chosen by TRYING to push,
# never by owning. If this drifted from dispatch.sh the office would show desks
# the runner cannot sit at, so the probe is the same probe.

def logins() -> list[str]:
    rc, out, err = sh(["gh", "auth", "status"], timeout=20)
    text = out + err
    return re.findall(r"Logged in to \S+ account (\S+)", text)


class Access:
    def __init__(self):
        self.mine = logins()
        self.cache = {}
        try:
            raw = json.loads(ACCESS_CACHE.read_text())
            if time.time() - raw.get("at", 0) < ACCESS_TTL:
                self.cache = raw.get("repos", {})
        except Exception:
            pass
        self.dirty = False

    def token_for(self, nwo: str):
        """(login, token) that can push to nwo, or (None, None)."""
        hit = self.cache.get(nwo)
        if hit is not None:
            who = hit or None
            return (who, self._token(who)) if who else (None, None)

        owner = nwo.split("/")[0]
        order = ([owner] if owner in self.mine else []) + [m for m in self.mine if m != owner]
        for who in order:
            tok = self._token(who)
            if not tok:
                continue
            rc, out, _ = sh(["gh", "api", f"repos/{nwo}", "--jq", ".permissions.push // false"],
                            timeout=25, env={"GH_TOKEN": tok})
            if rc == 0 and out.strip() == "true":
                self.cache[nwo] = who
                self.dirty = True
                return who, tok
        self.cache[nwo] = ""
        self.dirty = True
        return None, None

    def _token(self, who):
        if not who:
            return ""
        rc, out, _ = sh(["gh", "auth", "token", "--user", who], timeout=10)
        return out.strip() if rc == 0 else ""

    def save(self):
        if not self.dirty:
            return
        STATE.mkdir(parents=True, exist_ok=True)
        ACCESS_CACHE.write_text(json.dumps({"at": time.time(), "repos": self.cache}))


# ── what the pipeline did ────────────────────────────────────────────────────

def discover_from_github():
    """No receipts file? Then every repo you can push to under OFFICE_OWNERS gets
    a desk, and its most recent activity is whatever GitHub last saw."""
    if not OWNERS:
        return {}
    out = {}
    for owner in OWNERS:
        rc, body, _ = sh(["gh", "repo", "list", owner, "--limit", "200",
                          "--json", "nameWithOwner,pushedAt"], timeout=60)
        if rc != 0:
            log(f"could not list repos for {owner}")
            continue
        try:
            for r in json.loads(body):
                out[r["nameWithOwner"]] = [{
                    "at": (r.get("pushedAt") or "").replace(".000Z", "Z"),
                    "repo": r["nameWithOwner"], "issue": "",
                    "outcome": "survey", "detail": "discovered on GitHub",
                }]
        except Exception:
            continue
    return out


def receipts():
    """Group the runner's own receipts by repo. This file, not a repo walk, is the
    list of desks: a repo the runner never reached has no desk, and a repo it
    reached has one whether or not it lives under this checkout."""
    if not RECEIPTS:
        return discover_from_github(), {}
    rows = []
    try:
        for line in RECEIPTS.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        return {}, {}

    # A rolling 24 hours, not the UTC calendar day. "Landed today" that resets to
    # zero at five in the afternoon local time is a number that lies twice a day.
    since = (NOW - timedelta(hours=24)).strftime(ISO)
    counts = {}
    by_repo = {}
    for r in rows:
        repo = r.get("repo") or ""
        if "/" not in repo:
            continue
        if (r.get("at") or "") >= since:
            counts[r.get("outcome", "?")] = counts.get(r.get("outcome", "?"), 0) + 1
        by_repo.setdefault(repo, []).append(r)

    for repo, rs in by_repo.items():
        rs.sort(key=lambda x: x.get("at", ""), reverse=True)
        del rs[24:]
    return by_repo, counts


# The outcomes that describe the RUN rather than the repo. A desk whose last
# receipt is "5 issues not reached this run" is a working desk, not a special one.
NOISE = {"survey", "deferred", "dry-run", "caught-up"}


def headline(runs):
    for r in runs:
        if r.get("outcome") not in NOISE:
            return r
    return runs[0] if runs else {}


# ── put away desks ───────────────────────────────────────────────────────────
# A desk you put away is still a desk. It is never fetched, so it costs nothing
# against the hour's budget, and it still carries the last data we saw so the
# app can list it and bring it back. Hiding is a fact about this screen, never a
# fact about the repo, so it lives here and not on GitHub.

HIDDEN_FILE = STATE / "hidden.json"
DESKS_CACHE = STATE / "desks.json"
# The desks a person dragged to the top, in the order they dragged them. Like
# hiding, a fact about this screen and never about the repo. Order is the whole
# content of the file, so it is a list and never a set.
PINS_FILE = STATE / "pins.json"

# The one shape a repository name is allowed to have. Every name that reaches
# GitHub passes this first. The batch query names repos in GraphQL VARIABLES
# rather than in the query text, so this is the belt to that pair of braces: a
# repo called `a) { viewer { login } } #` is a 404, never a second query.
NWO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)


def read_hidden() -> list:
    """The repos put away, sorted. Never raises: an unreadable list means none."""
    try:
        raw = json.loads(HIDDEN_FILE.read_text())
    except Exception:
        return []
    rows = raw.get("repos") if isinstance(raw, dict) else None
    return sorted({r for r in (rows or []) if isinstance(r, str) and NWO_RE.match(r)})


_HIDDEN_LOCK = threading.Lock()


def write_hidden(repos) -> list:
    """Written whole and moved into place: a torn file reads as "nothing put
    away", which would quietly return every desk to the floor and the bill."""
    keep = sorted({r for r in repos if isinstance(r, str) and NWO_RE.match(r)})
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = HIDDEN_FILE.with_name(HIDDEN_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"repos": keep}, indent=1))
    tmp.replace(HIDDEN_FILE)
    return keep


def set_hidden(nwo: str, hidden: bool) -> list:
    """Put a desk away, or bring it back. Returns the whole list afterwards.

    Hiding a repo nobody has seen yet is allowed on purpose: a desk appears the
    first time the runner touches it, and "I do not want that one" has to be
    sayable before it turns up. Unhiding one that was never hidden is a no-op.
    """
    if not NWO_RE.match(nwo or ""):
        raise ValueError("bad repo")
    with _HIDDEN_LOCK:
        now = set(read_hidden())
        now.add(nwo) if hidden else now.discard(nwo)
        return write_hidden(now)


def read_pins() -> list:
    """The pinned repos, in pin order. Never raises: an unreadable list means none."""
    try:
        raw = json.loads(PINS_FILE.read_text())
    except Exception:
        return []
    rows = raw.get("repos") if isinstance(raw, dict) else None
    out = []
    for r in rows or []:
        if isinstance(r, str) and NWO_RE.match(r) and r not in out:
            out.append(r)
    return out


_PINS_LOCK = threading.Lock()


def write_pins(repos) -> list:
    """Replace the whole pin order. Same torn-file rule as `write_hidden`: written
    whole and moved into place, so a crash mid-write reads as "no pins" and not
    as half an order.

    A repo with no desk yet is kept on purpose, the way `set_hidden` keeps one:
    a pin outlives the desk losing its receipts for a day, and comes back with it.
    A malformed name is dropped rather than refused, because the file is the
    order and one bad entry must not lose the other nine.
    """
    keep = []
    for r in repos or []:
        if isinstance(r, str) and NWO_RE.match(r) and r not in keep:
            keep.append(r)
    with _PINS_LOCK:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = PINS_FILE.with_name(PINS_FILE.name + ".tmp")
        tmp.write_text(json.dumps({"repos": keep}, indent=1))
        tmp.replace(PINS_FILE)
    return keep


# ── last known good ──────────────────────────────────────────────────────────
# GitHub saying no must never blank the room. Every desk that answered is
# written here, and a desk that could not be reached shows what it showed last,
# stamped with when that was, next to one line saying what went wrong. A blank
# office is indistinguishable from an office with nothing to do, and only one of
# those two is ever true at four in the morning.

def read_desks() -> dict:
    try:
        raw = json.loads(DESKS_CACHE.read_text())
    except Exception:
        return {}
    repos = raw.get("repos") if isinstance(raw, dict) else None
    return dict(repos) if isinstance(repos, dict) else {}


def write_desks(repos: dict) -> None:
    """Written whole and moved into place, so a killed process leaves the last
    good file rather than half of a new one."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = DESKS_CACHE.with_name(DESKS_CACHE.name + ".tmp")
        tmp.write_text(json.dumps({"repos": repos}))
        tmp.replace(DESKS_CACHE)
    except Exception as exc:  # noqa: BLE001 - a cache that cannot be written is not a dead build
        log(f"could not write the desk cache: {exc}")


# ── the hour's budget ────────────────────────────────────────────────────────
# GitHub gives a user 5000 GraphQL points an hour. The office used to spend two
# queries per desk per minute, which is roughly seventy times the budget, and
# the room's answer to running out was to show every desk blank at once. So: one
# query per ten desks, the cost of every query read back from GitHub itself, and
# a hard stop with a reserve left over rather than a wall of errors.

GH_RESERVE = int(os.environ.get("OFFICE_GH_RESERVE", "") or 1000)
BATCH_SIZE = 10
PAUSE_FALLBACK_S = 15 * 60
# GitHub's own wording, matched loosely on purpose: "API rate limit exceeded for
# user ID ...", "You have exceeded a secondary rate limit". Both mean stop.
RATE_WORDS = "rate limit"

# What the last query said, and whether we have stopped asking. Module level
# because a pause outlives one build: it is a fact about the hour.
BUDGET = {"limit": None, "remaining": None, "reset_at": "", "paused_until": "", "error": ""}


def _parse_iso(s: str):
    try:
        return datetime.strptime(str(s), ISO).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def paused_until() -> str:
    """The stamp we are paused until, or "" when GitHub may be asked again."""
    until = BUDGET.get("paused_until") or ""
    if not until:
        return ""
    when = _parse_iso(until)
    if when is None or datetime.now(timezone.utc) >= when:
        BUDGET["paused_until"] = ""
        BUDGET["error"] = ""
        # The number that tripped the reserve is an hour old and nothing but a
        # query can replace it. Forget it, or the pause re-arms on it forever.
        BUDGET["remaining"] = None
        log(f"github: resuming, the pause to {until} is over")
        return ""
    return until


def pause(reason: str, until: str = "") -> str:
    when = _parse_iso(until) if until else None
    if when is None or when <= datetime.now(timezone.utc):
        when = datetime.now(timezone.utc) + timedelta(seconds=PAUSE_FALLBACK_S)
    stamp = when.strftime(ISO)
    if BUDGET.get("paused_until") != stamp:
        log(f"github: pausing until {stamp} ({str(reason)[:120]})")
    BUDGET["paused_until"] = stamp
    BUDGET["error"] = str(reason)[:200]
    return stamp


def note_rate(rate) -> int:
    """Fold one query's `rateLimit` block into the budget; return its cost."""
    if not isinstance(rate, dict):
        return 0
    for field in ("limit", "remaining"):
        v = rate.get(field)
        if isinstance(v, int):
            BUDGET[field] = v
    reset = str(rate.get("resetAt") or "")
    if reset:
        BUDGET["reset_at"] = reset.replace(".000Z", "Z")
    cost = rate.get("cost")
    return cost if isinstance(cost, int) else 0


# ── the issues and PRs on each desk ──────────────────────────────────────────
# One query for up to ten desks, not two queries per desk. The repos ride in as
# variables and the aliases r0..r9 come back in the same order, so a repo that is
# gone takes its own alias down and leaves the other nine standing.

DESK_FRAGMENT = """
fragment Desk on Repository {
  issues(first: 60, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes {
      number title body url updatedAt
      labels(first: 20) { nodes { name } }
      comments(last: 1) { nodes { body url createdAt } }
    }
  }
  pullRequests(first: 100, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes {
      number title headRefName baseRefName mergeable mergeStateStatus
      isDraft url body updatedAt
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

PR_PAGE_QUERY = """
query($o: String!, $n: String!, $after: String!) {
  rateLimit { limit cost remaining resetAt }
  r0: repository(owner: $o, name: $n) {
    pullRequests(first: 100, after: $after, states: OPEN,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title headRefName baseRefName mergeable mergeStateStatus
        isDraft url body updatedAt
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def batch_query(n: int) -> str:
    decl = ", ".join(f"$o{i}: String!, $n{i}: String!" for i in range(n))
    lines = "\n".join(f"  r{i}: repository(owner: $o{i}, name: $n{i}) {{ ...Desk }}"
                      for i in range(n))
    return (f"query({decl}) {{\n"
            f"  rateLimit {{ limit cost remaining resetAt }}\n"
            f"{lines}\n}}\n{DESK_FRAGMENT}")


def _bot_last_word(node) -> dict:
    """The bot's comment when it had the last word on this issue, else empty.

    THE rule, copied from dispatch.sh on purpose: the bot having the last
    word is what "waiting on a human" mechanically means. A label is a hint
    that can go stale; this cannot.

    `url` and `createdAt` ride along and cost NOTHING: they are scalar fields on
    a comment node the query already pays for, and GraphQL bills nodes. They are
    what turns "the pipeline commented on #284 an hour ago" into a link that
    lands on that comment rather than on the top of a thread with ninety others.
    """
    comments = ((node.get("comments") or {}).get("nodes")) or []
    if not comments:
        return {}
    last = comments[-1] or {}
    body = last.get("body") or ""
    if BOT_MARKER not in body:
        return {}
    return {"body": body, "url": last.get("url") or "", "at": last.get("createdAt") or ""}


def _issue_row(i) -> dict:
    last_word = _bot_last_word(i)
    return {
        "number": i.get("number"),
        "title": i.get("title") or "",
        "body": (i.get("body") or "")[:4000],
        "labels": [l.get("name") for l in ((i.get("labels") or {}).get("nodes") or [])],
        "url": i.get("url") or "",
        "updatedAt": i.get("updatedAt") or "",
        "bot_last": bool(last_word),
        # When the bot spoke last, its words ARE the question a human has to
        # answer, so they travel with the issue instead of behind a click.
        "last_word": str(last_word.get("body") or "")[:1500],
        # And where that comment is, so the automation overview can link to the
        # thing the runner actually left rather than to the issue it left it on.
        "last_word_url": str(last_word.get("url") or ""),
        "last_word_at": str(last_word.get("at") or "")[:20],
    }


def _issue_rows(nodes) -> list:
    issues = [_issue_row(i) for i in nodes or []]
    issues.sort(key=lambda x: (not x["bot_last"], -(x["number"] or 0)))
    return issues


def _pr_rows(nodes) -> list:
    """Every open PR, with pipeline ownership kept separate from visibility.

    A desk is allowed to show all of its work. Only a pipeline branch may gain
    the office's merge button; apply_merge re-reads and enforces the same
    boundary at the moment of action.
    """
    prs = []
    for pr in nodes or []:
        head = pr.get("headRefName") or ""
        body = pr.get("body") or ""
        prs.append({
            "number": pr.get("number"),
            "title": pr.get("title") or "",
            # The body travels: the desk pane shows its first paragraph and the
            # closes list, and a reviewer should not need GitHub to read why.
            "body": body[:4000],
            "head": head,
            "pipeline": _pipeline_branch(head),
            "base": pr.get("baseRefName") or "",
            "url": pr.get("url") or "",
            "draft": bool(pr.get("isDraft")),
            # GitHub's own words, not a guess: MERGEABLE / CONFLICTING / UNKNOWN,
            # and CLEAN / BLOCKED / BEHIND / DIRTY. "UNKNOWN" means GitHub has not
            # finished computing it, which is not the same as "cannot merge".
            "mergeable": pr.get("mergeable") or "UNKNOWN",
            "state": pr.get("mergeStateStatus") or "UNKNOWN",
            "closes": [int(n) for n in re.findall(r"(?:closes|fixes|resolves)\s+#(\d+)",
                                                  body, re.I)],
            "updatedAt": pr.get("updatedAt") or "",
        })
    prs.sort(key=lambda x: -(x["number"] or 0))
    return prs


def _batch_command(nwos) -> list:
    cmd = ["gh", "api", "graphql", "-f", "query=" + batch_query(len(nwos))]
    for i, nwo in enumerate(nwos):
        owner, name = nwo.split("/", 1)
        cmd += ["-f", f"o{i}={owner}", "-f", f"n{i}={name}"]
    return cmd


def _pr_page_command(nwo: str, cursor: str) -> list:
    owner, name = nwo.split("/", 1)
    return ["gh", "api", "graphql", "-f", "query=" + PR_PAGE_QUERY,
            "-f", f"o={owner}", "-f", f"n={name}", "-f", f"after={cursor}"]


def _add_rate(total: dict, page: dict) -> dict:
    """One rate block representing every query used for this batch."""
    answer = dict(total or {})
    answer["cost"] = int(answer.get("cost") or 0) + int(page.get("cost") or 0)
    for key in ("limit", "remaining", "resetAt"):
        if page.get(key) is not None:
            answer[key] = page[key]
    return answer


def _pr_reserve_error(rate: dict) -> str:
    remaining = rate.get("remaining")
    if isinstance(remaining, int) and remaining < GH_RESERVE:
        return (f"rate limit reserve reached: {remaining} points left, "
                f"reserve is {GH_RESERVE}")
    return ""


def _json_body(out: str) -> dict:
    """The JSON object gh printed, or {} when it printed nothing usable."""
    try:
        body = json.loads(out) if out.strip() else {}
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _is_rate_limited(msg: str, error: dict) -> bool:
    return RATE_WORDS in msg.lower() or str(error.get("type") or "") == "RATE_LIMITED"


def _alias_index(error: dict, n: int) -> int:
    """Which repo of the batch a GraphQL error is about, or -1 for the whole batch."""
    path = error.get("path") or []
    alias = str(path[0]) if path else ""
    if alias[:1] == "r" and alias[1:].isdigit() and int(alias[1:]) < n:
        return int(alias[1:])
    return -1


def _sort_errors(body_errors, nwos):
    """Per-repo errors and the one that sinks the whole batch, as (errors, fatal)."""
    errors, fatal = {}, ""
    for e in body_errors:
        msg = str(e.get("message") or e.get("type") or "graphql error")[:160]
        if _is_rate_limited(msg, e):
            fatal = msg
            continue
        i = _alias_index(e, len(nwos))
        if i >= 0:
            errors.setdefault(nwos[i], msg)
        else:
            fatal = fatal or msg
    return errors, fatal


def _desk_rows(data: dict, nwos, errors: dict, fatal: str) -> dict:
    """{nwo: {"issues", "prs"}} for every repo that answered; the rest get an error."""
    rows = {}
    for i, nwo in enumerate(nwos):
        node = data.get(f"r{i}")
        if not isinstance(node, dict):
            errors.setdefault(nwo, fatal or "GitHub returned nothing for this repo")
            continue
        rows[nwo] = {"issues": _issue_rows((node.get("issues") or {}).get("nodes")),
                     "prs": _pr_rows((node.get("pullRequests") or {}).get("nodes"))}
    return rows


def _pr_page_cursors(data: dict, nwos) -> dict:
    """Continuation cursor for each repo whose first hundred PRs were not all."""
    cursors = {}
    for i, nwo in enumerate(nwos):
        node = data.get(f"r{i}") or {}
        page = ((node.get("pullRequests") or {}).get("pageInfo")) or {}
        if page.get("hasNextPage"):
            cursors[nwo] = str(page.get("endCursor") or "")
    return cursors


def _fetch_pr_tail(nwo: str, token: str, cursor: str):
    """All PR rows after one connection cursor, or one fail-closed error."""
    prs, rate = [], {}
    if not cursor:
        return None, "GitHub omitted a pull-request cursor", rate, ""
    seen = set()
    while cursor:
        if cursor in seen:
            return None, "GitHub repeated a pull-request cursor", rate, ""
        seen.add(cursor)

        rc, out, err = sh(_pr_page_command(nwo, cursor), timeout=90,
                          env={"GH_TOKEN": token})
        body = _json_body(out)
        if not body:
            msg = (err.strip().splitlines() or [f"gh exit {rc}"])[0][:160]
            return None, msg, rate, ""

        data = body.get("data") or {}
        rate = _add_rate(rate, data.get("rateLimit") or {})
        errors, fatal = _sort_errors(body.get("errors") or [], [nwo])
        if fatal:
            return None, fatal, rate, fatal
        if nwo in errors:
            return None, errors[nwo], rate, ""

        node = data.get("r0")
        if not isinstance(node, dict):
            return None, "GitHub returned nothing for this PR page", rate, ""
        connection = node.get("pullRequests") or {}
        prs.extend(_pr_rows(connection.get("nodes")))
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        reserve_error = _pr_reserve_error(rate)
        if reserve_error:
            return None, reserve_error, rate, reserve_error
        cursor = str(page.get("endCursor") or "")
        if not cursor:
            return None, "GitHub omitted a pull-request cursor", rate, ""
    return prs, "", rate, ""


def fetch_batch(nwos, token):
    """(rows, errors, rate, fatal) for up to BATCH_SIZE repos on one token.

    rows    {nwo: {"issues": [...], "prs": [...]}} for every repo that answered
    errors  {nwo: "one line"} for the repos that did not
    rate    GitHub's own `rateLimit` block for this query, or {}
    fatal   a message when the whole batch is unusable, "" otherwise
    """
    nwos = [n for n in nwos if NWO_RE.match(n or "")]
    if not nwos:
        return {}, {}, {}, ""

    rc, out, err = sh(_batch_command(nwos), timeout=90, env={"GH_TOKEN": token})
    # gh exits non-zero when ANY alias in the batch errored, and still prints the
    # whole body. The body is the answer; the exit code is only a summary of it,
    # so one dead repo must not be read as ten dead repos.
    body = _json_body(out)
    if not body:
        return {}, {}, {}, (err.strip().splitlines() or [f"gh exit {rc}"])[0][:160]

    data = body.get("data") or {}
    rate = data.get("rateLimit") or {}
    errors, fatal = _sort_errors(body.get("errors") or [], nwos)
    rows = _desk_rows(data, nwos, errors, fatal)
    cursors = list(_pr_page_cursors(data, nwos).items())
    reserve_error = _pr_reserve_error(rate)
    if cursors and reserve_error:
        for nwo, _ in cursors:
            rows.pop(nwo, None)
            errors[nwo] = reserve_error
        return rows, errors, rate, reserve_error
    for index, (nwo, cursor) in enumerate(cursors):
        tail, page_error, page_rate, page_fatal = _fetch_pr_tail(nwo, token, cursor)
        rate = _add_rate(rate, page_rate)
        if page_error:
            rows.pop(nwo, None)
            errors[nwo] = page_error
        elif nwo in rows:
            rows[nwo]["prs"].extend(tail or [])
            rows[nwo]["prs"].sort(key=lambda x: -(x["number"] or 0))
        if page_fatal:
            fatal = page_fatal
            # No partial list may masquerade as every open PR. Repos whose
            # continuations were not attempted keep their last-good cache too.
            for pending, _ in cursors[index + 1:]:
                rows.pop(pending, None)
                errors[pending] = page_fatal
            break
    return rows, errors, rate, fatal


def fetch_issues(nwo, token):
    """One desk's open issues, for the paths that act on a single repo.

    The room itself never calls this: it fetches in batches. A repo-level nudge
    does, because it has exactly one repo in hand and needs to know which issues
    the bot is sitting on before it says anything.
    """
    if not NWO_RE.match(nwo or ""):
        return None, "malformed repo"
    rows, errors, rate, fatal = fetch_batch([nwo], token)
    note_rate(rate)
    if nwo in rows:
        return rows[nwo]["issues"], None
    return None, errors.get(nwo) or fatal or "GitHub returned nothing for this repo"


def _group_by_token(visible, tokens, errors) -> dict:
    """{token: [repos]} for the desks somebody can push to; the rest get an error."""
    by_token = {}
    for repo in visible:
        who, tok = tokens.get(repo) or (None, None)
        if not tok:
            errors[repo] = "no account holds push here"
            continue
        by_token.setdefault(tok, []).append(repo)
    return by_token


def _batches(by_token: dict) -> list:
    return [(tok, group[i:i + BATCH_SIZE])
            for tok, group in by_token.items()
            for i in range(0, len(group), BATCH_SIZE)]


def _blame_batch(chunk, rows, errors, fatal) -> None:
    """The batch as a whole is unusable, so every desk in it wears the same
    line. None of them is blanked; they keep what they had."""
    for repo in chunk:
        if repo not in rows:
            errors.setdefault(repo, fatal)


def _fetch_batches(batches, fresh, errors):
    """Run the batches in order, filling `fresh` and `errors`.

    Returns (cost, gh_error, stopped). Sequential on purpose. The reserve is
    only a reserve if the decision to stop is made before the next query goes
    out, not after eight of them already did.
    """
    cost, gh_error, stopped = 0, "", ""
    for tok, chunk in batches:
        left = BUDGET["remaining"]
        if isinstance(left, int) and left < GH_RESERVE:
            stopped = pause(f"only {left} graphql points left, reserve is {GH_RESERVE}",
                            BUDGET["reset_at"])
            break
        rows, errs, rate, fatal = fetch_batch(chunk, tok)
        cost += note_rate(rate)
        fresh.update(rows)
        errors.update(errs)
        if fatal:
            gh_error = fatal
            _blame_batch(chunk, rows, errors, fatal)
            if RATE_WORDS in fatal.lower():
                stopped = pause(fatal, BUDGET["reset_at"])
                break
    return cost, gh_error, stopped


def _fetch_visible(visible, tokens, fresh, errors):
    """Ask GitHub about every visible desk unless the budget says wait.

    Returns (cost, gh_error). A desk that got no fresh rows because the build
    was paused says so instead of blanking.
    """
    stopped = paused_until()
    cost, gh_error = 0, ""
    if not stopped:
        by_token = _group_by_token(visible, tokens, errors)
        cost, gh_error, stopped = _fetch_batches(_batches(by_token), fresh, errors)
    if stopped:
        for repo in visible:
            if repo not in fresh:
                errors[repo] = f"GitHub paused until {stopped}"
    return cost, gh_error


def _remember_fresh(cache: dict, fresh: dict, desks, hidden, stamp: str) -> dict:
    """Fold this build's rows into the on-disk cache and forget lost desks."""
    for repo, rows in fresh.items():
        cache[repo] = {"fetched_at": stamp, "issues": rows["issues"], "prs": rows["prs"]}
    keep = set(desks) | hidden
    cache = {r: v for r, v in cache.items() if r in keep}
    if fresh:
        write_desks(cache)
    return cache


def _desk_access(repo, hidden, access, tokens):
    """(identity, access) for one desk: who can push there, and whether anyone can."""
    if repo in hidden:
        # Never probed this build, so the answer is whatever we already knew.
        # `None` access means "the snapshot did not say", which is not the
        # same as "locked" and must not render as it.
        hit = access.cache.get(repo)
        return (hit or None), (None if hit is None else bool(hit))
    who = (tokens.get(repo) or (None, None))[0]
    return who, bool(who)


def _stale_reason(repo, errors, fresh) -> str:
    """Why a desk is showing last-good data, or "" when it heard from GitHub."""
    if repo in fresh:
        return ""
    return errors.get(repo) or ""


def _station(repo, runs, who, can, hidden: bool, pins, kept: dict, stale: str) -> dict:
    head = headline(runs)
    st = {
        "repo": repo,
        "identity": who,
        "access": can,
        "outcome": head.get("outcome", ""),
        "detail": head.get("detail", ""),
        "at": head.get("at", ""),
        "runs": runs[:10],
        # Put away, but still here, still carrying what it last showed, so
        # the app can list it and bring it back. Never fetched while hidden.
        "hidden": hidden,
        # Its rank at the top of the roster, or null when it is not pinned.
        # The order itself is `world.pins`; this is the same fact per desk.
        "pinned": pins.index(repo) if repo in pins else None,
        # When this desk last heard from GitHub. "" means never.
        "fetched_at": kept.get("fetched_at", ""),
        "issues": list(kept.get("issues") or []),
        "issues_error": None,
        "prs": list(kept.get("prs") or []),
        "prs_error": None,
    }
    if stale:
        # Last-good data AND the reason it is last-good. A desk that blanks
        # because GitHub said no is a lie; a stale desk that says so is not.
        st["issues_error"] = stale
        st["prs_error"] = stale
    return st


def _heartbeat() -> str:
    if not HEARTBEAT:
        return ""
    try:
        return HEARTBEAT.read_text().strip()[:40]
    except Exception:
        return ""


def _log_room(stations, cost, run) -> None:
    waiting = sum(1 for s in stations for i in s["issues"] if i["bot_last"])
    log(f"{len(stations)} desks, "
        f"{sum(len(s['issues']) for s in stations)} open issues, {waiting} waiting on you, "
        f"{cost} graphql points this build")
    gate = run.get("gate") or {}
    if gate.get("state") == "pending":
        log(f"A GATE IS OPEN: {gate.get('permission')} {gate.get('target', '')[:60]}")


def build_snapshot(access: Access):
    by_repo, counts = receipts()
    hidden = set(read_hidden())
    pins = read_pins()
    cache = read_desks()
    desks = sorted(by_repo)
    visible = [r for r in desks if r not in hidden and NWO_RE.match(r)]
    log(f"{len(desks)} desks, {len(hidden & set(desks))} put away, "
        f"{len(visible)} to fetch in {-(-len(visible) // BATCH_SIZE)} batches")

    # Probing push access is REST and cached for six hours, so it is not what
    # this rewrite is about. It stays parallel because a cold cache is 72 calls.
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = dict(zip(visible, pool.map(access.token_for, visible)))

    fresh, errors = {}, {}
    cost, gh_error = _fetch_visible(visible, tokens, fresh, errors)

    stamp = NOW.strftime(ISO)
    cache = _remember_fresh(cache, fresh, desks, hidden, stamp)

    stations = []
    for repo in desks:
        who, can = _desk_access(repo, hidden, access, tokens)
        stations.append(_station(repo, by_repo[repo], who, can, repo in hidden, pins,
                                 cache.get(repo) or {}, _stale_reason(repo, errors, fresh)))

    access.save()
    run = rt.snapshot()
    _log_room(stations, cost, run)

    fixtures = sections_mod.read_all()

    return {
        "generated": stamp,
        "heartbeat": _heartbeat(),
        "killed": bool(KILLSWITCH and KILLSWITCH.exists()),
        "today": counts,
        "stations": stations,
        # Pin order, and whose office this is. The roster sorts the first owner
        # here above every other org, so it comes from OFFICE_OWNERS when set
        # and from the `gh` logins otherwise: both are "you", in that order.
        "pins": pins,
        "owners": list(OWNERS) or list(getattr(access, "mine", None) or []),
        "runtime": run,
        "sections": fixtures,
        # The automation, as one page instead of three. Assembled from what this
        # function already measured: the runner's receipts, the desks (for the
        # link to the comment it left), and the two fixtures that know whether
        # anything is scheduled and whether anything can reach the door. No
        # second measurement of any of it.
        "automation": automation.build(by_repo, stations, fixtures, counts, stamp),
        "github": {
            "limit": BUDGET["limit"],
            "remaining": BUDGET["remaining"],
            "reset_at": BUDGET["reset_at"],
            "cost": cost,
            "paused_until": BUDGET["paused_until"],
            "error": BUDGET["error"] if BUDGET["paused_until"] else gh_error,
        },
    }


# ── applying what was clicked ────────────────────────────────────────────────

RUNTIME_KINDS = {"permit", "chat", "run", "stop"}


def apply_merge(repo, who, tok, payload, dry: bool):
    """Merge a PR the PIPELINE opened, and refuse everything else.

    The browser holds a view token and can queue any intent at all, so nothing it
    claims about a PR is trusted. The PR is re-read from GitHub here and checked
    on this machine:

      * the head branch must start with PR_PREFIX, so the office can never merge
        a human's own branch. This is the whole safety boundary.
      * a draft is never merged, because a draft is a statement that it is not
        ready.
      * GitHub's own mergeable verdict must not be CONFLICTING. UNKNOWN means
        GitHub has not finished computing it, which is not permission: it is
        "ask again in a moment".

    Squash and delete the branch: one issue, one branch, one commit on main, and
    nothing left behind to drift.
    """
    num = str(payload.get("pr") or "").strip()
    if not num.isdigit():
        return False, "a merge needs a PR number"

    env = {"GH_TOKEN": tok}
    pr, err = _read_pr(repo, num, env)
    if err:
        return False, err
    head = pr.get("headRefName") or ""
    refusal = _merge_refusal(pr, num, head)
    if refusal:
        return False, refusal

    if dry:
        return True, f"would squash-merge #{num} ({head})"

    rc, _, err = sh(["gh", "pr", "merge", num, "--repo", repo,
                     "--squash", "--delete-branch"], timeout=120, env=env)
    if rc != 0:
        msg = (err.strip().splitlines() or ["failed"])[0][:160]
        return False, f"merge refused by GitHub: {msg}"
    return True, f"as {who}: squash-merged #{num} ({head}); its Closes line shuts the issue"


def _read_pr(repo, num, env):
    """The PR as GitHub describes it right now, as (pr, error)."""
    rc, out, err = sh(["gh", "pr", "view", num, "--repo", repo, "--json",
                       "headRefName,isDraft,mergeable,state,title"],
                      timeout=60, env=env)
    if rc != 0:
        return None, f"could not read PR #{num}: {(err.strip().splitlines() or ['failed'])[0][:120]}"
    try:
        return json.loads(out or "{}"), ""
    except Exception as exc:
        return None, f"could not read PR #{num}: {exc}"


def _merge_refusal(pr: dict, num: str, head: str) -> str:
    """Why this PR must not be merged, or "" when it may."""
    if not PR_PREFIX or not PR_PREFIX.strip():
        return "refusing: the pipeline branch prefix is empty"
    if not _pipeline_branch(head):
        # The refusal names the branch on purpose. A silent no here would look
        # exactly like a failure to reach GitHub.
        return (f"refusing: PR #{num} is on {head!r}, which is not a pipeline "
                f"branch. The office only merges branches starting {PR_PREFIX!r}.")
    if (pr.get("state") or "").upper() != "OPEN":
        return f"PR #{num} is {pr.get('state', 'not open').lower()}"
    if pr.get("isDraft"):
        return f"PR #{num} is a draft; a draft says it is not ready"
    if (pr.get("mergeable") or "").upper() == "CONFLICTING":
        return f"PR #{num} conflicts with its base and needs a human"
    return ""


def _apply_permit(d, payload, dry: bool):
    """Answer the gate on disk, and only the gate whose id was answered."""
    root = rt._root()
    if root is None:
        return False, "no runtime root configured (OFFICE_RUNTIME_ROOT)"
    qid = str(payload.get("question_id") or "")
    answer = payload.get("answer")
    if answer not in ("allow", "deny"):
        return False, "a permit must answer allow or deny"
    if dry:
        live = rt.read_gate()
        if live.get("state") != "pending":
            return False, "nothing is waiting on a gate right now"
        same = live.get("id") == qid
        return same, ("would " + answer) if same else "the agent has moved on"
    return rt.answer_gate(root, qid, answer, bool(payload.get("always")))


def _apply_chat(d, payload, dry: bool):
    text = (payload.get("body") or "").strip()
    if not text:
        return False, "nothing to say"
    if dry:
        return True, f"would say {text[:60]!r}"
    try:
        rt.post("/api/chat", {"message": text})
    except Exception as exc:
        return False, f"the runtime did not take it: {exc}"
    return True, f"said {text[:60]!r}"


def _apply_run(d, payload, dry: bool):
    task = (payload.get("body") or "").strip()
    repo = d.get("repo") or ""
    issue = d.get("issue")
    if not task:
        task = f"Work {repo}#{issue}" if issue else f"Work on {repo}"
    if dry:
        return True, f"would run {task[:70]!r}"
    try:
        rt.post("/api/run", {"task": task})
    except Exception as exc:
        return False, f"the runtime refused the run: {exc}"
    return True, f"started {task[:70]!r}"


def _apply_stop(d, payload, dry: bool):
    if dry:
        return True, "would stop the current run"
    try:
        rt.post("/api/run/stop", {"run_id": payload.get("run_id") or ""})
    except Exception as exc:
        return False, f"could not stop it: {exc}"
    return True, "asked the runtime to stop at the next step boundary"


RUNTIME_HANDLERS = {
    "permit": _apply_permit,
    "chat": _apply_chat,
    "run": _apply_run,
    "stop": _apply_stop,
}


def apply_runtime_decision(d, dry: bool):
    """Route a decision at the local runtime rather than at GitHub.

    Nothing here trusts the Worker for anything but the words that were typed and
    the id of the question being answered. A permit in particular is re-checked
    against the gate on disk before a single byte is written.
    """
    kind = d.get("kind")
    handler = RUNTIME_HANDLERS.get(kind)
    if handler is None:
        return False, f"unknown runtime kind {kind}"
    return handler(d, d.get("payload") or {}, dry)


REQUEUE_LINE = "Requeued from the office board."


def _has_issue_number(issue) -> bool:
    return bool(issue and str(issue).isdigit())


def _first_error_line(err: str, width: int = 160) -> str:
    return (err.strip().splitlines() or ["failed"])[0][:width]


def _requeue_stuck_issues(repo, who, tok, dry: bool):
    """A repo-level nudge has no issue to speak on, so it means "unblock everything
    here": post the requeue line on each issue the bot is sitting on. That is the
    only bounded reading of "work this repo next" that does something real."""
    issues, err = fetch_issues(repo, tok)
    if issues is None:
        return False, f"could not list issues: {err}"
    stuck = [i for i in issues if i["bot_last"]][:10]
    if not stuck:
        return False, "nothing here is waiting on a human"
    env = {"GH_TOKEN": tok}
    done = []
    for i in stuck:
        n = str(i["number"])
        if dry:
            done.append(f"would requeue #{n}")
            continue
        rc, _, err = sh(["gh", "issue", "comment", n, "--repo", repo,
                         "--body", REQUEUE_LINE], timeout=60, env=env)
        if rc != 0:
            return False, f"#{n}: {_first_error_line(err, 120)}"
        sh(["gh", "issue", "edit", n, "--repo", repo,
            "--remove-label", WAITING_LABEL], timeout=60, env=env)
        done.append(f"#{n}")
    return True, f"as {who}: requeued " + ", ".join(done)


def _comment_step(kind, num, repo, body):
    """The comment an issue decision posts, as (command, refusal).

    A comment with no marker is exactly what re-queues an issue, because the
    runner's whole selection rule is "did the bot have the last word". This
    is the mechanism, not a side effect: answering a question IS the nudge.
    """
    if kind not in ("comment", "unblock", "nudge", "close"):
        return None, ""
    text = body or (REQUEUE_LINE if kind == "nudge" else "")
    if not text:
        if kind in ("comment", "unblock"):
            return None, "nothing to say"
        return None, ""
    if not num:
        return None, "a comment needs an issue"
    return ["gh", "issue", "comment", num, "--repo", repo, "--body", text], ""


def _edit_steps(kind, num, repo, payload):
    """The label, close and reopen commands an issue decision runs, as (commands, refusal)."""
    if not num:
        return [], ""
    steps = []
    if kind in ("unblock", "nudge"):
        steps.append(["gh", "issue", "edit", num, "--repo", repo,
                      "--remove-label", WAITING_LABEL])
    if kind == "label":
        label = (payload.get("label") or "").strip()
        if not label:
            return [], "no label given"
        steps.append(["gh", "issue", "edit", num, "--repo", repo, "--add-label", label])
    if kind == "close":
        steps.append(["gh", "issue", "close", num, "--repo", repo])
    if kind == "reopen":
        steps.append(["gh", "issue", "reopen", num, "--repo", repo])
    return steps, ""


def _issue_steps(kind, num, repo, body, payload):
    """Every gh command one issue decision turns into, as (commands, refusal)."""
    comment, err = _comment_step(kind, num, repo, body)
    if err:
        return None, err
    edits, err = _edit_steps(kind, num, repo, payload)
    if err:
        return None, err
    steps = ([comment] if comment else []) + edits
    if not steps:
        return None, f"nothing to do for {kind}"
    return steps, ""


def _is_missing_label_error(cmd, msg: str) -> bool:
    """Removing a label the issue never had is a no-op, not a failure, and
    failing the whole decision over it would strand a real reply."""
    return "--remove-label" in cmd and ("not found" in msg.lower() or "label" in msg.lower())


def _run_steps(steps, env, dry: bool):
    """Run the gh commands in order: (True, what was done) or (False, why it stopped)."""
    done = []
    for cmd in steps:
        if dry:
            done.append("would " + " ".join(cmd[1:4]))
            continue
        rc, _, err = sh(cmd, timeout=60, env=env)
        verb = f"{cmd[1]} {cmd[2]}"
        if rc != 0:
            msg = _first_error_line(err)
            if _is_missing_label_error(cmd, msg):
                done.append(f"{verb}: label was not set")
                continue
            return False, f"{verb}: {msg}"
        done.append(verb)
    return True, done


def apply_decision(d, access: Access, dry: bool):
    """Re-derive everything from the decision's own fields, trusting the Worker
    for nothing but the words that were typed. The repo is re-probed for push, so a
    forged intent against a repo we cannot write to dies here."""
    repo = d.get("repo") or ""
    kind = d.get("kind") or ""
    issue = d.get("issue")
    payload = d.get("payload") or {}
    body = (payload.get("body") or "").strip()

    if kind in RUNTIME_KINDS:
        return apply_runtime_decision(d, dry)

    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return False, f"refusing a malformed repo {repo!r}"
    who, tok = access.token_for(repo)
    if not tok:
        return False, f"no account can push to {repo}"

    # Merging is the only intent that puts code on a default branch, so it is
    # re-derived from GitHub here rather than from anything the browser said.
    if kind == "merge":
        return apply_merge(repo, who, tok, payload, dry)
    if kind != "nudge" and not _has_issue_number(issue):
        return False, f"{kind} needs an issue number"

    num = str(issue) if issue else ""
    if kind == "nudge" and not num:
        return _requeue_stuck_issues(repo, who, tok, dry)

    return _apply_issue_decision(repo, who, tok, num, kind, body, payload, dry)


def _apply_issue_decision(repo, who, tok, num, kind, body, payload, dry: bool):
    """Comment, relabel, close or reopen one issue, in that order, as this login."""
    steps, err = _issue_steps(kind, num, repo, body, payload)
    if err:
        return False, err
    ok, result = _run_steps(steps, {"GH_TOKEN": tok}, dry)
    if not ok:
        return False, result
    return True, f"as {who}: " + "; ".join(result)
