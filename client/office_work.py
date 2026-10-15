"""The Watch tab's work view: live buckets per desk, from the canonical pair.

GitHub is the truth for what an issue IS (open or closed, and when). Coordinator
receipts are the truth for whether anyone is working it. An issue is BEING
WORKED ON only with a lane claim — a Tower flight in state 'working', or a TBS
coordinator line saying a worker is running — AND receipt activity inside
WINDOW (24 h). DONE is closed on GitHub within DONE_DAYS, shown with its date.
A claim or activity older than the window is STALLED, with its last-activity
time. Everything else open is UP NEXT. No receipt inside the window, no claim
of work: the word 'running' is never produced from a stale line again (the
tbs#278 zombie, frozen 2026-10-07, presented as running for a week).

Built stale-while-revalidate: a request never blocks on GitHub. The first
call returns an empty view marked refreshing and builds in a thread; later
calls serve the last build until it is older than MEMO_S, then refresh in
the background the same way.
"""
import datetime as dt
import re
import threading
import time
from pathlib import Path

import office_github as github
import office_objects as objects

WINDOW = 24 * 3600
DONE_DAYS = 7
MEMO_S = 120
CAPS = {'worked_on': 15, 'stalled': 10, 'up_next': 10, 'done': 12}

DESKS = [
    {'id': 'nexus', 'name': 'Nexus Office', 'repos': ['ariaxhan/nexus-office']},
    {'id': 'tbs', 'name': 'Thinking Brain School',
     'repos': ['ariaxhan/thinking-brain-school', 'Thinking-Brain-School/tbs',
               'Thinking-Brain-School/tbs-www', 'Thinking-Brain-School/tbs-curriculum',
               'Thinking-Brain-School/tbs-backend']},
    {'id': 'matra', 'name': 'Matra',
     'repos': ['ariablinkbuild/matra-suite', 'jessstrom/matra']},
]
TBS_DOC_REPO = 'ariaxhan/thinking-brain-school'

_HEADING = re.compile(r'^### \[[^\]]*\]\(https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)\)')
_COORD = re.compile(r'^- \*\*Coordinator:\*\* (\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC\.')


