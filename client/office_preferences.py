"""Small revisioned UI preference file; no flight for a preference toggle."""
import json
import os
from pathlib import Path
import re
import threading

import private_state

DEFAULTS = {'theme': 'auto', 'background': '', 'accent': '#b9573c', 'font': 'classic',
            'reading': 'bookish', 'size': 100, 'density': 'comfortable', 'touch': False,
            'haptics': True, 'motion': False, 'remember': True, 'mini': True, 'speed': 1.0}
CHOICES = {'theme': ('auto', 'light', 'dark'), 'font': ('classic', 'system', 'literary'),
           'reading': ('bookish', 'clean', 'typewriter'), 'density': ('compact', 'comfortable', 'roomy')}
LOCK = threading.RLock()


def path():
    return Path(os.environ.get('OFFICE_STATE', str(Path.home() / '.local/state/nexus-office'))) / 'preferences.json'


def read():
    try:
        data = json.loads(path().read_text())
    except FileNotFoundError:
        data = {'revision': 0, 'preferences': DEFAULTS.copy()}
    return data


def valid(key, value):
    if key in CHOICES:
        return value in CHOICES[key]
    if key in ('background', 'accent'):
        return isinstance(value, str) and (value == '' or bool(re.fullmatch('#[0-9a-fA-F]{6}', value)))
    if key == 'size':
        return type(value) is int and 90 <= value <= 150
    if key == 'speed':
        return type(value) in (int, float) and .5 <= value <= 2
    return isinstance(DEFAULTS.get(key), bool) and isinstance(value, bool)


def save(body):
    with LOCK:
        old = read()
        if body.get('revision') != old['revision']:
            raise FileExistsError('Settings changed on another device. Refresh before saving.')
        update = DEFAULTS.copy() if body.get('reset') else body.get('preferences', {})
        if not isinstance(update, dict) or any(not valid(k, v) for k, v in update.items()):
            raise ValueError('Invalid preference value')
        result = {'revision': old['revision'] + 1, 'preferences': {**old['preferences'], **update}}
        private_state.ensure_dir(path().parent)
        private_state.atomic_write_text(path(), json.dumps(result))
        if not result['preferences']['remember']:
            from office_user_state import forget_activity
            forget_activity()
        return result
