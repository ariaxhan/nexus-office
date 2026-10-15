"""Lane receipt grammar (#291): a completion is a structured receipt, never prose.

A lane finishes by posting one fenced block on the work's issue:

    ```receipt
    outcome: done
    artifacts:
    - commit 993f534
    verification: origin/main contains 993f534
    evidence:
    - git branch -r --contains 993f534 -> origin/main
    next: none
    ```

Five fields, this order, each exactly once. `emit` writes it, so nobody types it from
memory; `consume` is the only reader. A completion without a parseable receipt does not
advance: it goes back to CLAIMED with the reasons, which are posted on the issue so the
lane learns the grammar from the rejection.
"""

FENCE = "```receipt"
OUTCOMES = ("done", "partial", "failed", "blocked")
FIELDS = ("outcome", "artifacts", "verification", "evidence", "next")
LISTS = ("artifacts", "evidence")


class Rejected(ValueError):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def _line(value):
    text = " ".join(str(value).split())
    if not text:
        raise ValueError("empty receipt value")
    return text


def emit(outcome, artifacts, verification, evidence, next="none"):
    """The receipt block for a comment body. Raises Rejected if it would not parse."""
    lines = [FENCE, f"outcome: {outcome}", "artifacts:"]
    lines += [f"- {_line(a)}" for a in artifacts]
    lines += [f"verification: {_line(verification)}", "evidence:"]
    lines += [f"- {_line(e)}" for e in evidence]
    lines += [f"next: {_line(next)}", "```"]
    body = "\n".join(lines)
    parse(body)
    return body


def _block(body):
    lines = str(body or "").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == FENCE]
    if not starts:
        raise Rejected(["no ```receipt block: prose is not a completion"])
    if len(starts) > 1:
        raise Rejected([f"{len(starts)} receipt blocks; post exactly one"])
    rest = lines[starts[0] + 1:]
    end = next((i for i, line in enumerate(rest) if line.strip() == "```"), None)
    if end is None:
        raise Rejected(["receipt block is not closed with ```"])
    return [line.rstrip() for line in rest[:end] if line.strip()]


def _read(lines):
    """Lines to fields, in the order given; every line that is not grammar is a reason."""
    fields, order, reasons, current = {}, [], [], None
    for line in lines:
        if line.lstrip().startswith("- "):
            if current in LISTS and isinstance(fields[current], list):
                fields[current].append(line.lstrip()[2:].strip())
            else:
                reasons.append(f"list item outside artifacts/evidence: {line.strip()[:60]}")
            continue
        key, sep, value = (part.strip() for part in line.partition(":"))
        current = key if sep and key in FIELDS else None
        if current is None:
            reasons.append(f"unknown line: {line.strip()[:60]}")
            continue
        if key in fields:
            reasons.append(f"{key} given twice")
        order.append(key)
        if key in LISTS and value not in ("", "none"):
            reasons.append(f"{key} must be '- ' items, one per line, or 'none'")
        fields[key] = [] if key in LISTS else value
    return fields, order, reasons


def _check(fields, order):
    missing = [f for f in FIELDS if f not in fields]
    if missing:
        yield "missing " + ", ".join(missing)
    elif order != list(FIELDS):
        yield "fields out of order; use " + ", ".join(FIELDS)
    outcome = fields.get("outcome")
    if "outcome" in fields and outcome not in OUTCOMES:
        yield f"outcome '{outcome}' is not one of {' | '.join(OUTCOMES)}"
    if fields.get("verification", "x") in ("", "none"):
        yield "verification must name the surface where the outcome is proven"
    if outcome == "done":
        yield from (f"done needs at least one {key} item" for key in LISTS if fields.get(key) == [])
    if "next" in fields and fields["next"] != "none" and not _named(fields["next"]):
        yield "next must be 'none' or '<owner>: <what>'"


def parse(body):
    """The receipt in `body` as a dict, or Rejected naming every defect."""
    fields, order, reasons = _read(_block(body))
    reasons += list(_check(fields, order))
    if reasons:
        raise Rejected(reasons)
    return {k: fields[k] for k in FIELDS}


def _named(value):
    owner, sep, what = str(value or "").partition(":")
    return bool(sep and owner.strip() and what.strip())


def consume(body):
    """The state transition a completion claim earns: RECEIPT with the receipt, or CLAIMED with reasons."""
    try:
        return {"transition": "RECEIPT", "receipt": parse(body)}
    except Rejected as exc:
        return {"transition": "CLAIMED", "reasons": exc.reasons}


def rejection(reasons, source=None):
    """The comment that returns a prose completion to CLAIMED and teaches the grammar."""
    where = f" ({source})" if source else ""
    lines = [f"Receipt rejected{where}: this completion does not advance; the item stays CLAIMED.", ""]
    lines += [f"- {r}" for r in reasons]
    lines += ["", "Post exactly one block in this grammar (`python3 -m nexus receipt emit` writes it):", "",
              FENCE, "outcome: done | partial | failed | blocked", "artifacts:", "- <commit, file or receipt>",
              "verification: <the surface where the outcome is proven>", "evidence:",
              "- <bounded fact: id, timestamp, count>", "next: none | <owner>: <what>", "```"]
    return "\n".join(lines)
