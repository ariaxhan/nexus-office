"""The Corpus Grader, in shadow: what would the grader have decided about the replies we sent.

The grader grades every care reply we SENT against the human-decision corpus, WITHOUT sending
anything itself. This card shows whether it agrees with what a person actually did, so we can trust
it before it ever gates a send:

  we sent it  +  grader PASS               -> AGREE    (it would have shipped it too)
  we sent it  +  grader REFUSE / ESCALATE  -> DISAGREE (it would have held it) <- the signal

`needs` is the disagreement count: a reply a person sent that the grader would have held is the one
thing here worth a human's eye. Everything else is dim data that accumulates on its own.

WHICH FILE THIS READS
---------------------
`_meta/logs/care-grader-shadow.jsonl`, appended by shadow_batch.py (nightly). One local JSONL read,
per-line fail-soft: a torn line costs that row, never the card. Row:
  {ts, email, subject, decision (PASS|REFUSE|ESCALATE), reason, human_action, agreement, sent_at}

STATES, IN ORDER
----------------
  unconfigured  no vault to read from
  never         the log does not exist: the shadow job has not run once. Not "clear".
  empty         the log exists but has no verdicts yet (the honest window can be empty). Not green.
  unreadable    every line was torn
  ok            verdicts present; agreement + breakdown shown

`empty` and `never` are distinct and neither is false-green: an unread grader looks exactly like a
grader that agreed with everything, and that is the false-green this office exists to kill.
"""

from __future__ import annotations

import json
import datetime
import os
import pathlib
import time

from sources import _card

KEY = "care-grader"
TITLE = "Care Grader (shadow)"

LOG = "_meta/logs/care-grader-shadow.jsonl"
MAIL_MIRROR = "CodingVault/tbs-care/.local/mail"
CARE_QUEUE = "CodingVault/thinking-brain-school/_meta/receipts/care-fix/queue.json"
MIRROR_MAX_AGE_S = 48 * 3600
RECENT = 20

TROUBLE = {
    "unconfigured": ("No vault to read the shadow log from", 0),
    "never": ("The shadow grader has never run", 1),
    "unreadable": ("The shadow log is torn", 1),
}


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _coverage(root, rows, now):
    mirror = root / MAIL_MIRROR
    mail_files = [p for p in mirror.rglob("*") if p.is_file()] if mirror.exists() else []
    mirror_at = max((p.stat().st_mtime for p in mail_files), default=None)
    mirror_current = mirror_at is not None and now - mirror_at <= MIRROR_MAX_AGE_S
    latest_care_send = ""
    queue_path = root / CARE_QUEUE
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
            latest_care_send = max((r.get("sent_at") or "" for r in queue.get("rows", [])), default="")
        except (OSError, ValueError):
            pass
    latest_graded_send = max((r.get("sent_at") or "" for r in rows), default="")
    return {"mirror_at": (None if mirror_at is None else
                          datetime.datetime.fromtimestamp(mirror_at, datetime.timezone.utc).isoformat()),
            "latest_care_send": latest_care_send, "latest_graded_send": latest_graded_send,
            "coverage_current": mirror_current and (not latest_care_send or latest_graded_send >= latest_care_send)}


def read(now=None) -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "OFFICE_RUNTIME_ROOT is not set, so there is no shadow log to read"}
    path = root / LOG
    if not path.exists():
        return {"state": "never", "detail": f"no {LOG}; the nightly shadow grader has not run yet"}

    rows, torn = [], 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"state": "unreadable", "detail": str(exc)[:160]}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            torn += 1

    if not rows:
        if torn:
            return {"state": "unreadable", "detail": f"{torn} torn line(s), no readable verdict"}
        return {"state": "empty", "detail": "the log exists but holds no verdicts yet"}

    by_decision: dict[str, int] = {}
    agree = disagree = 0
    for r in rows:
        d = r.get("decision", "?")
        by_decision[d] = by_decision.get(d, 0) + 1
        a = r.get("agreement")
        if a == "agree":
            agree += 1
        elif a == "disagree":
            disagree += 1

    scored = agree + disagree
    now = time.time() if now is None else now
    coverage = _coverage(root, rows, now)
    return {
        "state": "ok" if coverage["coverage_current"] else "coverage-stale",
        "total": len(rows),
        "by_decision": by_decision,
        "agree": agree,
        "disagree": disagree,
        "scored": scored,
        "agreement_pct": None if not scored else round(100 * agree / scored),
        "torn": torn,
        "as_of": _card.zulu(rows[-1].get("ts")),
        **coverage,
        "recent": rows[-RECENT:],
    }


def _facts(data, when):
    bd = data.get("by_decision") or {}
    disagree = data.get("disagree", 0)
    pct = data.get("agreement_pct")
    facts = [_card.fact("graded historically", str(data["total"]), "dim")]
    facts.append(_card.fact("current coverage", "yes" if data.get("coverage_current") else "no — source or grading stale",
                            "ok" if data.get("coverage_current") else "bad"))
    if pct is not None:
        facts.append(_card.fact("agreement", f"{pct}%", "ok" if pct >= 90 else "warn"))
    facts.append(_card.fact("would hold", str(disagree), "bad" if disagree else "ok"))
    facts.append(_card.fact("decisions", ", ".join(f"{k} {v}" for k, v in sorted(bd.items())),
                            "warn" if (bd.get("REFUSE", 0) or bd.get("ESCALATE", 0)) else "dim"))
    if data.get("torn"):
        facts.append(_card.fact("torn lines", str(data["torn"]), "warn"))
    facts.append(_card.fact("last graded", when, "dim"))
    return facts


def card(data: dict) -> dict:
    state = data.get("state")
    if state in TROUBLE:
        return _card.trouble(TITLE, state, data.get("detail"), TROUBLE)

    if state == "empty":
        # ran, nothing to grade yet. Named + dim, explicitly NOT a green all-clear.
        facts = [_card.fact("verdicts", "0", "dim"),
                 _card.fact("status", "waiting for new sends", "dim")]
        return _card.build(TITLE, "No care replies graded yet", 0, "", facts, [])

    as_of = data.get("as_of") or ""
    when = _card.ago(as_of) or "unknown"
    disagree = data.get("disagree", 0)
    pct = data.get("agreement_pct")
    facts = _facts(data, when)

    rows = []
    for r in data.get("recent") or []:
        dec = r.get("decision", "?")
        agr = r.get("agreement")
        tone = "bad" if agr == "disagree" else ("ok" if agr == "agree" else "")
        email = r.get("email")
        email = email[0] if isinstance(email, list) and email else (email or "")
        rows.append(_card.row(
            f"cg-{r.get('sent_at') or r.get('ts') or ''}",
            (r.get("subject") or "(no subject)")[:60],
            str(email)[:60],
            (r.get("reason") or "")[:120],
            dec, tone))
    rows.reverse()  # newest first

    if not data.get("coverage_current"):
        headline = "Shadow grader has historical verdicts; current replies are not covered"
    elif disagree:
        headline = f"{disagree} sent {_card.plural(disagree, 'reply')} the grader would have held"
    elif pct is not None:
        headline = f"Grader agrees with all {data['scored']} scored sends; last {when}"
    else:
        headline = f"{data['total']} graded, none scored yet; last {when}"
    return _card.build(TITLE, headline, disagree if data.get("coverage_current") else 0, as_of, facts, rows)
