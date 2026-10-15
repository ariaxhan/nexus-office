"""Pile alarm (nexus-office#290): on a timer, count each repo's stuck issues and dispatch the repair.

Stuck = the Watch tab's own STALLED bucket (office_work.classify): open, and no
claim or receipt movement inside office_work.WINDOW. One classifier, read from
the door's /api/watch, never a second one here. Until the intake state machine
lands its per-state windows, that 24 h rule is the window.

Count > 0 for a repo: one deduped `[pile-alarm]` issue in that repo naming every
stuck item and its state (edited only when the set changes), and a Tower task
on the owning coordinator's `<id>-work` plan where that plan exists. Count back
to 0: the alarm issue is closed with a comment. Open alarms are read from
GitHub itself each run, so a second one is never filed and any found are closed
as duplicates of the oldest. Every run appends a receipt to
pile-alarm.jsonl and rewrites pile-alarm.json; a run with no receipt did not
happen. Nobody has to notice first.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import office_work

PREFIX = '[pile-alarm]'
DOOR = os.environ.get('OFFICE_DOOR', 'http://127.0.0.1:8790')
STATE = Path(os.environ.get('PILE_ALARM_STATE', Path.home() / '.local/state/nexus-office'))
HERE = Path(__file__).resolve().parent


def piles(work):
    """({repo: count}, {repo: [stuck items]}, {repo: open alarm issue number})."""
    counts, stuck, alarms = {}, {}, {}
    for desk in work.get('desks') or []:
        for repo in desk.get('repos') or []:
            counts.setdefault(repo, 0)
            stuck.setdefault(repo, [])
        for bucket in ('worked_on', 'stalled', 'up_next'):
            for row in desk.get(bucket) or []:
                if (row.get('title') or '').startswith(PREFIX):
                    alarms[row['repo']] = row['number']
                elif bucket == 'stalled':
                    stuck.setdefault(row['repo'], []).append(row)
                    counts[row['repo']] = counts.get(row['repo'], 0) + 1
    return counts, stuck, alarms


def owner_plan(repo, work, coordinators):
    """`<coordinator id>-work` when a coordinator sits on the same Watch desk as repo."""
    for desk in work.get('desks') or []:
        if repo not in (desk.get('repos') or []):
            continue
        for coord in coordinators:
            if coord.get('id') == desk.get('id') or coord.get('repo') in desk.get('repos'):
                return f"{coord['id']}-work"
    return None


def body(repo, items, at):
    hours = office_work.WINDOW // 3600
    lines = [f'{len(items)} open issue(s) in `{repo}` with no claim and no receipt movement '
             f'in {hours} h (the Watch classifier\'s stalled rule).', '',
             f'Filed by the pile alarm job (nexus-office#290) at {at}. It closes this issue '
             'itself when the count returns to 0.', '', '| issue | state | last activity | title |',
             '| --- | --- | --- | --- |']
    for row in items:
        title = (row.get('title') or '').replace('|', '/')[:90]
        last = row.get('activity_at') or f"none (opened {row.get('created_at')})"
        lines.append(f"| {repo}#{row['number']} | stalled | {last} | {title} |")
    return '\n'.join(lines) + '\n'


def _digest(items):
    return hashlib.sha256(json.dumps(sorted(r['number'] for r in items)).encode()).hexdigest()


def open_alarms_for(repo, gh, seen, remembered):
    """Every open alarm issue in repo, oldest first.

    GitHub is the authority, not the work view: the view lags the issue the last
    run filed, and trusting it filed #294 on top of #292. The memo's issue covers
    search-index lag; the view's covers a failed search."""
    found = set(gh.open_alarms(repo))
    for number in (seen, remembered):
        if number and number not in found and (number == seen or gh.is_open(repo, number)):
            found.add(number)
    return sorted(found)


def tick(work, gh, tower, coordinators, state_dir, now=None):
    """One alarm pass. Effects go through gh/tower; the receipt is always written."""
    now = now if now is not None else time.time()
    at = office_work.iso(now)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    memo_path = state_dir / 'pile-alarm-memo.json'
    try:
        memo = json.loads(memo_path.read_text())
    except (OSError, ValueError):
        memo = {}
    counts, stuck, alarms = piles(work)
    receipt = {'at': at, 'work_built_at': work.get('built_at'), 'window_hours': office_work.WINDOW // 3600,
               'counts': counts, 'stuck': {r: [i['number'] for i in v] for r, v in stuck.items() if v},
               'dispatched': [], 'edited': [], 'cleared': [], 'duplicates': [], 'errors': []}
    for repo, count in sorted(counts.items()):
        try:
            entry = memo.get(repo)
            entry = entry if isinstance(entry, dict) else {'digest': entry}
            open_alarms = open_alarms_for(repo, gh, alarms.get(repo), entry.get('issue'))
            if count == 0:
                for number in open_alarms:
                    gh.close(repo, number, f'Pile alarm count for `{repo}` is 0 at {at}. Closing.')
                    receipt['cleared'].append({'repo': repo, 'issue': number})
                memo.pop(repo, None)
                continue
            digest = _digest(stuck[repo])
            text = body(repo, stuck[repo], at)
            title = f'{PREFIX} {repo}: {count} stuck issue(s)'
            if open_alarms:
                keep, extra = open_alarms[0], open_alarms[1:]
                for number in extra:
                    gh.close(repo, number, f'Duplicate of #{keep}: one open pile alarm per repo. Closing.')
                    receipt['duplicates'].append({'repo': repo, 'issue': number, 'kept': keep})
                if entry.get('digest') != digest or entry.get('issue') != keep:
                    gh.edit(repo, keep, title, text)
                    receipt['edited'].append({'repo': repo, 'issue': keep})
                memo[repo] = {'digest': digest, 'issue': keep}
                continue
            number = gh.create(repo, title, text)
            memo[repo] = {'digest': digest, 'issue': number}
            plan = owner_plan(repo, work, coordinators)
            task = tower.dispatch(plan, repo, number, stuck[repo]) if plan else None
            receipt['dispatched'].append({'repo': repo, 'issue': number, 'plan': plan, 'task': task})
        except Exception as exc:  # noqa: BLE001 - one repo's failure is a receipt line, not a dead run
            receipt['errors'].append(f'{repo}: {type(exc).__name__}: {exc}'[:240])
    errors = [e for d in work.get('desks') or [] for e in d.get('errors') or []]
    receipt['errors'].extend(f'work: {e}' for e in errors)
    memo_path.write_text(json.dumps(memo, sort_keys=True))
    with (state_dir / 'pile-alarm.jsonl').open('a') as fh:
        fh.write(json.dumps(receipt, sort_keys=True) + '\n')
    (state_dir / 'pile-alarm.json').write_text(json.dumps(receipt, sort_keys=True, indent=1))
    return receipt


