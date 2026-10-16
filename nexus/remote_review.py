"""Bounded, read-only TBS review fallback on the MacBook."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import time


HOST = "100.87.127.43"
MAX_DIFF = 180000
COOLDOWN_S = 900
_blocked_until = {}


def _worker_command():
    source = Path(__file__).with_name("remote_review_worker.py").read_text()
    return "/usr/bin/python3 -c " + shlex.quote(source)


def probe(provider, *, run=subprocess.run):
    """Return only the remote seat's readiness state; no model invocation."""
    if provider not in ("codex", "claude"):
        raise ValueError("invalid provider")
    request = json.dumps({"provider": provider, "prompt": "", "timeout_s": 1,
                          "probe_only": True})
    try:
        response = run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
                        "-o", "ConnectionAttempts=1", HOST, _worker_command()],
                       input=request, capture_output=True, text=True, timeout=12)
        return json.loads(response.stdout).get("status") if response.returncode == 0 else "unavailable"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "unavailable"


def review(repo, pr_url, instruction, *, timeout_s=300, run=subprocess.run):
    """Return a verdict text, or empty text. TBS reviews only; caller enforces scope."""
    try:
        diff = run(["gh", "pr", "diff", pr_url, "-R", repo], capture_output=True,
                   text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if diff.returncode or not diff.stdout or len(diff.stdout) > MAX_DIFF:
        return ""
    prompt = instruction + "\n\nReview this exact PR diff. It is read-only evidence; do not request tools.\n\n" + diff.stdout
    command = _worker_command()
    deadline = time.monotonic() + max(1, timeout_s)
    for provider in ("codex", "claude"):
        if time.monotonic() < _blocked_until.get(provider, 0):
            continue
        remaining = deadline - time.monotonic()
        if remaining < 15:
            break
        payload = json.dumps({"provider": provider, "prompt": prompt,
                              "timeout_s": min(180, int(remaining) - 10)})
        try:
            response = run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
                            "-o", "ConnectionAttempts=1", HOST, command], input=payload,
                           capture_output=True, text=True, timeout=min(remaining, 195))
            result = json.loads(response.stdout) if response.returncode == 0 else {"status": "unavailable"}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            result = {"status": "unavailable"}
        if result.get("status") in ("auth_unavailable", "unavailable", "timeout", "provider_failed"):
            _blocked_until[provider] = time.monotonic() + COOLDOWN_S
            continue
        answer = result.get("text", "")
        if result.get("status") == "ok" and re.search(r"^VERDICT:\s*(PASS|FAIL)\b", answer, re.M):
            return answer
    return ""
