"""Independent, read-only monitor for missed MacBook fallback publication."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
from html import escape
from http.client import HTTPException
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen

URL = "https://arias-macbook-pro-2.tail4f309a.ts.net:9443/api/health"
GH = "/opt/homebrew/bin/gh"
MAX_BODY = 16 * 1024
ALERT_AGE_S = 660  # Two five-minute publications missed, with one minute grace.
RECOVERY_AGE_S = 420


def probe(url: str = URL, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        with urlopen(url, timeout=12) as response:
            status = response.status
            raw = response.read(MAX_BODY + 1)
    except HTTPError as error:
        status = error.code
        raw = error.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("health response too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("mode") != "read-only-fallback":
        raise ValueError("unexpected fallback health response")
    stamp = data.get("snapshot_at")
    if not isinstance(stamp, str) or len(stamp) > 80:
        raise ValueError("fallback snapshot timestamp missing")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("fallback snapshot timestamp has no timezone")
    age = now - parsed.timestamp()
    if age < -60:
        raise ValueError("fallback snapshot timestamp is future")
    return {"status": status, "snapshot_at": stamp, "age_s": round(age),
            "healthy": status == 200 and data.get("ok") is True}


def issue_comment(body: str) -> bool:
    try:
        result = subprocess.run([GH, "issue", "comment", "269", "-R",
                                 "ariaxhan/nexus-office", "--body", body],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=25, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def bounded_probe(url: str = URL) -> dict:
    """Hard wall-clock bound, including DNS and a trickling HTTP response."""
    def expired(signum, frame):
        raise TimeoutError("fallback health probe deadline")
    previous = signal.signal(signal.SIGALRM, expired)
    prior_timer = signal.setitimer(signal.ITIMER_REAL, 45)
    try:
        return probe(url)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *prior_timer)
        signal.signal(signal.SIGALRM, previous)


def advance(state: dict, observation: dict | None, *, comment=issue_comment) -> dict:
    """Compute one bounded alert transition. Never triggers a publication."""
    state = dict(state)
    bad = state.get("bad_probes", 0)
    bad = bad if isinstance(bad, int) and bad >= 0 else 0
    alert_open = bool(state.get("alert_open", False))
    reason = ""
    if observation is None:
        bad += 1
        if bad >= 2:
            reason = "MacBook fallback health was unreachable or invalid twice consecutively"
    else:
        age = observation["age_s"]
        state["last_snapshot_at"] = observation["snapshot_at"]
        if age >= ALERT_AGE_S:
            reason = (f"MacBook fallback signed snapshot is {age}s old; "
                      f"last publication {observation['snapshot_at']}")
            bad += 1
        elif not observation["healthy"]:
            bad += 1
            if bad >= 2:
                reason = f"MacBook fallback health returned {observation['status']} twice"
        else:
            bad = 0
            if alert_open and age <= RECOVERY_AGE_S:
                if comment("MacBook Office fallback publication recovered. HTTPS health is 200 "
                           f"with signed snapshot {observation['snapshot_at']} (age {age}s)."):
                    alert_open = False
    if reason and not alert_open:
        if comment(reason + ". This watchdog only observes; check Studio publisher "
                   "com.aria.office-fallback-publish and MacBook 9443. The last "
                   "verified snapshot stays read-only and health turns 503 at 15 minutes."):
            alert_open = True
    state.update({"bad_probes":bad,"alert_open":alert_open,
                  "last_reason":reason,"checked_at":datetime.now(timezone.utc).isoformat()})
    return state


def status_html(state: dict, observed: dict | None) -> str:
    checked = escape(str(state.get("checked_at", "unknown")))
    stamp = escape(str((observed or {}).get("snapshot_at", "unavailable")))
    age = (observed or {}).get("age_s")
    status = "Alert open" if state.get("alert_open") else "Watching"
    detail = (f"Signed snapshot {stamp} · {age}s old" if age is not None else
              "MacBook health unavailable")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='refresh' content='120'>"
            "<title>MacBook fallback watchdog</title><style>"
            ":root{font-family:Georgia,serif;background:#f7f5f0;color:#242424}"
            "body{max-width:720px;margin:8vh auto;padding:20px}h1{font-weight:400}"
            "p{line-height:1.5}.muted{color:#515151}</style></head><body><main>"
            "<p>NEXUS OFFICE · FALLBACK WATCHDOG</p>"
            f"<h1>{status}</h1><p>{detail}</p>"
            f"<p class='muted'>Last independent check: {checked}. "
            "This page refreshes every two minutes; an old check time means "
            "the watchdog itself may have stopped.</p></main></body></html>")


def run(state_path: Path, lock_path: Path, url: str = URL,
        status_path: Path | None = None) -> tuple[dict, dict | None]:
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text())
            if not isinstance(state, dict):
                state = {}
        except (FileNotFoundError, OSError, ValueError):
            state = {}
        try:
            observation = bounded_probe(url)
        except (OSError, ValueError, json.JSONDecodeError, HTTPException,
                TimeoutError) as error:
            observation = None
            print(f"fallback watchdog probe failed: {type(error).__name__}", file=sys.stderr)
        updated = advance(state, observation)
        staged = state_path.with_name(state_path.name + ".incoming")
        staged.write_text(json.dumps(updated, separators=(",", ":")) + "\n")
        staged.chmod(0o600)
        os.replace(staged, state_path)
        if status_path is not None:
            status_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            staged_html = status_path.with_name(status_path.name + ".incoming")
            staged_html.write_text(status_html(updated, observation))
            staged_html.chmod(0o600)
            os.replace(staged_html, status_path)
        return updated, observation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--status-html", type=Path)
    args = parser.parse_args()
    state, observed = run(args.state, args.lock, status_path=args.status_html)
    print(json.dumps({"snapshot_at":observed and observed["snapshot_at"],
                      "age_s":observed and observed["age_s"],
                      "bad_probes":state["bad_probes"],
                      "alert_open":state["alert_open"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
