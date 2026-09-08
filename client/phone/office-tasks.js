import {transcriptNavigation,rememberDetail,$,api,el,button,sheet,section,card,empty,field,select,notice,failure,link} from './office-ui.js';
import {attachments} from './office-attachments.js';
import {markdownView} from './office-markdown.js';
export async function taskList(parent,onlyActive=false,cursor=0){
 const data=await api(`/api/tasks?cursor=${cursor}${onlyActive?"&active=1":""}`);
 for(const item of data.items){if(onlyActive&&item.flight?.state!=='running'&&item.flight?.state!=='queued')continue;
 parent.append(card(item.title,`${item.specification.engine} · ${item.specification.profile} · ${item.phase}`,()=>taskDetail(item.id)));}
 if(data.next_cursor!==null)parent.append(button('Older conversations',()=>taskList(parent,onlyActive,data.next_cursor)));
 return data.items;
}
export async function newTask(project='',context=null){
 const body=sheet('Start something');
 body.append(el('p','muted','Runs on your Mac in a disposable clone.'));
 const loading=el('p','muted','Checking projects and accounts on your Mac…');body.append(loading);
 const data=await api('/api/tasks/capabilities');loading.remove();
 body.append(el('p','muted',data.workspace));
 const controls=composerControls(body,data,project);
 const draft=restoreComposer(controls,context,project);
 const attached=attachments(body,draft.uploads,items=>{draft.uploads=items;localStorage.setItem('office-new-task',JSON.stringify(composerPayload(controls,draft)));});
 const status=el('p','muted');body.append(status);
 const start=button('Start task',()=>{if(!attached.ready())throw Error('Wait for the attachment upload to finish.');return startTask(controls,draft);},'primary');
 const readiness=()=>{
  const ready=data.profiles.find(p=>p.engine===controls.engine.value&&p.id===controls.profile.value);
  const pending=Boolean(localStorage.getItem('office-new-task-pending'));
  start.disabled=!pending&&(!ready?.ready||controls.source_ref.disabled);start.textContent=pending?'Recover submitted task':'Start task';
  status.textContent=pending?'Retrying retrieves the exact submitted task; edits stay in your next draft.':controls.source_ref.disabled?(controls.source_ref.dataset.error||'Loading saved branches from your Mac…'):ready?.detail||'This account is unavailable.';
 };
 for(const control of Object.values(controls))control.addEventListener('input',()=>{
  localStorage.setItem('office-new-task',JSON.stringify(composerPayload(controls,draft)));readiness();
 });
 body.append(start);readiness();
 const refreshSource=()=>{controls.source_ref.disabled=true;readiness();return loadSources(controls,body).finally(()=>{readiness();retry.hidden=!controls.source_ref.dataset.error;});};
 const retry=button('Retry loading branches',refreshSource);retry.hidden=true;body.append(retry);controls.project.addEventListener('change',refreshSource);await refreshSource();
}
async function startTask(controls,draft){
 const key='office-new-task-pending';const current=composerPayload(controls,draft);
 let pending=JSON.parse(localStorage.getItem(key)||'null');
 if(!pending){
  if(!current.prompt.trim())throw Error('Describe the task first.');
  pending=current;localStorage.setItem(key,JSON.stringify(pending));
 }
 let result;
 try{result=await api('/api/tasks/start',pending);}
 catch(error){if([400,403,404,409,413,422].includes(error.status))localStorage.removeItem(key);throw error;}
 localStorage.removeItem(key);
 if(JSON.stringify(current)===JSON.stringify(pending))localStorage.removeItem('office-new-task');
 else{draft.request_id=crypto.randomUUID();localStorage.setItem('office-new-task',JSON.stringify(composerPayload(controls,draft)));}
 notice('Accepted by your Mac');await taskDetail(result.task_id);
}
function composerControls(body,data,project){
 const selected=data.projects.find(p=>p.id===project||p.name===project)||data.projects[0];
 return {
  prompt:field(body,'What would you like to do?',el('textarea')),
  project:field(body,'Project',select(data.projects.map(p=>[p.id,p.label||p.name]),selected?.id)),
  source_ref:field(body,'Starting branch',select([['HEAD','Current checkout']],'HEAD')),
  engine:field(body,'Agent',select([['codex','Codex'],['claude','Claude Code']],'codex')),
  profile:field(body,'Account',select([['personal','Personal'],['tbs','TBS']],'personal')),
 };
}
function restoreComposer(controls,context,project){
 const saved=JSON.parse(localStorage.getItem('office-new-task')||'{}');
 for(const [key,control] of Object.entries(controls))if(saved[key]&&!(key==='project'&&project))control.value=saved[key];
 controls.source_ref.dataset.savedValue=saved.source_ref||'HEAD';
 const reference=context?{id:context.id,revision:context.revision,...(context.start_line?{start_line:context.start_line,end_line:context.end_line}:{})}:saved.context||null;
 if(context)controls.prompt.value=`Regarding ${context.project}/${context.path} at revision ${context.revision}${context.start_line?` lines ${context.start_line}–${context.end_line}`:''}:\n\n${controls.prompt.value}`;
 return {request_id:saved.request_id||crypto.randomUUID(),context:reference,uploads:saved.uploads||[]};
}
function composerPayload(controls,draft){
 return {...draft,uploads:(draft.uploads||[]).map(({id,revision})=>({id,revision})),...Object.fromEntries(Object.entries(controls).map(([key,node])=>[key,node.value]))};
}

