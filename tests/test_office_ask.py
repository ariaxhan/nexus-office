"""Durable FIFO behavior for the shared Office Ask conversation."""
import pathlib
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
import office_ask as ask


class AskQueueTest(unittest.TestCase):
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
        ask.WORKER = None
        self.addCleanup(self._finish_worker)

    def _finish_worker(self):
        worker = ask.WORKER
        if worker and worker.is_alive():
            worker.join(3)
        ask.WORKER = None

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

    def test_restart_and_provider_failure_finalize_then_continue(self):
        with patch.object(ask, "_ensure_worker"):
            receipts = [ask.send({"request_id": f"restart-request-{n:02d}", "text": text,
                                  "model": "model-a"})
                        for n, text in enumerate(("A", "B", "C"), 1)]
        with ask.connect() as db:
            db.execute("UPDATE messages SET status='working' WHERE id IN (?,?)",
                       (receipts[0]["user_id"], receipts[0]["reply_id"]))

        def provider(text, model, reply_id):
            if text == "B":
                raise RuntimeError("provider broke")
            return "answer " + text

        with patch.object(ask, "_answer_turn", side_effect=provider):
            recovered = ask.recover()
            self.assertEqual(recovered["interrupted"], 1)
            self._wait_for(lambda: not ask.read()["busy"])

        state = ask.read()
        users = {row["text"]: row for row in state["messages"] if row["role"] == "user"}
        answers = {row["parent_id"]: row for row in state["messages"] if row["role"] == "office"}
        self.assertEqual([users[text]["status"] for text in ("A", "B", "C")],
                         ["failed", "failed", "completed"])
        self.assertIn("restarted", answers[users["A"]["id"]]["text"].lower())
        self.assertIn("provider broke", answers[users["B"]["id"]]["text"])
        self.assertEqual(answers[users["C"]["id"]]["text"], "answer C")
        self.assertEqual(state["queue"], {"queued": 0, "working": 0})

    def test_request_retry_is_idempotent_and_conflicts_are_rejected(self):
        body = {"request_id": "retry-request-0001", "text": "Keep this", "model": "model-a"}
        with patch.object(ask, "_ensure_worker"):
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
        with patch.object(ask, "_ensure_worker"):
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
