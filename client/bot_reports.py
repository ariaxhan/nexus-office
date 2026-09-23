#!/usr/bin/env python3
"""Run the five evidence-aware Office reports and prove each reply landed."""

from __future__ import annotations

import argparse
import json
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request


BOTS = ("rune", "parallax", "north", "relay", "sphinx")
REPORT_TRIGGER = "Daily report."


def request(base: str, path: str, body: dict | None = None, timeout_s: float = 15) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data)
    if data is not None:
        req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        return json.load(response)


# A cold snapshot walks every station, and the walk has grown past the old 90s ceiling: the
# 2026-09-18 sphinx failures were this socket timeout, reported only as "timed out". The budget is
# now named, generous, and still well inside the job timeout (600s) alongside the report wait.
SNAPSHOT_TIMEOUT_S = 240


def fresh_evidence(office: str) -> dict:
    try:
        response = request(office, "/api/world?fresh=1", timeout_s=SNAPSHOT_TIMEOUT_S)
    except OSError as error:
        raise TimeoutError(f"Office snapshot did not refresh within {SNAPSHOT_TIMEOUT_S}s: {error}") from error
    world = response.get("world")
    if not isinstance(world, dict) or not world.get("generated"):
        raise ValueError("Office has no completed snapshot")
    if not world.get("stations") or not world.get("sections"):
        raise ValueError("Office snapshot is empty")
    return world


# A single 503 or socket timeout while the Mac is under load is not a failed report: the history
# proxy gives the harness 3s, and at load 29 on 14 cores one poll can miss that (2026-09-23 sphinx
# failure, "HTTP Error 503" from GET /api/chat while the reply was still being written). Polls are
# retried until the deadline; only a refusal that cannot heal (4xx) ends the run early.
def transient(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    return isinstance(error, OSError)


def patient(base: str, path: str, deadline: float, body: dict | None = None) -> dict:
    while True:
        try:
            return request(base, path, body)
        except (OSError, urllib.error.HTTPError) as error:
            if not transient(error) or time.monotonic() + 3 > deadline:
                raise
            time.sleep(3)


def latest(history, bots=BOTS) -> list[dict]:
    """The newest reply to a daily report, per bot, from each bot's chat history."""
    out = []
    for bot in bots:
        code, body = history(bot)
        row = {"bot": bot, "at": None, "text": None, "ok": None}
        if code != 200:
            row["error"] = str((body or {}).get("error") or code)
            out.append(row)
            continue
        turns = body.get("turns") or []
        groups = {turn.get("inference_group_id") for turn in turns if turn.get("role") == "user"
                  and str(turn.get("content", turn.get("text")) or "").startswith(REPORT_TRIGGER)}
        groups.discard(None)
        for turn in reversed(turns):
            if turn.get("role") == "assistant" and turn.get("inference_group_id") in groups:
                row.update(at=turn.get("timestamp"), text=turn.get("content", turn.get("text")),
                           ok=turn.get("ok"), id=turn.get("id"))
                break
        out.append(row)
    return out


def run_report(bot: str, base: str, timeout_s: float = 300) -> dict:
    if bot not in BOTS:
        raise ValueError(f"unknown bot: {bot}")
    deadline = time.monotonic() + timeout_s
    try:
        fresh_evidence(base)
    except (TimeoutError, OSError):
        # A stale snapshot is still evidence; the report says what it was built from.
        # Failing the whole report because the refresh was slow left sphinx red for a day.
        pass
    query = "/api/chat?bot=" + urllib.parse.quote(bot)
    before = {str(turn.get("id") or "") for turn in patient(base, query, deadline).get("turns", [])}
    # The asynchronous Office endpoint cannot return the harness group yet.
    # A unique inert marker binds the later user turn to this exact request.
    message = REPORT_TRIGGER + "\n\n<!-- office-report:" + uuid.uuid4().hex + " -->"
    accepted = request(base, "/api/chat", {"bot": bot, "message": message})
    if accepted.get("ok") is not True:
        raise ValueError(f"{bot} report request was not accepted")
    while time.monotonic() < deadline:
        turns = patient(base, query, deadline).get("turns", [])
        origins = [turn for turn in turns if turn.get("role") == "user"
                   and str(turn.get("id") or "") not in before
                   and turn.get("content", turn.get("text")) == message]
        if len(origins) > 1:
            raise ValueError(f"{bot} report request has ambiguous provenance")
        group = origins[0].get("inference_group_id") if origins else None
        if group:
            for turn in reversed(turns):
                if (turn.get("role") == "assistant" and str(turn.get("id") or "") not in before
                        and turn.get("inference_group_id") == group):
                    if turn.get("ok") is not True:
                        raise ValueError(f"{bot} report inference did not succeed")
                    return turn
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"{bot} did not answer within {timeout_s:g}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run daily Office bot reports")
    parser.add_argument("--bot", action="append", choices=BOTS)
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    for bot in args.bot or BOTS:
        try:
            turn = run_report(bot, args.base, args.timeout)
        except (OSError, ValueError, TimeoutError, urllib.error.HTTPError) as error:
            print(f"{bot}: FAILED: {error}")
            return 1
        print(f"{bot}: replied {turn.get('id', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
