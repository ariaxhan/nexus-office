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
os.environ["NEXUS_LEASE_INDEX"] = os.path.join(tempfile.mkdtemp(prefix="nexus-index-"), "lease-repos.json")  # never the real index


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Fake:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


class Lanes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-lanes-")
        os.environ["NEXUS_LEASE_INDEX"] = os.path.join(self.dir, "lease-repos.json")
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

    def test_failed_claude_lane_continues_on_codex_in_same_checkout(self):
        seen = []
        def run(argv, cwd=None, **kw):
            if cwd == self.repo and kw.get("timeout") != 1800:
                seen.append(argv)
                if argv[1] == "run":
                    return Fake(1)
                self.write("a.txt", "continued\n")
            return Fake()
        issue = {"number": 1, "title": "fix", "labels": []}
        with unittest.mock.patch("nexus.risk.classify", return_value="direct"):
            result = executor.fly(self.entry, issue, "flt_fallback", pr_create=None,
                                  comment=lambda b: "c", run=run, write_set=["a.txt"])
        self.assertEqual(result["state"], "LANDED")
        self.assertEqual([argv[1] for argv in seen], ["run", "run-provider"])
        self.assertEqual("continued", self.origin_file("a.txt"))

    def test_failed_check_hold_says_which_check_and_what_it_printed(self):
        said = []
        def run(argv, cwd=None, **kw):
            if kw.get("timeout") == 1800:
                return Fake(2, stdout="FAIL test_gate\n")
            self.write("a.txt", "edit\n")
            return Fake()
        self.entry["check"] = ["npm", "test"]
        issue = {"number": 1, "title": "fix", "labels": []}
        result = executor.fly(self.entry, issue, "flt_check", pr_create=None, comment=lambda b: said.append(b) or "c",
                              run=run, write_set=["a.txt"])
        self.assertEqual(("HELD", "check_failed"), (result["state"], result["reason"]))
        self.assertIn("`npm test` exited 2", said[0])
        self.assertIn("FAIL test_gate", result["detail"])
        self.assertNotIn("FAIL test_gate", said[0])

    def test_both_provider_failures_requeue_retained_edits(self):
        def run(argv, cwd=None, **kw):
            if cwd == self.repo and kw.get("timeout") != 1800:
                self.write("a.txt", "unfinished\n")
                return Fake(1)
            return Fake()
        issue = {"number": 1, "title": "fix", "labels": []}
        result = executor.fly(self.entry, issue, "flt_both_failed", pr_create=None,
                              comment=lambda b: "c", run=run, write_set=["a.txt"])
        self.assertEqual(result["state"], "HELD")
        self.assertTrue(result["requeue"])
        self.assertEqual("unfinished", git(self.origin, "show", f"{result['branch']}:a.txt"))

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

    def test_recovered_work_is_announced_on_the_dead_flights_own_issue(self):
        """#210 W5: a later flight recovering a dead one comments where the dead flight's issue is."""
        record = lanes.acquire(self.repo, "main", "flt_dead", os.getpid(), 600, ["a.txt"])
        self.write("a.txt", "crashed lane\n")
        with open(os.path.join(lanes._dir(self.repo), "flt_dead.json"), "w") as f:
            json.dump(dict(record, pid=999999), f)
        said = []
        [result] = lanes.recover(self.repo, comment_for=lambda flight: lambda body: said.append(flight) or "u")
        self.assertEqual(("HELD", ["flt_dead"]), (result["state"], said))

    def test_whole_repo_recovery_never_reverts_the_shared_checkout(self):
        record = lease.acquire(self.repo, "main", "flt_whole", os.getpid(), 600)
        self.write("human.txt", "another session\n")
        with open(lease.path(self.repo), "w") as f:
            json.dump(dict(record, pid=999999), f)
        self.assertEqual("HELD", lease.recover(self.repo, lambda b: "c")["state"])
        self.assertEqual("another session\n", self.read("human.txt"))

    def test_held_push_is_idempotent_and_own_stale_branch_moves_with_lease(self):
        lanes.PUSH_PAUSE_S = 0
        head = git(self.repo, "rev-parse", "HEAD")
        self.write("a.txt", "work\n")
        first = landing.commit_paths(self.repo, head, ["a.txt"], "one")
        self.assertEqual(first, lanes.push_owned(self.repo, first, "aria/held/flt_x"))
        again = landing.commit_paths(self.repo, head, ["a.txt"], "two")  # same tree, new commit: the old retry loop
        self.assertNotEqual(first, again)
        self.assertEqual(first, lanes.push_owned(self.repo, again, "aria/held/flt_x"))
        self.write("a.txt", "rebuilt\n")
        rebuilt = landing.commit_paths(self.repo, head, ["a.txt"], "three")
        self.assertEqual(first, lanes.push_owned(self.repo, first, "aria/issue-7"))
        self.assertEqual(rebuilt, lanes.push_owned(self.repo, rebuilt, "aria/issue-7"))  # our PR branch, leased
        self.assertEqual(rebuilt, git(self.origin, "rev-parse", "aria/issue-7"))
        git(self.repo, "push", "-q", "origin", f"{first}:refs/heads/person")
        self.assertIsNone(lanes.push_owned(self.repo, rebuilt, "person"))  # never forced: not ours
        self.assertEqual(first, git(self.origin, "rev-parse", "person"))

    def test_unpushable_hold_is_a_wait_that_keeps_bytes(self):
        record = lanes.acquire(self.repo, "main", "flt_np", os.getpid(), 600, ["a.txt"])
        self.write("a.txt", "keep\n")
        with unittest.mock.patch.object(lanes, "push_owned", return_value=None):
            with self.assertRaisesRegex(lease.Owned, "held_push_failed"):
                lanes.hold(self.repo, record, ["a.txt"], "crashed")
        self.assertEqual("keep\n", self.read("a.txt"))

    def test_stale_lane_already_held_is_released_not_re_held(self):
        record = lanes.acquire(self.repo, "main", "flt_done", os.getpid(), 600, ["a.txt"])
        self.write("a.txt", "held before\n")
        sha = landing.commit_paths(self.repo, git(self.repo, "rev-parse", "HEAD"), ["a.txt"], "held")
        git(self.repo, "push", "-q", "origin", f"{sha}:refs/heads/aria/held/flt_done")
        with open(os.path.join(lanes._dir(self.repo), "flt_done.json"), "w") as f:
            json.dump(dict(record, pid=999999), f)
        [result] = lanes.recover(self.repo, lambda b: "c")
        self.assertEqual(("HELD", "already_held", sha), (result["state"], result["reason"], result["sha"]))
        self.assertEqual([], lanes.records(self.repo))

    def test_records_carry_host_times_reason_and_paths(self):
        ws = lanes.acquire(self.repo, "main", "flt_ws", os.getpid(), 600, ["a.txt"])
        self.assertEqual(("write_set", ["a.txt"]), (ws["reason"], ws["paths"]))
        for key in ("host", "started_at", "renewed_at", "heartbeat_at"):
            self.assertIn(key, ws)
        lanes.release(self.repo, "flt_ws")
        whole = lease.acquire(self.repo, "main", "flt_w", os.getpid(), 600)
        self.assertEqual(("whole_repo", None), (whole["reason"], whole["paths"]))
        self.assertIn(self.repo, lease.indexed())

    def test_stale_rules(self):
        now = time.time()
        live = dict(lease.stamp("f", os.getpid(), 99999, "whole_repo", None, now), write_set=None)
        self.assertFalse(lease.stale(live, now))
        self.assertTrue(lease.stale(dict(live, pid=999999), now))  # dead pid
        self.assertTrue(lease.stale(dict(live, heartbeat_at=now - 601), now))  # wedged holder
        self.assertTrue(lease.stale(dict(live, heartbeat_at=now, renewed_at=now - 1801), now))  # max hold, repo-wide
        ws = dict(live, write_set=["a.txt"], heartbeat_at=now, renewed_at=now - 1801)
        self.assertFalse(lease.stale(ws, now))
        self.assertTrue(lease.stale(dict(ws, renewed_at=now - 3601), now))  # max hold, write set
        legacy = {"flight": "old", "pid": os.getpid(), "expires": now + 60}
        self.assertFalse(lease.stale(legacy, now))
        self.assertTrue(lease.stale(dict(legacy, expires=now - 1), now))

    def test_ticked_live_flight_older_than_max_hold_stays_live_and_dead_one_is_left(self):
        t0 = time.time() - 4000
        lease.acquire(self.repo, "main", "flt_long", os.getpid(), 99999)
        with open(lease.path(self.repo)) as f:
            record = json.load(f)
        with open(lease.path(self.repo), "w") as f:
            json.dump(dict(record, started_at=t0, renewed_at=t0 + 3900, heartbeat_at=t0 + 3900), f)
        for beat in range(3):
            lease.heartbeat(t0 + 3930 + 30 * beat)
        self.assertFalse(lease.stale(lease.read(self.repo), t0 + 4000))
        self.assertEqual(t0, lease.read(self.repo)["started_at"])
        dead = dict(lease.read(self.repo), pid=999999)
        with open(lease.path(self.repo), "w") as f:
            json.dump(dead, f)
        before = lease.read(self.repo)["heartbeat_at"]
        self.assertEqual(0, lease.heartbeat(t0 + 4100))
        self.assertEqual(before, lease.read(self.repo)["heartbeat_at"])  # a dead holder is never renewed

    def test_legacy_record_is_never_rewritten_by_heartbeat(self):
        lease.acquire(self.repo, "main", "flt_legacy", os.getpid(), 600)
        legacy = {k: v for k, v in lease.read(self.repo).items()
                  if k not in ("host", "started_at", "renewed_at", "heartbeat_at", "reason", "paths")}
        with open(lease.path(self.repo), "w") as f:
            json.dump(legacy, f)
        self.assertEqual(0, lease.heartbeat())
        self.assertEqual(legacy, lease.read(self.repo))
        self.assertFalse(lease.stale(legacy))

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


