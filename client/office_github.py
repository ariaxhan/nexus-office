"""On-demand, cached GitHub objects using Office's existing account resolver."""
import base64
import copy
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse

import office_objects as objects

CACHE={}
LOCK=threading.Lock()


def identity(access,repo,known):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or repo not in known:
        raise PermissionError('This repository is not registered in Office')
    who,token=access.token_for(repo)
    if not token:
        raise PermissionError('No configured GitHub identity can access this repository')
    return who,token


def fetch(endpoint,who,token,accept='application/vnd.github+json'):
    key=(who,endpoint,accept)
    with LOCK:
        cached=CACHE.get(key)
    if cached and time.time()-cached[0]<120:
        return cached[1],cached[0]
    env=dict(os.environ,GH_TOKEN=token)
    result=subprocess.run(['gh','api','--method','GET',endpoint,'-H','Accept: '+accept],env=env,capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise ValueError('GitHub request failed: '+(result.stderr or 'unavailable')[:250])
    data=json.loads(result.stdout) if accept=='application/vnd.github+json' else result.stdout
    observed=time.time()
    with LOCK:CACHE[key]=(observed,data)
    return data,observed


def number(value):
    if not str(value).isdigit() or int(value)<1:
        raise ValueError('A positive issue or pull request number is required')
    return str(value)


def detail(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known)
    num=number(q.get('number'));kind=q.get('kind','issues')
    category='pulls' if kind=='prs' else 'issues'
    item,stamp=(fresh if kind=='prs' else fetch)(f'repos/{repo}/{category}/{num}',who,token)
    comments,comment_stamp=fetch(f'repos/{repo}/issues/{num}/comments?per_page=50&page=1',who,token)
    result=dict(item,comments=comments,acting_identity=who,observed_at=min(stamp,comment_stamp),source='GitHub',comments_next_cursor=2 if len(comments)==50 else None)
    if kind=='prs':
        cache_identity=who+':'+item['head']['sha']
        result['diff'],_=fetch(f'repos/{repo}/pulls/{num}',cache_identity,token,'application/vnd.github.v3.diff')
        result['files'],_=fetch(f'repos/{repo}/pulls/{num}/files?per_page=100&page=1',cache_identity,token)
        result['files_next_cursor']=2 if len(result['files'])==100 else None
        checks,_=fetch(f"repos/{repo}/commits/{item['head']['sha']}/check-runs?per_page=100",who,token)
        result['checks']=checks
        guard_head(repo,num,who,token,item['head']['sha'])
    return result


def comments(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known)
    num=number(q.get('number'));page=max(1,int(q.get('cursor',1)))
    rows,stamp=fetch(f'repos/{repo}/issues/{num}/comments?per_page=50&page={page}',who,token)
    return {'items':rows,'observed_at':stamp,'next_cursor':page+1 if len(rows)==50 else None,'source':'GitHub'}


def files(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known)
    num=number(q.get('number'));page=max(1,int(q.get('cursor',1)))
    expected=q.get('head','')
    current=guard_head(repo,num,who,token,expected)
    rows,stamp=fetch(f'repos/{repo}/pulls/{num}/files?per_page=100&page={page}',who+':'+current,token)
    guard_head(repo,num,who,token,current)
    return {'items':rows,'observed_at':stamp,'next_cursor':page+1 if len(rows)==100 else None,'source':'GitHub'}


def tree(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known)
    relative=q.get('path','')
    if relative and not objects.permitted(relative):
        raise PermissionError('Hidden and parent paths are not work objects')
    ref=commit_ref(repo,q.get('ref','HEAD'),who,token)
    endpoint=f'repos/{repo}/contents/{urllib.parse.quote(relative,safe="/")}?ref={urllib.parse.quote(ref,safe="")}'
    data,stamp=fetch(endpoint,who,token)
    if isinstance(data,list):
        visible=[row for row in data if objects.permitted(row['path'])]
        return {'items':visible,'excluded':len(data)-len(visible),'observed_at':stamp,'source':'GitHub: '+ref,'ref':ref}
    data=copy.deepcopy(data)
    raw=blob_content(repo,data,who,token)
    try:
        if b'\0' in raw:raise UnicodeError('Binary content')
        data['text']=raw.decode('utf-8');data['readable']=True
    except UnicodeError:
        data['text']='Binary file. Open source to download the original.';data['readable']=False
    data['observed_at']=stamp
    data.pop('content',None)
    return {'object':data,'source':'GitHub: '+ref,'ref':ref}


def collection(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known)
    category='pulls' if q.get('kind')=='prs' else 'issues'
    page=max(1,int(q.get('cursor',1)))
    rows,stamp=fetch(f'repos/{repo}/{category}?state=all&sort=updated&direction=desc&per_page=40&page={page}',who,token)
    items=rows if category=='pulls' else [row for row in rows if not row.get('pull_request')]
    return {'items':items,'next_cursor':page+1 if len(rows)==40 else None,'observed_at':stamp,'source':'GitHub: open and closed'}


def reviews(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known);num=number(q.get('number'))
    category='comments' if q.get('inline')=='true' else 'reviews'
    page=max(1,int(q.get('cursor',1)))
    rows,stamp=fetch(f'repos/{repo}/pulls/{num}/{category}?per_page=50&page={page}',who,token)
    return {'items':rows,'next_cursor':page+1 if len(rows)==50 else None,'observed_at':stamp}


def blob_content(repo,data,who,token):
    if data.get('encoding')=='base64':
        return base64.b64decode(data.get('content',''),validate=False)
    sha=data.get('sha','')
    if not re.fullmatch(r'[0-9a-f]{40,64}',sha):
        raise ValueError('GitHub did not return a readable blob identity')
    blob,_=fetch(f'repos/{repo}/git/blobs/{sha}',who,token)
    if blob.get('encoding')!='base64':
        raise ValueError('GitHub cannot expose this blob through its file API; open its source link')
    return base64.b64decode(blob['content'],validate=False)


def fresh(endpoint,who,token):
    with LOCK:CACHE.pop((who,endpoint,'application/vnd.github+json'),None)
    return fetch(endpoint,who,token)


def guard_head(repo,num,who,token,expected):
    item,_=fresh(f'repos/{repo}/pulls/{num}',who,token)
    head=item['head']['sha']
    if expected and head!=expected:raise FileExistsError('PR head changed; reopen the current diff before continuing')
    return head


def timeline(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known);num=number(q.get('number'));page=max(1,int(q.get('cursor',1)))
    rows,stamp=fetch(f'repos/{repo}/issues/{num}/timeline?per_page=100&page={page}',who,token)
    return {'items':rows,'observed_at':stamp,'next_cursor':page+1 if len(rows)==100 else None}


def checks(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known);sha=q.get('head','')
    if not re.fullmatch(r'[0-9a-f]{40,64}',sha):raise ValueError('An exact commit is required')
    page=max(1,int(q.get('cursor',1)));category='statuses' if q.get('kind')=='statuses' else 'check-runs'
    data,stamp=fetch(f'repos/{repo}/commits/{sha}/{category}?per_page=100&page={page}',who,token)
    rows=data if category=='statuses' else data['check_runs']
    return {'items':rows,'observed_at':stamp,'next_cursor':page+1 if len(rows)==100 else None,'head':sha}


def commit_ref(repo,ref,who,token):
    if re.fullmatch(r'[0-9a-fA-F]{40}',ref):return ref.lower()
    data,_=fetch(f'repos/{repo}/commits/{urllib.parse.quote(ref,safe="")}',who,token)
    sha=data.get('sha','')
    if not re.fullmatch(r'[0-9a-fA-F]{40}',sha):raise ValueError('GitHub did not return a commit identity')
    return sha.lower()


def branches(access,known,q):
    repo=q.get('repo','');who,token=read_identity(access,repo,known)
    page=max(1,int(q.get('cursor',1)))
    rows,stamp=fetch(f'repos/{repo}/branches?per_page=100&page={page}',who,token)
    return {'items':[{'name':row['name'],'sha':row['commit']['sha']} for row in rows],
            'next_cursor':page+1 if len(rows)==100 else None,'observed_at':stamp,'source':'GitHub branches'}


def read_identity(access,repo,known):
    resolver=getattr(access,'read_token_for',None)
    if resolver is None:return identity(access,repo,known)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or repo not in known:
        raise PermissionError('This repository is not registered in Office')
    who,token=resolver(repo)
    if not token:raise PermissionError('No configured GitHub identity can read this repository')
    return who,token
