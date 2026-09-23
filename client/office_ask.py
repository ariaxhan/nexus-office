"""One durable Office chat backed by the personal Codex app-server session."""
import json
import random
import re
import selectors
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

import office_preferences as preferences
import office_profiles as profiles
import private_state

DEFAULT_MODEL = 'gpt-6-sol'
LOCK = threading.RLock()
CATALOG = (0, [])
AUTO_CHOICE = {'engine': 'office', 'id': 'auto', 'name': 'Auto · learns from feedback'}


def claude_binary():
    candidate = shutil.which('claude') or '/opt/homebrew/bin/claude'
    return candidate if Path(candidate).is_file() else None


def claude_environment():
    """Keep the personal Claude seat while isolating it from Office daemon settings."""
    selected = profiles.environment('claude', 'personal')
    allowed = ('HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR', 'SHELL', 'LANG', 'LC_ALL',
               'SSH_AUTH_SOCK', 'XDG_CONFIG_HOME')
    return {name: selected[name] for name in allowed if name in selected}
WORKER = None
OFFICE_DIR = Path(__file__).resolve().parents[1]
MANAGER_INSTRUCTIONS = (
    'You are the user\'s Office manager in one continuous chat. Primarily answer questions about '
    'coordinators, work, issues, pull requests, runs, and timelines from current evidence. '
    'The user wants to supervise and direct work without opening a terminal. '
    'Use exact source paths and GitHub issue/PR URLs in answers. State observation times and distinguish '
    'an instruction being queued, read, acted on, and verified. If evidence is missing, say so. '
    'For coordinator log evidence, include an Office link such as [Inspect TBS](#coordinator?id=tbs) '
    'or [Inspect Matra](#coordinator?id=matra) alongside any raw path. '
    'You may route explicit instructions and make requested small edits. For substantial work, use the '
    'existing coordinator/task system and report the receipt. Keep answers concise and useful. '
    'Do not claim completion from a queued message or a running process. '
    'For routine status answers, use at most five short bullets and about 150 words unless asked for depth.'
)


def path():
    return preferences.path().with_name('office-ask.sqlite3')


def connect():
    target = path()
    private_state.ensure_dir(target.parent)
    db = sqlite3.connect(target, timeout=10)
    target.chmod(0o600)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,
            text TEXT NOT NULL, model TEXT, status TEXT NOT NULL,
            created_at REAL NOT NULL, completed_at REAL
        );
        CREATE TABLE IF NOT EXISTS ratings (
            reply_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, updated_at REAL NOT NULL
        );
    ''')
    return db


def _state(db, key, default=None):
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def _set(db, key, value):
    db.execute('''INSERT INTO state(key,value) VALUES(?,?)
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (key, str(value)))


def read():
    with connect() as db:
        if not WORKER or not WORKER.is_alive():
            db.execute("UPDATE messages SET status='failed',text='Office restarted before this answer finished.',completed_at=? WHERE status='running'", (time.time(),))
        messages = [dict(row) for row in db.execute('''SELECT id,role,text,model,status,created_at,completed_at
                                                     FROM messages ORDER BY id LIMIT 500''')]
        ratings = {row['reply_id']: row['kind'] for row in db.execute('SELECT reply_id,kind FROM ratings')}
        for message in messages:
            if message['id'] in ratings:
                message['rating'] = ratings[message['id']]
        pending = next((row for row in messages if row['status'] == 'running'), None)
        return {'messages': messages, 'model': _state(db, 'model', DEFAULT_MODEL),
                'selection': _state(db, 'selection', _state(db, 'model', DEFAULT_MODEL)),
                'busy': pending is not None, 'thread_id': _state(db, 'thread_id'),
                'checked_at': time.time()}


def auto_model(db, available):
    candidates = [name for name in ('gpt-6-sol', 'gpt-6-luna', 'claude:sonnet', 'claude:haiku')
                  if name in available]
    if not candidates:
        raise ValueError('No model is available for Auto')
    scores = {name: [1, 1, 0] for name in candidates}
    for row in db.execute("SELECT model,status FROM messages WHERE role='office'"):
        if row['model'] in scores:
            score = scores[row['model']]
            score[2] += 1
            score[0 if row['status'] == 'complete' else 1] += 1
    for row in db.execute('''SELECT m.model,r.kind FROM ratings r
                             JOIN messages m ON m.id=r.reply_id'''):
        if row['model'] in scores:
            scores[row['model']][0 if row['kind'] == 'helpful' else 1] += 2
    fewest = min(scores[name][2] for name in candidates)
    if fewest < 2:
        return random.choice([name for name in candidates if scores[name][2] == fewest])
    return max(candidates, key=lambda name: random.betavariate(*scores[name][:2]))


