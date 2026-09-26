"""Office view of Tower's actual issue claims and retry evidence."""

from contextlib import closing
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
        if str(pending.get('reason', '')).startswith('gate:'):  # a closed gate is waiting, not a failed attempt
            return 'waiting', pending['reason'], _next(pending.get('next_retry'), now).replace('retry', 'recheck')
        return 'retrying', pending.get('reason') or 'Waiting for next Tower attempt', _next(pending.get('next_retry'), now)
    if failure:
        return 'retrying', failure.get('error') or 'Tower attempt failed', _next(failure.get('next_retry'), now)
    return _queued_state(labels, disposition)


def _queued_state(labels, disposition):
    if disposition.get('state') in ('backoff', 'blocked'):  # e.g. an open dependency: say so, not "ready"
        return 'waiting', disposition.get('reason') or 'Tower gate is closed', ''
    if disposition.get('state') in ('held', 'owned'):
        return 'held', disposition.get('reason') or 'Tower will not run it', ''
    if disposition.get('state') in ('ineligible', 'closed'):
        return None  # Tower decided it is not runnable: a leftover label does not make it ready (#210 D4)
    if labels & {'ready', 'in-pr', 'in pr'}:
        return 'ready', 'Waiting for Tower', ''
    return None  # nothing asked Tower to fly it: not Tower work, so not on this board (#210 D4)


def _row(db, task, now):
    """The board row for one issue-backed task, or None when it is not on the board."""
    target = (task['dedupe_key'] or '').removeprefix('github:')
    repo, _, number = target.rpartition('#')
    if not repo or not number.isdigit():
        return None
    attempt = db.execute("SELECT id,state,created_at,started_at,resolution_step FROM flights WHERE task_id=? "
                         "ORDER BY created_at DESC LIMIT 1", (task['id'],)).fetchone()
    issue = _payload(db, 'work.issue', task['id'])
    if str(issue.get('state', '')).lower() == 'closed':
        return None  # a closed issue is history, whatever its last hold said
    labels = {str(x.get('name', '')).lower() for x in issue.get('labels', [])}
    disposition = _payload(db, 'work.disposition', task['id'])
    if 'labels' in disposition and set(disposition['labels']) != labels:
        disposition = {}  # decided about labels that have since changed: not authoritative any more
    shown = _issue_state(attempt, labels, _payload(db, 'work.pending', task['id']),
                         _payload(db, 'work.failure', task['id']), disposition,
                         _recovery_state(db, attempt), now)
    if shown is None:
        return None
    state, detail, next_try = _single_owner(db, task, shown)
    return {'id': target, 'repo': repo, 'number': int(number),
            'title': issue.get('title') or task['title'], 'url': f'https://github.com/{target.replace("#", "/issues/")}',
            'state': state, 'detail': detail[:300], 'next': next_try, **_attempt_facts(db, task, attempt)}


def _single_owner(db, task, shown):
    """One owner per issue; a second live claim is a fault, never a second worker."""
    claims = db.execute("SELECT COUNT(*) FROM flights f JOIN tasks t ON t.id=f.task_id WHERE t.dedupe_key=? "
                        "AND f.state IN ('running','produced','verifying','verified','landing','resolving')",
                        (task['dedupe_key'],)).fetchone()[0]
    return ('failed', f'{claims} concurrent Tower claims on one issue', '') if claims > 1 else shown


def _attempt_facts(db, task, attempt):
    if not attempt:
        return {'attempt': '', 'phase': '', 'since': task['created_at'], 'progress_at': None}
    # progress is the attempt's own trail; rediscovery rewriting the task's labels is not progress
    progress = db.execute("SELECT MAX(ts) FROM events WHERE subject=? AND kind IN "
                          "('work.progress','flight.produced','flight.verified','flight.landed','flight.terminal')",
                          (attempt['id'],)).fetchone()[0]
    return {'attempt': attempt['id'], 'phase': attempt['resolution_step'] or attempt['state'],
            'since': attempt['started_at'] or attempt['created_at'], 'progress_at': progress}


TICK_S = 60  # nexus/tower.py TICK_RECEIPT_EVERY_S


