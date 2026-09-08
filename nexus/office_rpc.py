"""The installed Codex app-server JSON-RPC transport, owned by one flight."""
import json
import queue
import subprocess
import threading
import uuid


class Transport:
    def __init__(self,command,env,cwd,stderr):
        self.events=queue.Queue()
        self.pending={}
        self.lock=threading.Lock()
        self.process=subprocess.Popen(command,env=env,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1)
        self.reader=threading.Thread(target=self.read,daemon=True,name='office-engine-events')
        self.reader.start()

    def send(self,message):
        with self.lock:
            self.process.stdin.write(json.dumps(message)+'\n')
            self.process.stdin.flush()

    def call(self,method,params,timeout=60):
        identifier='office-'+uuid.uuid4().hex
        channel=queue.Queue(maxsize=1)
        self.pending[identifier]=channel
        try:
            self.send({'id':identifier,'method':method,'params':params})
            try:
                reply=channel.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f'Engine did not acknowledge {method}') from None
            if 'error' in reply:
                raise RuntimeError(str(reply['error'])[:500])
            return reply.get('result',{})
        finally:
            self.pending.pop(identifier,None)

    def read(self):
        try:
            for line in self.process.stdout:
                try:
                    message=json.loads(line)
                except ValueError:
                    self.events.put({'method':'office/parseError','params':{'detail':'Engine emitted non-JSON output'}})
                    continue
                channel=self.pending.get(message.get('id'))
                if channel is not None and 'method' not in message:
                    channel.put(message)
                else:
                    self.events.put(message)
        finally:
            self.events.put({'method':'office/disconnected','params':{}})
            for channel in list(self.pending.values()):
                try:channel.put_nowait({'error':'Engine disconnected before acknowledging the request'})
                except queue.Full:pass

    def reply(self,identifier,result):
        self.send({'id':identifier,'result':result})

    def close(self):
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill();self.process.wait(timeout=5)


class Codex:
    def __init__(self,env,cwd,stderr,resume=None):
        self.rpc=Transport(['codex','app-server','--listen','stdio://'],env,cwd,stderr)
        self.rpc.call('initialize',{'clientInfo':{'name':'nexus_office','title':'Nexus Office','version':'1.0.0'},'capabilities':{}})
        self.rpc.send({'method':'initialized'})
        params={'cwd':str(cwd),'approvalPolicy':'on-request','sandbox':'workspace-write'}
        method='thread/start'
        if resume:
            method='thread/resume';params['threadId']=resume
        result=self.rpc.call(method,params)
        self.session_id=result['thread']['id']
        self.turn_id=None
        self.requests={}

    def message(self,text):
        inputs=[{'type':'text','text':text}]
        if self.turn_id:
            result=self.rpc.call('turn/steer',{'threadId':self.session_id,'expectedTurnId':self.turn_id,'input':inputs})
        else:
            result=self.rpc.call('turn/start',{'threadId':self.session_id,'input':inputs})
            self.turn_id=result['turn']['id']
        return result

    def interrupt(self):
        if self.turn_id:
            return self.rpc.call('turn/interrupt',{'threadId':self.session_id,'turnId':self.turn_id})
        return {'idle':True}

    def event(self,timeout=.2):
        try:message=self.rpc.events.get(timeout=timeout)
        except queue.Empty:return None
        if 'id' in message and 'method' in message:
            self.requests[message['id']]=message
        if message.get('method')=='turn/completed':
            self.turn_id=None
        return message

    def answer(self,identifier,decision,answers=None):
        request=self.requests.pop(identifier,{})
        result=approval_response(request,decision,answers)
        self.rpc.reply(identifier,result)

    def close(self):
        self.rpc.close()


def approval_response(request,decision,answers=None):
    if request.get('method')=='item/permissions/requestApproval':
        requested=request.get('params',{}).get('permissions',{})
        return {'permissions':requested if decision=='accept' else {},'scope':'turn'}
    if decision=='answer':
        return {'answers':{key:{'answers':[value]} for key,value in answers.items()}}
    available=request.get('params',{}).get('availableDecisions')
    if decision=='decline' and available and 'decline' not in available:
        decision='cancel'
    return {'decision':decision}
