"""Explicit account seats. A missing client login never borrows Personal."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import office_objects as objects

LOCK=threading.Lock()
CACHE=(0,[])


def seats():
    home=Path.home()
    tbs=objects.vault()/'CodingVault/thinking-brain-school'
    return {('codex','personal'):home/'.codex-cli', ('codex','tbs'):tbs/'.codex-tbs-account',
            ('claude','personal'):None, ('claude','tbs'):tbs/'.claude-tbs-account'}


def environment(engine,profile):
    choices=seats()
    if (engine,profile) not in choices:
        raise ValueError('Choose Claude Code or Codex, with Personal or TBS')
    seat=choices[(engine,profile)]
    if seat is not None and not seat.is_dir():
        raise FileNotFoundError(f'{profile} {engine} account is not configured')
    # Credentials come only from the selected login store; parent-task IDs and
    # API credentials may not cross either account or process boundaries.
    prefixes=('CODEX_','CLAUDE_','ANTHROPIC_','OPENAI_','HCOM_')
    env={key:value for key,value in os.environ.items() if not key.startswith(prefixes)}
    for key in ('CLAUDECODE','CLAUDE_CONFIG_DIR','CODEX_HOME'):
        env.pop(key,None)
    if engine=='codex':
        env['CODEX_HOME']=str(seat)
    elif seat is not None:
        env['CLAUDE_CONFIG_DIR']=str(seat)
    # Personal Claude requires an UNSET variable: ~/.claude selects a different
    # keychain identity (account-router.sh documents this behavior).
    return env


def probe(choice):
    engine,profile=choice
    row={'engine':engine,'id':profile,'ready':False,'detail':''}
    binary=shutil.which(engine)
    if not binary:
        return dict(row,detail=f'{engine} is not installed')
    try:
        env=environment(engine,profile)
        args=[binary,'auth','status'] if engine=='claude' else [binary,'login','status']
        result=subprocess.run(args,env=env,cwd='/tmp',stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
        return auth_result(row,result)
    except (OSError,subprocess.TimeoutExpired) as exc:
        return dict(row,detail=f'Account readiness failed: {type(exc).__name__}')


def auth_result(row,result):
    if result.returncode:
        return dict(row,detail='Sign in to this account on the Mac; no other account will be used.')
    if row['engine']=='claude':
        try:
            identity=json.loads(result.stdout)
        except ValueError:
            return dict(row,detail='Claude returned an unreadable authentication result.')
        if not identity.get('loggedIn'):
            return dict(row,detail='This Claude account is logged out.')
        label=identity.get('email') or identity.get('authMethod') or 'Authenticated'
    else:
        label='Authenticated Codex login'
    return dict(row,ready=True,detail=f"{row['id'].title()} · {label}")


def readiness(fresh=False):
    global CACHE
    with LOCK:
        stamp,rows=CACHE
        if not fresh and time.time()-stamp<30:
            return rows
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(probe,seats()))
        CACHE=(time.time(),rows)
        return rows


def require(engine,profile):
    row=next((row for row in readiness(fresh=True) if row['engine']==engine and row['id']==profile),None)
    if not row or not row['ready']:
        raise PermissionError(row['detail'] if row else 'Unknown account selection')
    return row
