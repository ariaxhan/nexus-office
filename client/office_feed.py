"""Shared, provenance-first Office feed store and user feedback.

Publication is an explicit operation. Collection alone never creates a post.
"""
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import office_preferences as preferences
import private_state

CATEGORIES = {'politics', 'world', 'ai', 'science', 'culture', 'work', 'listen', 'history', 'business', 'technology'}
FORMATS = {'story', 'image', 'chart', 'gallery', 'timeline', 'quote', 'paper', 'listen', 'question', 'curiosity', 'work', 'thread_update'}
REACTIONS = {'love', 'dislike', 'save'}


def path():
    return preferences.path().with_name('office-feed.sqlite3')


def connect():
    db_path = path()
    private_state.ensure_dir(db_path.parent)
    db = sqlite3.connect(db_path, timeout=10)
    db_path.chmod(0o600)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY, published_at REAL NOT NULL, updated_at REAL NOT NULL,
            category TEXT NOT NULL, format TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL, payload TEXT NOT NULL, model TEXT NOT NULL,
            source_hash TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS posts_time ON posts(published_at DESC);
        CREATE INDEX IF NOT EXISTS posts_category_time ON posts(category,published_at DESC);
        CREATE TABLE IF NOT EXISTS reactions (
            post_id TEXT NOT NULL REFERENCES posts(id), kind TEXT NOT NULL,
            active INTEGER NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(post_id,kind)
        );
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT NOT NULL REFERENCES posts(id),
            body TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_hash TEXT NOT NULL,
            model TEXT NOT NULL, started_at REAL NOT NULL, latency_ms REAL NOT NULL,
            outcome TEXT NOT NULL, detail TEXT NOT NULL, post_id TEXT
        );
        CREATE TABLE IF NOT EXISTS followed_threads (
            label TEXT PRIMARY KEY, terms TEXT NOT NULL,
            active INTEGER NOT NULL, updated_at REAL NOT NULL
        );
    ''')
    return db


def record_run(source_hash, model, started_at, latency_ms, outcome, detail='', post_id=None):
    """A small evaluation receipt; feedback can be joined by post_id later."""
    with connect() as db:
        db.execute('''INSERT INTO model_runs(source_hash,model,started_at,latency_ms,outcome,detail,post_id)
                      VALUES(?,?,?,?,?,?,?)''',
                   (source_hash, model, started_at, latency_ms, outcome, str(detail)[:500], post_id))
    export_model_records()


def export_model_records():
    """Rebuild a Tradition RunRecord-compatible, non-content evaluation file."""
    with connect() as db:
        rows = db.execute('''SELECT m.id,m.source_hash,m.model,m.started_at,m.latency_ms,
                                    m.outcome,m.post_id FROM model_runs m ORDER BY m.id''').fetchall()
        reactions = {row['post_id']: {} for row in db.execute('SELECT DISTINCT post_id FROM reactions')}
        for row in db.execute('SELECT post_id,kind,active FROM reactions'):
            reactions[row['post_id']][row['kind']] = bool(row['active'])
        replies = {row['post_id']: row['n'] for row in db.execute(
            'SELECT post_id,COUNT(*) AS n FROM replies GROUP BY post_id')}
    records = []
    for row in rows:
        if row['outcome'] == 'dry_run':
            continue
        feedback = reactions.get(row['post_id'], {})
        grade = .5 + .25 * bool(feedback.get('love')) + .15 * bool(feedback.get('save')) - .4 * bool(feedback.get('dislike'))
        records.append(json.dumps({
            'run_id': 'office-feed-' + str(row['id']), 'source_hash': row['source_hash'],
            'post_id': row['post_id'], 'model': row['model'], 'route_class': 'office_editorial',
            'task_category': 'feed', 'success': row['outcome'] == 'published',
            'outcome': row['outcome'], 'latency_ms': row['latency_ms'],
            'judge_score': max(0, min(1, grade)) if row['outcome'] == 'published' else 0,
            'reply_count': replies.get(row['post_id'], 0),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(row['started_at']))
        }, sort_keys=True))
    target = path().with_name('tradition-office-editorial.jsonl')
    private_state.atomic_write_text(target, '\n'.join(records) + ('\n' if records else ''))
    return target


def model_scores():
    with connect() as db:
        rows = db.execute("SELECT model,outcome FROM model_runs WHERE outcome!='dry_run'").fetchall()
        reactions = db.execute('''SELECT m.model,r.kind,r.active FROM model_runs m
                                  JOIN reactions r ON r.post_id=m.post_id''').fetchall()
    scores = {}
    for row in rows:
        score = scores.setdefault(row['model'], {'success': 1, 'failure': 1, 'runs': 0})
        score['runs'] += 1
        score['success' if row['outcome'] == 'published' else 'failure'] += 1
    for row in reactions:
        score = scores[row['model']]
        if row['active'] and row['kind'] in ('love', 'save'):
            score['success'] += 1
        if row['active'] and row['kind'] == 'dislike':
            score['failure'] += 1
    return scores


def _source(value):
    if not isinstance(value, dict):
        raise ValueError('A source needs a title and URL')
    url = value.get('url', '')
    parsed = urlparse(url)
    local_path = ((url.startswith('/api/media/detail?id=') and len(url) < 500)
                  or (url.startswith('/api/buzz/detail?id=') and len(url) < 500)
                  or url == '/#watch')
    if not local_path and (parsed.scheme not in ('https', 'http') or not parsed.netloc):
        raise ValueError('Source URL must be HTTP, HTTPS, or an Office detail')
    title = value.get('title', '')
    if not isinstance(title, str) or not title.strip() or len(title) > 240:
        raise ValueError('Source title is required')
    return {'title': title.strip(), 'url': url, 'published_at': value.get('published_at')}


def publish(item):
    """Accept only locally authored posts with traceable source records."""
    if not isinstance(item, dict):
        raise ValueError('Invalid post')
    identifier = item.get('id', '')
    source_hash = item.get('source_hash', '')
    model = item.get('model', '')
    title = item.get('title', '')
    body = item.get('body', '')
    category = item.get('category', '')
    format_ = item.get('format', '')
    if not all(isinstance(v, str) for v in (identifier, source_hash, model, title, body, category, format_)):
        raise ValueError('Invalid post fields')
    if not 1 <= len(identifier) <= 160 or not 16 <= len(source_hash) <= 128:
        raise ValueError('Invalid post identity')
    if not model.startswith('local:'):
        raise ValueError('Feed posts require a local model receipt')
    if category not in CATEGORIES or format_ not in FORMATS:
        raise ValueError('Unknown feed category or format')
    if not title.strip() or len(title) > 240 or not body.strip() or len(body) > 2000:
        raise ValueError('Post copy is empty or too long')
    sources = item.get('sources')
    if not isinstance(sources, list) or not sources or len(sources) > 12:
        raise ValueError('Posts need source records')
    sources = [_source(source) for source in sources]
    media = item.get('media', [])
    if not isinstance(media, list) or len(media) > 8:
        raise ValueError('Invalid media')
    for asset in media:
        if not isinstance(asset, dict) or not asset.get('source_url') or not asset.get('alt'):
            raise ValueError('Media needs source and alt text')
        _source({'title': asset['alt'], 'url': asset['source_url']})
        if asset.get('kind') == 'audio' and not asset.get('url', '').startswith('/api/media/content?id='):
            raise ValueError('Audio must use the Office podcast catalog')
    now = time.time()
    payload = dict(item, sources=sources, media=media)
    payload['published_at'] = now
    with connect() as db:
        db.execute('''INSERT INTO posts(id,published_at,updated_at,category,format,title,body,payload,model,source_hash)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (identifier, now, now, category, format_, title.strip(), body.strip(), json.dumps(payload), model, source_hash))
    return payload


