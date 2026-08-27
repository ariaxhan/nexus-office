"""The agents running on this machine, and the way to answer one.

hcom is faked here, by putting a script called `hcom` on PATH. That is the whole
fixture: this module's job is to run a local binary and make sense of what it
says, so the test that matters is what it does when that binary lies, hangs, or
answers with something that is not JSON.

Three things are being protected.

**A reply that goes nowhere must be refused, not sent.** hcom will happily accept
a message for a dead agent. A "sent" toast over a message nothing will ever read
is the false-green this whole project exists to kill, so `say` checks the status
first and returns a conflict.

**Starting a session runs a program with Aria's credentials.** So the engine is
one of two exact names and the directory is one the office already knows about.
Neither is ever interpolated into a shell, and the checks are here rather than in
the door, because the door's job is "is this Aria" and this one's is "is this a
thing to run".

**hcom being absent is not an empty office.** A machine with no hcom can see no
sessions AND can see no evidence that there are none. Those must not draw alike.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

import sessions  # noqa: E402


def agent(name, status="active", directory="/tmp", tool="claude", **kw):
    row = {"name": name, "status": status, "directory": directory, "tool": tool,
           "description": f"{status}: Bash", "unread_count": 0, "headless": False,
           "session_id": "s-" + name, "status_age_seconds": 12,
           "created_at": 1787815131.0, "launch_context": {"git_branch": "main"}}
    row.update(kw)
    return row


class FakeHcom(unittest.TestCase):
    """A stub `hcom` on PATH, and a record of how it was called.

    The recording is the point of writing argv to a file rather than asserting on
    a mock: this module's contract with the outside world is an argument list,
    and an argument list is exactly what a mock lets you get wrong without
    noticing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.calls = self.dir / "calls.jsonl"
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.was_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:{self.was_path}"
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self.was_path))
        sessions._origin_cache.clear()
        self.addCleanup(sessions._origin_cache.clear)
        self.install()

    def install(self, list_out="[]", transcript_out="[]", term_out="{}",
                send_rc=0, send_out="delivered", start_rc=0, sleep=0):
        """Write the stub. Every subcommand answers from a literal, so a test
        changing one answer cannot accidentally change another."""
        script = f"""#!/usr/bin/env python3
import json, os, sys, time
with open({str(self.calls)!r}, "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "stdin": (
        sys.stdin.read() if not sys.stdin.isatty() else "")}}) + "\\n")
time.sleep({sleep})
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "list":
    print({list_out!r}); sys.exit(0)
if cmd == "transcript":
    print({transcript_out!r}); sys.exit(0)
if cmd == "term":
    print({term_out!r}); sys.exit(0)
if cmd == "send":
    print({send_out!r}); sys.exit({send_rc})
sys.exit({start_rc})
"""
        target = self.bin / "hcom"
        target.write_text(script)
        target.chmod(0o755)

    def with_agents(self, *rows, **kw):
        self.install(list_out=json.dumps(list(rows)), **kw)

    def argv(self):
        try:
            return [json.loads(l) for l in self.calls.read_text().splitlines() if l.strip()]
        except OSError:
            return []


