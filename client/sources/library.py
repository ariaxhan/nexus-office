"""what memory holds.

NOT BUILT YET. A source exports KEY and read(); read() returns a dict whose
`state` says which of "not configured", "broken" and "genuinely empty" this is.
"""

from __future__ import annotations

KEY = "library"


def read() -> dict:
    return {"state": "unbuilt"}
