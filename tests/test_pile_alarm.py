import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import pile_alarm


def item(repo, number, title='t', activity=None, created='2026-10-01T00:00:00Z'):
    return {'repo': repo, 'number': number, 'title': title, 'url': f'https://x/{number}',
            'activity_at': activity, 'created_at': created}


def work(stalled=(), up_next=(), repos=('a/one', 'b/two')):
    return {'built_at': '2026-10-15T00:00:00Z', 'desks': [
        {'id': 'tbs', 'repos': list(repos), 'stalled': list(stalled), 'up_next': list(up_next),
         'worked_on': [], 'done': [], 'errors': []}]}


class Gh:
    def __init__(self):
        self.calls = []
        self.next = 100
        self.open = {}  # repo -> set of open alarm numbers, what GitHub itself says

    def open_alarms(self, repo):
        return sorted(self.open.get(repo, ()))

    def create(self, repo, title, body):
        self.calls.append(('create', repo, title, body))
        self.next += 1
        self.open.setdefault(repo, set()).add(self.next)
        return self.next

    def edit(self, repo, number, title, body):
        self.calls.append(('edit', repo, number, title, body))

    def close(self, repo, number, comment):
        self.calls.append(('close', repo, number, comment))
        self.open.get(repo, set()).discard(number)


class Tower:
    def __init__(self, plans=('tbs-work',)):
        self.plans = set(plans)
        self.tasks = []

    def dispatch(self, plan, repo, issue, stuck):
        if plan not in self.plans:
            return None
        self.tasks.append((plan, repo, issue, len(stuck)))
        return f'task_{len(self.tasks)}'


class PileTest(unittest.TestCase):
    def test_counts_every_repo_and_only_stalled_items(self):
        counts, stuck, alarms = pile_alarm.piles(work(
            stalled=[item('a/one', 1)], up_next=[item('a/one', 2)]))
        self.assertEqual(counts, {'a/one': 1, 'b/two': 0})
        self.assertEqual([r['number'] for r in stuck['a/one']], [1])
        self.assertEqual(alarms, {})

    def test_alarm_issue_never_counts_itself(self):
        counts, _, alarms = pile_alarm.piles(work(
            stalled=[item('a/one', 9, '[pile-alarm] a/one: 1 stuck')]))
        self.assertEqual(counts['a/one'], 0)
        self.assertEqual(alarms, {'a/one': 9})

    def test_owner_plan_is_the_desk_coordinators_work_plan(self):
        coords = [{'id': 'tbs', 'repo': 'b/two'}]
        self.assertEqual(pile_alarm.owner_plan('a/one', work(), coords), 'tbs-work')
        self.assertIsNone(pile_alarm.owner_plan('a/one', work(), []))


class TickTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.gh, self.tower = Gh(), Tower()
        self.coords = [{'id': 'tbs', 'repo': 'a/one'}]

    def tick(self, w, now=1000.0):
        return pile_alarm.tick(w, self.gh, self.tower, self.coords, self.dir, now)

    def receipts(self):
        return [json.loads(l) for l in (self.dir / 'pile-alarm.jsonl').read_text().splitlines()]

    def test_nonzero_count_files_one_issue_and_one_task(self):
        receipt = self.tick(work(stalled=[item('a/one', 1, activity='2026-10-10T00:00:00Z')]))
        self.assertEqual(receipt['counts'], {'a/one': 1, 'b/two': 0})
        self.assertEqual([c[0] for c in self.gh.calls], ['create'])
        _, repo, title, body = self.gh.calls[0]
        self.assertEqual(repo, 'a/one')
        self.assertTrue(title.startswith('[pile-alarm] a/one'))
        self.assertIn('#1', body)
        self.assertIn('stalled', body)
        self.assertEqual(self.tower.tasks, [('tbs-work', 'a/one', 101, 1)])
        self.assertEqual(receipt['dispatched'][0]['issue'], 101)
        self.assertEqual(receipt['dispatched'][0]['task'], 'task_1')
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(json.loads((self.dir / 'pile-alarm.json').read_text())['at'], receipt['at'])

    def test_dedupes_onto_the_open_alarm_and_edits_only_on_change(self):
        stuck = [item('a/one', 1)]
        self.tick(work(stalled=stuck))
        alarm = item('a/one', 101, '[pile-alarm] a/one: 1 stuck')
        self.tick(work(stalled=stuck, up_next=[alarm]), now=2000.0)
        self.assertEqual([c[0] for c in self.gh.calls], ['create'])
        self.tick(work(stalled=stuck + [item('a/one', 2)], up_next=[alarm]), now=3000.0)
        self.assertEqual([c[0] for c in self.gh.calls], ['create', 'edit'])
        self.assertEqual(self.gh.calls[1][3], '[pile-alarm] a/one: 2 stuck issue(s)')
        self.assertEqual(len(self.receipts()), 3)

    def test_zero_count_closes_the_open_alarm(self):
        alarm = item('a/one', 101, '[pile-alarm] a/one: 1 stuck')
        receipt = self.tick(work(up_next=[alarm]))
        self.assertEqual(receipt['counts']['a/one'], 0)
        self.assertEqual(self.gh.calls[0][:3], ('close', 'a/one', 101))
        self.assertEqual(receipt['cleared'], [{'repo': 'a/one', 'issue': 101}])

    def test_alarm_missing_from_the_work_view_is_still_not_refiled(self):
        # nexus-office#290: #292 then #294 - the work view had not caught up with
        # the issue the previous run filed, so the run filed a second one.
        stuck = [item('a/one', 1)]
        self.tick(work(stalled=stuck))
        receipt = self.tick(work(stalled=stuck), now=2000.0)
        self.assertEqual([c[0] for c in self.gh.calls], ['create'])
        self.assertEqual(receipt['dispatched'], [])
        self.assertEqual(len(self.tower.tasks), 1)

    def test_memo_remembers_the_issue_when_github_search_lags(self):
        stuck = [item('a/one', 1)]
        self.tick(work(stalled=stuck))
        self.gh.open_alarms = lambda repo: []
        self.gh.is_open = lambda repo, n: True
        self.tick(work(stalled=stuck), now=2000.0)
        self.assertEqual([c[0] for c in self.gh.calls], ['create'])

    def test_existing_duplicates_collapse_onto_the_oldest(self):
        self.gh.open = {'a/one': {292, 294}}
        receipt = self.tick(work(stalled=[item('a/one', 1)]))
        self.assertNotIn('create', [c[0] for c in self.gh.calls])
        self.assertEqual([c[:3] for c in self.gh.calls if c[0] == 'close'], [('close', 'a/one', 294)])
        self.assertIn('#292', self.gh.calls[-1][3] if self.gh.calls[-1][0] == 'close' else
                      [c for c in self.gh.calls if c[0] == 'close'][0][3])
        self.assertEqual(receipt['duplicates'], [{'repo': 'a/one', 'issue': 294, 'kept': 292}])

    def test_zero_count_closes_every_open_alarm_github_knows(self):
        self.gh.open = {'a/one': {292, 294}}
        self.tick(work())
        self.assertEqual(sorted(c[2] for c in self.gh.calls if c[0] == 'close'), [292, 294])

    def test_repo_without_coordinator_plan_gets_issue_only(self):
        self.coords = []
        receipt = self.tick(work(stalled=[item('b/two', 5)]))
        self.assertEqual(self.tower.tasks, [])
        self.assertEqual(receipt['dispatched'], [{'repo': 'b/two', 'issue': 101, 'plan': None, 'task': None}])

    def test_a_failed_dispatch_is_a_receipt_not_a_crash(self):
        def boom(*a):
            raise ValueError('gh down')
        self.gh.create = boom
        receipt = self.tick(work(stalled=[item('a/one', 1)]))
        self.assertIn('gh down', receipt['errors'][0])
        self.assertEqual(len(self.receipts()), 1)


if __name__ == '__main__':
    unittest.main()
