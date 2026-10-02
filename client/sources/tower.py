"""whether Tower is landing work, not whether it is ticking.

A scheduler that ticks is green by every count that is easy to take: flights
produced, exits zero. The only count that matters is changes landed, so this
fixture puts that sentence on the wall next to the things stopping it:
quarantined plans, issues retried with nothing to show, flights past their time.

All of it is `tower_board`'s reading of the ledger; this file only hangs it on
the wall.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tower_board  # noqa: E402  (needs the path above)

KEY = "tower"
TITLE = "Tower"


def read() -> dict:
    board = tower_board.read()
    return {"state": board.get("state", "missing"), "detail": board.get("detail", ""),
            "summary": board.get("summary"), "tower": board.get("tower")}


def card(data: dict) -> dict:
    return tower_board.card(data)