export async function taskDetail(id){
 rememberDetail('task',id);
 const data=await api(`/api/tasks/detail?id=${id}`);const body=sheet(data.task.title);body.dataset.taskId=id;const viewToken=crypto.randomUUID();body.dataset.taskView=viewToken;
 body.append(el('p','muted',`${data.specification.engine} · ${data.specification.profile} · ${data.specification.project.name}`));
 const state=el('p','pill',data.flights[0]?.state||data.task.state);body.append(state);
 const initial=el('details','card');initial.append(el('summary','','Original request'),el('p','',data.specification.prompt));showAttachments(initial,data.specification.attachments);body.append(initial);
 const turns=section(body,'Conversation');
 const live=el('div','reading');turns.append(live);
 const prompt=field(body,'Continue the conversation',el('textarea'));prompt.placeholder='A follow-up, correction, or next step…';
 prompt.value=localStorage.getItem('office-task-draft:'+id)||'';prompt.addEventListener('input',()=>localStorage.setItem('office-task-draft:'+id,prompt.value));
 const actions=el('div','actions');
 const uploadKey='office-task-uploads:'+id;
 const attached=attachments(body,JSON.parse(localStorage.getItem(uploadKey)||'[]'),items=>localStorage.setItem(uploadKey,JSON.stringify(items)));
 actions.append(replyButton(id,prompt,attached));
 const flightControls=el('div','actions');actions.append(flightControls);
 updateFlightControls(flightControls,id,data.flights[0]);
 body.append(actions);let cursor=-1;
 const earlier=earlierTaskMessages(body,turns,id);
 async function poll(){
  if(!$('#detail').open||body.dataset.taskId!==id||body.dataset.taskView!==viewToken)return;
  try{const events=await api(`/api/tasks/history?id=${id}&cursor=${cursor}`);if(cursor===-1){earlier.dataset.cursor=events.previous_cursor;earlier.hidden=events.previous_cursor===null;}for(const event of events.items){cursor=event.id;renderEvent(turns,live,state,event,id);}
   if(cursor===-1)cursor=0;
   const current=await api(`/api/tasks/detail?id=${id}`);
   if(body.dataset.taskView===viewToken)updateFlightControls(flightControls,id,current.flights[0]);}
  catch(error){state.textContent=error.message;}
  if($('#detail').open&&body.dataset.taskId===id&&body.dataset.taskView===viewToken)setTimeout(poll,1200);
 }
 earlier.classList.add("transcript-earlier");transcriptNavigation(body,turns).append(earlier);await poll();
}
function earlierTaskMessages(body,turns,id){
 const earlier=button('Load earlier messages',async()=>{
  const data=await api(`/api/tasks/history?id=${id}&cursor=${earlier.dataset.cursor}`);
  const group=el('div','stack'),live=el('div','reading'),state=el('p');group.append(live);
  for(const event of data.items)renderEvent(group,live,state,event,id);
  const height=body.scrollHeight;turns.prepend(...group.childNodes);body.scrollTop+=body.scrollHeight-height;
  earlier.dataset.cursor=data.previous_cursor;earlier.hidden=data.previous_cursor===null;
 });
 earlier.hidden=true;turns.parentElement.insertBefore(earlier,turns);return earlier;
}
function replyButton(id,prompt,attached){
 const key='office-task-pending:'+id;
 return button('Send',async()=>{
  let pending=JSON.parse(localStorage.getItem(key)||'null');
  if(!pending){
   if(!prompt.value.trim())throw Error('Write a message first.');
   if(!attached.ready())throw Error('Wait for the attachment upload to finish.');
   pending={task_id:id,request_id:crypto.randomUUID(),text:prompt.value,uploads:attached.references()};localStorage.setItem(key,JSON.stringify(pending));
  }
  try{await api('/api/tasks/say',pending);}
  catch(error){if([400,403,404,409,413,422].includes(error.status))localStorage.removeItem(key);throw error;}
  localStorage.removeItem(key);
  if(attached.ready()&&JSON.stringify(attached.references())===JSON.stringify(pending.uploads||[]))attached.clear();
  if(prompt.value===pending.text){prompt.value='';localStorage.removeItem('office-task-draft:'+id);}
  notice('Queued on your Mac. Delivery status appears in the conversation.');
 },'primary');
}
function updateFlightControls(parent,id,latest){
 const version=latest?`${latest.id}:${latest.state}`:'';
 if(parent.dataset.version===version)return;
 parent.dataset.version=version;parent.replaceChildren();
 if(!latest)return;
 parent.append(button('Outputs & changes',()=>document.dispatchEvent(new CustomEvent('office-flight-detail',{detail:latest.id}))));
 if(latest.state!=='running')return;
 for(const [action,label] of [['interrupt','Interrupt turn'],['close','Close session']])parent.append(button(label,async()=>{
  await api('/api/tasks/control',{task_id:id,flight_id:latest.id,request_id:crypto.randomUUID(),action});notice('Control queued for this attempt.');
 }));
}
function renderEvent(turns,live,state,event,taskId){
 const payload=event.payload;
 const renderers={
  'office.phase':()=>state.textContent=payload.state,
  'office.message':()=>{if(!payload.initial){const node=card('You',payload.text);showAttachments(node,payload.attachments);turns.insertBefore(node,live);}},
  'office.delivery':()=>turns.insertBefore(el('p','muted',`Message ${payload.message_id}: ${payload.state}${payload.consumption==='unknown'?' · provider acknowledged; consumption unconfirmed':''}`),live),
  'office.permission_closed':()=>{const node=turns.querySelector(`[data-permission-id="${payload.permission_id}"]`);if(node)node.replaceChildren(el('p','muted','Permission answered'));},
  'office.permission':()=>turns.insertBefore(permissionCard({id:event.id,task_id:taskId,payload}),live),
  'office.provider':()=>provider(turns,live,payload),
  'office.session_closed':()=>state.textContent='Session closed; output retained',
  'office.unsupported_request':()=>turns.insertBefore(card('Engine request needs support',JSON.stringify(payload.params)),live),
 };
 renderers[event.kind]?.();
}
function provider(turns,live,payload){
 const method=payload.method,params=payload.params||{};
 if(method==='item/agentMessage/delta'){live.textContent+=params.delta||'';return;}
 if(method==='claude/StreamEvent'){const delta=params.event?.delta;if(delta?.type==='text_delta')live.textContent+=delta.text;return;}
 if(method==='item/completed'){renderItem(turns,live,params.item||{});return;}
 if(method==='claude/AssistantMessage'){const text=(params.content||[]).filter(b=>b.text).map(b=>b.text).join('\n');if(text){live.textContent='';turns.insertBefore(markdownView(text),live);}}
}
function renderItem(turns,live,item){
 if(item.type==='agentMessage'){live.textContent='';turns.insertBefore(markdownView(item.text),live);return;}
 const details=el('details','card');details.append(el('summary','',item.type||'Engine event'),el('pre','',JSON.stringify(item,null,2)));turns.insertBefore(details,live);
}
export function permissionCard(request){
 const payload=request.payload;const params=payload.params||{};
 if(payload.method==='item/tool/requestUserInput'||params.tool==='AskUserQuestion')return inputRequest(request);
 const node=el('article','card');node.append(el('h3','',params.title||params.reason||'Permission requested'),el('pre','',JSON.stringify(params,null,2)));
 const controls=el('div','actions');const requestId=crypto.randomUUID();
 for(const [decision,label] of [['accept','Allow once'],['decline','Deny']])controls.append(button(label,async()=>{await api('/api/tasks/answer',{task_id:request.task_id,permission_id:request.id,decision,request_id:requestId});controls.replaceChildren(el('p','muted','Answer queued for this exact request.'));}));
 node.dataset.permissionId=String(request.id);node.append(controls);return node;
}
export async function permissions(parent){const data=await api('/api/tasks/permissions');for(const request of data.items)parent.append(permissionCard(request));return data.items.length;}

