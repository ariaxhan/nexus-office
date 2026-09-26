"""Durable FIFO behavior for the shared Office Ask conversation."""
import pathlib
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
import office_ask as ask


class AskQueueTest(unittest.TestCase):
    def test_provider_workspace_is_stable_across_release_paths(self):
        with tempfile.TemporaryDirectory() as root:
            stable = pathlib.Path(root) / 'workspace'
            stable.mkdir()
            release = pathlib.Path(root) / '.office-release-123'
            release.mkdir()
            with patch.dict(os.environ, {'OFFICE_RUNTIME_ROOT': str(stable)}, clear=False):
                with patch.dict(os.environ, {'OFFICE_WORKSPACE': ''}, clear=False):
                    self.assertEqual(ask.workspace_dir(), stable.resolve())
                    self.assertNotEqual(ask.workspace_dir(), release)
            with patch.dict(os.environ, {'OFFICE_WORKSPACE': str(stable)}, clear=False):
                self.assertEqual(ask.workspace_dir(), stable.resolve())

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = pathlib.Path(temp.name) / "ask.sqlite3"
        self.path_patch = patch.object(ask, "path", return_value=self.database)
        self.models_patch = patch.object(ask, "models", return_value={
            "items": [{"id": name} for name in ("model-a", "model-b", "model-c")],
            "default": "model-a",
        })
        self.path_patch.start()
        self.models_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.models_patch.stop)
        self.workers = []
        self.addCleanup(self._finish_worker)

    def _start_drain(self):
        worker = threading.Thread(target=ask._drain, daemon=True)
        self.workers.append(worker)
        worker.start()

    def _finish_worker(self):
        for worker in self.workers:
            worker.join(3)

    def _wait_for(self, predicate):
        for _ in range(200):
            if predicate():
                return
            threading.Event().wait(0.01)
        self.fail("timed out waiting for Ask queue")

    def test_fifo_preserves_each_model_and_runs_one_turn_at_a_time(self):
        releases = {text: threading.Event() for text in ("A", "B", "C")}
        started = {text: threading.Event() for text in releases}
        calls, active, maximum = [], 0, 0
        guard = threading.Lock()

        def provider(text, model, reply_id):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
                calls.append((text, model))
            started[text].set()
            self.assertTrue(releases[text].wait(3))
            with guard:
                active -= 1
            return "answer " + text

        with patch.object(ask, "_answer_turn", side_effect=provider):
            ask.send({"request_id": "request-00000001", "text": "A", "model": "model-a"})
            self._start_drain()
            self.assertTrue(started["A"].wait(1))
            ask.send({"request_id": "request-00000002", "text": "B", "model": "model-b"})
            ask.send({"request_id": "request-00000003", "text": "C", "model": "model-c"})
            waiting = ask.read()
            self.assertEqual(waiting["queue"], {"queued": 2, "working": 1})
            releases["A"].set()
            self.assertTrue(started["B"].wait(1))
            releases["B"].set()
            self.assertTrue(started["C"].wait(1))
            releases["C"].set()
            self._wait_for(lambda: not ask.read()["busy"])

        users = [row for row in ask.read()["messages"] if row["role"] == "user"]
        answers = [row for row in ask.read()["messages"] if row["role"] == "office"]
        self.assertEqual(calls, [("A", "model-a"), ("B", "model-b"), ("C", "model-c")])
        self.assertEqual(maximum, 1)
        self.assertEqual([row["model"] for row in users], ["model-a", "model-b", "model-c"])
        self.assertTrue(all(row["status"] == "completed" for row in users + answers))

    def test_restart_requeues_turn_then_continues_fifo_after_provider_failure(self):
        with patch.object(ask, "_ensure_worker", create=True):
            receipts = [ask.send({"request_id": f"restart-request-{n:02d}", "text": text,
                                  "model": "model-a"})
                        for n, text in enumerate(("A", "B", "C"), 1)]
        with ask.connect() as db:
            db.execute("UPDATE messages SET status='working' WHERE id IN (?,?)",
                       (receipts[0]["user_id"], receipts[0]["reply_id"]))

        failures = [0]
        def provider(text, model, reply_id):
            if text == "B" and failures[0] == 0:
                failures[0] += 1
                raise RuntimeError("provider broke")
            return "answer " + text

        with patch.object(ask, "_answer_turn", side_effect=provider):
            recovered = ask.recover()
            self.assertEqual(recovered["resuming"], 1)
            self._start_drain()
            self._wait_for(lambda: any(row['text'] == 'answer C' for row in ask.read()['messages']))
            with ask.connect() as db:
                db.execute("UPDATE messages SET next_attempt_at=0 WHERE role='user' AND text='B'")
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])

        state = ask.read()
        users = {row["text"]: row for row in state["messages"] if row["role"] == "user"}
        answers = {row["parent_id"]: row for row in state["messages"] if row["role"] == "office"}
        self.assertEqual([users[text]["status"] for text in ("A", "B", "C")],
                         ["completed", "completed", "completed"])
        self.assertEqual(answers[users["A"]["id"]]["text"], "answer A")
        self.assertEqual(answers[users["B"]["id"]]["text"], "answer B")
        self.assertEqual(answers[users["C"]["id"]]["text"], "answer C")
        self.assertEqual(state["queue"], {"queued": 0, "working": 0})

    def test_provider_failure_continues_on_other_provider_without_repeating_actions(self):
        available = {"items": [{"id": "gpt-6-sol"}, {"id": "claude:sonnet"}], "default": "gpt-6-sol"}
        for primary, alternate in (("gpt-6-sol", "claude:sonnet"),
                                   ("claude:sonnet", "gpt-6-sol")):
            calls = []
            def provider(text, model, reply_id):
                calls.append((text, model))
                if model == primary:
                    raise RuntimeError("provider unavailable")
                return "verified continuation"
            with patch.object(ask, "models", return_value=available), patch.object(ask, "_answer_turn", side_effect=provider):
                receipt = ask.send({"request_id": f"fallback-{primary.replace(':', '-')}-001",
                                    "text": "Complete the request", "model": primary})
                self._start_drain()
                self._wait_for(lambda: not ask.read()["busy"])
            reply = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
            self.assertEqual(reply["status"], "completed")
            self.assertEqual(reply["model"], alternate)
            self.assertEqual(reply["text"], "verified continuation")
            self.assertEqual([model for _, model in calls], [primary, alternate])
            self.assertIn("do not repeat a completed send", calls[1][0])

    def test_both_provider_failures_remain_queued_for_automatic_retry(self):
        available = {"items": [{"id": "gpt-6-sol"}, {"id": "claude:sonnet"}], "default": "gpt-6-sol"}
        with patch.object(ask, "models", return_value=available), patch.object(
                ask, "_answer_turn", side_effect=RuntimeError("provider unavailable")):
            receipt = ask.send({"request_id": "both-provider-failure-001",
                                "text": "Keep going", "model": "gpt-6-sol"})
            self._start_drain()
            self._wait_for(lambda: any(row['id'] == receipt['reply_id'] and 'retry automatically' in row['text']
                                       for row in ask.read()['messages']))
        reply = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        self.assertEqual(reply["status"], "queued")
        self.assertIn("retry automatically", reply["text"])
        with ask.connect() as db:
            db.execute("UPDATE messages SET next_attempt_at=0 WHERE id=?", (receipt["user_id"],))
        with patch.object(ask, "_answer_turn", return_value="Recovered answer"):
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])
        reply = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        self.assertEqual(reply["status"], "completed")
        self.assertEqual(reply["text"], "Recovered answer")

    def test_restart_restores_completed_codex_turn_without_starting_it_again(self):
        body = {"request_id": "recover-request-0001", "text": "Inspect the work", "model": "model-a"}
        with patch.object(ask, "_ensure_worker", create=True):
            receipt = ask.send(body)
        with ask.connect() as db:
            ask._set(db, "thread_id", "thread-1")
            db.execute("UPDATE messages SET status='working' WHERE id IN (?,?)",
                       (receipt["user_id"], receipt["reply_id"]))

        turn = {"id": "turn-1", "status": "completed", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": "[Office request: recover-request-0001]\nInspect the work"}]},
            {"type": "agentMessage", "text": "The verified answer"}]}

        class SavedServer:
            calls = []
            def request(self, method, params, timeout=20):
                self.calls.append(method)
                if method == "thread/read":
                    return {"thread": {"turns": [turn]}}
                return {"thread": {"id": "thread-1"}}
            def close(self):
                pass

        fake = SavedServer()
        with patch.object(ask, "AppServer", return_value=fake):
            self.assertEqual(ask.recover(), {"resuming": 1})
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])
        answer = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        self.assertEqual(answer["text"], "The verified answer")
        self.assertEqual(answer["status"], "completed")
        self.assertNotIn("turn/start", fake.calls)

    def test_lost_completion_notification_reads_finished_turn_and_unblocks_queue(self):
        with patch.object(ask, "_ensure_worker", create=True):
            receipt = ask.send({"request_id": "lost-event-request-001", "text": "Check the work",
                                "model": "model-a"})
        turn = {"id": "turn-1", "status": "completed", "items": [
            {"type": "agentMessage", "text": "Saved answer"}]}

        class SilentServer:
            def request(self, method, params, timeout=20):
                if method == "thread/start":
                    return {"thread": {"id": "thread-1"}}
                if method == "turn/start":
                    return {"turn": {"id": "turn-1"}}
                if method == "thread/read":
                    return {"thread": {"turns": [turn]}}
                return {}
            def receive(self, timeout):
                raise TimeoutError("completion notification lost")
            def close(self):
                pass

        with patch.object(ask, "AppServer", return_value=SilentServer()):
            self.assertEqual(ask._answer_turn("Check the work", "model-a", receipt["reply_id"]),
                             "Saved answer")

    def test_interrupted_codex_turn_is_not_replayed(self):
        body = {"request_id": "recover-request-0002", "text": "Do the work", "model": "model-a"}
        with patch.object(ask, "_ensure_worker", create=True):
            receipt = ask.send(body)
        with ask.connect() as db:
            ask._set(db, "thread_id", "thread-1")
            db.execute("UPDATE messages SET status='working' WHERE id IN (?,?)",
                       (receipt["user_id"], receipt["reply_id"]))

        turn = {"id": "turn-old", "status": "interrupted", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": "[Office request: recover-request-0002]\nDo the work"}]}]}

        class InterruptedServer:
            prompts = []
            def request(self, method, params, timeout=20):
                if method == "thread/read":
                    return {"thread": {"turns": [turn]}}
                if method == "turn/start":
                    self.prompts.append(params["input"][0]["text"])
                    return {"turn": {"id": "turn-next"}}
                return {"thread": {"id": "thread-1"}}
            def receive(self, timeout):
                if not hasattr(self, "answered"):
                    self.answered = True
                    return {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "Recovered answer"}}}
                return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
            def close(self):
                pass

        fake = InterruptedServer()
        with patch.object(ask, "AppServer", return_value=fake):
            self.assertEqual(ask.recover(), {"resuming": 1})
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])
        answer = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        self.assertEqual(answer["status"], "failed")
        self.assertIn("was not replayed", answer["text"])
        self.assertEqual(fake.prompts, [])

    def _run_drain(self, model, request_id, server):
        available = {"items": [{"id": "gpt-6-sol"}, {"id": "claude:sonnet"}], "default": model}
        claude_calls = []
        def fake_claude(message, alias, bridge):
            claude_calls.append(message)
            return "second execution"
        patches = [patch.object(ask, "models", return_value=available),
                   patch.object(ask, "_claude_answer", side_effect=fake_claude),
                   patch.object(ask, "AppServer", return_value=server)]
        for item in patches:
            item.start()
        try:
            receipt = ask.send({"request_id": request_id, "text": "Send the invoice", "model": model})
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])
        finally:
            for item in patches:
                item.stop()
        reply = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        return reply, claude_calls

    def test_timed_out_codex_turn_fails_instead_of_running_on_other_model(self):
        class HungServer:
            starts = []
            def request(self, method, params, timeout=20):
                if method == "turn/start":
                    self.starts.append(params)
                    return {"turn": {"id": "turn-live"}}
                if method == "thread/read":
                    return {"thread": {"turns": [{"id": "turn-live", "status": "inProgress", "items": []}]}}
                return {"thread": {"id": "thread-1"}}
            def receive(self, timeout):
                raise TimeoutError("no notification")
            def close(self):
                pass
        server = HungServer()
        clock = iter(range(0, 10 ** 7, 1000))
        with patch.object(ask.time, "monotonic", side_effect=lambda: next(clock)):
            reply, claude_calls = self._run_drain("gpt-6-sol", "timeout-codex-request-01", server=server)
        self.assertEqual(claude_calls, [])
        self.assertEqual(len(server.starts), 1)
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["model"], "gpt-6-sol")
        self.assertIn("was not replayed", reply["text"])
        self.assertIn("exceeded 30 minutes", reply["text"])

    def test_timed_out_claude_run_fails_instead_of_running_on_other_model(self):
        import subprocess
        available = {"items": [{"id": "gpt-6-sol"}, {"id": "claude:sonnet"}], "default": "claude:sonnet"}
        runs = []
        def hung(*args, **kwargs):
            runs.append(args)
            raise subprocess.TimeoutExpired("claude", 1800)
        with patch.object(ask, "models", return_value=available), \
                patch.object(ask, "claude_binary", return_value="/bin/claude"), \
                patch.object(ask, "claude_environment", return_value={}), \
                patch.object(ask.subprocess, "run", side_effect=hung), \
                patch.object(ask, "AppServer", side_effect=AssertionError("second execution on Codex")) as codex:
            receipt = ask.send({"request_id": "timeout-claude-request-1", "text": "Send the invoice",
                                "model": "claude:sonnet"})
            self._start_drain()
            self._wait_for(lambda: not ask.read()["busy"])
        reply = next(row for row in ask.read()["messages"] if row["id"] == receipt["reply_id"])
        self.assertEqual(len(runs), 1, reply["text"])
        codex.assert_not_called()
        self.assertEqual(reply["status"], "failed")
        self.assertIn("was not replayed", reply["text"])

    def test_codex_turn_that_never_started_still_falls_back(self):
        class RefusingServer:
            def request(self, method, params, timeout=20):
                if method == "turn/start":
                    raise RuntimeError("provider unavailable")
                if method == "thread/read":
                    return {"thread": {"turns": []}}
                return {"thread": {"id": "thread-1"}}
            def close(self):
                pass
        reply, claude_calls = self._run_drain("gpt-6-sol", "never-started-request-1", server=RefusingServer())
        self.assertEqual(len(claude_calls), 1)
        self.assertEqual(reply["status"], "completed")
        self.assertEqual(reply["model"], "claude:sonnet")

    def test_codex_start_error_with_turn_recorded_is_not_replayed(self):
        class AmbiguousServer:
            def request(self, method, params, timeout=20):
                if method == "turn/start":
                    raise TimeoutError("turn/start response lost")
                if method == "thread/read":
                    return {"thread": {"turns": [{"id": "turn-ghost", "status": "inProgress", "items": [
                        {"type": "userMessage", "content": [{"type": "text",
                         "text": "[Office request: ambiguous-start-request]\nSend the invoice"}]}]}]}}
                return {"thread": {"id": "thread-1"}}
            def close(self):
                pass
        reply, claude_calls = self._run_drain("gpt-6-sol", "ambiguous-start-request", server=AmbiguousServer())
        self.assertEqual(claude_calls, [])
        self.assertEqual(reply["status"], "failed")
        self.assertIn("was not replayed", reply["text"])

    def test_request_retry_is_idempotent_and_conflicts_are_rejected(self):
        body = {"request_id": "retry-request-0001", "text": "Keep this", "model": "model-a"}
        with patch.object(ask, "_ensure_worker", create=True):
            first = ask.send(body)
            self.assertEqual(ask.send(dict(body)), first)
            with self.assertRaisesRegex(ValueError, "request_id"):
                ask.send({**body, "text": "Different"})
            with self.assertRaisesRegex(ValueError, "request_id"):
                ask.send({**body, "model": "model-b"})
            with self.assertRaises(ValueError):
                ask.send({"request_id": "short", "text": "No", "model": "model-a"})
        state = ask.read()
        self.assertEqual(len([row for row in state["messages"] if row["role"] == "user"]), 1)
        self.assertEqual(state["queue"], {"queued": 1, "working": 0})

    def test_cached_client_without_request_id_can_send(self):
        with patch.object(ask, "_ensure_worker", create=True):
            receipt = ask.send({"text": "From old client", "model": "model-a"})
        self.assertRegex(receipt["request_id"], r"^[a-f0-9-]{36}$")
        self.assertEqual([row["text"] for row in ask.read()["messages"] if row["role"] == "user"],
                         ["From old client"])

    def test_read_keeps_newest_messages_and_auto_ignores_nonterminal_answers(self):
        with ask.connect() as db:
            db.executemany("INSERT INTO messages(role,text,status,created_at) VALUES('system',?,'completed',0)",
                           [(f"old-{n}",) for n in range(501)])
            db.executemany("INSERT INTO messages(role,text,model,status,created_at) VALUES('office','',?,?,0)",
                           [("gpt-6-sol", "queued"), ("gpt-6-sol", "working"),
                            ("gpt-6-luna", "completed"), ("gpt-6-luna", "failed")])
            db.execute("INSERT INTO messages(role,text,model,status,created_at,request_id) VALUES('user','newest','gpt-6-sol','queued',0,'newest-request-001')")
            with patch.object(ask.random, "betavariate", side_effect=lambda good, bad: good):
                choice = ask.auto_model(db, {"gpt-6-sol", "gpt-6-luna"})
        messages = ask.read()["messages"]
        self.assertEqual(choice, "gpt-6-sol")
        self.assertEqual(len(messages), 500)
        self.assertEqual([row["id"] for row in messages], sorted(row["id"] for row in messages))
        self.assertEqual(messages[-1]["text"], "newest")


if __name__ == "__main__":
    unittest.main()
