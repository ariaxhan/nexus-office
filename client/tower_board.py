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


ACTIVE = ('running', 'produced', 'verifying', 'verified', 'landing')
TICK_EVERY_S = 60   # nexus.tower.TICK_RECEIPT_S
DOWN_AFTER_S = 3 * TICK_EVERY_S


def _age(ts, now):
    """Seconds since a ledger timestamp, computed when read so the snapshot carries it as data."""
    return None if ts is None else max(0, int(now - ts))


def _issue_state(attempt, labels, pending, failure, disposition, recovery, now):
    if recovery:
        return recovery
    if attempt and attempt['state'] in ACTIVE:
        return 'working', 'Tower is working this issue', ''
    if labels & {'hold', 'waiting on human', 'blocked-needs-look'}:
        return 'held', disposition.get('reason') or 'Held by issue label', ''
    if 'epic' in labels:
        return 'planned', 'Milestone; not a Tower flight', ''
    if pending and (not failure or pending.get('next_retry', 0) >= failure.get('next_retry', 0)):
        return 'retrying', pending.get('reason') or 'Waiting for next Tower attempt', _next(pending.get('next_retry'), now)
    if failure:
        if not failure.get('next_retry'):
            return 'failed', failure.get('error') or 'Tower attempt failed; no retry scheduled', ''
        return 'retrying', failure.get('error') or 'Tower attempt failed', _next(failure.get('next_retry'), now)
    return 'queued', '', ''


