"""Read-only TBS Buzz view. A relay fetch is never a coordinator-read receipt."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import re
import subprocess
import threading
import time

ROOT = Path(os.environ.get('OFFICE_TBS_ROOT', str(Path(__file__).resolve().parents[2] / 'thinking-brain-school')))
CHANNELS = ('general', 'workroom', 'human-decisions', 'queue')
HEAD = re.compile(r'^  (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC author=([0-9a-f]{64}) id=([0-9a-f]{64}) thread=([0-9a-f]{64})(?: reply_to=([0-9a-f]{64}))?')
ISSUE = re.compile(r'https://github\.com/(Thinking-Brain-School/[A-Za-z0-9-]+)/issues/(\d+)')
_lock = threading.Lock()
_cache = None
_cache_at = 0
AUTHORS = {'3ad891d9487437f75799caef446ec3819e4c985fc2357beadf75a2e8e5d198e9': 'TBS agent',
           '54476ad3864ec1c36e9a63e3be1f582a8e521be90dbd0c11cd890499ff18d219': 'Caleb',
           'cccac9b62d5387f52f17ae7ff6e39980a7797e8524da8b36c91f13684159ae88': 'Aria',
           'd2755ff4e2290e7358fe6c06ff3f6992cae34a4b1aae848d5751c0c565fff5ee': 'Tim'}


def _receipt(folder, event_id):
    path = ROOT / '_meta/receipts' / folder / f'{event_id}.json'
    try:
        data = json.loads(path.read_text())
        return data if data.get('message_id') == event_id else None
    except (OSError, ValueError):
        return None


def _read(channel):
    result = subprocess.run([str(ROOT / 'tbs'), 'buzz', 'read', channel, '100'], cwd=ROOT,
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError(f'Buzz #{channel} fetch failed (exit {result.returncode})')
    rows = []
    channel_id = result.stdout.splitlines()[0].split('channel=', 1)[-1].strip()
    for line in result.stdout.splitlines():
        match = HEAD.match(line)
        if match:
            stamp, author, event_id, thread, reply = match.groups()
            rows.append({'id': event_id, 'channel': channel, 'author': AUTHORS.get(author, author), 'author_id': author,
                         'at': stamp.replace(' ', 'T') + 'Z', 'thread': thread,
                         'reply_to': reply, 'text': '', 'source': f'buzz://message?channel={channel_id}&id={event_id}&thread={thread}'})
        elif rows and line.startswith('    '):
            rows[-1]['text'] += ('\n' if rows[-1]['text'] else '') + line[4:]
    if not result.stdout.startswith(f'#{channel} channel='):
        raise RuntimeError(f'Buzz #{channel} returned an invalid snapshot')
    for row in rows:
        row['mirrored'] = any(row['id'] in p.read_text(errors='replace') for p in (ROOT / 'context/buzz').glob(f'*-{channel}.md') if p.stat().st_size < 500000)
        row['coordinator_read'] = bool(_receipt('buzz-reads', row['id']))
        row['acted'] = bool(_receipt('buzz-actions', row['id']))
        row['receipt'] = f'{ROOT}/_meta/receipts/buzz-actions/{row["id"]}.json' if row['acted'] else (f'{ROOT}/_meta/receipts/buzz-reads/{row["id"]}.json' if row['coordinator_read'] else '')
    return rows


def _issue_state(repo, number):
    result = subprocess.run(['gh', 'api', f'repos/{repo}/issues/{number}', '--jq', '.state'],
                            capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError(f'GitHub {repo}#{number} state unavailable')
    return result.stdout.strip()


def _annotate(messages, errors):
    states = {}
    for row in messages:
        match = ISSUE.search(row['text'])
        if match:
            repo, number = match.groups()
            key = f'{repo}#{number}'
            row['issue'] = key
            if key not in states and channel_ask(row) and (datetime.now(timezone.utc) - datetime.fromisoformat(row['at'].replace('Z', '+00:00'))).total_seconds() < 172800:
                try:
                    states[key] = _issue_state(repo, number)
                except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                    states[key] = 'unknown'
                    errors.append(str(error) if isinstance(error, RuntimeError) else f'GitHub {key} state unavailable')
            row['issue_state'] = states.get(key)
        row['needs_you'] = False  # an open queue work item is not evidence of human authority
    messages.sort(key=lambda row: row['at'], reverse=True)
    for row in messages:
        if direct_judgment_request(row) and not any(
                later['thread'] == row['thread'] and later['at'] > row['at'] and later['author'] == 'Aria'
                for later in messages):
            row['needs_you'] = True
            row['question'] = concise_question(row['text'])
    return messages


def direct_judgment_request(row):
    if row['author'] not in ('Tim', 'Caleb') or row['acted'] or row['channel'] == 'queue':
        return False
    text = row['text'].split('\n\n', 1)[0][:300]
    return (bool(re.search(r'(?i)\b(?:@aria|aria[, :])', text))
            and bool(re.search(r'(?i)\b(?:what do you think|can you check|could you (?:decide|choose|review)|do you approve|which (?:one|option)|please decide)\b', text)))


def concise_question(text):
    clean = re.sub(r'[*_`>#]+', '', text).replace('\n', ' ')
    match = re.search(r'[^.!?]{10,220}\?', clean)
    return match.group(0).strip() if match else clean[:180].strip()


def listing():
    global _cache, _cache_at
    with _lock:
        if _cache and time.monotonic() - _cache_at < 45:
            return _cache
        now = datetime.now(timezone.utc).isoformat()
        messages, errors = [], []
        with ThreadPoolExecutor(max_workers=4) as pool:
            reads = {channel: pool.submit(_read, channel) for channel in CHANNELS}
            for channel, task in reads.items():
                try:
                    messages.extend(task.result())
                except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as error:
                    errors.append(str(error) if isinstance(error, RuntimeError) else f'Buzz #{channel} unavailable ({type(error).__name__})')
        messages = _annotate(messages, errors)
        visible = messages[:160] + [row for row in messages[160:] if row['needs_you']]
        _cache = {'items': visible, 'errors': sorted(set(errors)), 'synced_at': now,
                  'latest_at': messages[0]['at'] if messages else None,
                  'source': 'TBS Buzz live reader (read only)'}
        _cache_at = time.monotonic()
        return _cache


def detail(event_id):
    """Raw provenance is opened explicitly from a Feed source or decision."""
    if not re.fullmatch(r'[0-9a-f]{64}', event_id):
        raise ValueError('Invalid Buzz event id')
    snapshot = listing()
    message = next((row for row in snapshot['items'] if row['id'] == event_id), None)
    if message is None:
        raise ValueError('Buzz event is outside the retained snapshot')
    return {'message': message,
            'thread': [row for row in snapshot['items'] if row['thread'] == message['thread']],
            'synced_at': snapshot['synced_at'], 'errors': snapshot['errors']}


def channel_ask(row):
    text = row['text']
    if row['channel'] == 'queue':
        return text.startswith('ARIA ·') and 'reply with the letter' in text and bool(ISSUE.search(text))
    return ('@Aria' in text or 'Aria,' in text) and ('?' in text or 'please ' in text.lower())
