"""money-swarm, seen from the office. A window, never a second ledger.

money-swarm (`<root>/money-swarm`) is an existing local revenue system: it scores
lanes, queues actions, and refuses to send anything identity-bearing until Aria
approves that exact draft. It already owns all of this. Nothing here writes, and
nothing here re-derives a number money-swarm already computed.

The office asks it exactly one question first: **how many things are waiting on
Aria.** Everything else on the card is context for that number.

THE WAITING RULE IS NOT INVENTED HERE. It is money-swarm's own, in
`automation/digest.py:41-48`, which prints it as "Decisions requiring Aria":

    completed_ids = {
        row["action_id"] for row in outcomes
        if ((row.get("kind") == "action_completed" and row.get("status") == "completed")
            or (row.get("kind") == "action_superseded" and row.get("status") == "superseded"))
        and row.get("action_id")
    }
    open_actions = [row for row in actions if row["id"] not in completed_ids]
    aria = [row for row in open_actions if row.get("requires_aria")]

An action is created with `requires_aria` and `state` in lockstep
(`automation/agents.py:52`: `"approval_required" if requires_aria else "draft"`),
so either field alone raises the hand and both are checked rather than one.

There is a SECOND way to wait, which digest does not count and a window must.
`automation/policy.py:196-200`:

    if kind in RING1_KINDS:
        envelope = _live_envelope(now)
        if envelope is None:
            reasons.append("ring1 kind with no live signed envelope; escalate")
            return {"ring": 1, "escalate": True, ...}

A Ring 1 kind is an identity-bearing send. No signed envelope exists, so every
one of them escalates to a person whatever its own flags say. An escalated action
is waiting until somebody decides, and it stays waiting even at `requires_aria:
false`, because in money-swarm authority comes from typed fields and never from
what a row says about itself.

"Decided" is `automation/approval.py::_matching_from_rows`: a live approval is
`status == "approved"`, on this `action_id`, with a `draft_hash` equal to the
sha256 of the exact action, not superseded by a `consumed` or `revoked` row, and
not expired. Anything short of that and money-swarm itself escalates again, so
anything short of that still counts as waiting here.

Freshness has its own single source of truth, and money-swarm says so out loud in
`automation/scheduler.py::write_current_health`:

    2026-07-19 repair: after a failed run, scheduler-runs.jsonl was current but
    run-history and the digest stayed dated, which was too easy to misread. This
    file is the single place that is always true, and it names the evidence cutoff.

So `state/runtime/current-health.json` is the health, and `red` is red. It matters
that `cycle.py` appends a run-history row ONLY after `validate()` returned, since
validate raises on failure: a red validate leaves no receipt at all, and an
office reading run-history alone would draw a healthy board over a broken system.
That is the exact false-green this file exists to refuse.

Every lane value is a projection. `expected_value` is probability times price
(`lane-job-response-engine`: 0.02 x $200,000 = $4,000), so it travels wrapped with
`estimate: true` and the arithmetic that produced it. Measured money in
money-swarm is a `verified_received` row in `metrics/outcomes.jsonl` and is a
different number entirely; flattening the two would be a guess with a decimal
point on it.

States, for the reason `sections.py` gives: "nothing configured", "not installed",
"the ledger is broken", "the data is old" and "genuinely nothing waiting" are five
different things and must never render the same.

  unconfigured  no OFFICE_RUNTIME_ROOT
  missing       no money-swarm installed under that root
  unreadable    the action queue will not parse, so what waits cannot be said
  stale         the last run or the last validate is older than its own max age,
                or the health file is red. Rows are still shown; `detail` says why
  ok            we could tell
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import pathlib
import time

from sources import _card

KEY = "money_swarm"
# The human name of the fixture, fixed. A card whose title moves is a
# card the eye has to find again every time it is drawn.
TITLE = "Money swarm"

SWARM = "money-swarm"

ACTIONS = "state/actions.jsonl"
APPROVALS = "state/approvals.jsonl"
OUTCOMES = "metrics/outcomes.jsonl"
RUN_HISTORY = "state/run-history.jsonl"
HEALTH = "state/runtime/current-health.json"
ENVELOPE = "state/policy-envelope.json"
PORTFOLIO = "opportunities/portfolio.jsonl"
CURRENT = "opportunities/current.jsonl"
PROMOTIONS = "state/promotions.jsonl"

# The scheduler runs every six hours while the Mac is awake, and money-swarm's
# README is explicit that intervals missed during sleep are NOT replayed. So six
# hours is the schedule, not the alarm: a laptop shut overnight would trip it
# every morning, and an alarm that cries every morning is an alarm nobody reads.
# A full day plus slack is the honest bar for "this stopped running".
MAX_RUN_AGE_S = 26 * 3600
MAX_VALIDATE_AGE_S = 26 * 3600

# Identity-bearing kinds, copied from policy.py RING1_KINDS. Copied rather than
# imported: importing money-swarm's modules would run its code inside the
# snapshot push, and this list changes by edit and review, never at runtime.
RING1_KINDS = frozenset({
    "outreach-email",
    "reply",
    "negotiation-move",
    "application-submit",
    "recruiter-message",
})

# An action that has already happened or been replaced is not waiting on anyone.
# digest.py's completed_ids, as (kind, status) pairs.
CLOSING = frozenset({
    ("action_completed", "completed"),
    ("action_superseded", "superseded"),
})

# Where a Ring 1 action carries the words that would be sent. No action row on
# disk carries one today (drafts live in their own file, hashed by approval.py),
# so these are the fields one will land in, in preference order, and an absent
# draft is reported as absent rather than as an empty string.
DRAFT_FIELDS = ("draft", "draft_text", "body", "message", "proposed_message")
DRAFT_CHARS = 1500

MAX_LANES = 10

# Every ledger here is small (the largest is a few hundred KB) and every one of
# them only ever grows. A cap means a file that runs away is reported as
# truncated instead of taking the whole snapshot down with it.
MAX_BYTES = 8_000_000


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _parse(ts) -> float | None:
    """An ISO stamp as epoch seconds, or None when it will not parse.

    None means "this cannot be placed in time", which is a real answer and never
    the same as `now`. Every caller has to handle it rather than get a default.
    """
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _iso(epoch: float | None) -> str:
    if epoch is None:
        return ""
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _draft_hash(payload) -> str:
    """approval.py::payload_hash, byte for byte.

    The same canonical form, so a hash computed here matches the one money-swarm
    bound the approval to. A different canonicalisation would silently never
    match, and every approved action would render as still waiting.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_jsonl(path: pathlib.Path) -> dict:
    """One ledger. Rows, torn lines counted, and how the read itself went.

      {"state": "missing"}                  nothing there
      {"state": "unreadable", "detail"}     it is there and would not open
      {"state": "ok", "rows": [...], "torn": n, "truncated": bool}

    A line that will not parse is COUNTED, never dropped. A queue quietly losing
    rows is a count of what waits on a person quietly going wrong.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"state": "missing", "rows": [], "torn": 0, "truncated": False}
    except OSError as exc:
        return {"state": "unreadable", "rows": [], "torn": 0, "truncated": False,
                "detail": f"{type(exc).__name__}: {exc}"[:200]}

    rows: list[dict] = []
    torn = 0
    read = 0
    truncated = False
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                read += len(line)
                if read > MAX_BYTES:
                    truncated = True
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    torn += 1
                    continue
                if not isinstance(row, dict):
                    torn += 1
                    continue
                rows.append(row)
    except OSError as exc:
        return {"state": "unreadable", "rows": [], "torn": 0, "truncated": False,
                "detail": f"{type(exc).__name__}: {exc}"[:200]}

    return {"state": "ok", "rows": rows, "torn": torn, "truncated": truncated,
            "size": size}


def read_json(path: pathlib.Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"state": "missing"}
    except OSError as exc:
        return {"state": "unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    try:
        value = json.loads(text)
    except ValueError as exc:
        return {"state": "unreadable", "detail": f"will not parse: {exc}"[:200]}
    if not isinstance(value, dict):
        return {"state": "unreadable", "detail": f"expected an object, got {type(value).__name__}"}
    return {"state": "ok", "value": value}


def envelope_is_live(path: pathlib.Path, now: float) -> bool:
    """policy.py::_live_envelope. Signed by Aria, and not yet expired.

    Absent, unsigned, or expired all mean the same thing to the ring engine:
    every Ring 1 action escalates. Defaulting to "live" here would hide a whole
    class of raised hand, so anything we cannot confirm counts as not live.
    """
    got = read_json(path)
    if got["state"] != "ok":
        return False
    env = got["value"]
    if env.get("signed_by") != "aria":
        return False
    expires = _parse(env.get("expires_at"))
    return expires is not None and expires > now


def live_approvals(rows: list[dict], now: float) -> dict[str, set]:
    """action_id -> the draft hashes that currently carry a live approval.

    approval.py::_matching_from_rows, read-side. A `consumed` or `revoked` row
    takes its approval out of play by `approval_id`; what survives is approved,
    unexpired, and still unused.
    """
    unavailable = {
        row.get("approval_id") for row in rows
        if row.get("status") in {"consumed", "revoked"} and row.get("approval_id")
    }
    live: dict[str, set] = {}
    for row in rows:
        if row.get("status") != "approved":
            continue
        if row.get("id") in unavailable:
            continue
        expires = _parse(row.get("expires_at"))
        if expires is None or expires <= now:
            continue
        action_id = row.get("action_id")
        draft_hash = row.get("draft_hash")
        if action_id and draft_hash:
            live.setdefault(str(action_id), set()).add(str(draft_hash))
    return live


def closed_action_ids(outcomes: list[dict]) -> set:
    """digest.py's completed_ids: done or replaced, either way not waiting."""
    return {
        str(row["action_id"]) for row in outcomes
        if (row.get("kind"), row.get("status")) in CLOSING and row.get("action_id")
    }


