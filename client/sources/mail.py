"""what intake has caught and not yet filed.

NOT BUILT YET. A source exports KEY and read(); read() returns a dict whose
`state` says which of "not configured", "broken" and "genuinely empty" this is.
"""

from __future__ import annotations

KEY = "mail"


def read() -> dict:
    return {"state": "unbuilt"}
