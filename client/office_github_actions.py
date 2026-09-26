"""Typed, receipt-backed GitHub writes; no shell text from the phone."""
from contextlib import closing
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse

from nexus.ledger import Ledger,loads
import office_github as github
import office_github_bump as bump
import office_tasks
import run_board


def request(endpoint,method,payload,token):
    result=subprocess.run(['gh','api','--method',method,endpoint,'--input','-'],input=json.dumps(payload),
                          text=True,capture_output=True,env=dict(os.environ,GH_TOKEN=token),timeout=120)
    if result.returncode:raise RuntimeError('GitHub did not confirm the action: '+result.stderr[:240])
    try:return json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:raise RuntimeError('GitHub response was unreadable; the action may have completed') from None


def text(body,key,required=False,limit=60000):
    value=body.get(key,'')
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):raise ValueError('Invalid '+key)
    return value


def validate(body):
    action=body.get('action')
    if action not in ('create','comment','review','labels','label_add','label_remove','edit','bump','close','reopen','merge'):raise ValueError('Unknown GitHub action')
    text(body,'body',required=action=='comment' or (action=='review' and body.get('event')!='APPROVE'))
    if action=='create':text(body,'title',required=True,limit=256)
    else:github.number(body.get('number'))
    if action in ('review','merge') and not re.fullmatch(r'[0-9a-f]{40,64}',body.get('head','')):raise ValueError('An exact reviewed head is required')
    if action=='review' and body.get('event') not in ('APPROVE','COMMENT','REQUEST_CHANGES'):raise ValueError('Invalid review action')
    if action in ('labels','label_add','label_remove'):validate_labels(body.get('labels'))
    if action=='edit':validate_edit(body)
    if action=='bump':bump.validate(body)
    return action


def validate_edit(body):
    if 'title' not in body and 'body' not in body:raise ValueError('Edit needs a title or a body')
    if 'title' in body:text(body,'title',required=True,limit=256)


def validate_labels(labels):
    if not isinstance(labels,list) or len(labels)>20:raise ValueError('Choose at most20 labels')
    if any(not isinstance(label,str) or not label.strip() or len(label)>100 for label in labels):raise ValueError('Invalid label')


def fresh_access(access,repo,known):
    who,token=github.identity(access,repo,known)
    metadata=request('repos/'+repo,'GET',{},token)
    if not metadata.get('permissions',{}).get('push'):raise PermissionError('This GitHub identity cannot write this repository')
    return who,token


def command(world,known,body,sync):
    key=office_tasks.request_id(body);action=validate(body)
    fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        with ledger.tx():
            prior=ledger.conn.execute("SELECT payload FROM events WHERE subject=? AND kind='office.github_request' ORDER BY id LIMIT 1",(key,)).fetchone()
            if prior:return prior_result(ledger,key,fingerprint,loads(prior['payload'],{}))
            ledger._event('office.github_request',key,{'fingerprint':fingerprint,'body':body},'phone')
        attempted=False
        try:
            who,token=fresh_access(world.access(),body['repo'],known)
            attempted=True
            result=perform(action,body,who,token,sync)
            receipt={'state':'confirmed','acting_identity':who,'result':result}
        except Exception as exc:
            rejected=not attempted or isinstance(exc,(ValueError,PermissionError,FileExistsError))
            receipt={'state':'rejected' if rejected else 'unconfirmed','error':str(exc)[:400],'request_id':key}
        ledger.event('office.github_result',key,receipt,source='office')
    with github.LOCK:github.CACHE.clear()
    return receipt


def prior_result(ledger,key,fingerprint,prior):
    if fingerprint!=prior['fingerprint']:raise FileExistsError('This action ID already belongs to another request')
    row=ledger.conn.execute("SELECT payload FROM events WHERE subject=? AND kind='office.github_result' ORDER BY id DESC LIMIT 1",(key,)).fetchone()
    return loads(row['payload'],{}) if row else {'state':'unconfirmed','error':'The earlier action may have reached GitHub. Inspect its source before submitting another.','request_id':key}


def perform(action,body,who,token,sync):
    repo=body['repo'];num=str(body.get('number',''));endpoint=f'repos/{repo}/issues'
    message=text(body,'body')+'\n\n<!-- office-request:'+body['request_id']+' -->'
    if action=='create':return request(endpoint,'POST',{'title':body['title'],'body':message},token)
    if action=='comment':return request(endpoint+'/'+num+'/comments','POST',{'body':message},token)
    if action in ISSUE_WRITES:return ISSUE_WRITES[action](repo,endpoint+'/'+num,body,token)
    if action=='merge':
        ok,result=sync.apply_merge(repo,who,token,{'pr':num,'head':body['head']},False)
        if not ok:
            if result.startswith('merge refused by GitHub'):raise RuntimeError(result)
            raise ValueError(result)
        return {'message':result}
    current=request(f'repos/{repo}/pulls/{num}','GET',{},token)
    if current['head']['sha']!=body['head']:raise FileExistsError('PR head changed; reopen the current diff before reviewing')
    return request(f'repos/{repo}/pulls/{num}/reviews','POST',{'commit_id':body['head'],'event':body['event'],'body':message},token)


