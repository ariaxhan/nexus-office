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
REQUEST_ID_RE = re.compile(r'[A-Za-z0-9_-]{16,80}\Z', re.ASCII)
INTERRUPTED = 'Office restarted before this answer finished.'


class Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


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
RETRY_TIMER = None
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
    db = sqlite3.connect(target, timeout=10, factory=Connection)
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
    columns = {row['name'] for row in db.execute('PRAGMA table_info(messages)')}
    for name, definition in (('request_id', 'TEXT'), ('parent_id', 'INTEGER'),
                             ('requested_model', 'TEXT'), ('provider_turn_id', 'TEXT'),
                             ('recovering', 'INTEGER NOT NULL DEFAULT 0'),
                             ('next_attempt_at', 'REAL NOT NULL DEFAULT 0'),
                             ('failure_count', 'INTEGER NOT NULL DEFAULT 0')):
        if name not in columns:
            db.execute(f'ALTER TABLE messages ADD COLUMN {name} {definition}')
    db.execute("UPDATE messages SET status='completed' WHERE status='complete'")
    db.execute("UPDATE messages SET status='working' WHERE status='running'")
    db.execute("UPDATE messages SET requested_model=model WHERE role='user' AND requested_model IS NULL")
    db.execute('''UPDATE messages SET parent_id=(
                    SELECT MAX(u.id) FROM messages u WHERE u.role='user' AND u.id < messages.id
                  ) WHERE role='office' AND parent_id IS NULL''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS messages_request_id
                  ON messages(request_id) WHERE request_id IS NOT NULL''')
    db.commit()
    return db