function inputRequest(request){
 const params=request.payload.params;const questions=params.questions||params.input.questions;
 const node=el('article','card');node.dataset.permissionId=String(request.id);const fields=new Map();
 for(const question of questions){const control=el('input');control.type=question.isSecret?'password':'text';const label=question.question;field(node,label,control);fields.set(question.id||label,control);
 if(question.options){const options=el('div','actions');for(const option of question.options)options.append(button(option.label,()=>control.value=option.label));node.append(options);}}
 const requestId=crypto.randomUUID();node.append(button('Send answers',async()=>{const answers=Object.fromEntries([...fields].map(([key,node])=>[key,node.value]));await api('/api/tasks/answer',{task_id:request.task_id,permission_id:request.id,decision:'answer',answers,request_id:requestId});node.replaceChildren(el('p','muted','Answers queued for this exact request.'));}));return node;
}

function showAttachments(parent,items=[]){
 for(const item of items){
  if(item.upload_id){parent.append(link(item.source,`/api/uploads/content?id=${encodeURIComponent(item.upload_id)}&revision=${item.revision}`));continue;}
  if(!item.object_id)continue;
  const context=el('details','card');context.append(el('summary','',item.source||'Attached context'),el('p','muted',`Attached snapshot · ${item.revision}`),el('pre','',item.text||''));
  context.append(button('Open current source',async()=>{const {openFile}=await import('./office-files.js');await openFile(item.object_id);}));parent.append(context);
 }
}

