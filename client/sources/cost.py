"""the spend ledger.

The ledger is `<root>/_meta/logs/costs.jsonl`, one JSON object per line, written
by several different tools over about a year. It is NOT one schema. Measured on
2026-08-25 across 1225 rows: nine distinct key sets, two different names for the
money field (`total_cost` and `cost_usd`), two for the timestamp (`timestamp`
and `ts`), rows whose timestamp is literally `null`, and rows carrying no time
field at all.

Three rules this file exists to keep.

**Never re-derive money from tokens.** The row already carries the number the
writer computed. A second calculation of money is a second number to be wrong,
and the two will disagree the first time a price changes.

**Never drop an awkward row.** A row with no usable date still has real money in
it. Undated rows cannot sit on a time axis, so they are counted separately, their
money is still in the lifetime total, and the count travels to the panel so the
room can say so out loud. Silently dropping them is the exact defect this
fixture is about.

**Never present an estimate as a measurement.** `estimate: true` rows are
estimated. `estimate: false` rows are measured. Rows with no `estimate` key at
all are neither: they were written before the flag existed and nobody now knows
which they were, so they get their own third band. Folding them into "measured"
would be presenting an unknown as a measurement.

Like `runtime.py`, this returns a `state` rather than a bare number, because
"no root configured", "no ledger on disk", "the ledger is unreadable" and "the
ledger says zero" must never render the same.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

from sources import _card

KEY = "cost"
# The human name of the fixture, fixed. A card whose title moves is a
# card the eye has to find again every time it is drawn.
TITLE = "Cost"

LEDGER = "_meta/logs/costs.jsonl"

# Roughly a fortnight. Long enough to see a habit, short enough that a bar is
# still wide enough to read from across the room.
WINDOW_DAYS = 14

# In preference order. The first one present on a row wins; we never add them up
# and never fall back to computing one from tokens.
MONEY_FIELDS = ("total_cost", "cost_usd")
TIME_FIELDS = ("timestamp", "ts")

BANDS = ("measured", "estimated", "unflagged")


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _money(row: dict) -> tuple[float, bool]:
    """The money the row itself carries, and whether it carried any at all."""
    for f in MONEY_FIELDS:
        if f in row:
            try:
                return float(row[f]), True
            except (TypeError, ValueError):
                # A money field that is not a number is not zero, it is broken.
                return 0.0, False
    return 0.0, False


def _local_date(row: dict) -> datetime.date | None:
    """The row's date in local time, or None if it cannot be placed on an axis.

    Local rather than UTC on purpose: the chart says "today", and a person's
    today is their own midnight, not Greenwich's.
    """
    for f in TIME_FIELDS:
        v = row.get(f)
        if not isinstance(v, str) or not v.strip():
            continue
        try:
            dt = datetime.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        # A naive timestamp is assumed local, which is what astimezone() already
        # does with one. Nothing here guesses UTC on its behalf.
        return dt.astimezone().date()
    return None


def _band(row: dict) -> str:
    e = row.get("estimate")
    if e is True:
        return "estimated"
    if e is False:
        return "measured"
    return "unflagged"


def _bucket() -> dict:
    return {"measured": 0.0, "estimated": 0.0, "unflagged": 0.0, "rows": 0}


def _rank(counts: dict) -> list[dict]:
    """A breakdown, biggest first. A missing key is its own honest bucket."""
    out = [
        {"key": k, "value": round(v["value"], 4), "rows": v["rows"], "missing": k is None}
        for k, v in counts.items()
    ]
    out.sort(key=lambda d: -d["value"])
    return out


def read() -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured"}
    if not root.exists():
        return {"state": "missing-root", "detail": str(root)}

    path = root / LEDGER
    if not path.exists():
        # Configured, but nothing has ever written a ledger here. Not the same as
        # a ledger that reads zero, and it must not render the same.
        return {"state": "missing-ledger", "path": str(path)}

    daily: dict[str, dict] = {}
    totals = _bucket()
    undated = {"rows": 0, "value": 0.0}
    families: dict[str | None, dict] = {}
    sources: dict[str | None, dict] = {}
    unparseable = 0
    unpriced = 0
    latest: str | None = None

    try:
        # Read once, line by line. 1225 rows is nothing, but the ledger only ever
        # grows and there is no reason for this to ever hold the whole file.
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    # A torn or hand-edited line. Counted, never silently skipped:
                    # a ledger quietly losing rows is a total quietly going wrong.
                    unparseable += 1
                    continue
                if not isinstance(row, dict):
                    unparseable += 1
                    continue

                value, priced = _money(row)
                if not priced:
                    unpriced += 1
                band = _band(row)

                totals[band] += value
                totals["rows"] += 1

                day = _local_date(row)
                if day is None:
                    undated["rows"] += 1
                    undated["value"] += value
                else:
                    iso = day.isoformat()
                    b = daily.setdefault(iso, _bucket())
                    b[band] += value
                    b["rows"] += 1
                    if latest is None or iso > latest:
                        latest = iso

                for field, into in (("family", families), ("source", sources)):
                    k = row.get(field)
                    k = k if isinstance(k, str) and k.strip() else None
                    slot = into.setdefault(k, {"value": 0.0, "rows": 0})
                    slot["value"] += value
                    slot["rows"] += 1
    except OSError as exc:
        return {"state": "error", "path": str(path), "detail": str(exc)[:200]}

    if not totals["rows"] and not unparseable:
        return {"state": "empty", "path": str(path)}

    today = datetime.date.today()
    days = []
    for i in range(WINDOW_DAYS - 1, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        b = daily.get(d, _bucket())
        days.append({
            "date": d,
            "measured": round(b["measured"], 4),
            "estimated": round(b["estimated"], 4),
            "unflagged": round(b["unflagged"], 4),
            "rows": b["rows"],
        })

    lifetime = sum(totals[b] for b in BANDS)
    return {
        "state": "ok",
        "path": str(path),
        "currency": "USD",
        "rows": totals["rows"],
        "unparseable": unparseable,
        # Rows carrying no money field at all. Distinct from a row worth $0.
        "unpriced": unpriced,
        "window_days": WINDOW_DAYS,
        "today": today.isoformat(),
        "latest_day": latest,
        "days": days,
        "window_total": round(sum(d["measured"] + d["estimated"] + d["unflagged"] for d in days), 4),
        "lifetime": {
            "total": round(lifetime, 4),
            "measured": round(totals["measured"], 4),
            "estimated": round(totals["estimated"], 4),
            "unflagged": round(totals["unflagged"], 4),
        },
        # Reported rather than left to the renderer, so the number on the panel
        # and the number in the ledger are the same arithmetic.
        "estimated_fraction": round(totals["estimated"] / lifetime, 6) if lifetime else 0.0,
        "undated": {"rows": undated["rows"], "value": round(undated["value"], 4)},
        "by_family": _rank(families),
        "by_source": _rank(sources),
    }


# `missing-ledger` and `empty` want nobody: nothing has been written yet, and
# that is a fact about the vault rather than a fault in it.
TROUBLE = {
    "unconfigured": ("not configured", 0),
    "missing-root": ("the vault root is not there", 1),
    "missing-ledger": ("no ledger has ever been written here", 0),
    "empty": ("the ledger is there and holds nothing", 0),
    "error": ("ledger unreadable", 1),
}


def card(data: dict) -> dict:
    """The spend in one line, and never one number for three different bands.

    The headline is the MEASURED money in the window. Estimated money is its own
    row and says so in the label, and rows written before the flag existed are a
    third row again: folding either into the headline would print a guess with a
    dollar sign in front of it.
    """
    if data.get("state") != "ok":
        return _card.trouble(TITLE, data.get("state"),
                             data.get("detail") or data.get("path"), TROUBLE)

    days = data.get("days") or []
    window = int(data.get("window_days") or len(days))
    measured = sum(float(d.get("measured") or 0) for d in days)
    estimated = sum(float(d.get("estimated") or 0) for d in days)
    unflagged = sum(float(d.get("unflagged") or 0) for d in days)
    today = days[-1] if days else {}
    lifetime = data.get("lifetime") or {}

    unpriced = int(data.get("unpriced") or 0)
    unparseable = int(data.get("unparseable") or 0)

    facts = [
        _card.fact("today, measured", _card.money(today.get("measured") or 0)),
        _card.fact(f"{window} days, measured", _card.money(measured)),
        _card.fact("estimated (not measured)", _card.money(estimated),
                   "warn" if estimated else "dim"),
    ]
    if unflagged:
        # Written before the estimate flag existed. Nobody now knows which they
        # were, so they are neither, and they are shown rather than absorbed.
        facts.append(_card.fact("unflagged (band unknown)", _card.money(unflagged), "warn"))
    facts += [
        _card.fact("lifetime, measured", _card.money(lifetime.get("measured") or 0), "dim"),
        _card.fact("rows with no price", _card.count(unpriced), "warn" if unpriced else "dim"),
        _card.fact("rows that would not parse", _card.count(unparseable),
                   "bad" if unparseable else "dim"),
    ]

    latest = data.get("latest_day")
    return _card.build(
        TITLE,
        f"{_card.money(measured)} measured in {window} days",
        unpriced + unparseable,
        f"{latest}T00:00:00Z" if latest else "",
        facts,
    )
