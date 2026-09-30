"""The sole durable human-input boundary. Needs You only reads this store."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import closing

DB = Path(os.environ.get('OFFICE_HUMAN_ASKS_DB',
         Path.home() / '.local/state/nexus-office/human-asks.sqlite'))
HUMAN_ASKS_SCHEMA_VERSION = 2
GATE_TYPES = {'inaccessible_authentication', 'physical_action', 'new_judgment',
              'outside_authority', 'unrecoverable_missing_information'}
ORDINARY_ACTION = re.compile(
    r'^(?:(?:next[,;:]?\s+)?(?:aria\s+(?:should|must|needs?\s+to)\s+|'
    r'ask\s+aria\s+to\s+|please\s+))?'
    r'(?:run|rerun|retry|restart|push|merge|deploy|release|regenerate|repair|rollback|fix\s+ci)\b|'
    r'^decide\s+whether\s+to\s+(?:run|rerun|retry|restart|push|merge|deploy|release|rollback|continue)\b',
    re.IGNORECASE)
REDUNDANT_APPROVAL = re.compile(
    r'(?i)\b(?:approve|confirm|authorize|click)\b.{0,50}'
    r'\b(?:merge|deploy|release|publish(?:ing)?|activate|commit|push|retry|continue|freeze)\b|'
    r'\bdecide whether to continue\b')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def connect(path=None):
    path = Path(path or DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > HUMAN_ASKS_SCHEMA_VERSION:
        db.close()
        raise RuntimeError(f'human asks schema {version} is newer than this Office supports')
    if version == 1:
        with db:
            for column in ('gate_type', 'proof', 'resume'):
                db.execute(f'ALTER TABLE asks ADD COLUMN {column} TEXT')
            db.execute("UPDATE asks SET owner='office',state='reassigned',"
                       "resolution_evidence=? WHERE state='open' AND owner='aria'",
                       (json.dumps({'reason': 'legacy ask lacked central validation'}),))
            db.execute('PRAGMA user_version=2')
    elif version == 0:
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS asks (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT NOT NULL,
                owner TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL,
                observed_at TEXT NOT NULL, state TEXT NOT NULL, resolution_evidence TEXT,
                last_source_verification TEXT, source_stale INTEGER NOT NULL DEFAULT 0,
                gate_type TEXT, proof TEXT, resume TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS ask_events (
                id INTEGER PRIMARY KEY, ask_id TEXT NOT NULL, at TEXT NOT NULL,
                state TEXT NOT NULL, evidence TEXT NOT NULL)''')
            db.execute('PRAGMA user_version=2')
    required = {'id','source','source_ref','owner','action','created_at',
                'observed_at','state','gate_type','proof','resume'}
    if not required <= {row['name'] for row in db.execute('PRAGMA table_info(asks)')}:
        db.close()
        raise RuntimeError('human asks schema is incompatible')
    db.execute('PRAGMA journal_mode=WAL')
    return db


def request_human_input(db, *, identifier, execution_ref, gate_type, action,
                        why_agent_cannot_do_it, authorization_gap,
                        resume_after_answer):
    """The one path that can transfer an unfinished execution to Aria."""
    if gate_type not in GATE_TYPES:
        raise ValueError('not a human-input gate')
    values = (identifier,execution_ref,action,why_agent_cannot_do_it,
              authorization_gap,resume_after_answer)
    if any(not isinstance(value,str) or not value.strip() for value in values):
        raise ValueError('human input needs an exact action and ownership proof')
    if ORDINARY_ACTION.match(action.strip()) or REDUNDANT_APPROVAL.search(action):
        raise ValueError('ordinary technical work remains Office-owned')
    if gate_type == 'new_judgment' and re.search(r'(?i)\b(?:approve|approval|acceptance)\b',action):
        raise ValueError('an approval label is not a new product judgment')
    proof = {'why_agent_cannot_do_it':why_agent_cannot_do_it,
             'authorization_gap':authorization_gap,
             'resume_after_answer':resume_after_answer}
    at = now()
    row = db.execute('SELECT source_ref,state,gate_type,action,proof,resume FROM asks WHERE id=?',
                     (identifier,)).fetchone()
    if row and row['source_ref'] != execution_ref:
        raise ValueError('human-input ID belongs to a different execution')
    if row and row['state'] == 'open':
        if (row['gate_type'],row['action'],row['proof'],row['resume']) != (
                gate_type,action,json.dumps(proof),resume_after_answer):
            raise ValueError('an open human-input ID cannot change request')
        return identifier
    with db:
        if row:
            db.execute("""UPDATE asks SET source='request_human_input',
                owner='aria',action=?,observed_at=?,state='open',
                gate_type=?,proof=?,resume=?,resolution_evidence=NULL WHERE id=?""",
                (action,at,gate_type,json.dumps(proof),resume_after_answer,identifier))
        else:
            db.execute("""INSERT INTO asks(id,source,source_ref,owner,action,created_at,
                observed_at,state,gate_type,proof,resume) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier,'request_human_input',execution_ref,'aria',action,at,at,
                 'open',gate_type,json.dumps(proof),resume_after_answer))
        db.execute('INSERT INTO ask_events(ask_id,at,state,evidence) VALUES(?,?,?,?)',
                   (identifier,at,'open',json.dumps({'gate_type':gate_type,'proof':proof})))
    return identifier


def resolve_human_input(db, identifier, answer):
    if not isinstance(answer,str) or not answer.strip():
        raise ValueError('an answer is required')
    row = db.execute("SELECT * FROM asks WHERE id=? AND state='open' AND gate_type IS NOT NULL",
                     (identifier,)).fetchone()
    if row is None:
        raise FileNotFoundError('active human-input request not found')
    at = now()
    with db:
        db.execute("UPDATE asks SET state='resolved',owner='office',resolution_evidence=?,"
                   "last_source_verification=? WHERE id=?",
                   (json.dumps({'answer':answer,'at':at}),at,identifier))
        db.execute('INSERT INTO ask_events(ask_id,at,state,evidence) VALUES(?,?,?,?)',
                   (identifier,at,'resolved',json.dumps({'answer':answer})))
    return {'execution_ref':row['source_ref'],'resume':row['resume'],'answer':answer}


def listing(path=None):
    with closing(connect(path)) as db:
        rows = db.execute("SELECT * FROM asks WHERE state='open' AND owner='aria' "
                          "AND gate_type IS NOT NULL ORDER BY created_at,id").fetchall()
        return {'items':[dict(row) for row in rows], 'at':now()}


def ownership(execution_ref, task_state, path=None):
    """The only ownership decision: terminal, validated input, or Office."""
    if task_state == 'done':
        return 'DONE'
    with closing(connect(path)) as db:
        waiting = db.execute("SELECT 1 FROM asks WHERE source_ref=? AND state='open' "
                             "AND owner='aria' AND gate_type IS NOT NULL LIMIT 1",
                             (execution_ref,)).fetchone()
    return 'HUMAN_INPUT_REQUIRED' if waiting else 'OFFICE_OWNED'
