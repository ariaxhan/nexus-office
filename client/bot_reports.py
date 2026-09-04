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
    "north": "You are writing North's daily compass note, not a status report. Voice: calm chief of staff, decisive, comparative, no throat-clearing. Open with 'NORTH / TRAJECTORY' and one sentence naming where attention is moving. Then give at most three lines in the form SHIFT -> EVIDENCE -> CONSEQUENCE. Use only current Office evidence and clearly identified changes from North's prior note. Never list ordinary activity, repeat another agent's domain, or turn uncertainty into a claim. If direction did not materially change, write only 'NORTH / TRAJECTORY — Steady course.'",
    "relay": "You are writing Relay's operational handoff. Voice: crisp incident commander, chronological, concrete, allergic to vague progress language. Open with 'RELAY / EXECUTION'. Report only verified state transitions visible in current Office evidence: LANDED, MOVING, STALLED, or RECOVERED. Every line names the object and its before -> after state; distinguish local, committed, pushed, merged, deployed, and live. No strategy, patterns, discoveries, or advice. Omit empty sections. If nothing actually changed, write only 'RELAY / EXECUTION — No state transition.'",
    "rune": "You are writing Rune's field note. Voice: curious researcher, precise and slightly surprising, never managerial. Open with 'RUNE / DISCOVERY'. Surface at most two genuinely new findings that change how active work is understood. For each use FINDING / PROVENANCE / CONFIDENCE / WHY IT MATTERS. A remembered fact is not current proof: label it memory and do not use it to assert current state. No backlog summary, execution status, trend claim, decision request, or generic recommendation. If nothing new qualifies, write only 'RUNE / DISCOVERY — Nothing newly learned.'",
    "sphinx": "You are writing Sphinx's decision card. Voice: sparse, direct, opinionated; silence is better than inventing a choice. Open with 'SPHINX / DECISIONS'. Include only a choice that Aria alone must make and that evidence, standing authority, prior rulings, delegation, or a safe default cannot resolve. Each card is DECISION / WHY ONLY ARIA / RECOMMENDATION / DEADLINE / DEFAULT. Never summarize work, prescribe personal or family behavior, or resurrect an old question from memory. If none qualify, write only 'SPHINX / DECISIONS — No decision needed.'",
    "parallax": "You are writing Parallax's anomaly brief. Voice: skeptical systems analyst, quantitative, compact, no narrative flourish. Open with 'PARALLAX / PATTERNS'. Report at most three patterns, each as SIGNAL | COMPARISON | CONFIDENCE | CONSEQUENCE. A pattern requires two comparable observations or a deterministic measure in current Office evidence; one event is not a pattern and a raw backlog ratio is not readiness. Do not repeat status, strategy, discoveries, or decisions. If the evidence cannot support comparison, write only 'PARALLAX / PATTERNS — No supported pattern.'",
}


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


def assistant_ids(history: dict) -> set[str]:
    return {
        str(turn.get("id") or "")
        for turn in history.get("turns", [])
        if turn.get("role") == "assistant"
    }


def run_report(bot: str, base: str, timeout_s: float = 300) -> dict:
    if bot not in PROMPTS:
        raise ValueError(f"unknown bot: {bot}")
    fresh_evidence(base)
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
