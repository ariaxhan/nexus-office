"""The intake and sync flows, one row each, read from the receipts themselves.

`clock.py` is the aggregate: every one of the ~52 scheduled jobs, judged by
`jobctl status`, folded into counts. This is the other lens: a NAMED handful of
flows, the ones that move data into the vault, each judged on its own row.

The reason it reads the receipts file directly instead of asking jobctl is a
thing jobctl cannot see: a run that exited 0 and did nothing. On 2026-08-26
inbox-fill logged `ok: true` after its MCP was not authenticated and it wrote no
tasks. jobctl called that healthy, because rc was 0. So this source also reads
`stderr_tail`, and an rc-0 run whose tail says it stopped is `broken` here.

Per-flow states, kept apart, in this precedence:

  off       the registry says it is not enabled. A DECISION, never a fault
  never     no receipt in the tail at all
  broken    last run exited non-zero, OR exited 0 and admitted it did nothing
  stale     the last real success is older than the flow's own budget
  ok        a real success inside the budget

Every return carries a `state` and never a bare number: no root, no receipts
file, and an unreadable file must never render the same as "all holding".
"""

from __future__ import annotations

import datetime
import os
import json
import pathlib
import re
import time

from sources import _card
from sources import clock

KEY = "flows"
TITLE = "Flows"

REGISTRY = clock.REGISTRY

# The flows this card watches, by job id, with the name a person uses for it.
#
# This is a named subset BY DESIGN, not a hand-maintained mirror of the
# registry: the clock already covers every job, and this card exists to give
# the eight that move data into the vault a row each. A flow listed here that
# the registry does not declare reports `unregistered`, so the list can never
# drift ahead of reality in silence.
FLOWS = [
    ("com.aria.granola-sync", "Granola"),
    ("com.nexus.inbox-fill", "Inbox"),
    ("com.nexus.morning-briefing", "Morning briefing"),
    ("com.nexus.issue-dispatch", "Issue pipeline"),
    ("com.nexus.money-swarm", "Money swarm"),
    ("com.aria.mobile-capture", "Mobile capture"),
    ("com.aria.distillations", "Distillations"),
    ("com.aria.outbound-daily", "Outbound"),
]

# rc 0 lies here. inbox-fill on 2026-08-26 exited 0 with "Structured MCP is not
# available in this session. No task tools were found. Stopping without writing
# anything." in its stderr, and jobctl called it healthy. A run whose own tail
# says one of these is a run that produced nothing, whatever its exit code.
DID_NOTHING = re.compile(
    r"not authenticated|not available|no task tools|stopping without writing",
    re.IGNORECASE,
)

# Lines the job runner adds around every run. Never the detail a person wants.
BOILERPLATE = re.compile(r"^\s*=== (jobrun\b|cmd:)")

# The receipts file is ~20 MB and grows forever. The last 2 MB is a couple of
# days of receipts, which is more than any flow's budget, so only that is read.
TAIL_BYTES = 6 * 1024 * 1024  # about a week of receipts at tonight's write rate

# Where jobctl keeps its receipts: RUNTIME_DIR / state / receipts.jsonl, with
# RUNTIME_DIR overridable the same way jobctl allows (JOBCTL_RUNTIME_DIR).
RUNTIME_DIR_DEFAULT = pathlib.Path.home() / "Library" / "Application Support" / "nexus-jobs"


def _receipts_path() -> pathlib.Path:
    v = os.environ.get("OFFICE_JOB_RECEIPTS", "").strip()
    if v:
        return pathlib.Path(v).expanduser()
    runtime = os.environ.get("JOBCTL_RUNTIME_DIR", "").strip()
    base = pathlib.Path(runtime).expanduser() if runtime else RUNTIME_DIR_DEFAULT
    return base / "state" / "receipts.jsonl"


def read_tail(path: pathlib.Path, wanted: set[str],
              tail_bytes: int = TAIL_BYTES) -> dict[str, list[dict]]:
    """The receipts for `wanted` jobs from the last `tail_bytes` of the file.

    Seeks from the end and never reads the whole file. The first line of the
    tail is almost always a torn line, and it is dropped without comment: a
    torn receipt is not a receipt. Any other line that will not parse is also
    skipped, because one junk line must not cost every flow its row.
    """
    out: dict[str, list[dict]] = {j: [] for j in wanted}
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - tail_bytes)
        f.seek(start)
        blob = f.read()
    lines = blob.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        # Cheap reject before json: most of the tail is other jobs.
        if b'"job"' not in raw:
            continue
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        job = str(row.get("job") or "")
        if job in out:
            out[job].append(row)
    for rows in out.values():
        rows.sort(key=lambda r: str(r.get("started") or ""))
    return out


def _ts(s) -> float | None:
    z = _card.zulu(s)
    if not z:
        return None
    return datetime.datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def did_nothing(receipt: dict) -> bool:
    return bool(DID_NOTHING.search(str(receipt.get("stderr_tail") or "")))


def succeeded(receipt: dict) -> bool:
    """A real success: rc 0 and the run did not admit it produced nothing."""
    try:
        rc = int(receipt.get("rc"))
    except (TypeError, ValueError):
        return False
    return rc == 0 and not did_nothing(receipt)


def _detail(receipt: dict) -> str:
    """The last line of stderr that is not the runner's own framing."""
    for line in reversed(str(receipt.get("stderr_tail") or "").splitlines()):
        line = line.strip()
        if line and not BOILERPLATE.match(line):
            return line[:200]
    return ""