def listing(category='all', cursor=0, limit=40):
    if category not in CATEGORIES and category not in ('all', 'latest', 'saved', 'following'):
        raise ValueError('Unknown category')
    offset = max(0, int(cursor))
    limit = min(80, max(1, int(limit)))
    with connect() as db:
        if category == 'following':
            threads = [json.loads(row['terms']) for row in db.execute(
                'SELECT terms FROM followed_threads WHERE active=1')]
            posts = []
            for row in db.execute('SELECT payload FROM posts ORDER BY published_at DESC'):
                post = json.loads(row['payload'])
                text = (post['title'] + ' ' + post['body']).lower()
                if any(all(re.search(r'\b' + re.escape(term) + r'\b', text) for term in terms)
                       for terms in threads):
                    posts.append(post)
            selected = posts[offset:offset + limit]
            feedback = _feedback(db, [post['id'] for post in selected])
            return {'items': [dict(post, feedback=feedback[post['id']]) for post in selected],
                    'next_cursor': offset + limit if len(posts) > offset + limit else None,
                    'checked_at': time.time(), 'categories': sorted(CATEGORIES)}
        if category in ('all', 'latest'):
            rows = db.execute('SELECT category,published_at,payload FROM posts').fetchall()
            parsed = [(row['category'], float(row['published_at']), json.loads(row['payload'])) for row in rows]
            if category == 'all':
                weights = {}
                for row in db.execute('''SELECT p.category,r.kind,COUNT(*) AS n FROM reactions r
                                         JOIN posts p ON p.id=r.post_id
                                         WHERE r.active=1 AND r.kind IN ('save','love','dislike')
                                         GROUP BY p.category,r.kind'''):
                    weights[row['category']] = weights.get(row['category'], 0) + row['n'] * {
                        'save': 2, 'love': 1, 'dislike': -2
                    }[row['kind']]
                # Personal signals can move a topic forward, while the bounded
                # 18 hour shift keeps newer events and deliberate breadth visible.
                parsed.sort(key=lambda item: (item[1] + max(-3, min(3, weights.get(item[0], 0))) * 6 * 3600,
                                              item[1]), reverse=True)
            else:
                parsed.sort(key=lambda item: item[1], reverse=True)
            selected = parsed[offset:offset + limit]
            ids = [item[2]['id'] for item in selected]
            feedback = _feedback(db, ids)
            items = [dict(item[2], feedback=feedback[item[2]['id']]) for item in selected]
            return {'items': items,
                    'next_cursor': offset + limit if len(parsed) > offset + limit else None,
                    'checked_at': time.time(), 'categories': sorted(CATEGORIES)}
        where = ''
        args = []
        if category == 'saved':
            where = "WHERE EXISTS(SELECT 1 FROM reactions r WHERE r.post_id=p.id AND r.kind='save' AND r.active=1)"
        elif category != 'all':
            where = 'WHERE p.category=?'
            args.append(category)
        rows = db.execute(f'''SELECT p.payload FROM posts p {where}
                             ORDER BY p.published_at DESC LIMIT ? OFFSET ?''', (*args, limit + 1, offset)).fetchall()
        ids = [json.loads(row['payload'])['id'] for row in rows[:limit]]
        feedback = _feedback(db, ids)
        items = [dict(json.loads(row['payload']), feedback=feedback.get(identifier, {}))
                 for row, identifier in zip(rows[:limit], ids)]
        return {'items': items, 'next_cursor': offset + limit if len(rows) > limit else None,
                'checked_at': time.time(), 'categories': sorted(CATEGORIES)}


