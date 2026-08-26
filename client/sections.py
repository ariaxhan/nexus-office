"""The local data each fixture in the room reads.

The office already ships the pipeline's own state. These are the OTHER local
facts a room should show: what is scheduled, what it cost, what is waiting in
the mailroom, what memory holds. Each one lives in its own file under
`sources/`, and each is listed here once.

Every source returns a dict carrying its own `state`, never a bare number, for
the same reason `runtime.py` does: "the source is not configured", "the source
is broken" and "the source says zero" must never render the same. A silent zero
is the false-green this whole project exists to kill.

A source that raises is caught here and reported as an error section. One
unreadable ledger must not cost you the whole snapshot.
"""

from __future__ import annotations

from sources import clock, cost, library, mail

SOURCES = [clock, cost, library, mail]


def read_all() -> dict:
    out = {}
    for mod in SOURCES:
        try:
            out[mod.KEY] = mod.read()
        except Exception as exc:  # noqa: BLE001 - one bad source, not a bad snapshot
            out[mod.KEY] = {"state": "error", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    return out
