"""Office's Watch adapter and inbox over the existing Tower ledger.

Tower's office-code-work plan is the sole issue executor. This module never
claims an issue or starts another worker.
"""
import datetime as dt
from contextlib import closing
import json
from pathlib import Path
import re
import time

from .ledger import Ledger, default_path, loads

PLAN = "office-code-work"
REPO = "ariaxhan/nexus-office"
COMMAND = re.compile(r"^prioriti[sz]e\s+(?:ariaxhan/nexus-office)?#?(\d+)\s*$", re.I)


def _iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _messages(ledger):
    rows = ledger.conn.execute("SELECT id,kind,payload,ts FROM events WHERE kind IN "
                               "('office.coordinator.message','office.coordinator.read','office.coordinator.acted') ORDER BY id").fetchall()
    messages = {}
    for row in rows:
        body = loads(row['payload'], {})
        if row['kind'] == 'office.coordinator.message':
            messages[body['id']] = dict(id=body['id'], text=body['text'], at=_iso(row['ts']), read_at=None, acted_at=None, action=None)
        elif body.get('id') in messages:
            item = messages[body['id']]
            if row['kind'] == 'office.coordinator.read':
                item['read_at'] = _iso(row['ts'])
            else:
                item['acted_at'] = _iso(row['ts'])
                item['action'] = body.get('action')
    return list(messages.values())


def say(body):
    request, message = body.get('id'), body.get('text')
    if not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,64}', request):
        raise ValueError('A message needs its request id')
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 8000:
        raise ValueError('A message is 1-8000 characters')
    with closing(Ledger(default_path())) as ledger:
        with ledger.tx():
            prior = ledger.conn.execute("SELECT payload FROM events WHERE kind='office.coordinator.message' AND subject=?", (request,)).fetchone()
            if prior:
                old = loads(prior['payload'], {})
                if old['text'] != message.strip():
                    raise FileExistsError('Request id already has different text')
                return {'ok': True, 'duplicate': True, 'message': old}
            row = {'id': request, 'text': message.strip(), 'at': _iso(time.time()), 'from': 'aria'}
            ledger._event('office.coordinator.message', request, row, 'phone')
            return {'ok': True, 'duplicate': False, 'message': row}


def receive(ledger):
    """A Tower work cycle reads each message once and records its disposition.

    Only a priority command affects scheduling. Other text remains visible as
    received and explicitly unsupported, never silently executed as authority.
    """
    for message in _messages(ledger):
        if message['acted_at']:
            continue
        with ledger.tx():
            ledger._event('office.coordinator.read', message['id'], {'id': message['id']}, 'tower')
            match = COMMAND.fullmatch(message['text'])
            action = 'unsupported; use prioritize #N'
            if match:
                number = int(match[1])
                key = f'github:{REPO}#{number}'
                task = ledger.conn.execute("SELECT id FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
                if task:
                    ledger._event('office.coordinator.priority', key, {'id': message['id'], 'number': number}, 'tower')
                    action = f'prioritized #{number}'
                else:
                    action = f'#{number} has not been received by Tower'
            ledger._event('office.coordinator.acted', message['id'], {'id': message['id'], 'action': action}, 'tower')


def _overview(ledger):
    plan = ledger.plan_by_name(PLAN)
    flights = ledger.flights(plan_id=plan['id'], limit=30) if plan else []
    latest = flights[0] if flights else None
    running = next((f for f in flights if f['state'] == 'running'), None)
    failures = [f for f in flights if f['state'] == 'failed']
    age = int(time.time() - (latest['ended_at'] or latest['started_at'] or latest['created_at'])) if latest else None
    current = _current_issues(ledger)
    health = _health(plan, flights, failures, running, age)
    messages = _messages(ledger)
    return {'id': 'office', 'name': 'Office', 'health': health, 'live': bool(running),
            'last_start': _iso(latest['started_at']) if latest else None,
            'last_end': _iso(latest['ended_at']) if latest else None, 'age_s': age,
            'working_on': current[0] if current else 'No open Office issue in Tower',
            'lanes': current[:8], 'commits': [], 'failures': len(failures),
            'changes': ledger.conn.execute("SELECT count(*) FROM events WHERE kind='work.closed' AND ts>=? AND subject IN "
                                           "(SELECT id FROM tasks WHERE dedupe_key LIKE ?)",
                                           (time.time()-7*86400, f'github:{REPO}#%')).fetchone()[0],
            'unread': sum(not m['read_at'] for m in messages),
            'prompt': 'docs/office-coordinator.md', 'restart': 'vaults services restart com.nexus.tower'}


def _current_issues(ledger):
    issues = ledger.conn.execute("SELECT id,title,dedupe_key,state FROM tasks WHERE origin='github-work' AND dedupe_key LIKE ? ORDER BY created_at DESC LIMIT 30", (f'github:{REPO}#%',)).fetchall()
    current = []
    for task in issues:
        if task['state'] == 'done':
            continue
        attempt = ledger.conn.execute('SELECT state FROM flights WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (task['id'],)).fetchone()
        state = attempt['state'] if attempt else task['state']
        if state == 'cancelled':
            continue
        text = f"#{task['dedupe_key'].split('#')[-1]} · {task['title']} · {state}"
        current.append((0 if state == 'running' else 1, text))
    return [text for _, text in sorted(current)]


def _health(plan, flights, failures, running, age):
    if not plan or not plan['enabled'] or plan['quarantined_at'] or age is None or age > 900:
        return 'stalled'
    success = next((f for f in flights if f['state'] in ('produced', 'landed')), None)
    if failures and (not success or failures[0]['created_at'] > success['created_at']):
        return 'failing'
    return 'running' if running else 'idle'


def overview():
    with closing(Ledger(default_path())) as ledger:
        return _overview(ledger)


def read():
    with closing(Ledger(default_path())) as ledger:
        messages = _messages(ledger)
        items = [{'kind': 'message', 'id': m['id'], 'at': m['at'], 'read_at': m['read_at'],
                  'acted_at': m['acted_at'], 'action': m['action'],
                  'segments': [{'text': m['text']}]} for m in messages]
        plan = ledger.plan_by_name(PLAN)
        for flight in ledger.flights(plan_id=plan['id'], limit=12) if plan else []:
            started = flight['started_at'] or flight['created_at']
            ended = flight['ended_at']
            log = Path(flight['workspace'] or Path(default_path()).parent / 'flights' / flight['id']) / 'log'
            if not log.exists():
                log = Path(default_path()).parent / 'logs' / f"{flight['id']}.log"
            items.append({'kind': 'run', 'at': _iso(started), 'mode': 'tower',
                          'live': flight['state'] == 'running', 'log': str(log),
                          'end': {'at': _iso(ended), 'secs': max(0, ended-started),
                                  'rc': 0 if flight['state'] in ('produced', 'landed') else 1,
                                  'prod_changes': 0, 'holds': 0} if ended else None,
                          'truncated': False, 'events': [{'kind': 'text',
                          'text': f"Tower Office work flight {flight['id']}: {flight['state']}",
                          'at': _iso(started), 'segments': [{'text': f"Tower Office work flight {flight['id']}: {flight['state']}"}]}],
                          'holds': [], 'changes': {'commits': [], 'publishes': [], 'issues': []}})
        items.sort(key=lambda item: item['at'])
        return {'items': items, 'unread': sum(not m['read_at'] for m in messages), 'as_of': _iso(time.time())}
