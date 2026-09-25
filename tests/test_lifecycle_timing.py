"""Disposable v1 lifecycle fixtures; never touches the live Nexus ledger."""

import json
import sqlite3
import unittest

from nexus.lifecycle_timing import project, read_events, summarize


def load_tests(_loader, _tests, _pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in globals().items()
                              if name.startswith("test_") and callable(fn))


class Fixture:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, subject TEXT, payload TEXT)")
        self.number = 0

    def emit(self, kind, at, *, work="github:sample/app#1", revision="rev-1", **fields):
        self.number += 1
        payload = dict(schema="office.lifecycle.v1", work_key=work, task_id="task-1",
                       source_revision=revision, transition_key=f"transition-{self.number}",
                       at=at, **fields)
        cursor = self.db.execute("INSERT INTO events(ts,kind,subject,payload) VALUES (?,?,?,?)",
                                 (at, kind, work, json.dumps(payload, sort_keys=True)))
        return cursor.lastrowid

    def replay(self, event_id, *, conflicting=False):
        row = self.db.execute("SELECT ts,kind,subject,payload FROM events WHERE id=?", (event_id,)).fetchone()
        payload = json.loads(row[3])
        if conflicting:
            payload["at"] += 1
        self.db.execute("INSERT INTO events(ts,kind,subject,payload) VALUES (?,?,?,?)",
                        (*row[:3], json.dumps(payload, sort_keys=True)))

    def result(self, as_of=1000):
        return project(read_events(self.db), as_of=as_of)


def successful(f, *, work="github:sample/app#1", revision="rev-1", start=0,
               attempt="attempt-1", previous=None, verified=True):
    f.emit("lifecycle.recognized", start, work=work, revision=revision,
           actionable_at=start - 10, actionable_status="known")
    f.emit("lifecycle.selected", start + 10, work=work, revision=revision,
           attempt_id=attempt, previous_attempt_id=previous)
    f.emit("lifecycle.queued", start + 12, work=work, revision=revision,
           attempt_id=attempt, flight_id="flight-" + attempt)
    f.emit("lifecycle.execution_started", start + 15, work=work, revision=revision,
           attempt_id=attempt, flight_id="flight-" + attempt)
    f.emit("lifecycle.verification_started", start + 20, work=work, revision=revision,
           attempt_id=attempt, verification_id="verify-" + attempt, exact_head="abc")
    f.emit("lifecycle.verification_finished", start + 25, work=work, revision=revision,
           attempt_id=attempt, verification_id="verify-" + attempt, result="passed",
           exact_head="abc", proof_ref="receipt-" + attempt)
    f.emit("lifecycle.attempt_finished", start + 30, work=work, revision=revision,
           attempt_id=attempt, outcome="landed")
    if verified:
        event_id = f.emit("lifecycle.verified", start + 35, work=work, revision=revision,
                          attempt_id=attempt, verification_id="verify-" + attempt,
                          exact_head="abc", proof_ref="receipt-" + attempt)
        f.emit("lifecycle.office_published", start + 40, work=work, revision=revision,
               attempt_id=attempt, verified_event_id=event_id, office_revision="office-1")
    return f


def test_success_has_adjacent_edges_and_explicit_unclassified_gap():
    row = successful(Fixture()).result()["completed"][0]
    assert row["attempt_ids"] == ["attempt-1"]
    assert row["elapsed_s"] == 35
    assert row["edges"]["actionable_to_recognized"] == 10
    assert row["edges"]["recognized_to_selected"] == 10
    assert row["edges"]["selected_to_queued"] == 2
    assert row["edges"]["queued_to_execution"] == 3
    assert row["edges"]["execution_elapsed"] == 15
    assert row["edges"]["verification_elapsed"] == 5
    assert row["edges"]["verified_to_office"] == 5
    assert row["unclassified_s"] == 5  # finished→verified has no named phase
    assert sum(row["exclusive_s"].values()) == 35
    assert row["errors"] == []


