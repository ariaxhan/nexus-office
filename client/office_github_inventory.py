"""The complete open-issue inventory across TBS, Matra and Tower, with explicit denominators.

Read-only and on demand: no poller. One batched GraphQL query per ten repos with
`totalCount`, continuation pages only where `hasNextPage`. A repo that cannot be
fetched keeps its last-good rows with their age and is counted as partial, never zero.
"""
import calendar
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import office_preferences as preferences
import private_state

TBS_ORG = 'Thinking-Brain-School'
MATRA = ('jessstrom/matra', 'jessstrom/matra-care')
MATRA_SOURCE = 'canonical Matra repositories: ' + ', '.join(MATRA)
BATCH = 10
TTL = 300
STALE_DAYS = 30
NWO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
HELD = {'hold', 'on-hold', 'blocked', 'blocked-needs-look', 'cancelled', 'canceled'}
OWNED = {'direct', 'claimed', 'in-progress', 'in progress', 'tower-v2'}
NEEDS_YOU = {'needs-you', 'needs you', 'needs-human', 'needs-aria', 'waiting', 'waiting on human', 'question', 'decision'}
PRIORITY = ('p0', 'p1', 'p2')
LOCK = threading.Lock()
MEMORY = {'at': 0.0, 'result': None}
REFRESHING = threading.Lock()
ORG_KEY = '__tbs_org_listing__'

ISSUE_FIELDS = 'number title url createdAt updatedAt author { login } labels(first: 20) { nodes { name } } comments { totalCount }'
PAGE_QUERY = ('query($o: String!, $n: String!, $after: String) { r0: repository(owner: $o, name: $n) {'
              ' isArchived issues(first: 100, after: $after, states: OPEN, orderBy: {field: CREATED_AT, direction: ASC})'
              ' { totalCount pageInfo { hasNextPage endCursor } nodes { ' + ISSUE_FIELDS + ' } } } }')


def batch_query(n):
    decl = ', '.join(f'$o{i}: String!, $n{i}: String!' for i in range(n))
    rows = '\n'.join(f'  r{i}: repository(owner: $o{i}, name: $n{i}) {{ ...Inv }}' for i in range(n))
    return (f'query({decl}) {{\n  rateLimit {{ cost remaining resetAt }}\n{rows}\n}}\n'
            'fragment Inv on Repository { isArchived issues(first: 100, states: OPEN, '
            'orderBy: {field: CREATED_AT, direction: ASC}) { totalCount pageInfo { hasNextPage endCursor } '
            'nodes { ' + ISSUE_FIELDS + ' } } }')


def graphql(query, variables, token):
    cmd = ['gh', 'api', 'graphql', '-f', 'query=' + query]
    for key, value in variables.items():
        cmd += ['-f', f'{key}={value}']
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=dict(os.environ, GH_TOKEN=token))
    try:
        body = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        body = {}
    if not body:
        raise RuntimeError((proc.stderr.strip().splitlines() or [f'gh exit {proc.returncode}'])[0][:200])
    return body


# ── the three repository sets ────────────────────────────────────────────────

def registry_path():
    explicit = os.environ.get('OFFICE_WORK_REGISTRY') or os.environ.get('NEXUS_WORK_REGISTRY')
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get('VAULT_ROOT', str(Path.home() / 'Developer/Vaults'))) / '_meta/services/nexus-work-registry.json'


def _work():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from nexus import work
    return work


def registry_rows():
    """(rows, error). Rows carry `tower` = enabled && tower_row, exactly as Tower decides."""
    work = _work()
    try:
        rows = work.registry(registry_path())
    except (OSError, ValueError, KeyError, TypeError, work.WorkError) as exc:
        return [], f'work registry unreadable: {exc}'[:240]
    return [dict(row, tower=bool(row['enabled'] and work.tower_row(row))) for row in rows], ''


def tower_set(rows, hidden):
    included = sorted(row['repo'] for row in rows if row['tower'] and row['repo'] not in hidden)
    excluded = [{'repo': row['repo'], 'reason': 'put away in Office'} for row in rows if row['tower'] and row['repo'] in hidden]
    excluded += [{'repo': row['repo'], 'reason': 'no Tower risk rules or checkout' if row['enabled'] else 'disabled in registry'}
                 for row in rows if not row['tower']]
    return {'included': included, 'excluded': excluded, 'source': 'Tower registry rows with enabled && tower_row'}


