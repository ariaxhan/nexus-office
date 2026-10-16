"""Deliver a bounded Studio Office world to the separate MacBook fallback."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from urllib.request import urlopen

MAX_BYTES = 2 * 1024 * 1024
HOST = "slowember@100.87.127.43"
REMOTE = "/Users/slowember/.local/share/nexus-office/fallback"


def publish(signing_key: Path, verifier: Path) -> str:
    if signing_key.is_symlink() or not signing_key.is_file() or signing_key.stat().st_mode & 0o077:
        raise ValueError("private signing key must be a regular owner-only file")
    with urlopen("http://127.0.0.1:8790/api/world", timeout=20) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("snapshot exceeds 2 MiB")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("Office response shape invalid")
    world = envelope.get("world")
    if not isinstance(world, dict) or not isinstance(world.get("stations"), list):
        raise ValueError("Office world shape invalid")
    stamp = world.get("generated")
    if not isinstance(stamp, str):
        raise ValueError("Office world timestamp missing")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or not -60 <= time.time() - parsed.timestamp() <= 300:
        raise ValueError("Office world too old or in future")
    data = json.dumps(world, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--remote-verifier", type=Path, required=True)
    args = parser.parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        print(publish(args.signing_key, args.remote_verifier))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