def _flow(job: str, name: str, reg: dict | None, receipts: list[dict],
          now: float) -> dict:
    last = receipts[-1] if receipts else None
    last_ok = next((r for r in reversed(receipts) if succeeded(r)), None)
    budget = (reg or {}).get("max_success_age_h")
    ok_ts = _ts(last_ok.get("started")) if last_ok else None
    age_s = int(now - ok_ts) if ok_ts is not None else None

    if reg is None:
        state = "unregistered"
    elif str(reg.get("state") or "") != "enabled":
        state = "off"
    elif last is None:
        state = "never"
    elif not succeeded(last):
        state = "broken"
    elif budget and (age_s is None or age_s > float(budget) * 3600):
        state = "stale"
    else:
        state = "ok"

    rc = None
    if last is not None:
        try:
            rc = int(last.get("rc"))
        except (TypeError, ValueError):
            rc = None

    return {
        "id": job,
        "name": name,
        "state": state,
        "schedule": clock._schedule((reg or {}).get("schedule") or {}),
        "last_run": _card.zulu(last.get("started")) if last else "",
        "last_ok": _card.zulu(last_ok.get("started")) if last_ok else "",
        "age_s": age_s,
        "budget_h": budget,
        "watch": f"{budget}h" if budget else "nothing",
        "rc": rc,
        "duration_s": last.get("duration_s") if last else None,
        "did_nothing": bool(last is not None and rc == 0 and did_nothing(last)),
        "detail": _detail(last) if last else "",
    }


def read(now: float | None = None) -> dict:
    root = clock._root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "no OFFICE_RUNTIME_ROOT, so no job registry to read"}
    if not root.exists():
        return {"state": "missing-root", "detail": str(root)}

    receipts_path = _receipts_path()
    if not receipts_path.exists():
        # Its own state, never eight blank rows. All the flows share this one
        # file, so its absence means every one of them has gone silent at once.
        return {"state": "missing-receipts", "detail": str(receipts_path)}

    wanted = {job for job, _ in FLOWS}
    try:
        by_job = read_tail(receipts_path, wanted)
    except OSError as exc:
        return {"state": "unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]}

    reg_by_id, _bad = clock.read_registry(root / REGISTRY)
    now = time.time() if now is None else now
    flows = [_flow(job, name, reg_by_id.get(job), by_job.get(job) or [], now)
             for job, name in FLOWS]

    counts = {k: 0 for k in ("ok", "stale", "broken", "never", "off", "unregistered")}
    for fl in flows:
        counts[fl["state"]] = counts.get(fl["state"], 0) + 1

    return {
        "state": "ok",
        "receipts": str(receipts_path),
        "counts": counts,
        "alarm": counts["broken"] + counts["stale"] + counts["never"],
        "flows": flows,
    }


TROUBLE = {
    "unconfigured": ("not configured", 0),
    "missing-root": ("the vault root is not there", 1),
    "missing-receipts": ("no receipts file, every flow is silent", 1),
    "unreadable": ("could not read the receipts", 1),
}

# Most-wrong first, `off` last: a decision is reported, and never leads, and
# never sits between a fault and a healthy row where it reads like one.
ORDER = {"broken": 0, "stale": 1, "never": 2, "unregistered": 3, "ok": 5, "off": 6}
TONE = {"broken": "bad", "stale": "bad", "never": "warn", "unregistered": "warn",
        "off": "dim", "ok": "ok"}


def _value(fl: dict, now: float) -> str:
    state = fl["state"]
    run_ago = _card.ago(fl.get("last_run"), now)
    if state == "off":
        return "off"
    if state == "never":
        return "never ran"
    if state == "unregistered":
        return "not in the registry"
    if state == "broken":
        if fl.get("did_nothing"):
            return f"did nothing (rc 0) · {run_ago}"
        return f"rc {fl.get('rc')} · {run_ago}"
    if state == "stale":
        ok_ago = _card.ago(fl.get("last_ok"), now) or "no success in the tail"
        return f"stale · {ok_ago}, budget {fl.get('budget_h')}h"
    return f"ok · {run_ago}" if run_ago else "ok"


def _why(fl: dict) -> str:
    state = fl["state"]
    if state == "broken":
        return "did nothing (rc 0)" if fl.get("did_nothing") else f"rc {fl.get('rc')}"
    if state == "stale":
        return f"stale, budget {fl.get('budget_h')}h"
    if state == "never":
        return "never ran"
    if state == "unregistered":
        return "not in the registry"
    return state


def card(data: dict, now: float | None = None) -> dict:
    """One line per flow, worst first, and a headline that names the worst."""
    if data.get("state") != "ok":
        return _card.trouble(TITLE, data.get("state"), data.get("detail"), TROUBLE)

    now = time.time() if now is None else now
    flows = sorted(data.get("flows") or [],
                   key=lambda f: (ORDER.get(f["state"], 4), f["name"]))
    total = len(flows)
    alarm = int(data.get("alarm") or 0)

    if not total:
        headline = "no flows named"
    elif alarm:
        worst = flows[0]
        headline = (f"{alarm} of {total} {_card.plural(total, 'flow')} "
                    f"{'needs' if alarm == 1 else 'need'} a look: "
                    f"{worst['name']} {_why(worst)}")
    else:
        headline = f"{total} {_card.plural(total, 'flow')}, all holding"

    facts = [_card.fact(fl["name"], _value(fl, now), TONE.get(fl["state"], "warn"))
             for fl in flows]
    as_of = max((fl.get("last_run") or "" for fl in flows), default="")
    return _card.build(TITLE, headline, alarm, as_of, facts)
