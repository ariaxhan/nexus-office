"""Verify a Studio-signed Office snapshot before making it visible to fallback."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

MAX_BYTES = 2 * 1024 * 1024


def verify(snapshot: Path, signature: Path, allowed_signers: Path, destination: Path,
           *, now: float | None = None) -> str:
    if any(path.is_symlink() for path in (snapshot, signature, allowed_signers, destination)):
        raise ValueError("symlink refused")
    if not snapshot.is_file() or snapshot.stat().st_size > MAX_BYTES:
        raise ValueError("snapshot missing or too large")
    if not signature.is_file() or signature.stat().st_size > 8192:
        raise ValueError("signature missing or too large")
    raw = snapshot.read_bytes()
    result = subprocess.run(
        ["/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(allowed_signers),
         "-I", "office-fallback", "-n", "office-fallback", "-s", str(signature)],
        input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=10,
    )
    if result.returncode:
        raise ValueError("snapshot signature invalid")
    world = json.loads(raw)
    if not isinstance(world, dict) or not isinstance(world.get("stations"), list):
        raise ValueError("snapshot shape invalid")
    stamp = world.get("generated")
    if not isinstance(stamp, str):
        raise ValueError("snapshot timestamp missing")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("snapshot timestamp timezone missing")
    age = (time.time() if now is None else now) - parsed.timestamp()
    if age < -60 or age > 600:
        raise ValueError("snapshot outside 10 minute freshness window")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = destination.with_name(destination.name + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if destination.exists():
            previous = json.loads(destination.read_bytes())
            old_stamp = previous.get("generated")
            if not isinstance(old_stamp, str):
                raise ValueError("existing snapshot timestamp invalid")
            old = datetime.fromisoformat(old_stamp.replace("Z", "+00:00"))
            if parsed <= old:
                raise ValueError("snapshot replay or rollback")
        staged = destination.with_name(destination.name + ".incoming")
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, destination)
        finally:
            if staged.exists():
                staged.unlink()
    return stamp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--allowed-signers", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(verify(args.snapshot, args.signature, args.allowed_signers, args.destination))
    finally:
        # The inbox is transient. Keep only the atomically installed snapshot.
        args.snapshot.unlink(missing_ok=True)
        args.signature.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
