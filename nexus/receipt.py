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
import re
import subprocess

OUTCOMES = ("done", "partial", "failed", "blocked")
FIELDS = ("outcome", "artifacts", "verification", "evidence", "next")
LISTS = {"artifacts", "evidence"}
FENCE = "receipt"
REJECTION_MARK = "<!-- nexus:receipt-rejection -->"
MAX_ITEM = 300
MAX_ITEMS = 20
# Words a lane uses when it means "I am finished". A comment saying one of them without a
# receipt is a prose completion; a comment saying none of them is just talk and is left alone.
CLAIM = re.compile(r"\b(outcome\s*:|done|fixed|complete[d]?|shipped|resolved|landed|merged)\b", re.I)
BLOCK = re.compile(r"```" + FENCE + r"[ \t]*\n(.*?)\n```", re.S)


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


def consume(led, repo, number, body, comment_url=None, run=None):
    """The consumer side: record the transition; a prose completion is rejected on the issue."""
    subject = f"{repo}#{number}"
    state, detail = judge(body)
    if state is None:
        return None, None
    if state == "RECEIPT":
        led.event("receipt.accepted", subject, {"comment": comment_url, "receipt": detail}, "receipt")
        return state, detail
    proc = (run or subprocess.run)(["gh", "issue", "comment", str(number), "-R", repo, "--body", rejection(detail, comment_url)],
               capture_output=True, text=True, timeout=60)
    posted = proc.stdout.strip() if proc.returncode == 0 else None
    led.event("receipt.rejected", subject, {"comment": comment_url, "why": detail, "rejection": posted,
                                            "rejection_error": None if posted else (proc.stderr or "")[-300:]},
              "receipt")
    return state, {"why": detail, "rejection": posted}


def latest_comment(repo, number, run=None):
    proc = (run or subprocess.run)(["gh", "api", f"repos/{repo}/issues/{number}/comments?per_page=100"],
               capture_output=True, text=True, timeout=30)
    if proc.returncode:
        raise ReceiptError(f"cannot read comments: {proc.stderr.strip()[-200:]}")
    rows = [c for c in json.loads(proc.stdout) if REJECTION_MARK not in (c.get("body") or "")]
    if not rows:
        raise ReceiptError("no comments to judge")
    return rows[-1]