# ---- the live effects ------------------------------------------------------

class GitHub:
    """Issue writes as the identity Office already resolves for the repo."""

    def __init__(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('office_sync', HERE / 'office-sync.py')
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        self.access = sync.Access()

    def _api(self, method, endpoint, payload):
        repo = '/'.join(endpoint.split('/')[1:3])
        _, token = self.access.token_for(repo)
        if not token:
            raise PermissionError(f'no GitHub identity can write {repo}')
        args = ['gh', 'api', '--method', method, endpoint] + (['--input', '-'] if payload is not None else [])
        result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None, capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, GH_TOKEN=token))
        if result.returncode:
            raise ValueError(f'gh {method} {endpoint}: {(result.stderr or "failed")[:200]}')
        return json.loads(result.stdout or '{}')

    def create(self, repo, title, text):
        return self._api('POST', f'repos/{repo}/issues', {'title': title, 'body': text})['number']

    def edit(self, repo, number, title, text):
        self._api('PATCH', f'repos/{repo}/issues/{number}', {'title': title, 'body': text})

    def open_alarms(self, repo):
        q = f'repo:{repo} is:issue is:open in:title "{PREFIX}"'
        _, token = self.access.token_for(repo)
        if not token:
            raise PermissionError(f'no GitHub identity can read {repo}')
        result = subprocess.run(['gh', 'api', '-X', 'GET', 'search/issues', '-f', f'q={q}', '-f', 'per_page=100',
                                 '--jq', '[.items[] | select(.title | startswith("' + PREFIX + '")) | .number]'],
                                capture_output=True, text=True, timeout=60, env=dict(os.environ, GH_TOKEN=token))
        if result.returncode:
            raise ValueError(f'gh search {repo}: {(result.stderr or "failed")[:200]}')
        return json.loads(result.stdout or '[]')

    def is_open(self, repo, number):
        return self._api('GET', f'repos/{repo}/issues/{number}', None).get('state') == 'open'

    def close(self, repo, number, comment):
        self._api('POST', f'repos/{repo}/issues/{number}/comments', {'body': comment})
        self._api('PATCH', f'repos/{repo}/issues/{number}', {'state': 'closed', 'state_reason': 'completed'})


class Tower:
    """A candidate task on the coordinator's plan, one live task per repo."""

    def __init__(self, path=None):
        sys.path.insert(0, str(HERE.parent))
        from nexus.ledger import Ledger, default_path
        self.ledger = Ledger(path or default_path())

    def dispatch(self, plan_name, repo, issue, stuck):
        plan = self.ledger.plan_by_name(plan_name)
        if plan is None:
            return None
        key = f'pile-alarm:{repo}'
        live = self.ledger.live_task_with_key(key)
        if live is not None:
            return live['id']
        refs = ', '.join(f"#{r['number']}" for r in stuck)
        return self.ledger.add_task(
            title=f'Pile alarm: {len(stuck)} stuck issue(s) in {repo} ({repo}#{issue})',
            origin='pile-alarm', plan_id=plan['id'], dedupe_key=key, unique_live=True, risk='low',
            reason=json.dumps({'repo': repo, 'alarm_issue': issue, 'stuck': refs}, sort_keys=True))


def fetch_work(attempts=12, wait=10):
    """The Watch tab's work view, from the door. Waits out its first background build."""
    for _ in range(attempts):
        with urllib.request.urlopen(f'{DOOR}/api/watch', timeout=30) as resp:
            work = (json.load(resp).get('data') or {}).get('work') or {}
        if work.get('built_at') and work.get('desks'):
            return work
        if work.get('error'):
            raise RuntimeError(f"work view failed: {work['error']}")
        time.sleep(wait)
    raise RuntimeError('work view never finished building')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dry-run', action='store_true', help='count only; no GitHub or Tower writes')
    args = ap.parse_args(argv)
    work = fetch_work()
    coordinators = json.loads((HERE / 'coordinators.json').read_text())
    if args.dry_run:
        counts, _, alarms = piles(work)
        print(json.dumps({'counts': counts, 'alarms': alarms}, sort_keys=True))
        return 0
    receipt = tick(work, GitHub(), Tower(), coordinators, STATE)
    print(json.dumps({k: receipt[k] for k in ('at', 'counts', 'dispatched', 'edited', 'cleared', 'duplicates', 'errors')},
                     sort_keys=True))
    return 1 if receipt['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