class ReadTest(FakeHcom):
    def test_no_hcom_is_its_own_state_and_never_an_empty_office(self):
        # An empty PATH, not the stub removed: this machine has a real hcom, and
        # a test that only deletes the fake one would quietly measure the real
        # one instead.
        (self.bin / "hcom").unlink()
        os.environ["PATH"] = str(self.bin)
        out = sessions.read()
        self.assertEqual(out["state"], "unavailable")
        self.assertEqual(out["sessions"], [])
        self.assertIn("not installed", out["detail"])

    def test_hcom_answering_with_something_that_is_not_json_is_unreadable(self):
        self.install(list_out="not json at all")
        out = sessions.read()
        self.assertEqual(out["state"], "unreadable")
        self.assertEqual(out["sessions"], [])

    def test_hcom_answering_with_nothing_running_is_a_real_answer(self):
        out = sessions.read()
        self.assertEqual(out["state"], "empty")
        self.assertEqual((out["live"], out["blocked"]), (0, 0))

    def test_a_hung_hcom_is_reported_rather_than_holding_the_door(self):
        self.install(sleep=3)
        was = sessions.LIST_TIMEOUT_S
        sessions.LIST_TIMEOUT_S = 0.4
        self.addCleanup(lambda: setattr(sessions, "LIST_TIMEOUT_S", was))
        out = sessions.read()
        self.assertEqual(out["state"], "unreadable")
        self.assertIn("did not answer", out["detail"])

    def test_the_one_waiting_on_a_person_is_first(self):
        """Blocked, then listening, then active, then the dead. A roster that
        sorts by name puts the agent that needs you halfway down."""
        self.with_agents(agent("zed", "blocked"), agent("ann", "active"),
                         agent("bob", "listening"), agent("cal", "inactive"))
        rows = sessions.read()["sessions"]
        self.assertEqual([r["name"] for r in rows], ["zed", "bob", "ann", "cal"])
        self.assertEqual(sessions.read()["blocked"], 1)

    def test_live_counts_the_reachable_and_never_the_dead(self):
        self.with_agents(agent("a", "active"), agent("b", "inactive"),
                         agent("c", "listening"))
        out = sessions.read()
        self.assertEqual(out["live"], 2)
        self.assertEqual([r["reachable"] for r in out["sessions"]],
                         [True, True, False])

    def test_a_row_says_whether_a_message_would_ever_be_read(self):
        self.with_agents(agent("dead", "inactive"))
        self.assertFalse(sessions.read()["sessions"][0]["reachable"])

    def test_a_bash_heredoc_in_a_status_never_becomes_the_whole_row(self):
        self.with_agents(agent("a", status_detail="x" * 5000))
        self.assertLessEqual(len(sessions.read()["sessions"][0]["detail"]), 300)

    def test_filtering_by_desk_asks_for_one_repo_and_gets_one_repo(self):
        self.with_agents(agent("a", directory="/one"), agent("b", directory="/two"))
        sessions._origin_cache.update({"/one": "acme/one", "/two": "acme/two"})
        rows = sessions.read("acme/two")["sessions"]
        self.assertEqual([r["name"] for r in rows], ["b"])

    def test_a_session_in_a_folder_that_is_not_a_repo_still_gets_a_row(self):
        self.with_agents(agent("a", directory=str(self.dir)))
        row = sessions.read()["sessions"][0]
        self.assertEqual(row["repo"], "")
        self.assertEqual(row["name"], "a")

    def test_by_desk_leaves_out_the_dead_and_the_deskless(self):
        self.with_agents(agent("a", directory="/one"), agent("b", "inactive", directory="/one"),
                         agent("c", directory="/nowhere"))
        sessions._origin_cache.update({"/one": "acme/one", "/nowhere": ""})
        self.assertEqual(sessions.by_desk(), {"acme/one": ["a"]})


class OriginTest(unittest.TestCase):
    """A session's folder is joined to a desk by its git remote, and only by a
    GitHub one. Guessing a desk for a remote that is not GitHub would sit a
    session at somebody else's desk."""

    def test_every_shape_a_github_remote_comes_in(self):
        for url in ("git@github.com:acme/thing.git", "git@github.com:acme/thing",
                    "https://github.com/acme/thing.git",
                    "https://github.com/acme/thing",
                    "git@github-work:acme/thing.git"):
            self.assertEqual(sessions.parse_origin(url), "acme/thing", url)

    def test_a_remote_that_is_not_github_has_no_desk(self):
        for url in ("git@gitlab.com:acme/thing.git", "https://bitbucket.org/acme/thing",
                    "/srv/git/thing.git", "", "nonsense"):
            self.assertEqual(sessions.parse_origin(url), "", url)