def _state(db, key, default=None):
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def _set(db, key, value):
    db.execute('''INSERT INTO state(key,value) VALUES(?,?)
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (key, str(value)))


def read():
    with connect() as db:
        messages = [dict(row) for row in db.execute('''SELECT * FROM (
                                                       SELECT id,role,text,model,status,created_at,completed_at,
                                                              request_id,parent_id
                                                       FROM messages ORDER BY id DESC LIMIT 500
                                                     ) ORDER BY id''')]
        ratings = {row['reply_id']: row['kind'] for row in db.execute('SELECT reply_id,kind FROM ratings')}
        for message in messages:
            if message['id'] in ratings:
                message['rating'] = ratings[message['id']]
        counts = {row['status']: row['count'] for row in db.execute(
            "SELECT status,COUNT(*) count FROM messages WHERE role='user' GROUP BY status")}
        queue = {'queued': counts.get('queued', 0), 'working': counts.get('working', 0)}
        return {'messages': messages, 'model': _state(db, 'model', DEFAULT_MODEL),
                'selection': _state(db, 'selection', _state(db, 'model', DEFAULT_MODEL)),
                'busy': queue['working'] > 0 or queue['queued'] > 0, 'queue': queue,
                'thread_id': _state(db, 'thread_id'),
                'checked_at': time.time()}


def auto_model(db, available):
    candidates = [name for name in ('gpt-6-sol', 'gpt-6-luna', 'claude:sonnet', 'claude:haiku')
                  if name in available]
    if not candidates:
        raise ValueError('No model is available for Auto')
    scores = {name: [1, 1, 0] for name in candidates}
    for row in db.execute("SELECT model,status FROM messages WHERE role='office' AND status IN ('completed','failed')"):
        if row['model'] in scores:
            score = scores[row['model']]
            score[2] += 1
            score[0 if row['status'] == 'completed' else 1] += 1
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
        row = db.execute("SELECT 1 FROM messages WHERE id=? AND role='office' AND status='completed'", (reply_id,)).fetchone()
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
    message = body.get('text')
    requested = body.get('model', DEFAULT_MODEL)
    request_id = body.get('request_id')
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 8000:
        raise ValueError('Ask needs a message of at most 8000 characters')
    if not isinstance(requested, str):
        raise ValueError('Ask needs a model')
    # Older cached Office clients send no request ID. Keep those sends working
    # while preserving strict validation and idempotency for current clients.
    if request_id is None:
        request_id = str(uuid.uuid4())
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError('Ask request_id must be 16 to 80 letters, numbers, underscores, or hyphens')
    message = message.strip()
    with LOCK, connect() as db:
        existing = db.execute("SELECT * FROM messages WHERE role='user' AND request_id=?",
                              (request_id,)).fetchone()
        if existing:
            return _retry_receipt(db, existing, message, requested)
    available = {row['id'] for row in models()['items']}
    if requested not in available and requested != 'auto':
        raise ValueError('That model is not currently available in the selected personal account')
    with LOCK, connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute("SELECT * FROM messages WHERE role='user' AND request_id=?",
                              (request_id,)).fetchone()
        if existing:
            receipt = _retry_receipt(db, existing, message, requested)
            db.commit()
            return receipt
        model = auto_model(db, available) if requested == 'auto' else requested
        previous = _state(db, 'model', DEFAULT_MODEL)
        if previous != model:
            db.execute('INSERT INTO messages(role,text,model,status,created_at) VALUES(?,?,?,?,?)',
                       ('system', f'Model changed from {previous} to {model}', model, 'completed', time.time()))
        _set(db, 'model', model)
        _set(db, 'selection', requested)
        cursor = db.execute('''INSERT INTO messages(role,text,model,status,created_at,request_id,requested_model)
                               VALUES(?,?,?,?,?,?,?)''',
                            ('user', message, model, 'queued', time.time(), request_id, requested))
        user_id = cursor.lastrowid
        cursor = db.execute('''INSERT INTO messages(role,text,model,status,created_at,parent_id)
                               VALUES(?,?,?,?,?,?)''',
                            ('office', '', model, 'queued', time.time(), user_id))
        reply_id = cursor.lastrowid
        receipt = _receipt(request_id, user_id, reply_id, model)
        db.commit()
    _ensure_worker()
    return receipt


def _receipt(request_id, user_id, reply_id, model):
    return {'accepted': True, 'request_id': request_id, 'user_id': user_id,
            'reply_id': reply_id, 'model': model}


def _retry_receipt(db, row, message, requested):
    if row['text'] != message or (row['requested_model'] or row['model']) != requested:
        raise ValueError('That request_id was already used for a different Ask message')
    reply = db.execute("SELECT id FROM messages WHERE role='office' AND parent_id=?", (row['id'],)).fetchone()
    if not reply:
        raise RuntimeError('Ask receipt is missing its answer row')
    return _receipt(row['request_id'], row['id'], reply['id'], row['model'])


def _ensure_worker():
    global WORKER
    with LOCK:
        if WORKER and WORKER.is_alive():
            return
        with connect() as db:
            queued = db.execute("SELECT MIN(next_attempt_at) due FROM messages WHERE role='user' AND status='queued'").fetchone()['due']
            working = db.execute("SELECT 1 FROM messages WHERE role='user' AND status='working' LIMIT 1").fetchone()
        if queued is None or working:
            return
        if queued > time.time():
            _schedule_retry(queued - time.time())
            return
        WORKER = threading.Thread(target=_drain, name='office-ask', daemon=True)
        WORKER.start()


def _schedule_retry(delay):
    global RETRY_TIMER
    if RETRY_TIMER:
        RETRY_TIMER.cancel()
    RETRY_TIMER = threading.Timer(max(.1, delay), _ensure_worker)
    RETRY_TIMER.daemon = True
    RETRY_TIMER.start()


def _claim():
    with LOCK, connect() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute('''UPDATE messages SET status='working' WHERE id=(
                                   SELECT id FROM messages WHERE role='user' AND status='queued'
                                   AND next_attempt_at <= ?
                                   ORDER BY id LIMIT 1
                                ) AND NOT EXISTS (
                                  SELECT 1 FROM messages WHERE role='user' AND status='working'
                                 )''', (time.time(),)).rowcount
        if changed != 1:
            db.commit()
            return None
        row = db.execute("SELECT id,text,model FROM messages WHERE role='user' AND status='working' ORDER BY id LIMIT 1").fetchone()
        reply = db.execute("SELECT id FROM messages WHERE role='office' AND parent_id=?", (row['id'],)).fetchone()
        if not reply:
            db.execute("UPDATE messages SET status='failed',completed_at=? WHERE id=?", (time.time(), row['id']))
            db.commit()
            return None
        db.execute("UPDATE messages SET status='working' WHERE id=? AND status='queued'", (reply['id'],))
        db.commit()
        return {'user_id': row['id'], 'reply_id': reply['id'], 'text': row['text'], 'model': row['model']}


def _finish(turn, answer, status):
    with LOCK, connect() as db:
        now = time.time()
        db.execute("UPDATE messages SET status=?,completed_at=?,recovering=0 WHERE id=? AND status='working'",
                   (status, now, turn['user_id']))
        db.execute("UPDATE messages SET text=?,status=?,completed_at=? WHERE id=? AND status='working'",
                   (answer, status, now, turn['reply_id']))


def _retry(turn, error):
    with LOCK, connect() as db:
        row = db.execute('SELECT failure_count FROM messages WHERE id=?', (turn['user_id'],)).fetchone()
        failures = (row['failure_count'] if row else 0) + 1
        due = time.time() + min(3600, 60 * 3 ** min(failures - 1, 4))
        db.execute("UPDATE messages SET status='queued',recovering=1,failure_count=?,next_attempt_at=? "
                   "WHERE id=? AND status='working'", (failures, due, turn['user_id']))
        db.execute("UPDATE messages SET text=?,status='queued',completed_at=NULL WHERE id=? AND status='working'",
                   (f'Both providers failed; Office will retry automatically. {error}'[:1000], turn['reply_id']))


def _drain():
    global WORKER
    while True:
        turn = _claim()
        if turn:
            try:
                answer = _answer_turn(turn['text'], turn['model'], turn['reply_id'])
                _finish(turn, answer, 'completed')
            except Exception as exc:
                alternate = ('gpt-6-sol' if turn['model'].startswith('claude:') else
                             'claude:sonnet' if turn['model'].startswith('gpt-') else None)
                if alternate:
                    try:
                        continuation = (
                            f'The {turn["model"]} provider failed while handling this request. '
                            'Inspect its recorded work and current external state before acting. '
                            'Continue only unfinished work; do not repeat a completed send, publish, '
                            'merge, or payment. Verify the outcome before answering.\n\n'
                            f'Original request:\n{turn["text"]}')
                        answer = _answer_turn(continuation, alternate, turn['reply_id'])
                        with connect() as db:
                            db.execute('UPDATE messages SET model=? WHERE id IN (?,?)',
                                       (alternate, turn['user_id'], turn['reply_id']))
                        _finish(turn, answer, 'completed')
                        continue
                    except Exception as fallback_exc:
                        exc = RuntimeError(f'{type(exc).__name__}: {exc}; '
                                           f'{alternate} also failed: {type(fallback_exc).__name__}: {fallback_exc}')
                error = f'Office could not finish this answer: {type(exc).__name__}: {exc}'[:1000]
                _retry(turn, error)
            continue
        with LOCK:
            with connect() as db:
                queued = db.execute("SELECT MIN(next_attempt_at) due FROM messages WHERE role='user' AND status='queued'").fetchone()['due']
                working = db.execute("SELECT 1 FROM messages WHERE role='user' AND status='working' LIMIT 1").fetchone()
            if queued is not None and queued <= time.time() and not working:
                continue
            if WORKER is threading.current_thread():
                WORKER = None
            if queued is not None and not working:
                _schedule_retry(queued - time.time())
            return


def recover():
    with LOCK, connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('''SELECT u.id user_id,o.id reply_id
                             FROM messages u JOIN messages o ON o.parent_id=u.id
                             WHERE u.role='user' AND o.role='office' AND
                                   (u.status='working' OR o.status='working' OR
                                    (o.status='failed' AND o.text=?))
                             ORDER BY u.id''', (INTERRUPTED,)).fetchall()
        for row in rows:
            db.execute("UPDATE messages SET status='queued',completed_at=NULL,recovering=1 WHERE id=?",
                       (row['user_id'],))
            db.execute("UPDATE messages SET text='',status='queued',completed_at=NULL WHERE id=?",
                       (row['reply_id'],))
        db.execute('''UPDATE messages SET status='failed',completed_at=?
                      WHERE role='user' AND status='working' AND NOT EXISTS
                      (SELECT 1 FROM messages o WHERE o.parent_id=messages.id)''', (time.time(),))
        db.commit()
    _ensure_worker()
    return {'resuming': len(rows)}


def _bridge(db, model, before_id):
    rows = db.execute('''SELECT role,text,model FROM messages
                         WHERE id < ? AND ((role='user' AND status IN ('completed','failed'))
                                           OR (role='office' AND status='completed'))
                         ORDER BY id DESC LIMIT 20''', (before_id,)).fetchall()
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
        with connect() as db:
            _set(db, 'claude_session_id', session_id)
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


def _turn_input(turn):
    return '\n'.join(part.get('text', '') for item in turn.get('items', [])
                     if item.get('type') == 'userMessage'
                     for part in item.get('content', []) if part.get('type') == 'text')


def _turn_answer(turn):
    answers = [item.get('text', '') for item in turn.get('items', [])
               if item.get('type') == 'agentMessage' and item.get('text')]
    return answers[-1] if answers else ''


def _saved_turn(client, thread_id, user_id, message, request_id, provider_turn_id):
    turns = client.request('thread/read', {'threadId': thread_id, 'includeTurns': True}, 30)['thread'].get('turns', [])
    if provider_turn_id:
        exact = next((turn for turn in turns if turn.get('id') == provider_turn_id), None)
        if exact:
            return exact
    marker = f'[Office request: {request_id}]' if request_id else None
    for turn in reversed(turns):
        value = _turn_input(turn)
        if marker and marker in value:
            return turn
        if value == message or (not marker and value.endswith('\n' + message)):
            return turn
    return None


def _record_turn(user_id, turn_id):
    with connect() as db:
        db.execute("UPDATE messages SET provider_turn_id=? WHERE id=? AND status='working'",
                   (turn_id, user_id))


def _answer_turn(message, model, reply_id):
    client = None
    answer = ''
    try:
        with connect() as db:
            user_id = db.execute("SELECT parent_id FROM messages WHERE id=?", (reply_id,)).fetchone()['parent_id']
            bridge = _bridge(db, model, user_id)
            user = db.execute("SELECT request_id,provider_turn_id,recovering FROM messages WHERE id=?", (user_id,)).fetchone()
        if model.startswith('claude:'):
            resume_note = ('Office restarted while this request was in progress. Inspect the existing session '
                           'and current external state, continue the unfinished work, and do not repeat completed actions.\n\n') if user['recovering'] else ''
            answer = _claude_answer(resume_note + message, model, bridge)
            if not answer:
                raise RuntimeError('Claude Code returned an empty answer')
            return answer
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
        previous = (_saved_turn(client, thread_id, user_id, message, user['request_id'],
                                user['provider_turn_id']) if user['recovering'] else None)
        if previous and previous.get('status') == 'completed':
            answer = _turn_answer(previous)
            if answer:
                return answer
        if previous and previous.get('status') == 'inProgress':
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                time.sleep(2)
                previous = _saved_turn(client, thread_id, user_id, message, user['request_id'],
                                       previous['id'])
                if previous and previous.get('status') != 'inProgress':
                    break
            if previous and previous.get('status') == 'completed':
                answer = _turn_answer(previous)
                if answer:
                    return answer
        marker = f'[Office request: {user["request_id"]}]\n' if user['request_id'] else ''
        if previous:
            prompt = (marker + 'Office restarted during the preceding turn. Reconcile its recorded work and '
                      'the current external state before continuing. Do not repeat completed actions. '
                      'Answer the original request once the outcome is verified.\n\nOriginal request:\n' + message)
        else:
            prompt = marker + (bridge or '') + message
        started = client.request('turn/start', {'threadId': thread_id,
                                                'input': [{'type': 'text', 'text': prompt}], 'model': model}, 30)
        _record_turn(user_id, started['turn']['id'])
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                item = client.receive(min(30, max(.1, deadline - time.monotonic())))
            except TimeoutError:
                # Notifications can be lost while the provider's durable turn has finished.
                # Read the recorded turn before waiting again, so one finished answer
                # cannot hold every later Ask message until the 30-minute timeout.
                saved = _saved_turn(client, thread_id, user_id, message,
                                    user['request_id'], started['turn']['id'])
                if saved and saved.get('status') == 'completed':
                    answer = _turn_answer(saved) or answer
                    if answer:
                        return answer
                if saved and saved.get('status') in ('failed', 'interrupted', 'cancelled'):
                    raise RuntimeError('Codex turn ' + saved['status'])
                continue
            if item.get('method') == 'item/completed':
                output = item.get('params', {}).get('item', {})
                if output.get('type') == 'agentMessage':
                    answer = output.get('text', '')
                    with connect() as db:
                        db.execute("UPDATE messages SET text=? WHERE id=? AND status='working'", (answer, reply_id))
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
        return answer
    finally:
        if client:
            client.close()
