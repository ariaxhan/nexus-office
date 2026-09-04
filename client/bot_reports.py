#!/usr/bin/env python3
"""Run the five evidence-aware Office reports and prove each reply landed."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request


BOTS = ("rune", "parallax", "north", "relay", "sphinx")
PROMPTS = {
    "north": "Run today's orientation report. Name only material changes in direction, priorities, or attention from the current Office evidence. If none changed, say so plainly.",
    "relay": "Run today's execution report. Name only material lifecycle changes, failures, stalls, recoveries, and states actually verified by the current Office evidence.",
    "rune": "Run today's discovery report. Name only evidence or remembered context that makes active work more understandable, actionable, or questionable, with provenance and confidence.",
    "sphinx": "Run today's decision report. Ask only decisions that genuinely require Aria and cannot be answered from evidence, prior decisions, authority, or a safe default. If none exist, say so plainly.",
    "parallax": "Run today's pattern report. Name only recurrence, drift, or anomalies supported by deterministic measures across the current Office evidence. Distinguish patterns from isolated events.",
}


def request(base: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data)
    if data is not None:
        req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def assistant_ids(history: dict) -> set[str]:
    return {
        str(turn.get("id") or "")
        for turn in history.get("turns", [])
        if turn.get("role") == "assistant"
    }


def run_report(bot: str, base: str, timeout_s: float = 300) -> dict:
    if bot not in PROMPTS:
        raise ValueError(f"unknown bot: {bot}")
    query = "/api/chat?bot=" + urllib.parse.quote(bot)
    before = assistant_ids(request(base, query))
    request(base, "/api/chat", {"bot": bot, "message": PROMPTS[bot]})
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for turn in reversed(request(base, query).get("turns", [])):
            if turn.get("role") == "assistant" and str(turn.get("id") or "") not in before:
                return turn
        time.sleep(2)
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