class StaleCheckout(Lanes):
    """#210: a whole-repo flight never builds on a checkout origin has moved past."""

    def advance_origin(self, rel, text):
        other = os.path.join(self.dir, "other")
        git(self.dir, "clone", "-q", self.origin, other)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            git(other, "config", k, v)
        with open(os.path.join(other, rel), "w") as f:
            f.write(text)
        git(other, "commit", "-qam", "upstream")
        git(other, "push", "-q", "origin", "main")
        return git(other, "rev-parse", "HEAD")

    def test_behind_checkout_catches_up_and_keeps_unrelated_dirty_bytes(self):
        tip = self.advance_origin("a.txt", "upstream\n")
        self.write("human.txt", "a person's edit\n")
        record = lease.acquire(self.repo, "main", "flt_new", os.getpid(), 600)
        self.assertEqual(tip, record["head"])
        self.assertEqual("upstream\n", self.read("a.txt"))
        self.assertEqual("a person's edit\n", self.read("human.txt"))

    def test_dirty_bytes_on_a_file_origin_changed_refuse_the_flight_and_free_the_lock(self):
        self.advance_origin("a.txt", "upstream\n")
        self.write("a.txt", "a person's edit\n")
        with self.assertRaisesRegex(lease.Owned, "behind_origin:dirty a.txt"):
            lease.acquire(self.repo, "main", "flt_new", os.getpid(), 600)
        self.assertEqual("a person's edit\n", self.read("a.txt"))
        self.assertIsNone(lease.read(self.repo))


