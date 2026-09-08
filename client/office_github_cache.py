"""Atomic per-repository snapshots; failed collection never replaces good data."""
import hashlib
from collections import deque
import json
import os
from pathlib import Path
import threading
import time

import office_preferences as preferences
import private_state
import office_workspaces

LOCK=threading.Lock()
STATUS={}
HEADER_BYTES=1024
TTL=3600
RETRY=300


def root():
    return private_state.ensure_dir(preferences.path().parent/'github-corpus')


def path(repo):
    value=root()/(hashlib.sha256(repo.encode()).hexdigest()+'.jsonl')
    if any(part.is_symlink() for part in (value,*value.parents)):raise PermissionError('Linked GitHub cache is unavailable')
    return value


def header(repo):
    target=path(repo)
    if not target.exists():return None
    with target.open() as stream:return read_header(stream,repo)


def read_header(stream,repo):
    info=json.loads(stream.readline())
    if info.get('repo')!=repo or not isinstance(info.get('generation'),str) or not isinstance(info.get('count'),int):
        raise ValueError('Invalid GitHub cache header')
    return info


def collect(access,known,repo):
    import office_github_stage
    result=office_github_stage.advance(access,known,repo,path(repo))
    STATUS[repo]=dict(result,started_at=STATUS.get(repo,{}).get('started_at',time.time()))
    return result


def known(world):
    if world is None:return []
    repositories={row['repo'] for row in (world.snapshot or {}).get('stations',[])}
    base=os.environ.get('OFFICE_RUNTIME_ROOT')
    if base:
        repositories.update(name for name,_ in office_workspaces.discover(Path(base)) if not name.startswith('Local / '))
    return sorted(repositories)


def refresh(world):
    if world is None or not LOCK.acquire(blocking=False):return
    threading.Thread(target=refresh_all,args=(world,),daemon=True,name='office-github-corpus').start()


def refresh_all(world):
    try:
        repositories=known(world)
        try:access=world.access()
        except Exception as exc:
            for repo in repositories:failed(repo,exc)
            return
        ordered=sorted(repositories,key=lambda repo:((safe_header(repo) or {}).get('finished_at',0),repo.casefold()))
        pending=deque(repo for repo in ordered if due(repo))
        while pending:
            repo=pending.popleft()
            try:
                if collect(access,repositories,repo)['state']=='fetching':pending.append(repo)
            except Exception as exc:failed(repo,exc)

    finally:LOCK.release()


def cached_repositories(repositories):
    return {repo for repo in repositories if path(repo).exists()}


def records(repositories,errors):
    for repo in repositories:
        try:
            target=path(repo)
            if not target.exists():continue
            with target.open() as stream:
                info=read_header(stream,repo);ids=set()
                for line in stream:
                    row=json.loads(line);ids.add(row['id'])
                    row['coverage']='GitHub corpus:'+info['generation'];yield row
                if len(ids)!=info['count']:raise ValueError('GitHub snapshot record count mismatch')
        except (OSError,ValueError,KeyError) as exc:
            errors.append({'source':repo,'error':str(exc)[:200]})
            raise RuntimeError('GitHub snapshot unreadable; retaining previous search index') from exc


def coverage(repositories,indexed):
    rows=[]
    for repo in repositories:
        info=safe_header(repo) or {};status=STATUS.get(repo,{})
        count=sum(row['indexed'] for row in indexed if row['source']==repo and row['coverage']=='GitHub corpus:'+info.get('generation',''))
        stale=any(row['source']==repo and row['coverage'].startswith('GitHub corpus:') and row['coverage']!='GitHub corpus:'+info.get('generation','') for row in indexed)
        state='indexed' if info and count==info['count'] and not stale else 'indexing' if info else 'unbuilt'
        if status.get('state') in ('fetching','error'):state=status['state']
        rows.append({'repo':repo,'state':state,'indexed':count,'fetched':status.get('records',info.get('count',0)),'observed_at':info.get('finished_at'),'error':status.get('error')})
    return rows


def failed(repo,exc):
    STATUS[repo]={**STATUS.get(repo,{}),'state':'error','error':str(exc)[:250],'failed_at':time.time()}


def safe_header(repo):
    try:return header(repo)
    except (OSError,ValueError,AttributeError) as exc:
        prior=STATUS.get(repo,{})
        if prior.get('state')!='fetching':STATUS[repo]={**prior,'state':'error','error':str(exc)[:250]}
        return None


def due(repo):
    current=safe_header(repo) or {};last=STATUS.get(repo,{})
    return time.time()-current.get('finished_at',0)>=TTL and time.time()-last.get('failed_at',0)>=RETRY
