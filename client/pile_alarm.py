"""Pile alarm (nexus-office#290, records per #303): on a timer, find each repo's stuck set.

Stuck = the Watch tab's own STALLED bucket (office_work.classify): open, and no
claim or receipt movement inside office_work.WINDOW. One classifier, read from
the door's /api/watch, never a second one here.

The output is records, not text: one {repo, issue, filed_at, age_s, state, owner}
per stuck item, handed to the intake machine (nexus/stuck.py), which keeps them
in the ledger and re-dispatches an aged item's owner on its `<id>-work` plan.
The alarm creates no issue. A `[pile-alarm]` issue left open by the text-filing
version is closed with a pointer to the records. Every run appends a receipt to
pile-alarm.jsonl and rewrites pile-alarm.json; a run with no receipt did not
happen. Nobody has to notice first.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import office_work  # noqa: E402
from nexus import stuck  # noqa: E402

PREFIX = '[pile-alarm]'
DOOR = os.environ.get('OFFICE_DOOR', 'http://127.0.0.1:8790')
STATE = Path(os.environ.get('PILE_ALARM_STATE', Path.home() / '.local/state/nexus-office'))


def piles(work):
    """({repo: count}, {repo: [stuck rows]}, {repo: open legacy alarm issue number})."""
    counts, rows, alarms = {}, {}, {}
    for desk in work.get('desks') or []:
        for repo in desk.get('repos') or []:
            counts.setdefault(repo, 0)
            rows.setdefault(repo, [])
        for bucket in ('worked_on', 'stalled', 'up_next'):
            for row in desk.get(bucket) or []:
                if (row.get('title') or '').startswith(PREFIX):
                    alarms[row['repo']] = row['number']
                elif bucket == 'stalled':
                    rows.setdefault(row['repo'], []).append(row)
                    counts[row['repo']] = counts.get(row['repo'], 0) + 1
    return counts, rows, alarms


def owner_plan(repo, work, coordinators):
    """`<coordinator id>-work` when a coordinator sits on the same Watch desk as repo."""
    for desk in work.get('desks') or []:
        if repo not in (desk.get('repos') or []):
            continue
        for coord in coordinators:
            if coord.get('id') == desk.get('id') or coord.get('repo') in desk.get('repos'):
                return f"{coord['id']}-work"
    return None


def records(work, coordinators, now):
    """The stuck set as records: age is time since the last movement, else since filing."""
    _, rows, _ = piles(work)
    out = []
    for repo, items in sorted(rows.items()):
        owner = owner_plan(repo, work, coordinators)
        for row in items:
            since = office_work.parse_iso(row.get('activity_at')) or office_work.parse_iso(row.get('created_at'))
            out.append({'repo': repo, 'issue': row['number'], 'filed_at': row.get('created_at'),
                        'age_s': round(now - since) if since else None, 'state': 'stalled', 'owner': owner})
    return out


def tick(work, ledger, coordinators, state_dir, gh=None, now=None, dispatch=None):
    """One alarm pass: records into the intake machine; the receipt is always written."""
    now = now if now is not None else time.time()
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    counts, _, alarms = piles(work)
    found = records(work, coordinators, now)
    receipt = {'at': office_work.iso(now), 'work_built_at': work.get('built_at'),
               'window_hours': office_work.WINDOW // 3600, 'counts': counts, 'records': found,
               'intake': {}, 'retired': [], 'errors': []}
    try:
        receipt['intake'] = stuck.ingest(ledger, found, now, dispatch)
    except Exception as exc:  # noqa: BLE001 - the receipt still says the run happened and why it failed
        receipt['errors'].append(f'intake: {type(exc).__name__}: {exc}'[:240])
    for repo in sorted(counts) if gh else ():
        try:
            for number in sorted(set(gh.open_alarms(repo)) | ({alarms[repo]} if repo in alarms else set())):
                gh.close(repo, number, 'The pile alarm now emits stuck-set records into the intake machine '
                         '(nexus-office#303) instead of filing issues. Closing this text filing.')
                receipt['retired'].append({'repo': repo, 'issue': number})
        except Exception as exc:  # noqa: BLE001 - one repo's failure is a receipt line, not a dead run
            receipt['errors'].append(f'{repo}: {type(exc).__name__}: {exc}'[:240])
    receipt['errors'].extend(f'work: {e}' for d in work.get('desks') or [] for e in d.get('errors') or [])
    with (state_dir / 'pile-alarm.jsonl').open('a') as fh:
        fh.write(json.dumps(receipt, sort_keys=True) + '\n')
    (state_dir / 'pile-alarm.json').write_text(json.dumps(receipt, sort_keys=True, indent=1))
    return receipt


# ---- the live effects ------------------------------------------------------

class GitHub:
    """Retiring legacy alarm issues, as the identity Office already resolves for the repo. Never creates."""

    def __init__(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('office_sync', HERE / 'office-sync.py')
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        self.access = sync.Access()

    def _token(self, repo):
        _, token = self.access.token_for(repo)
        if not token:
            raise PermissionError(f'no GitHub identity can write {repo}')
        return token

    def _gh(self, repo, args, payload=None):
        result = subprocess.run(['gh', *args], input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, GH_TOKEN=self._token(repo)))
        if result.returncode:
            raise ValueError(f'gh {args[:3]}: {(result.stderr or "failed")[:200]}')
        return json.loads(result.stdout or 'null')

    def open_alarms(self, repo):
        q = f'repo:{repo} is:issue is:open in:title "{PREFIX}"'
        return self._gh(repo, ['api', '-X', 'GET', 'search/issues', '-f', f'q={q}', '-f', 'per_page=100',
                               '--jq', '[.items[] | select(.title | startswith("' + PREFIX + '")) | .number]']) or []

    def close(self, repo, number, comment):
        self._gh(repo, ['api', '--method', 'POST', f'repos/{repo}/issues/{number}/comments', '--input', '-'],
                 {'body': comment})
        self._gh(repo, ['api', '--method', 'PATCH', f'repos/{repo}/issues/{number}', '--input', '-'],
                 {'state': 'closed', 'state_reason': 'completed'})


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
    ap.add_argument('--dry-run', action='store_true', help='records only; no ledger, GitHub or Tower writes')
    args = ap.parse_args(argv)
    work = fetch_work()
    coordinators = json.loads((HERE / 'coordinators.json').read_text())
    if args.dry_run:
        print(json.dumps(records(work, coordinators, time.time()), sort_keys=True))
        return 0
    from nexus.ledger import Ledger, default_path
    ledger = Ledger(default_path())
    try:
        receipt = tick(work, ledger, coordinators, STATE, gh=GitHub())
    finally:
        ledger.close()
    print(json.dumps({k: receipt[k] for k in ('at', 'counts', 'intake', 'retired', 'errors')}, sort_keys=True))
    return 1 if receipt['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
