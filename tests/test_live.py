"""The read-only window on every agent running on this machine.

Two things here can be wrong in a way a person would never catch by looking at
the page. The roster could quietly drop a session, which turns "what is running"
into "what we managed to parse" and makes the count on the page disagree with
the count in a terminal. And a failure to ask the machine could be drawn as an
empty list, which is the same lie in a different shape.

So the machine is a stub in every test here: `live._run` is the one seam every
subprocess goes through, and replacing it means the real reading code is tested
against a machine the test made up, including the machines that answer badly.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

import live  # noqa: E402


def claude_record(kind, **rest):
    body = {"type": kind, "timestamp": "2026-09-01T20:00:00.000Z"}
    body.update(rest)
    return body


def codex_record(payload, kind="response_item"):
    return {"type": kind, "timestamp": "2026-09-01T20:00:00.000Z", "payload": payload}


class SlugTest(unittest.TestCase):
    """The one fact this module holds about another program's file layout."""

    def test_every_slash_and_dot_becomes_a_dash(self):
        self.assertEqual(live.claude_slug("/Users/a/Developer/Vaults"),
                         "-Users-a-Developer-Vaults")
        self.assertEqual(live.claude_slug("/tmp/dispatch.83156.x4MbQp/wt/kernel.200"),
                         "-tmp-dispatch-83156-x4MbQp-wt-kernel-200")

    def test_a_worktree_path_is_looked_for_under_both_of_its_names(self):
        """Measured, not guessed: a dispatch worktree has a fully dashed slug dir
        AND one that kept its dots sitting side by side on this machine, and only
        one of them is the one being written to."""
        dirs = live.claude_dirs("/private/var/folders/T/dispatch.10960.d8/wt/rs.10960.1")
        self.assertIn("-private-var-folders-T-dispatch-10960-d8-wt-rs-10960-1", dirs)
        self.assertIn("-private-var-folders-T-dispatch.10960.d8-wt-rs.10960.1", dirs)

    def test_the_private_prefix_macos_adds_is_tried_without_it(self):
        dirs = live.claude_dirs("/private/var/folders/T/x")
        self.assertIn("-var-folders-T-x", dirs)

    def test_nothing_is_a_directory_name_for_nothing(self):
        self.assertEqual(live.claude_dirs(""), [])


class ClaudeParserTest(unittest.TestCase):
    def test_a_plain_user_message_is_one_user_line(self):
        rows = live.parse_claude_line(claude_record(
            "user", message={"role": "user", "content": "fix the door"}))
        self.assertEqual([(r["who"], r["text"]) for r in rows], [("user", "fix the door")])

    def test_an_assistant_turn_comes_apart_into_what_it_thought_said_and_ran(self):
        rows = live.parse_claude_line(claude_record("assistant", message={
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "the gate is pending"},
                {"type": "text", "text": "looking now"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]}))
        self.assertEqual([(r["who"], r["kind"]) for r in rows],
                         [("agent", "thinking"), ("agent", "text"), ("tool", "tool")])
        # The thinking is kept and tagged rather than dropped: a reader that hides
        # it is a reader that cannot explain why the agent did the next thing.
        self.assertEqual(rows[0]["text"], "the gate is pending")
        self.assertIn('{"command":"ls"}', rows[2]["text"])

    def test_a_tool_result_arrives_as_its_own_kind_and_not_as_the_person_talking(self):
        rows = live.parse_claude_line(claude_record("user", message={
            "role": "user",
            "content": [{"type": "tool_result", "content": [{"type": "text", "text": "ok"}]}]}))
        self.assertEqual([(r["who"], r["kind"], r["text"]) for r in rows],
                         [("result", "result", "ok")])

    def test_the_records_that_are_not_conversation_produce_nothing(self):
        for kind in ("ai-title", "attachment", "mode", "file-history-snapshot",
                     "queue-operation", "last-prompt"):
            self.assertEqual(live.parse_claude_line(claude_record(kind)), [], kind)
        self.assertEqual(live.parse_claude_line("not a record"), [])

    def test_a_very_long_line_is_clipped_and_says_so(self):
        rows = live.parse_claude_line(claude_record(
            "user", message={"role": "user", "content": "x" * (live.MAX_TEXT + 50)}))
        self.assertTrue(rows[0]["truncated"])
        self.assertEqual(len(rows[0]["text"]), live.MAX_TEXT)


