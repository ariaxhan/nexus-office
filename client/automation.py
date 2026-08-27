"""What the automation is doing, as one page you can read.

The office already ships every part of this answer, and that is exactly the
problem it has: the schedule is on the Pipeline card, the public path is on the
Webhooks card, and what the runner actually DID is 24 receipts buried on each of
72 desks. Nobody assembles them, so the question a person actually asks -- "is
the thing running, what did it just touch, and where is what it said" -- takes
three screens and a terminal.

This module is that assembly and nothing else. It reads no files, runs no
subprocesses and calls no network: `build()` is a pure function of the snapshot
being built around it. Everything it knows, some other part of the office
already measured, which is the point. A second measurement is a second number to
keep in step and the first one to go wrong.

THE LINK IS THE FEATURE
-----------------------
A row that says "commented on warehouse#284 an hour ago" and makes you go and find
it is a row that costs more than it saves. So every row carries two links:

  issue_url    the thread. Always present when the receipt names an issue,
               because it is built from `owner/name` and a number.
  comment_url  the exact comment the runner left, when the office knows it.

`comment_url` comes from the desk, not from the receipt: office-sync already
pulls each open issue's last comment, so when the runner still has the last word
the URL of the thing it said is right there. It is deliberately absent, rather
than approximated, in the two cases where it is not known:

  * a human replied after the runner, so the last comment is theirs. The runner's
    comment is still in that thread and the issue link is the honest way to it.
  * the issue is closed, or on a desk that is put away, so it is not in the
    snapshot at all.

An approximated deep link is worse than no deep link: it lands on the wrong
comment and reads exactly like the right one.

WHAT `activity` IS AND IS NOT
-----------------------------
It is the runner's receipts, filtered to the ones that name an issue. That is a
choice: a sweep writes one `survey` receipt per repo per pass, so the raw file is
92% "9 open: 0 to work, 9 waiting on a human" and reading it is how nobody ever
notices the four rows that mattered. The surveys are still counted, in
`reached`, so what was dropped is visible as a number rather than silently.

It is NOT a log of what the pipeline is doing right now. That is `now`, and it
comes from the pipeline source's reading of the live pid and log.
"""

from __future__ import annotations

from sources import _card

# One screen of history. The receipts file goes back weeks and a page nobody
# scrolls to the bottom of is a page with a bottom nobody has read.
MAX_ACTIVITY = 60

# The outcomes a receipt can carry, and what each one means for a person. This
# table IS the "how does it work" answer for the activity list: without it every
# row is a word from a bash script that only dispatch.sh's author can read.
OUTCOMES = {
    "landed": ("ok", "opened a PR that closes the issue"),
    "parked": ("dim", "left alone on purpose: this repo is configured to park"),
    "refused": ("warn", "would not touch it, and said why"),
    "deferred": ("dim", "ran out of run, will pick it up next sweep"),
    "timeout": ("bad", "hit its per-issue cap and was stopped"),
    "report-only": ("dim", "reviewed and commented; this repo grants no code change"),
    "dry-run": ("dim", "decided everything, changed nothing"),
    "no-access": ("warn", "no account here can push, so nothing was attempted"),
    "no-issues": ("dim", "nothing open to work on"),
    "caught-up": ("dim", "every open issue is waiting on a human"),
    "survey": ("dim", "counted the open issues and moved on"),
}

# The receipts that describe the SWEEP rather than an issue. Same set office-sync
# calls NOISE, for the same reason, and named separately here because this module
# drops them from a list rather than skipping them in a headline.
PER_REPO = {"survey", "no-issues", "caught-up", "no-access"}