class PullRequestBranch(Lanes):
    """A review repair rebuilds from main: it may replace a PR branch Nexus wrote, never one a person pushed to."""

    def commit_on(self, branch, message):
        git(self.repo, "checkout", "-q", "-b", branch, "main")
        self.write("a.txt", message + "\n")
        git(self.repo, "commit", "-qam", message)
        git(self.repo, "push", "-q", "origin", branch)
        git(self.repo, "checkout", "-q", "main")
        return git(self.repo, "rev-parse", branch)

    def rebuilt(self):
        self.write("b.txt", "repair\n")
        return landing.commit_paths(self.repo, git(self.repo, "rev-parse", "HEAD"), ["b.txt"],
                                    "repair\n\nNexus-Flight: flt_2")

    def test_nexus_branch_is_replaced_by_the_repair(self):
        self.commit_on("aria/issue-7", "first\n\nNexus-Flight: flt_1")
        sha = self.rebuilt()
        self.assertTrue(landing._replace_own(self.repo, sha, "aria/issue-7"))
        self.assertEqual(sha, git(self.origin, "rev-parse", "aria/issue-7"))

    def test_a_persons_commit_on_the_branch_is_never_overwritten(self):
        theirs = self.commit_on("aria/issue-7", "a person's fix")
        self.assertFalse(landing._replace_own(self.repo, self.rebuilt(), "aria/issue-7"))
        self.assertEqual(theirs, git(self.origin, "rev-parse", "aria/issue-7"))


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


    def test_wave_gates_then_strict_priority_within_wave(self):
        from nexus import work
        item = lambda n: {"repo": "o/r", "number": n, "write_set": [str(n)], "depends_on": [], "window": "any"}
        labels = {1: [], 2: ["p2"], 3: ["p1"], 4: ["p0"], 9: ["p0"]}
        led = unittest.mock.Mock()
        led.conn.execute.side_effect = lambda sql, args: unittest.mock.Mock(
            fetchone=lambda: {"id": int(args[0].rsplit("#", 1)[1]), "state": "accepted"})
        issue = lambda _led, _kind, n: {"state": "open", "labels": [{"name": l} for l in ["ready"] + labels[n]]}
        with unittest.mock.patch.object(lanes, "read_plan", return_value=[[item(1), item(2), item(3), item(4)], [item(9)]]), \
                unittest.mock.patch.object(work, "eligible", return_value=[{"repo": "o/r"}]), \
                unittest.mock.patch.object(work, "discover"), \
                unittest.mock.patch.object(work, "next_retry", return_value=0), \
                unittest.mock.patch.object(work, "latest", side_effect=issue):
            self.assertEqual([4, 9, 3], [p["number"] for p in work.wave_candidates(led, [], (3, 3))])  # wave 2's p0 preempts
            self.assertEqual([4], [p["number"] for p in work.wave_candidates(led, [], (1, 3))])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
