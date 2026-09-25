"""Disposable producer→ledger→reducer→Office API exposure proof."""

import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.ledger import Ledger
from nexus import work
from nexus.lifecycle_timing import project, read_events


ISSUE = {"number": 42, "title": "Deliver", "state": "open", "updated_at": "2026-09-25T21:00:00Z",
         "body": "", "labels": [{"name": "ready"}]}


def load_tests(_loader, _tests, _pattern):
    def run(fn):
        with tempfile.TemporaryDirectory() as directory:
            with _patches() as monkeypatch:
                if fn.__code__.co_argcount == 2:
                    fn(Path(directory), monkeypatch)
                else:
                    fn(Path(directory))
    return unittest.TestSuite(unittest.FunctionTestCase(lambda fn=fn: run(fn))
                              for name, fn in globals().items()
                              if name.startswith("test_") and callable(fn))


class _patches:
    def __init__(self):
        self.active = []

    def __enter__(self):
        return self

    def setattr(self, target, name, value):
        replacement = patch.object(target, name, value)
        replacement.start()
        self.active.append(replacement)

    def __exit__(self, *_):
        for replacement in reversed(self.active):
            replacement.stop()


def test_tower_observations_join_source_flight_proof_and_office_api(tmp_path, monkeypatch):
    from client import office_timing
    import run_board
    from nexus import tower, contract

    path = tmp_path / "ledger.sqlite"
    led = Ledger(str(path))
    monkeypatch.setattr(work.flights, "alive", lambda pid: True)
    tid = work.capture(led, "sample/app", ISSUE)
    fid = work.claim(led, "sample/app", 42, 12345, runner=True)
    selected = led.conn.execute("SELECT payload FROM events WHERE kind='lifecycle.selected'").fetchone()
    assert json.loads(selected[0])["source_revision"]["github_updated_at"] == ISSUE["updated_at"]
    from nexus import lifecycle_observe
    lifecycle_observe.for_flight(led, fid, "lifecycle.execution_started", runner_kind="tower")

    def terminal(ledger, flight_id, repo, result):
        ledger.set_state(flight_id, "produced", source="fixture")
        ledger.set_state(flight_id, "verified", source="fixture")

    monkeypatch.setattr(tower, "land_write_flight", terminal)
    monkeypatch.setattr(contract, "done_receipt", lambda result, contract_, repo: (True, "fixture proof"))
    monkeypatch.setattr(work, "close_issue", lambda ledger, payload: None)
    result = {"state": "LANDED", "sha": "a" * 40, "flight": fid}
    assert work._settle(led, fid, {"repo": "sample/app", "path": str(tmp_path)},
                        {"id": tid}, ISSUE, result) == "done"
    monkeypatch.setattr(run_board, "LEDGER", path)
    body = {"items": [{"id": fid, "state": "landed", "plan": "github-work"}]}
    assert office_timing.observe_runs(body, "b" * 40) == 1
    assert office_timing.observe_runs(body, "b" * 40) == 0
    projection = project(read_events(led.conn), as_of=time.time() + 1)
    assert projection["invalid_results"] == []
    assert len(projection["completed"]) == 1
    row = projection["completed"][0]
    assert row["attempt_ids"] == [fid]
    assert row["proof_ref"].startswith("nexus:event:")
    assert row["edges"]["verified_to_office"] >= 0
    assert row["unclassified_s"] is not None
    assert row["completeness"]["actionable_to_recognized"] == "left_censored"
    led.close()


def test_gate_wait_is_deduplicated_and_closed_only_when_cleared(tmp_path):
    from nexus import lifecycle_observe
    led = Ledger(str(tmp_path / "ledger.sqlite"))
    tid = work.capture(led, "sample/app", ISSUE)
    task = dict(led.conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    reason = "blocked by open dependency sample/app#2"
    lifecycle_observe.gate_state(led, task, ISSUE, reason)
    lifecycle_observe.gate_state(led, task, ISSUE, reason)
    assert len(led.events(kind="lifecycle.wait_started")) == 1
    payload = json.loads(led.events(kind="lifecycle.wait_started")[0]["payload"])
    assert payload["wait_kind"] == "dependency"
    assert payload["attempt_id"] is None
    lifecycle_observe.gate_state(led, task, ISSUE, None)
    assert len(led.events(kind="lifecycle.wait_ended")) == 1
    led.close()


def test_malformed_issue_never_breaks_capture(tmp_path):
    led = Ledger(str(tmp_path / "ledger.sqlite"))
    issue = {"number": 7, "title": "No state", "labels": [None]}  # eligibility and revision both raise
    assert work.capture(led, "sample/app", issue)
    assert not led.conn.execute("SELECT 1 FROM events WHERE kind LIKE 'lifecycle.%'").fetchone()
