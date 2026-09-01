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

Every section also carries a `card`: the one sentence, the count of things that
want a person, and the few rows underneath. Each source builds its own from its
own data (`sources/_card.py` says why it is built here and not in a renderer),
and a card that raises is caught exactly like a source that raises, because a
summary going wrong must never cost the data it was summarising.
"""

from __future__ import annotations

from sources import care, care_grader, clock, cost, flows, library, mail, money_swarm, pipeline, webhook

SOURCES = [care, care_grader, clock, cost, flows, library, mail, money_swarm, pipeline, webhook]


def _fallback(mod, headline: str) -> dict:
    """A card for a fixture that could not produce one. Same five keys, always,
    so nothing downstream has to ask whether a card is there."""
    return {"title": str(mod.KEY).title(), "headline": headline[:79],
            "needs": 0, "as_of": "", "facts": []}


def read_all() -> dict:
    out = {}
    for mod in SOURCES:
        try:
            section = mod.read()
        except Exception as exc:  # noqa: BLE001 - one bad source, not a bad snapshot
            detail = f"{type(exc).__name__}: {exc}"[:300]
            section = {"state": "error", "detail": detail}
            # An error section is still a section, and it still gets a card, or
            # the room would draw a fixture with nothing written on it at all.
            section["card"] = _fallback(mod, detail)
            out[mod.KEY] = section
            continue
        if not isinstance(section, dict):
            section = {"state": "error",
                       "detail": f"{mod.KEY}.read() returned {type(section).__name__}, not a dict"}
        try:
            card = mod.card(section)
            if not isinstance(card, dict):
                raise TypeError(f"card() returned {type(card).__name__}, not a dict")
            section["card"] = card
        except Exception as exc:  # noqa: BLE001 - a bad card, not a bad section
            section["card"] = _fallback(mod, f"card failed: {type(exc).__name__}: {exc}")
        out[mod.KEY] = section
    return out
