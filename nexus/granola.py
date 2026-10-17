"""Issue #308: a Granola meeting's commitments, filed as dispatch records at extraction time.

The transcript processor (Vaults `_meta/services/granola_processor.py`) does the LLM extraction and
hands its verified receipt here, for new transcripts and for the historical archive alike. Each
commitment becomes one owner-held record whose state the poller advances from its completion
surface; nothing downstream re-reads prose.
"""

import os

from . import dispatch

DAY = 86400
STALL_S = {"build-order": 14 * DAY, "work": 7 * DAY, "executed": DAY}


def _issue_verify(repo, number):
    return ["sh", "-c", 'test "$(gh issue view "$1" --repo "$2" --json state -q .state)" = CLOSED',
            "sh", str(number), repo]


def commitments(meeting_id, receipt, vault):
    """Every commitment in a verified receipt, as record fields. Pure; one per surface."""
    issues = list(receipt.get("issues") or [])
    orders = {(o.get("repo"), o.get("number")): o for o in receipt.get("build_orders") or []}
    known = {(i.get("repo"), i.get("number")) for i in issues}
    issues += [o for k, o in orders.items() if k not in known]
    out, seen = [], set()
    for item in issues:
        repo, num = item.get("repo"), item.get("number")
        if not repo or not num or (repo, num) in seen:
            continue
        seen.add((repo, num))
        kind = "build-order" if (repo, num) in orders or item.get("kind") == "build-order" else "work"
        act = item.get("title") or (orders.get((repo, num)) or {}).get("summary") or f"{repo}#{num}"
        out.append({"key": f"granola:{meeting_id}:{repo}#{num}", "owner": item.get("owner") or "unassigned",
                    "act": act, "surface": f"{repo}#{num} closed", "verify": _issue_verify(repo, num),
                    "stall_s": STALL_S[kind]})
    for n, ai in enumerate(receipt.get("aria_items") or []):
        ev = (ai.get("evidence") or "").strip().rstrip(".,;")
        if ai.get("disposition") != "executed" or "/" not in ev or ev.startswith("http"):
            continue  # needs-aria stays human-held in the processor's digest; issue evidence is covered above
        path = ev if ev.startswith("/") else os.path.join(vault, ev)
        out.append({"key": f"granola:{meeting_id}:aria:{n}", "owner": "aria", "act": ai.get("summary") or ev,
                    "surface": f"{path} exists", "verify": ["test", "-e", path], "stall_s": STALL_S["executed"]})
    return out


def file_meeting(led, meeting_id, title, date, receipt, vault, now=None):
    """File one meeting's commitments. Idempotent: a reprocess or archive pass returns the same ids."""
    if not (isinstance(meeting_id, str) and meeting_id.strip()):
        raise dispatch.DispatchError("required: meeting_id")
    source = f'Granola meeting "{title}" ({date}), id {meeting_id}'
    return [dispatch.file_commitment(led, c["key"], c["owner"], c["act"], source, c["surface"], c["verify"],
                                     c["stall_s"], now=now)
            for c in commitments(meeting_id, receipt, vault)]
