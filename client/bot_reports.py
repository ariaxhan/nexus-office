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
    "north": "Run today's NORTH / TRAJECTORY report. Cover only changes in direction, priority, or attention since the last report. Do not summarize operations, patterns, discoveries, or decisions. Start with the exact heading 'NORTH / TRAJECTORY'. Give at most three changes, each as: shift -> evidence -> implication. If none changed, say 'No trajectory change.'",
    "relay": "Run today's RELAY / EXECUTION report. Cover only work that landed, failed, stalled, or recovered since the last report, verified by current evidence. Do not discuss strategy, recurring patterns, research context, or decisions. Start with the exact heading 'RELAY / EXECUTION'. Use four compact sections: Landed, Moving, Stalled, Recovered. Omit empty sections; if all are empty, say 'No execution change.'",
    "rune": "Run today's RUNE / DISCOVERY report. Surface only new evidence or remembered context that changes how one active item should be understood. Do not provide status, strategy, pattern analysis, or ask decisions. Start with the exact heading 'RUNE / DISCOVERY'. Give at most two findings as: finding; source/provenance; confidence; why it matters. If none exist, say 'No new discovery.'",
    "sphinx": "Run today's SPHINX / DECISIONS report. Include only decisions that genuinely require Aria and cannot be answered from evidence, prior rulings, delegated authority, or a safe default. Do not summarize work or recommend personal actions. Start with the exact heading 'SPHINX / DECISIONS'. For each decision give: decision, why only Aria can make it, deadline, and default if unanswered. If none exist, say 'No decision needed.'",
    "parallax": "Run today's PARALLAX / PATTERNS report. Cover only recurrence, drift, or anomalies supported by at least two comparable observations or a deterministic measure. Never infer a pattern from one event and do not report ordinary status. Start with the exact heading 'PARALLAX / PATTERNS'. Give at most three items as: pattern -> measure/comparison -> confidence -> consequence. If none qualify, say 'No supported pattern.'",
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
