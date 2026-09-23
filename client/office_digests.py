"""Read-only views of the existing generated daily email artifacts."""
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re

import office_objects as objects

NAME = re.compile(r'email-(morning-briefing|midday-pulse|research-digest|creative-spark|content-bank|evening-reflection)-\d{4}-\d{2}-\d{2}\.html')
LABELS = {'morning-briefing': 'Morning briefing', 'midday-pulse': 'Midday pulse',
          'research-digest': 'Research digest', 'creative-spark': 'Creative spark',
          'content-bank': 'Content bank', 'evening-reflection': 'Evening reflection'}


def folder():
    return objects.vault() / '_meta/logs'


def listing():
    rows = []
    for path in folder().glob('email-*.html'):
        if not NAME.fullmatch(path.name) or not path.is_file():
            continue
        match = NAME.fullmatch(path.name)
        kind = match.group(1)
        rows.append({'id': path.name, 'kind': kind, 'title': LABELS[kind],
                     'date': path.stem[-10:], 'updated_at': path.stat().st_mtime})
    rows.sort(key=lambda row: (row['date'], row['updated_at']), reverse=True)
    return {'items': rows[:36], 'total': len(rows)}


class Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.suppressed = 0
        self.href = None
        self.label = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'svg'):
            self.suppressed += 1
        if self.suppressed:
            return
        if tag in ('p', 'div', 'section', 'article', 'h1', 'h2', 'h3', 'li', 'br', 'tr'):
            self.parts.append('\n')
        if tag == 'a':
            href = dict(attrs).get('href', '')
            self.href = href if href.startswith(('https://', 'http://')) else None
            self.label = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'svg') and self.suppressed:
            self.suppressed -= 1
        if self.suppressed:
            return
        if tag == 'a' and self.href:
            label = ' '.join(''.join(self.label).split())[:140] or self.href
            self.links.append({'title': label, 'url': self.href})
            self.href = None
        if tag in ('p', 'div', 'section', 'article', 'h1', 'h2', 'h3', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.suppressed:
            self.parts.append(value)
            if self.href:
                self.label.append(value)


def detail(identifier):
    if not isinstance(identifier, str) or not NAME.fullmatch(identifier):
        raise ValueError('Unknown daily email')
    path = folder() / identifier
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 250_000:
        raise FileNotFoundError('Daily email is unavailable')
    raw = path.read_text(errors='replace')
    reader = Reader()
    reader.feed(raw)
    text = '\n'.join(' '.join(line.split()) for line in ''.join(reader.parts).splitlines() if line.strip())
    kind = NAME.fullmatch(identifier).group(1)
    return {'id': identifier, 'kind': kind, 'title': LABELS[kind], 'date': path.stem[-10:],
            'text': text[:60_000], 'links': list({row['url']: row for row in reader.links}.values())[:100],
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