ISSUE_WRITES={
    'labels':lambda repo,issue,body,token:request(issue+'/labels','PUT',{'labels':body['labels']},token),
    'label_add':lambda repo,issue,body,token:request(issue+'/labels','POST',{'labels':body['labels']},token),
    'label_remove':lambda repo,issue,body,token:remove_labels(issue,body['labels'],token),
    'edit':lambda repo,issue,body,token:request(issue,'PATCH',{key:body[key] for key in ('title','body') if key in body},token),
    'bump':lambda repo,issue,body,token:perform_bump(repo,issue,body['priority'],token),
    'close':lambda repo,issue,body,token:request(issue,'PATCH',{'state':'closed'},token),
    'reopen':lambda repo,issue,body,token:request(issue,'PATCH',{'state':'open'},token),
}


def remove_labels(endpoint,labels,token):
    """One DELETE per label, so a concurrent change to any other label survives. Absent is removed."""
    for label in labels:
        try:request(endpoint+'/labels/'+urllib.parse.quote(label,safe=''),'DELETE',{},token)
        except RuntimeError as exc:
            if 'HTTP 404' not in str(exc):raise
    return request(endpoint,'GET',{},token)


def perform_bump(repo,endpoint,priority,token):
    current=request(endpoint,'GET',{},token)
    verdict=bump.assess(repo,current,priority)
    if not verdict['eligible']:raise PermissionError('Bump refused: '+verdict['reason'])
    stale=[label['name'] for label in current.get('labels',[]) if label['name'].lower() in bump.PRIORITIES and label['name'].lower()!=priority]
    request(endpoint+'/labels','POST',{'labels':['ready',priority]},token)
    return remove_labels(endpoint,stale,token)


def reconcile(world,known,body):
    key=office_tasks.request_id(body)
    page=max(1,int(body.get('page',1)))
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        row=ledger.conn.execute("SELECT payload FROM events WHERE subject=? AND kind='office.github_request' ORDER BY id LIMIT 1",(key,)).fetchone()
        if row is None:raise FileNotFoundError('No recorded GitHub request')
        prior=loads(row['payload'],{});intent=prior['body']
        previous=prior_result(ledger,key,prior['fingerprint'],prior)
        if previous.get('state') in ('confirmed','rejected'):return previous
        who,token=fresh_access(world.access(),intent['repo'],known)
        result,next_page=observe_outcome(intent,who,token,page)
        receipt={'state':'confirmed' if result is not None else 'unconfirmed','request_id':key,
                 'acting_identity':who,'result':result,'next_page':next_page,
                 'evidence':'Current GitHub source; no action replayed','observed_at':time.time()}
        if result is None:receipt['error']='No matching outcome confirmed yet. No write was repeated.'
        ledger.event('office.github_result',key,receipt,source='office-reconciliation')
        return receipt


def observe_outcome(body,who,token,page):
    repo=body['repo'];num=body.get('number');action=body['action']
    base=f'repos/{repo}/issues'
    if action in ('labels','label_add','label_remove','edit','bump','close','reopen','merge'):
        endpoint=f'repos/{repo}/pulls/{num}' if action=='merge' else f'{base}/{num}'
        current=request(endpoint,'GET',{},token)
        return (current if desired_state(body,current) else None),None
    endpoint={'create':base+'?state=all&sort=created&direction=desc',
              'comment':f'{base}/{num}/comments?', 'review':f'repos/{repo}/pulls/{num}/reviews?'}[action]
    rows=request(endpoint+f'&per_page=100&page={page}','GET',{},token)
    marker='<!-- office-request:'+body['request_id']+' -->'
    for row in rows:
        if marker in (row.get('body') or '') and row.get('user',{}).get('login','').lower()==who.lower():
            return row,None
    return None,page+1 if len(rows)==100 else None


LABEL_OUTCOMES={
    'label_add':lambda body,names:all(label.lower() in names for label in body['labels']),
    'label_remove':lambda body,names:not any(label.lower() in names for label in body['labels']),
    'bump':lambda body,names:{'ready',body['priority']}<=names and not (names&set(bump.PRIORITIES))-{body['priority']},
}


def desired_state(body,current):
    action=body['action']
    if action in LABEL_OUTCOMES:return LABEL_OUTCOMES[action](body,{label['name'].lower() for label in current.get('labels',[])})
    if action=='labels':return sorted(label['name'] for label in current.get('labels',[]))==sorted(body['labels'])
    if action=='edit':return all(current.get(key)==body[key] for key in ('title','body') if key in body)
    if action=='merge':return bool(current.get('merged')) and current.get('head',{}).get('sha')==body['head']
    return current.get('state')==('closed' if action=='close' else 'open')
