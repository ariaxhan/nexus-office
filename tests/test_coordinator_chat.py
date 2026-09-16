"""The coordinator conversation: inbox append/read-mark, stream parsing, links, missing state."""
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
import coordinator_chat as chat

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "coordinator-stream.jsonl"


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = pathlib.Path(temp.name).resolve()
        self.root = self.base / "thinking-brain-school"
        self.root.mkdir()
        for cache in (chat.REMOTES, chat.SHAS, chat.CHANGES):
            cache.clear()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class InboxTest(Fixture):
    def test_append_is_private_idempotent_and_read_marked(self):
        first = chat.say({"id": "req-00000001", "text": " ship #12 first "}, self.root)
        again = chat.say({"id": "req-00000001", "text": " ship #12 first "}, self.root)
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        path = self.root / chat.INBOX
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(len(path.read_text().splitlines()), 1)
        self.assertEqual([row["text"] for row in chat.unread(self.root)], ["ship #12 first"])
        chat.mark_read(["req-00000001"], "2026-09-15T00:00:00Z", self.root)
        self.assertEqual(chat.unread(self.root), [])
        self.assertIsNotNone(chat.inbox(self.root)[0]["read_at"])

    def test_concurrent_retries_land_once(self):
        threads = [threading.Thread(target=chat.say, args=({"id": "req-concurrent", "text": "x"}, self.root))
                   for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len((self.root / chat.INBOX).read_text().splitlines()), 1)

    def test_rejects_empty_oversized_and_idless(self):
        for body in ({"id": "req-00000001", "text": "  "}, {"id": "req-00000001", "text": "x" * 9000},
                     {"text": "hello"}, {"id": "../../etc", "text": "hello"}):
            with self.assertRaises(ValueError):
                chat.say(body, self.root)

    def test_corrupt_lines_are_skipped(self):
        self.write(chat.INBOX, 'not json\n{"id":"req-00000002","at":"2026-09-15T00:00:00Z","text":"ok"}\n[1]\n')
        self.assertEqual([row["id"] for row in chat.inbox(self.root)], ["req-00000002"])


class StreamTest(Fixture):
    def test_recorded_stream_renders_text_and_one_line_per_tool(self):
        events = chat.parse_log(FIXTURE.read_text())
        kinds = [event["kind"] for event in events]
        self.assertIn("tool", kinds)
        self.assertEqual(kinds, ["tool", "text"])
        self.assertIn("tbs-www#382", events[-1]["text"])
        self.assertTrue(all("\n" not in event["text"] for event in events if event["kind"] == "tool"))
        self.assertFalse(any("tool_result" in event["text"] for event in events))

    def test_plain_and_partial_logs(self):
        self.assertEqual(chat.parse_log(""), [])
        self.assertEqual(chat.parse_log("PASS tbs-claude: invoked\nold plain log\n")[0]["text"], "old plain log")
        partial = '{"type":"assistant","message":{"content":[{"type":"text","text":"half"}]}}\n{"type":"assis'
        self.assertEqual([event["text"] for event in chat.parse_log(partial)][-1], "half")

    def test_missing_state_reads_empty(self):
        data = chat.read(self.root)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["unread"], 0)

    def test_runs_pair_end_rows_holds_and_messages_in_order(self):
        log = ".tbs-out/coordinator-20260915T011118Z-live.log"
        self.write(log, FIXTURE.read_text())
        self.write(".tbs-out/coordinator-20260915T021118Z-live.log", "")
        self.write(chat.RUNS, json.dumps({"at": "2026-09-15T01:40:00Z", "event": "end", "start": "2026-09-15T01:11:17Z",
                                          "rc": 0, "secs": 1720, "prod_changes": 1, "holds": 1, "log": str(self.root / log)}) + "\n")
        self.write(chat.HOLDS, json.dumps({"run_start": "2026-09-15T01:11:17Z", "repo": "tbs-www", "issue": 9, "blocker": "b"}) + "\n")
        self.write(chat.INBOX, json.dumps({"id": "req-00000003", "at": "2026-09-15T01:30:00Z", "text": "hi"}) + "\n")
        with patch.object(chat, "_live", return_value=False):
            items = chat.read(self.root)["items"]
        self.assertEqual([item["kind"] for item in items], ["run", "message"])  # the empty, unfinished log is dropped
        self.assertEqual(items[0]["end"]["rc"], 0)
        self.assertEqual(len(items[0]["holds"]), 1)


