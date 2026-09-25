"""Current TBS Care intake and obligation state, from live scanner and queue receipts."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time

from sources import _card

KEY = "care"
TITLE = "Care"
MAILBOX = "hello@thinkingbrainschool.com"
BASE = pathlib.Path("CodingVault/thinking-brain-school/_meta/receipts/care-fix")
SCAN = BASE / "scan-receipt.json"
QUEUE = BASE / "queue.json"
SCAN_MAX_AGE_S = 4 * 3600
QUEUE_MAX_AGE_S = 2 * 3600
STATES = ("answered", "no-reply-owed", "waiting", "draft-held", "escalated",
          "outcome-unverified", "non-care", "we-originated")


def _root() -> pathlib.Path | None:
    value = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(value).expanduser() if value else None


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"_error": str(exc)[:160]}


def _age(stamp, now):
    parsed = _card.zulu(stamp)
    if not parsed:
        return None
    instant = dt.datetime.fromisoformat(parsed.replace("Z", "+00:00")).timestamp()
    return max(0, int(now - instant))


def _receipts(root):
    scan_path, queue_path = root / SCAN, root / QUEUE
    missing = [str(p.relative_to(root)) for p in (scan_path, queue_path) if not p.exists()]
    if missing:
        return None, None, "Missing current Care receipt: " + ", ".join(missing)
    scan, queue = _load(scan_path), _load(queue_path)
    if "_error" in scan or "_error" in queue:
        return None, None, scan.get("_error") or queue.get("_error")
    if not isinstance(queue.get("rows"), list) or not isinstance(scan, dict):
        return None, None, "Care receipts have an invalid shape"
    return scan, queue, None


def _queue_counts(queue):
    counts = {key: 0 for key in STATES}
    for row in queue["rows"]:
        state = row.get("state") if isinstance(row, dict) else None
        if state not in counts:
            return None, f"Unknown Care queue state: {state}"
        counts[state] += 1
    claimed = queue.get("summary") or {}
    if claimed.get("threads") != len(queue["rows"]) or any(
            claimed.get(key.replace("-", "_")) != count for key, count in counts.items()):
        return None, "Care queue summary disagrees with its rows"
    return counts, None


def read(now: float | None = None) -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured", "detail": "OFFICE_RUNTIME_ROOT is not set"}
    scan, queue, error = _receipts(root)
    if error:
        return {"state": "source-stale", "detail": error}

    now = time.time() if now is None else now
    scan_at = _card.zulu(scan.get("at"))
    queue_at = _card.zulu(queue.get("generated_at"))
    scan_age, queue_age = _age(scan_at, now), _age(queue_at, now)
    intake_current = (scan_age is not None and scan_age <= SCAN_MAX_AGE_S
                      and scan.get("status") == "ok" and not scan.get("errors"))
    data_current = queue_age is not None and queue_age <= QUEUE_MAX_AGE_S
    counts, error = _queue_counts(queue)
    if error:
        return {"state": "source-stale", "detail": error}

    state = ("source-stale" if not data_current else "intake-stale" if not intake_current
             else "unverified" if counts["outcome-unverified"] or counts["escalated"]
             or counts["waiting"] or counts["draft-held"] else "ok")
    return {"state": state, "mailbox": MAILBOX, "intake_current": intake_current,
            "data_current": data_current, "scan_at": scan_at, "queue_at": queue_at,
            "scan_status": scan.get("status"), "scan_errors": scan.get("errors"),
            "scan_age_s": scan_age, "queue_age_s": queue_age, "counts": counts,
            "threads": len(queue["rows"]), "rows": queue["rows"]}


def card(data: dict) -> dict:
    state = data.get("state")
    if state in ("unconfigured", "source-stale") and "counts" not in data:
        return _card.build(TITLE, data.get("detail") or "Care source unavailable", 0, "",
                           [_card.fact("source", state, "bad")])

    counts = data["counts"]
    pending = counts["waiting"] + counts["draft-held"]
    unverified = counts["outcome-unverified"]
    escalated = counts["escalated"]
    facts = [
        _card.fact("intake", "current" if data["intake_current"] else "stale or failed",
                   "ok" if data["intake_current"] else "bad"),
        _card.fact("queue evidence", "current" if data["data_current"] else "stale",
                   "ok" if data["data_current"] else "bad"),
        _card.fact("answered", counts["answered"], "dim"),
        _card.fact("no reply owed", counts["no-reply-owed"], "dim"),
        _card.fact("awaiting reply", counts["waiting"], "warn" if counts["waiting"] else "dim"),
        _card.fact("draft held", counts["draft-held"], "warn" if counts["draft-held"] else "dim"),
        _card.fact("escalated", escalated, "warn" if escalated else "dim"),
        _card.fact("outcome unverified", unverified, "bad" if unverified else "dim"),
    ]
    rows = []
    for row in reversed(data["rows"]):
        outcome = row["state"]
        if outcome in ("answered", "no-reply-owed", "non-care", "we-originated"):
            continue
        rows.append(_card.row("care-" + row["thread"], row.get("subject") or "Care inquiry",
                              row.get("last_inbound_at") or "", row.get("note") or "",
                              outcome, "bad" if outcome == "outcome-unverified" else "warn"))
        if len(rows) == 30:
            break
    if not data["data_current"]:
        headline = "Care queue source is stale; current customer outcomes are unknown"
    elif not data["intake_current"]:
        headline = "Care intake is stale or failed; recent mail may be missing"
    elif unverified or escalated:
        headline = f"Care has {unverified} unverified outcomes and {escalated} escalations"
    elif pending:
        headline = f"Care has {pending} {_card.plural(pending, 'obligation')} awaiting a reply or draft"
    else:
        headline = "Care intake current; no unresolved obligations in the queue"
    return _card.build(TITLE, headline, 0, data.get("queue_at") or "", facts, rows)
