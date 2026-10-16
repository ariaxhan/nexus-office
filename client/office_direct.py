"""One Office conversation owns a GitHub issue until Aria returns it."""
import hashlib
import json
import os
import re
from contextlib import closing
from pathlib import Path

from nexus.ledger import Ledger
from nexus.ledger import loads
from nexus import office_tasks as task_ledger
import office_github_actions as github
import office_profiles as profiles
import office_tasks as tasks
import run_board


def identity(body):
    issue = body.get('issue') or {}
    repo, number = issue.get('repo'), issue.get('number')
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Choose a registered GitHub issue')
    if type(number) is not int or number < 1:
        raise ValueError('Choose a valid issue number')
    return repo, number


def conversation(ledger, repo, number):
    rows = ledger.conn.execute("""SELECT t.id,t.state,e.payload FROM tasks t JOIN events e
        ON e.subject=t.id AND e.kind='office.task'
        WHERE json_extract(e.payload,'$.issue.repo')=? AND json_extract(e.payload,'$.issue.number')=?
        AND NOT EXISTS (SELECT 1 FROM events r WHERE r.subject=t.id AND r.kind='office.issue_released')
        ORDER BY t.created_at DESC LIMIT 1""", (repo, number)).fetchall()
    return {'task_id': rows[0]['id'], 'state': rows[0]['state']} if rows else None


def status(repo, number):
    identity({'issue': {'repo': repo, 'number': int(number)}})
    with closing(Ledger(str(run_board.LEDGER), read_only=True)) as ledger:
        return {'conversation': conversation(ledger, repo, int(number)),
                'intake': intake_status(ledger, repo, int(number))}


