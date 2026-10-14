"""Aria's comment on an issue becomes a delivered directive to its owner.

Everything around this module already existed (the full map is in
~/workspace/nexus-office/issue-loop/REPORT.md): the phone could already post a
GitHub comment through the receipt-backed write door
(office_github_actions.command), and every coordinator already had an inbox
file whose unread rows wake it and outrank everything else. What was missing
is only what is in this file:

  * at Send time, the alert is written into the owning coordinator's own
    inbox, in its established row shape, and recorded in the Nexus Ledger;
  * the three existing signals (GitHub receipt, inbox presence, the owner's
    own read row) are read back as one delivery state:
    sent -> delivered -> picked up.

No new queue, store, or watcher. The Nexus Ledger is the record; the
coordinators' inboxes are the delivery; GitHub is the thread.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import datetime as dt

# Same path fix office_tasks.py applies: the nexus package lives at the repo
# root, this module in client/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.ledger import Ledger, loads
import office_github_actions as github_actions
import office_tasks as tasks
import run_board

REPO_RE = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
TBS_ORG = 'thinking-brain-school'
MATRA_REPOS = {'jessstrom/matra', 'jessstrom/matra-care'}
ALERT_KIND = 'office.issue_alert'
PICKUP_KIND = 'office.issue_alert_pickup'


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def owner_for(repo):
    """repo -> owning coordinator id, over the estate's existing sets.

    TBS owns its GitHub org (the inventory's TBS denominator) plus Aria's
    mirror checkout repo; Matra owns its canonical repos (inventory's MATRA
    tuple) and the blinkbuild org its suite lives under. Everything else —
    ariaxhan/nexus-office included, and any repo nobody registered — routes
    to Nexus, which is the substrate owner and the router of last resort.
    """
    repo = (repo or '').strip()
    owner, _, _name = repo.partition('/')
    if owner.lower() == TBS_ORG or repo.lower() == 'ariaxhan/thinking-brain-school':
        return 'tbs'
    if repo.lower() in MATRA_REPOS or owner.lower() == 'blinkbuild':
        return 'matra'
    return 'nexus'


def _coordinator_roots():
    """id -> checkout root (Path), from the door's own coordinators.json."""
    here = Path(__file__).resolve()
    coding_vault = here.parents[2]
    try:
        rows = json.loads((here.parent / 'coordinators.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        rows = []
    return {row['id']: coding_vault / row['root'] for row in rows if row.get('id') and row.get('root')}


def inbox_path(owner):
    override = os.environ.get(f'OFFICE_ISSUE_LOOP_INBOX_{owner.upper()}')
    if override:
        return Path(override)
    root = _coordinator_roots().get(owner)
    if root is None:
        return None
    return root / '_meta/state/coordinator-inbox.jsonl'


def _inbox_rows(path):
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if len(raw) > 8 * 1024 * 1024:
        raw = raw[-8 * 1024 * 1024:]
    out = []
    for line in raw.decode('utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def append_inbox(owner, row):
    """Append the alert row in the coordinator inbox's established shape.

    Idempotent by row id: a retried Send never delivers the same alert
    twice. Returns True when the row is present afterwards.
    """
    path = inbox_path(owner)
    if path is None:
        return False
    if any(r.get('id') == row['id'] for r in _inbox_rows(path)):
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return True


def inbox_state(owner, alert_id):
    """(present, picked_up) for one alert in a coordinator's inbox.

    Pickup is the coordinator's own convention, observed not invented: a
    later row whose 'read' names this id (tbs-coordinator-changed.py and
    Matra's coord.py both compute unread exactly this way).
    """
    path = inbox_path(owner)
    if path is None:
        return False, False
    rows = _inbox_rows(path)
    present = any(r.get('id') == alert_id for r in rows)
    picked = any(r.get('read') == alert_id for r in rows)
    return present, picked


def _identity(body):
    issue = body.get('issue') or {}
    repo, number = issue.get('repo'), issue.get('number')
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise ValueError('Choose a registered GitHub issue')
    if type(number) is not int or number < 1:
        raise ValueError('Choose a valid issue number')
    return repo, number


def send(world, known, body, sync=None):
    """One Send: GitHub comment + Ledger record + owner inbox alert.

    Each step is idempotent under the caller's request_id, so a retry after
    a partial failure converges instead of duplicating: the GitHub door
    dedupes by request_id, the Ledger record and the inbox row by alert id.
    """
    key = tasks.request_id(body)
    repo, number = _identity(body)
    text = tasks.prompt(body, 'text')
    owner = owner_for(repo)
    alert_id = 'aria-comment-' + hashlib.sha256(key.encode()).hexdigest()[:12]

    receipt = github_actions.command(world, known, {
        'action': 'comment', 'repo': repo, 'number': number,
        'body': text, 'request_id': key}, sync)
    comment = receipt.get('result') or {}
    comment_url = comment.get('html_url', '') if isinstance(comment, dict) else ''

    payload = {'repo': repo, 'number': number, 'owner': owner,
               'request_id': key, 'comment_state': receipt['state'],
               'comment_url': comment_url, 'at': now()}
    if receipt['state'] == 'rejected':
        payload['comment_error'] = receipt.get('error', '')
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        prior = ledger.conn.execute(
            'SELECT 1 FROM events WHERE kind=? AND subject=? LIMIT 1',
            (ALERT_KIND, alert_id)).fetchone()
        if not prior:
            ledger.event(ALERT_KIND, alert_id, payload, source='office')

    note = '' if receipt['state'] != 'rejected' else (
        f"\n\nNote: the GitHub comment itself was rejected ({receipt.get('error', '')}); "
        'her words are delivered here in full instead.')
    row = {'id': alert_id, 'at': now(), 'from': 'aria', 'via': 'office-issue-loop',
           'repo': repo, 'number': number, 'comment_url': comment_url,
           'text': (f'Aria commented on {repo}#{number} (via Nexus Office):\n\n{text}\n\n'
                    'Treat this as a directive: do the work, or reply on the issue '
                    'thread with the outcome or the blocker.\n'
                    f'Issue: https://github.com/{repo}/issues/{number}'
                    + (f'\nComment: {comment_url}' if comment_url else '') + note)}
    if owner == 'nexus':
        delivered = True  # the Ledger event above is Nexus's own intake record
    else:
        delivered = append_inbox(owner, row)

    state = 'alert_only' if receipt['state'] == 'rejected' else 'sent'
    return {'state': state, 'owner': owner, 'alert_id': alert_id,
            'comment_state': receipt['state'], 'comment_url': comment_url,
            'delivered': delivered,
            **({'error': receipt.get('error', '')} if state == 'alert_only' else {})}


def alerts(repo, number):
    """Delivery state for every alert on one issue, oldest first."""
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo or ''):
        raise ValueError('Choose a registered GitHub issue')
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise ValueError('Choose a valid issue number') from None
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        rows = ledger.conn.execute(
            'SELECT subject,payload FROM events WHERE kind=? '
            "AND json_extract(payload,'$.repo')=? "
            "AND json_extract(payload,'$.number')=? ORDER BY id",
            (ALERT_KIND, repo, number)).fetchall()
        pickups = {r['subject'] for r in ledger.conn.execute(
            'SELECT subject FROM events WHERE kind=?', (PICKUP_KIND,)).fetchall()}
    out = []
    for row in rows:
        alert = loads(row['payload'], {})
        alert_id = row['subject']
        owner = alert.get('owner', 'nexus')
        if alert_id in pickups:
            state = 'picked_up'
        elif owner != 'nexus':
            present, picked = inbox_state(owner, alert_id)
            state = 'picked_up' if picked else ('delivered' if present else 'sent')
        else:
            state = 'delivered'  # Nexus's intake is the Ledger itself
        out.append({'id': alert_id, 'at': alert.get('at', ''), 'owner': owner,
                    'state': state, 'comment_state': alert.get('comment_state', ''),
                    'comment_url': alert.get('comment_url', '')})
    return {'alerts': out}


def mark_pickup(alert_id, by='nexus'):
    """Record that the Nexus side picked an alert up.

    Nexus has no inbox file — its intake is the Ledger — so this event is
    its pickup marker, the counterpart of the coordinators' read rows.
    Domain coordinators never call this; their own read rows are observed
    by alerts() directly.
    """
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        prior = ledger.conn.execute(
            'SELECT 1 FROM events WHERE kind=? AND subject=? LIMIT 1',
            (PICKUP_KIND, alert_id)).fetchone()
        if prior:
            return {'picked_up': True, 'alert_id': alert_id, 'already': True}
        ledger.event(PICKUP_KIND, alert_id, {'by': by, 'at': now()}, source='office')
    return {'picked_up': True, 'alert_id': alert_id}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'pickup':
        print(json.dumps(mark_pickup(sys.argv[2])))
    else:
        raise SystemExit('usage: issue_loop.py pickup <alert_id>')
