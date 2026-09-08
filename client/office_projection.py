"""Search projections of existing Office sources; authorities stay unchanged."""
from contextlib import closing
import hashlib
import json
import time

from pathlib import Path
import office_jobs
import office_github_cache as github_cache
import office_system

WORLD=None


def record(kind,title,path,body,project='',modified=0):
    identifier='projection:'+hashlib.sha256((kind+':'+path).encode()).hexdigest()
    return {'id':identifier,'title':title,'path':path,'project':project,'kind':kind,
            'body':body if isinstance(body,str) else json.dumps(body,ensure_ascii=False,indent=2),
            'modified':modified,'coverage':'Office source snapshot'}


def records(errors):
    if WORLD is not None:
        snapshot=WORLD.snapshot or {}
        repositories=github_cache.known(WORLD)
        covered=github_cache.cached_repositories(repositories)
        yield from world_records(snapshot,covered)
        yield from github_cache.records(repositories,errors)
    try:
        yield from ledger_records()
        yield from flight_log_records(errors)
    except (OSError,ValueError,office_system.sqlite3.Error) as exc:
        errors.append({'source':'Nexus ledger','error':str(exc)[:200]})
    try:
        yield from job_log_records(errors)
    except (OSError,ValueError) as exc:
        errors.append({'source':'Job logs','error':str(exc)[:200]})


def world_records(snapshot,covered=()):
    for station in snapshot.get('stations',[]):
        repo=station['repo']
        if repo in covered:continue
        for kind in ('issues','prs'):
            for item in station.get(kind,[]):
                yield record('github',item.get('title','GitHub item'),f"{repo}#{item['number']}",item,repo)
    for name,section in snapshot.get('sections',{}).items():
        yield record('source',name,name,section,'Office sources')


def ledger_records():
    with closing(office_system.connect()) as db:
        for row in db.execute('SELECT id,title,objective,created_at FROM tasks ORDER BY created_at'):
            yield record('task',row['title'],row['id'],row['objective'] or '', 'Nexus',row['created_at'])
        for row in db.execute('SELECT id,kind,subject,payload,ts FROM events ORDER BY id'):
            yield record('event',row['kind'],str(row['id']),row['payload'],row['subject'] or 'Nexus',row['ts'])


def log_record(identifier,title,path,project,errors):
    try:
        if any(part.is_symlink() for part in (path,*path.parents)):
            raise PermissionError('Linked log is unavailable')
        if not path.is_file():return None
        return {'id':identifier,'title':title,'path':str(path),'project':project,'kind':'log',
                'body':'','modified':path.stat().st_mtime,'coverage':'Full retained owner log',
                'source_path':str(path)}
    except OSError as exc:
        errors.append({'source':project,'path':str(path),'error':str(exc)[:200]})
        return None


def job_log_records(errors):
    registry,faults=office_jobs.clock.read_registry(office_jobs.objects.vault()/office_jobs.clock.REGISTRY)
    errors.extend({'source':'Job registry','error':str(error)} for error in faults)
    for identifier in registry:
        if not valid_owner(identifier,office_jobs.validate_identifier,'Job logs',errors):continue
        path=office_jobs.objects.vault()/'_meta/logs/jobs'/f'{identifier}.log'
        row=log_record('job-log:'+identifier,identifier+' log',path,'System / jobs',errors)
        if row:yield row


def flight_log_records(errors):
    with closing(office_system.connect()) as db:
        identifiers=[row['id'] for row in db.execute('SELECT id FROM flights')]
    for identifier in identifiers:
        if not valid_owner(identifier,office_system.validate_flight,'Nexus logs',errors):continue
        base=office_system.run_board.FLIGHTS/identifier
        main=base/'log'
        if not main.exists():main=office_system.run_board.LEDGER.parent/'logs'/f'{identifier}.log'
        candidates=[('',main)]
        folder=base/'lanes'
        if not any(part.is_symlink() for part in (folder,*folder.parents)):
            candidates.extend((path.name,path) for path in sorted(folder.glob('*.out')))
        for lane,path in candidates:
            if not valid_owner(lane,office_system.validate_lane,'Nexus logs',errors):continue
            key='flight-log:'+json.dumps([identifier,lane],separators=(',',':'))
            row=log_record(key,identifier+(' / '+lane if lane else '')+' log',path,'System / Nexus',errors)
            if row:yield row


def valid_owner(identifier,validator,source,errors):
    try:
        validator(identifier)
        return True
    except ValueError as exc:
        errors.append({'source':source,'error':str(exc)})
        return False


def current(row):
    """Resolve an indexed projection against its owner at open time."""
    kind=row['kind'];identifier=row['path']
    if kind=='github':
        repo,number=identifier.rsplit('#',1)
        return dict(row,target={'kind':'github','repo':repo,'number':int(number)})
    if kind=='source':
        sections=(WORLD.snapshot if WORLD is not None else {}).get('sections',{})
        if identifier not in sections:raise FileNotFoundError('Office source is no longer available')
        value=sections[identifier]
    else:value=current_ledger(kind,identifier)
    body=json.dumps(value,ensure_ascii=False,indent=2)
    title=value.get('title') or row['title'] if isinstance(value,dict) else row['title']
    related=value.get('attempts',[]) if kind=='task' else []
    return dict(row,title=title,body=body,related_flights=related,coverage='Current owner record',observed_at=time.time(),revision=hashlib.sha256(body.encode()).hexdigest())


def current_ledger(kind,identifier):
    with closing(office_system.connect()) as db:
        if kind=='task':
            row=db.execute('SELECT * FROM tasks WHERE id=?',(identifier,)).fetchone()
            if row is None:raise FileNotFoundError('Task is no longer available')
            value=dict(row)
            value['attempts']=[dict(item) for item in db.execute('SELECT id,state,created_at FROM flights WHERE task_id=? ORDER BY created_at DESC',(identifier,))]
            return value
        if kind=='event':
            row=db.execute('SELECT * FROM events WHERE id=?',(identifier,)).fetchone()
            if row is None:raise FileNotFoundError('Event is no longer available')
            return dict(row,payload=json.loads(row['payload']))
    raise ValueError('This projection has no current owner reader')
