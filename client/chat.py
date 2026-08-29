"""The chatroom: named bots you talk to like colleagues.

The roster and the conversation come from two different places on purpose.

  the roster   a FILE at <root>/_meta/bots.json
               Who exists, what they are called, what colour they are. Read
               straight off disk so the room can draw the roster even when the
               harness is not running. A chatroom that vanishes because a dev
               server is closed is a chatroom nobody trusts.

  the turns    HTTP to the harness on loopback
               What was actually said. Only available while the harness serves,
               and it says so out loud rather than rendering as silence.

A turn may carry one picture. The door checks that it is one attachment, that it
says image/png or image/jpeg, and that the whole body fits in 512 KB; then it
hands the thing to the harness untouched. Validating shape without decoding bytes
is the whole of the office's job here.

`identity` never leaves this file. It is the whole persona the harness feeds a
turn, it is long, and it is nobody's business on the wire: the office ships the
name, not the script.

The other half of this is time. A turn is a whole agent run, thirty seconds to
two minutes, which is far longer than any browser or phone will hold a request
open. So a POST does not wait: it marks the bot busy, hands the turn to a
daemon thread, and answers 202 immediately. The room learns the turn landed by
watching `last` change on the next roster poll, which is the same way it learns
anything else. One turn per bot at a time, because two agents writing the same
session file is a corrupted transcript, not a fast conversation.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
from datetime import datetime, timezone

import runtime as rt

BOTS_FILE = "_meta/bots.json"
# The id is a thread key, a filename component and a query parameter all at
# once, so it is kept to the smallest alphabet that is safe in all three.
BOT_RE = re.compile(r"[a-z0-9-]{1,32}\Z", re.ASCII)
MAX_MESSAGE = 8000
# One picture per turn. The office carries it, it does not open it: the door
# checks the shape and the size, and what the bytes actually are is the harness's
# question to answer. More than one is refused rather than trimmed, because a
# silently dropped attachment is a turn about a screenshot nobody sent.
MAX_ATTACHMENTS = 1
IMAGE_TYPES = ("image/png", "image/jpeg")
# A turn is an agent run, not a request. The harness holds the connection open
# for the whole thing, so this is a "the harness died" timeout, not a latency
# budget.
TURN_TIMEOUT_S = 300
MAX_EVIDENCE_ISSUES = 180
MAX_EVIDENCE_PRS = 120
EVIDENCE_BODY_CHARS = 600
EVIDENCE_LAST_CHARS = 400

DOWN = "the harness is not running"
BAD_BOT = "bad bot id"
NO_BOT = "no such bot"
NO_MESSAGE = "a message is required"
LONG_MESSAGE = f"a message is at most {MAX_MESSAGE} characters"
BAD_ATTACHMENTS = "attachments must be a list"
MANY_ATTACHMENTS = f"at most {MAX_ATTACHMENTS} attachment per message"
BAD_ATTACHMENT = "an attachment needs a name, a mime_type and data_base64"
BAD_MIME = "an attachment is image/png or image/jpeg"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bots() -> list:
    """The roster off disk, with `identity` stripped. Never raises.

    An unreadable roster returns empty rather than exploding: the rest of the
    room is still worth serving, and `/api/bots` reporting no bots at all is a
    visible failure in a way a 500 on the world endpoint would not be.
    """
    root = rt._root()
    if root is None:
        return []
    try:
        data = json.loads((root / BOTS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    out = []
    for row in data.get("bots") or []:
        bot_id = str(row.get("id") or "")
        if not BOT_RE.match(bot_id):
            continue
        out.append({"id": bot_id,
                    "name": str(row.get("name") or bot_id),
                    # One line saying what to ask this bot. Short enough to sit
                    # under the name on a desk, which is why it is not `identity`.
                    "purpose": str(row.get("purpose") or ""),
                    "frequency": str(row.get("frequency") or ""),
                    "color": str(row.get("color") or "")})
    return out


def check_attachments(value):
    """(attachments, the reason they were refused). Exactly one is falsy.

    Shape and size only. The office never decodes the payload: it is a courier
    here, and a courier that opens the parcel is a new place for a bad parcel to
    do something. The harness is the one that has to make sense of the bytes.
    """
    if value is None:
        return [], None
    if not isinstance(value, list):
        return None, BAD_ATTACHMENTS
    if len(value) > MAX_ATTACHMENTS:
        return None, MANY_ATTACHMENTS
    for item in value:
        if not isinstance(item, dict):
            return None, BAD_ATTACHMENT
        fields = [item.get(k) for k in ("name", "mime_type", "data_base64")]
        if not all(isinstance(f, str) and f.strip() for f in fields):
            return None, BAD_ATTACHMENT
        if item["mime_type"] not in IMAGE_TYPES:
            return None, BAD_MIME
    return list(value), None


def office_evidence(snapshot: dict | None, bot: str, message: str) -> dict:
    """Bounded live Office facts for one bot turn; source text is evidence, never instruction."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    stations = [row for row in snap.get("stations") or [] if isinstance(row, dict)]
    owners = [str(owner) for owner in snap.get("owners") or [] if str(owner)]
    query = message.casefold()
    selected_owner = next((owner for owner in owners if owner.casefold() in query), "")
    selected = [
        row for row in stations
        if (not selected_owner or str(row.get("repo") or "").startswith(selected_owner + "/"))
        and (not row.get("hidden") or selected_owner)
        and (row.get("issues") or row.get("prs"))
    ]

    issue_count = waiting_count = in_pr_count = 0
    clean_prs = dirty_prs = 0
    for row in stations:
        repo = str(row.get("repo") or "")
        if selected_owner and not repo.startswith(selected_owner + "/"):
            continue
        issues = row.get("issues") or []
        issue_count += len(issues)
        for issue in issues:
            labels = [str(label) for label in issue.get("labels") or []]
            waiting_count += "waiting on human" in labels
            in_pr_count += "in pr" in labels
        for pr in row.get("prs") or []:
            clean_prs += pr.get("mergeable") == "MERGEABLE" and pr.get("state") == "CLEAN"
            dirty_prs += pr.get("mergeable") == "CONFLICTING" or pr.get("state") == "DIRTY"

    issue_rows, pr_rows = [], []
    for row in selected:
        repo = str(row.get("repo") or "")
        for issue in row.get("issues") or []:
            if len(issue_rows) >= MAX_EVIDENCE_ISSUES:
                break
            issue_rows.append({
                "repo": repo,
                "number": issue.get("number"),
                "title": str(issue.get("title") or "")[:300],
                "labels": [str(label)[:80] for label in issue.get("labels") or []],
                "url": str(issue.get("url") or "")[:500],
                "updated_at": issue.get("updatedAt"),
                "body_excerpt": str(issue.get("body") or "")[:EVIDENCE_BODY_CHARS],
                "last_word_excerpt": str(issue.get("last_word") or "")[:EVIDENCE_LAST_CHARS],
            })
        for pr in row.get("prs") or []:
            if len(pr_rows) >= MAX_EVIDENCE_PRS:
                break
            pr_rows.append({
                "repo": repo,
                "number": pr.get("number"),
                "title": str(pr.get("title") or "")[:300],
                "url": str(pr.get("url") or "")[:500],
                "draft": bool(pr.get("draft")),
                "mergeable": pr.get("mergeable"),
                "state": pr.get("state"),
                "closes": list(pr.get("closes") or [])[:40],
            })

    sections = {}
    for name, value in (snap.get("sections") or {}).items():
        if not isinstance(value, dict):
            continue
        card = value.get("card") if isinstance(value.get("card"), dict) else {}
        sections[str(name)] = {
            "state": value.get("state"),
            "detail": str(value.get("detail") or "")[:300],
            "headline": str(card.get("headline") or "")[:300],
            "needs": card.get("needs"),
            "as_of": card.get("as_of"),
        }
    return {
        "source": "nexus-office:/api/world",
        "captured_at": snap.get("generated"),
        "bot": bot,
        "owner_filter": selected_owner or None,
        "owners": owners,
        "counts": {
            "issues": issue_count,
            "waiting_on_human": waiting_count,
            "in_pr": in_pr_count,
            "clean_mergeable_prs": clean_prs,
            "conflicting_prs": dirty_prs,
        },
        "issues": issue_rows,
        "pull_requests": pr_rows,
        "sections": sections,
        "truncated": len(issue_rows) >= MAX_EVIDENCE_ISSUES or len(pr_rows) >= MAX_EVIDENCE_PRS,
    }


