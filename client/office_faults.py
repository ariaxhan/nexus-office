"""Read Tower fault machine state for Office; never turn faults into asks."""
from contextlib import closing
import sqlite3

import run_board
from nexus import faults
from nexus.ledger import Ledger


def snapshot(limit=20):
    try:
        with closing(Ledger(str(run_board.LEDGER), read_only=True)) as ledger:
            rows = faults.escalated(ledger)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {"count": 0, "items": [], "error": f"{type(exc).__name__}: {exc}"[:160]}
    return {"count": len(rows), "items": rows[:limit], "error": None}