def _draft_of(row: dict) -> str:
    for field in DRAFT_FIELDS:
        v = row.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()[:DRAFT_CHARS]
    return ""


def waiting_rows(actions: list[dict], closed: set, approved: dict[str, set],
                 envelope_live: bool) -> list[dict]:
    """Everything money-swarm would escalate to Aria and nobody has decided.

    Both of its own rules, and its own definition of decided. A row can wait for
    more than one reason and every reason travels: "it is flagged" and "it is
    identity-bearing with no envelope" are different arguments to a person.
    """
    out = []
    for row in actions:
        action_id = str(row.get("id") or "")
        if action_id and action_id in closed:
            continue

        why = []
        if row.get("requires_aria"):
            why.append("the action is flagged requires_aria")
        if row.get("state") == "approval_required":
            why.append("its state is approval_required")
        kind = str(row.get("kind") or "")
        if kind in RING1_KINDS and not envelope_live:
            why.append("ring 1 kind (identity-bearing) with no live signed envelope, "
                       "so the policy engine escalates it")
        if not why:
            continue

        # Decided only on money-swarm's own terms: this exact action, this exact
        # draft. A live approval against a different hash is not an approval of
        # what is on disk now, and money-swarm would escalate it again.
        hashes = approved.get(action_id)
        if hashes and _draft_hash(row) in hashes:
            continue
        if hashes:
            why.append("an approval exists for this action but not for this exact draft, "
                       "so it is void")

        out.append({
            "id": action_id,
            "kind": kind,
            "what": str(row.get("summary") or "")[:400],
            "why": "; ".join(why),
            "since": str(row.get("created_at") or ""),
            "draft": _draft_of(row),
            "agent": str(row.get("agent") or ""),
            "subject_id": str(row.get("subject_id") or ""),
        })
    out.sort(key=lambda r: (r["since"], r["id"]))
    return out


