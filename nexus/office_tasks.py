"""Phone commands persisted atomically in Nexus's existing ledger.

No request queue or task database lives beside the ledger. The request event,
task and queued flight commit together before the HTTP door acknowledges them.
"""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time

from .ledger import new_id, loads

PLAN_NAME='office-conversations'


def digest(spec):
    intent={key:spec[key] for key in ('engine','profile','prompt')}
    intent['project']=spec['project']['id']
    intent['context']=spec.get('context')
    if spec.get('uploads'):intent['uploads']=spec['uploads']
    if spec.get('source_ref','HEAD')!='HEAD':intent['source_ref']=spec['source_ref']
    return hashlib.sha256(json.dumps(intent,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def existing(ledger,request_id,payload_hash):
    row=ledger.conn.execute("SELECT payload FROM events WHERE kind='office.request' AND subject=? ORDER BY id LIMIT 1",(request_id,)).fetchone()
    if not row:
        return None
    receipt=loads(row['payload'],{})
    if receipt['payload_hash']!=payload_hash:
        raise FileExistsError('This request ID already belongs to a different task')
    return receipt


def plan(ledger,now):
    root=Path(__file__).resolve().parents[1]
    # Deployed releases are source archives; their Python lives outside the archive.
    runtime=Path(sys.executable)
    command=['env','PYTHONPATH='+str(root),'NEXUS_LEDGER='+ledger.path,'OFFICE_NEXUS_LEDGER='+ledger.path,str(runtime),'-m','nexus.office_agent']
    inputs={'cmd':shlex.join(command),'persistent_task':True}
    budget={'timeout_s':86400,'concurrency':8,'max_retries':3}
    row=ledger.conn.execute('SELECT id FROM plans WHERE name=?',(PLAN_NAME,)).fetchone()
    if row:
        ledger.conn.execute('UPDATE plans SET inputs=?,budget=?,resolution_policy=? WHERE id=?',
                            (json.dumps(inputs),json.dumps(budget),
                             json.dumps({'may_retry':True,'may_accept':True}),row['id']))
        return row['id']
    identifier=new_id('plan')
    ledger.conn.execute('INSERT INTO plans (id,name,kind,schedule,inputs,outputs,budget,resolution_policy,resources,enabled,created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)',
                        (identifier,PLAN_NAME,'script','{}',json.dumps(inputs),json.dumps(['response.md','conversation.jsonl','changes.patch','session.json']),json.dumps(budget),json.dumps({'may_retry':True,'may_accept':True}),'[]',now))
    ledger._event('plan.added',identifier,{'name':PLAN_NAME,'kind':'script'},'office',now)
    return identifier


def submit(ledger,request_id,spec):
    fingerprint=digest(spec)
    spec=dict(spec)
    spec['outcome_contract']={
        'user_goal':spec['prompt'],
        'terminal_condition':spec.get('terminal_condition') or spec['prompt'],
        'verification_method':spec.get('verification_method') or 'Verify the requested behavior at its actual target',
        'allowed_recovery':'diagnose, repair, retry, reassign, rollback and resume',
        'human_gate_policy':'request_human_input only',
    }
    with ledger.tx():
        receipt=existing(ledger,request_id,fingerprint)
        if receipt:
            return receipt
        now=time.time();plan_id=plan(ledger,now)
        task_id=new_id('task');flight_id=new_id('flt')
        title=spec['prompt'].splitlines()[0][:160]
        ledger.conn.execute("INSERT INTO tasks (id,plan_id,origin,title,state,dedupe_key,objective,created_at) VALUES (?,?,?,?,'accepted',?,?,?)",(task_id,plan_id,'office',title,'office:'+request_id,spec['prompt'],now))
        ledger._event('task.state',task_id,{'from':None,'to':'accepted'},'office',now)
        ledger.conn.execute("INSERT INTO flights (id,task_id,plan_id,state,created_at) VALUES (?,?,?,'queued',?)",(flight_id,task_id,plan_id,now))
        ledger._event('flight.state',flight_id,{'from':None,'to':'queued','task_id':task_id,'plan_id':plan_id},'office',now)
        ledger._event('office.task',task_id,spec,'office',now)
        ledger._event('office.message',task_id,{'request_id':'initial:'+request_id,'text':initial_prompt(spec),'initial':True},'phone',now)
        receipt={'task_id':task_id,'flight_id':flight_id,'request_id':request_id,'payload_hash':fingerprint,'state':'queued','status_url':'/api/tasks/detail?id='+task_id}
        ledger._event('office.request',request_id,receipt,'office',now)
        return receipt


def specification(ledger,task_id):
    row=ledger.conn.execute("SELECT payload FROM events WHERE kind='office.task' AND subject=? ORDER BY id LIMIT 1",(task_id,)).fetchone()
    if not row:
        raise FileNotFoundError('Office task not found')
    return loads(row['payload'],{})


def message(ledger,task_id,request_id,text):
    return message_payload(ledger,task_id,request_id,{'text':text,'attachments':[]})


def message_payload(ledger,task_id,request_id,payload):
    with ledger.tx():
        specification(ledger,task_id)
        prior=ledger.conn.execute("SELECT id,payload FROM events WHERE kind='office.message' AND subject=? AND json_extract(payload,'$.request_id')=?",(task_id,request_id)).fetchone()
        if prior:
            previous=loads(prior['payload'],{})
            if previous.get('text')!=payload['text'] or previous.get('attachments',[])!=payload.get('attachments',[]):
                raise FileExistsError('Message request ID was reused with different content')
            return {'message_id':prior['id'],'state':'queued'}
        ledger._event('office.message',task_id,dict(payload,request_id=request_id),'phone')
        message_id=ledger.conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        resume_if_idle(ledger,task_id)
        return {'message_id':message_id,'state':'queued'}


def control(ledger,task_id,request_id,action,flight_id):
    if action not in ('interrupt','close'):
        raise ValueError('Unknown conversation control')
    with ledger.tx():
        specification(ledger,task_id)
        row=ledger.flight(flight_id)
        if not row or row['task_id']!=task_id or row['state']!='running':
            raise FileExistsError('This attempt is no longer running')
        payload={'request_id':request_id,'action':action,'flight_id':flight_id}
        return command_event(ledger,'office.control',task_id,request_id,payload)


def command_event(ledger,kind,task_id,request_id,payload):
    row=ledger.conn.execute("SELECT id,payload FROM events WHERE subject=? AND kind=? AND json_extract(payload,'$.request_id')=? ORDER BY id LIMIT 1",(task_id,kind,request_id)).fetchone()
    if row:
        if loads(row['payload'],{})!=payload:
            raise FileExistsError('Request ID already has a different command')
        return {'event_id':row['id'],'state':'queued'}
    ledger._event(kind,task_id,payload,'phone')
    return {'event_id':ledger.conn.execute('SELECT last_insert_rowid()').fetchone()[0],'state':'queued'}


def resume_if_idle(ledger,task_id):
    live=ledger.conn.execute("SELECT 1 FROM flights WHERE task_id=? AND state IN ('queued','running','verifying','verified','landing','resolving') LIMIT 1",(task_id,)).fetchone()
    if live:return
    task=ledger.task(task_id)
    if task['state']!='running':
        raise FileExistsError('This task has ended and cannot be resumed')
    previous=ledger.conn.execute('SELECT max(attempt) FROM flights WHERE task_id=?',(task_id,)).fetchone()[0] or 0
    identifier=new_id('flt');now=time.time()
    ledger.conn.execute("INSERT INTO flights (id,task_id,plan_id,state,created_at,attempt) VALUES (?,?,?,'queued',?,?)",(identifier,task_id,task['plan_id'],now,previous+1))
    ledger._event('flight.state',identifier,{'from':None,'to':'queued','task_id':task_id,'plan_id':task['plan_id'],'attempt':previous+1},'phone',now)


def initial_prompt(spec):
    text=spec['prompt']
    for item in spec.get('attachments',[]):
        text+='\n\nAttached exact context: '+item['source']+' at revision '+item['revision']+'. Read .office-context/'+item['name']+'. This is an immutable snapshot; report if you need the current source instead.'
    return text
