"""Office view of Tower's actual issue claims and retry evidence."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

from sources import _card


def _payload(db, kind, subject):
    row = db.execute("SELECT payload FROM events WHERE kind=? AND subject=? ORDER BY id DESC LIMIT 1",
                     (kind, subject)).fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        return {}


def _latest(db, kind):
    """Newest payload per subject for a RARE kind, in one walk of the kind index.

    Asking per issue instead walks every event that issue ever had (thousands of
    re-logged `work.issue` rows) to learn that it has no such event: 4 s a board.
    """
    out = {}
    for subject, payload in db.execute("SELECT subject,payload FROM events INDEXED BY events_kind "
                                       "WHERE kind=? ORDER BY id DESC", (kind,)):
        if subject not in out:
            try:
                out[subject] = json.loads(payload)
            except (TypeError, ValueError):
                out[subject] = {}
    return out


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


def _row(db, task, now, pending, failure):
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
    shown = _issue_state(attempt, labels, pending.get(task['id']) or {},
                         failure.get(task['id']) or {}, disposition,
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
    """A landed sha with its post-landing receipt; the terminal alone precedes verification."""
    rows = db.execute("SELECT e.subject,e.ts,e.payload,t.dedupe_key FROM events e LEFT JOIN flights f ON f.id=e.subject "
                      "LEFT JOIN tasks t ON t.id=f.task_id WHERE e.kind='flight.terminal' "
                      "AND json_extract(e.payload,'$.state')='LANDED' AND json_extract(e.payload,'$.sha') IS NOT NULL "
                      "AND EXISTS (SELECT 1 FROM events r WHERE r.kind='work.receipt' AND r.subject=f.task_id "
                      "AND json_extract(r.payload,'$.sha')=json_extract(e.payload,'$.sha')) "
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


# ── did Tower do real work ────────────────────────────────────────────────────
#
# A flight count is not an outcome. Thousands of sub-second scheduler ticks end
# `produced` with `artifacts: []` and read as green, so nothing below counts a
# flight as work unless it executed an issue or landed a change. Every query is
# bounded by an index and a LIMIT: the live ledger is gigabytes.

DAY_S = 86400
WEEK_S = 7 * DAY_S
LOOP_FLIGHTS = 5  # one issue flown this often in a day with nothing landed is a loop, not a retry
IN_AIR = ('running', 'verifying', 'verified', 'landing', 'resolving')
SCAN = 5000  # ceiling on any one read; a day of flights is a few hundred


def _json(text):
    try:
        value = json.loads(text or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _issue_ref(key, title=''):
    """`github:owner/repo#7` as (repo, '#7', title); anything else keeps its title."""
    repo, _, number = (key or '').removeprefix('github:').rpartition('#')
    return {'repo': repo.rpartition('/')[2], 'number': f'#{number}' if repo else '', 'title': title or ''}


def _receipts(db, now):
    """Landings by `work.receipt`: the week's, plus the newest of any age. Keyed by flight."""
    found = {}
    for ts, task, payload in db.execute("SELECT ts,subject,payload FROM events WHERE kind='work.receipt' "
                                        "ORDER BY id DESC LIMIT ?", (SCAN,)):
        if found and ts < now - WEEK_S:
            break
        data = _json(payload)
        if data.get('sha'):
            found[data.get('flight') or task] = {'at': ts, 'task': task, 'sha': str(data['sha'])}
    return found


def _landings(db, now):
    """Every landed change in the last week plus the newest one of any age, newest first.

    Two receipts for one fact: a `landed` flight, and the `work.receipt` that names the sha.
    Either alone is a landing (a merged change whose post-merge step died still has its receipt).
    """
    found = _receipts(db, now)
    for fid, task, ended in db.execute("SELECT id,task_id,ended_at FROM flights WHERE state='landed' "
                                       "ORDER BY ended_at DESC LIMIT ?", (SCAN,)):
        if found and (ended or 0) < now - WEEK_S:
            break
        if fid not in found:
            sha = _payload(db, 'flight.terminal', fid).get('sha') or ''
            found[fid] = {'at': ended or 0, 'task': task, 'sha': str(sha)}
    rows = sorted(found.values(), key=lambda row: row['at'], reverse=True)
    return [row for i, row in enumerate(rows) if i == 0 or row['at'] >= now - WEEK_S]


def _last_landed(db, landings):
    if not landings:
        return None
    last = landings[0]
    task = db.execute("SELECT dedupe_key,title FROM tasks WHERE id=?", (last['task'],)).fetchone()
    return dict(_issue_ref(task[0], task[1]) if task else _issue_ref(''), at=last['at'], sha=last['sha'][:7])


def _executed(db, since):
    """Flights that really started issue work: a `work.executing` receipt, not a flight row."""
    flights = set()
    for ts, fid in db.execute("SELECT ts,subject FROM events WHERE kind='work.executing' "
                              "ORDER BY id DESC LIMIT ?", (SCAN,)):
        if ts < since:
            break
        flights.add(fid)
    return len(flights)


