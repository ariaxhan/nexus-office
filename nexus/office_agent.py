"""One ledger-owned, Mac-hosted conversation; engines are child transports.

Task output and the disposable checkout outlive flight cleanup. No cloud host,
PTY injection, or independent scheduler is involved.
"""
from contextlib import closing
import json
import base64
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import time

from .ledger import Ledger,loads
from . import office_tasks
from .office_rpc import Codex

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_profiles

APPROVALS={'item/tool/requestUserInput','item/commandExecution/requestApproval','item/fileChange/requestApproval','item/permissions/requestApproval','claude/requestApproval'}


def write(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,indent=2)+'\n')
    temporary.replace(path)


def git(directory,*args):
    return subprocess.run(['git','-C',str(directory),*args],capture_output=True,text=True,timeout=180,check=True).stdout


def prepare(ledger,task_id,spec):
    directory=Path(ledger.path).parent/'office-tasks'/task_id
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkout=directory/'checkout'
    if not checkout.exists():
        git(directory,'clone','--shared','--no-checkout',spec['project']['path'],str(checkout))
        git(checkout,'checkout','-b','office/'+task_id,spec['source_revision'])
        source=Path(spec['project']['path'])
        if 'origin' in git(source,'remote').splitlines():
            original=git(source,'remote','get-url','origin').strip()
            git(checkout,'remote','set-url','origin',original)
        else:
            git(checkout,'remote','remove','origin')
    attach_context(checkout,spec.get('attachments',[]))
    return directory,checkout


def attach_context(checkout,attachments):
    if not attachments:return
    folder=checkout/'.office-context'
    if folder.is_symlink():raise PermissionError('Context folder was replaced by a link')
    folder.mkdir(exist_ok=True)
    for item in attachments:materialize_attachment(folder,item)
    excludes=checkout/'.git/info/exclude'
    with excludes.open('a') as stream:stream.write('\n.office-context/\n')


def materialize_attachment(folder,item):
    if Path(item['name']).name!=item['name']:raise PermissionError('Invalid context filename')
    target=folder/item['name']
    if target.is_symlink():raise PermissionError('Context snapshot was replaced by a link')
    raw=target.read_bytes() if target.exists() else attachment_bytes(item)
    if hashlib.sha256(raw).hexdigest()!=item['revision']:raise ValueError('Attachment snapshot failed its revision check')
    if not target.exists():target.write_bytes(raw);target.chmod(0o444)


def attachment_bytes(item):
    if 'text' in item:return item['text'].encode()
    path=Path(item['snapshot_path'])
    if any(part.is_symlink() for part in (path,*path.parents)):raise PermissionError('Linked attachment storage is unavailable')
    record=json.loads(path.read_text())
    return base64.b64decode(record['base64'],validate=True)