def tbs_set(rows, org_repos, org_error, last_good=None):
    """The org listing is the denominator. When it fails, the set is the last-good listing plus every
    registered org repo, and the group is marked incomplete: a failed listing never shrinks the set."""
    registered = {row['repo'].lower(): row['repo'] for row in rows if TBS_ORG.lower() in row['repo'].lower()}
    listing, source, listing_error = org_repos, f'GitHub org listing {TBS_ORG}', ''
    if org_repos is None:
        listing, source, listing_error = listing_fallback(registered, org_error, last_good)
    active = sorted(name for name, archived in listing if not archived)
    in_org = {name.lower() for name, _ in listing}
    return {'included': active,
            'excluded': [{'repo': name, 'reason': 'archived'} for name, archived in listing if archived],
            'source': source + '; runs through the TBS coordinator, not Tower',
            'listing_error': listing_error,
            'registry_only': sorted(registered[key] for key in set(registered) - in_org),
            'org_only': [name for name in active if name.lower() not in registered]}


def listing_fallback(registered, org_error, last_good):
    listing = [tuple(pair) for pair in (last_good or {}).get('repos') or []]
    error = f'org listing failed: {org_error}'
    source = f"last-good org listing from {last_good['fetched_at']}" if listing else 'registry only'
    seen = {name.lower() for name, _ in listing}
    listing += [(name, False) for key, name in registered.items()
                if key.startswith(TBS_ORG.lower() + '/') and key not in seen]
    return listing, f'{source} ({error})', error


def org_listing(access):
    who, token = access.read_token_for(f'{TBS_ORG}/tbs')
    if not token:
        return None, 'no identity can read the TBS org'
    proc = subprocess.run(['gh', 'api', '--paginate', f'orgs/{TBS_ORG}/repos?per_page=100',
                           '--jq', '.[] | [.full_name, .archived] | @tsv'],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ, GH_TOKEN=token))
    if proc.returncode:
        return None, (proc.stderr.strip() or 'org listing failed')[:200]
    rows = [line.split('\t') for line in proc.stdout.splitlines() if '\t' in line]
    return [(name, flag == 'true') for name, flag in rows], ''


# ── fetching, with last-good ─────────────────────────────────────────────────

def cache_path():
    return private_state.ensure_dir(preferences.path().parent / 'github-inventory') / 'last-good.json'


def read_cache():
    try:
        return json.loads(cache_path().read_text())
    except (OSError, ValueError):
        return {}


def write_cache(cache):
    private_state.atomic_write_text(cache_path(), json.dumps(cache))


def issue_row(node):
    return {'number': node['number'], 'title': node.get('title') or '', 'url': node.get('url') or '',
            'created_at': node.get('createdAt') or '', 'updated_at': node.get('updatedAt') or '',
            'author': (node.get('author') or {}).get('login') or '',
            'labels': [row['name'] for row in (node.get('labels') or {}).get('nodes') or []],
            'comments': (node.get('comments') or {}).get('totalCount') or 0}


def _alias_errors(body, names):
    errors = {}
    for error in body.get('errors') or []:
        path = error.get('path') or []
        alias = str(path[0]) if path else ''
        message = str(error.get('message') or error.get('type') or 'GitHub error')[:200]
        if alias[1:].isdigit() and int(alias[1:]) < len(names):
            errors.setdefault(names[int(alias[1:])], message)
    return errors


def fetch_rest(repo, token, cursor):
    owner, name = repo.split('/', 1)
    rows = []
    seen = set()
    while cursor:
        if cursor in seen:
            raise RuntimeError('GitHub repeated an issue cursor')
        seen.add(cursor)
        body = graphql(PAGE_QUERY, {'o': owner, 'n': name, 'after': cursor}, token)
        connection = ((body.get('data') or {}).get('r0') or {}).get('issues')
        if not connection:
            raise RuntimeError(str((body.get('errors') or [{}])[0].get('message') or 'issue page missing')[:200])
        rows += [issue_row(node) for node in connection['nodes']]
        page = connection['pageInfo']
        cursor = page['endCursor'] if page['hasNextPage'] else None
    return rows


def fetch_batch(names, token):
    """{repo: {total, issues, archived}} and {repo: error}; a missing alias is an error, never zero."""
    variables = {}
    for i, repo in enumerate(names):
        variables[f'o{i}'], variables[f'n{i}'] = repo.split('/', 1)
    body = graphql(batch_query(len(names)), variables, token)
    errors, rows = _alias_errors(body, names), {}
    for i, repo in enumerate(names):
        node = (body.get('data') or {}).get(f'r{i}')
        if not isinstance(node, dict):
            errors.setdefault(repo, 'GitHub returned nothing for this repository')
            continue
        try:
            rows[repo] = complete(repo, node, token)
        except RuntimeError as exc:
            errors[repo] = str(exc)
    return rows, errors