def rate(body):
    reply_id, kind = body.get('reply_id'), body.get('kind')
    if type(reply_id) is not int or kind not in ('helpful', 'missed'):
        raise ValueError('Choose Helpful or Missed for an Office answer')
    with connect() as db:
        row = db.execute("SELECT 1 FROM messages WHERE id=? AND role='office' AND status='complete'", (reply_id,)).fetchone()
        if not row:
            raise FileNotFoundError('Office answer is unavailable')
        db.execute('''INSERT INTO ratings(reply_id,kind,updated_at) VALUES(?,?,?)
                      ON CONFLICT(reply_id) DO UPDATE SET kind=excluded.kind,updated_at=excluded.updated_at''',
                   (reply_id, kind, time.time()))
    return {'reply_id': reply_id, 'kind': kind}


class AppServer:
    def __init__(self):
        binary = shutil.which('codex')
        if not binary:
            raise RuntimeError('Codex is not installed')
        self.proc = subprocess.Popen([binary, 'app-server'], cwd=OFFICE_DIR,
                                     env=profiles.environment('codex', 'personal'),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        self.serial = 0
        self.request('initialize', {'clientInfo': {'name': 'nexus-office', 'version': '1.0.0'},
                                    'capabilities': None}, 15)
        self.send({'method': 'initialized', 'params': {}})

    def close(self):
        self.selector.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def send(self, data):
        self.proc.stdin.write(json.dumps(data) + '\n')
        self.proc.stdin.flush()

    def receive(self, timeout):
        if not self.selector.select(timeout=timeout):
            raise TimeoutError('Codex did not respond in time')
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('Codex app-server exited')
        return json.loads(line)

    def request(self, method, params, timeout=20):
        self.serial += 1
        request_id = self.serial
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.receive(max(.1, deadline - time.monotonic()))
            if item.get('id') != request_id:
                continue
            if item.get('error'):
                raise RuntimeError(str(item['error'])[:500])
            return item['result']
        raise TimeoutError(method + ' timed out')


def models(fresh=False):
    global CATALOG
    with LOCK:
        if not fresh and time.time() - CATALOG[0] < 300:
            return {'items': [AUTO_CHOICE] + CATALOG[1], 'default': DEFAULT_MODEL}
        profiles.require('codex', 'personal')
        client = AppServer()
        try:
            rows = []
            cursor = None
            while True:
                result = client.request('model/list', {'includeHidden': False, 'cursor': cursor})
                rows.extend({'engine': 'codex', 'id': row['id'],
                             'name': row.get('displayName') or row['id']}
                            for row in result.get('data', []) if not row.get('hidden'))
                cursor = result.get('nextCursor')
                if not cursor:
                    break
        finally:
            client.close()
        binary = claude_binary()
        claude_status = 'not installed'
        if binary:
            try:
                proc = subprocess.run([binary, '-p', '/model', '--setting-sources', 'project,local', '--output-format', 'json',
                                       '--max-budget-usd', '0.01'],
                                       env=claude_environment(),
                                       cwd='/tmp', stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, timeout=20)
                claude_status = 'exit ' + str(proc.returncode)
                result = json.loads(proc.stdout).get('result', '') if proc.returncode == 0 else ''
                match = re.search(r'Available:\s*([^\n]+)', result)
                if match:
                    claude_status = 'available'
                    for alias in ('sonnet', 'opus', 'haiku', 'fable', 'sonnet[1m]', 'opus[1m]', 'fable[1m]'):
                        if re.search(r'(?<![A-Za-z])' + re.escape(alias) + r'(?![A-Za-z])', match.group(1)):
                            rows.append({'engine': 'claude', 'id': 'claude:' + alias,
                                         'name': 'Claude Code · ' + alias.title()})
            except (OSError, ValueError, subprocess.TimeoutExpired, PermissionError) as error:
                claude_status = type(error).__name__
        private_state.atomic_write_text(path().with_name('office-claude-catalog-status.txt'),
                                        str(int(time.time())) + ' ' + claude_status + '\n')
        CATALOG = (time.time(), rows)
        return {'items': [AUTO_CHOICE] + rows, 'default': DEFAULT_MODEL}


def send(body):
    global WORKER
    message = body.get('text')
    requested = body.get('model', DEFAULT_MODEL)
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 8000:
        raise ValueError('Ask needs a message of at most 8000 characters')
    available = {row['id'] for row in models()['items']}
    if requested not in available and requested != 'auto':
        raise ValueError('That model is not currently available in the selected personal account')
    with LOCK, connect() as db:
        model = auto_model(db, available) if requested == 'auto' else requested
        if not WORKER or not WORKER.is_alive():
            db.execute("UPDATE messages SET status='failed',text='Office restarted before this answer finished.',completed_at=? WHERE status='running'", (time.time(),))
        if db.execute("SELECT 1 FROM messages WHERE status='running' LIMIT 1").fetchone():
            raise ValueError('Office is finishing the previous answer')
        previous = _state(db, 'model', DEFAULT_MODEL)
        if previous != model:
            db.execute('INSERT INTO messages(role,text,model,status,created_at) VALUES(?,?,?,?,?)',
                       ('system', f'Model changed from {previous} to {model}', model, 'complete', time.time()))
        _set(db, 'model', model)
        _set(db, 'selection', requested)
        db.execute('INSERT INTO messages(role,text,model,status,created_at) VALUES(?,?,?,?,?)',
                   ('user', message.strip(), model, 'complete', time.time()))
        cursor = db.execute('INSERT INTO messages(role,text,model,status,created_at) VALUES(?,?,?,?,?)',
                            ('office', '', model, 'running', time.time()))
        reply_id = cursor.lastrowid
        WORKER = threading.Thread(target=_answer, args=(reply_id, message.strip(), model), daemon=True)
        WORKER.start()
    return {'accepted': True, 'reply_id': reply_id, 'model': model}


def _bridge(db, model):
    rows = db.execute('''SELECT role,text,model FROM messages WHERE status='complete' AND role IN ('user','office')
                         ORDER BY id DESC LIMIT 20''').fetchall()
    earlier = list(reversed(rows))
    last_office = next((row for row in reversed(earlier) if row['role'] == 'office'), None)
    if not last_office or last_office['model'].startswith('claude:') == model.startswith('claude:'):
        return ''
    return 'Conversation context from the other Office engine, for continuity:\n' + '\n'.join(
        f"{row['role']}: {row['text'][:1600]}" for row in earlier) + '\n\nCurrent user message:\n'


def _claude_answer(message, model, bridge):
    alias = model.split(':', 1)[1]
    with connect() as db:
        session_id = _state(db, 'claude_session_id')
    binary = claude_binary()
    if not binary:
        raise RuntimeError('Claude Code is not installed')
    args = [binary, '-p', '--setting-sources', 'project,local', '--output-format', 'json', '--model', alias,
            '--permission-mode', 'auto', '--append-system-prompt', MANAGER_INSTRUCTIONS]
    if session_id:
        args.extend(['--resume', session_id])
    else:
        session_id = str(uuid.uuid4())
        args.extend(['--session-id', session_id])
    args.append((bridge or '') + message)
    proc = subprocess.run(args, cwd=OFFICE_DIR, env=claude_environment(),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout)[-600:])
    result = json.loads(proc.stdout)
    if result.get('is_error'):
        raise RuntimeError(str(result.get('result') or result.get('error'))[:600])
    with connect() as db:
        _set(db, 'claude_session_id', result.get('session_id') or session_id)
    return result.get('result', '')


