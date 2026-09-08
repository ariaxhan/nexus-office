"""Read the harness-owned retained bot JSONL, including archived sessions."""
import hashlib
import json
from pathlib import Path
import re

import office_objects as objects
from office_archives import reverse_records


def root():
    return objects.vault()/'_meta/logs/bots'


def resolve(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.jsonl',name):raise ValueError('Invalid bot history identity')
    path=root()/name
    if any(part.is_symlink() for part in (path,*path.parents)):raise PermissionError('Linked bot history is unavailable')
    if not path.is_file():raise FileNotFoundError('No retained bot history exists')
    return path


def listing():
    rows=[]
    for candidate in sorted(root().glob('*.jsonl')):
        path=resolve(candidate.name);stat=path.stat()
        rows.append({'id':'bot-history:'+path.name,'name':path.stem,'bytes':stat.st_size,'modified':stat.st_mtime})
    return {'items':rows,'source':'Harness-owned retained histories, including archives'}


def history(identifier,offset=0):
    if int(offset)<0:return recent(identifier,-int(offset)-1)
    name=identifier.removeprefix('bot-history:');path=resolve(name);start=max(0,int(offset));items=[];errors=[]
    with path.open('rb') as stream:
        stream.seek(start)
        while len(items)<40 and stream.tell()-start<1024*1024:
            position=stream.tell();line=stream.readline()
            if not line:break
            try:items.append(json.loads(line))
            except ValueError:errors.append({'offset':position,'error':'Malformed retained record'})
        end=stream.tell()
    return {'items':items,'errors':errors,'next_offset':end if end<path.stat().st_size else None,'source':'Harness-owned JSONL','name':name}


def records(errors):
    try:rows=listing()['items']
    except (OSError,ValueError) as exc:
        errors.append({'source':'Bot histories','error':str(exc)});return
    for row in rows:
        path=resolve(row['id'].removeprefix('bot-history:'))
        yield {'id':row['id'],'title':row['name'],'path':str(path),'project':'Office voices',
               'kind':'conversation','body':'','modified':row['modified'],'coverage':'full retained bot transcript','source_path':str(path)}


def recent(identifier,before=0):
    name=identifier.removeprefix('bot-history:');path=resolve(name)
    items=[];errors=[];first=0
    for first,line in reverse_records(path,before):
        try:items.append(json.loads(line))
        except ValueError:errors.append({'offset':first,'error':'Malformed retained record'})
        if len(items)+len(errors)==40:break
    return {'items':list(reversed(items)),'errors':errors,'name':name,
            'previous_offset':-(first+1) if first else None,'next_offset':None,'source':'Harness-owned JSONL'}
