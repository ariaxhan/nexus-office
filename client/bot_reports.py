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


def fresh_evidence(office: str) -> dict:
    response = request(office, "/api/world?fresh=1", timeout_s=90)
    world = response.get("world")
    if not isinstance(world, dict) or not world.get("generated"):
        raise ValueError("Office has no completed snapshot")
    if not world.get("stations") or not world.get("sections"):
        raise ValueError("Office snapshot is empty")
    return world


def run_report(bot: str, base: str, timeout_s: float = 300) -> dict:
    if bot not in BOTS:
        raise ValueError(f"unknown bot: {bot}")
    fresh_evidence(base)
    query = "/api/chat?bot=" + urllib.parse.quote(bot)
    before = {str(turn.get("id") or "") for turn in request(base, query).get("turns", [])}
    # The asynchronous Office endpoint cannot return the harness group yet.
    # A unique inert marker binds the later user turn to this exact request.
    message = REPORT_TRIGGER + "\n\n<!-- office-report:" + uuid.uuid4().hex + " -->"
    accepted = request(base, "/api/chat", {"bot": bot, "message": message})
    if accepted.get("ok") is not True:
        raise ValueError(f"{bot} report request was not accepted")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        turns = request(base, query).get("turns", [])
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