def _liveness(db, now):
    """Tower's own receipts: a tick within 3x its interval, and whether it is paused."""
    tick = db.execute("SELECT ts FROM events WHERE kind='tower.tick' ORDER BY id DESC LIMIT 1").fetchone()
    pause = db.execute("SELECT kind FROM events WHERE kind IN ('tower.paused','tower.resumed') "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    tick_at = tick['ts'] if tick else None
    return tick_at, bool(pause) and pause['kind'] == 'tower.paused', bool(tick_at and now - tick_at <= DOWN_AFTER_S)


def _verified(db, now, limit=5):
    """Recent completions with a proven terminal result: LANDED with a commit, never just an exit."""
    rows = db.execute("SELECT e.subject, e.ts, e.payload, t.dedupe_key, t.title, f.state flight_state FROM events e "
                      "JOIN flights f ON f.id=e.subject LEFT JOIN tasks t ON t.id=f.task_id "
                      "WHERE e.kind='flight.terminal' ORDER BY e.id DESC LIMIT 200").fetchall()
    done = []
    for row in rows:
        try:
            result = json.loads(row['payload'])
        except (TypeError, ValueError):
            continue
        if result.get('state') != 'LANDED' or not result.get('sha') or row['flight_state'] != 'landed':
            continue
        done.append({'flight': row['subject'], 'age_s': _age(row['ts'], now), 'sha': str(result['sha'])[:12],
                     'branch': result.get('branch') or '', 'title': row['title'] or '',
                     'issue': (row['dedupe_key'] or '').removeprefix('github:')})
        if len(done) >= limit:
            break
    return done


def _issue_row(db, task, target, repo, number, now):
    issue = _payload(db, 'work.issue', task['id'])
    disposition = _payload(db, 'work.disposition', task['id'])
    if issue.get('state') == 'closed' or disposition.get('state') == 'closed':
        return None
    attempt = db.execute("SELECT id,state,created_at,started_at FROM flights WHERE task_id=? "
                         "ORDER BY created_at DESC LIMIT 1", (task['id'],)).fetchone()
    claims = db.execute(f"SELECT count(*) FROM flights WHERE task_id=? AND state IN "
                        f"({','.join('?' * len(ACTIVE))})", (task['id'], *ACTIVE)).fetchone()[0]
    recovery = _recovery_state(db, attempt)
    pending = _payload(db, 'work.pending', task['id'])
    failure = _payload(db, 'work.failure', task['id'])
    labels = {str(x.get('name', '')).lower() for x in issue.get('labels', [])}
    state, detail, next_try = _issue_state(attempt, labels, pending, failure, disposition, recovery, now)
    if claims > 1:
        state, detail = 'fault', f'{claims} concurrent Tower claims on one issue'
    progress = db.execute("SELECT max(ts) FROM events WHERE subject IN (?,?)",
                          (task['id'], attempt['id'] if attempt else '')).fetchone()[0]
    return ({'id': target, 'repo': repo, 'number': int(number),
                   'title': task['title'], 'url': f'https://github.com/{target.replace("#", "/issues/")}',
                   'state': state, 'detail': detail[:300], 'next': next_try,
                   'attempt': attempt['id'] if attempt else '',
                   'since_s': _age((attempt['started_at'] or attempt['created_at']) if attempt
                                   else task['created_at'], now),
                   'progress_s': _age(progress or task['created_at'], now)})


def _issue_rows(db, now):
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
        row = _issue_row(db, task, target, repo, number, now)
        if row:
            issues.append(row)
    return issues


def _office_rows(db, now):
    issues = []
    office = db.execute("SELECT f.id,f.state,f.created_at,f.started_at,f.ended_at,"
                        "t.id task_id,t.title,p.name plan FROM flights f "
                        "JOIN tasks t ON t.id=f.task_id JOIN plans p ON p.id=f.plan_id "
                        "WHERE p.name='office-code-work' AND f.state NOT IN "
                        "('landed','cancelled','failed') ORDER BY f.created_at DESC LIMIT 5").fetchall()
    for flight in office:
        progress = db.execute("SELECT max(ts) FROM events WHERE subject IN (?,?)",
                              (flight['task_id'], flight['id'])).fetchone()[0]
        state = 'working' if flight['state'] in ACTIVE else 'queued' if flight['state'] == 'queued' else 'held'
        issues.append({'id': flight['id'], 'repo': 'Nexus Office', 'number': 0,
                       'title': flight['title'], 'url': '', 'state': state,
                       'detail': f"{flight['plan']} · {flight['state']}", 'next': '',
                       'attempt': flight['id'],
                       'since_s': _age(flight['started_at'] or flight['created_at'], now),
                       'progress_s': _age(progress, now)})
    return issues


def _summary(issues, verified, tick_at, paused, alive, now):
    working = [x for x in issues if x['state'] == 'working']
    for item in issues:
        if item['state'] == 'queued':
            item['detail'] = _queued_reason(item, working, paused)
    queued = [x for x in issues if x['state'] == 'queued']
    activity = _activity(alive, paused, working, queued)
    order = {'fault': 0, 'working': 1, 'failed': 2, 'queued': 3, 'retrying': 4, 'held': 5, 'planned': 6}
    issues.sort(key=lambda item: (order.get(item['state'], 9), item['repo'], item['number']))
    return {'state': 'ok', 'detail': '', 'activity': activity, 'tick_s': _age(tick_at, now), 'paused': paused,
            'issues': issues[:60], 'dropped': max(0, len(issues) - 60), 'verified': verified,
            'working': len(working), 'queued': len(queued),
            'retrying': sum(x['state'] == 'retrying' for x in issues),
            'held': sum(x['state'] == 'held' for x in issues),
            'failed': sum(x['state'] in ('failed', 'fault') for x in issues),
            'oldest_queued_s': max((x['since_s'] for x in queued), default=None)}


def _queued_reason(item, working, paused):
    if paused:
        return 'Tower is paused'
    if any(w['repo'] == item['repo'] for w in working):
        return 'waiting for a free slot in this repo'
    return 'not yet scheduled'


def _activity(alive, paused, working, queued):
    if not alive:
        return 'tower-down'
    if paused:
        return 'paused'
    if working:
        return 'working'
    return 'queued' if queued else 'idle'


def read(path=None, now=None):
    now = time.time() if now is None else now
    path = Path(path or os.environ.get('OFFICE_WORK_LEDGER') or
                Path.home() / 'Library/Application Support/nexus/ledger.sqlite')
    if not path.exists():
        return {'state': 'missing', 'detail': 'Tower ledger is unavailable', 'activity': 'unknown', 'issues': []}
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            tick_at, paused, alive = _liveness(db, now)
            issues = _issue_rows(db, now)
            verified = _verified(db, now)
            issues.extend(_office_rows(db, now))
        return _summary(issues, verified, tick_at, paused, alive, now)
    except (OSError, sqlite3.Error) as exc:
        return {'state': 'unreadable', 'detail': f'Tower ledger: {type(exc).__name__}',
                'activity': 'unknown', 'issues': []}
