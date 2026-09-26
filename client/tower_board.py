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


TICK_EVERY_S = 60           # nexus.tower.TICK_RECEIPT_EVERY_S
DOWN_AFTER_S = 3 * TICK_EVERY_S
ACTIVE = ('running', 'produced', 'verifying', 'verified', 'landing', 'resolving')


def _ago(at, now):
    if not at:
        return ''
    seconds = max(0, int(now - float(at)))
    if seconds < 90:
        return f'{seconds}s ago'
    if seconds < 5400:
        return f'{seconds // 60} min ago'
    if seconds < 172800:
        return f'{seconds // 3600} h ago'
    return f'{seconds // 86400} d ago'


def _last(db, kinds):
    marks = ','.join('?' * len(kinds))
    return db.execute(f"SELECT kind,ts,payload FROM events WHERE kind IN ({marks}) ORDER BY id DESC LIMIT 1",
                      kinds).fetchone()


def _liveness(db, now):
    """Is the controller alive and deciding? Only a tick receipt says so; silence is not idle."""
    tick = _last(db, ('tower.tick',))
    pause = _last(db, ('tower.paused', 'tower.resumed'))
    paused = bool(pause) and pause['kind'] == 'tower.paused'
    if not tick:
        return {'state': 'down', 'paused': paused, 'last_tick': None,
                'detail': 'no Tower tick receipt in the ledger'}
    age = now - tick['ts']
    if age > DOWN_AFTER_S:
        return {'state': 'down', 'paused': paused, 'last_tick': tick['ts'],
                'detail': f'no Tower tick since {_ago(tick["ts"], now)}'}
    return {'state': 'paused' if paused else 'running', 'paused': paused, 'last_tick': tick['ts'],
            'detail': f'last tick {_ago(tick["ts"], now)}'}


def _progress_at(db, flight):
    row = db.execute("SELECT MAX(ts) FROM events WHERE subject=?", (flight['id'],)).fetchone()
    return max(x for x in (row[0], flight['started_at'], flight['created_at']) if x)


def _recent(db, now, limit=5):
    """Verified completions: a work.receipt is Tower's done proof, never a process exit."""
    rows = db.execute("SELECT e.ts,e.subject,e.payload,t.dedupe_key,t.title FROM events e "
                      "LEFT JOIN tasks t ON t.id=e.subject WHERE e.kind='work.receipt' "
                      "ORDER BY e.id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError):
            payload = {}
        out.append({'id': (row['dedupe_key'] or row['subject'] or '').removeprefix('github:'),
                    'title': row['title'] or '', 'flight': payload.get('flight') or '',
                    'sha': str(payload.get('sha') or '')[:12], 'receipt': str(payload.get('receipt') or '')[:200],
                    'at': row['ts'], 'age': _ago(row['ts'], now)})
    return out


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
    if failure and failure.get('next_retry'):
        return 'retrying', failure.get('error') or 'Tower attempt failed', _next(failure.get('next_retry'), now)
    if failure:
        return 'failed', failure.get('error') or 'Tower attempt failed; no retry scheduled', ''
    return 'ready', 'Waiting for Tower', ''


def _ready_reason(live, working):
    if live['state'] == 'down':
        return 'not launching: Tower is down'
    if live['paused']:
        return 'not launching: Tower is paused'
    if working:
        return f'waiting for a lane; {working} running'
    return 'not yet scheduled'


def read(path=None, now=None):
    now = time.time() if now is None else now
    path = Path(path or os.environ.get('OFFICE_WORK_LEDGER') or
                Path.home() / 'Library/Application Support/nexus/ledger.sqlite')
    if not path.exists():
        return {'state': 'missing', 'detail': 'Tower ledger is unavailable', 'issues': [],
                'summary': 'unavailable', 'recent': []}
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            live = _liveness(db, now)
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
                issue = _payload(db, 'work.issue', task['id'])
                if str(issue.get('state', '')).lower() == 'closed':
                    continue
                claims = db.execute("SELECT id,state,created_at,started_at FROM flights WHERE task_id=? "
                                    f"AND state IN ({','.join('?' * len(ACTIVE))}) ORDER BY created_at DESC",
                                    (task['id'], *ACTIVE)).fetchall()
                attempt = claims[0] if claims else db.execute(
                    "SELECT id,state,created_at,started_at FROM flights WHERE task_id=? "
                    "ORDER BY created_at DESC LIMIT 1", (task['id'],)).fetchone()
                recovery = _recovery_state(db, attempt)
                pending = _payload(db, 'work.pending', task['id'])
                failure = _payload(db, 'work.failure', task['id'])
                disposition = _payload(db, 'work.disposition', task['id'])
                labels = {str(x.get('name', '')).lower() for x in issue.get('labels', [])}
                state, detail, next_try = _issue_state(attempt, labels, pending, failure, disposition, recovery, now)
                if len(claims) > 1:  # one owner per issue; a second live claim is a fault, never hidden
                    state, detail = 'fault', f'{len(claims)} concurrent claims: ' + ', '.join(c['id'] for c in claims)
                row = {'id': target, 'repo': repo, 'number': int(number),
                       'title': task['title'], 'url': f'https://github.com/{target.replace("#", "/issues/")}',
                       'state': state, 'detail': detail[:300], 'next': next_try,
                       'attempt': attempt['id'] if attempt else '',
                       'phase': attempt['state'] if attempt and state in ('working', 'fault') else '',
                       'queued_at': task['created_at']}
                if attempt and state in ('working', 'fault'):
                    row['started'] = _ago(attempt['started_at'] or attempt['created_at'], now)
                    row['progress'] = _ago(_progress_at(db, attempt), now)
                issues.append(row)
            recent = _recent(db, now)
        working = sum(x['state'] == 'working' for x in issues)
        ready = [x for x in issues if x['state'] == 'ready']
        for item in ready:
            item['detail'] = _ready_reason(live, working)
        order = {'fault': 0, 'working': 1, 'failed': 2, 'ready': 3, 'retrying': 4, 'held': 5, 'planned': 6}
        issues.sort(key=lambda item: (order.get(item['state'], 9), item['repo'], item['number']))
        oldest = min((x['queued_at'] for x in ready), default=None)
        if live['state'] == 'down':
            summary = 'tower-down'
        elif working:
            summary = 'working'
        elif ready:
            summary = 'queued'
        elif live['paused']:
            summary = 'paused'
        else:
            summary = 'idle'
        return {'state': 'ok', 'detail': '', 'issues': issues[:60],
                'dropped': max(0, len(issues) - 60),
                'summary': summary, 'liveness': live['state'], 'liveness_detail': live['detail'],
                'working': working, 'queued': len(ready),
                'oldest_queued': _ago(oldest, now),
                'retrying': sum(x['state'] == 'retrying' for x in issues),
                'held': sum(x['state'] == 'held' for x in issues),
                'failed': sum(x['state'] in ('failed', 'fault') for x in issues),
                'recent': recent}
    except (OSError, sqlite3.Error) as exc:
        return {'state': 'unreadable', 'detail': f'Tower ledger: {type(exc).__name__}', 'issues': [],
                'summary': 'unavailable', 'recent': []}