def _day(db, now):
    """One pass over the last day's flights: failures by cause, retry loops, no-op ticks."""
    rows = db.execute(
        "SELECT f.state,json_extract(f.result,'$.error.code'),json_array_length(f.result,'$.artifacts'),"
        "t.origin,COALESCE(t.dedupe_key,f.task_id),t.title FROM flights f LEFT JOIN tasks t ON t.id=f.task_id "
        "WHERE f.plan_id IN (SELECT id FROM plans) AND f.created_at>=? LIMIT ?", (now - DAY_S, SCAN)).fetchall()
    failures, issues, ticks = {}, {}, 0
    for state, code, artifacts, origin, key, title in rows:
        if state == 'failed':
            failures[code or 'unknown'] = failures.get(code or 'unknown', 0) + 1
        if origin == 'plan':
            ticks += state == 'produced' and not artifacts
        elif key:
            entry = issues.setdefault(key, {'flights': 0, 'landed': False, 'title': title})
            entry['flights'] += 1
            entry['landed'] = entry['landed'] or state == 'landed'
    loops = [dict(_issue_ref(key, e['title']), flights=e['flights']) for key, e in issues.items()
             if e['flights'] >= LOOP_FLIGHTS and not e['landed']]
    return {'failures': sorted(failures.items(), key=lambda kv: -kv[1]),
            'loops': sorted(loops, key=lambda row: -row['flights']), 'ticks': ticks}


def _in_air(db, now):
    rows = db.execute(
        "SELECT f.id,COALESCE(f.started_at,f.created_at),p.name,p.budget,t.dedupe_key,t.title,t.origin "
        "FROM flights f JOIN plans p ON p.id=f.plan_id LEFT JOIN tasks t ON t.id=f.task_id "
        f"WHERE f.state IN ({','.join('?' * len(IN_AIR))}) ORDER BY f.created_at LIMIT 50", IN_AIR).fetchall()
    out = []
    for fid, since, plan, budget, key, title, origin in rows:
        timeout = _json(budget).get('timeout_s')
        ref = _issue_ref(key, title) if origin != 'plan' else _issue_ref('', plan)
        out.append(dict(ref, flight=fid, plan=plan, age_s=now - since,
                        overdue=bool(timeout) and now - since > float(timeout)))
    return out


def summary(db, now):
    """The outcome numbers, raw. `card` turns them into the sentence a person reads."""
    landings = _landings(db, now)
    newest = db.execute("SELECT ts FROM events ORDER BY id DESC LIMIT 1").fetchone()
    quarantined = db.execute("SELECT name,quarantined_at FROM plans WHERE quarantined_at IS NOT NULL "
                             "ORDER BY quarantined_at").fetchall()
    return dict(_day(db, now), now=now, as_of=newest[0] if newest else None,
                last_landed=_last_landed(db, landings),
                landed_24h=sum(row['at'] >= now - DAY_S for row in landings),
                landed_7d=sum(row['at'] >= now - WEEK_S for row in landings),
                executed_7d=_executed(db, now - WEEK_S),
                quarantined=[{'plan': name, 'since': since} for name, since in quarantined],
                running=_in_air(db, now))


def _named(ref):
    return ' '.join(x for x in (f"{ref['repo']}{ref['number']}", _card.clip(ref['title'], 48)) if x)


def _problems(s, tower):
    """What is wrong, worst first, as short clauses for one headline."""
    out = []
    if tower.get('state') in ('down', 'erroring', 'paused'):
        out.append(f"Tower {tower['state']}")
    last = s['last_landed']
    if last is None:
        out.append('No change has ever landed')
    elif not s['landed_24h']:
        age = s['now'] - last['at']
        out.append('No change landed in ' + (f'{int(age // DAY_S)} days' if age >= 2 * DAY_S else _card.human(age)))
    overdue = sum(r['overdue'] for r in s['running'])
    for n, one, many in ((len(s['quarantined']), 'plan quarantined', 'plans quarantined'),
                         (len(s['loops']), 'issue in a retry loop', 'issues in retry loops'),
                         (overdue, 'flight past its timeout', 'flights past their timeout')):
        if n:
            out.append(f'{n} {one if n == 1 else many}')
    return out


def _needs(s):
    """Things only a person can clear. A no-op tick, or a quiet day, is not one."""
    return len(s['quarantined']) + len(s['loops']) + sum(r['overdue'] for r in s['running'])


def _landed_fact(s):
    last = s['last_landed']
    if not last:
        return _card.fact('last landed change', 'none on record', 'bad')
    age = s['now'] - last['at']
    return _card.fact('last landed change', f"{_named(last)} · {last['sha'] or 'no sha'} · {_card.human(age)} ago",
                      'ok' if age < DAY_S else 'warn' if age < 3 * DAY_S else 'bad')


