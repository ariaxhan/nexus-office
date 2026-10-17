"""#303: the pile alarm emits stuck-set records; the intake machine re-dispatches; no issue is filed."""
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client'))
sys.path.insert(0, str(ROOT))
import office_work  # noqa: E402
import pile_alarm  # noqa: E402
from nexus import intake, stuck  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402

H = 3600
NOW = intake.SINCE + 100 * H
COORDS = [{'id': 'tbs', 'repo': 'o/r'}]


def row(number, idle_h, title='probe'):
    return {'repo': 'o/r', 'number': number, 'title': title, 'activity_at': None,
            'created_at': office_work.iso(NOW - idle_h * H)}


def work(*stalled, extra=()):
    return {'built_at': office_work.iso(NOW), 'desks': [{'id': 'tbs', 'repos': ['o/r', 'o/quiet'],
            'stalled': list(stalled) + list(extra), 'worked_on': [], 'up_next': []}]}


class NoCreate:
    """GitHub that refuses to create anything: the alarm may only retire legacy filings."""

    def __init__(self, legacy=()):
        self.legacy, self.closed, self.calls = list(legacy), [], []

    def open_alarms(self, repo):
        return [n for r, n in self.legacy if r == repo]

    def close(self, repo, number, comment):
        self.closed.append((repo, number))

    def __getattr__(self, name):
        raise AssertionError(f'pile alarm called gh.{name}')


class PileAlarmRecords(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='pile-alarm-')
        self.led = Ledger(str(Path(self.dir) / 'ledger.sqlite'))
        self.plan = self.led.add_plan('tbs-work')

    def tearDown(self):
        self.led.close()

    def tick(self, w, now=NOW, gh=None):
        return pile_alarm.tick(w, self.led, COORDS, self.dir, gh=gh or NoCreate(), now=now)

    def events(self, kind):
        return self.led.conn.execute('SELECT subject, payload FROM events WHERE kind=? ORDER BY id',
                                     (kind,)).fetchall()

    def test_synthetic_stuck_set_becomes_records_and_redispatches_owner_with_no_issue(self):
        gh = NoCreate()
        receipt = self.tick(work(row(7, 30), row(8, 40)), gh=gh)
        recs = {s: json.loads(p) for s, p in self.events('intake.stuck')}
        self.assertEqual(set(recs), {'github:o/r#7', 'github:o/r#8'})
        self.assertEqual(recs['github:o/r#7'], {'repo': 'o/r', 'issue': 7, 'filed_at': office_work.iso(NOW - 30 * H),
                                                'age_s': 30 * H, 'state': 'stalled', 'owner': 'tbs-work'})
        tasks = self.led.conn.execute("SELECT * FROM tasks WHERE origin='intake-stuck'").fetchall()
        self.assertEqual(sorted(t['dedupe_key'] for t in tasks), ['stuck:github:o/r#7', 'stuck:github:o/r#8'])
        self.assertTrue(all(t['plan_id'] == self.plan for t in tasks))
        self.assertEqual(len(receipt['intake']['redispatched']), 2)
        self.assertEqual(receipt['records'], [{**json.loads(p)} for _, p in self.events('intake.stuck')])
        self.assertEqual(gh.closed, [])
        log = (Path(self.dir) / 'pile-alarm.jsonl').read_text().splitlines()
        self.assertEqual(len(log), 1)

    def test_repeat_runs_record_once_and_redispatch_within_bounds(self):
        w = work(row(7, 30))
        self.tick(w)
        self.tick(w, now=NOW + 900)
        self.assertEqual(len(self.events('intake.stuck')), 1)
        self.assertEqual(len(self.events('intake.redispatch')), 1)
        self.assertEqual(intake.sweep(self.led, NOW + 1800, force=True, claims={})['stuck']['redispatched'], [])
        # the owner's task finished and the item is still stuck a lane bound later: dispatched again
        task = self.led.conn.execute("SELECT id FROM tasks WHERE origin='intake-stuck'").fetchone()['id']
        self.led.conn.execute("UPDATE tasks SET state='done' WHERE id=?", (task,))
        later = NOW + stuck.REDISPATCH_S + 60
        out = intake.sweep(self.led, later, force=True, claims={})['stuck']
        self.assertEqual(len(out['redispatched']), 1)
        self.assertNotEqual(out['redispatched'][0]['task'], task)

    def test_item_leaving_the_set_is_recorded_and_no_longer_dispatched(self):
        self.tick(work(row(7, 30)))
        receipt = self.tick(work(), now=NOW + 900)
        self.assertEqual(receipt['intake']['left'], 1)
        self.assertEqual([s for s, _ in self.events('intake.unstuck')], ['github:o/r#7'])
        self.assertEqual(stuck.current(self.led), {})

    def test_a_backlog_reaches_its_owner_a_few_live_tasks_at_a_time(self):
        self.tick(work(*(row(n, 30) for n in range(1, 11))))
        intake.sweep(self.led, NOW + 60, force=True, claims={})
        live = self.led.conn.execute("SELECT COUNT(*) FROM tasks WHERE origin='intake-stuck'").fetchone()[0]
        self.assertEqual(live, stuck.MAX_LIVE_PER_OWNER)
        self.assertEqual(len(self.events('intake.stuck')), 10)

    def test_young_or_unowned_records_are_kept_not_dispatched(self):
        young = stuck.ingest(self.led, [{'repo': 'o/r', 'issue': 1, 'age_s': 600, 'owner': 'tbs-work'},
                                        {'repo': 'o/x', 'issue': 2, 'age_s': 30 * H, 'owner': None}], NOW)
        self.assertEqual((young['entered'], young['redispatched'], young['unowned']), (2, [], 1))

    def test_legacy_text_filings_are_closed_never_created(self):
        gh = NoCreate(legacy=[('o/r', 292)])
        receipt = self.tick(work(row(7, 30), extra=[{**row(294, 30), 'title': '[pile-alarm] o/r: 1'}]), gh=gh)
        self.assertEqual(sorted(gh.closed), [('o/r', 292), ('o/r', 294)])
        self.assertNotIn('github:o/r#294', dict(self.events('intake.stuck')))
        self.assertEqual(len(receipt['retired']), 2)


if __name__ == '__main__':
    unittest.main()