# How the whole thing works, in the order the events happen. Written here rather
# than in a Swift string and a JavaScript string, because it is one explanation
# and two copies of an explanation drift the first time the mechanism does.
HOW = [
    "A launchd job wakes the runner on the interval below. It sweeps every repo "
    "the office has a desk for, one at a time.",
    "On each repo it lists the open issues and picks the ones the bot did NOT "
    "comment on last. An issue the bot spoke on last is waiting on you, and it "
    "is left alone until you reply. No label gates anything.",
    "It runs one agent lane against one issue at a time, capped in minutes. "
    "What it is allowed to do is that repo's capability: most can open a branch "
    "and a PR, none can merge, none can push to a default branch.",
    "Every issue it touches gets a comment carrying the bot marker, even when the "
    "lane failed. An issue with no marker would be retried forever.",
    "A webhook does the same thing without waiting for the hour: GitHub posts to "
    "the door the moment an issue or comment changes, and the door asks the "
    "runner to look at that one repo.",
]


def _tone(outcome: str) -> str:
    return OUTCOMES.get(outcome, ("", ""))[0]


def _means(outcome: str) -> str:
    return OUTCOMES.get(outcome, ("", ""))[1]


def _issue_index(stations: list) -> dict:
    """{(repo, "284"): issue row} for every open issue on every visible desk.

    Keyed on the issue number AS A STRING, because that is what a receipt holds:
    dispatch.sh writes whatever `gh` gave it, and comparing "284" to 284 is the
    kind of quiet miss that would empty this whole column without an error.
    """
    out = {}
    for station in stations or []:
        repo = str((station or {}).get("repo") or "")
        if not repo:
            continue
        for issue in (station.get("issues") or []):
            num = str((issue or {}).get("number") or "").strip()
            if num:
                out[(repo, num)] = issue
    return out


def _row(receipt: dict, issues: dict, now_iso: str) -> dict:
    repo = str(receipt.get("repo") or "")
    issue = str(receipt.get("issue") or "").strip()
    outcome = str(receipt.get("outcome") or "")
    at = str(receipt.get("at") or "")
    known = issues.get((repo, issue)) or {}
    # Only when the bot still has the last word does that URL point at what the
    # runner said. A human reply moves it, and a link to a human's comment
    # labelled as the runner's is a lie with an anchor on it.
    comment = str(known.get("last_word_url") or "") if known.get("bot_last") else ""
    return {
        "at": at,
        "ago": _card.ago(at) or "",
        "repo": repo,
        "issue": issue,
        "outcome": outcome,
        "tone": _tone(outcome),
        "means": _means(outcome),
        "detail": str(receipt.get("detail") or "")[:200],
        "title": str(known.get("title") or "")[:120],
        "issue_url": f"https://github.com/{repo}/issues/{issue}" if repo and issue else "",
        "comment_url": comment,
        "comment_at": str(known.get("last_word_at") or "") if comment else "",
    }