def _lane(row: dict) -> dict:
    """One scored lane, with its money wrapped in what it actually is.

    `expected_value` is probability times price. That is a projection every time,
    whatever the row's confidence says, so the flag is not conditional: it is the
    arithmetic. Measured money in money-swarm is a `verified_received` outcome and
    is a different number that does not belong in this field.
    """
    try:
        value = float(row.get("expected_value") or 0)
    except (TypeError, ValueError):
        value = 0.0
    try:
        score = float(row.get("score"))
    except (TypeError, ValueError):
        score = None

    probability = row.get("probability")
    price = row.get("transaction_price_usd")
    if isinstance(probability, (int, float)) and isinstance(price, (int, float)):
        basis = f"probability {probability} of {_card.money(price)}"
    else:
        basis = "a projection, not a receipt"

    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("title") or row.get("canonical_name") or row.get("id") or ""),
        "score": score,
        # money-swarm's confidence is a word ("high"), not a number, and it is
        # about the EVIDENCE, not about the money. Passed through untouched
        # rather than turned into a percentage nobody measured.
        "confidence": row.get("confidence"),
        "expected_value": {"value": round(value, 2), "estimate": True, "basis": basis},
        "stage": str(row.get("status") or ""),
        "bucket": str(row.get("bucket") or ""),
    }