def parse_iso(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def iso(epoch):
    if epoch is None:
        return None
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _stamp_epoch(month, day, hour, minute, now):
    try:
        stamp = dt.datetime(now.year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    if stamp > now + dt.timedelta(hours=1):
        stamp = stamp.replace(year=now.year - 1)
    return stamp.timestamp()


def doc_claims(text, now=None):
    """{(repo, number): {'activity': epoch, 'running': bool}} from office-doc text.

    The TBS coordinator's published receipts: one Coordinator line per item,
    stamped MM-DD HH:MM UTC. Mirrors bin/office_doc.py's own reading of them.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    claims, current = {}, None
    for line in text.split('\n'):
        heading = _HEADING.match(line)
        if heading:
            current = (heading.group(1), int(heading.group(2)))
            continue
        if line.startswith('#'):
            current = None
            continue
        coord = _COORD.match(line)
        if coord and current:
            epoch = _stamp_epoch(*(int(g) for g in coord.groups()), now)
            if epoch is not None:
                claims[current] = {'activity': epoch, 'running': 'Worker running' in line}
    return claims


def tower_activity(snapshot):
    """{(repo, number): {'claim': bool, 'activity': epoch|None}} from the Tower board."""
    out = {}
    tower = ((snapshot or {}).get('automation') or {}).get('tower') or {}
    for row in tower.get('issues') or []:
        try:
            key = (row['repo'], int(row['number']))
        except (KeyError, TypeError, ValueError):
            continue
        stamps = [row.get('progress_at'), row.get('since')]
        activity = max((float(s) for s in stamps if s), default=None)
        out[key] = {'claim': row.get('state') == 'working', 'activity': activity}
    return out


def classify(claim, activity, created, now):
    """The bucket one open issue earns. Receipts only; no receipt, no claim of work."""
    if claim and activity is not None and now - activity <= WINDOW:
        return 'worked_on'
    if activity is not None:
        return 'stalled' if now - activity > WINDOW else 'up_next'
    if created is not None and now - created > WINDOW:
        return 'stalled'
    return 'up_next'


def _station_words(snapshot):
    """{(repo, number): epoch} — the bot's last word per issue, from the snapshot."""
    out = {}
    for station in (snapshot or {}).get('stations') or []:
        repo = station.get('repo')
        for row in station.get('issues') or []:
            stamp = parse_iso(row.get('last_word_at')) if row.get('bot_last') else None
            if stamp is not None:
                out[(repo, row.get('number'))] = stamp
    return out


def _doc_text():
    try:
        root = next((r for r in objects.roots().values() if r.get('name') == TBS_DOC_REPO), None)
        if not root:
            return ''
        path = Path(root['path']) / '_meta' / 'state' / 'office-doc.md'
        return path.read_text() if path.exists() else ''
    except Exception:  # noqa: BLE001 - claims are evidence, never a blocker
        return ''


def _issues(access, known, repo, state):
    who, token = github.identity(access, repo, known)
    rows, page = [], 1
    while True:
        batch, _ = github.fetch(
            f'repos/{repo}/issues?state={state}&sort=updated&direction=desc&per_page=100&page={page}',
            who, token)
        rows.extend(row for row in batch if 'pull_request' not in row)
        if len(batch) < 100 or state == 'closed' or page >= 3:
            return rows
        page += 1


def _build(world):
    now = time.time()
    snapshot = world.snapshot or {}
    # Registered = on a station, a project root, or one of this view's own desks.
    # A desk repo missing from the current snapshot (a fetch error upstream) must
    # still resolve: the desk list is itself the registration for its repos.
    known = {row['repo'] for row in snapshot.get('stations') or []}
    try:
        known.update(root.get('name') for root in objects.roots().values())
    except Exception:  # noqa: BLE001 - an aid to identity resolution, never a blocker
        pass
    for desk in DESKS:
        known.update(desk['repos'])
    known.discard(None)
    access = world.access()
    tower = tower_activity(snapshot)
    claims = doc_claims(_doc_text())
    words = _station_words(snapshot)
    desks = []
    for desk in DESKS:
        result = {'id': desk['id'], 'name': desk['name'], 'repos': desk['repos'],
                  'worked_on': [], 'stalled': [], 'up_next': [], 'done': [],
                  'counts': {'worked_on': 0, 'stalled': 0, 'up_next': 0, 'open': 0},
                  'errors': []}
        for repo in desk['repos']:
            try:
                open_rows = _issues(access, known, repo, 'open')
                closed_rows = _issues(access, known, repo, 'closed')
            except Exception as exc:  # noqa: BLE001 - one repo down must not blank the tab
                result['errors'].append(f'{repo}: {type(exc).__name__}: {exc}'[:160])
                continue
            for row in open_rows:
                key = (repo, row['number'])
                evidence = [tower.get(key, {}).get('activity'),
                            claims.get(key, {}).get('activity'), words.get(key)]
                activity = max((e for e in evidence if e is not None), default=None)
                claim = bool(tower.get(key, {}).get('claim') or claims.get(key, {}).get('running'))
                bucket = classify(claim, activity, parse_iso(row.get('created_at')), now)
                result[bucket].append({
                    'repo': repo, 'number': row['number'], 'title': row.get('title') or '',
                    'url': row.get('html_url') or '', 'activity_at': iso(activity),
                    'updated_at': row.get('updated_at'), 'created_at': row.get('created_at')})
                result['counts'][bucket] += 1
                result['counts']['open'] += 1
            for row in closed_rows:
                closed = parse_iso(row.get('closed_at'))
                if closed is not None and now - closed <= DONE_DAYS * 86400:
                    result['done'].append({
                        'repo': repo, 'number': row['number'], 'title': row.get('title') or '',
                        'url': row.get('html_url') or '', 'closed_at': row.get('closed_at')})
        result['worked_on'].sort(key=lambda r: r['activity_at'] or '', reverse=True)
        # Stalled is a triage list: longest without activity first. An item with
        # no recorded activity has been untouched since it was opened, so its
        # effective last activity is its creation — the oldest debt leads.
        result['stalled'].sort(key=lambda r: r['activity_at'] or r['created_at'] or '')
        result['up_next'].sort(key=lambda r: r['updated_at'] or '', reverse=True)
        result['done'].sort(key=lambda r: r['closed_at'] or '', reverse=True)
        for bucket, cap in CAPS.items():
            result[bucket] = result[bucket][:cap]
        desks.append(result)
    return {'built_at': iso(now), 'window_hours': 24, 'done_days': DONE_DAYS, 'desks': desks}


_MEMO = {'at': 0.0, 'data': None, 'building': False}
_LOCK = threading.Lock()


def build(world):
    """The work view, stale-while-revalidate. Never blocks a request on GitHub."""
    with _LOCK:
        fresh = _MEMO['data'] is not None and time.time() - _MEMO['at'] < MEMO_S
        if fresh:
            return _MEMO['data']
        if not _MEMO['building']:
            _MEMO['building'] = True
            threading.Thread(target=_refresh, args=(world,), daemon=True).start()
        return _MEMO['data'] or {'built_at': None, 'refreshing': True, 'desks': []}


def _refresh(world):
    try:
        data = _build(world)
        with _LOCK:
            _MEMO['data'], _MEMO['at'] = data, time.time()
    except Exception as exc:  # noqa: BLE001 - surface the failure, keep the last good view
        with _LOCK:
            if _MEMO['data'] is None:
                _MEMO['data'] = {'built_at': None, 'desks': [],
                                 'error': f'{type(exc).__name__}: {exc}'[:200]}
            _MEMO['at'] = time.time()
    finally:
        with _LOCK:
            _MEMO['building'] = False
