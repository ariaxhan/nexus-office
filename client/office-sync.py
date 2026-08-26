#!/usr/bin/env python3
"""Keep the cloud office and the real world pointed at each other.

Two directions, one process, deliberately:

  push   what your automation actually did -> a snapshot the browser can render
  drain  what you clicked in the browser   -> real comments, labels and closes

The Worker holds no credentials and never will. Everything that touches GitHub
happens here, on the machine that already has a `gh` login, and every intent that
arrives from the browser is re-checked against the real repo before it is acted
on. A stolen session can therefore QUEUE an intent and never EXECUTE one.

  office-sync.py            drain, then push
  office-sync.py --push     push only
  office-sync.py --drain    drain only
  office-sync.py --dry-run  say what it would do, touch nothing
  office-sync.py --check    prove the live surface end to end
  office-sync.py --open     open the office in a browser
  office-sync.py --cancel N drop a queued decision

Configuration, all from the environment so nothing personal lives in this file:

  OFFICE_URL        where the Worker is                  (required)
  OFFICE_RECEIPTS   a JSONL of pipeline receipts         (optional)
  OFFICE_OWNERS     comma separated GitHub owners        (optional; used when
                    there is no receipts file, so every repo you can push to
                    under those owners gets a desk)
  OFFICE_HEARTBEAT  file holding your runner's last-success stamp  (optional)
  OFFICE_KILLSWITCH file whose existence means "the runner is halted" (optional)
  OFFICE_MARKER     the comment marker your bot leaves    (default: pipeline-bot)
  OFFICE_WAITING    the label meaning a human must look   (default: waiting on human)
  OFFICE_KEYCHAIN   keychain service holding the tokens   (default: nexus-office)
  OFFICE_RUNTIME_ROOT  a local agent runtime's repo root   (optional; enables gates)
  OFFICE_RUNTIME_URL   that runtime's dashboard            (default 127.0.0.1:8787)

A receipt is one JSON object per line:

  {"at": "2026-08-25T23:14:38Z", "repo": "owner/name",
   "issue": "42", "outcome": "landed", "detail": "opened a PR"}

Outcome is free text. The values in NOISE below describe a RUN rather than a
repo, and are skipped when picking the headline state for a desk.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import runtime as rt  # noqa: E402  (needs the path above)

def _env_path(name):
    v = os.environ.get(name, "").strip()
    return pathlib.Path(v).expanduser() if v else None


RECEIPTS = _env_path("OFFICE_RECEIPTS")
HEARTBEAT = _env_path("OFFICE_HEARTBEAT")
KILLSWITCH = _env_path("OFFICE_KILLSWITCH")
OWNERS = [o.strip() for o in os.environ.get("OFFICE_OWNERS", "").split(",") if o.strip()]

KEYCHAIN_SERVICE = os.environ.get("OFFICE_KEYCHAIN", "nexus-office")
STATE = pathlib.Path.home() / ".local/state/nexus-office"
ACCESS_CACHE = STATE / "access.json"
ACCESS_TTL = 6 * 3600

BOT_MARKER = os.environ.get("OFFICE_MARKER", "pipeline-bot")
WAITING_LABEL = os.environ.get("OFFICE_WAITING", "waiting on human")
DEFAULT_URL = os.environ.get("OFFICE_URL", "")

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


def keychain(account: str) -> str:
    """Secrets live in the keychain and nowhere else. Never in a file, ever.

    On a machine without `security`, export OFFICE_PUSH_TOKEN / OFFICE_PASSWORD
    instead; the environment is checked first so a Linux box or a container can
    run this unchanged.
    """
    env_name = f"OFFICE_{account.upper()}_TOKEN" if account != "password" else "OFFICE_PASSWORD"
    if os.environ.get(env_name):
        return os.environ[env_name].strip()
    rc, out, _ = sh(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                     "-a", account, "-w"], timeout=10)
    return out.strip() if rc == 0 else ""


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


# ── the issues on each desk ──────────────────────────────────────────────────

ISSUE_FIELDS = "number,title,body,labels,url,updatedAt,comments"


def fetch_issues(nwo, token):
    rc, out, err = sh(["gh", "issue", "list", "--repo", nwo, "--state", "open",
                       "--limit", "60", "--json", ISSUE_FIELDS],
                      timeout=60, env={"GH_TOKEN": token})
    if rc != 0 or not out.strip():
        return None, (err.strip().splitlines() or ["empty response"])[0][:140]
    try:
        raw = json.loads(out)
    except Exception as e:
        return None, str(e)[:140]

    issues = []
    for i in raw:
        comments = i.get("comments") or []
        last = comments[-1] if comments else None
        # THE rule, copied from dispatch.sh on purpose: the bot having the last
        # word is what "waiting on a human" mechanically means. A label is a hint
        # that can go stale; this cannot.
        bot_last = bool(last and BOT_MARKER in (last.get("body") or ""))
        issues.append({
            "number": i.get("number"),
            "title": i.get("title") or "",
            "body": (i.get("body") or "")[:4000],
            "labels": [l.get("name") for l in (i.get("labels") or [])],
            "url": i.get("url") or "",
            "updatedAt": i.get("updatedAt") or "",
            "bot_last": bot_last,
            # When the bot spoke last, its words ARE the question a human has to
            # answer, so they travel with the issue instead of behind a click.
            "last_word": (last.get("body") or "")[:1500] if bot_last else "",
        })
    issues.sort(key=lambda x: (not x["bot_last"], -(x["number"] or 0)))
    return issues, None


def build_snapshot(access: Access):
    by_repo, counts = receipts()
    log(f"{len(by_repo)} repos in the receipts; fetching issues")

    def one(item):
        repo, runs = item
        who, tok = access.token_for(repo)
        head = headline(runs)
        st = {
            "repo": repo,
            "identity": who,
            "access": bool(who),
            "outcome": head.get("outcome", ""),
            "detail": head.get("detail", ""),
            "at": head.get("at", ""),
            "runs": runs[:10],
            "issues": [],
            "issues_error": None,
        }
        if not tok:
            st["issues_error"] = "no account holds push here"
            return st
        issues, err = fetch_issues(repo, tok)
        if issues is None:
            st["issues_error"] = err
        else:
            st["issues"] = issues
        return st

    with ThreadPoolExecutor(max_workers=8) as pool:
        stations = list(pool.map(one, sorted(by_repo.items())))

    access.save()
    hb = ""
    if HEARTBEAT:
        try:
            hb = HEARTBEAT.read_text().strip()[:40]
        except Exception:
            pass

    waiting = sum(1 for s in stations for i in s["issues"] if i["bot_last"])

    log(f"{len(stations)} desks, "
        f"{sum(len(s['issues']) for s in stations)} open issues, {waiting} waiting on you")
    run = rt.snapshot()
    gate = run.get("gate") or {}
    if gate.get("state") == "pending":
        log(f"A GATE IS OPEN: {gate.get('permission')} {gate.get('target', '')[:60]}")

    return {
        "generated": NOW.strftime(ISO),
        "heartbeat": hb,
        "killed": bool(KILLSWITCH and KILLSWITCH.exists()),
        "today": counts,
        "stations": stations,
        "runtime": run,
    }


# ── the Worker ───────────────────────────────────────────────────────────────

class Office:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, path, method="GET", body=None, timeout=45):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("authorization", f"Bearer {self.token}")
        req.add_header("content-type", "application/json")
        # Cloudflare's browser-integrity check answers urllib's default agent with
        # a 1010 before the request ever reaches the Worker. Naming ourselves
        # honestly is enough; this is not evasion, it is having a name at all.
        req.add_header("user-agent", "nexus-office-sync/1.0 (+local vault runner)")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from None


# ── applying what was clicked ────────────────────────────────────────────────

RUNTIME_KINDS = {"permit", "chat", "run", "stop"}


def apply_runtime_decision(d, dry: bool):
    """Route a decision at the local runtime rather than at GitHub.

    Nothing here trusts the Worker for anything but the words that were typed and
    the id of the question being answered. A permit in particular is re-checked
    against the gate on disk before a single byte is written.
    """
    kind = d.get("kind")
    payload = d.get("payload") or {}

    if kind == "permit":
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

    if kind == "chat":
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

    if kind == "run":
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

    if kind == "stop":
        if dry:
            return True, "would stop the current run"
        try:
            rt.post("/api/run/stop", {"run_id": payload.get("run_id") or ""})
        except Exception as exc:
            return False, f"could not stop it: {exc}"
        return True, "asked the runtime to stop at the next step boundary"

    return False, f"unknown runtime kind {kind}"


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
    if kind != "nudge" and not (issue and str(issue).isdigit()):
        return False, f"{kind} needs an issue number"

    env = {"GH_TOKEN": tok}
    num = str(issue) if issue else ""
    steps = []

    # A repo-level nudge has no issue to speak on, so it means "unblock everything
    # here": post the requeue line on each issue the bot is sitting on. That is the
    # only bounded reading of "work this repo next" that does something real.
    if kind == "nudge" and not num:
        issues, err = fetch_issues(repo, tok)
        if issues is None:
            return False, f"could not list issues: {err}"
        stuck = [i for i in issues if i["bot_last"]][:10]
        if not stuck:
            return False, "nothing here is waiting on a human"
        done = []
        for i in stuck:
            n = str(i["number"])
            if dry:
                done.append(f"would requeue #{n}")
                continue
            rc, _, err = sh(["gh", "issue", "comment", n, "--repo", repo,
                             "--body", "Requeued from the office board."],
                            timeout=60, env=env)
            if rc != 0:
                return False, f"#{n}: {(err.strip().splitlines() or ['failed'])[0][:120]}"
            sh(["gh", "issue", "edit", n, "--repo", repo,
                "--remove-label", WAITING_LABEL], timeout=60, env=env)
            done.append(f"#{n}")
        return True, f"as {who}: requeued " + ", ".join(done)

    if kind in ("comment", "unblock", "nudge", "close"):
        # A comment with no marker is exactly what re-queues an issue, because the
        # runner's whole selection rule is "did the bot have the last word". This
        # is the mechanism, not a side effect: answering a question IS the nudge.
        text = body or ("Requeued from the office board." if kind == "nudge" else "")
        if kind in ("comment", "unblock") and not text:
            return False, "nothing to say"
        if text and num:
            steps.append(["gh", "issue", "comment", num, "--repo", repo, "--body", text])
        elif text and not num:
            return False, "a comment needs an issue"

    if kind in ("unblock", "nudge") and num:
        steps.append(["gh", "issue", "edit", num, "--repo", repo,
                      "--remove-label", WAITING_LABEL])
    if kind == "label" and num:
        label = (payload.get("label") or "").strip()
        if not label:
            return False, "no label given"
        steps.append(["gh", "issue", "edit", num, "--repo", repo, "--add-label", label])
    if kind == "close" and num:
        steps.append(["gh", "issue", "close", num, "--repo", repo])
    if kind == "reopen" and num:
        steps.append(["gh", "issue", "reopen", num, "--repo", repo])

    if not steps:
        return False, f"nothing to do for {kind}"

    done = []
    for cmd in steps:
        if dry:
            done.append("would " + " ".join(cmd[1:4]))
            continue
        rc, _, err = sh(cmd, timeout=60, env=env)
        verb = f"{cmd[1]} {cmd[2]}"
        if rc != 0:
            msg = (err.strip().splitlines() or ["failed"])[0][:160]
            # Removing a label the issue never had is a no-op, not a failure, and
            # failing the whole decision over it would strand a real reply.
            if "--remove-label" in cmd and ("not found" in msg.lower() or "label" in msg.lower()):
                done.append(f"{verb}: label was not set")
                continue
            return False, f"{verb}: {msg}"
        done.append(verb)
    return True, f"as {who}: " + "; ".join(done)


# While a gate is open the drain interval is the answer latency, and a two minute
# loop against a gate that fails closed is a gate that fails closed. So the job
# stays alive and polls fast, but only while somebody is actually being asked
# something, and only inside its own bounded window.
GATE_POLL_S = 8
GATE_WATCH_S = 150


def watch_gate(office: Office, access: Access, dry: bool):
    """Hold the run open while an agent is blocked, polling for the answer.

    Returns as soon as the gate clears, by any route: answered from here,
    answered at the terminal, or the runtime giving up and failing closed. It
    never outlives GATE_WATCH_S, because a job that does not exit is a job that
    collides with its own next firing.
    """
    deadline = time.monotonic() + GATE_WATCH_S
    served = 0
    while time.monotonic() < deadline:
        gate = rt.read_gate()
        if gate.get("state") != "pending":
            log("the gate cleared")
            return served
        time.sleep(GATE_POLL_S)
        served += drain_once(office, access, dry, quiet=True)
    log(f"still waiting on a gate after {GATE_WATCH_S}s; the next run picks it up")
    return served


def drain(office: Office, access: Access, dry: bool):
    n = drain_once(office, access, dry)
    gate = rt.read_gate()
    if gate.get("state") == "pending":
        log(f"a gate is open ({gate.get('permission')}); watching for an answer")
        n += watch_gate(office, access, dry)
    return n


def drain_once(office: Office, access: Access, dry: bool, quiet: bool = False):
    pending = office.call("/api/inbox").get("pending", [])
    if not pending:
        if not quiet:
            log("inbox empty")
        return 0
    log(f"{len(pending)} decision(s) waiting")
    n = 0
    for d in pending:
        ok, result = apply_decision(d, access, dry)
        log(f"  #{d['id']} {d['kind']} {d['repo']}#{d.get('issue') or '-'}: "
            f"{'ok' if ok else 'FAILED'} — {result}")
        if not dry:
            office.call(f"/api/inbox/{d['id']}", "POST",
                        {"status": "done" if ok else "failed", "result": result})
        n += ok
    access.save()
    return n


def open_office(url):
    """Open the office. It will ask for the password like any other site."""
    sh(["open", url], timeout=15)
    log(f"opened {url}")
    return 0


def check(url):
    """The live end-to-end proof, run from here so the token never crosses a shell.

    It asserts the two halves that actually matter: the view token can SEE the
    world, and the view token cannot REACH the inbox. If the second one ever
    passes, a browser can execute rather than merely ask, and that is the whole
    security model gone."""
    # The password door, checked from here so the secret never crosses a shell.
    # A login screen nobody has ever successfully walked through is a locked door
    # with a nice font, and the only way to know is to open it.
    pw = keychain("password")
    if pw:
        try:
            r = Office(url, "").call("/api/login", "POST", {"password": pw})
            log("password opens the office" if r.get("token") else "FAIL: login gave no token")
            if not r.get("token"):
                return 1
        except RuntimeError as e:
            log(f"FAIL: the stored password was refused: {e}")
            return 1
        try:
            Office(url, "").call("/api/login", "POST", {"password": pw + "x"})
            log("FAIL: a wrong password was accepted")
            return 1
        except RuntimeError as e:
            if "401" not in str(e) and "429" not in str(e):
                log(f"FAIL: wrong password refused for the wrong reason: {e}")
                return 1
            log("a wrong password is refused")
    else:
        log("no password in the keychain; skipping the door check")

    view = keychain("view")
    if not view:
        log("no view token in the keychain")
        return 2

    world = Office(url, view).call("/api/world")
    w = world.get("world")
    if not w:
        log("FAIL: authenticated, but the office holds no snapshot yet")
        return 1
    stations = w.get("stations", [])
    issues = [i for s in stations for i in s.get("issues", [])]
    waiting = [i for i in issues if i.get("bot_last")]
    locked = [s for s in stations if not s.get("access")]
    log(f"view token reads the world: snapshot {world.get('at')}")
    log(f"  {len(stations)} desks | {len(issues)} open issues | "
        f"{len(waiting)} waiting on you | {len(locked)} locked")
    log(f"  today: {w.get('today')}")
    for i in waiting[:5]:
        log(f"  waiting: #{i['number']} {i['title'][:60]}")

    try:
        Office(url, view).call("/api/inbox")
    except RuntimeError as e:
        if "401" in str(e):
            log("view token is correctly REFUSED on the runner-only inbox")
            return 0
        log(f"FAIL: inbox refused for the wrong reason: {e}")
        return 1
    log("FAIL: the view token reached the inbox. A browser could execute intent.")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--drain", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--open", action="store_true",
                    help="open the office in a browser, key attached, once")
    ap.add_argument("--cancel", metavar="ID",
                    help="drop a queued decision without applying it")
    ap.add_argument("--check", action="store_true",
                    help="prove the live surface end to end and print what it holds")
    ap.add_argument("--url", default=DEFAULT_URL)
    a = ap.parse_args()
    if not a.url:
        log("set OFFICE_URL (or pass --url) to your deployed Worker")
        return 2
    # These two never touch GitHub, so they run before anything else is set up.
    # Both need the VIEW token, which is deliberately the only place in this file
    # that reads it: it exists to leave the machine, so it leaves from one door.
    if a.open:
        return open_office(a.url)
    if a.check:
        return check(a.url)
    if a.cancel:
        # A queued intent has to be revocable. Anything a click can start and
        # nothing can stop is a button nobody should be willing to press.
        tok = keychain("push")
        if not tok:
            log("no push token in the keychain")
            return 2
        Office(a.url, tok).call(f"/api/inbox/{int(a.cancel)}", "POST",
                                {"status": "failed", "result": "cancelled before it ran"})
        log(f"decision {a.cancel} cancelled; it will not be applied")
        return 0

    do_push = a.push or not a.drain
    do_drain = a.drain or not a.push

    token = keychain("push")
    if not token:
        log(f"no push token found (keychain {KEYCHAIN_SERVICE}/push, or "
            "$OFFICE_PUSH_TOKEN). Run scripts/mint-tokens.sh first.")
        return 2

    office = Office(a.url, token)
    access = Access()
    if not access.mine:
        log("gh is not authenticated as anyone; nothing can be read or written")
        return 2
    log(f"authenticated as: {', '.join(access.mine)}")

    rc = 0
    # Drain FIRST. A decision applied before the snapshot is taken shows up in the
    # very next picture you see; the other order makes your own click look lost
    # for a whole cycle.
    if do_drain:
        try:
            drain(office, access, a.dry_run)
        except Exception as e:
            log(f"drain failed: {e}")
            rc = 1
    if do_push:
        try:
            snap = build_snapshot(access)
            if a.dry_run:
                log(f"would push {len(json.dumps(snap))} bytes")
            else:
                office.call("/api/snapshot", "POST", snap, timeout=90)
                log(f"pushed {len(json.dumps(snap))} bytes to {a.url}")
        except Exception as e:
            log(f"push failed: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