class CodexParserTest(unittest.TestCase):
    def test_the_three_roles_land_where_a_reader_expects_them(self):
        for role, who in (("user", "user"), ("assistant", "agent"), ("developer", "system")):
            rows = live.parse_codex_line(codex_record({
                "type": "message", "role": role,
                "content": [{"type": "input_text", "text": "hello"}]}))
            self.assertEqual([(r["who"], r["text"]) for r in rows], [(who, "hello")], role)

    def test_a_tool_call_and_its_output_are_a_call_and_a_result(self):
        call = live.parse_codex_line(codex_record({
            "type": "custom_tool_call", "name": "exec", "input": "ls -la"}))
        self.assertEqual((call[0]["who"], call[0]["kind"]), ("tool", "tool"))
        self.assertEqual(call[0]["text"], "exec ls -la")
        out = live.parse_codex_line(codex_record({
            "type": "custom_tool_call_output",
            "output": [{"type": "input_text", "text": "Script completed"}]}))
        self.assertEqual((out[0]["who"], out[0]["text"]), ("result", "Script completed"))

    def test_encrypted_reasoning_and_the_bookkeeping_produce_nothing(self):
        self.assertEqual(live.parse_codex_line(codex_record(
            {"type": "reasoning", "encrypted_content": "gAAAA"})), [])
        self.assertEqual(live.parse_codex_line(codex_record({"cwd": "/x"}, kind="turn_context")), [])
        self.assertEqual(live.parse_codex_line(codex_record(
            {"type": "task_complete", "last_agent_message": "done"}, kind="event_msg")), [])


