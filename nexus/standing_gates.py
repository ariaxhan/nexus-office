"""Standing rules with a mechanical test, enforced where the banned shape would pass.

Judgment rules (tone, doctrine) stay prose. These three do not, because each was
remembered, then forgotten, and cost Aria a correction (nexus-office#306, pd-11):

  attachment freshness   her client caches attachments by name, so a same-name
                         resend shows the stale copy. A name she was already
                         sent is refused at attach time; an edit means a rename.
  security topics        no proactive security alerts (2026-09-29). The topic
                         list is data, `standing_topics.json`; a matching item
                         is dropped before any surfacing code sees it.
  raw-draft sends        lives in `nexus/mail.py`: the only body-bearing send.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from email.utils import getaddresses
from functools import lru_cache
from pathlib import Path

TOPICS = Path(__file__).with_name("standing_topics.json")
LEDGER = Path(os.environ.get("NEXUS_SENT_ATTACHMENTS")
              or Path.home() / ".local/state/nexus-mail/sent-attachments.json")


class StaleAttachment(ValueError):
    """A filename this recipient has already been sent."""


# ── security topics ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def security_topics() -> tuple:
    rows = json.loads(TOPICS.read_text())["security"]
    return tuple({"topic": r["topic"], "pattern": r["pattern"], "rx": re.compile(r["pattern"], re.I)} for r in rows)


def security_class(text) -> str | None:
    """The security topic this text belongs to, or None."""
    line = " ".join(str(text or "").split())
    return next((t["topic"] for t in security_topics() if t["rx"].search(line)), None)


def partition(items, field="text"):
    """(kept, dropped): the evaluator's hard filter over a batch of candidates."""
    kept, dropped = [], []
    for item in items:
        topic = security_class(item.get(field))
        if topic:
            dropped.append(dict(item, dropped_for=topic))
        else:
            kept.append(item)
    return kept, dropped


# ── attachment freshness ─────────────────────────────────────────────────────

def _recipients(to) -> list[str]:
    return sorted({addr.lower() for _, addr in getaddresses([str(to or "")]) if addr})


def _read(ledger: Path) -> dict:
    try:
        return json.loads(ledger.read_text())
    except (OSError, ValueError):
        return {}


def check_fresh(to, names, ledger: Path = LEDGER) -> None:
    """Raise StaleAttachment when any name was already sent to any recipient."""
    sent = _read(Path(ledger))
    stale = sorted({n for r in _recipients(to) for n in names if n.lower() in sent.get(r, [])})
    if stale:
        raise StaleAttachment(
            f"already sent under this name: {', '.join(stale)}. Rename the edited file (her client caches by name).")


def record_sent(to, names, ledger: Path = LEDGER) -> None:
    """Mark names as sent, after Gmail accepted the send. Locked read-modify-write."""
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger.with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sent = _read(ledger)
        for r in _recipients(to):
            sent[r] = sorted(set(sent.get(r, [])) | {n.lower() for n in names})
        tmp = ledger.with_suffix(".tmp")
        tmp.write_text(json.dumps(sent, indent=1, sort_keys=True))
        os.replace(tmp, ledger)
