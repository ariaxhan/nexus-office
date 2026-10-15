"""The TBS coordinator's published receipts (`_meta/state/office-doc.md`): one claim surface.

Read by the Watch classifier (client/office_work.py) and by the intake state machine
(nexus/intake.py), so a coordinator claim means the same thing on both.
"""

import datetime as dt
import re
from pathlib import Path

DOC = Path("_meta") / "state" / "office-doc.md"
_HEADING = re.compile(r'^### \[[^\]]*\]\(https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)\)')
_COORD = re.compile(r'^- \*\*Coordinator:\*\* (\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC\.')


def _stamp_epoch(month, day, hour, minute, now):
    try:
        stamp = dt.datetime(now.year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    if stamp > now + dt.timedelta(hours=1):
        stamp = stamp.replace(year=now.year - 1)
    return stamp.timestamp()


def doc_claims(text, now=None):
    """{(repo, number): {'activity': epoch, 'running': bool, 'guidance': bool}}.

    The TBS coordinator's published receipts: one Coordinator line per item,
    stamped MM-DD HH:MM UTC. Mirrors bin/office_doc.py's own reading of them.
    'guidance' marks items Aria herself has written on — her tracked items.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    claims, current, in_note = {}, None, False
    for line in text.split('\n'):
        heading = _HEADING.match(line)
        if heading:
            current = (heading.group(1), int(heading.group(2)))
            claims.setdefault(current, {'activity': None, 'running': False, 'guidance': False})
            in_note = False
            continue
        if line.startswith('#'):
            current, in_note = None, False
            continue
        if current is None:
            continue
        if line.strip() == '**Aria:**':
            in_note = True
            continue
        if in_note and line.strip():
            claims[current]['guidance'] = True
        coord = _COORD.match(line)
        if coord:
            epoch = _stamp_epoch(*(int(g) for g in coord.groups()), now)
            if epoch is not None:
                claims[current].update({'activity': epoch, 'running': 'Worker running' in line})
    return claims


def published(repo_path, now=None):
    """The claims a checkout's coordinator doc publishes; {} when it publishes none."""
    try:
        return doc_claims((Path(repo_path) / DOC).read_text(), now=now)
    except (OSError, TypeError, ValueError):
        return {}