class Machine:
    """A machine that answers `pgrep`, `lsof` and `ps` however the test says."""

    def __init__(self, procs, fail=""):
        self.procs = procs  # {engine: {pid: (cwd, etime)}}
        self.fail = fail
        self.calls = []

    def __call__(self, args, timeout=live.PROBE_TIMEOUT_S):
        self.calls.append(list(args))
        if args[0] == "pgrep":
            engine = args[-1]
            if self.fail == engine:
                return 2, "", "pgrep: cannot access the process table"
            found = self.procs.get(engine) or {}
            return (0 if found else 1), "\n".join(str(p) for p in found) + "\n", ""
        if args[0] == "lsof":
            pid = int(args[args.index("-p") + 1])
            for found in self.procs.values():
                if pid in found:
                    return 0, f"p{pid}\nfcwd\nn{found[pid][0]}\n", ""
            return 1, "", ""
        if args[0] == "ps":
            out = []
            for found in self.procs.values():
                for pid, (_, etime) in found.items():
                    out.append(f"{pid:>6} {etime}")
            return 0, "\n".join(out) + "\n", ""
        return 127, "", "no such probe"


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.projects = root / "projects"
        self.codex = root / "codex"
        self.projects.mkdir()
        self.codex.mkdir()
        for name, was in (("CLAUDE_PROJECTS", live.CLAUDE_PROJECTS),
                          ("CODEX_SESSIONS", live.CODEX_SESSIONS)):
            self.addCleanup(lambda n=name, w=was: setattr(live, n, w))
        live.CLAUDE_PROJECTS = self.projects
        live.CODEX_SESSIONS = self.codex
        was_run = live._run
        self.addCleanup(lambda: setattr(live, "_run", was_run))
        # Module level caches outlive a test the way they outlive a poll.
        self.addCleanup(self.reset)
        self.reset()

    def reset(self):
        live._joins.clear()
        live._joins.update({"at": 0.0, "cwd": {}, "tx": {}})
        live._parsed.update({"key": None, "lines": []})

    def machine(self, procs, fail=""):
        stub = Machine(procs, fail)
        live._run = stub
        return stub

    def claude_transcript(self, cwd, lines):
        folder = self.projects / live.claude_slug(cwd)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "abcd.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        return path

    def codex_transcript(self, cwd, lines, sub=False):
        day = time.strftime("%Y/%m/%d")
        folder = self.codex / day
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"type": "session_meta", "timestamp": "2026-09-01T20:00:00Z",
                "payload": {"cwd": cwd, "session_id": "s1", "cli_version": "0.1"}}
        if sub:
            meta["payload"]["source"] = {"subagent": {"thread_spawn": {"depth": 1}}}
        name = f"rollout-{'sub' if sub else 'top'}.jsonl"
        path = folder / name
        path.write_text("\n".join(json.dumps(line) for line in [meta] + lines) + "\n")
        return path

    # ── the roster ──────────────────────────────────────────────────────────
    def test_every_process_becomes_a_row_with_its_transcript(self):
        self.claude_transcript("/repos/thing", [
            claude_record("user", message={"role": "user", "content": "start here"}),
            claude_record("assistant", message={
                "role": "assistant", "content": [{"type": "text", "text": "on it"}]}),
        ])
        self.codex_transcript("/repos/other", [codex_record({
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}]})])
        self.machine({"claude": {101: ("/repos/thing", "02:30")},
                      "codex": {202: ("/repos/other", "01-02:00:00")}})

        got = live.read()
        self.assertEqual(got["state"], "ok")
        self.assertEqual(sorted(r["key"] for r in got["sessions"]),
                         ["claude-101", "codex-202"])
        # The one that moved most recently is first: the codex rollout above was
        # written after the claude jsonl.
        self.assertEqual(got["sessions"][0]["key"], "codex-202")
        first = [r for r in got["sessions"] if r["key"] == "claude-101"][0]
        self.assertEqual(first["engine"], "claude")
        self.assertEqual(first["cwd"], "/repos/thing")
        self.assertEqual(first["title"], "start here")
        self.assertEqual(first["last_line"], "on it")
        self.assertEqual(first["turns"], 1)
        self.assertEqual(first["state"], "working")
        self.assertTrue(first["started"])
        self.assertTrue(first["transcript"])

    def test_a_process_with_no_transcript_is_still_a_row(self):
        """The count on this page has to be the count a terminal would give. A
        session whose transcript is somewhere this module cannot find is state
        `unknown`, never a row that quietly went missing."""
        self.machine({"claude": {101: ("/nowhere", "00:10")}, "codex": {}})
        got = live.read()
        self.assertEqual(len(got["sessions"]), 1)
        self.assertEqual(got["sessions"][0]["state"], "unknown")
        self.assertEqual(got["sessions"][0]["transcript"], "")
        self.assertEqual(got["working"], 0)

    def test_a_quiet_session_is_idle_and_not_working(self):
        path = self.claude_transcript("/repos/thing", [claude_record(
            "user", message={"role": "user", "content": "hello"})])
        old = time.time() - (live.WORKING_S + 600)
        import os
        os.utime(path, (old, old))
        self.machine({"claude": {101: ("/repos/thing", "10:00")}, "codex": {}})
        self.assertEqual(live.read()["sessions"][0]["state"], "idle")

    def test_a_codex_subagents_rollout_never_stands_in_for_the_session(self):
        """The subagent runs inside the same process, so its thread has the same
        cwd and a newer mtime. Showing it would answer "what is this agent
        doing" with somebody else's transcript."""
        self.codex_transcript("/repos/other", [codex_record({
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "the real ask"}]})])
        time.sleep(0.01)
        self.codex_transcript("/repos/other", [codex_record({
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "a lane's errand"}]})], sub=True)
        self.machine({"claude": {}, "codex": {202: ("/repos/other", "05:00")}})
        row = live.read()["sessions"][0]
        self.assertTrue(row["transcript"].endswith("rollout-top.jsonl"))
        self.assertEqual(row["title"], "the real ask")

    def test_a_session_titled_by_its_own_preamble_is_titled_by_the_ask_instead(self):
        self.codex_transcript("/repos/other", [
            codex_record({"type": "message", "role": "user",
                          "content": [{"type": "input_text",
                                       "text": "# AGENTS.md instructions for /repos"}]}),
            codex_record({"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": "audit the pipeline"}]}),
        ])
        self.machine({"claude": {}, "codex": {202: ("/repos/other", "05:00")}})
        self.assertEqual(live.read()["sessions"][0]["title"], "audit the pipeline")

    def test_nothing_running_is_empty_and_says_it_asked(self):
        self.machine({"claude": {}, "codex": {}})
        got = live.read()
        self.assertEqual(got["state"], "empty")
        self.assertEqual(got["sessions"], [])

    def test_a_machine_that_could_not_be_asked_is_never_drawn_as_a_quiet_one(self):
        stub = self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}},
                            fail="codex")
        got = live.read()
        self.assertEqual(got["state"], "unreadable")
        self.assertEqual(got["sessions"], [])
        self.assertIn("process table", got["detail"])
        # And it did not go on to walk the disk after the machine failed.
        self.assertTrue(all(call[0] == "pgrep" for call in stub.calls))

    def test_the_join_is_cached_so_a_poll_is_not_three_subprocesses_a_session(self):
        self.claude_transcript("/repos/thing", [claude_record(
            "user", message={"role": "user", "content": "hello"})])
        stub = self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})
        live.read()
        lsofs = sum(1 for call in stub.calls if call[0] == "lsof")
        live.read()
        self.assertEqual(sum(1 for call in stub.calls if call[0] == "lsof"), lsofs)

    def test_every_probe_is_capped(self):
        stub = self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})
        live.read()
        self.assertTrue(stub.calls)
        self.assertLessEqual(live.PROBE_TIMEOUT_S, 2.0)

    # ── one transcript ──────────────────────────────────────────────────────
    def test_the_whole_transcript_comes_back_a_page_at_a_time(self):
        lines = []
        for n in range(50):
            lines.append(claude_record("user", message={"role": "user", "content": f"ask {n}"}))
            lines.append(claude_record("assistant", message={
                "role": "assistant", "content": [{"type": "text", "text": f"answer {n}"}]}))
        self.claude_transcript("/repos/thing", lines)
        self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})

        code, body = live.transcript("claude-101", 0, 10)
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 100)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(len(body["lines"]), 10)
        self.assertEqual(body["lines"][0]["text"], "ask 0")

        code, more = live.transcript("claude-101", 10, 10)
        self.assertEqual(more["lines"][0]["text"], "ask 5")

    def test_a_negative_offset_is_the_newest_page(self):
        lines = [claude_record("user", message={"role": "user", "content": f"ask {n}"})
                 for n in range(30)]
        self.claude_transcript("/repos/thing", lines)
        self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})
        code, body = live.transcript("claude-101", -1, 5)
        self.assertEqual(code, 200)
        self.assertEqual(body["offset"], 25)
        self.assertEqual(body["lines"][-1]["text"], "ask 29")

    def test_a_key_that_is_not_engine_and_pid_never_reaches_a_file(self):
        self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})
        for bad in ("../x", "/etc/passwd", "claude-101/../..", "claude", "-1",
                    "claude-101 ", "CLAUDE-101"):
            code, body = live.transcript(bad.strip() if bad != "claude-101 " else bad)
            if bad == "claude-101 ":
                continue  # a trailing space is stripped, and that key is real
            self.assertEqual(code, 400, bad)
            self.assertEqual(body["error"], live.BAD_KEY)

    def test_a_key_for_a_process_that_is_not_running_is_not_found(self):
        self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})
        code, body = live.transcript("claude-999999")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], live.NO_SESSION)

    def test_a_session_with_no_transcript_says_so_rather_than_showing_an_empty_one(self):
        self.machine({"claude": {101: ("/nowhere", "01:00")}, "codex": {}})
        code, body = live.transcript("claude-101")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], live.NO_TRANSCRIPT)

    def test_a_page_is_capped_however_large_the_ask(self):
        self.claude_transcript("/repos/thing", [claude_record(
            "user", message={"role": "user", "content": "x"})])
        self.machine({"claude": {101: ("/repos/thing", "01:00")}, "codex": {}})
        code, body = live.transcript("claude-101", 0, 99999)
        self.assertEqual(code, 200)
        self.assertLessEqual(len(body["lines"]), live.MAX_LIMIT)


class ReadOnlyTest(unittest.TestCase):
    def test_this_module_has_no_way_to_change_anything(self):
        """The whole claim of this surface. It reads processes and files, and if
        a write ever appears in here it must be a deliberate decision and not a
        line that arrived with a feature."""
        source = (pathlib.Path(live.__file__)).read_text()
        for forbidden in ("subprocess.Popen", "open(", ".write_text(", ".write(",
                          "os.remove", "shutil.", "unlink"):
            if forbidden == "open(":
                continue  # reads are the point; the modes are checked below
            self.assertNotIn(forbidden, source, forbidden)
        for mode in ('"w"', "'w'", '"a"', "'a'", '"r+"'):
            self.assertNotIn(mode, source, mode)


if __name__ == "__main__":
    unittest.main()