async function loadSources(controls,body){
 const project=controls.project.value;const picker=controls.source_ref;const saved=picker.dataset.savedValue||picker.value||'HEAD';picker.disabled=true;const token=crypto.randomUUID();picker.dataset.request=token;delete picker.dataset.error;
 try{
  const data=await api('/api/tasks/sources?project='+encodeURIComponent(project));
  if(!sourceCurrent(controls,project,token))return;
  const options=[['HEAD','Current checkout · '+data.revision.slice(0,12)],...data.items.map(item=>[item.id,item.label])];
  picker.replaceChildren(...select(options,'HEAD').children);picker.value=options.some(([id])=>id===saved)?saved:'HEAD';
  picker.disabled=false;delete picker.dataset.savedValue;picker.dispatchEvent(new Event('input',{bubbles:true}));
  let note=body.querySelector('[data-source-note]');if(!note){note=el('p','muted');note.dataset.sourceNote='true';picker.closest('label').after(note);}
  note.textContent=data.dirty?'This checkout has uncommitted changes. The task starts from the selected saved commit.':'The task starts from the selected saved commit in an isolated folder.';
 }catch(error){if(sourceCurrent(controls,project,token)){picker.dataset.error=error.message;notice(error.message);}}
}

function sourceCurrent(controls,project,token){return controls.project.value===project&&controls.source_ref.dataset.request===token&&controls.source_ref.isConnected;}
