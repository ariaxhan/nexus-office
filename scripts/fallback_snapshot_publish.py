"""Deliver a bounded Studio Office world to the separate MacBook fallback."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.request import urlopen

MAX_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
HOST = "slowember@100.87.127.43"
REMOTE = "/Users/slowember/.local/share/nexus-office/fallback"
WORLD_URL = "http://127.0.0.1:8790/api/world"
GH = "/opt/homebrew/bin/gh"


def project_world(world: dict) -> dict:
    """Carry bounded display cards, never the full credential-bearing Office world."""
    sections = world.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("Office sections unavailable")
    cards = []
    for name, section in list(sections.items())[:32]:
        if not isinstance(section, dict):
            continue
        card = section.get("card")
        if not isinstance(card, dict):
            continue
        title = card.get("title")
        headline = card.get("headline")
        if not isinstance(title, str) or not isinstance(headline, str):
            continue
        cards.append({"repo": title[:100], "summary": headline[:220],
                      "state": str(section.get("state", ""))[:30]})
    if not cards:
        raise ValueError("Office display cards unavailable")
    return {"generated": world["generated"], "stations": cards}


def fetch_world(url: str, timeout: int) -> dict:
    with urlopen(url, timeout=timeout) as response:
        raw = response.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Office source exceeds 8 MiB")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("Office response shape invalid")
    world = envelope.get("world")
    if not isinstance(world, dict) or not isinstance(world.get("stations"), list):
        raise ValueError("Office world shape invalid")
    return world


def source_world(now: float | None = None) -> dict:
    """Read cached Office, then request one bounded refresh only if stale."""
    world = fetch_world(WORLD_URL, 20)
    def age_of(candidate: dict) -> float:
        stamp = candidate.get("generated")
        if not isinstance(stamp, str):
            raise ValueError("Office world timestamp missing")
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Office world timestamp timezone missing")
        return (time.time() if now is None else now) - parsed.timestamp()
    age = age_of(world)
    if age > 300:
        world = fetch_world(WORLD_URL + "?fresh=1", 50)
        age = age_of(world)
    if not -60 <= age <= 300:
        raise ValueError("Office world too old or in future")
    return world


def publish(signing_key: Path, verifier: Path) -> str:
    if signing_key.is_symlink() or not signing_key.is_file() or signing_key.stat().st_mode & 0o077:
        raise ValueError("private signing key must be a regular owner-only file")
    world = source_world()
    stamp = world.get("generated")
    data = json.dumps(project_world(world), separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("serialized world exceeds 2 MiB")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=yes", HOST]
    subprocess.run(ssh + ["install", "-d", "-m", "700", REMOTE],
                   check=True, timeout=20)
    with tempfile.TemporaryDirectory(prefix="office-fallback-") as tmp:
        name = "world-" + uuid.uuid4().hex + ".json"
        snapshot = Path(tmp) / name
        snapshot.write_bytes(data)
        subprocess.run(["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(signing_key),
                        "-n", "office-fallback", str(snapshot)],
                       check=True, stdout=subprocess.DEVNULL, timeout=20)
        sig = Path(str(snapshot) + ".sig")
        subprocess.run(["scp", "-B", "-o", "StrictHostKeyChecking=yes",
                        str(snapshot), str(sig), f"{HOST}:{REMOTE}/"],
                       check=True, timeout=30)
        result = subprocess.run(ssh + ["/usr/bin/python3", str(verifier),
                                      "--snapshot", REMOTE + "/" + name,
                                      "--signature", REMOTE + "/" + name + ".sig",
                                      "--allowed-signers", REMOTE + "/allowed_signers",
                                      "--destination", REMOTE + "/verified-world.json"],
                                check=True, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def _issue_comment(body: str) -> bool:
    try:
        result = subprocess.run([GH, "issue", "comment", "269", "-R",
                                 "ariaxhan/nexus-office", "--body", body],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=25, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def record_result(state_path: Path, *, stamp: str = "", error: str = "") -> None:
    """Escalate only two consecutive failures; acknowledge one recovery."""
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            state = {}
    except (FileNotFoundError, ValueError, OSError):
        state = {}
    failures = int(state.get("consecutive_failures", 0))
    alert_open = bool(state.get("alert_open", False))
    if error:
        failures += 1
        if failures >= 2 and not alert_open:
            alert_open = _issue_comment(
                "MacBook Office fallback publisher has failed at least twice consecutively. "
                f"Last failure class: {error}. The MacBook serves its last verified snapshot "
                "until the 15-minute freshness limit, then health returns 503. "
                "Check Studio com.aria.office-fallback-publish and its stderr log."
            )
    else:
        if alert_open:
            if _issue_comment("MacBook Office fallback publisher recovered: signed snapshot "
                              f"{stamp} was accepted by the MacBook after the prior alert."):
                alert_open = False
        failures = 0
    state = {"consecutive_failures": failures, "alert_open": alert_open,
             "last_error": error, "last_snapshot_at": stamp or state.get("last_snapshot_at", ""),
             "updated_at": datetime.now().astimezone().isoformat()}
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged = state_path.with_name(state_path.name + ".incoming")
    staged.write_text(json.dumps(state, separators=(",", ":")) + "\n")
    staged.chmod(0o600)
    os.replace(staged, state_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--remote-verifier", type=Path, required=True)
    args = parser.parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_path = args.lock.with_name("publisher-state.json")
        try:
            stamp = publish(args.signing_key, args.remote_verifier)
        except Exception as exc:
            reason = type(exc).__name__
            try:
                record_result(state_path, error=reason)
            except Exception as report_error:
                print(f"fallback alert recording failed: {type(report_error).__name__}", file=sys.stderr)
            raise
        try:
            record_result(state_path, stamp=stamp)
        except Exception as report_error:
            print(f"fallback recovery recording failed: {type(report_error).__name__}", file=sys.stderr)
        print(stamp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