def test_retry_and_replacement_use_explicit_parent_chain():
    f = Fixture()
    f.emit("lifecycle.recognized", 0, actionable_at=None, actionable_status="left_censored")
    f.emit("lifecycle.selected", 1, attempt_id="first", previous_attempt_id=None)
    f.emit("lifecycle.queued", 2, attempt_id="first")
    f.emit("lifecycle.execution_started", 3, attempt_id="first")
    f.emit("lifecycle.attempt_finished", 8, attempt_id="first", outcome="failed")
    f.emit("lifecycle.wait_started", 8, attempt_id="first", wait_id="retry", wait_kind="backoff",
           reason_code="check_failed", eligible_at=18)
    f.emit("lifecycle.selected", 22, attempt_id="second", previous_attempt_id="first", resume_reason="retry")
    f.emit("lifecycle.queued", 23, attempt_id="second")
    f.emit("lifecycle.execution_started", 24, attempt_id="second")
    f.emit("lifecycle.attempt_finished", 26, attempt_id="second", outcome="replaced")
    f.emit("lifecycle.selected", 27, attempt_id="third", previous_attempt_id="second", resume_reason="replacement")
    f.emit("lifecycle.queued", 28, attempt_id="third")
    f.emit("lifecycle.execution_started", 29, attempt_id="third")
    f.emit("lifecycle.verification_started", 30, attempt_id="third", verification_id="v", exact_head="head")
    f.emit("lifecycle.verification_finished", 31, attempt_id="third", verification_id="v",
           result="passed", exact_head="head", proof_ref="proof")
    f.emit("lifecycle.attempt_finished", 32, attempt_id="third", outcome="landed")
    f.emit("lifecycle.verified", 33, attempt_id="third", verification_id="v",
           exact_head="head", proof_ref="proof")
    row = f.result()["completed"][0]
    assert row["attempt_ids"] == ["first", "second", "third"]
    assert row["raw_wait_s"]["backoff"] == 10
    assert row["unclassified_s"] >= 4  # eligible→next selection was not labeled active
    assert row["completeness"]["actionable_to_recognized"] == "left_censored"
    assert row["errors"] == []


def test_held_failed_work_stays_in_open_backlog_not_completed_percentiles():
    f = Fixture()
    f.emit("lifecycle.recognized", 100, work="github:sample/app#2", actionable_at=None)
    f.emit("lifecycle.selected", 110, work="github:sample/app#2", attempt_id="held")
    f.emit("lifecycle.attempt_finished", 120, work="github:sample/app#2", attempt_id="held", outcome="HELD")
    f.emit("lifecycle.wait_started", 120, work="github:sample/app#2", attempt_id="held",
           wait_id="permission", wait_kind="human", reason_code="approval")
    f.emit("lifecycle.recognized", 200, work="github:sample/app#3", actionable_at=None)
    f.emit("lifecycle.selected", 210, work="github:sample/app#3", attempt_id="failed")
    f.emit("lifecycle.attempt_finished", 220, work="github:sample/app#3", attempt_id="failed", outcome="failed")
    result = f.result(as_of=300)
    assert result["completed"] == []
    assert {r["work_key"] for r in result["open_backlog"]} == {"github:sample/app#2", "github:sample/app#3"}
    held = next(r for r in result["open_backlog"] if r["work_key"].endswith("#2"))
    assert held["open_waits"] == [{"wait_id": "permission", "kind": "human", "age_s": 180, "eligible_at": None}]


def test_overlapping_waits_are_raw_and_exclusive_without_double_counting():
    f = successful(Fixture())
    f.emit("lifecycle.wait_started", 16, attempt_id="attempt-1", wait_id="h", wait_kind="human")
    f.emit("lifecycle.wait_ended", 22, attempt_id="attempt-1", wait_id="h", wait_kind="human")
    f.emit("lifecycle.wait_started", 18, attempt_id="attempt-1", wait_id="s", wait_kind="safety_gate")
    f.emit("lifecycle.wait_ended", 24, attempt_id="attempt-1", wait_id="s", wait_kind="safety_gate")
    row = f.result()["completed"][0]
    assert row["raw_wait_s"] == {"human": 6.0, "safety_gate": 6.0}
    assert row["exclusive_s"]["human"] == 6
    assert row["exclusive_s"]["safety_gate"] == 2
    assert row["overlap_s"] >= 4
    assert sum(row["exclusive_s"].values()) == row["elapsed_s"]


def test_missing_timestamps_and_open_intervals_are_not_filled_from_next_event():
    f = Fixture()
    f.emit("lifecycle.recognized", 0, actionable_at=None, actionable_status="left_censored")
    f.emit("lifecycle.selected", 5, attempt_id="a")
    f.emit("lifecycle.execution_started", 10, attempt_id="a")  # queued is absent
    f.emit("lifecycle.wait_started", 11, attempt_id="a", wait_id="external", wait_kind="external")
    f.emit("lifecycle.verification_started", 20, attempt_id="a", verification_id="v", exact_head="h")
    f.emit("lifecycle.verification_finished", 25, attempt_id="a", verification_id="v",
           result="passed", exact_head="h", proof_ref="p")
    f.emit("lifecycle.attempt_finished", 30, attempt_id="a", outcome="landed")
    f.emit("lifecycle.verified", 35, attempt_id="a", verification_id="v", exact_head="h", proof_ref="p")
    row = f.result()["completed"][0]
    assert row["edges"]["queued_to_execution"] is None
    assert row["completeness"]["queued_to_execution"] == "not_emitted"
    assert row["open_intervals"] == [{"kind": "external", "id": "external", "started_at": 11}]
    assert row["unclassified_s"] > 0


