"""Terminal state needs terminal evidence.

unknown != success. attempted != completed. accepted != executed. executed != verified.
The ledger calls `require` on every success-terminal write, so a caller cannot reach
`verified`, `landed` or task `done` without handing over the proof that state needs.
"""


class IllegalTransition(Exception):
    pass


def landed(result, receipt):
    """A write flight: a LANDED result with a sha, and a receipt that names that sha."""
    return {"kind": "landed", "result": result or {}, "receipt": receipt}


def delivered(result):
    """A work item: a delivery proof that says verified and carries evidence."""
    return {"kind": "delivered", "result": result or {}}


def outputs(result):
    """A script flight: the runner's declared-output check passed."""
    return {"kind": "outputs", "result": result or {}}


def applied(sha):
    """A landing whose commit was pushed."""
    return {"kind": "applied", "sha": sha}


def closed(receipt, source):
    """An issue-backed task: a receipt, and the source confirmed closed."""
    return {"kind": "closed", "receipt": receipt, "source": source}


def _landed(ev):
    result, sha = ev["result"], ev["result"].get("sha")
    if result.get("state") != "LANDED" or not sha:
        return f"no landed commit ({result.get('state')})"
    if not ev.get("receipt") or str(sha) not in str(ev["receipt"]):
        return f"no receipt naming {sha}"
    return None


def _delivered(ev):
    result = ev["result"]
    ok = result.get("verified") is True and isinstance(result.get("evidence"), list) and result["evidence"]
    return None if ok else "delivery proof missing"


def _outputs(ev):
    result = ev["result"]
    return None if result.get("ok") is True and not result.get("error") else "runner did not report ok"


def _applied(ev):
    return None if ev.get("sha") else "no applied sha"


def _closed(ev):
    if not ev.get("receipt"):
        return "no receipt"
    return None if ev.get("source") == "closed" else f"source not confirmed closed ({ev.get('source')})"


CHECKS = {"landed": _landed, "delivered": _delivered, "outputs": _outputs,
          "applied": _applied, "closed": _closed}

#: which evidence may carry an entity into a success-terminal state
ALLOWED = {
    ("flight", "verified"): ("landed", "delivered", "outputs"),
    ("flight", "landed"): ("landed", "applied"),
    ("task", "done"): ("closed", "applied", "outputs"),
}

#: an outcome that proves nothing; it never settles anything
UNPROVEN = {"unknown", "timeout", "timed_out", "failed", "error", "crashed"}


def _unproven(ev):
    result = ev.get("result") or {}
    words = {str(result.get(k, "")).lower() for k in ("state", "outcome", "status")}
    code = result.get("returncode", result.get("exit"))
    if words & UNPROVEN or (code not in (None, 0)):
        return f"unproven outcome {sorted(words & UNPROVEN) or code}"
    return None


def require(kind, state, evidence):
    """Raise IllegalTransition unless `evidence` proves `kind` may enter `state`."""
    allowed = ALLOWED.get((kind, state))
    if allowed is None:
        return
    if not isinstance(evidence, dict) or evidence.get("kind") not in allowed:
        raise IllegalTransition(f"{kind} -> {state} needs {' | '.join(allowed)} evidence")
    why = _unproven(evidence) or CHECKS[evidence["kind"]](evidence)
    if why:
        raise IllegalTransition(f"{kind} -> {state}: {why}")
