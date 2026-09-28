"""#235: batched mechanical reads, the tiered local-task seam, and precedent packets."""

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import unittest.mock

from nexus import batch, delegation, precedent


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "delegation.jsonl")
        patcher = unittest.mock.patch.object(delegation, "LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self):
        with open(self.log) as fh:
            return [json.loads(line) for line in fh]


class Batch(Base):
    def setUp(self):
        super().setUp()
        self.db = os.path.join(self.tmp, "l.sqlite")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE t (id TEXT, state TEXT)")
            c.executemany("INSERT INTO t VALUES (?, ?)", [("a", "held"), ("b", "done")])
        self.doc = os.path.join(self.tmp, "doc.md")
        with open(self.doc, "w") as fh:
            fh.write("# Doc\n## Verdict\nno switch\nstill no\n## Next\nother\n")

    def test_one_result_with_provenance_for_every_step(self):
        out = batch.run({"task": "t", "steps": [
            {"id": "q", "op": "sqlite", "db": self.db, "sql": "select id from t where state = ?", "params": ["held"]},
            {"id": "v", "op": "read", "path": self.doc, "section": "## Verdict"},
            {"id": "g", "op": "grep", "path": self.tmp, "glob": "*.md", "pattern": "no switch"},
        ]})
        self.assertTrue(out["ok"])
        q, v, g = out["steps"]
        self.assertEqual([{"id": "a"}], q["rows"])
        self.assertEqual("no switch\nstill no", v["text"])
        self.assertEqual(1, len(g["hits"]))
        self.assertTrue(all("provenance" in s for s in out["steps"]))
        self.assertEqual("deterministic", self.rows()[-1]["tier"])

    def test_a_failed_step_is_evidence_not_an_answer(self):
        out = batch.run({"task": "t", "steps": [{"id": "x", "op": "read", "path": self.doc, "section": "## Missing"},
                                                {"id": "y", "op": "sqlite", "db": self.db, "sql": "select 1 one"}]})
        self.assertFalse(out["ok"])
        self.assertEqual(["x"], out["errors"])
        self.assertNotIn("text", out["steps"][0])
        self.assertEqual([{"one": 1}], out["steps"][1]["rows"])

    def test_no_writes(self):
        out = batch.run({"task": "t", "steps": [
            {"id": "w", "op": "sqlite", "db": self.db, "sql": "delete from t"},
            {"id": "c", "op": "git", "repo": self.tmp, "args": ["commit", "-m", "x"]},
            {"id": "o", "op": "git", "repo": self.tmp, "args": ["log", "--output=/tmp/x"]},
            {"id": "u", "op": "shell", "cmd": "rm -rf /"},
        ]})
        self.assertEqual(["w", "c", "o", "u"], out["errors"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(2, c.execute("select count(*) from t").fetchone()[0])

    def test_git_read_and_file_as_of_a_commit(self):
        repo = os.path.join(self.tmp, "r")
        os.makedirs(repo)
        git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)  # noqa: E731
        git("init", "-q")
        with open(os.path.join(repo, "f.json"), "w") as fh:
            json.dump({"repos": [{"repo": "x/a", "on": True}, {"repo": "x/b", "on": False}]}, fh)
        git("add", "f.json")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
        with open(os.path.join(repo, "f.json"), "w") as fh:
            fh.write("{}")
        out = batch.run({"task": "t", "steps": [
            {"id": "j", "op": "json", "repo": repo, "rev": "HEAD", "path": "f.json", "select": "repos", "match": "x/b", "fields": ["on"]},
            {"id": "l", "op": "git", "repo": repo, "args": ["log", "--format=%s"]},
        ]})
        self.assertTrue(out["ok"], out["errors"])
        self.assertEqual([{"on": False}], out["steps"][0]["value"])
        self.assertEqual("one\n", out["steps"][1]["text"])


class LocalTask(Base):
    schema = {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}

    def call(self, **kw):
        return delegation.local_task("normalize", self.schema, "E: boom", model="m", prompt=str,
                                     fallback=lambda d, r: (None, "frontier"), **kw)

    def test_deterministic_first_and_the_model_is_never_asked(self):
        with unittest.mock.patch.object(delegation, "_ollama") as model:
            result, rec = self.call(deterministic=lambda d: {"code": "E"})
        model.assert_not_called()
        self.assertEqual(({"code": "E"}, "deterministic"), (result, rec["tier"]))

    def test_a_local_answer_counts_only_when_it_validates(self):
        with unittest.mock.patch.object(delegation, "_ollama", return_value=({"code": "E"}, 40)):
            result, rec = self.call(validator=lambda d, r: None if r["code"] in d else "not in input")
        self.assertEqual(("local", "pass", 40), (rec["tier"], rec["validator"], rec["tokens"]))
        with unittest.mock.patch.object(delegation, "_ollama", return_value=({"code": "Z"}, 40)):
            result, rec = self.call(validator=lambda d, r: None if r["code"] in d else "not in input")
        self.assertIsNone(result)
        self.assertEqual(("frontier", "not in input"), (rec["tier"], rec["escalation"]))
        with unittest.mock.patch.object(delegation, "_ollama", return_value=({"other": 1}, 5)):
            self.assertEqual("missing code", self.call()[1]["escalation"])
        with unittest.mock.patch.object(delegation, "_ollama", side_effect=OSError):
            self.assertEqual("local unavailable: OSError", self.call()[1]["escalation"])

    def test_gradient_counts_every_tier_and_admits_what_was_not_measured(self):
        delegation.record("a", "deterministic")
        delegation.record("b", "local", tokens=100)
        delegation.record("c", "frontier", tokens=2000, cost_usd=0.03)
        g = delegation.gradient()
        self.assertEqual((1, 1), (g["deterministic"]["calls"], g["deterministic"]["unmeasured"]))
        self.assertEqual((100, 0), (g["local"]["tokens"], g["local"]["unmeasured"]))
        self.assertEqual(0.03, g["frontier"]["cost_usd"])
        with self.assertRaises(ValueError):
            delegation.record("d", "opus")


class Precedent(unittest.TestCase):
    def test_packet_fields_are_substrings_of_the_lesson_with_its_id(self):
        text = ("launchd job cannot open chat.db-wal because the WAL file is rotated between stat and open; "
                "fix by opening with immutable=1 in read_only mode")
        p = precedent.packet("LRN-1", text)
        self.assertEqual("agentdb:LRN-1", p["id"])
        for field in ("symptom", "cause", "repair"):
            self.assertIn(p[field], text)
        self.assertTrue(p["repair"].startswith("fix by"))
        self.assertIn("chat.db", p["scope"])
        self.assertIn("repair: fix by", precedent.render(p))

    def test_gate_sends_nothing_without_evidence(self):
        emb = [("a", 0.9), ("b", 0.2)]
        self.assertEqual(["a"], precedent.gate(["a", "b"], [], emb, "q", {}, structure=False, floor=0.5))
        self.assertEqual([], precedent.gate(["b"], [], [("b", 0.2)], "q", {}, structure=False, floor=0.5))

    def test_fusion_and_structure(self):
        self.assertEqual("b", precedent.fuse([("a", 1), ("b", 1)], [("b", 1), ("c", 1)])[0])
        order = precedent.structural(["x", "y"], "fails in serve.py", {"x": "other", "y": "serve.py host check"})
        self.assertEqual(["y", "x"], order)


if __name__ == "__main__":
    unittest.main()
