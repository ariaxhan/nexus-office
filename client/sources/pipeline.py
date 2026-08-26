"""whether the issue pipeline is working right now.

NOT BUILT YET. A source exports KEY and read(); read() returns a dict whose
`state` says which of "not configured", "broken" and "genuinely idle" this is.
"""

from __future__ import annotations

KEY = "pipeline"


def read() -> dict:
    return {"state": "unbuilt"}