def _feedback(db, ids):
    if not ids:
        return {}
    marks = ','.join('?' for _ in ids)
    result = {identifier: {'reactions': {}, 'reply_count': 0} for identifier in ids}
    for row in db.execute(f'SELECT post_id,kind,active FROM reactions WHERE post_id IN ({marks})', ids):
        result[row['post_id']]['reactions'][row['kind']] = bool(row['active'])
    for row in db.execute(f'SELECT post_id,COUNT(*) AS n FROM replies WHERE post_id IN ({marks}) GROUP BY post_id', ids):
        result[row['post_id']]['reply_count'] = row['n']
    return result


def detail(identifier):
    with connect() as db:
        row = db.execute('SELECT payload FROM posts WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise FileNotFoundError('Feed post not found')
        post = json.loads(row['payload'])
        post['feedback'] = _feedback(db, [identifier])[identifier]
        post['replies'] = [dict(row) for row in db.execute(
            'SELECT id,body,created_at FROM replies WHERE post_id=? ORDER BY id DESC LIMIT 100', (identifier,))]
        return post


def withdraw(identifier, reason):
    """Editorial rollback; callers must run locally, never through a public route."""
    with connect() as db:
        if not db.execute('SELECT 1 FROM posts WHERE id=?', (identifier,)).fetchone():
            raise FileNotFoundError('Feed post not found')
        db.execute('DELETE FROM reactions WHERE post_id=?', (identifier,))
        db.execute('DELETE FROM replies WHERE post_id=?', (identifier,))
        db.execute('DELETE FROM posts WHERE id=?', (identifier,))
        db.execute("UPDATE model_runs SET outcome='withdrawn',detail=?,post_id=NULL WHERE post_id=?",
                   (reason[:500], identifier))
    export_model_records()


def react(body):
    identifier, kind, active = body.get('id'), body.get('kind'), body.get('active')
    if kind not in REACTIONS or type(active) is not bool:
        raise ValueError('Invalid reaction')
    with connect() as db:
        if not db.execute('SELECT 1 FROM posts WHERE id=?', (identifier,)).fetchone():
            raise FileNotFoundError('Feed post not found')
        db.execute('''INSERT INTO reactions(post_id,kind,active,updated_at) VALUES(?,?,?,?)
                      ON CONFLICT(post_id,kind) DO UPDATE SET active=excluded.active,updated_at=excluded.updated_at''',
                   (identifier, kind, int(active), time.time()))
        if active and kind in ('love', 'dislike'):
            db.execute('UPDATE reactions SET active=0,updated_at=? WHERE post_id=? AND kind=?',
                       (time.time(), identifier, 'dislike' if kind == 'love' else 'love'))
    export_model_records()
    return {'id': identifier, 'feedback': detail(identifier)['feedback']}


def reply(body):
    identifier, message = body.get('id'), body.get('body')
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 4000:
        raise ValueError('Reply is empty or too long')
    with connect() as db:
        if not db.execute('SELECT 1 FROM posts WHERE id=?', (identifier,)).fetchone():
            raise FileNotFoundError('Feed post not found')
        cursor = db.execute('INSERT INTO replies(post_id,body,created_at) VALUES(?,?,?)',
                            (identifier, message.strip(), time.time()))
        result = {'id': cursor.lastrowid, 'post_id': identifier, 'body': message.strip()}
    export_model_records()
    return result


def threads():
    with connect() as db:
        rows = db.execute('SELECT label,updated_at FROM followed_threads WHERE active=1 ORDER BY updated_at DESC').fetchall()
        return {'items': [dict(row) for row in rows]}


def follow(body):
    label, active = body.get('label'), body.get('active', True)
    if not isinstance(label, str) or not 2 <= len(label.strip()) <= 100 or type(active) is not bool:
        raise ValueError('Enter a thread name of 2 to 100 characters')
    label = ' '.join(label.split())
    terms = [term for term in re.findall(r'[a-z0-9]{3,}', label.lower())
             if term not in ('the', 'and', 'for', 'from', 'with', 'about')]
    if not terms:
        raise ValueError('Thread needs a specific topic')
    with connect() as db:
        db.execute('''INSERT INTO followed_threads(label,terms,active,updated_at) VALUES(?,?,?,?)
                      ON CONFLICT(label) DO UPDATE SET active=excluded.active,updated_at=excluded.updated_at''',
                   (label, json.dumps(terms), int(active), time.time()))
    return {'label': label, 'active': active}