def _answer(reply_id, message, model):
    client = None
    answer = ''
    status = 'complete'
    try:
        with connect() as db:
            bridge = _bridge(db, model)
        if model.startswith('claude:'):
            answer = _claude_answer(message, model, bridge)
            if not answer:
                raise RuntimeError('Claude Code returned an empty answer')
            return
        client = AppServer()
        with connect() as db:
            thread_id = _state(db, 'thread_id')
        if thread_id:
            client.request('thread/resume', {'threadId': thread_id, 'model': model,
                                             'cwd': str(OFFICE_DIR), 'sandbox': 'danger-full-access',
                                             'approvalPolicy': 'never',
                                             'developerInstructions': MANAGER_INSTRUCTIONS}, 30)
        else:
            result = client.request('thread/start', {'model': model, 'cwd': str(OFFICE_DIR),
                                                     'sandbox': 'danger-full-access',
                                                     'approvalPolicy': 'never',
                                                     'developerInstructions': MANAGER_INSTRUCTIONS}, 30)
            thread_id = result['thread']['id']
            with connect() as db:
                _set(db, 'thread_id', thread_id)
        client.request('turn/start', {'threadId': thread_id,
                                      'input': [{'type': 'text', 'text': (bridge or '') + message}], 'model': model}, 30)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                item = client.receive(min(30, max(.1, deadline - time.monotonic())))
            except TimeoutError:
                continue
            if item.get('method') == 'item/completed':
                output = item.get('params', {}).get('item', {})
                if output.get('type') == 'agentMessage':
                    answer = output.get('text', '')
                    with connect() as db:
                        db.execute('UPDATE messages SET text=? WHERE id=?', (answer, reply_id))
            if item.get('method') == 'turn/completed':
                turn = item.get('params', {}).get('turn', {})
                if turn.get('status') != 'completed':
                    raise RuntimeError(str(turn.get('error') or turn.get('status'))[:500])
                if not answer:
                    answer = next((x.get('text', '') for x in reversed(turn.get('items', []))
                                   if x.get('type') == 'agentMessage'), '')
                break
        else:
            raise TimeoutError('Ask turn exceeded 30 minutes')
        if not answer:
            raise RuntimeError('Codex completed without an answer')
    except Exception as exc:
        answer = f'Office could not finish this answer: {type(exc).__name__}: {exc}'[:1000]
        status = 'failed'
    finally:
        if client:
            client.close()
        with connect() as db:
            db.execute('UPDATE messages SET text=?,status=?,completed_at=? WHERE id=?',
                       (answer, status, time.time(), reply_id))
