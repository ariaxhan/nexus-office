"""Lane receipt grammar (#291): completion is five fixed fields, never prose.

    ```receipt
    outcome: done | partial | failed | blocked
    artifacts:
    - <durable pointer: commit, file, receipt url>
    verification: <the surface where the outcome is proven, named exactly>
    evidence:
    - <bounded fact from that surface: id, timestamp, count>
    next: none | <owner>: <what>
    ```

`emit` is the only way a lane should write one. `consume` is the enforcement: a completion
claim without a parseable receipt goes back to CLAIMED with the rejection posted on the issue,
so a lane learns the grammar from the rejection, not from a style guide.
"""

from __future__ import annotations

import json
import datetime
import re
import subprocess

OUTCOMES = ("done", "partial", "failed", "blocked")
FIELDS = ("outcome", "artifacts", "verification", "evidence", "next")
LISTS = {"artifacts", "evidence"}
FENCE = "receipt"
REJECTION_MARK = "<!-- nexus:receipt-rejection -->"
MAX_ITEM = 300
MAX_ITEMS = 20
# The existing Tower consumer recognizes informal completion words. The autonomous coordinator
# poller below uses a narrower declaration so ordinary discussion does not get a rejection.
CLAIM = re.compile(r"\boutcome\W*(?:done|partial|failed|blocked)\b|\boutcome\s*:|\b(done|fixed|complete[d]?|shipped|resolved|landed|merged)\b", re.I)
EXPLICIT_CLAIM = re.compile(
    r"(?im)^\s*(?:#{1,3}\s*)?(?:\*\*)?outcome\s*:|"
    r"^\s*(?:#{1,3}\s*)?(?:done|fixed|completed?|shipped|resolved|landed|merged)\s*[.!]?\s*$")
TRUSTED_AUTHORS = frozenset({'OWNER', 'MEMBER', 'COLLABORATOR'})
BLOCK = re.compile(r"```" + FENCE + r"[ \t]*\n(.*?)\n```", re.S)
COMMIT = re.compile(r"commit ([0-9a-f]{40})\Z")


class ReceiptError(ValueError):
    pass


def _one_line(value, field):
    text = " ".join(str(value).split())
    if not text:
        raise ReceiptError(f"{field}: empty")
    if len(text) > MAX_ITEM:
        raise ReceiptError(f"{field}: over {MAX_ITEM} characters; evidence is bounded facts, not a transcript")
    return text


def _next(value):
    text = _one_line(value, "next")
    if text != "none" and not re.fullmatch(r"[^:\s][^:]*:\s*\S.*", text):
        raise ReceiptError("next: 'none' or '<owner>: <what>'")
    return text


def validate(receipt):
    """The canonical dict, or ReceiptError naming the first broken field."""
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt is not a mapping")
    extra = set(receipt) - set(FIELDS)
    if extra:
        raise ReceiptError(f"unknown field(s): {', '.join(sorted(extra))}")
    missing = [f for f in FIELDS if f not in receipt]
    if missing:
        raise ReceiptError(f"missing field(s): {', '.join(missing)}")
    outcome = str(receipt["outcome"]).strip().lower()
    if outcome not in OUTCOMES:
        raise ReceiptError(f"outcome: one of {' | '.join(OUTCOMES)}, got {receipt['outcome']!r}")
    out = {"outcome": outcome}
    for field in ("artifacts", "evidence"):
        items = receipt[field]
        if isinstance(items, str) or not isinstance(items, (list, tuple)):
            raise ReceiptError(f"{field}: a list")
        if len(items) > MAX_ITEMS:
            raise ReceiptError(f"{field}: at most {MAX_ITEMS} items")
        out[field] = [_one_line(i, field) for i in items]
    if not out["evidence"]:
        raise ReceiptError("evidence: at least one bounded fact from the verification surface")
    if outcome == "done" and not out["artifacts"]:
        raise ReceiptError("artifacts: done needs at least one durable pointer verification can read")
    out["verification"] = _one_line(receipt["verification"], "verification")
    out["next"] = _next(receipt["next"])
    return {f: out[f] for f in FIELDS}


def emit(outcome, artifacts, verification, evidence, next="none"):
    """The receipt block, mechanically. Raises before anything unparseable can be posted."""
    r = validate({"outcome": outcome, "artifacts": list(artifacts), "verification": verification,
                  "evidence": list(evidence), "next": next})
    lines = [f"outcome: {r['outcome']}", "artifacts:", *[f"- {a}" for a in r["artifacts"]],
             f"verification: {r['verification']}", "evidence:", *[f"- {e}" for e in r["evidence"]],
             f"next: {r['next']}"]
    block = "```" + FENCE + "\n" + "\n".join(lines) + "\n```"
    assert parse(block) == r  # the emitter and the parser are one grammar
    return block


