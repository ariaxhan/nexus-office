"""Radio: how a flight talks. Never how it lives or dies.

The one rule, from the spec: communications may fail; lifecycle must continue.
A flight calls `notify()` on its way out so whoever listens (the Office, a peer
flight, later hcom's replacement) hears about it. If nobody answers, or the
transport hangs forever, the flight still exits, its result is already on disk,
and tower still reads it. The transport is a command in `NEXUS_RADIO`; unset
means silence, which is a valid radio.

Bounded means bounded: the transport gets `RADIO_TIMEOUT_S` of wall clock, in
its own process group, and is SIGKILLed as a group when that runs out. There is
no retry and no watchdog around this; a slow radio is dropped, not waited for.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time

RADIO_TIMEOUT_S = 2.0
ENV_TRANSPORT = "NEXUS_RADIO"
ENV_TIMEOUT = "NEXUS_RADIO_TIMEOUT"


def transport() -> str | None:
    cmd = os.environ.get(ENV_TRANSPORT, "").strip()
    return cmd or None


def timeout_s() -> float:
    try:
        return max(0.1, float(os.environ.get(ENV_TIMEOUT, RADIO_TIMEOUT_S)))
    except ValueError:
        return RADIO_TIMEOUT_S


def notify(kind: str, payload: dict | None = None) -> dict:
    """Send one message, best effort. Returns what happened; never raises.

    Outcomes: `silent` (no transport), `sent`, `spawn_failed`, `exit_nonzero`,
    `timeout` (the transport was killed). The caller may record the outcome and
    must not branch its own lifecycle on it.
    """
    cmd = transport()
    if cmd is None:
        return {"outcome": "silent"}
    body = json.dumps({"kind": kind, "at": time.time(), **(payload or {})}, sort_keys=True)
    limit = timeout_s()
    try:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", cmd], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return {"outcome": "spawn_failed", "detail": str(exc)}
    try:
        proc.communicate(body.encode(), timeout=limit)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        try:
            proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return {"outcome": "timeout", "after_s": limit}
    except (BrokenPipeError, OSError):
        proc.wait()
    if proc.returncode == 0:
        return {"outcome": "sent"}
    return {"outcome": "exit_nonzero", "code": proc.returncode}


def _kill_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
