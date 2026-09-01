#!/usr/bin/env python3
"""Read-only source/app/door identity proof. Unknown is failure."""

from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import shlex
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = pathlib.Path("/Applications/Office.app")
RELEVANT = ("app", "client", "scripts", "package.json")
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
LSREGISTER = pathlib.Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


def _run(args, *, cwd=None, timeout=15):
    try:
        result = subprocess.run(
            [str(x) for x in args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


def _git(root, *args):
    return _run(("git", "-C", root, *args))


def _revision(value) -> str:
    value = str(value or "").strip().lower()
    return value if REVISION_RE.fullmatch(value) else ""


def source_report(root=ROOT) -> dict:
    root = pathlib.Path(root).resolve()
    rc, out, _ = _git(root, "rev-parse", "--verify", "HEAD")
    revision = _revision(out) if rc == 0 else ""
    rc, branch_out, _ = _git(root, "symbolic-ref", "--short", "HEAD")
    branch = branch_out.strip() if rc == 0 else ""
    rc, out, _ = _git(root, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
    remote = _revision(out.split()[0]) if rc == 0 and out.split() else ""
    rc1, changed, _ = _git(root, "diff", "--name-only", "-z", "HEAD", "--", *RELEVANT)
    rc2, untracked, _ = _git(
        root, "ls-files", "--others", "--exclude-standard", "-z", "--", *RELEVANT
    )
    dirty = sorted(set(filter(None, (changed + untracked).split("\0"))))
    issues = []
    if not revision:
        issues.append("source revision unknown")
    if not branch or not remote:
        issues.append("remote default-branch revision unknown")
    elif revision and remote != revision:
        issues.append("HEAD does not equal the remote branch")
    if rc1 or rc2:
        issues.append("relevant source cleanliness unknown")
    elif dirty:
        issues.append("relevant tracked or untracked source is dirty")
    return {"ok": not issues, "revision": revision, "remote": remote, "branch": branch,
            "dirty": dirty, "issues": issues, "root": str(root)}


def app_report(app, expected: str) -> dict:
    app = pathlib.Path(app)
    plist = app / "Contents" / "Info.plist"
    issues = []
    revision = ""
    executable = ""
    try:
        with plist.open("rb") as handle:
            info = plistlib.load(handle)
        revision = _revision(info.get("NexusSourceRevision"))
        name = str(info.get("CFBundleExecutable") or "")
        candidate = (app / "Contents" / "MacOS" / name).resolve()
        app_root = app.resolve()
        if not name or not candidate.is_file() or app_root not in candidate.parents:
            issues.append("app executable path is unknown or outside the bundle")
        else:
            executable = str(candidate)
    except (OSError, ValueError, plistlib.InvalidFileException):
        issues.append("installed app or Info.plist is unreadable")
    if not revision:
        issues.append("app revision unknown")
    elif revision != expected:
        issues.append("app revision does not equal source revision")
    return {"ok": not issues, "revision": revision, "executable": executable,
            "path": str(app), "issues": issues}


def _door_report(root, expected: str, door: dict) -> dict:
    expected_server = (pathlib.Path(root) / "client" / "serve.py").resolve()
    revision = _revision(door.get("revision"))
    server = pathlib.Path(str(door.get("server") or ""))
    executable = pathlib.Path(str(door.get("executable") or ""))
    listen = str(door.get("listen") or "")
    issues = []
    try:
        server = server.resolve()
    except OSError:
        pass
    if server != expected_server:
        issues.append("listener is not running this checkout's client/serve.py")
    if not executable.is_absolute() or not executable.is_file():
        issues.append("server executable path is unknown")
    if not (listen.startswith("127.0.0.1:") or listen.startswith("[::1]:")):
        issues.append("listener address is unknown or not loopback")
    if not revision:
        issues.append("server revision unknown")
    elif revision != expected:
        issues.append("server revision does not equal source revision")
    return {"ok": not issues, "revision": revision, "server": str(server),
            "executable": str(executable), "listen": listen,
            "pid": door.get("pid"), "issues": issues}


def probe_door(root=ROOT, port=8790) -> dict:
    rc, out, _ = _run(("lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fp", "-Fn"))
    if rc or not out.strip():
        return {}
    pid, listen = "", ""
    for line in out.splitlines():
        if line.startswith("p") and not pid:
            pid = line[1:].strip()
        elif line.startswith("n") and not listen:
            listen = line[1:].strip()
    if not pid.isdigit():
        return {}
    rc, command, _ = _run(("ps", "-p", pid, "-o", "command="))
    if rc:
        return {"pid": int(pid), "listen": listen}
    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        tokens = []
    executable = tokens[0] if tokens else ""
    expected_name = str((pathlib.Path(root) / "client" / "serve.py").resolve())
    server = ""
    for token in tokens[1:]:
        try:
            if str(pathlib.Path(token).expanduser().resolve()) == expected_name:
                server = expected_name
                break
        except OSError:
            continue
    revision = ""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/api/health", timeout=3) as response:
            payload = json.loads(response.read())
        revision = str(payload.get("revision") or "") if isinstance(payload, dict) else ""
    except Exception:
        pass
    return {"pid": int(pid), "listen": listen, "executable": executable,
            "server": server, "revision": revision}


def probe_copies(app=APP):
    rc, out, _ = _run((LSREGISTER, "-dump"), timeout=30)
    if rc:
        return None
    found = sorted(set(re.findall(r"/[^\n ]*Office\.app", out)))
    expected = str(pathlib.Path(app))
    return [path for path in found if path != expected]


def audit(*, root=ROOT, app=APP, door=None, copies=None) -> dict:
    source = source_report(root)
    expected = source["revision"]
    app_state = app_report(app, expected)
    actual_door = probe_door(root, int(os.environ.get("OFFICE_PORT", "8790"))) if door is None else door
    door_state = _door_report(root, expected, actual_door)
    actual_copies = probe_copies(app) if copies is None else list(copies)
    copy_issues = (["LaunchServices copy census unknown"] if actual_copies is None
                   else (["additional registered Office copies exist"] if actual_copies else []))
    copy_state = {"ok": not copy_issues, "paths": actual_copies or [], "issues": copy_issues}
    ok = source["ok"] and app_state["ok"] and door_state["ok"] and copy_state["ok"]
    return {"ok": ok, "revision": expected, "source": source, "app": app_state,
            "door": door_state, "copies": copy_state}


def main() -> int:
    report = audit()
    print("immutable revision")
    print("  %s %s" % ("✓" if report["revision"] else "✗", report["revision"] or "unknown"))
    for name in ("source", "app", "door", "copies"):
        row = report[name]
        print(name)
        if row["ok"]:
            print("  ✓ identity verified")
        else:
            for issue in row["issues"]:
                print("  ✗ " + issue)
        for path in row.get("dirty", ()):
            print("      dirty: " + path)
        for path in row.get("paths", ()):
            print("      copy: " + path)
    print("everything running is current." if report["ok"] else
          "identity mismatch or unknown. Do not trust runtime symptoms.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
