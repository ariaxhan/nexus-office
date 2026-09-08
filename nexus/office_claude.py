"""Claude's supported SDK transport with a phone permission callback.

The SDK stays inside the selected-account flight process. It neither schedules
work nor owns the Nexus task lifecycle.
"""
import asyncio
from dataclasses import asdict,is_dataclass
import queue
import shutil
import threading
import uuid


class Claude:
    def __init__(self,env,cwd,stderr,resume=None):
        self.events=queue.Queue();self.commands=queue.Queue();self.answers={}
        self.session_id=resume or str(uuid.uuid4());self.turn_id=None
        self.ready=threading.Event();self.failure=None
        self.cwd=cwd;self.env=env;self.stderr=stderr;self.resume=resume
        self.thread=threading.Thread(target=self.run,daemon=True,name='office-claude-sdk')
        self.thread.start()
        if not self.ready.wait(60):raise TimeoutError('Claude did not connect')
        if self.failure:raise RuntimeError(self.failure)

    def run(self):
        try:asyncio.run(self.connected())
        except Exception as exc:
            self.failure=f'{type(exc).__name__}: {exc}'
            self.events.put({'method':'office/disconnected','params':{'error':self.failure}})
            self.ready.set()

    async def connected(self):
        from claude_agent_sdk import ClaudeSDKClient,ClaudeAgentOptions
        options=ClaudeAgentOptions(cwd=self.cwd,cli_path=shutil.which('claude'),env=self.env,
            resume=self.resume,session_id=None if self.resume else self.session_id,
            permission_mode='acceptEdits',can_use_tool=self.permission,
            system_prompt={'type':'preset','preset':'claude_code'},setting_sources=['user','project','local'],
            include_partial_messages=True,stderr=self.write_stderr,
            extra_args={'replay-user-messages':None})
        async with ClaudeSDKClient(options) as client:
            self.client=client;self.ready.set()
            reader=asyncio.create_task(self.receive())
            try:
                while True:
                    if reader.done():
                        failure=reader.exception()
                        if failure:raise failure
                        raise RuntimeError('Claude event stream ended')
                    try:command=self.commands.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(.1);continue
                    if command[0]=='close':break
                    await self.dispatch(command)
            finally:
                reader.cancel()
                try:await reader
                except asyncio.CancelledError:pass

    def write_stderr(self,text):
        self.stderr.write(text+'\n');self.stderr.flush()

    async def dispatch(self,command):
        kind,value,channel=command
        try:
            if kind=='message':
                await self.client.query(value)
                self.turn_id='active'
                result={'submitted':True}
            else:
                await self.client.interrupt();result={'interrupt_requested':True}
            channel.put(result)
        except Exception as exc:channel.put(exc)

    async def receive(self):
        async for message in self.client.receive_messages():
            kind=type(message).__name__
            data=asdict(message) if is_dataclass(message) else {'text':str(message)}
            if kind=='ResultMessage':self.turn_id=None
            self.events.put({'method':'claude/'+kind,'params':data})

    async def permission(self,name,inputs,context):
        from claude_agent_sdk import PermissionResultAllow,PermissionResultDeny
        identifier=context.tool_use_id
        self.events.put({'id':identifier,'method':'claude/requestApproval','params':{'tool':name,'input':inputs,'title':context.title,'reason':context.decision_reason}})
        while identifier not in self.answers:await asyncio.sleep(.1)
        decision,answers=self.answers.pop(identifier)
        if decision=='answer':return PermissionResultAllow(updated_input={**inputs,'answers':answers})
        if decision=='accept':return PermissionResultAllow(updated_input=inputs)
        return PermissionResultDeny(message='Declined from Nexus Office',interrupt=decision=='cancel')

    def command(self,kind,value=None):
        channel=queue.Queue(maxsize=1);self.commands.put((kind,value,channel))
        try:result=channel.get(timeout=60)
        except queue.Empty:raise TimeoutError(f'Claude did not acknowledge {kind}') from None
        if isinstance(result,Exception):raise result
        return result

    def message(self,text):return self.command('message',text)
    def interrupt(self):return self.command('interrupt')
    def answer(self,identifier,decision,answers=None):self.answers[identifier]=(decision,answers)

    def event(self,timeout=.2):
        try:return self.events.get(timeout=timeout)
        except queue.Empty:return None

    def close(self):
        self.commands.put(('close',None,None));self.thread.join(timeout=15)
