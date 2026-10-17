"""Config-driven GitHub inputs to the existing Tower github-work ledger.

No model calls. Run python3 -m nexus.input_watch from the repository root.
"""
import argparse
import datetime as dt
import fcntl
import json
import pathlib
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HOME = pathlib.Path.home()
STATE = HOME / 'Library/Application Support/nexus-jobs/state/input-watch.json'
LEDGER = HOME / 'Library/Application Support/nexus/ledger.sqlite'
PLAN_ID = 'plan_ba61e8c006b6'
TOKEN = None
CONFIG = pathlib.Path(__file__).resolve().parents[1] / 'input-watch.json'
CLASSES = ('issue_comments', 'issues', 'pr_comments', 'pr_review_comments', 'failed_check_runs')
AUTOMATION_AUTHORS = {'github-actions[bot]', 'dependabot[bot]', 'github-actions', 'codecov[bot]'}

def token():
    global TOKEN
    if TOKEN is None:
        out = subprocess.run([str(HOME / 'local/gh/bin/gh'), 'auth', 'token'],
                             capture_output=True, text=True, timeout=30)
        TOKEN = out.stdout.strip()
    return TOKEN

def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)

def insert_task(title, brief, dedupe):
    db = sqlite3.connect(LEDGER, timeout=15)
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM tasks WHERE dedupe_key=?', (dedupe,)).fetchone():
            return 'duplicate'
        db.execute(
            'INSERT INTO tasks (id, plan_id, origin, title, reason, state, dedupe_key, created_at, objective) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (f"task_{uuid.uuid4().hex[:12]}", PLAN_ID, 'github-work', title,
             'unified input wake', 'accepted', dedupe, time.time(), brief))
        db.commit()
        return 'registered'
    finally:
        db.close()


def stamp(now=None):
    return dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def config(path=CONFIG):
    data = json.loads(path.read_text())['repos']
    for repo, classes in data.items():
        if len(repo.split('/')) != 2 or any(c not in CLASSES for c in classes):
            raise ValueError(f'invalid input configuration: {repo}')
    return data


class Backoff(Exception):
    def __init__(self, status, seconds=1800):
        self.status, self.seconds = status, seconds
        super().__init__(f'GitHub {status}; backoff {seconds}s')


