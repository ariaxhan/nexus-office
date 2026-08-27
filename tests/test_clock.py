"""The wall clock's data: parsing jobctl, and keeping the states apart.

The sharp edge here is not the parsing, it is the classification. Four things
have four different fixes and jobctl's own output does not keep all four apart:

  stale    ran fine once, then stopped inside its own budget. Investigate
  off      somebody switched it off. A decision, and not a fault
  never    no receipt at all. Usually a bad path or a permission
  failing  ran, exited non-zero, still inside its budget

jobctl overwrites its own NO DATA with STALE whenever the job declares a budget,
so a job that has NEVER RUN arrives labelled exactly like one that used to work.
The tests that matter are the ones about telling those apart, plus the ones that
prove a broken or hung source never renders as "no jobs".

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
import textwrap
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

JOBCTL_REL = "_meta/services/jobs/jobctl"
REGISTRY_REL = "_meta/services/jobs/registry.jsonl"


class ClockBase(unittest.TestCase):
    """Each test builds a whole fake vault root: a jobctl that prints what we
    want, and a registry beside it. Faking the subprocess rather than mocking it
    keeps the timeout and the exit code honest, and those are the two things
    most likely to be got wrong."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "_meta" / "services" / "jobs").mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        from sources import clock  # noqa: PLC0415 - reloaded so each test reads the env fresh
        self.clock = importlib.reload(clock)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    # -- fixtures --------------------------------------------------------------

    def jobctl(self, body: str):
        """Install a fake jobctl. `body` is python, run when it is executed."""
        p = self.root / JOBCTL_REL
        p.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
        p.chmod(0o755)

    def status(self, jobs, unhealthy=0, off=0):
        payload = json.dumps({"checked": len(jobs), "unhealthy": unhealthy,
                              "off": off, "jobs": jobs})
        self.jobctl(f"""
            import sys
            sys.stdout.write({payload!r})
            # jobctl exits 1 whenever anything is unhealthy. That is the NORMAL
            # alarm case, and reading it as a failure would blank the wall.
            sys.exit({1 if unhealthy else 0})
        """)

    def registry(self, *rows, extra=""):
        lines = ["# a comment, skipped"] + [json.dumps(r) for r in rows]
        if extra:
            lines.append(extra)
        (self.root / REGISTRY_REL).write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def row(job, state, attempt=None, success=None, rc=None, detail=""):
        return {"job": job, "state": state, "detail": detail,
                "last_attempt": attempt, "last_success": success, "last_rc": rc}

    @staticmethod
    def reg(jid, budget=6, kind="interval", command=None):
        sched = {"kind": kind}
        if kind == "interval":
            sched["seconds"] = 3600
        elif kind == "calendar":
            sched["at"] = [{"hour": 7, "minute": 30}]
        return {"id": jid, "state": "enabled", "owner": "aria", "note": "",
                "timeout_s": 3600, "max_success_age_h": budget, "schedule": sched,
                "command": command or ["/bin/bash", f"/srv/{jid}.sh"]}

    def by_id(self, sec):
        return {j["id"]: j for j in sec["jobs"]}

