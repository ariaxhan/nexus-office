"""Flights: one unit of execution, in a workspace of its own.

The runner wrapper is the only thing that decides what a script produced, and it
decides it from the exit code and the declared outputs. The child's free text
goes to `<workspace>/log` and is never the result: a script that prints "ERROR"
and exits 0 succeeded, and a script that prints nothing and exits 1 failed.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time

from . import landing, radio

RESULT_NAME = "result.json"
LOG_NAME = "log"


def workspace_path(root: str, flight_id: str) -> str:
    return os.path.join(root, flight_id)


def result_path(workspace: str) -> str:
    return os.path.join(workspace, RESULT_NAME)


def write_result(workspace: str, result: dict) -> None:
    """Atomically, so a reader never sees half a result even mid-crash."""
    final = result_path(workspace)
    tmp = final + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(result, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, final)


def read_result(workspace: str):
    """Return (result, error_code). error_code is None when the result is usable."""
    path = result_path(workspace)
    if not os.path.exists(path):
        return None, "missing_result"
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (ValueError, OSError):
        return None, "malformed_result"
    if not isinstance(data, dict) or "ok" not in data:
        return None, "malformed_result"
    data.setdefault("artifacts", [])
    data.setdefault("error", None)
    data.setdefault("cost", {})
    if not isinstance(data.get("artifacts"), list):
        return None, "malformed_result"
    return data, None


def run_script(workspace: str, cmd: str, timeout_s: float = 600, outputs=None,
               target=None) -> dict:
    """Run one script flight to completion here, and write its result.

    Called in the detached child (`python3 -m nexus flight-run`), never in tower.
    With a `target` (repo, branch) the script runs inside a hangar clone under
    the workspace and its outputs are paths in that clone; landing commits them.
    """
    outputs = list(outputs or [])
    os.makedirs(workspace, exist_ok=True)
    started = time.time()
    code = None
    error = None
    cwd = workspace
    if target is not None:
        try:
            cwd = landing.clone_hangar(target[0], target[1], workspace)
        except (landing.LandingError, subprocess.TimeoutExpired, OSError) as exc:
            error = {"code": "hangar_failed", "detail": str(exc)[:400]}
    with open(os.path.join(workspace, LOG_NAME), "ab") as log:
        try:
            if error is not None:
                raise OSError(error["detail"])
            proc = subprocess.Popen(
                ["/bin/sh", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True)
        except OSError as exc:
            if error is None:
                error = {"code": "spawn_failed", "detail": str(exc)}
            proc = None
        if proc is not None:
            try:
                code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid)
                code = None
                error = {"code": "timeout", "detail": f"over {timeout_s}s"}

    artifacts = []
    rel = os.path.relpath(cwd, workspace) if cwd != workspace else ""
    if outputs:
        for name in outputs:
            if os.path.exists(os.path.join(cwd, name)):
                artifacts.append({"kind": "file", "ref": os.path.join(rel, name)})
    elif os.path.isdir(cwd):
        # Nothing declared: whatever the flight left behind IS the artifact set.
        # A plan that declares outputs gets them checked; one that does not still
        # gets what it made recorded, because an unrecorded artifact is a lie.
        for name in sorted(os.listdir(cwd)):
            if name in (LOG_NAME, RESULT_NAME, ".git") or name.endswith(".tmp"):
                continue
            artifacts.append({"kind": "file", "ref": os.path.join(rel, name)})
    if error is None and code != 0:
        error = {"code": "exit_nonzero", "detail": f"exit {code}"}
    if error is None and outputs and len(artifacts) != len(outputs):
        missing = [n for n in outputs if not os.path.exists(os.path.join(cwd, n))]
        error = {"code": "missing_output", "detail": ",".join(missing)}

    result = {
        "ok": error is None,
        "artifacts": artifacts,
        "error": error,
        "cost": {"wall_s": round(time.time() - started, 3)},
    }
    if target is not None:
        result["hangar"] = rel
    write_result(workspace, result)
    # The result is on disk before the radio is touched: a hung radio can delay
    # this process by at most radio.timeout_s(), and cannot change what tower reads.
    radio.notify("flight.exiting", {"workspace": workspace, "ok": result["ok"]})
    return result


def _kill_group(pid: int, sig=signal.SIGKILL) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def kill(pid, sig=signal.SIGKILL) -> None:
    if pid:
        _kill_group(int(pid), sig)


def alive(pid) -> bool:
    """Is this pid a process we can still signal? A dead pid is a fact, not a guess."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
