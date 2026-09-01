"""Office-owned board and webhook state stays private across every write path."""

import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest

CLIENT = pathlib.Path(__file__).resolve().parents[1] / "client"
sys.path.insert(0, str(CLIENT))

import board  # noqa: E402
import webhook  # noqa: E402


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class PrivateStateTests(unittest.TestCase):
  def test_board_migrates_existing_tree_and_secures_compose_reply_replaces(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    root = pathlib.Path(temp.name)
    account = root / "_meta" / "board" / "repo"
    account.mkdir(parents=True, mode=0o755)
    existing = account / ("2026-09-01T070000Z-" + "a" * 32 + ".json")
    existing.write_text(json.dumps({
        "id": "a" * 32, "ts": "2026-09-01T07:00:00Z", "account": "repo",
        "kind": "asking", "text": "may I", "gate_id": "",
    }))
    os.chmod(root / "_meta" / "board", 0o755)
    os.chmod(account, 0o755)
    os.chmod(existing, 0o644)
    os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
    old_umask = os.umask(0o022)
    try:
        self.assertEqual(board.read_feed()["state"], "ok")
        self.assertEqual(_mode(root / "_meta" / "board"), 0o700)
        self.assertEqual(_mode(account), 0o700)
        self.assertEqual(_mode(existing), 0o600)

        ok, made = board.compose("private note", repo="repo")
        self.assertTrue(ok)
        made_path = next(account.glob(f"*-{made['id']}.json"))
        self.assertEqual(_mode(made_path), 0o600)

        ok, _ = board.reply("a" * 32, "yes")
        self.assertTrue(ok)
        reply_dir = root / "_meta" / "board" / "_replies" / ("a" * 32)
        self.assertEqual(_mode(root / "_meta" / "board" / "_replies"), 0o700)
        self.assertEqual(_mode(reply_dir), 0o700)
        self.assertEqual(_mode(next(reply_dir.glob("*.json"))), 0o600)
    finally:
        os.umask(old_umask)
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)


  def test_webhook_migrates_and_secures_atomic_append_and_trim_paths(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    state_dir = pathlib.Path(temp.name) / "state"
    state_dir.mkdir(mode=0o755)
    for name in (webhook.SEEN_FILE, webhook.EVENTS_FILE, webhook.RUNS_FILE):
        path = state_dir / name
        path.write_text('{"ids": []}' if name == webhook.SEEN_FILE else "")
        os.chmod(path, 0o644)

    old_umask = os.umask(0o022)
    old_max, old_keep = webhook.EVENTS_MAX, webhook.EVENTS_KEEP
    try:
        box = webhook.Mailbox(state_dir)
        self.assertEqual(_mode(state_dir), 0o700)
        self.assertTrue(all(_mode(state_dir / name) == 0o600 for name in (
            webhook.SEEN_FILE, webhook.EVENTS_FILE, webhook.RUNS_FILE)))

        box.remember("delivery-1")
        box.append({"delivery": "delivery-1"})
        box.record_run({"delivery": "delivery-1", "rc": 0})
        webhook.EVENTS_MAX, webhook.EVENTS_KEEP = 1, 1
        box.append({"delivery": "delivery-2"})

        self.assertEqual(_mode(box.seen_path), 0o600)
        self.assertEqual(_mode(box.events_path), 0o600)
        self.assertEqual(_mode(box.runs_path), 0o600)
    finally:
        webhook.EVENTS_MAX, webhook.EVENTS_KEEP = old_max, old_keep
        os.umask(old_umask)

  def test_board_migration_refuses_a_symlinked_meta_ancestor(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    base = pathlib.Path(temp.name)
    root, outside = base / "runtime", base / "outside"
    root.mkdir()
    account = outside / "board" / "repo"
    account.mkdir(parents=True)
    post = account / ("2026-09-01T070000Z-" + "a" * 32 + ".json")
    post.write_text(json.dumps({"id": "a" * 32, "ts": "2026-09-01T07:00:00Z"}))
    os.chmod(outside / "board", 0o755)
    os.chmod(account, 0o755)
    os.chmod(post, 0o644)
    (root / "_meta").symlink_to(outside, target_is_directory=True)
    os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
    self.addCleanup(os.environ.pop, "OFFICE_RUNTIME_ROOT", None)

    report = board.read_feed()

    self.assertEqual(report["state"], "error")
    self.assertEqual(_mode(outside / "board"), 0o755)
    self.assertEqual(_mode(account), 0o755)
    self.assertEqual(_mode(post), 0o644)

  def test_webhook_refuses_a_symlinked_state_root(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    base = pathlib.Path(temp.name)
    outside = base / "outside"
    outside.mkdir(mode=0o755)
    link = base / "state"
    link.symlink_to(outside, target_is_directory=True)

    with self.assertRaises(OSError):
        webhook.Mailbox(link)
    self.assertEqual(_mode(outside), 0o755)


if __name__ == "__main__":
    unittest.main()