def top_lanes(portfolio: list[dict], current: list[dict]) -> list[dict]:
    """The ranked runtime input, biggest score first.

    `portfolio.jsonl` is canonical (its own README: "Keno's canonical ranked
    runtime input"). `current.jsonl` is a generated projection and is only used
    when the canonical file is not there, filtered to `record_type: lane` so an
    asset never renders as a revenue lane.
    """
    rows = portfolio or [r for r in current if r.get("record_type") == "lane"]
    lanes = [_lane(r) for r in rows]
    lanes.sort(key=lambda l: (l["score"] is None, -(l["score"] or 0), l["id"]))
    return lanes[:MAX_LANES]


def read() -> dict:
    now = time.time()

    root = _root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "no OFFICE_RUNTIME_ROOT, so there is no money-swarm to look at",
                **_blank()}
    swarm = root / SWARM
    if not swarm.is_dir():
        return {"state": "missing",
                "detail": f"no money-swarm installed at {swarm}",
                "path": str(swarm), **_blank()}

    actions = read_jsonl(swarm / ACTIONS)
    if actions["state"] == "unreadable":
        return {"state": "unreadable", "path": str(swarm),
                "detail": f"the action queue would not read: {actions.get('detail', '')}",
                **_blank()}
    if actions["state"] == "ok" and actions["torn"] and not actions["rows"]:
        # Every line torn is not an empty queue. Saying "nothing waits on you"
        # off a file nobody can parse is the false-green this whole office is
        # built to refuse.
        return {"state": "unreadable", "path": str(swarm),
                "detail": (f"the action queue is {actions['torn']} lines and not one of them "
                           "parses, so what waits on you cannot be said"),
                "torn": actions["torn"], **_blank(torn=actions["torn"])}

    approvals = read_jsonl(swarm / APPROVALS)
    outcomes = read_jsonl(swarm / OUTCOMES)
    history = read_jsonl(swarm / RUN_HISTORY)
    portfolio = read_jsonl(swarm / PORTFOLIO)
    current = read_jsonl(swarm / CURRENT)
    promotions = read_jsonl(swarm / PROMOTIONS)
    health = read_json(swarm / HEALTH)

    torn = sum(r["torn"] for r in (actions, approvals, outcomes, history,
                                   portfolio, current, promotions))

    envelope_live = envelope_is_live(swarm / ENVELOPE, now)
    closed = closed_action_ids(outcomes["rows"])
    approved = live_approvals(approvals["rows"], now)
    waiting = waiting_rows(actions["rows"], closed, approved, envelope_live)

    counts = {"draft": 0, "research_required": 0, "approval_required": 0,
              "other": 0, "closed": len(closed), "total": len(actions["rows"])}
    for row in actions["rows"]:
        state = str(row.get("state") or "")
        counts[state if state in counts else "other"] += 1

    # The last cycle receipt. cycle.py writes one only AFTER validate() returned,
    # so this stamp is the last time the whole dataset was known good.
    last_run_epoch = None
    for row in history["rows"]:
        at = _parse(row.get("at"))
        if at is not None and (last_run_epoch is None or at > last_run_epoch):
            last_run_epoch = at

    validate = _validate(health, history["rows"])

    executed = sum(1 for row in outcomes["rows"]
                   if row.get("kind") == "external_action_executed")
    promoted = sum(1 for row in promotions["rows"] if row.get("verdict") == "promoted")

    lanes = top_lanes(portfolio["rows"], current["rows"])
    lanes_scored = len(portfolio["rows"] or
                       [r for r in current["rows"] if r.get("record_type") == "lane"])

    # Why it would be stale, in the order a person would want to hear it. Red
    # first: an age is a suspicion, a red health file is a statement.
    stale = []
    if validate["at"] and not validate["ok"]:
        stale.append(f"the last run failed ({validate['detail']})")
    if last_run_epoch is None:
        stale.append("nothing has ever recorded a run")
    elif now - last_run_epoch > MAX_RUN_AGE_S:
        stale.append(f"the last run was {_card.human(now - last_run_epoch)} ago")
    v_at = _parse(validate["at"])
    if v_at is None:
        stale.append("no health receipt says when it was last validated")
    elif now - v_at > MAX_VALIDATE_AGE_S:
        stale.append(f"the last validate was {_card.human(now - v_at)} ago")

    if stale:
        state, detail = "stale", "; ".join(stale)
    else:
        state = "ok"
        n = len(waiting)
        detail = (f"{n} {_card.plural(n, 'decision')} waiting on you"
                  if n else f"nothing waits on you; {lanes_scored} "
                            f"{_card.plural(lanes_scored, 'lane')} scored")

    return {
        "state": state,
        "detail": detail,
        "path": str(swarm),

        # The number that comes first, and the rows behind it.
        "waiting": waiting,
        "waiting_count": len(waiting),

        "last_run": _iso(last_run_epoch),
        "last_run_age_s": None if last_run_epoch is None else round(now - last_run_epoch),
        "last_validate": validate,

        "lanes": lanes,
        "lanes_scored": lanes_scored,
        "counts": counts,

        # Things that actually happened in the world, kept apart from things
        # that are projected to.
        "actions_executed": executed,
        "promotions": {"promoted": promoted, "rows": len(promotions["rows"])},

        # Lines that would not parse, across every ledger read. Never dropped.
        "torn": torn,
        "envelope_live": envelope_live,
        "ledgers": {
            "actions": actions["state"], "approvals": approvals["state"],
            "outcomes": outcomes["state"], "run_history": history["state"],
            "portfolio": portfolio["state"], "current": current["state"],
            "promotions": promotions["state"], "health": health["state"],
        },
    }