def _count_facts(s):
    if not s['executed_7d']:
        ratio = 'dim'
    else:
        ratio = 'ok' if s['landed_7d'] * 2 >= s['executed_7d'] else 'bad'
    return [
        _card.fact('landed, 24 h / 7 d', f"{s['landed_24h']} / {s['landed_7d']}",
                   'ok' if s['landed_24h'] else 'warn' if s['landed_7d'] else 'bad'),
        _card.fact('issue flights, 7 d', f"{s['executed_7d']} executed, {s['landed_7d']} landed", ratio),
        _card.fact('failed flights, 24 h', ', '.join(f'{n} {code}' for code, n in s['failures']) or 'none',
                   'bad' if s['failures'] else 'ok'),
    ]


def _stuck_facts(s):
    held = ', '.join(f"{q['plan']} ({_card.human(s['now'] - q['since'])})" for q in s['quarantined'])
    loops = s['loops']
    worst = f"{_named(loops[0])}: {loops[0]['flights']} flights, none landed" if loops else 'none'
    more = f' (+{len(loops) - 1} more)' if len(loops) > 1 else ''
    return [_card.fact('quarantined plans', held or 'none', 'bad' if held else 'ok'),
            _card.fact('retry loops, 24 h', worst + more, 'bad' if loops else 'ok')]


def _running_fact(s):
    running = s['running']
    shown = ', '.join(f"{_named(r)}, {_card.human(r['age_s'])}" + (' (past timeout)' if r['overdue'] else '')
                      for r in running[:3])
    more = f' (+{len(running) - 3} more)' if len(running) > 3 else ''
    overdue = any(r['overdue'] for r in running)
    return _card.fact('running now', shown + more or 'nothing', 'bad' if overdue else '' if running else 'dim')


def _facts(s):
    return [_landed_fact(s), *_count_facts(s), *_stuck_facts(s), _running_fact(s),
            _card.fact('scheduler ticks, 24 h', f"{_card.count(s['ticks'])} no-op, not counted as work", 'dim')]


def card(board):
    """The card contract (`sources/_card.py`) for Tower: pure, from `read()`'s own data."""
    s = board.get('summary')
    if board.get('state') != 'ok' or not s:
        return _card.trouble('Tower', board.get('state', 'missing'), board.get('detail', ''),
                             {'missing': ('Tower ledger missing', 1), 'unreadable': ('Tower ledger unreadable', 1)})
    problems, last = _problems(s, board.get('tower') or {}), s['last_landed']
    headline = '; '.join(problems) if problems else (
        f"{s['landed_24h']} {_card.plural(s['landed_24h'], 'change')} landed in 24 h; "
        f"last {last['repo']}{last['number']} {_card.human(s['now'] - last['at'])} ago")
    as_of = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s['as_of'])) if s['as_of'] else ''
    return _card.build('Tower', headline, _needs(s), as_of, _facts(s))


def _carded(board):
    """Attach the card, and let the two words the existing views already draw carry it."""
    board['card'] = card(board)
    if board.get('state') != 'ok':
        return board
    if board['card']['needs'] and board['activity'] in ('idle', 'queued'):
        board['activity'] = 'failed'  # quarantined plans or a retry loop are not a green idle
    if not board['tower'].get('detail'):
        board['tower'] = dict(board['tower'], detail=board['card']['headline'])
    return board


def read(path=None, now=None):
    now = time.time() if now is None else now
    path = Path(path or os.environ.get('OFFICE_WORK_LEDGER') or
                Path.home() / 'Library/Application Support/nexus/ledger.sqlite')
    if not path.exists():
        return _carded({'state': 'missing', 'detail': 'Tower ledger is unavailable', 'issues': []})
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            tasks = db.execute("SELECT id,dedupe_key,title,state,created_at FROM tasks "
                               "WHERE origin='github-work' ORDER BY created_at DESC").fetchall()
            seen, issues, not_queued = set(), [], 0
            pending, failure = _latest(db, 'work.pending'), _latest(db, 'work.failure')
            for task in tasks:
                key = task['dedupe_key'] or ''
                if not key.startswith('github:') or key in seen:
                    continue
                seen.add(key)
                if task['state'] == 'done':
                    continue
                row = _row(db, task, now, pending, failure)
                if row is None:
                    not_queued += 1
                else:
                    issues.append(row)
            tower, completions, runs = _liveness(db, now), _completions(db), _runs(db)
            outcomes = summary(db, now)
        return _carded(dict(_board(issues, not_queued, tower, now, completions, runs), summary=outcomes))
    except (OSError, sqlite3.Error) as exc:
        return _carded({'state': 'unreadable', 'detail': f'Tower ledger: {type(exc).__name__}', 'issues': []})