class ClockTest(ClockBase):
    # -- the four states, which is the whole point ------------------------------

    def test_a_job_that_has_never_run_is_never_not_stale(self):
        # jobctl labels this STALE because the job declares a budget and there
        # is no successful run. But there is no receipt AT ALL: it has never
        # fired. Different cause, different fix, so it gets its own state.
        self.status([self.row("com.x.brief", "STALE",
                              detail="no successful run on record, budget 6h")], unhealthy=1)
        self.registry(self.reg("com.x.brief"))
        sec = self.clock.read()
        self.assertEqual(sec["state"], "ok")
        self.assertEqual(self.by_id(sec)["com.x.brief"]["state"], "never")
        self.assertEqual(sec["counts"]["never"], 1)
        self.assertEqual(sec["counts"]["stale"], 0)

    def test_a_job_that_ran_and_stopped_is_stale(self):
        self.status([self.row("com.x.swarm", "STALE", attempt="2026-08-26T01:07:03Z",
                              success="2026-08-16T19:07:05Z", rc=2,
                              detail="last success 226h ago, budget 18h")], unhealthy=1)
        self.registry(self.reg("com.x.swarm", budget=18))
        j = self.by_id(self.clock.read())["com.x.swarm"]
        self.assertEqual(j["state"], "stale")
        self.assertEqual(j["last_rc"], 2)
        self.assertEqual(j["budget_h"], 18)

    def test_off_is_a_decision_and_never_becomes_stale(self):
        # An OFF job whose last success is long gone still reads OFF. Dressing a
        # deliberate pause as a fault is how it becomes an outage nobody looks at.
        self.status([self.row("com.x.tick", "OFF", attempt="2026-08-25T08:22:26Z",
                              success=None, rc=70,
                              detail="switched off in launchd's disabled list")], off=1)
        self.registry(self.reg("com.x.tick", budget=2))
        sec = self.clock.read()
        self.assertEqual(self.by_id(sec)["com.x.tick"]["state"], "off")
        self.assertEqual(sec["counts"]["off"], 1)
        self.assertEqual(sec["counts"]["never"], 0)
        self.assertEqual(sec["counts"]["stale"], 0)
        # OFF is reported but it is not an alarm.
        self.assertEqual(sec["alarm"], 0)

    def test_failing_is_kept_apart_from_stale(self):
        self.status([self.row("com.x.index", "FAILING", attempt="2026-08-26T03:00:00Z",
                              success="2026-08-25T03:00:00Z", rc=127,
                              detail="rc=127 no such file")], unhealthy=1)
        self.registry(self.reg("com.x.index", budget=36))
        sec = self.clock.read()
        self.assertEqual(self.by_id(sec)["com.x.index"]["state"], "failing")
        self.assertEqual(sec["alarm"], 1)

    def test_a_dead_daemon_is_failing_not_never_fired(self):
        # A daemon is judged by liveness, so it legitimately carries no attempt,
        # no success and no rc. Reading that as "never fired" would send you
        # hunting a bad path in a service that has run for months.
        self.status([self.row("com.x.listener", "FAILING", detail="daemon is not running")],
                    unhealthy=1)
        self.registry(self.reg("com.x.listener", budget=None, kind="daemon"))
        j = self.by_id(self.clock.read())["com.x.listener"]
        self.assertEqual(j["state"], "failing")
        self.assertEqual(j["watch"], "liveness")
        self.assertFalse(j["unwatched"])

    # -- the null budget, which is itself the finding ---------------------------

    def test_a_null_budget_job_is_marked_unwatched(self):
        self.status([self.row("com.x.quiet", "OK", attempt="2026-08-26T01:00:00Z",
                              success="2026-08-26T01:00:00Z", rc=0)])
        self.registry(self.reg("com.x.quiet", budget=None))
        sec = self.clock.read()
        j = self.by_id(sec)["com.x.quiet"]
        self.assertTrue(j["unwatched"])
        self.assertIsNone(j["budget_h"])
        self.assertEqual(j["watch"], "nothing")
        self.assertEqual(sec["unwatched"], 1)
        # It is unwatched AND currently fine. Those are not in conflict, and
        # folding one into the other would lose the finding.
        self.assertEqual(j["state"], "ok")

    def test_a_daemon_with_a_null_budget_is_not_called_unwatched(self):
        self.status([self.row("com.x.daemon", "OK", detail="daemon, pid 4211")])
        self.registry(self.reg("com.x.daemon", budget=None, kind="daemon"))
        sec = self.clock.read()
        self.assertEqual(sec["unwatched"], 0)
        self.assertEqual(self.by_id(sec)["com.x.daemon"]["watch"], "liveness")

    # -- the registry ----------------------------------------------------------

    def test_a_malformed_registry_line_is_reported_not_swallowed(self):
        self.status([self.row("com.x.ok", "OK", attempt="2026-08-26T01:00:00Z",
                              success="2026-08-26T01:00:00Z", rc=0)])
        self.registry(self.reg("com.x.ok"), extra='{"id": "com.x.torn", "command": [')
        sec = self.clock.read()
        self.assertEqual(sec["state"], "ok")
        self.assertEqual(len(sec["registry_bad"]), 1)
        self.assertIn("line 3", sec["registry_bad"][0])
        # And the good line still landed. One bad row is not a bad registry.
        self.assertIn("com.x.ok", self.by_id(sec))

    def test_the_command_and_schedule_come_through_verbatim(self):
        self.status([self.row("com.x.ok", "OK", attempt="2026-08-26T01:00:00Z",
                              success="2026-08-26T01:00:00Z", rc=0)])
        self.registry(self.reg("com.x.ok", kind="calendar",
                               command=["/bin/bash", "/srv/run.sh", "--full"]))
        j = self.by_id(self.clock.read())["com.x.ok"]
        self.assertEqual(j["command"], "/bin/bash /srv/run.sh --full")
        self.assertEqual(j["schedule"], "at 07:30")

    def test_a_job_missing_from_the_registry_is_flagged(self):
        self.status([self.row("com.x.ghost", "OK", attempt="2026-08-26T01:00:00Z",
                              success="2026-08-26T01:00:00Z", rc=0)])
        self.registry()
        sec = self.clock.read()
        self.assertEqual(sec["unregistered"], ["com.x.ghost"])
        self.assertEqual(self.by_id(sec)["com.x.ghost"]["command"], "")

    # -- the source's own health, which must never render as zero ---------------

    def test_a_hanging_jobctl_is_a_timeout_not_an_empty_wall(self):
        self.clock.TIMEOUT_S = 1
        self.jobctl("""
            import time
            time.sleep(30)
        """)
        self.registry()
        sec = self.clock.read()
        self.assertEqual(sec["state"], "timeout")
        self.assertIn("did not answer", sec["detail"])
        self.assertNotIn("jobs", sec)

    def test_a_nonzero_exit_is_still_read_because_unhealthy_exits_one(self):
        self.status([self.row("com.x.swarm", "STALE", attempt="2026-08-26T01:00:00Z",
                              success="2026-08-16T01:00:00Z", rc=2)], unhealthy=1)
        self.registry(self.reg("com.x.swarm", budget=18))
        sec = self.clock.read()
        self.assertEqual(sec["state"], "ok")
        self.assertEqual(sec["checked"], 1)

    def test_garbage_on_stdout_is_unreadable_not_empty(self):
        self.jobctl("""
            import sys
            sys.stdout.write("Traceback (most recent call last):")
            sys.exit(1)
        """)
        self.registry()
        sec = self.clock.read()
        self.assertEqual(sec["state"], "unreadable")
        self.assertTrue(sec["detail"])

    def test_no_jobctl_says_so_rather_than_showing_nothing_scheduled(self):
        self.registry()
        sec = self.clock.read()
        self.assertEqual(sec["state"], "missing")
        self.assertIn("jobctl", sec["detail"])

    def test_an_unconfigured_root_is_its_own_state(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        from sources import clock  # noqa: PLC0415
        c = importlib.reload(clock)
        self.assertEqual(c.read()["state"], "unconfigured")

    def test_genuinely_zero_jobs_is_ok_and_empty_not_broken(self):
        # The other half of the same rule: "nothing is scheduled" is a real,
        # healthy answer and must be distinguishable from every failure above.
        self.status([])
        self.registry()
        sec = self.clock.read()
        self.assertEqual(sec["state"], "ok")
        self.assertEqual(sec["checked"], 0)
        self.assertEqual(sec["alarm"], 0)
        self.assertEqual(sec["jobs"], [])

    # -- ordering, so the alarm is never below the fold -------------------------

    def test_faults_sort_above_off_and_ok(self):
        self.status([
            self.row("com.x.a-ok", "OK", attempt="2026-08-26T01:00:00Z",
                     success="2026-08-26T01:00:00Z", rc=0),
            self.row("com.x.b-off", "OFF", attempt="2026-08-26T01:00:00Z", rc=0),
            self.row("com.x.c-stale", "STALE", attempt="2026-08-26T01:00:00Z",
                     success="2026-08-01T01:00:00Z", rc=1),
        ], unhealthy=1, off=1)
        self.registry(self.reg("com.x.a-ok"), self.reg("com.x.b-off"),
                      self.reg("com.x.c-stale"))
        states = [j["state"] for j in self.clock.read()["jobs"]]
        self.assertEqual(states, ["stale", "off", "ok"])


class CardTest(ClockBase):
    """The card is the only thing most people will ever read off this source, so
    the number on it has to be the same alarm the wall is sorted by."""

    def card(self):
        return self.clock.card(self.clock.read())

    def test_the_card_says_how_many_jobs_need_a_look(self):
        self.status([
            self.row("good", "OK", attempt="2026-08-26T05:00:00Z",
                     success="2026-08-26T05:00:00Z", rc=0),
            self.row("fine", "OK", attempt="2026-08-26T06:00:00Z",
                     success="2026-08-26T06:00:00Z", rc=0),
            self.row("stopped", "STALE", attempt="2026-08-20T05:00:00Z",
                     success="2026-08-20T05:00:00Z", rc=0),
        ], unhealthy=1)
        self.registry(self.reg("good"), self.reg("fine"), self.reg("stopped"))

        card = self.card()
        assert_card(self, card)
        self.assertEqual(card["headline"], "1 of 3 jobs needs a look")
        self.assertEqual(card["needs"], 1)
        # The freshest attempt any job made, not the moment the card was built.
        self.assertEqual(card["as_of"], "2026-08-26T06:00:00Z")
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertEqual(facts["ok"], "2")
        self.assertEqual(facts["stale"], "1")

    def test_a_wall_with_nothing_wrong_says_so_rather_than_going_quiet(self):
        self.status([self.row("good", "OK", attempt="2026-08-26T05:00:00Z",
                              success="2026-08-26T05:00:00Z", rc=0)])
        self.registry(self.reg("good"))

        card = self.card()
        assert_card(self, card)
        self.assertEqual(card["headline"], "1 job, all fine")
        self.assertEqual(card["needs"], 0)

    def test_a_switched_off_job_is_counted_and_never_leads(self):
        self.status([self.row("paused", "OFF"),
                     self.row("good", "OK", attempt="2026-08-26T05:00:00Z",
                              success="2026-08-26T05:00:00Z", rc=0)], off=1)
        self.registry(self.reg("paused"), self.reg("good"))

        card = self.card()
        self.assertEqual(card["needs"], 0)
        self.assertEqual(card["headline"], "2 jobs, all fine")
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertEqual(facts["off"], "1")

    def test_a_broken_jobctl_card_says_what_is_wrong_and_wants_a_person(self):
        # No jobctl installed at all: the source cannot tell, and the card has
        # to say so where the count would have gone.
        card = self.card()
        assert_card(self, card)
        self.assertIn("jobctl", card["headline"])
        self.assertEqual(card["needs"], 1)


if __name__ == "__main__":
    unittest.main()
