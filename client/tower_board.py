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
            seen, issues = set(), []
            for task in tasks:
                key = task['dedupe_key'] or ''
                if not key.startswith('github:') or key in seen:
                    continue
                seen.add(key)
                if task['state'] == 'done':
                    continue
                target = key.removeprefix('github:')
                if '#' not in target:
                    continue
                repo, number = target.rsplit('#', 1)
                if not number.isdigit():
                    continue
                attempt = db.execute("SELECT id,state,created_at FROM flights WHERE task_id=? "
                                     "ORDER BY created_at DESC LIMIT 1", (task['id'],)).fetchone()
                recovery = (_payload(db, 'work.recovery_ambiguous', attempt['id']) if attempt else {})
                recovery_error = (_payload(db, 'work.recovery_unconfigured', attempt['id']) if attempt else {})
                owner_recovered = (_payload(db, 'work.recovered', attempt['id']) if attempt else {})
                pending = _payload(db, 'work.pending', task['id'])
                failure = _payload(db, 'work.failure', task['id'])
                disposition = _payload(db, 'work.disposition', task['id'])
                issue = _payload(db, 'work.issue', task['id'])
                labels = {str(x.get('name', '')).lower() for x in issue.get('labels', [])}
                if attempt and attempt['state'] == 'resolving':
                    state, detail, next_try = 'held', (recovery.get('reason') or recovery_error.get('reason')
                                                        or 'Tower recovery needs checkout inspection'), ''
                elif attempt and attempt['state'] in ('running', 'produced', 'verifying', 'verified', 'landing'):
                    state, detail, next_try = 'working', 'Tower is working this issue', ''
                elif labels & {'hold', 'waiting on human', 'blocked-needs-look'}:
                    state, detail, next_try = 'held', disposition.get('reason') or 'Held by issue label', ''
                elif 'epic' in labels:
                    state, detail, next_try = 'planned', 'Milestone; not a Tower flight', ''
                elif pending and (not failure or pending.get('next_retry', 0) >= failure.get('next_retry', 0)):
                    state, detail = 'retrying', pending.get('reason') or 'Waiting for next Tower attempt'
                    next_try = _next(pending.get('next_retry'), now)
                elif owner_recovered.get('reason') == 'dead_owner_claim_released':
                    state, detail, next_try = 'retrying', 'Work owner exited; Tower released the claim. Outcome proof is required before another execution.', 'ready to verify'
                elif failure:
                    state, detail = 'retrying', failure.get('error') or 'Tower attempt failed'
                    next_try = _next(failure.get('next_retry'), now)
                else:
                    state, detail, next_try = 'ready', 'Waiting for Tower', ''
                issues.append({'id': target, 'repo': repo, 'number': int(number),
                               'title': task['title'], 'url': f'https://github.com/{target.replace("#", "/issues/")}',
                               'state': state, 'detail': detail[:300], 'next': next_try,
                               'attempt': attempt['id'] if attempt else ''})
        order = {'working': 0, 'ready': 1, 'retrying': 2, 'held': 3, 'planned': 4}
        issues.sort(key=lambda item: (order.get(item['state'], 9), item['repo'], item['number']))
        return {'state': 'ok', 'detail': '', 'issues': issues[:60],
                'dropped': max(0, len(issues) - 60),
                'working': sum(x['state'] == 'working' for x in issues),
                'retrying': sum(x['state'] == 'retrying' for x in issues)}
    except (OSError, sqlite3.Error) as exc:
        return {'state': 'unreadable', 'detail': f'Tower ledger: {type(exc).__name__}', 'issues': []}