def complete(repo, node, token):
    connection = node['issues']
    issues = [issue_row(item) for item in connection['nodes']]
    if connection['pageInfo']['hasNextPage']:
        issues += fetch_rest(repo, token, connection['pageInfo']['endCursor'])
    if len(issues) != connection['totalCount']:
        raise RuntimeError(f'fetched {len(issues)} of {connection["totalCount"]} open issues')
    return {'total': connection['totalCount'], 'issues': issues, 'archived': bool(node.get('isArchived'))}


def collect(access, repos):
    """Fresh rows for every repo that answered; errors for the rest (inaccessible included)."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = dict(zip(repos, pool.map(lambda repo: access.read_token_for(repo), repos)))
    by_token, fresh, errors = {}, {}, {}
    for repo in repos:
        who, token = identities[repo] or (None, None)
        if not token:
            errors[repo] = 'inaccessible: no configured GitHub identity can read this repository'
            continue
        by_token.setdefault(token, []).append(repo)
    for token, group in by_token.items():
        for start in range(0, len(group), BATCH):
            chunk = group[start:start + BATCH]
            try:
                rows, errs = fetch_batch(chunk, token)
            except RuntimeError as exc:
                rows, errs = {}, {repo: str(exc) for repo in chunk}
            fresh.update(rows)
            errors.update(errs)
    return fresh, errors


# ── the answer ───────────────────────────────────────────────────────────────

def age_days(stamp, now):
    try:
        return max(0, int((now - calendar.timegm(time.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ'))) // 86400))
    except (TypeError, ValueError):
        return None


def classify(labels, active):
    names = {label.lower() for label in labels}
    if active:
        return 'active'
    if names & NEEDS_YOU:
        return 'needs_you'
    if names & HELD:
        return 'held'
    if names & OWNED:
        return 'owned'
    if 'ready' in names:
        return 'ready'
    if names & {'in-pr', 'in pr'}:
        return 'in_pr'
    return 'untriaged' if not names else 'labeled'


def decorate(repo, issue, group, active, now):
    labels = issue['labels']
    lowered = {label.lower() for label in labels}
    return dict(issue, repo=repo, group=group, state=classify(labels, (repo.lower(), issue['number']) in active),
                priority=next((p for p in PRIORITY if p in lowered), ''),
                age_days=age_days(issue['created_at'], now), idle_days=age_days(issue['updated_at'], now))


def group_summary(name, spec, repos_state, active, now):
    rows, issues = [], []
    for repo in spec['included']:
        state = repos_state.get(repo, {'status': 'never fetched'})
        issues += [decorate(repo, issue, name, active, now) for issue in state.get('issues') or []]
        rows.append({'repo': repo, 'open': state.get('total'), 'status': state['status'],
                     'fetched_at': state.get('fetched_at'), 'error': state.get('error')})
    return dict(coverage(name, rows, spec.get('listing_error')), source=spec['source'], counts=tally(issues), oldest=oldest(issues),
                stale=sum(1 for item in issues if (item['idle_days'] or 0) >= STALE_DAYS), repos=rows,
                excluded=spec['excluded'], **{key: spec[key] for key in ('registry_only', 'org_only') if key in spec}), issues


def coverage(name, rows, listing_error=''):
    fresh = sum(1 for row in rows if row['status'] == 'fresh')
    known = [row['open'] for row in rows if row['open'] is not None]
    stamps = [row['fetched_at'] for row in rows if row['fetched_at']]
    return {'name': name, 'total': sum(known), 'repos_fresh': fresh, 'repos_counted': len(known),
            'repos_configured': len(rows), 'complete': fresh == len(rows) and not listing_error, 'oldest_fetch': min(stamps) if stamps else None,
            'denominator': denominator(name, sum(known), rows, listing_error)}


def denominator(name, total, rows, listing_error=''):
    fresh = sum(1 for row in rows if row['status'] == 'fresh')
    line = f'{total} open across {fresh}/{len(rows)} {name} repos'
    gaps = {}
    for row in rows:
        if row['status'] != 'fresh':
            kind = 'from last-good' if row['open'] is not None else row['status']
            gaps[kind] = gaps.get(kind, 0) + 1
    parts = [f'{count} {kind}' for kind, count in sorted(gaps.items())]
    if listing_error:
        parts.append('repo set unconfirmed, ' + listing_error)
    if parts:
        line += ' (partial: ' + ', '.join(parts) + ')'
    return line


def tally(issues):
    counts = {}
    for item in issues:
        for key in filter(None, (item['state'], item['priority'])):
            counts[key] = counts.get(key, 0) + 1
    return counts


def oldest(issues):
    item = max(issues, key=lambda row: row['age_days'] or 0, default=None)
    return item and {key: item[key] for key in ('repo', 'number', 'title', 'age_days')}


def active_issues():
    """Tower's own view per issue from #183's board: attempting now, or held. Seen is never "handled"."""
    try:
        import tower_board
        board = tower_board.read()
    except Exception:
        return set(), {}
    active, detail = set(), {}
    for item in board.get('issues') or []:
        key = (str(item.get('repo') or '').lower(), item.get('number'))
        if item.get('state') in ('working', 'queued', 'retrying'):
            active.add(key)
        detail[f'{key[0]}#{key[1]}'] = {k: item.get(k) for k in ('state', 'detail', 'attempt', 'age', 'obligation')}
    return active, detail


def build(access, hidden, fresh_required=False):
    rows, registry_error = registry_rows()
    org, org_error = org_listing(access)
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    cache = read_cache()
    if org is not None:
        cache[ORG_KEY] = {'repos': org, 'fetched_at': stamp}
    specs = {'TBS': tbs_set(rows, org, org_error, cache.get(ORG_KEY)), 'Matra': matra_set(),
             'Tower': tower_set(rows, hidden)}
    repos = sorted({repo for spec in specs.values() for repo in spec['included']})
    fresh, errors = collect(access, repos)
    for repo, data in fresh.items():
        cache[repo] = dict(data, fetched_at=stamp)
    write_cache(cache)
    return assemble(specs, cache, fresh, errors, registry_error, stamp)


def matra_set():
    return {'included': list(MATRA), 'excluded': [], 'source': MATRA_SOURCE}


def repo_state(repo, cache, fresh, errors):
    kept = cache.get(repo)
    if repo in fresh:
        return dict(kept, status='fresh')
    error = errors.get(repo) or 'not fetched'
    status = 'inaccessible' if 'inaccessible' in error or 'Could not resolve' in error else 'stale'
    if kept:
        return dict(kept, status=status + ' (last-good)', error=error)
    return {'status': status, 'error': error, 'total': None}


def assemble(specs, cache, fresh, errors, registry_error, stamp):
    now = time.time()
    active, attempts = active_issues()
    states = {repo: repo_state(repo, cache, fresh, errors)
              for spec in specs.values() for repo in spec['included']}
    groups, issues = [], {}
    for name, spec in specs.items():
        summary, items = group_summary(name, spec, states, active, now)
        groups.append(summary)
        for item in items:
            key = f"{item['repo'].lower()}#{item['number']}"
            if key in issues:
                issues[key]['groups'].append(name)
                continue
            issues[key] = dict(item, groups=[name], attempt=attempts.get(key))
    return {'generated_at': stamp, 'groups': groups, 'issues': list(issues.values()),
            'registry_error': registry_error, 'source': 'GitHub GraphQL open issues (PRs excluded), totalCount-checked'}


def read(access, hidden, fresh=False):
    """Serve last-good at once; refresh in the background when older than TTL, or wait when asked."""
    with LOCK:
        current, at = MEMORY['result'], MEMORY['at']
    if current is not None and not fresh and time.time() - at < TTL:
        return dict(current, refreshing=False)
    if current is None or fresh:
        return dict(refresh(access, hidden), refreshing=False)
    if REFRESHING.acquire(blocking=False):
        REFRESHING.release()
        threading.Thread(target=refresh, args=(access, hidden), daemon=True, name='office-issue-inventory').start()
    return dict(current, refreshing=True)


def refresh(access, hidden):
    with REFRESHING:
        result = build(access, hidden)
        with LOCK:
            MEMORY.update(result=result, at=time.time())
        return result


def repositories():
    """Every repo in the three denominators, for the write door's `known` set."""
    with LOCK:
        current = MEMORY['result']
    if not current:
        return set()
    return {row['repo'] for group in current['groups'] for row in group['repos']}