def _validate(health: dict, history: list[dict]) -> dict:
    """The last validate result, from the one file money-swarm calls always true.

    `state/runtime/current-health.json` is written on success AND on failure
    (scheduler.py::write_current_health), which is exactly why it is preferred:
    run-history only gains a row when validate PASSED, so a system whose validate
    is failing looks perfectly healthy from run-history alone.

    The fallback is the newest run-history row carrying `validation`, for a tree
    where cycle.py has run but the scheduler never has. Neither present means we
    do not know, and "" is how this office says it does not know.
    """
    if health["state"] == "ok":
        row = health["value"]
        red = bool(row.get("red"))
        status = str(row.get("status") or ("red" if red else "passed"))
        at = row.get("updated_at") or row.get("last_success_at")
        note = f"the last run {status}"
        if red and row.get("last_success_at"):
            note += f"; last good evidence {row['last_success_at']}"
        return {"at": _iso(_parse(at)), "ok": not red, "detail": note[:200]}

    newest, at = None, None
    for row in history:
        if not row.get("validation"):
            continue
        stamp = _parse(row.get("at"))
        if stamp is not None and (at is None or stamp > at):
            at, newest = stamp, row
    if newest is not None:
        ok = str(newest.get("validation")).upper() == "PASS"
        return {"at": _iso(at), "ok": ok,
                "detail": f"run-history says validation {newest.get('validation')}"[:200]}

    return {"at": "", "ok": False,
            "detail": (health.get("detail")
                       or "no health receipt and no run-history row says validate ever ran")[:200]}