def _liveness(db, now):
    """Is Tower alive, and is it allowed to launch? Only its own receipts say so."""
    tick = db.execute("SELECT ts FROM events WHERE kind='tower.tick' ORDER BY id DESC LIMIT 1").fetchone()
    pause = db.execute("SELECT kind,payload FROM events WHERE kind IN ('tower.paused','tower.resumed') "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    error = db.execute("SELECT ts,payload FROM events WHERE kind='tower.tick_error' ORDER BY id DESC LIMIT 1").fetchone()
    last = tick[0] if tick else None
    if last is None or now - last > 3 * TICK_S:
        return {'state': 'down', 'tick_at': last,
                'detail': 'no Tower tick receipt' + (f' for {_age(now - last)}' if last else ' recorded')}
    if pause and pause[0] == 'tower.paused':
        reason = (json.loads(pause[1] or '{}') or {}).get('reason', '')
        return {'state': 'paused', 'tick_at': last, 'detail': f'paused: reaps, launches nothing ({reason})'}
    if error and error[0] > last - TICK_S:
        return {'state': 'erroring', 'tick_at': last, 'detail': str(json.loads(error[1]).get('error', ''))[:200]}
    return {'state': 'running', 'tick_at': last, 'detail': ''}


def _age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f'{seconds}s'
    if seconds < 5400:
        return f'{seconds // 60}m'
    if seconds < 172800:
        return f'{seconds // 3600}h'
    return f'{seconds // 86400}d'


def _completions(db, limit=5):
    """Verified outcomes only: a LANDED terminal with a sha. A process exiting is not one."""
    rows = db.execute("SELECT e.subject,e.ts,e.payload,t.dedupe_key FROM events e LEFT JOIN flights f ON f.id=e.subject "
                      "LEFT JOIN tasks t ON t.id=f.task_id WHERE e.kind='flight.terminal' "
                      "AND json_extract(e.payload,'$.state')='LANDED' AND json_extract(e.payload,'$.sha') IS NOT NULL "
                      "ORDER BY e.id DESC LIMIT ?", (limit,)).fetchall()
    return [{'flight': r[0], 'at': r[1], 'issue': (r[3] or '').removeprefix('github:'),
             'sha': json.loads(r[2]).get('sha', ''), 'url': json.loads(r[2]).get('pr_url') or ''} for r in rows]


def _runs(db):
    """Open Office-origin flights, kept separate from verified issue outcomes."""
    rows = db.execute("SELECT f.id,p.name,f.state,f.started_at,f.created_at FROM flights f JOIN plans p ON p.id=f.plan_id "
                      "WHERE p.name='office-code-work' AND f.state IN ('queued','running') "
                      "ORDER BY f.created_at DESC LIMIT 20").fetchall()
    return [{'flight': r[0], 'plan': r[1], 'state': r[2], 'since': r[3] or r[4]} for r in rows]


def _queued_reason(tower, working):
    if tower['state'] == 'paused':
        return 'queued: Tower is paused'
    if tower['state'] == 'down':
        return 'queued: Tower is not ticking'
    if working:
        return f'queued: lanes busy ({working} working)'
    return 'queued: not yet scheduled'


def _activity(tower, counts, runs):
    """One honest word for the whole of Tower; never a generic "healthy"."""
    if tower['state'] in ('down', 'paused', 'erroring'):
        return tower['state']
    if any(r['state'] != 'queued' for r in runs):
        return 'working'
    for state in ('working', 'failed', 'held', 'retrying', 'waiting'):
        if counts.get(state):
            return state
    return 'queued' if counts.get('ready') or runs else 'idle'


def _stamp(issues, tower, working, now):
    """Ages on every row and the reason on every queued one; returns the queued rows."""
    for item in issues:
        item['age'] = _age(now - item['since'])
        item['progress'] = _age(now - item['progress_at']) if item['progress_at'] else ''
        item['obligation'] = {
            'working': 'Finish and verify this flight',
            'ready': 'Tower to launch this issue',
            'retrying': 'Tower to make the next attempt',
            'held': 'Resolve the hold before retry',
            'waiting': 'Wait for the dependency or gate',
            'failed': 'Inspect duplicate claims and recover one owner',
        }.get(item['state'], '')
    ready = [x for x in issues if x['state'] == 'ready']
    for item in ready:
        item['detail'] = _queued_reason(tower, working)
    return ready


def _board(issues, not_queued, tower=None, now=None, completions=(), runs=(), limit=60):
    """What needs a look comes first and is never cut; only quiet queue rows make room (#210 D4)."""
    now = time.time() if now is None else now
    tower = tower or {'state': 'unknown', 'tick_at': None, 'detail': ''}
    order = {'working': 0, 'failed': 1, 'held': 2, 'retrying': 3, 'waiting': 4, 'ready': 5, 'planned': 6}
    issues.sort(key=lambda item: (order.get(item['state'], 9), item['repo'], item['number']))
    counts = {state: sum(x['state'] == state for x in issues) for state in order}
    ready = _stamp(issues, tower, counts['working'] + sum(r['state'] == 'running' for r in runs), now)
    urgent = [x for x in issues if order.get(x['state'], 9) <= order['retrying']]
    shown = urgent + [x for x in issues if x not in urgent][:max(0, limit - len(urgent))]
    oldest = min((x['since'] for x in ready), default=None)
    return {'state': 'ok', 'detail': '', 'issues': shown,
            'dropped': len(issues) - len(shown), 'not_queued': not_queued,
            'working': counts['working'], 'retrying': counts['retrying'],
            'failed': counts['failed'], 'held': counts['held'], 'queued': len(ready),
            'oldest_queued': _age(now - oldest) if oldest else '',
            'activity': _activity(tower, counts, runs), 'tower': tower,
            'tick_age': _age(now - tower['tick_at']) if tower.get('tick_at') else '',
            'completions': _aged(completions, 'at', now), 'runs': _aged(runs, 'since', now)}


def _aged(rows, key, now):
    return [dict(row, age=_age(now - row[key])) for row in rows]


def read(path=None, now=None):
    now = time.time() if now is None else now
    path = Path(path or os.environ.get('OFFICE_WORK_LEDGER') or
                Path.home() / 'Library/Application Support/nexus/ledger.sqlite')
    if not path.exists():
        return {'state': 'missing', 'detail': 'Tower ledger is unavailable', 'issues': []}
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
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
            tower, completions, runs = _liveness(db, now), _completions(db), _runs(db)
        return _board(issues, not_queued, tower, now, completions, runs)
    except (OSError, sqlite3.Error) as exc:
        return {'state': 'unreadable', 'detail': f'Tower ledger: {type(exc).__name__}', 'issues': []}
