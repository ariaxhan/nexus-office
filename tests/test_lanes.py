"""Parallel Tower lanes: write-set leases in one canonical checkout on main. Real git, real pushes."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus import executor, landing, lanes, lease  # noqa: E402

landing.GIT_LOCK = "/nonexistent"
lease.LANE_LOCK = os.environ["NEXUS_LANE_LOCK"] = ""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Fake:
    def __init__(self, returncode=0):
        self.returncode, self.stdout, self.stderr = returncode, "", ""


class Lanes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-lanes-")
        self.origin = os.path.join(self.dir, "origin.git")
        self.repo = os.path.join(self.dir, "repo")
        git(self.dir, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.dir, "clone", "-q", self.origin, self.repo)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            git(self.repo, "config", k, v)
        os.makedirs(os.path.join(self.repo, "src"))
        for rel in ("a.txt", "b.txt", "human.txt", "src/x.py"):
            self.write(rel, "base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "push", "-q", "origin", "main")
        self.entry = {"repo": "o/r", "path": self.repo, "default_branch": "main", "risk": None}

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.repo, rel)) as f:
            return f.read()

    def origin_file(self, rel):
        return git(self.origin, "show", f"main:{rel}")

    def fly(self, number, write_set, edits, spans=None, sleep=0.0):
        def run(argv, cwd=None, **kw):
            if cwd == self.repo and kw.get("timeout") != 1800:
                start = time.time()
                for rel, text in edits.items():
                    with open(os.path.join(cwd, rel), "w") as f:
                        f.write(text)
                time.sleep(sleep)
                if spans is not None:
                    spans[number] = (start, time.time())
            return Fake()
        issue = {"number": number, "title": f"t{number}", "labels": []}
        with unittest.mock.patch("nexus.risk.classify", return_value="direct"):
            return executor.fly(self.entry, issue, f"flt_{number}", pr_create=None, comment=lambda b: "c",
                                run=run, write_set=write_set)

    def test_two_disjoint_lanes_run_concurrently_and_both_land_without_worktrees(self):
        spans, results = {}, {}
        threads = [threading.Thread(target=lambda n=n, ws=ws: results.__setitem__(
            n, self.fly(n, ws, {ws[0]: f"lane {n}\n"}, spans, sleep=0.6))) for n, ws in ((1, ["a.txt"]), (2, ["b.txt"]))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual({"LANDED"}, {r["state"] for r in results.values()}, results)
        self.assertLess(max(s for s, _ in spans.values()), min(e for _, e in spans.values()))  # overlapped
        self.assertEqual("lane 1", self.origin_file("a.txt"))
        self.assertEqual("lane 2", self.origin_file("b.txt"))
        self.assertEqual(1, len(git(self.repo, "worktree", "list").splitlines()))
        self.assertEqual("main", git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual([], lanes.records(self.repo))

    def test_overlapping_write_sets_serialize(self):
        lanes.acquire(self.repo, "main", "flt_a", os.getpid(), 600, ["src"])
        with self.assertRaisesRegex(lease.Owned, "write_set:flt_a"):
            lanes.acquire(self.repo, "main", "flt_b", os.getpid(), 600, ["src/x.py"])
        lanes.acquire(self.repo, "main", "flt_c", os.getpid(), 600, ["a.txt"])  # disjoint still runs

    def test_whole_repo_lane_waits_for_write_set_lanes_and_blocks_them(self):
        lanes.acquire(self.repo, "main", "flt_a", os.getpid(), 600, ["a.txt"])
        with self.assertRaisesRegex(lease.Owned, "write_set_lanes"):
            lease.acquire(self.repo, "main", "flt_w", os.getpid(), 600)
        lanes.release(self.repo, "flt_a")
        lease.acquire(self.repo, "main", "flt_w", os.getpid(), 600)
        with self.assertRaisesRegex(lease.Owned, "owned:flt_w"):
            lanes.acquire(self.repo, "main", "flt_b", os.getpid(), 600, ["b.txt"])

    def test_per_repo_cap(self):
        for n, p in enumerate(("a.txt", "b.txt")):
            lanes.acquire(self.repo, "main", f"flt_{n}", os.getpid(), 600, [p], per_repo=2)
        with self.assertRaisesRegex(lease.Owned, "per_repo_cap"):
            lanes.acquire(self.repo, "main", "flt_x", os.getpid(), 600, ["human.txt"], per_repo=2)

    def test_out_of_set_edit_is_refused_at_commit_with_one_lease_note(self):
        lanes.NOTES = os.path.join(self.dir, "notes.jsonl")
        before = git(self.origin, "rev-parse", "main")
        result = self.fly(3, ["a.txt"], {"human.txt": "stray\n"})
        self.assertEqual("plan", result["requeue"])
        self.assertEqual(["human.txt"], result["strays"])
        [row] = [json.loads(l) for l in open(lanes.NOTES)]
        self.assertEqual((3, ["human.txt"]), (row["issue"], row["paths"]))
        self.assertEqual(before, git(self.origin, "rev-parse", "main"))
        self.assertEqual("stray\n", self.read("human.txt"))  # never reverted: may be a person's bytes

    def test_disjoint_landing_elsewhere_is_caught_up_and_overlap_requeues(self):
        other = os.path.join(self.dir, "other")
        git(self.dir, "clone", "-q", self.origin, other)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            git(other, "config", k, v)
        with open(os.path.join(other, "b.txt"), "w") as f:
            f.write("elsewhere\n")
        git(other, "commit", "-qam", "b")
        git(other, "push", "-q", "origin", "main")
        self.assertEqual("LANDED", self.fly(4, ["a.txt"], {"a.txt": "mine\n"})["state"])
        self.assertEqual("elsewhere", self.origin_file("b.txt"))
        self.assertEqual("elsewhere\n", self.read("b.txt"))
        with open(os.path.join(other, "src/x.py"), "w") as f:
            f.write("theirs\n")
        git(other, "pull", "-q")
        git(other, "commit", "-qam", "x")
        git(other, "push", "-q", "origin", "main")
        result = self.fly(5, ["src"], {"src/x.py": "mine\n"})
        self.assertEqual(("HELD", "plan"), (result["state"], result["requeue"]))
        self.assertEqual("theirs", self.origin_file("src/x.py"))

    def test_stale_recovery_touches_only_the_lane_write_set(self):
        record = lanes.acquire(self.repo, "main", "flt_dead", os.getpid(), 600, ["a.txt"])
        self.write("a.txt", "crashed lane\n")
        self.write("human.txt", "live session\n")
        path = os.path.join(lanes._dir(self.repo), "flt_dead.json")
        with open(path, "w") as f:
            json.dump(dict(record, pid=999999), f)
        [result] = lanes.recover(self.repo, lambda b: "c")
        self.assertEqual("HELD", result["state"])
        self.assertEqual(["a.txt"], git(self.origin, "diff", "--name-only", "main", result["sha"]).split())
        self.assertEqual("live session\n", self.read("human.txt"))
        self.assertEqual("base\n", self.read("a.txt"))

    def test_whole_repo_recovery_never_reverts_the_shared_checkout(self):
        record = lease.acquire(self.repo, "main", "flt_whole", os.getpid(), 600)
        self.write("human.txt", "another session\n")
        with open(lease.path(self.repo), "w") as f:
            json.dump(dict(record, pid=999999), f)
        self.assertEqual("HELD", lease.recover(self.repo, lambda b: "c")["state"])
        self.assertEqual("another session\n", self.read("human.txt"))

    def test_plan_reads_triage_schema_ts_and_depends_on(self):
        path = os.path.join(self.dir, "plan.json")
        with open(path, "w") as f:
            json.dump({"schema": "tbs.execution-plan/v1", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "waves": [{"index": 0, "window": "sensitive", "issues": [
                           {"repo": "O/R", "number": 9, "write_set": ["x"], "depends_on": ["O/R#8"]}]}]}, f)
        [[item]] = lanes.read_plan(path)
        self.assertEqual((["o/r#8"], "sensitive"), (item["depends_on"], item["window"]))

    def test_plan_fresh_stale_and_missing(self):
        path = os.path.join(self.dir, "plan.json")
        now = time.time()
        with open(path, "w") as f:
            json.dump({"generated_at": now - 60, "waves": [{"issues": [
                {"repo": "O/R", "number": 7, "write_set": ["src/", "a.txt"]}, {"repo": "o/r", "number": 8}]}]}, f)
        self.assertEqual(["a.txt", "src"], lanes.write_set_for("o/r", 7, path))
        self.assertIsNone(lanes.write_set_for("o/r", 8, path))  # unknown write set = whole repo
        with open(path, "w") as f:
            json.dump({"generated_at": now - 4 * 3600, "waves": [[{"repo": "o/r", "number": 7, "write_set": ["a"]}]]}, f)
        self.assertIsNone(lanes.read_plan(path))
        self.assertIsNone(lanes.read_plan(os.path.join(self.dir, "missing.json")))


class Dispatch(unittest.TestCase):
    def test_stale_or_missing_plan_falls_back_to_current_selection(self):
        from nexus import work
        with unittest.mock.patch.object(lanes, "read_plan", return_value=None), \
                unittest.mock.patch.object(work, "_run", return_value=["fallback"]) as fallback:
            self.assertEqual(["fallback"], work.run(None, [], lane=work.TOWER_LABEL, registry_path="/nonexistent"))
        fallback.assert_called_once()

    def test_wave_takes_disjoint_items_within_caps(self):
        from nexus import work
        wave = [{"repo": "o/r", "number": n, "write_set": ws, "depends_on": [], "window": "any"} for n, ws in
                ((1, ["a"]), (2, ["a/b"]), (3, ["c"]), (4, ["d"]), (5, None))]
        task = {"id": "t", "state": "accepted"}
        led = unittest.mock.Mock()
        led.conn.execute.return_value.fetchone.return_value = task
        with unittest.mock.patch.object(lanes, "read_plan", return_value=[wave]), \
                unittest.mock.patch.object(work, "eligible", return_value=[{"repo": "o/r"}]), \
                unittest.mock.patch.object(work, "discover"), \
                unittest.mock.patch.object(work, "next_retry", return_value=0), \
                unittest.mock.patch.object(work, "latest", return_value={"labels": [{"name": "ready"}], "state": "open"}):
            picked = work.wave_candidates(led, [], (3, 3))
            self.assertEqual([1, 3, 4], [p["number"] for p in picked])  # 2 overlaps 1; 5 (whole repo) overlaps all
            self.assertEqual([1, 3], [p["number"] for p in work.wave_candidates(led, [], (2, 3))])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