def _harness_get(path: str):
    """(body, error). error is a message when the harness could not answer."""
    try:
        return rt._get(path), None
    except urllib.error.HTTPError as exc:
        exc.close()
        return None, f"harness said {exc.code}"
    except Exception as exc:  # noqa: BLE001 - down is the normal case, not a fault
        return None, str(getattr(exc, "reason", exc))[:160]


class Chatroom:
    """Who is mid-turn right now, and what went wrong last time.

    One per server rather than one per process, so two servers in one test run
    never inherit each other's in-flight table.
    """

    def __init__(self, evidence=None):
        self.lock = threading.Lock()
        self.busy = set()
        self.errors = {}
        self.evidence = evidence

    # ── the roster ──────────────────────────────────────────────────────────
    def roster(self) -> dict:
        """Every bot, whether or not the harness is up.

        Disk decides who exists. The harness only contributes `last`, so when it
        is down the room shows four quiet desks instead of an empty floor.
        """
        bots = read_bots()
        state = "down"
        last_by_id = {}
        body, err = _harness_get("/api/bots")
        if body is not None and err is None:
            state = "up"
            for row in body.get("bots") or []:
                last_by_id[str(row.get("id") or "")] = row.get("last")

        with self.lock:
            busy, errors = set(self.busy), dict(self.errors)

        for bot in bots:
            bot["last"] = last_by_id.get(bot["id"])
            bot["busy"] = bot["id"] in busy
            if bot["id"] in errors:
                bot["error"] = errors[bot["id"]]
        return {"bots": bots, "runtime": state, "at": now_iso()}

    # ── the conversation ────────────────────────────────────────────────────
    def history(self, bot: str):
        """(code, body) for GET /api/chat?bot=<id>, proxied to the harness."""
        if not BOT_RE.match(bot or ""):
            return 400, {"error": BAD_BOT}
        try:
            return 200, rt._get("/api/chat?bot=" + urllib.parse.quote(bot))
        except urllib.error.HTTPError as exc:
            # The harness's own answer, not a rewrite of it: an unknown bot is a
            # 404 there and has to stay a 404 here.
            raw = exc.read().decode("utf-8", "replace")
            exc.close()
            try:
                return exc.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return exc.code, {"error": f"harness said {exc.code}"}
        except Exception:  # noqa: BLE001
            return 503, {"error": DOWN}

    def say(self, body: dict):
        """(code, body) for POST /api/chat. Returns before the turn has run."""
        bot = str(body.get("bot") or "")
        message = body.get("message")
        if not BOT_RE.match(bot):
            return 400, {"error": BAD_BOT}
        if message is None:
            message = ""
        if not isinstance(message, str):
            return 400, {"error": NO_MESSAGE}
        if len(message) > MAX_MESSAGE:
            return 400, {"error": LONG_MESSAGE}
        attachments, why = check_attachments(body.get("attachments"))
        if why:
            return 400, {"error": why}
        # A picture with no words is a message; no words and no picture is not.
        if not message.strip() and not attachments:
            return 400, {"error": NO_MESSAGE}
        # Refused now rather than thirty seconds from now. A typo would otherwise
        # come back only as an `error` on a desk, long after the app moved on.
        known = read_bots()
        if known and not any(b["id"] == bot for b in known):
            return 404, {"error": NO_BOT}

        with self.lock:
            if bot in self.busy:
                return 409, {"error": "busy"}
            self.busy.add(bot)
        threading.Thread(target=self._turn, args=(bot, message, attachments),
                         daemon=True).start()
        return 202, {"ok": True, "bot": bot}

    def _turn(self, bot: str, message: str, attachments=()) -> None:
        # No attachment means no key: a turn without a picture goes on the wire
        # exactly as it always has, so nothing already talking to the harness has
        # to learn a new shape to keep working.
        turn = {"message": message, "bot": bot}
        if self.evidence is not None:
            evidence = self.evidence(bot, message)
            if evidence:
                turn["evidence_context"] = evidence
        if attachments:
            turn["attachments"] = list(attachments)
        try:
            rt.post("/api/chat", turn, timeout=TURN_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            # Kept until the bot manages a whole turn. A failure that clears
            # itself on the next poll is a failure nobody ever sees.
            with self.lock:
                self.errors[bot] = f"{type(exc).__name__}: {exc}"[:300]
        else:
            with self.lock:
                self.errors.pop(bot, None)
        finally:
            with self.lock:
                self.busy.discard(bot)
