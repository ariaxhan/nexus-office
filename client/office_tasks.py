"""The authenticated phone's typed task boundary."""
from contextlib import closing
import json
from pathlib import Path
import re
import subprocess
import sys
import time

# The application already serves sibling modules from client/. Nexus is its
# repository-owned lifecycle, not a globally installed unrelated package.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus.ledger import Ledger
from nexus import flights as flight_runtime
from nexus import office_tasks as tasks
import office_objects as objects
import office_profiles as profiles
import run_board
import office_uploads as uploads


def projects():
    return [root for root in objects.roots().values() if (Path(root['path'])/'.git').exists()]


def capabilities():
    return {'projects':projects(),'profiles':profiles.readiness(),
            'source':'Mac installed engines','workspace':'Disposable clone of the saved commit; uncommitted edits stay in the original checkout.'}


def request_id(body):
    value=body.get('request_id','')
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',value):
        raise ValueError('A stable request ID is required')
    return value


def prompt(body,key='prompt'):
    text=body.get(key)
    if not isinstance(text,str) or not text.strip() or len(text)>64000:
        raise ValueError('Write a message between 1 and 64,000 characters')
    return text.strip()


def start(body):
    key=request_id(body)
    engine,profile=body.get('engine'),body.get('profile')
    message=prompt(body)
    intent={'engine':engine,'profile':profile,'project':{'id':body.get('project')},'prompt':message,'context':body.get('context'),'uploads':body.get('uploads') or [],'source_ref':body.get('source_ref','HEAD')}
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        prior=tasks.existing(ledger,key,tasks.digest(intent))
        if prior:
            return {k:v for k,v in prior.items() if k!='payload_hash'}
    project=next((r for r in projects() if r['id']==body.get('project')),None)
    if not project:
        raise ValueError('Choose an available Mac project')
    profiles.require(engine,profile)
    revision=source_revision(project,body.get('source_ref','HEAD'))
    spec={'engine':engine,'profile':profile,'project':project,'source_revision':revision,'source_ref':body.get('source_ref','HEAD'),'prompt':message,
          'runtime_root':str(objects.vault()),'context':body.get('context'),'uploads':body.get('uploads') or [],'attachments':context_attachment(body.get('context'))+uploads.attachments(body.get('uploads'))}
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        receipt=tasks.submit(ledger,key,spec)
    return {k:v for k,v in receipt.items() if k!='payload_hash'}


def detail(identifier):
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        spec=tasks.specification(ledger,identifier)
        task=dict(ledger.task(identifier))
        flights=[dict(row) for row in ledger.flights(task_id=identifier)]
    return {'task':task,'specification':spec,'flights':flights}


def say(body):
    key=request_id(body);text=prompt(body,'text')
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        return tasks.message_payload(ledger,body.get('task_id',''),key,{'text':text,'attachments':uploads.attachments(body.get('uploads'))})


def history(identifier,cursor=0):
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        tasks.specification(ledger,identifier)
        value=int(cursor)
        if value<0:
            before=-value-1 or 9223372036854775807
            rows=list(reversed(ledger.conn.execute('SELECT * FROM events WHERE subject=? AND id<? ORDER BY id DESC LIMIT 100',(identifier,before)).fetchall()))
        else:
            rows=ledger.conn.execute('SELECT * FROM events WHERE subject=? AND id>? ORDER BY id LIMIT 100',(identifier,value)).fetchall()
        earlier=bool(rows and ledger.conn.execute('SELECT 1 FROM events WHERE subject=? AND id<? LIMIT 1',(identifier,rows[0]['id'])).fetchone())
    return {'items':[dict(row,payload=json.loads(row['payload'])) for row in rows],
            'previous_cursor':-(rows[0]['id']+1) if earlier else None,
            'next_cursor':rows[-1]['id'] if len(rows)==100 else None}


def control(body):
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        return tasks.control(ledger,body.get('task_id',''),request_id(body),body.get('action'),body.get('flight_id'))


def answer(body):
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        return tasks.answer(ledger,body.get('task_id',''),request_id(body),int(body.get('permission_id',0)),body.get('decision'),body.get('answers'))


def listing(cursor=0,project=""):
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        rows=ledger.conn.execute("SELECT t.id,t.title,t.state,t.created_at,e.payload specification FROM tasks t JOIN events e ON e.subject=t.id AND e.kind='office.task' WHERE (?='' OR json_extract(e.payload,'$.project.id')=?) ORDER BY t.created_at DESC LIMIT 40 OFFSET ?",(project,project,max(0,int(cursor)))).fetchall()
        items=[task_row(ledger,row) for row in rows]
    return {'items':items,'next_cursor':int(cursor)+40 if len(items)==40 else None}


