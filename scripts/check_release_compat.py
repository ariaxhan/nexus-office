#!/usr/bin/env python3
"""Stable service-wrapper preflight for durable human asks across rollbacks."""
import ast
from contextlib import closing
from pathlib import Path
import sqlite3
import sys


def supported_version(release):
    client = Path(release) / 'client'
    source = client / 'human_asks.py'
    api = client / 'office_api.py'
    ui = client / 'phone' / 'office.js'
    if not all(path.is_file() for path in (source, api, ui)):
        return 0
    if '/api/human-asks' not in api.read_text() or "api('/api/human-asks')" not in ui.read_text():
        return 0
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                target.id == 'HUMAN_ASKS_SCHEMA_VERSION' for target in node.targets):
            return int(ast.literal_eval(node.value))
    # a13753e introduced the complete v1 reader before it declared a version.
    return 1


def check(release, store):
    store = Path(store)
    if not store.exists():
        return
    # Read the live WAL too; this macOS system SQLite cannot open this WAL store
    # through a read-only URI while the service owns it.
    with closing(sqlite3.connect(store, timeout=5)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        columns = {row[1] for row in db.execute('PRAGMA table_info(asks)')}
        required = {'id', 'owner', 'state', 'source_ref'}
        if not required <= columns:
            raise RuntimeError('human ask store schema cannot be inspected safely')
        open_count = db.execute("SELECT count(*) FROM asks WHERE state='open' AND owner='aria'").fetchone()[0]
    supported = supported_version(release)
    if version > supported or (open_count and supported == 0):
        raise RuntimeError(f'release supports human asks schema {supported}; store is schema {version} with {open_count} open Aria asks')


if __name__ == '__main__':
    try:
        check(sys.argv[1], sys.argv[2])
    except (IndexError, OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        print(f'Office release refused: {exc}', file=sys.stderr)
        raise SystemExit(78)