def _blank(torn: int = 0) -> dict:
    """Every field the card reads, present in every return.

    A consumer must never have to work out whether an absent key means zero or
    means nobody looked. It means nobody looked, and `state` already said so.
    """
    return {
        "waiting": [], "waiting_count": 0,
        "last_run": "", "last_run_age_s": None,
        "last_validate": {"at": "", "ok": False, "detail": "not read"},
        "lanes": [], "lanes_scored": 0,
        "counts": {}, "actions_executed": 0,
        "promotions": {"promoted": 0, "rows": 0},
        "torn": torn, "envelope_live": False, "ledgers": {},
    }


# `unconfigured` wants nobody: a machine with no vault root is not a broken
# money-swarm, it is a machine that does not have one. `missing` is the same
# fact about the directory. The two that want a person are the two that mean
# something is installed and something is wrong with it.
TROUBLE = {
    "unconfigured": ("not configured", 0),
    "missing": ("no money swarm here", 0),
    "unreadable": ("the queue is unreadable", 1),
}


def card(data: dict) -> dict:
    """The waiting count first, then whether the system behind it is honest.

    The count leads because it is the only line on this card a person can act on.
    Everything under it exists to say whether the count can be believed: a run
    that stopped, a validate that went red, or a torn ledger all mean the zero
    on the top line is a zero nobody should trust yet.
    """
    state = data.get("state")
    if state not in ("ok", "stale"):
        return _card.trouble(TITLE, state, data.get("detail") or data.get("path"), TROUBLE)

    n = int(data.get("waiting_count") or 0)
    validate = data.get("last_validate") or {}
    red = not validate.get("ok")
    lanes = data.get("lanes") or []
    scored = int(data.get("lanes_scored") or 0)
    torn = int(data.get("torn") or 0)
    age = data.get("last_run_age_s")

    if state == "stale":
        headline = f"stale: {data.get('detail') or 'the data stopped moving'}"
    elif n:
        headline = f"{n} {_card.plural(n, 'decision')} {'waits' if n == 1 else 'wait'} on you"
    else:
        when = f", last run {_card.human(age)} ago" if age is not None else ""
        headline = (f"nothing waits on you; {scored} "
                    f"{_card.plural(scored, 'lane')} scored{when}")

    facts = [
        _card.fact("waiting on you", _card.count(n), "warn" if n else "ok"),
        _card.fact("last run",
                   f"{_card.human(age)} ago" if age is not None else "never",
                   "bad" if age is None else ("warn" if state == "stale" else "dim")),
        _card.fact("validate",
                   f"red — {validate.get('detail') or 'no receipt'}" if red
                   else f"ok, {_card.ago(validate.get('at')) or 'no stamp'}",
                   "bad" if red else "ok"),
    ]
    # Third, above everything else, because the card is capped at eight rows and
    # a ledger losing lines is the one row that must never be the one clipped
    # off the bottom. It is the reason to distrust the number on the top line.
    if torn:
        facts.append(_card.fact("lines that would not parse", _card.count(torn), "bad"))
    facts.append(_card.fact("lanes scored", _card.count(scored), "dim"))

    if lanes:
        top = lanes[0]
        score = top.get("score")
        facts.append(_card.fact(
            "top lane",
            f"{top.get('name') or top.get('id')}"
            + (f" — score {score:g}" if isinstance(score, (int, float)) else ""),
        ))
        # The band travels in the LABEL, the way cost.py does it, so what a
        # person reads is never one number that folded a projection into a
        # receipt. This one is always a projection: it is probability x price.
        ev = top.get("expected_value") or {}
        facts.append(_card.fact("its value, estimated (not measured)",
                                _card.money(ev.get("value")), "warn"))

    promotions = data.get("promotions") or {}
    facts += [
        _card.fact("actions executed, lifetime",
                   _card.count(data.get("actions_executed")), "dim"),
        _card.fact("promotions",
                   f"{_card.count(promotions.get('promoted'))} promoted "
                   f"of {_card.count(promotions.get('rows'))} examined", "dim"),
    ]

    # A red validate with nothing waiting still wants a person: the zero on the
    # top line came off data the system itself will not vouch for.
    needs = n if n else (1 if (red or state == "stale") else 0)
    return _card.build(TITLE, headline, needs, _card.zulu(data.get("last_run")), facts)