class LinkTest(Fixture):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin", "git@github.com:ariaxhan/thinking-brain-school.git"], check=True)
        self.write("findings/2026-09-15-audit.md", "# audit\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "audit"], check=True, env=env)
        self.sha = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        www = self.base / "tbs-www"
        subprocess.run(["git", "init", "-q", str(www)], check=True)
        subprocess.run(["git", "-C", str(www), "remote", "add", "origin", "https://github.com/Thinking-Brain-School/tbs-www.git"], check=True)
        roots = {"r1": {"path": str(self.root)}}
        patcher = patch.object(chat.office_objects, "roots", return_value=roots)
        patcher.start()
        self.addCleanup(patcher.stop)

    def kinds(self, text):
        return [(part.get("kind"), part["text"]) for part in chat.Linker(self.root).segments(text) if part.get("kind")]

    def test_issue_refs(self):
        self.assertEqual(chat.Linker(self.root).segments("fix #12 now")[1]["repo"], chat.DEFAULT_REPO)
        part = chat.Linker(self.root).segments("tbs-www#382")[0]
        self.assertEqual((part["repo"], part["number"]), ("Thinking-Brain-School/tbs-www", 382))
        url = chat.Linker(self.root).segments("see https://github.com/ariaxhan/thinking-brain-school/issues/7.")[1]
        self.assertEqual((url["kind"], url["repo"], url["number"]), ("issue", "ariaxhan/thinking-brain-school", 7))
        self.assertEqual(self.kinds("C# and unknownrepo#4"), [])

    def test_files_shas_urls_and_text_is_preserved(self):
        short = next(self.sha[:n] for n in range(8, 41) if any(c in "abcdef" for c in self.sha[:n]))
        text = f"landed {short} in findings/2026-09-15-audit.md:3 and {self.root}/findings/2026-09-15-audit.md, e.g. missing/nope.md https://example.com/x"
        parts = chat.Linker(self.root).segments(text)
        self.assertEqual("".join(part["text"] for part in parts), text)
        kinds = [part.get("kind") for part in parts if part.get("kind")]
        self.assertEqual(kinds, ["sha", "file", "file", "url"])
        file = next(part for part in parts if part.get("kind") == "file")
        self.assertEqual(chat.office_objects.decode(file["id"]), ("r1", "findings/2026-09-15-audit.md"))

    def test_private_and_traversal_paths_never_link(self):
        self.write(".env", "SECRET=1")
        self.assertEqual(self.kinds(".env ../thinking-brain-school/findings/2026-09-15-audit.md"), [])

    def test_commit_view_links_its_files(self):
        data = chat.commit(self.sha[:10], "thinking-brain-school", self.root)
        self.assertEqual(data["sha"], self.sha)
        self.assertEqual(data["files"][0]["path"], "findings/2026-09-15-audit.md")
        self.assertIsNotNone(data["files"][0]["id"])
        with self.assertRaises(ValueError):
            chat.commit("HEAD;rm", "thinking-brain-school", self.root)
        with self.assertRaises(FileNotFoundError):
            chat.commit(self.sha, "nowhere", self.root)

    def test_changes_panel_reads_commits_publishes_and_issue_commands(self):
        self.write("_meta/receipts/publish/a/release.json", json.dumps({"outcome": "PASS", "finishedAt": "2090-01-01T00:00:01Z"}))
        found = chat.changes(self.root, "2000-01-01T00:00:00Z", "2090-01-02T00:00:00Z")
        self.assertEqual(found["commits"][0]["sha"], self.sha)
        self.assertEqual(found["publishes"][0]["outcome"], "PASS")
        issues = chat.issue_actions([{"kind": "tool", "text": "Bash: gh issue close 381 -R Thinking-Brain-School/tbs-www --comment done"},
                                     {"kind": "tool", "text": "Bash: gh issue create -R ariaxhan/thinking-brain-school --title x"}])
        self.assertEqual(issues, [{"action": "close", "repo": "Thinking-Brain-School/tbs-www", "number": 381},
                                  {"action": "create", "repo": "ariaxhan/thinking-brain-school", "number": None}])


if __name__ == "__main__":
    unittest.main()


def test_last_skip_is_dropped_once_a_run_starts_after_it(tmp_path):
    """A skip older than the newest start is not the current status."""
    ledger = tmp_path / "_meta/ledgers/coordinator-runs.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        '{"at":"2026-09-15T23:55:13Z","event":"skip","mode":"live","reason":"daily-cap"}\n'
        '{"at":"2026-09-16T01:34:07Z","event":"start","mode":"live"}\n'
    )
    assert chat.last_skip(tmp_path) is None

    ledger.write_text(
        '{"at":"2026-09-16T01:34:07Z","event":"start","mode":"live"}\n'
        '{"at":"2026-09-16T02:10:00Z","event":"skip","mode":"live","reason":"daily-cap"}\n'
    )
    assert chat.last_skip(tmp_path)["reason"] == "daily-cap"