class SayTest(FakeHcom):
    def test_a_message_to_a_dead_session_is_refused_and_never_sent(self):
        """hcom would take it and nothing would read it. A reply that silently
        goes nowhere is worse than a refusal a person can act on."""
        self.with_agents(agent("ghost", "inactive"))
        code, body = sessions.say({"name": "ghost", "text": "hello"})
        self.assertEqual(code, 409)
        self.assertIn("inactive", body["error"])
        self.assertEqual([c["argv"][0] for c in self.argv()], ["list"])

    def test_a_message_to_a_session_hcom_never_heard_of_is_a_404(self):
        code, body = sessions.say({"name": "nobody", "text": "hi"})
        self.assertEqual(code, 404)

    def test_a_message_reaches_hcom_on_stdin_and_never_in_argv(self):
        """argv is world readable through `ps`. Aria's prose is not."""
        self.with_agents(agent("veru", "active"))
        code, body = sessions.say({"name": "veru", "text": "stop and explain"})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        send = [c for c in self.argv() if c["argv"][:1] == ["send"]][0]
        self.assertEqual(send["argv"], ["send", "@veru"])
        self.assertEqual(send["stdin"], "stop and explain")

    def test_a_blocked_agent_is_exactly_the_one_worth_answering(self):
        self.with_agents(agent("veru", "blocked"))
        self.assertEqual(sessions.say({"name": "veru", "text": "yes"})[0], 200)

    def test_hcom_failing_is_reported_as_a_failure_and_never_as_a_send(self):
        self.with_agents(agent("veru"), send_rc=1, send_out="no such agent")
        code, body = sessions.say({"name": "veru", "text": "hi"})
        self.assertEqual(code, 502)
        self.assertIn("no such agent", body["error"])

    def test_a_name_that_is_not_a_name_never_reaches_argv(self):
        for bad in ("; rm -rf /", "../../etc", "a b", "@veru", "", None, 7, "x" * 200):
            code, body = sessions.say({"name": bad, "text": "hi"})
            self.assertEqual(code, 400, repr(bad))
            self.assertEqual(body["error"], sessions.BAD_NAME)
        self.assertEqual(self.argv(), [], "nothing ran at all")

    def test_an_empty_message_is_refused_before_anything_runs(self):
        for bad in (None, "", "   ", 5, ["hi"]):
            self.assertEqual(sessions.say({"name": "veru", "text": bad})[0], 400)
        self.assertEqual(sessions.say({"name": "veru", "text": "x" * 9000})[1]["error"],
                         sessions.LONG_MESSAGE)
        self.assertEqual(self.argv(), [])


class TranscriptTest(FakeHcom):
    def test_the_conversation_comes_back_in_the_office_s_own_words(self):
        rows = [{"position": 4, "timestamp": "2026-08-27T21:17:10.329Z",
                 "user": "what are you doing", "action": "reading the diff",
                 "files": ["a.py"]}]
        self.install(transcript_out=json.dumps(rows))
        code, body = sessions.transcript("veru", 5)
        self.assertEqual(code, 200)
        self.assertEqual(body["exchanges"][0]["you"], "what are you doing")
        self.assertEqual(body["exchanges"][0]["them"], "reading the diff")
        self.assertEqual(self.argv()[0]["argv"],
                         ["transcript", "veru", "--last", "5", "--json"])

    def test_how_many_exchanges_is_clamped_rather_than_trusted(self):
        for asked, want in ((0, "1"), (-4, "1"), (9999, str(sessions.MAX_EXCHANGES)),
                            ("nonsense", str(sessions.DEFAULT_EXCHANGES))):
            self.calls.write_text("")
            sessions.transcript("veru", asked)
            self.assertEqual(self.argv()[0]["argv"][3], want, repr(asked))

    def test_a_bad_name_is_refused_before_hcom_is_run(self):
        self.assertEqual(sessions.transcript("; ls")[0], 400)
        self.assertEqual(self.argv(), [])

    def test_hcom_printing_prose_is_a_bad_gateway_not_an_empty_conversation(self):
        self.install(transcript_out="no such agent")
        code, body = sessions.transcript("veru")
        self.assertEqual(code, 502)


class ScreenTest(FakeHcom):
    def test_the_terminal_is_read_and_never_typed_into(self):
        self.install(term_out=json.dumps({"lines": ["one", "two"], "ready": True,
                                          "prompt_empty": True}))
        code, body = sessions.screen("veru")
        self.assertEqual(code, 200)
        self.assertEqual(body["lines"], ["one", "two"])
        self.assertTrue(body["ready"])
        self.assertEqual([c["argv"][0] for c in self.argv()], ["term"])
        self.assertNotIn("inject", json.dumps(self.argv()),
                         "this module never injects keystrokes into a live agent")