def parse(text):
    """The one receipt in `text`, validated; ReceiptError if none, several, or malformed."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        raise ReceiptError("no ```receipt block")
    if len(blocks) > 1:
        raise ReceiptError("more than one ```receipt block")
    raw, current, order = {}, None, []
    for n, line in enumerate(blocks[0].splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith("- "):
            if current not in LISTS:
                raise ReceiptError(f"line {n}: list item outside artifacts/evidence")
            raw[current].append(line[2:])
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or key not in FIELDS:
            raise ReceiptError(f"line {n}: expected one of {', '.join(FIELDS)}")
        if key in raw:
            raise ReceiptError(f"line {n}: {key} repeated")
        order.append(key)
        if key in LISTS:
            if value.strip():
                raise ReceiptError(f"line {n}: {key} items go on '- ' lines below it")
            raw[key], current = [], key
        else:
            raw[key], current = value.strip(), None
    if order != [f for f in FIELDS if f in raw]:
        raise ReceiptError(f"fields out of order; the grammar is {', '.join(FIELDS)}")
    return validate(raw)


def is_claim(text):
    return bool(text) and REJECTION_MARK not in text and bool(BLOCK.search(text) or CLAIM.search(text))


def judge(text):
    """('RECEIPT', receipt) for a parseable completion, ('CLAIMED', why) for a rejected one, (None, None) for talk."""
    if not is_claim(text):
        return None, None
    try:
        return "RECEIPT", parse(text)
    except ReceiptError as exc:
        return "CLAIMED", str(exc)


def rejection(why, comment_url=None):
    where = f" ({comment_url})" if comment_url else ""
    return (f"{REJECTION_MARK}\n**Completion rejected: no parseable receipt{where}.** {why}.\n\n"
            "State stays CLAIMED. Post the outcome in the lane receipt grammar "
            "(`python3 -m nexus receipt emit`):\n\n"
            "```receipt\noutcome: done | partial | failed | blocked\nartifacts:\n- <commit, file or receipt url>\n"
            "verification: <the surface where it is proven, named exactly>\nevidence:\n- <id, timestamp or count>\n"
            "next: none | <owner>: <what>\n```")


def state(led, repo, number):
    """The item's receipt state as the consumer last recorded it: {} (never judged), CLAIMED or RECEIPT."""
    rows = led.events(kind="receipt.state", subject=f"{repo}#{number}")
    return json.loads(rows[-1]["payload"]) if rows else {}


def consume(led, repo, number, body, comment_url=None, run=None, verifier=None):
    """The one writer of the item's receipt state, from a comment as it stands on the issue.

    RECEIPT for a parseable receipt; CLAIMED for a prose completion, with the rejection posted on
    the issue once per comment. Talk changes nothing."""
    subject = f"{repo}#{number}"
    verdict, detail = judge(body)
    if verdict is None:
        return None, None
    proof = None
    if verdict == "RECEIPT" and verifier is not None:
        try:
            proof = verifier(repo, number, detail)
        except ReceiptError as exc:
            verdict, detail = "CLAIMED", str(exc)
    if verdict == "RECEIPT":
        led.event("receipt.state", subject, {"state": "RECEIPT", "comment": comment_url,
                                             "receipt": detail, "proof": proof}, "receipt")
        return verdict, detail
    prior = state(led, repo, number)
    posted = prior.get("rejection") if prior.get("comment") == comment_url and comment_url else None
    error = None
    if not posted:
        proc = (run or subprocess.run)(["gh", "issue", "comment", str(number), "-R", repo,
                                        "--body", rejection(detail, comment_url)],
                                       capture_output=True, text=True, timeout=60)
        posted = proc.stdout.strip() if proc.returncode == 0 else None
        error = None if posted else (proc.stderr or "")[-300:]
    led.event("receipt.state", subject, {"state": "CLAIMED", "comment": comment_url, "why": detail,
                                         "rejection": posted, "rejection_error": error}, "receipt")
    return verdict, {"why": detail, "rejection": posted}


def check(led, repo, number, run=None, timeout=60):
    """Judge the issue's newest comment, read from GitHub, never from the caller's copy."""
    row = latest_comment(repo, number, run=run, timeout=timeout)
    return consume(led, repo, number, row.get("body") or "", row.get("html_url"), run=run)


