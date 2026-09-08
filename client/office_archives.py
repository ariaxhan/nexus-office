"""Exact, account-labelled native histories; no process-to-newest-file guesses."""
import hashlib
import codecs
import json
import os
from pathlib import Path
import time

import office_objects as objects
import office_profiles as profiles

CACHE = {'at': 0, 'items': [], 'errors': []}


def locations():
    seats=profiles.seats()
    result=[]
    for (engine,profile),home in seats.items():
        home=home or Path.home()/'.claude'
        folders=('sessions','archived_sessions') if engine=='codex' else ('projects',)
        result.extend((engine,profile,home/folder) for folder in folders)
    # Desktop and CLI Personal stores are distinct; retain both identities.
    result.extend(('codex','personal',Path.home()/'.codex'/folder) for folder in ('sessions','archived_sessions'))
    return result


def files(base):
    def failed(exc):raise exc
    for directory,dirs,names in os.walk(base,followlinks=False,onerror=failed):
        parent=Path(directory)
        dirs[:]=[d for d in dirs if not (parent/d).is_symlink()]
        for name in names:
            path=parent/name
            if name.endswith('.jsonl') and not path.is_symlink():yield path


def metadata(engine,profile,path):
    native=None;cwd='';title=path.stem
    with path.open() as stream:
        for line in stream:
            try:row=json.loads(line)
            except ValueError:continue
            info=row.get('payload',{}) if row.get('type')=='session_meta' else row
            native=native or info.get('sessionId') or (info.get('id') if row.get('type')=='session_meta' else None)
            cwd=cwd or info.get('cwd','')
            if row.get('type')=='custom-title':title=row.get('customTitle') or title
            if native and cwd:break
    if not native:return None
    return {'id':'archive:'+hashlib.sha256(str(path).encode()).hexdigest(), 'engine_session_id':native,
            'engine':engine,'profile':profile,'title':title,'cwd':cwd,'path':str(path),
            'modified':path.stat().st_mtime,'bytes':path.stat().st_size}


def inventory(refresh=False):
    if not refresh and time.time()-CACHE['at']<120:return CACHE
    items=[];errors=[];seen=set()
    for engine,profile,base in locations():
        if not base.exists():continue
        try:
            for path in files(base):
                if str(path) in seen:continue
                seen.add(str(path))
                try:
                    row=metadata(engine,profile,path)
                    if row:items.append(row)
                except (OSError,ValueError) as exc:errors.append({'source':str(path),'error':str(exc)[:200]})
        except OSError as exc:errors.append({'source':str(base),'error':str(exc)[:200]})
    CACHE.update(at=time.time(),items=sorted(items,key=lambda r:r['modified'],reverse=True),errors=errors)
    return CACHE


def listing(cursor=0):
    data=inventory();start=max(0,int(cursor));end=start+40
    return {'items':[{k:v for k,v in row.items() if k!='path'} for row in data['items'][start:end]],
            'total':len(data['items']),'next_cursor':end if end<len(data['items']) else None,
            'observed_at':data['at'],'errors':data['errors']}


def lookup(identifier):
    row=next((row for row in inventory()['items'] if row['id']==identifier),None)
    if row is None:raise FileNotFoundError('Native history is not available')
    path=Path(row['path'])
    if any(p.is_symlink() for p in (path,*path.parents)):raise PermissionError('Linked history cannot be opened')
    return row,path


def transcript(identifier,offset=0):
    row,path=lookup(identifier);start=max(0,int(offset))
    # Byte cursor preserves every original record, even one larger than a page.
    with path.open('rb') as stream:
        stream.seek(start);raw=stream.read(65536)
    decoded=codecs.getincrementaldecoder('utf-8')(errors='replace');text=decoded.decode(raw,final=start+len(raw)>=path.stat().st_size)
    consumed=len(raw)-len(decoded.getstate()[0])
    return {'session':{k:v for k,v in row.items() if k!='path'},'text':text,
            'offset':start,'next_offset':start+consumed if start+consumed<path.stat().st_size else None,
            'format':'native JSONL; complete retained records','bytes':path.stat().st_size}


def records(errors):
    try:
        data=inventory(refresh=True)
    except (OSError,ValueError) as exc:
        errors.append({'source':'Native histories','error':str(exc)[:200]})
        return
    errors.extend(data['errors'])
    for row in data['items']:
        yield {'id':row['id'],'title':row['title'],'path':row['cwd'],'project':row['profile']+' '+row['engine'],
               'kind':'conversation','body':'','modified':row['modified'],'coverage':'full native transcript',
               'source_path':row['path']}


def messages(identifier,offset=0):
    if int(offset)<0:return recent_messages(identifier,-int(offset)-1)
    row,path=lookup(identifier);start=max(0,int(offset));items=[]
    with path.open('rb') as stream:
        stream.seek(start)
        while len(items)<40 and stream.tell()-start<1024*1024:
            position=stream.tell();line=stream.readline()
            if not line:break
            try:item=readable_record(json.loads(line))
            except ValueError:item={'role':'Unreadable record','text':line.decode('utf-8',errors='replace')}
            if item:items.append(dict(item,offset=position))
        end=stream.tell()
    return {'session':{k:v for k,v in row.items() if k!='path'},'items':items,
            'next_offset':end if end<path.stat().st_size else None,'bytes':path.stat().st_size,
            'source':'Complete retained native history; raw records remain available'}


def readable_record(row):
    kind=row.get('type','event')
    if kind in ('session_meta','custom-title','queue-operation','file-history-snapshot'):return None
    message=row.get('message') or row.get('payload') or row
    if not isinstance(message,dict):return {'role':kind,'text':str(message)}
    role=message.get('role') or kind
    content=message.get('content')
    if isinstance(content,str):return {'role':role,'text':content}
    if isinstance(content,list):return {'role':role,'text':'\n\n'.join(block_text(block) for block in content)}
    text=message.get('message') or message.get('text')
    if isinstance(text,str):return {'role':role,'text':text}
    return {'role':kind,'text':json.dumps(message,ensure_ascii=False,indent=2),'structured':True}


def block_text(block):
    if isinstance(block,str):return block
    if not isinstance(block,dict):return str(block)
    if block.get('text'):return block['text']
    if block.get('type') in ('image','input_image'):return '[Image retained in the raw record]'
    if block.get('type')=='tool_result':return str(block.get('content',''))
    return json.dumps(block,ensure_ascii=False,indent=2)


def reverse_records(path,before=0):
    with path.open('rb') as stream:
        end=min(before or path.stat().st_size,path.stat().st_size)
        remaining=b''
        while end:
            start=max(0,end-65536)
            stream.seek(start)
            data=stream.read(end-start)+remaining
            parts=data.split(b'\n')
            remaining=parts[0]
            position=start+len(data)
            for line in reversed(parts[1:]):
                position-=len(line)+1
                if line.strip():yield position+1,line
            end=start
        if remaining.strip():yield 0,remaining


def recent_messages(identifier,before=0):
    row,path=lookup(identifier)
    items=[]
    for position,line in reverse_records(path,before):
        try:item=readable_record(json.loads(line))
        except ValueError:item={'role':'Unreadable record','text':line.decode('utf-8',errors='replace')}
        if item:items.append(dict(item,offset=position))
        if len(items)==40:break
    items.reverse()
    first=items[0]['offset'] if items else 0
    return {'session':{k:v for k,v in row.items() if k!='path'},'items':items,
            'previous_offset':-(first+1) if first else None,'next_offset':None,
            'bytes':path.stat().st_size,'source':'Latest retained messages; load earlier messages above'}
