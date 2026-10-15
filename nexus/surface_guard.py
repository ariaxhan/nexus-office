"""Refusal at Aria's surfaces (issue #273, shape 1).

Any status text the substrate sends toward Aria that asserts a broken or
unrepaired state must carry a repair reference -- an open issue plus a repair
task verified LIVE in the Tower ledger at send time. The check lives in the
composition/send path itself, not in a style guide for whoever composes: a
message without a live repair behind it does not leave.

The one allowed exception is the tier-3 escalation shape: the repair has
been attempted three times, and the message carries what broke, what was
tried, and the single thing only she can do. That message is not a status
report; it is the end of the escalation ladder, and it still carries the
repair reference.

Detection is deliberately narrow: a sentence must name a substrate subject
(a plan, the tower, a job, the feed...) together with a broken state, or use
one of the known status phrases. Narrative text about the world (a bridge
collapsed, a service's stock is down) is not a substrate status and passes.
"""

from __future__ import annotations

import re
import sqlite3

#: task states in which a repair is actually being worked (ledger.TASK_LIVE,
#: duplicated here so the client tree can import this module without the
#: ledger's migration side effects; the two must agree).
REPAIR_LIVE_STATES = ("candidate", "ranked", "accepted", "running")

_SUBJECT = (r"(tower|plan|job|jobs|feed|door|office|execution|coordinator|"
            r"worker|runner|service|system|pipeline|briefing|ledger|flight|"
            r"substrate|code-work|voice|ask worker)")
#: the shorthand pattern uses only unambiguous substrate components, so
#: narrative text ("the streaming service went down") is never a status.
_CORE = (r"(tower|plan|job|jobs|feed|door|execution|coordinator|worker|"
         r"runner|ledger|flight|code-work|ask worker)")
_STATE = (r"(stopped|down|wedged|quarantined|impaired|failing|failed|broken|"
          r"unavailable|stalled|degraded|halted|dead|blocked|parked)")

_PATTERNS = [
    # "<subject> ... is/still/remains ... <state>"
    re.compile(rf"\b{_SUBJECT}\b[^.\n]{{0,48}}?\b(is|are|was|were|still|remains|has been|have been)\b[^.\n]{{0,24}}?\b{_STATE}\b", re.I),
    # "<core subject> <state>" status shorthand: "Tower stopped plan", "plan quarantined"
    re.compile(rf"\b{_CORE}\b[^.\n]{{0,32}}?\b{_STATE}\b", re.I),
    # leading status words, the shape status emitters actually print
    re.compile(rf"^\s*(QUARANTINED|WEDGED|IMPAIRED|DEGRADED|DOWN)\b", re.M),
    # the named class: "<anything> still impaired"
    re.compile(r"\bstill\s+(impaired|broken|failing|down|wedged)\b", re.I),
]


class SurfaceBlocked(RuntimeError):
    """A broken-state message tried to reach Aria without a live repair."""


def broken_state_hits(text):
    """The broken-state assertions in `text`, as matched fragments."""
    if not text:
        return []
    hits = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(str(text)):
            fragment = match.group(0).strip()
            if fragment and fragment not in hits:
                hits.append(fragment)
    return hits


def repair_task_live(task_id, ledger_path):
    """True when the ledger holds the task in a live (being-worked) state."""
    if not task_id or not ledger_path:
        return False
    try:
        db = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True, timeout=5)
        try:
            row = db.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return False
    return row is not None and row[0] in REPAIR_LIVE_STATES


def assert_sendable(text, repair_ref=None, ledger_path=None):
    """Raise SurfaceBlocked unless `text` may be sent toward Aria.

    Clean text passes untouched. Broken-state text passes only with a
    repair reference whose task verifies live at send time, or as the
    tier-3 escalation shape (attempts >= 3, single act named).
    """
    hits = broken_state_hits(text)
    if not hits:
        return True
    ref = repair_ref or {}
    issue, task_id = ref.get("issue"), ref.get("task_id")
    if not issue or not task_id:
        raise SurfaceBlocked(
            f"broken-state status without a repair reference: {hits[0]!r} "
            "(issue #273: a fault reaches Aria only behind a live repair, "
            "or as the tier-3 escalation)")
    if ref.get("escalated") and int(ref.get("attempts", 0)) >= 3 and ref.get("single_act"):
        return True  # the escalation shape: what broke, what was tried, one act
    if repair_task_live(task_id, ledger_path or ref.get("ledger_path")):
        return True
    raise SurfaceBlocked(
        f"broken-state status whose repair is not live at send time: {hits[0]!r} "
        f"(task {task_id} not in {REPAIR_LIVE_STATES})")
