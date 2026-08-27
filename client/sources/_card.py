"""One card per fixture: the sentence a person reads from across the room.

Every source in here already returns the whole truth, and that is the point of
them. None of it fits on a card at the far end of an office, and none of it fits
on a phone. So each source also answers a much smaller question: what is the one
line, how many things need a person, and what are the few rows underneath.

The card is built HERE, from the section's own data, by a pure function. Not in
the Mac app, because the phone page draws the same card in M2 and two renderers
each deciding "is this fine" is two places for it to go wrong, in two languages,
that drift the first time a state is added.

Three rules this file exists to keep.

**A number in a card is already a string a person can read.** `$4,628.55`,
`12.4k`, `6h ago`. A renderer that has to decide how to print money is a renderer
that will print it differently from the other renderer.

**An estimate is never shown as a measurement.** The band travels in the LABEL,
so what a person reads is "estimated (not measured)" and never a single total
that quietly folded two different kinds of number together.

**A source that could not tell says so in the headline.** `needs` is how many
things want a person, so a deliberate absence (nothing configured, switched off,
genuinely empty) carries 0 and a break carries at least 1. Conflating those is
how a paused thing becomes an alarm nobody reads any more.
"""

from __future__ import annotations

import datetime
import time

# The card contract, frozen: the Mac app and the phone page both code against
# exactly these five keys and these five tones.
KEYS = ("title", "headline", "needs", "as_of", "facts")
TONES = ("ok", "warn", "bad", "dim", "")

# Under 80 on purpose: a headline that wraps on a card is a headline nobody
# finishes reading.
HEADLINE_CHARS = 79
FACT_CHARS = 120
MAX_FACTS = 8


def clip(text, n: int) -> str:
    s = str(text if text is not None else "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def plural(n: int, word: str) -> str:
    return word if abs(int(n)) == 1 else word + "s"


def money(v) -> str:
    """Money the way a ledger prints it, never a bare float."""
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "unknown"


def count(n) -> str:
    """A count a person can take in at a glance: 32, 4,599, 12.4k, 1.2M."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "unknown"
    a = abs(n)
    if a >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def human(seconds) -> str:
    """A duration the way a person says it. Never a bare number of seconds."""
    try:
        s = int(abs(float(seconds)))
    except (TypeError, ValueError):
        return "unknown"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h}h{m}m" if m else f"{h}h"
    d, h = divmod(s // 3600, 24)
    return f"{d}d{h}h" if h else f"{d}d"


def zulu(ts) -> str:
    """A timestamp as ISO Z, or "" when it will not parse.

    "" means "this source does not know how fresh its data is", which is a real
    answer. Inventing `now` here would make every card look freshly measured.
    """
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        # A naive stamp is local, which is what astimezone() already assumes.
        dt = dt.astimezone()
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(ts, now: float | None = None) -> str:
    """"6h ago", or "" when the stamp will not parse."""
    z = zulu(ts)
    if not z:
        return ""
    then = datetime.datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()
    delta = (time.time() if now is None else now) - then
    return "just now" if delta < 0 else f"{human(delta)} ago"


def fact(label: str, value, tone: str = "") -> dict:
    """One row. The value is already a string, and the tone is one of five."""
    return {
        "label": clip(label, 40),
        "value": clip(value, FACT_CHARS),
        "tone": tone if tone in TONES else "",
    }


def build(title: str, headline: str, needs: int = 0, as_of: str = "",
          facts=()) -> dict:
    """The card, clamped to the contract so a renderer never has to check.

    Clamped rather than asserted: a card is a summary, and a summary that raises
    costs the whole section. `sections.read_all` catches that anyway, but a card
    that quietly drops a ninth row is better than a fixture that goes blank.
    """
    try:
        n = int(needs)
    except (TypeError, ValueError):
        n = 0
    rows = []
    for row in list(facts)[:MAX_FACTS]:
        rows.append(fact(row.get("label", ""), row.get("value", ""), row.get("tone", "")))
    return {
        "title": str(title),
        "headline": clip(headline, HEADLINE_CHARS),
        "needs": max(0, n),
        "as_of": str(as_of or ""),
        "facts": rows,
    }


def trouble(title: str, state, detail, phrases: dict) -> dict:
    """The card for a source that is not `ok`.

    `phrases` maps a state to (what to say, how many people it needs). A state
    nobody wrote a phrase for still gets a card, carrying the raw state, because
    an unnamed state is exactly the one worth seeing.
    """
    phrase, needs = phrases.get(state, (str(state), 1))
    detail = clip(detail, 160)
    headline = f"{phrase}: {detail}" if detail else phrase
    rows = [fact("state", str(state), "bad" if needs else "dim")]
    if detail:
        rows.append(fact("detail", detail, "dim"))
    return build(title, headline, needs, "", rows)
