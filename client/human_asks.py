"""Durable, source-owned human requests. A failed read never clears an ask."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from contextlib import closing

ROOT = Path(__file__).resolve().parent
SEEDS = ROOT / 'human_asks_sources.json'
DB = Path(os.environ.get('OFFICE_HUMAN_ASKS_DB', Path.home() / '.local/state/nexus-office/human-asks.sqlite'))
_lock = threading.Lock()
_last_check = 0.0
STATES = {'open', 'resolved', 'dismissed', 'reassigned', 'superseded'}
ASK_MARKER = '<!-- office-human-ask\n'
OUTCOME_MARKER = '<!-- office-human-ask-outcome\n'


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def connect(path=None):
    path = Path(path or DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS asks (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT NOT NULL,
        owner TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL,
        observed_at TEXT NOT NULL, state TEXT NOT NULL, resolution_evidence TEXT,
        last_source_verification TEXT, source_stale INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS ask_events (
        id INTEGER PRIMARY KEY, ask_id TEXT NOT NULL, at TEXT NOT NULL,
        state TEXT NOT NULL, evidence TEXT NOT NULL)''')
    return db


def observe(db, item):
    """A source explicitly publishes an ask; repeated observations retain one identity."""
    required = ('id', 'source', 'source_ref', 'owner', 'action', 'created_at')
    if any(not item.get(k) for k in required):
        raise ValueError('human ask requires stable id, source, reference, owner, action and creation time')
    at = now()
    before = db.execute('SELECT * FROM asks WHERE id=?', (item['id'],)).fetchone()
    if before and (before['source'], before['source_ref']) != (item['source'], item['source_ref']):
        raise ValueError('human ask id belongs to a different source')
    if before:
        # A later observation is not authority to undo a terminal receipt.
        db.execute('UPDATE asks SET observed_at=? WHERE id=?', (at, item['id']))
        return
    db.execute('''INSERT INTO asks(id,source,source_ref,owner,action,created_at,observed_at,state)
                  VALUES(?,?,?,?,?,?,?,?)''', tuple(item[k] for k in required) + (at, 'open'))
    db.execute('INSERT INTO ask_events(ask_id,at,state,evidence) VALUES(?,?,?,?)',
               (item['id'], at, 'open', json.dumps({'source': item['source_ref']})))


def transition(db, ask_id, state, evidence, owner=None):
    if state not in STATES or state == 'open' or not evidence or not evidence.get('source_ref'):
        raise ValueError('terminal human ask state requires authoritative source evidence')
    row = db.execute('SELECT * FROM asks WHERE id=?', (ask_id,)).fetchone()
    if row is None:
        raise KeyError(ask_id)
    if evidence['source_ref'] != row['source_ref']:
        raise ValueError('resolution evidence must match the authoritative source')
    if state == 'reassigned' and not owner:
        raise ValueError('reassignment requires the new owner')
    at = now()
    encoded = json.dumps(evidence, ensure_ascii=False)
    db.execute('''UPDATE asks SET state=?, owner=?, resolution_evidence=?,
                  last_source_verification=?, source_stale=0 WHERE id=?''',
               (state, owner or row['owner'], encoded, at, ask_id))
    db.execute('INSERT INTO ask_events(ask_id,at,state,evidence) VALUES(?,?,?,?)',
               (ask_id, at, state, encoded))


def seed(db, path=None):
    for item in json.loads(Path(path or SEEDS).read_text()):
        observe(db, item)


def declarations(text, marker=ASK_MARKER):
    """Only source-authored, typed JSON blocks qualify; prose is never classified."""
    out = []
    for part in str(text or '').split(marker)[1:]:
        payload, sep, _ = part.partition('\n-->')
        if sep:
            try:
                value = json.loads(payload)
                if isinstance(value, dict):
                    out.append(value)
            except ValueError:
                continue
    return out


def observe_stations(db, stations):
    for station in stations or []:
        repo = station.get('repo') or ''
        for issue in station.get('issues') or []:
            number = issue.get('number')
            if not number:
                continue
            ref = f'{repo}#{number}'
            for entry in issue.get('human_ask_declarations') or []:
                if not all(entry.get(k) for k in ('key', 'owner', 'action')):
                    continue
                observe(db, {'id': f'github:{ref}:{entry["key"]}', 'source': 'github',
                             'source_ref': ref, 'owner': entry['owner'], 'action': entry['action'],
                             'created_at': entry.get('created_at') or issue.get('updatedAt') or now()})


def github_issue(ref):
    repo, number = ref.rsplit('#', 1)
    result = subprocess.run(['gh', 'issue', 'view', number, '-R', repo,
                             '--json', 'state,closedAt,updatedAt,url,comments'],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f'GitHub {ref} unavailable: {result.stderr.strip()[:120]}')
    return json.loads(result.stdout)


def reconcile(db, fetch=github_issue):
    """Only a verified source transition may remove an open ask."""
    rows = db.execute("SELECT * FROM asks WHERE source='github'").fetchall()
    by_ref = {}
    for row in rows:
        ref = row['source_ref']
        if ref not in by_ref:
            try:
                by_ref[ref] = fetch(ref)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                by_ref[ref] = exc
        source = by_ref[ref]
        if isinstance(source, Exception):
            db.execute('UPDATE asks SET source_stale=1 WHERE id=?', (row['id'],))
            continue
        at = now()
        outcomes = [dict(value, comment_url=comment.get('url'))
                    for comment in source.get('comments') or []
                    for value in declarations(comment.get('body'), OUTCOME_MARKER)
                    if value.get('id') == row['id'] and value.get('state') in STATES - {'open'}]
        if outcomes and row['state'] == 'open':
            outcome = outcomes[-1]
            transition(db, row['id'], outcome['state'],
                       {'source_ref': ref, 'comment_url': outcome.get('comment_url'),
                        'verified_at': at, 'declared_evidence': outcome.get('evidence')},
                       owner=outcome.get('owner'))
        elif source.get('state') == 'CLOSED' and row['state'] == 'open':
            transition(db, row['id'], 'resolved', {'source_ref': ref, 'url': source.get('url'),
                       'closed_at': source.get('closedAt'), 'verified_at': at})
        elif source.get('state') == 'OPEN':
            # Explicit dismissal/reassignment/supersession remains terminal even if the issue stays open.
            db.execute('UPDATE asks SET last_source_verification=?, source_stale=0 WHERE id=?', (at, row['id']))
        else:
            db.execute('UPDATE asks SET source_stale=1 WHERE id=?', (row['id'],))


def listing(path=None, fetch=github_issue, max_age_s=300, stations=None):
    global _last_check
    with _lock, closing(connect(path)) as db:
        with db:
            seed(db)
            observe_stations(db, stations)
            if time.monotonic() - _last_check >= max_age_s or path is not None:
                reconcile(db, fetch)
                if path is None:
                    _last_check = time.monotonic()
            rows = db.execute("SELECT * FROM asks WHERE owner='aria' AND state='open' ORDER BY created_at,id").fetchall()
            return {'items': [dict(r, resolution_evidence=json.loads(r['resolution_evidence'])
                                      if r['resolution_evidence'] else None) for r in rows],
                    'at': now()}