def publish(led, repo, number, body, run=None, timeout=60):
    """Post a receipt, then let the consumer read it back off the issue.

    The accepted receipt, or None: a failed post, or an issue whose newest comment is not this
    receipt, leaves the state where it was. Nothing local stands in for the issue."""
    proc = (run or subprocess.run)(["gh", "issue", "comment", str(number), "-R", repo, "--body", body],
                                   capture_output=True, text=True, timeout=timeout)
    if proc.returncode or not proc.stdout.strip():
        led.event("receipt.unposted", f"{repo}#{number}", {"error": (proc.stderr or "")[-300:]}, "receipt")
        return None
    try:
        verdict, got = check(led, repo, number, run=run, timeout=timeout)
    except (ReceiptError, ValueError, OSError, subprocess.SubprocessError) as exc:
        led.event("receipt.unread", f"{repo}#{number}", {"error": str(exc)[-300:]}, "receipt")
        return None
    return got if verdict == "RECEIPT" and got == parse(body) else None


def latest_comment(repo, number, run=None, timeout=60):
    """The newest non-rejection comment, across every page."""
    proc = (run or subprocess.run)(["gh", "api", "--paginate", "--slurp",
                                    f"repos/{repo}/issues/{number}/comments?per_page=100"],
                                   capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise ReceiptError(f"cannot read comments: {proc.stderr.strip()[-200:]}")
    rows = [c for page in json.loads(proc.stdout) for c in page if REJECTION_MARK not in (c.get("body") or "")]
    if not rows:
        raise ReceiptError("no comments to judge")
    return rows[-1]


def latest_claim(repo, number, since, run=None, timeout=60):
    """Newest trusted completion claim after this claim began, preferring a valid receipt."""
    proc = (run or subprocess.run)(["gh", "api", "--paginate", "--slurp",
                                    f"repos/{repo}/issues/{number}/comments?per_page=100"],
                                   capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise OSError(f"cannot read comments: {(proc.stderr or '')[-200:]}")
    rows = []
    for page in json.loads(proc.stdout):
        for comment in page:
            if not isinstance(comment, dict) or comment.get('author_association') not in TRUSTED_AUTHORS:
                continue
            try:
                at = datetime.datetime.fromisoformat(comment['created_at'].replace('Z', '+00:00')).timestamp()
            except (KeyError, AttributeError, ValueError):
                continue
            body = comment.get('body') or ''
            if at >= since and REJECTION_MARK not in body and (BLOCK.search(body) or EXPLICIT_CLAIM.search(body)):
                rows.append(comment)
    for comment in reversed(rows):
        try:
            parse(comment['body'])
            return comment
        except ReceiptError:
            continue
    return rows[-1] if rows else None


def verify_github_commit(repo, _number, completion, claimed_at, run=None, timeout=30):
    """Coordinator receipt verifier: read its commit from GitHub, fail closed on any other surface."""
    if completion["outcome"] != "done" or completion["verification"] != "GitHub commit":
        raise ReceiptError("coordinator receipt requires outcome done and verification: GitHub commit")
    commits = [match.group(1) for item in completion["artifacts"]
               if (match := COMMIT.fullmatch(item))]
    if len(commits) != 1:
        raise ReceiptError("coordinator receipt requires exactly one artifact: commit <40-char sha>")
    sha = commits[0]
    proc = (run or subprocess.run)(["gh", "api", f"repos/{repo}/commits/{sha}"],
                                   capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise OSError(f"commit verification unavailable: {(proc.stderr or '')[-200:]}")
    commit = json.loads(proc.stdout)
    if commit.get('sha') != sha:
        raise ReceiptError("GitHub returned a different commit than the receipt artifact")
    try:
        stamped = datetime.datetime.fromisoformat(
            commit['commit']['committer']['date'].replace('Z', '+00:00')).timestamp()
    except (KeyError, AttributeError, ValueError) as exc:
        raise ReceiptError('GitHub commit has no readable committer date') from exc
    if stamped < claimed_at:
        raise ReceiptError('commit predates the coordinator claim')
    root = (run or subprocess.run)(["gh", "api", f"repos/{repo}", "--jq", ".default_branch"],
                                   capture_output=True, text=True, timeout=timeout)
    if root.returncode:
        raise OSError(f"default branch unavailable: {(root.stderr or '')[-200:]}")
    branch = root.stdout.strip()
    if not re.fullmatch(r'[\w./-]{1,100}', branch) or '..' in branch:
        raise ReceiptError('GitHub returned an invalid default branch')
    compare = (run or subprocess.run)(["gh", "api", f"repos/{repo}/compare/{sha}...{branch}",
                                       "--jq", ".status"], capture_output=True, text=True, timeout=timeout)
    if compare.returncode:
        raise OSError(f"commit reachability unavailable: {(compare.stderr or '')[-200:]}")
    if compare.stdout.strip() not in {'ahead', 'identical'}:
        raise ReceiptError('commit is not on the repository default branch')
    return {"surface": "GitHub commit", "repo": repo, "sha": sha, "branch": branch,
            "committer_at": stamped}