def test_replayed_events_are_deduped_and_conflicts_flagged():
    f = successful(Fixture())
    f.replay(1)
    first = f.result()
    assert len(first["completed"]) == 1
    assert first["data_quality"] == []
    f.replay(1, conflicting=True)
    assert f.result()["data_quality"][0]["reason"] == "conflicting_replay"


def test_clock_reversal_and_failed_proof_do_not_become_fast_success():
    f = successful(Fixture())
    f.db.execute("UPDATE events SET ts=9, payload=json_set(payload,'$.at',9) WHERE kind='lifecycle.queued'")
    result = f.result()
    assert result["completed"] == []
    row = result["invalid_results"][0]
    assert "clock_invalid:selection" in row["errors"]
    assert row["elapsed_s"] is None
    g = successful(Fixture())
    g.db.execute("UPDATE events SET payload=json_set(payload,'$.result','failed') "
                 "WHERE kind='lifecycle.verification_finished'")
    result = g.result()
    assert result["completed"] == []
    invalid = result["invalid_results"][0]
    assert "verified_without_passing_verification" in invalid["errors"]
    assert invalid["elapsed_s"] is None


def test_seven_day_window_and_summary_completeness():
    f = successful(Fixture())
    successful(f, work="github:sample/app#2", revision="rev-2", start=900000,
               attempt="attempt-2")
    result = f.result(as_of=900040)
    assert len(result["completed"]) == 1
    stats = summarize(result["completed"], "verified_to_office")
    assert stats == dict(count=1, applicable=1, completeness=1.0,
                         p50=5, p90=5, p99=5, worst=5, total_s=5, missing={})


def test_backlog_post_eligibility_age_is_separate_from_intentional_backoff():
    f = Fixture()
    f.emit("lifecycle.recognized", 0, actionable_at=0)
    f.emit("lifecycle.selected", 2, attempt_id="a")
    f.emit("lifecycle.attempt_finished", 5, attempt_id="a", outcome="failed")
    f.emit("lifecycle.wait_started", 5, attempt_id="a", wait_id="backoff",
           wait_kind="backoff", eligible_at=15)
    row = f.result(as_of=30)["open_backlog"][0]
    assert row["age_s"] == 30
    assert row["eligible_at"] == 15
    assert row["post_eligibility_age_s"] == 15
    assert row["open_waits"] == []


def test_structured_source_revision_and_resource_missingness():
    f = Fixture()
    revision = {"github_updated_at": "2026-09-25T00:00:00Z", "eligibility_digest": "abc"}
    successful(f, revision=revision)
    result = f.result()
    assert len(result["completed"]) == 1
    assert result["open_backlog"] == []
    assert result["completed"][0]["worker_wall_s"] is None  # missing cost is not zero
    f.db.execute("UPDATE events SET payload=json_set(payload,'$.worker_wall_s',7.5) "
                 "WHERE kind='lifecycle.attempt_finished'")
    assert f.result()["completed"][0]["worker_wall_s"] == 7.5


def test_contract_rejects_missing_identity_and_preserves_data_quality():
    f = successful(Fixture())
    f.db.execute("UPDATE events SET payload=json_remove(payload,'$.task_id') WHERE kind='lifecycle.queued'")
    result = f.result()
    assert result["data_quality"] == [{"event_id": 3, "reason": "invalid_contract"}]
    assert result["completed"][0]["completeness"]["queued_to_execution"] == "not_emitted"


def test_invalid_verification_does_not_skew_completed_summary():
    f = successful(Fixture())
    successful(f, work="github:sample/app#2", revision="rev-2", start=100, attempt="b")
    f.db.execute("UPDATE events SET payload=json_set(payload,'$.proof_ref','wrong') "
                 "WHERE kind='lifecycle.verified' AND subject='github:sample/app#2'")
    result = f.result()
    assert len(result["completed"]) == 1
    assert len(result["invalid_results"]) == 1
    assert result["invalid_results"][0]["errors"] == ["proof_mismatch"]
    assert {r["work_key"] for r in result["open_backlog"]} == {"github:sample/app#2"}
    assert summarize(result["completed"], "verified_to_office")["count"] == 1


def test_revision_change_requires_explicit_source_change_link():
    f = successful(Fixture())
    f.emit("lifecycle.selected", -2, revision="older", attempt_id="old",
           previous_attempt_id=None)
    f.db.execute("UPDATE events SET payload=json_set(payload,'$.previous_attempt_id','old') "
                 "WHERE kind='lifecycle.selected' AND json_extract(payload,'$.attempt_id')='attempt-1'")
    result = f.result()
    assert result["completed"] == []
    assert "unexplained_source_revision_change" in result["invalid_results"][0]["errors"]
    f.db.execute("UPDATE events SET payload=json_set(payload,'$.resume_reason','source_change') "
                 "WHERE kind='lifecycle.selected' AND json_extract(payload,'$.attempt_id')='attempt-1'")
    assert len(f.result()["completed"]) == 1
