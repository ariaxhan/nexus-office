"""Office view of Tower's actual issue claims and retry evidence."""

import json
import os
from pathlib import Path
import sqlite3
import time


def _payload(db, kind, subject):
    row = db.execute("SELECT payload FROM events WHERE kind=? AND subject=? ORDER BY id DESC LIMIT 1",
                     (kind, subject)).fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        return {}


def _next(due, now):
    if not due:
        return ""
    seconds = max(0, int(float(due) - now))
    if seconds == 0:
        return "ready to retry"
    return f"retry in {max(1, (seconds + 59) // 60)} min"


def _recovery_state(db, attempt):
    if not attempt:
        return None
    fid = attempt['id']
    if attempt['state'] == 'resolving':
        recovery = _payload(db, 'work.recovery_ambiguous', fid)
        error = _payload(db, 'work.recovery_unconfigured', fid)
        return 'held', recovery.get('reason') or error.get('reason') or 'Tower recovery needs checkout inspection', ''
    owner = _payload(db, 'work.recovered', fid)
    if owner.get('reason') == 'dead_owner_claim_released':
        return 'retrying', 'Work owner exited; Tower released the claim. Outcome proof is required before another execution.', 'ready to verify'
    return None


def _issue_state(attempt, labels, pending, failure, disposition, recovery, now):
    if recovery:
        return recovery
    if attempt and attempt['state'] in ('running', 'produced', 'verifying', 'verified', 'landing'):
        return 'working', 'Tower is working this issue', ''
    if labels & {'hold', 'waiting on human', 'blocked-needs-look'}:
        return 'held', disposition.get('reason') or 'Held by issue label', ''
    if 'epic' in labels:
        return 'planned', 'Milestone; not a Tower flight', ''
    if pending and (not failure or pending.get('next_retry', 0) >= failure.get('next_retry', 0)):
        return 'retrying', pending.get('reason') or 'Waiting for next Tower attempt', _next(pending.get('next_retry'), now)
    if failure:
        return 'retrying', failure.get('error') or 'Tower attempt failed', _next(failure.get('next_retry'), now)
    return _queued_state(labels, disposition)


def _queued_state(labels, disposition):
    if disposition.get('state') in ('backoff', 'blocked'):  # e.g. an open dependency: say so, not "ready"
        return 'waiting', disposition.get('reason') or 'Tower gate is closed', ''
    if labels & {'ready', 'in-pr', 'in pr'}:
        return 'ready', 'Waiting for Tower', ''
    return None  # nothing asked Tower to fly it: not Tower work, so not on this board (#210 D4)


def _row(db, task, now):
    """The board row for one issue-backed task, or None when it is not on the board."""
    target = (task['dedupe_key'] or '').removeprefix('github:')
    repo, _, number = target.rpartition('#')
    if not repo or not number.isdigit():
        return None
    attempt = db.execute("SELECT id,state,created_at FROM flights WHERE task_id=? "
                         "ORDER BY created_at DESC LIMIT 1", (task['id'],)).fetchone()
    issue = _payload(db, 'work.issue', task['id'])
    labels = {str(x.get('name', '')).lower() for x in issue.get('labels', [])}
    shown = _issue_state(attempt, labels, _payload(db, 'work.pending', task['id']),
                         _payload(db, 'work.failure', task['id']), _payload(db, 'work.disposition', task['id']),
                         _recovery_state(db, attempt), now)
    if shown is None:
        return None
    state, detail, next_try = shown
    return {'id': target, 'repo': repo, 'number': int(number),
            'title': task['title'], 'url': f'https://github.com/{target.replace("#", "/issues/")}',
            'state': state, 'detail': detail[:300], 'next': next_try,
            'attempt': attempt['id'] if attempt else ''}


def _board(issues, not_queued, limit=60):
    """What needs a look comes first and is never cut; only quiet queue rows make room (#210 D4)."""
    order = {'working': 0, 'held': 1, 'retrying': 2, 'waiting': 3, 'ready': 4, 'planned': 5}
    issues.sort(key=lambda item: (order.get(item['state'], 9), item['repo'], item['number']))
    urgent = [x for x in issues if order.get(x['state'], 9) <= order['retrying']]
    shown = urgent + [x for x in issues if x not in urgent][:max(0, limit - len(urgent))]
    return {'state': 'ok', 'detail': '', 'issues': shown,
            'dropped': len(issues) - len(shown), 'not_queued': not_queued,
            'working': sum(x['state'] == 'working' for x in issues),
            'retrying': sum(x['state'] == 'retrying' for x in issues)}


def read(path=None, now=None):
    now = time.time() if now is None else now
    path = Path(path or os.environ.get('OFFICE_WORK_LEDGER') or
                Path.home() / 'Library/Application Support/nexus/ledger.sqlite')
    if not path.exists():
        return {'state': 'missing', 'detail': 'Tower ledger is unavailable', 'issues': []}
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            tasks = db.execute("SELECT id,dedupe_key,title,state,created_at FROM tasks "
                               "WHERE origin='github-work' ORDER BY created_at DESC").fetchall()
            seen, issues, not_queued = set(), [], 0
            for task in tasks:
                key = task['dedupe_key'] or ''
                if not key.startswith('github:') or key in seen:
                    continue
                seen.add(key)
                if task['state'] == 'done':
                    continue
                row = _row(db, task, now)
                if row is None:
                    not_queued += 1
                else:
                    issues.append(row)
        return _board(issues, not_queued)
    except (OSError, sqlite3.Error) as exc:
        return {'state': 'unreadable', 'detail': f'Tower ledger: {type(exc).__name__}', 'issues': []}