def build(by_repo: dict, stations: list, sections: dict, counts: dict,
          now_iso: str = "") -> dict:
    """The whole automation page, from what the snapshot already measured.

    `by_repo`  the runner's receipts, grouped and newest-first, from office-sync
    `stations` the desks, for issue titles and comment links
    `sections` the room's fixtures, for the pipeline's live state and the door's
    `counts`   the rolling 24h outcome tally office-sync already derived

    Every field the app and the phone draw is present in every return, including
    when the pipeline could not be read at all, so no renderer has to decide
    whether a missing key means "no" or means "nobody looked".
    """
    pipeline = (sections or {}).get("pipeline") or {}
    webhook = (sections or {}).get("webhook") or {}
    issues = _issue_index(stations)

    rows = []
    for repo_receipts in (by_repo or {}).values():
        for receipt in repo_receipts or []:
            if not isinstance(receipt, dict):
                continue
            if not str(receipt.get("issue") or "").strip():
                continue
            if str(receipt.get("outcome") or "") in PER_REPO:
                continue
            rows.append(receipt)
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    dropped = max(0, len(rows) - MAX_ACTIVITY)
    activity = [_row(r, issues, now_iso) for r in rows[:MAX_ACTIVITY]]

    cover = pipeline.get("covered") or {}
    state = str(pipeline.get("state") or "unknown")

    return {
        "state": state,
        "headline": _headline(pipeline, webhook, activity),
        "how": list(HOW),

        # WHEN it looks.
        "schedule": {
            "every": str(pipeline.get("every") or ""),
            "next_in": pipeline.get("next_in"),
            "next_at": pipeline.get("next_at"),
            "overdue": bool(pipeline.get("overdue")),
            "late_by": pipeline.get("late_by"),
            "enabled": pipeline.get("enabled"),
            "kill_switch": bool(pipeline.get("kill_switch")),
            "power": str(pipeline.get("power") or "unknown"),
            # dispatch defers the whole run on battery, so an hourly deferral
            # and an idle hour look identical without this.
            "deferring": bool(pipeline.get("deferring")),
            "last_full_run": pipeline.get("heartbeat"),
            "last_full_run_age_s": pipeline.get("heartbeat_age_s"),
        },

        # WHETHER it is looking right now.
        "now": {
            "running": bool(pipeline.get("running")),
            "for": pipeline.get("running_for"),
            "doing": str(pipeline.get("doing") or ""),
            "last_said": str(pipeline.get("last_said") or "")[:400],
            "last_said_at": pipeline.get("last_said_at"),
            "last_said_age_s": pipeline.get("last_said_age_s"),
            "stale_pid": pipeline.get("stale_pid"),
            "detail": str(pipeline.get("detail") or ""),
        },

        # The other way in: GitHub telling us, instead of us asking.
        "trigger": {
            "state": str(webhook.get("state") or "unknown"),
            "reachable": bool(webhook.get("state") == "ok"),
            "deliveries": webhook.get("events_total"),
            "today": webhook.get("events_today"),
            "last_at": webhook.get("last_at"),
            "last_age_s": webhook.get("last_age_s"),
            "runs_today": webhook.get("runs_today"),
            "queued": len(webhook.get("queued") or []),
            "detail": str(webhook.get("detail") or ""),
            "blocked_by": str(webhook.get("blocked_by") or ""),
        },

        # WHAT it reached, and what this list is not showing.
        "reached": {
            "repos": cover.get("repos"),
            "receipts": cover.get("receipts"),
            "window": "24h",
            "state": str(cover.get("state") or "unknown"),
        },
        "counts": dict(counts or {}),
        "activity": activity,
        # Never a silent truncation: a list capped without saying so reads as
        # "that is everything that happened".
        "activity_dropped": dropped,
    }


def _headline(pipeline: dict, webhook: dict, activity: list) -> str:
    """The one sentence, and it says the WORST true thing first.

    Order matters and is not aesthetic. A run in flight is the most present
    fact; a scheduler that stopped firing is the most expensive one to not
    notice; and "nothing happened" is only reassuring once you know both.
    """
    state = str(pipeline.get("state") or "")
    if state not in ("ok", "off"):
        return _card.clip(pipeline.get("detail") or f"the pipeline is {state}", 79)
    # A run in flight outranks the kill switch, exactly as it does on the
    # Pipeline card. The switch stops the NEXT run; it does not reach into one
    # already going, and a page that says "switched off" while a lane is pushing
    # a branch is the more dangerous of the two sentences.
    if pipeline.get("running"):
        doing = str(pipeline.get("doing") or "").strip()
        return _card.clip(f"running now: {doing}" if doing else "a run is in flight", 79)
    if pipeline.get("kill_switch"):
        return "switched off at the kill switch; nothing will run until it is removed"
    if state == "off":
        return _card.clip(pipeline.get("detail") or "the job is off", 79)
    if pipeline.get("overdue"):
        return _card.clip(
            f"overdue by {pipeline.get('late_by') or 'a while'}; it should have looked already", 79)
    if pipeline.get("deferring"):
        return "on battery, so every run is deferring without doing anything"
    touched = len(activity)
    nxt = pipeline.get("next_in") or "soon"
    if not touched:
        return _card.clip(f"idle, next look {nxt}; no issue touched in the last day", 79)
    return _card.clip(
        f"idle, next look {nxt}; {touched} {_card.plural(touched, 'issue')} touched recently", 79)
