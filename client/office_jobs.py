"""Registry-qualified jobctl history and logs. Never accept arbitrary log paths."""
import json
import codecs
from pathlib import Path
import os
import re

import office_objects as objects
from sources import clock


def validate_identifier(identifier):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',identifier):raise ValueError('Invalid registered job ID')


def job(identifier):
    validate_identifier(identifier)
    rows,errors=clock.read_registry(objects.vault()/clock.REGISTRY)
    if errors:raise ValueError('Job registry has errors: '+'; '.join(errors))
    if identifier not in rows:raise FileNotFoundError('Job is not registered')
    return rows[identifier]


def logs(identifier,offset=0,version=''):
    job(identifier)
    return read_log(objects.vault()/'_meta/logs/jobs'/f'{identifier}.log',offset,version)


def read_log(path,offset=0,version=''):
    if any(p.is_symlink() for p in (path,*path.parents)):raise PermissionError('Linked logs cannot be opened')
    if not path.exists():return {'text':'','state':'not-created','next_offset':None,'offset':0}
    info=path.stat();identity=f'{info.st_dev}:{info.st_ino}'
    start=max(0,int(offset))
    if (version and version!=identity) or start>info.st_size:
        return {'text':'','state':'rotated','next_offset':0,'offset':0,'version':identity,'bytes':info.st_size}
    with path.open('rb') as stream:
        stream.seek(start);raw=stream.read(65536)
    decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
    text=decoder.decode(raw,final=start+len(raw)>=info.st_size)
    end=start+len(raw)-len(decoder.getstate()[0])
    return {'text':text,'state':'ok','offset':start,
            'next_offset':end if end<info.st_size else None,'version':identity,'bytes':info.st_size}


def receipts(identifier,offset=0):
    job(identifier)
    root=Path(os.environ.get('JOBCTL_RUNTIME_DIR',str(Path.home()/'Library/Application Support/nexus-jobs')))
    path=root/'state/receipts.jsonl'
    if not path.exists():return {'items':[],'next_offset':None,'state':'no-receipts'}
    if any(p.is_symlink() for p in (path,*path.parents)):raise PermissionError('Linked receipts cannot be read')
    rows=[];errors=[];start=max(0,int(offset))
    with path.open('rb') as stream:
        stream.seek(start)
        while stream.tell()-start<1024*1024 and len(rows)<40:
            line=stream.readline()
            if not line:break
            try:
                row=json.loads(line)
                if row.get('job')==identifier or row.get('job_id')==identifier:rows.append(row)
            except ValueError:errors.append({'offset':stream.tell()-len(line),'error':'Malformed receipt'})
        end=stream.tell()
    return {'items':rows,'errors':errors,'next_offset':end if end<path.stat().st_size else None,'state':'ok'}