class StartTest(FakeHcom):
    def setUp(self):
        super().setUp()
        self.root = self.dir / "vault"
        (self.root / "CodingVault" / "thing").mkdir(parents=True)
        self.was_root = os.environ.get("OFFICE_RUNTIME_ROOT", "")
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        self.addCleanup(lambda: os.environ.__setitem__("OFFICE_RUNTIME_ROOT", self.was_root))

    def test_only_two_engines_exist_and_neither_is_taken_from_the_body(self):
        for bad in ("bash", "claude; rm -rf /", "", None, "CLAUDE ", 7):
            code, body = sessions.start({"tool": bad, "directory": str(self.root)})
            if bad == "CLAUDE ":
                continue  # cased and trimmed on purpose; it is still `claude`
            self.assertEqual(code, 400, repr(bad))
            self.assertEqual(body["error"], sessions.BAD_TOOL)

    def test_a_directory_outside_the_vault_with_nothing_running_in_it_is_refused(self):
        outside = self.dir / "elsewhere"
        outside.mkdir()
        code, body = sessions.start({"tool": "claude", "directory": str(outside)})
        self.assertEqual(code, 400)
        self.assertIn("not a desk", body["error"])
        # It asked hcom what is running, because that is one of the two ways a
        # directory earns its place. It never ran an engine.
        self.assertEqual([c["argv"][0] for c in self.argv()], ["list"])

    def test_a_directory_that_is_not_there_is_refused_before_anything_runs(self):
        code, body = sessions.start({"tool": "codex", "directory": str(self.dir / "nope")})
        self.assertEqual((code, body["error"]), (400, sessions.BAD_DIR))

    def test_a_folder_under_the_vault_is_allowed_and_runs_the_named_engine(self):
        target = self.root / "CodingVault" / "thing"
        code, body = sessions.start({"tool": "codex", "directory": str(target)})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.argv()[-1]["argv"], ["codex", "--dir", str(target.resolve())])

    def test_a_folder_an_agent_already_runs_in_is_allowed_wherever_it_is(self):
        """This grants no reach the machine did not already have: something is
        running there already."""
        outside = self.dir / "elsewhere"
        outside.mkdir()
        self.with_agents(agent("a", directory=str(outside)))
        code, _ = sessions.start({"tool": "claude", "directory": str(outside)})
        self.assertEqual(code, 200)

    def test_a_desk_is_resolved_to_its_checkout_rather_than_guessed(self):
        outside = self.dir / "checkout"
        outside.mkdir()
        self.with_agents(agent("a", directory=str(outside)))
        sessions._origin_cache[str(outside)] = "acme/thing"
        code, body = sessions.start({"tool": "claude", "repo": "acme/thing"})
        self.assertEqual(code, 200)
        self.assertEqual(body["directory"], str(outside))

    def test_a_desk_nobody_has_checked_out_says_so_rather_than_inventing_a_path(self):
        code, body = sessions.start({"tool": "claude", "repo": "acme/absent"})
        self.assertEqual(code, 400)
        self.assertIn("checked out", body["error"])

    def test_a_repo_that_is_not_a_repo_name_is_refused(self):
        self.assertEqual(sessions.start({"tool": "claude", "repo": "../../etc"})[0], 400)

    def test_an_engine_still_coming_up_is_accepted_and_not_called_a_failure(self):
        """hcom exits 2 for "still launching". A terminal that is still opening
        has not failed, and reporting it as one sends a person to look at a
        window that is about to appear."""
        self.with_agents(start_rc=2)
        code, body = sessions.start({"tool": "claude", "directory": str(self.root)})
        self.assertEqual(code, 200)
        self.assertTrue(body["starting"])

    def test_an_engine_that_would_not_start_is_a_failure_with_its_own_words(self):
        self.with_agents(start_rc=1)
        code, body = sessions.start({"tool": "claude", "directory": str(self.root)})
        self.assertEqual(code, 502)

    def test_a_first_prompt_rides_along_when_there_is_one(self):
        code, _ = sessions.start({"tool": "claude", "directory": str(self.root),
                                  "prompt": "read the failing test"})
        self.assertEqual(code, 200)
        self.assertEqual(self.argv()[-1]["argv"][-2:], ["--hcom-prompt", "read the failing test"])

    def test_an_enormous_prompt_is_refused_rather_than_trimmed(self):
        code, body = sessions.start({"tool": "claude", "directory": str(self.root),
                                     "prompt": "x" * 9000})
        self.assertEqual((code, body["error"]), (400, sessions.LONG_PROMPT))

    def test_a_body_that_is_not_an_object_is_a_bad_request(self):
        for bad in (None, [], "claude", 7):
            self.assertEqual(sessions.start(bad)[0], 400)
            self.assertEqual(sessions.say(bad)[0], 400)


if __name__ == "__main__":
    unittest.main()