def active_listing():
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        rows=ledger.conn.execute("""SELECT t.id,t.title,t.state,t.created_at,e.payload specification
            FROM tasks t JOIN events e ON e.subject=t.id AND e.kind='office.task'
            WHERE COALESCE((SELECT state FROM flights WHERE task_id=t.id ORDER BY created_at DESC LIMIT 1),'queued') IN ('running','queued')
            AND t.state NOT IN ('closed','cancelled','failed','done') ORDER BY t.created_at DESC""").fetchall()
        items=[task_row(ledger,row) for row in rows]
    return {'items':items,'next_cursor':None}


def task_row(ledger,row):
    data=dict(row);data['specification']=json.loads(data['specification'])
    last=ledger.conn.execute('SELECT id,state,pid,started_at FROM flights WHERE task_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone()
    data['flight']=dict(last) if last else None
    phase=ledger.conn.execute("SELECT payload FROM events WHERE kind='office.phase' AND subject=? ORDER BY id DESC LIMIT 1",(row['id'],)).fetchone()
    data['phase']=json.loads(phase['payload']).get('state') if phase else 'queued'
    if last and last['state'] in ('failed','cancelled','produced'):
        data['phase']='closed' if last['state']=='produced' else last['state']
    data['observed_at']=time.time()
    if last and last['state']=='running' and not flight_runtime.alive(last['pid']):
        data['phase']='starting' if time.time()-(last['started_at'] or 0)<5 else 'runner missing; awaiting reconciliation'
    return data


def permissions():
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        rows=ledger.conn.execute("""SELECT e.id,e.subject task_id,e.payload,t.title FROM events e
            JOIN tasks t ON t.id=e.subject JOIN flights f ON f.id=json_extract(e.payload,'$.flight_id')
            WHERE e.kind='office.permission' AND f.state='running'
            AND NOT EXISTS (SELECT 1 FROM events c WHERE c.subject=e.subject AND c.kind='office.permission_closed' AND json_extract(c.payload,'$.permission_id')=e.id)
            ORDER BY e.id""").fetchall()
    return {'items':[dict(row,payload=json.loads(row['payload'])) for row in rows]}


def context_attachment(reference):
    if reference is None:return []
    if isinstance(reference,dict) and str(reference.get('id','')).startswith('upload:'):return [uploads.resolve(reference)]
    if not isinstance(reference,dict) or set(reference) not in ({'id','revision'},{'id','revision','start_line','end_line'}):raise ValueError('Context needs an exact object, revision and optional line range')
    data=objects.read(reference['id'])
    if data['revision']!=reference['revision']:raise FileExistsError('The selected file changed; reopen it before attaching')
    selected='start_line' in reference
    if not data['is_text'] or (not selected and data['next_offset'] is not None):raise ValueError('Attach a text file up to1 MiB, or select its lines')
    text=objects.selection(reference) if selected else data['text']
    source=data['project']+'/'+data['path']
    if selected:source+=f":L{reference['start_line']}-L{reference['end_line']}"
    import hashlib
    name=hashlib.sha256(reference['id'].encode()).hexdigest()[:16]+'.txt'
    return [{'name':name,'object_id':reference['id'],'revision':reference['revision'],'text':text,'source':source}]


def source_revision(project,reference='HEAD'):
    if not isinstance(reference,str) or (reference!='HEAD' and not re.fullmatch(r'[0-9a-f]{40,64}',reference)):
        raise ValueError('Choose a branch or commit from this project')
    proc=subprocess.run(['git','-C',project['path'],'rev-parse','--verify',reference+'^{commit}'],capture_output=True,text=True,timeout=5)
    if proc.returncode:raise ValueError('The selected commit is no longer available in this checkout')
    return proc.stdout.strip()


def source_choices(identifier):
    project=next((row for row in projects() if row['id']==identifier),None)
    if project is None:raise ValueError('Choose an available Mac project')
    def git(*args):
        return subprocess.run(['git','-C',project['path'],*args],capture_output=True,text=True,timeout=5,check=True).stdout.strip()
    revision=source_revision(project)
    rows=git('for-each-ref','--format=%(objectname)%00%(refname:short)','refs/heads','refs/remotes')
    items=[{'id':sha,'label':name+' · '+sha[:12]} for sha,name in (line.split('\0',1) for line in rows.splitlines())]
    return {'items':items,'revision':revision,'dirty':bool(git('status','--porcelain=1','--untracked-files=normal')),'observed_at':time.time()}
