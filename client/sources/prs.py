"""The merge plan: which open PRs can land now, across every desk.

The PR pipeline (`_meta/services/pr-pipeline/prs.sh`) reviews every open PR at
its current head, fixes what it can on the branch, and writes one plan per repo
to `_meta/services/pr-pipeline/.runtime/plan/<owner>_<repo>.json`. This card
reads those files and nothing else: no GitHub, no subprocess, no second
measurement of anything the runner already measured.

`needs` counts the PRs parked with a question for a person (a rolling branch to
close or split, three reviews without an APPROVE) plus repos whose plan is torn.
A PR merely waiting on a review is not a need: the next sweep owns it.

A plan older than a day is stale and says so in the headline: the runner is a
15-minute job, and a plan nobody has refreshed is a plan about a different
GitHub.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

from sources import _card

KEY = "prs"
TITLE = "Pull requests"

PLANS = "_meta/services/pr-pipeline/.runtime/plan"
HEARTBEAT = "_meta/services/pr-pipeline/.runtime/last-success"
STALE_S = 24 * 3600

TROUBLE = {
    "unconfigured": ("No vault to read merge plans from", 0),
    "never": ("The PR pipeline has never swept", 1),
}


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def read(now: float | None = None) -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured", "detail": "OFFICE_RUNTIME_ROOT is not set"}
    plans_dir = root / PLANS
    beat = root / HEARTBEAT
    if not beat.exists() and not plans_dir.exists():
        return {"state": "never", "detail": f"no {HEARTBEAT}"}

    repos, torn = [], []
    for path in sorted(plans_dir.glob("*.json")) if plans_dir.exists() else []:
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            steps, parked = plan["steps"], plan["parked"]
        except (OSError, ValueError, KeyError, TypeError):
            torn.append(path.name)
            continue
        repos.append({
            "repo": plan.get("repo") or path.stem.replace("_", "/", 1),
            "open": len(steps) + len(parked),
            "merge_now": [s["number"] for s in steps if s.get("merge_now")],
            "waiting": [s["number"] for s in steps if not s.get("merge_now")],
            "parked": [{"number": p["number"], "kind": p.get("kind", "")} for p in parked],
        })

    as_of = ""
    try:
        as_of = _card.zulu(beat.read_text(encoding="utf-8").strip()) if beat.exists() else ""
    except OSError:
        as_of = ""
    stale = False
    if as_of:
        t = _card_epoch(as_of)
        stale = t is not None and ((now if now is not None else time.time()) - t) > STALE_S
    return {"state": "ok", "repos": repos, "torn": torn, "as_of": as_of, "stale": stale}


def _card_epoch(z: str) -> float | None:
    try:
        return time.mktime(time.strptime(z, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, OverflowError):
        return None


def card(data: dict) -> dict:
    state = data.get("state")
    if state in TROUBLE:
        return _card.trouble(TITLE, state, data.get("detail"), TROUBLE)

    repos = data.get("repos") or []
    torn = data.get("torn") or []
    as_of = data.get("as_of") or ""
    open_n = sum(r["open"] for r in repos)
    now_n = sum(len(r["merge_now"]) for r in repos)
    parked = [(r["repo"], p) for r in repos for p in r["parked"]]
    asked = [p for p in parked if p[1]["kind"] == "rolling"]
    needs = len(asked) + len(torn)

    if data.get("stale"):
        headline = f"Merge plan is stale ({_card.ago(as_of)}); {open_n} PRs open"
        needs = max(needs, 1)
    elif not repos:
        headline = "No open PRs anywhere"
    elif now_n:
        headline = f"{now_n} of {open_n} open PRs can merge now"
    else:
        headline = f"{open_n} open PRs, none ready to merge yet"

    facts = [
        _card.fact("can merge now", str(now_n), "ok" if now_n else "dim"),
        _card.fact("waiting on a review", str(sum(len(r["waiting"]) for r in repos)), "dim"),
        _card.fact("rolling branches to close", str(len(asked)), "warn" if asked else "dim"),
        _card.fact("repos with PRs", str(len(repos)), "dim"),
    ]
    if torn:
        facts.append(_card.fact("torn plans", ", ".join(torn)[:100], "bad"))
    if as_of:
        facts.append(_card.fact("last sweep", _card.ago(as_of), "dim"))

    rows = []
    for r in sorted(repos, key=lambda r: (-len(r["merge_now"]), r["repo"])):
        badge = f"{len(r['merge_now'])}/{r['open']}"
        tone = "ok" if r["merge_now"] else ("warn" if r["parked"] else "dim")
        sub = ", ".join(f"#{n}" for n in r["merge_now"]) or "nothing ready"
        rows.append(_card.row(r["repo"], r["repo"], sub,
                              f"{len(r['waiting'])} waiting, {len(r['parked'])} parked",
                              badge, tone, "", f"https://github.com/{r['repo']}/pulls"))
    return _card.build(TITLE, headline, needs, as_of, facts, rows)