def intake_status(ledger, repo, number):
    """A bounded, read-only issue proof card from ledger events, including closed issues."""
    if repo.lower() == 'jessstrom/matra':
        return None  # this Office/Tower panel has no Matra ownership
    key = f'github:{repo.lower()}#{number}'
    subject = f'{repo.lower()}#{number}'

    def event(row):
        if not row:
            return None
        payload = loads(row['payload'], {})
        return {**(payload if isinstance(payload, dict) else {}), 'at': row['ts']}

    def latest(kind, ref):
        row = ledger.conn.execute('SELECT ts,payload FROM events WHERE kind=? AND subject=?'
                                  ' ORDER BY id DESC LIMIT 1', (kind, ref)).fetchone()
        return event(row)

    claim = latest('intake.claim', key)
    state = latest('intake.state', key)
    receipt = latest('receipt.state', subject)
    poll = latest('receipt.poll', key)
    rejected = ledger.conn.execute("SELECT ts,payload FROM events WHERE kind='receipt.state' AND subject=?"
                                   " AND json_extract(payload,'$.state')='CLAIMED' ORDER BY id DESC LIMIT 1",
                                   (subject,)).fetchone()
    rejection = event(rejected)
    attention_row = ledger.conn.execute("SELECT ts,payload FROM events WHERE subject=? AND kind IN"
                                        " ('receipt.verification_due','receipt.abandoned')"
                                        " ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    attention = event(attention_row)
    if claim:
        state = state if state and state['at'] >= claim['at'] else None
        receipt = receipt if receipt and receipt['at'] >= claim['at'] else None
        poll = poll if poll and poll['at'] >= claim['at'] else None
        rejection = rejection if rejection and rejection['at'] >= claim['at'] else None
        attention = attention if attention and attention.get('claimed_at') == claim['at'] else None
    if not any((claim, state, receipt, poll, rejection, attention)):
        return None
    proof = receipt.get('proof') if receipt and receipt.get('state') == 'RECEIPT' else None
    return {'owner': (state or {}).get('task'), 'state': (state or {}).get('state') or
            ('CLAIMED' if claim else 'UNKNOWN'), 'since': (state or {}).get('at') or
            (claim or {}).get('at'), 'last_poll_at': (poll or {}).get('at'),
            'proof': {'comment': receipt.get('comment'), 'sha': proof.get('sha'),
                      'branch': proof.get('branch')} if proof else None,
            'rejection': {'at': rejection['at'], 'why': rejection.get('why'),
                          'comment': rejection.get('comment')} if rejection else None,
            'attention': {'at': attention['at'], 'act_needed': attention.get('act_needed')}
            if attention else None}


def _issue(repo, number, token):
    result = github.request(f'repos/{repo}/issues/{number}', 'GET', {}, token)
    if result.get('pull_request') or result.get('number') != number:
        raise ValueError('Choose a GitHub issue, not a pull request')
    return result


def _coordinator_idle(repo, project):
    if repo == 'jessstrom/matra':
        lock = Path(project['path']) / '.coordinator-out/coordinator.lock/pid'
    elif repo.startswith('Thinking-Brain-School/') or repo == 'ariaxhan/thinking-brain-school':
        lock = Path.home() / 'Developer/Vaults/CodingVault/thinking-brain-school/.tbs-out/coordinator.lock/pid'
    else:
        return
    try:
        pid = int(lock.read_text().strip())
        os.kill(pid, 0)
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return
    raise FileExistsError('The coordinator is running. Start this conversation after its current run finishes.')


def _reserve(repo, number, token, world, known, key, attempted):
    issue = _issue(repo, number, token)
    if issue.get('state') != 'open':
        raise FileExistsError('This issue is already closed')
    if not attempted and any(label.get('name', '').lower() == 'hold' for label in issue.get('labels', [])):
        raise FileExistsError('This issue is already held; inspect its current owner')
    try:
        github.request(f'repos/{repo}/labels/hold', 'GET', {}, token)
    except RuntimeError as exc:
        if '404' not in str(exc):
            raise
        try:
            github.request(f'repos/{repo}/labels', 'POST',
                           {'name': 'hold', 'color': 'BFDADC',
                            'description': 'Reserved from automation for a direct Office conversation'}, token)
        except RuntimeError:
            github.request(f'repos/{repo}/labels/hold', 'GET', {}, token)
    claim_id = hashlib.sha256(('office-direct:'+key).encode()).hexdigest()[:32]
    receipt = github.command(world, known, {'action': 'label_add', 'repo': repo,
                                           'number': number, 'labels': ['hold'],
                                           'request_id': claim_id}, None)
    if receipt['state'] == 'unconfirmed':
        receipt = github.reconcile(world, known, {'request_id': claim_id})
    if receipt['state'] != 'confirmed':
        raise RuntimeError(receipt.get('error') or 'GitHub did not confirm the issue hold')
    if not any(label.get('name', '').lower() == 'hold' for label in _issue(repo, number, token).get('labels', [])):
        raise RuntimeError('GitHub did not confirm the issue hold')


def start(body, world):
    key = tasks.request_id(body)
    repo, number = identity(body)
    message = tasks.prompt(body)
    normalized = {**body, 'issue': {'repo': repo, 'number': number}, 'interactive_issue': True}
    project = next((row for row in tasks.projects() if row['id'] == body.get('project') and row['name'] == repo), None)
    if project is None:
        raise ValueError('This issue needs its registered Git checkout in Office')
    profiles.require(body.get('engine'), body.get('profile'))
    tasks.source_revision(project, body.get('source_ref', 'HEAD'))
    claim_id = hashlib.sha256(('office-direct:'+key).encode()).hexdigest()[:32]
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        prior = ledger.conn.execute("SELECT payload FROM events WHERE kind='office.request' AND subject=? ORDER BY id LIMIT 1", (key,)).fetchone()
        if prior:
            return tasks.start(normalized)
        existing = conversation(ledger, repo, number)
        if existing:
            if existing['state'] in ('done', 'closed', 'cancelled', 'failed'):
                raise FileExistsError('The earlier conversation ended. Return the issue to its coordinator before starting another.')
            tasks.say({'task_id': existing['task_id'], 'request_id': key, 'text': message})
            return {'task_id': existing['task_id'], 'request_id': key, 'state': 'continued'}
        lease = ledger.conn.execute('SELECT holder_flight FROM leases WHERE lower(resource)=?',
                                    (f'github:{repo.lower()}#{number}',)).fetchone()
        if lease:
            raise FileExistsError(f'Issue already owned by flight {lease[0]}; reconcile it before taking over')
        attempted = ledger.conn.execute("SELECT 1 FROM events WHERE kind='office.github_request' AND subject=? LIMIT 1", (claim_id,)).fetchone() is not None
    if not attempted:
        _coordinator_idle(repo, project)
    known = {row['name'] for row in tasks.projects()}
    _, token = github.fresh_access(world.access(), repo, known)
    _reserve(repo, number, token, world, known, key, attempted)
    return tasks.start(normalized)


def release(body, world):
    task_id = body.get('task_id')
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        spec = task_ledger.specification(ledger, task_id)
        if not spec.get('interactive_issue') or not spec.get('issue'):
            raise ValueError('This is not a direct issue conversation')
        active = ledger.conn.execute("SELECT 1 FROM flights WHERE task_id=? AND state IN ('running','queued','verifying','landing','resolving') LIMIT 1", (task_id,)).fetchone()
        if active:
            raise FileExistsError('Close the agent session before returning the issue to its coordinator')
        repo, number = identity(spec)
        prior = ledger.conn.execute("SELECT 1 FROM events WHERE subject=? AND kind='office.issue_released' LIMIT 1", (task_id,)).fetchone()
        if prior:
            return {'released': True, 'repo': repo, 'number': number, 'already_released': True}
    known = {row['name'] for row in tasks.projects()}
    _, token = github.fresh_access(world.access(), repo, known)
    issue = _issue(repo, number, token)
    if any(label.get('name', '').lower() == 'hold' for label in issue.get('labels', [])):
        github.request(f'repos/{repo}/issues/{number}/labels/hold', 'DELETE', {}, token)
        if any(label.get('name', '').lower() == 'hold' for label in _issue(repo, number, token).get('labels', [])):
            raise RuntimeError('GitHub did not confirm release of the hold')
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        ledger.event('office.issue_released', task_id,
                     {'repo': repo, 'number': number, 'issue_state': issue.get('state')}, source='office')
    return {'released': True, 'repo': repo, 'number': number, 'issue_state': issue.get('state')}