class Conversation:
    def __init__(self,ledger,flight,directory,checkout,spec):
        self.ledger=ledger;self.flight=flight;self.task_id=flight['task_id']
        self.directory=directory;self.checkout=checkout;self.spec=spec
        self.cursor=0;self.closed=False;self.adapter=None
        self.pending={}
        previous=directory/'response.md'
        self.output=[previous.read_text().strip()] if previous.exists() else []

    def emit(self,kind,payload):
        data=dict(payload,flight_id=self.flight['id'])
        self.ledger.event(kind,self.task_id,data,source='office-engine')
        with (self.directory/'conversation.jsonl').open('a') as stream:
            stream.write(json.dumps({'at':time.time(),'kind':kind,'payload':data})+'\n')

    def connect(self,stderr):
        previous=self.directory/'session.json'
        resume=json.loads(previous.read_text()).get('engine_session_id') if previous.exists() else None
        env=office_profiles.environment(self.spec['engine'],self.spec['profile'])
        # SDK environment options overlay their parent; clean this dedicated
        # flight process too, so removed credentials cannot be inherited again.
        os.environ.clear();os.environ.update(env)
        engine=Codex
        if self.spec['engine']=='claude':
            from .office_claude import Claude
            engine=Claude
        self.adapter=engine(env,str(self.checkout),stderr,resume=resume)
        session={'engine_session_id':self.adapter.session_id,'engine':self.spec['engine'],'profile':self.spec['profile'],'checkout':str(self.checkout),'flight_id':self.flight['id']}
        write(previous,session);self.emit('office.session',session)
        self.emit('office.phase',{'state':'listening' if resume else 'starting'})

    def run(self):
        try:
            while not self.closed:
                message=self.adapter.event()
                if message:self.provider(message)
                self.commands()
        finally:
            try:
                if self.adapter:self.adapter.close()
            finally:
                self.emit('office.session_closed',{})
                self.save_outputs()

    def provider(self,message):
        method=message.get('method','')
        if method=='office/disconnected':
            raise RuntimeError('Engine disconnected; conversation and checkout retained')
        if 'id' in message and 'method' in message:
            return self.provider_request(message)
        self.emit('office.provider',message)
        text=assistant_text(method,message.get('params',{}))
        if text:
            self.output.append(text)
            (self.directory/'response.md').write_text('\n\n'.join(self.output)+'\n')
        if method in ('turn/completed','claude/ResultMessage'):
            self.emit('office.phase',{'state':'listening'})
            self.save_outputs()

    def provider_request(self,message):
        if message['method'] not in APPROVALS:
            self.emit('office.unsupported_request',message)
            # Never reinterpret an unfamiliar control request as permission.
            if hasattr(self.adapter,'rpc'):
                self.adapter.rpc.send({'id':message['id'],'error':{'code':-32601,'message':'This request type is not supported by Office yet'}})
            return
        self.emit('office.permission',message)
        event=self.ledger.conn.execute("SELECT id FROM events WHERE kind='office.permission' AND subject=? ORDER BY id DESC LIMIT 1",(self.task_id,)).fetchone()
        self.pending[event['id']]=message['id']
        self.emit('office.phase',{'state':'needs_permission'})

    def commands(self):
        rows=self.ledger.conn.execute('SELECT id,kind,payload FROM events WHERE subject=? AND id>? ORDER BY id LIMIT 100',(self.task_id,self.cursor)).fetchall()
        for row in rows:
            self.cursor=row['id']
            body=loads(row['payload'],{})
            actions={'office.message':self.message,'office.control':self.control,'office.permission_answer':self.answer}
            if row['kind'] in actions:actions[row['kind']](row['id'],body)

    def message(self,event_id,body):
        prior=self.ledger.conn.execute("SELECT payload FROM events WHERE subject=? AND kind='office.delivery' AND json_extract(payload,'$.message_id')=? ORDER BY id DESC LIMIT 1",(self.task_id,event_id)).fetchone()
        if prior:
            delivery=loads(prior['payload'],{})
            if delivery.get('state')=='attempting' and delivery.get('flight_id')!=self.flight['id']:
                self.emit('office.delivery',{'message_id':event_id,'state':'uncertain','error':'Previous attempt ended before provider acknowledgement; inspect retained output before resending.'})
                self.emit('office.phase',{'state':'needs_attention'})
            return
        self.emit('office.delivery',{'message_id':event_id,'state':'attempting'})
        try:
            attach_context(self.checkout,body.get('attachments',[]))
            self.adapter.message(office_tasks.initial_prompt({'prompt':body['text'],'attachments':body.get('attachments',[])}))
            self.emit('office.delivery',{'message_id':event_id,'state':'submitted','consumption':'unknown'})
            self.emit('office.phase',{'state':'working'})
        except Exception as exc:
            self.emit('office.delivery',{'message_id':event_id,'state':'uncertain','error':str(exc)[:300]})

    def control(self,event_id,body):
        if body.get('flight_id')!=self.flight['id']:return
        if body['action']=='close':self.closed=True
        elif body['action']=='interrupt':self.adapter.interrupt()
        self.emit('office.control_received',{'event_id':event_id,'action':body['action']})

    def answer(self,event_id,body):
        request=self.pending.pop(body.get('permission_id'),None)
        if request is None:
            return
        self.adapter.answer(request,body['decision'],body.get('answers'))
        self.emit('office.permission_closed',{'permission_id':body['permission_id'],'answer_event_id':event_id})
        self.emit('office.phase',{'state':'working'})

    def save_outputs(self):
        (self.directory/'response.md').write_text('\n\n'.join(self.output)+'\n')
        try:
            patch=changes(self.checkout,self.spec['source_revision'])
            (self.directory/'changes.patch').write_text(patch)
        except subprocess.SubprocessError as exc:
            self.emit('office.artifact_error',{'error':str(exc)[:200]})
        for name in ('response.md','conversation.jsonl','changes.patch','session.json'):
            source=self.directory/name
            if source.exists():(Path.cwd()/name).write_bytes(source.read_bytes())


def changes(checkout,revision):
    policy=office_profiles.objects.permitted
    tracked=[name for name in git(checkout,'diff','--name-only','-z',revision).split('\0') if name and policy(name)]
    patches=[]
    for start in range(0,len(tracked),80):
        patches.append(git(checkout,'diff',revision,'--binary','--',*tracked[start:start+80]))
    untracked=git(checkout,'ls-files','--others','--exclude-standard','-z').split('\0')
    for name in untracked:
        if not name or not policy(name):continue
        result=subprocess.run(['git','diff','--no-index','--binary','--','/dev/null',name],cwd=checkout,
                              capture_output=True,text=True,timeout=30)
        if result.returncode not in (0,1):raise RuntimeError('Could not capture new file '+name)
        patches.append(result.stdout)
    return ''.join(patches)


def assistant_text(method,params):
    if method=='item/completed':
        item=params.get('item',{})
        return item.get('text','') if item.get('type')=='agentMessage' else ''
    if method=='claude/AssistantMessage':
        return '\n'.join(block.get('text','') for block in params.get('content',[]) if 'text' in block)
    return ''


def main():
    os.umask(0o077)
    with closing(Ledger(os.environ.get('OFFICE_NEXUS_LEDGER') or None)) as ledger:
        flight=ledger.flight(Path.cwd().name)
        if flight is None:raise ValueError(f'Flight {Path.cwd().name} is absent from its owning ledger {ledger.path}')
        spec=office_tasks.specification(ledger,flight['task_id'])
        if spec.get('runtime_root'):
            os.environ['OFFICE_RUNTIME_ROOT']=spec['runtime_root']
        directory,checkout=prepare(ledger,flight['task_id'],spec)
        conversation=Conversation(ledger,flight,directory,checkout,spec)
        with (directory/'engine.log').open('a') as log:
            conversation.connect(log)
            conversation.run()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
