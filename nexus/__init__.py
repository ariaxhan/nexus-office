"""nexus: one ledger, one tower, flights that do the work.

Stdlib only, on purpose. The thing that keeps everything else alive cannot have
an install step that can fail.
"""

__all__ = ["ledger", "tower", "flights", "cli"]