def request(path, etag=None, query=None):
    headers = {'Authorization': f'Bearer {token()}', 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    if etag:
        headers['If-None-Match'] = etag
    payload = json.dumps({'query': query}).encode() if query else None
    req = urllib.request.Request('https://api.github.com' + path, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.load(response)
            if isinstance(data, dict) and data.get('errors'):
                if any(e.get('type') == 'RATE_LIMITED' for e in data['errors']):
                    raise Backoff(403)
                raise RuntimeError(str(data['errors']))
            return data, response.headers.get('ETag'), False
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return [], etag, True
        if error.code in (403, 429):
            delay = max(1800, int(error.headers.get('Retry-After', '0')))
            raise Backoff(error.code, delay) from error
        raise


def normalize(repo, cls, item, metadata=None):
    """One shape, one stable identity, one task path for all input classes."""
    if cls not in CLASSES:
        raise ValueError(cls)
    check = cls == 'failed_check_runs'
    identity = item.get('databaseId') if check else item.get('id')
    if identity is None:
        identity = item['id']
    created = item.get('completedAt') if check else item['created_at']
    if not created:
        raise ValueError('input has no event timestamp')
    url = (item.get('detailsUrl') or item.get('url')) if check else item.get('html_url')
    body = item.get('summary', '') if check else (item.get('body') or '')
    title = item.get('name', '') if check else (item.get('title') or body.split('\n')[0])
    return {'repo': repo, 'class': cls, 'id': str(identity), 'at': created, 'url': url,
            'title': title, 'body': body, 'author': (item.get('user') or {}).get('login', ''),
            'conclusion': item.get('conclusion'), 'metadata': metadata,
            'dedupe': f'input:{cls}:{identity}'}


def skip_reason(event, enabled):
    if event['class'] not in enabled:
        return 'class_disabled'
    if event['class'] == 'failed_check_runs':
        return None if event['conclusion'] in ('FAILURE', 'TIMED_OUT', 'ACTION_REQUIRED', 'STARTUP_FAILURE') else 'not_failed'
    if event['author'] in AUTOMATION_AUTHORS or event['author'].endswith('[bot]'):
        return 'automation_author'
    if '<!-- wake-lane' in event['body'] or '<!-- no-wake' in event['body']:
        return 'loop_marker'
    if event['title'].startswith('[watchdog-fix:'):
        return 'automation_filing'
    return None


def register(event):
    brief = (f"New {event['class']} input for {event['repo']} at {event['at']}.\n"
             f"{event['url']}\n\n{event['body'][:5000]}\n\n"
             "Complete the owning coordinator's work through publication on the source issue/PR "
             "or linked check/commit. Replies MUST begin <!-- wake-lane --> to prevent loops. "
             "If the newest activity is a lane's own output, close as a no-op.")
    return insert_task(f"{event['class']} {event['repo']}: {event['title'][:140]}", brief, event['dedupe'])


def count_skip(cs, reason):
    counts = cs.setdefault('skips', {})
    counts[reason] = counts.get(reason, 0) + 1


def consume(repo, source, items, cs, enabled, upper, register_task=register, metadata=None):
    """Do not commit a watermark until every matching input is durably registered."""
    summary = {'registered': 0, 'duplicate': 0, 'skipped': 0}
    for item in items:
        cls = source
        meta = None
        if source in ('issue_comments', 'pr_comments'):
            # Fresh detail GET, never another list call or cached gate.
            meta = metadata(item['issue_url'])
            cls = 'pr_comments' if 'pull_request' in meta else 'issue_comments'
            if cls != source:
                count_skip(cs, 'other_comment_class')
                summary['skipped'] += 1
                continue
        elif source == 'issues' and 'pull_request' in item:
            count_skip(cs, 'pull_request_creation')
            summary['skipped'] += 1
            continue
        event = normalize(repo, cls, item, meta)
        lower = cs['watermark'] if source == 'failed_check_runs' else cs.get('initialized_at', cs['watermark'])
        if event['at'] < lower or event['at'] > upper:
            count_skip(cs, 'outside_window')
            summary['skipped'] += 1
            continue
        reason = skip_reason(event, enabled)
        if reason:
            count_skip(cs, reason)
            summary['skipped'] += 1
        else:
            result = register_task(event)
            if result not in ('registered', 'duplicate'):
                raise RuntimeError(f'non-durable registration: {result}')
            summary[result] += 1
    return summary


def rest_path(repo, cls, cs):
    endpoint = 'issues/comments' if cls in ('issue_comments', 'pr_comments') else 'pulls/comments' if cls == 'pr_review_comments' else 'issues'
    # Keyset over updated_at, inclusive at the boundary. Always page 1: edits or
    # deletions cannot shift an unread item behind an offset cursor.
    boundary = dt.datetime.fromisoformat(cs['watermark'].replace('Z', '+00:00')).timestamp()
    params = {'since': stamp(boundary - 1), 'sort': 'updated', 'direction': 'asc', 'per_page': 100}
    if cls == 'issues':
        params['state'] = 'all'
    return f'/repos/{repo}/{endpoint}?' + urllib.parse.urlencode(params)


def checks_query(repo, cs):
    # GitHub has no repository-wide check-run list. Traverse every branch's entire
    # reachable history, including old commits whose checks only just completed.
    # One GraphQL request per poll; cursors persist until the sweep is complete.
    owner, name = repo.split('/')
    ref_cursor = json.dumps(cs.get('ref_cursor'))
    commit_cursor = json.dumps(cs.get('commit_cursor'))
    return '''query { repository(owner:%s,name:%s) {
      refs(refPrefix:"refs/heads/",first:1,after:%s) {
        pageInfo {hasNextPage endCursor}
        nodes { name target { ... on Commit { history(first:50,after:%s) {
          pageInfo {hasNextPage endCursor}
          nodes { checkSuites(first:50) { pageInfo {hasNextPage}
            nodes { checkRuns(first:100) { pageInfo {hasNextPage}
              nodes { id databaseId name completedAt conclusion detailsUrl }
            } }
          } }
        } } } }
      }
    } }''' % (json.dumps(owner), json.dumps(name), ref_cursor, commit_cursor)


def unpack_checks(data, cs):
    refs = data['data']['repository']['refs']
    items = []
    next_state = {}
    if refs['nodes']:
        history = refs['nodes'][0]['target']['history']
        for commit in history['nodes']:
            suites = commit['checkSuites']
            if suites['pageInfo']['hasNextPage']:
                raise RuntimeError('check suite overflow; watermark retained')
            for suite in suites['nodes']:
                runs = suite['checkRuns']
                if runs['pageInfo']['hasNextPage']:
                    raise RuntimeError('check run overflow; watermark retained')
                items.extend(r for r in runs['nodes'] if r['completedAt'])
        if history['pageInfo']['hasNextPage']:
            next_state = {'ref_cursor': cs.get('ref_cursor'), 'commit_cursor': history['pageInfo']['endCursor']}
        elif refs['pageInfo']['hasNextPage']:
            next_state = {'ref_cursor': refs['pageInfo']['endCursor'], 'commit_cursor': None}
    return items, next_state


def poll_class(repo, cls, cs, enabled, upper, fetch, register_task, metadata):
    fence = cs.setdefault('upper', upper) if cls == 'failed_check_runs' else upper
    if cls == 'failed_check_runs':
        data, _, _ = fetch('/graphql', query=checks_query(repo, cs))
        items, next_state = unpack_checks(data, cs)
        done = not next_state
    else:
        path = rest_path(repo, cls, cs)
        cached = cs.get('conditional', {})
        items, etag, unchanged = fetch(path, etag=cached.get('etag') if cached.get('path') == path else None)
        if unchanged:
            return {'unchanged': True}
        done = len(items) < 100
    result = consume(repo, cls, items, cs, enabled, fence, register_task, metadata)
    if cls == 'failed_check_runs':
        if done:
            cs['watermark'] = fence
            for key in ('upper', 'ref_cursor', 'commit_cursor'):
                cs.pop(key, None)
        else:
            cs.update(next_state)
    else:
        # A full page advances only to the largest fetched updated_at, inclusive.
        # Repeated boundary items dedupe. Never advance past an unobserved page.
        next_watermark = fence if done else min(fence, max(i['updated_at'] for i in items))
        if not done and next_watermark <= cs['watermark']:
            raise RuntimeError('100 inputs at one timestamp; watermark retained; reconciliation required')
        cs['watermark'] = next_watermark
        cs['conditional'] = {'path': path, 'etag': etag}
    return result


def poll(state, repos, now=None, fetch=request, register_task=register):
    upper = stamp(now)
    summary = {'at': upper, 'status': 'ok', 'inputs': {}}
    state['last_run'] = summary
    if (time.time() if now is None else now) < state.get('backoff_until', 0):
        summary['status'] = 'backoff'
        return
    # Reuse only metadata fetched in this poll; never persisted or used for gating.
    fresh = {}
    def metadata(url):
        if url not in fresh:
            fresh[url] = fetch(urllib.parse.urlsplit(url).path)[0]
        return fresh[url]
    for repo, enabled in repos.items():
        rs = state.setdefault('repos', {}).setdefault(repo, {})
        for cls in CLASSES:
            key = f'{repo}:{cls}'
            if cls not in rs:
                rs[cls] = {'watermark': upper, 'initialized_at': upper, 'skips': {}}
                summary['inputs'][key] = {'initialized': True}
                continue
            cs = rs[cls]
            cs.setdefault('initialized_at', cs['watermark'])
            if cls not in enabled:
                count_skip(cs, 'class_disabled_poll')
                continue
            try:
                summary['inputs'][key] = poll_class(repo, cls, cs, enabled, upper, fetch, register_task, metadata)
            except Backoff as error:
                state['backoff_until'] = (time.time() if now is None else now) + error.seconds
                summary.update(status='backoff', error=str(error))
                return
            except Exception as error:
                message = f'{type(error).__name__}: {error}'
                summary['inputs'][key] = {'error': message}
                summary['status'] = 'error'
                cs['last_error'] = {'at': upper, 'reason': message}


def main():
    global STATE, LEDGER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=pathlib.Path, default=CONFIG)
    parser.add_argument('--state', type=pathlib.Path, default=STATE)
    parser.add_argument('--ledger', type=pathlib.Path, default=LEDGER)
    args = parser.parse_args()
    STATE, LEDGER = args.state, args.ledger
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # Lock the directory: no second state JSON or lock file, no concurrent sweep.
    with directory_lock(STATE.parent):
        state = json.loads(STATE.read_text()) if STATE.exists() else {'repos': {}, 'backoff_until': 0}
        try:
            poll(state, config(args.config))
        finally:
            save_state(state)
    print(json.dumps(state['last_run']))
    return 1 if state['last_run']['status'] == 'error' else 0


from contextlib import contextmanager
import os


@contextmanager
def directory_lock(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
