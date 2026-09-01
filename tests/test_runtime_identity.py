"""The runtime checker binds clean source, app, and door to one revision."""

import importlib.util
import pathlib
import plistlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "runtime_identity.py"
SPEC = importlib.util.spec_from_file_location("runtime_identity", SCRIPT)
runtime_identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_identity)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(root):
    origin, repo = root / "origin.git", root / "repo"
    _run(["git", "init", "--bare", str(origin)], root)
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    _run(["git", "config", "user.email", "test@example.invalid"], repo)
    for name in ("app", "client", "scripts"):
        (repo / name).mkdir()
    (repo / "client" / "serve.py").write_text("pass\n")
    (repo / "app" / "source.swift").write_text("// source\n")
    (repo / "scripts" / "whats-running.sh").write_text("#!/bin/sh\n")
    (repo / "package.json").write_text("{}\n")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "fixture"], repo)
    _run(["git", "branch", "-M", "main"], repo)
    _run(["git", "remote", "add", "origin", str(origin)], repo)
    _run(["git", "push", "-u", "origin", "main"], repo)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, revision


def _make_app(root, revision):
    app = root / "Office.app"
    executable = app / "Contents" / "MacOS" / "Office"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "Office", "NexusSourceRevision": revision}, handle)
    return app


class RuntimeIdentityTests(unittest.TestCase):
  def test_relevant_tracked_and_untracked_client_changes_are_reported(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    repo, revision = _make_repo(pathlib.Path(temp.name))
    (repo / "client" / "serve.py").write_text("changed\n")
    (repo / "client" / "new.py").write_text("untracked\n")

    report = runtime_identity.source_report(repo)

    self.assertEqual(report["revision"], revision)
    self.assertEqual(report["dirty"], ["client/new.py", "client/serve.py"])
    self.assertFalse(report["ok"])


  def test_clean_source_app_and_door_must_share_the_exact_revision(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    root = pathlib.Path(temp.name)
    repo, revision = _make_repo(root)
    app = _make_app(root, revision)
    door = {
        "pid": 42,
        "executable": "/usr/bin/python3",
        "server": str((repo / "client" / "serve.py").resolve()),
        "revision": revision,
        "listen": "127.0.0.1:8790",
    }

    report = runtime_identity.audit(root=repo, app=app, door=door, copies=[])

    self.assertTrue(report["ok"])
    self.assertEqual(report["revision"], revision)


  def test_unknown_or_mismatched_artifact_identity_fails_closed(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    root = pathlib.Path(temp.name)
    repo, revision = _make_repo(root)
    app = _make_app(root, "unknown")
    door = {
        "pid": 42,
        "executable": "/usr/bin/python3",
        "server": str((repo / "client" / "serve.py").resolve()),
        "revision": "f" * 40,
        "listen": "127.0.0.1:8790",
    }

    report = runtime_identity.audit(root=repo, app=app, door=door, copies=[])

    self.assertFalse(report["ok"])
    self.assertFalse(report["app"]["ok"])
    self.assertFalse(report["door"]["ok"])


if __name__ == "__main__":
    unittest.main()
