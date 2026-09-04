"""Flights: one unit of execution, in a workspace of its own.

The runner wrapper is the only thing that decides what a script produced, and it
decides it from the exit code and the declared outputs. The child's free text
goes to `<workspace>/log` and is never the result: a script that prints "ERROR"
and exits 0 succeeded, and a script that prints nothing and exits 1 failed.
"""

from __future__ import annotations

import glob
import json
import os
import signal
import subprocess
import time

from . import landing, radio

RESULT_NAME = "result.json"
LOG_NAME = "log"
KILL_GRACE_S = 5.0
KILL_POLL_S = 0.02


class _Cancelled(Exception):
    pass


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
    proc = None
    cwd = workspace
    previous_term = signal.getsignal(signal.SIGTERM)

    def cancel(_signum, _frame):
        if proc is not None:
            _kill_owned_group(proc.pid)
        raise _Cancelled()

    signal.signal(signal.SIGTERM, cancel)
    try:
        if target is not None:
            cwd = landing.clone_hangar(target[0], target[1], workspace)
        with open(os.path.join(workspace, LOG_NAME), "ab") as log:
            try:
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, start_new_session=True)
                try:
                    code = proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    error = {"code": "timeout", "detail": f"over {timeout_s}s"}
                finally:
                    _kill_owned_group(proc.pid)
            except OSError as exc:
                error = {"code": "spawn_failed", "detail": str(exc)}
    except (landing.LandingError, subprocess.TimeoutExpired, OSError) as exc:
        error = {"code": "hangar_failed", "detail": str(exc)[:400]}
    except _Cancelled:
        code = None
        error = {"code": "cancelled", "detail": "operator"}
    finally:
        # A second cancellation while the small result-writing tail runs must not
        # interrupt the receipt that tells tower teardown completed.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    artifacts = []
    rel = os.path.relpath(cwd, workspace) if cwd != workspace else ""
    if outputs:
        # A declared output is a path or a glob, relative to where the flight ran.
        # Only declared outputs are artifacts and only artifacts land; anything
        # else the flight (or a hook inside the hangar) leaves behind is ignored.
        for name in declared_outputs(cwd, outputs):
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
        error = {"code": "exit_nonzero", "detail": f"exit {code}", "exit_code": code}
    if error is None and outputs:
        missing = [n for n in outputs if not declared_outputs(cwd, [n])]
        if missing:
            error = {"code": "missing_output", "detail": ",".join(missing)}

    result = {
        "ok": error is None,
        "artifacts": artifacts,
        "error": error,
        "cost": {"wall_s": round(time.time() - started, 3)},
    }
    if target is not None:
        result["hangar"] = rel
    try:
        write_result(workspace, result)
        # The result is on disk before the radio is touched: a hung radio can delay
        # this process by at most radio.timeout_s(), and cannot change what tower reads.
        radio.notify("flight.exiting", {"workspace": workspace, "ok": result["ok"]})
        return result
    finally:
        signal.signal(signal.SIGTERM, previous_term)


def declared_outputs(cwd: str, outputs):
    """Expand declared outputs (paths or globs) to the files that exist, in order."""
    found = []
    for pattern in outputs:
        matches = sorted(glob.glob(os.path.join(cwd, pattern)))
        for match in matches:
            if os.path.exists(match):
                name = os.path.relpath(match, cwd)
                if name not in found:
                    found.append(name)
    return found


def _kill_owned_group(group: int) -> None:
    """Kill the command group by its stable id, even after its leader exits."""
    try:
        os.killpg(group, signal.SIGKILL)
    except OSError:
        pass


def kill(pid, grace_s: float = KILL_GRACE_S) -> bool:
    """Ask the runner to tear down its owned command group; escalate truthfully."""
    if not pid:
        return True
    root = int(pid)
    try:
        os.kill(root, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + grace_s
    while alive(root) and time.monotonic() < deadline:
        time.sleep(KILL_POLL_S)
    if not alive(root):
        return True
    try:
        os.kill(root, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    # Forced runner death cannot confirm that its command-group cleanup ran.
    return False


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
