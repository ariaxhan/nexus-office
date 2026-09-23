// The one conversation with the TBS coordinator. Everything shown is read from disk on the Mac;
// a message is kept on this device until the Mac confirms it, and the Mac drops a repeated id.
import {api,el,button,sheet,link,notice,restoreDraft,failure,section} from './office-ui.js';
import {openFile} from './office-files.js';
import {markdownView,tokenText} from './office-markdown.js';
const PICK='office-coordinator-pick';
let pick=(()=>{try{return localStorage.getItem(PICK)||'';}catch{return '';}})();
const pendingKey=()=>'office-coordinator-pending'+(pick&&pick!=='tbs'?'-'+pick:'');
const q=()=>pick?`id=${encodeURIComponent(pick)}`:'';
const clock=at=>at?new Date(at).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';
function token(part){
 if(part.kind==='url')return link(part.text,part.href);
 const open={issue:()=>document.dispatchEvent(new CustomEvent('office-github-detail',{detail:{repo:part.repo,number:part.number}})),
  file:()=>openFile(part.id),sha:()=>commit(part.sha,part.checkout)}[part.kind];
 const node=el('button','coord-link',part.text);node.type='button';node.dataset.kind=part.kind;
 node.title={issue:`${part.repo} #${part.number}`,file:part.path,sha:`Commit in ${part.checkout}`}[part.kind];
 node.addEventListener('click',()=>Promise.resolve(open()).catch(error=>notice(error.message)));return node;
}
function segments(parent,list){
 for(const part of list||[])parent.append(part.kind?token(part):document.createTextNode(part.text));
 return parent;
}
// Markdown from the whole text; the Mac's link tokens are re-applied inside plain text, never in code.
function rich(parent,list){
 const text=(list||[]).map(part=>part.text).join('');
 parent.append(markdownView(text,{text:tokenText((list||[]).filter(part=>part.kind),token)}));return parent;
}
export async function commit(sha,checkout){
 const body=sheet(`Commit ${sha.slice(0,10)}`);
 try{
  const data=await api(`/api/coordinator/commit?sha=${encodeURIComponent(sha)}&checkout=${encodeURIComponent(checkout)}&${q()}`);
  body.append(el('p','muted',`${data.repo||data.checkout} · ${data.sha}`));
  if(data.repo)body.append(link('Open on GitHub',`https://github.com/${data.repo}/commit/${data.sha}`));
  const files=el('div','stack');body.append(files);
  for(const file of data.files){const row=el('button','coord-link coord-file',file.path);row.type='button';row.disabled=!file.id;if(file.id)row.addEventListener('click',()=>openFile(file.id).catch(error=>notice(error.message)));files.append(row);}
  body.append(el('pre','',data.text));
 }catch(error){failure(body,error);}
}
function runHead(item){
 const head=el('div','coord-run');const mode=item.mode==='plan'?'plan run':'run';
 head.append(el('span','',`${item.live?'Live ':''}${mode} · ${clock(item.at)}`));
 if(item.live)head.classList.add('live');return head;
}
function runEnd(item){
 const end=item.end;if(!end)return item.live?el('p','coord-status','Working. New output appears here as it is written.'):el('p','coord-status','No end recorded: the run stopped without finishing its ledger row.');
 const minutes=Math.round((end.secs||0)/60);
 return el('p','coord-status',`Ended ${clock(end.at)} · ${minutes} min · exit ${end.rc}${end.timed_out?' (timed out)':''} · ${end.prod_changes||0} production changes · ${end.holds||0} holds`);
}
function conversation(parent,data){
 for(const item of data.items){
  if(item.kind==='message'){
   const node=el('article','coord-you');rich(node.appendChild(el('div','coord-text')),item.segments);
   node.append(el('small','',`You · ${clock(item.at)} · ${item.read_at?'read by the coordinator '+clock(item.read_at):'waiting for the coordinator'}`));
   parent.append(node);continue;
  }
  parent.append(runHead(item));
  if(item.truncated)parent.append(el('p','coord-status','Earlier output from this run is in '+item.log));
  if(!item.events.length)parent.append(el('p','coord-status',item.live?'Starting…':'This run wrote no output.'));
  for(const event of item.events){
   if(event.kind==='tool'){const row=el('p','coord-tool');row.append(document.createTextNode(event.text),el('small','coord-time',clock(event.at)));parent.append(row);continue;}
   const node=el('article',event.kind==='result'?'coord-say coord-result':'coord-say');rich(node,event.segments);node.append(el('small','coord-time',clock(event.at)));parent.append(node);
  }
  for(const hold of item.holds){const node=el('article','coord-say coord-hold');node.append(el('strong','','Hold · '));rich(node,hold.segments);parent.append(node);}
  parent.append(runEnd(item));
 }
 if(!data.items.length)parent.append(el('p','empty','No coordinator runs or messages yet.'));
}
function changes(parent,data){
 let any=false;
 for(const item of [...data.items].reverse()){
  if(item.kind!=='run')continue;const found=item.changes;
  if(!found.commits.length&&!found.publishes.length&&!found.issues.length)continue;any=true;
  const group=el('section','section');group.append(el('h2','section-head',`Run · ${clock(item.at)}`));parent.append(group);
  for(const row of found.publishes){const node=el('p','coord-change');node.append(el('span','coord-tag',row.outcome==='PASS'?'Live':'Publish '+(row.outcome||'')));segments(node,[row.id?{kind:'file',id:row.id,path:row.path,text:row.path}:{text:row.path}]);group.append(node);}
  for(const row of found.commits){const node=el('p','coord-change');node.append(el('span','coord-tag',row.checkout));segments(node,[{kind:'sha',sha:row.sha,checkout:row.checkout,text:row.sha.slice(0,8)},{text:' '},...(row.segments||[{text:row.subject}])]);group.append(node);}
  for(const row of found.issues){const node=el('p','coord-change');node.append(el('span','coord-tag','Issue '+row.action));segments(node,row.number?[{kind:'issue',repo:row.repo,number:row.number,text:`${row.repo}#${row.number}`}]:[{text:row.repo}]);group.append(node);}
 }
 if(!any)parent.append(el('p','empty','No landed commits, publishes or issue changes in the recorded runs.'));
}
const age=s=>s==null?'never':s<90?'just now':s<5400?`${Math.round(s/60)} min ago`:s<172800?`${Math.round(s/3600)} h ago`:`${Math.round(s/86400)} d ago`;
const HEALTH={running:'Running',ok:'Healthy',thrashing:'Thrashing',failing:'Last run failed',stalled:'Stalled',error:'Unreadable'};
// One line per coordinator: is it alive, did the last run work, is it shipping anything.
export function healthLine(row){
 const end=row.last_end||{};const node=el('span','coord-health');node.dataset.health=row.health;
 const shipped=(row.shipped_recent||[]).join(' · ');
 node.textContent=[HEALTH[row.health]||row.health,row.live?'run live now':`last run ${age(row.age_s)}`,end.rc!=null?`exit ${end.rc}${end.timed_out?' (timed out)':''}`:'',shipped?`shipped per run: ${shipped}`:'',row.unread?`${row.unread} unread`:''].filter(Boolean).join(' · ');
 return node;
}
export async function overview(parent,onPick){
 const data=await api('/api/coordinators');
 for(const row of data.coordinators){
  const node=button('',()=>onPick(row.id),'card coord-row');if(row.id===(pick||data.coordinators[0].id))node.setAttribute('aria-current','true');
  node.append(el('h3','',row.name),healthLine(row));if(row.working_on)node.append(el('p','muted coord-doing',row.working_on.split('\n')[0].slice(0,220)));
  parent.append(node);
 }
 return data;
}
function detail(parent,row){
 parent.replaceChildren();if(!row)return;
 if(row.error){parent.append(el('p','error',row.error));return;}
 const doing=section(parent,'Working on');doing.append(row.working_on?markdownView(row.working_on):el('p','empty','No output from the latest run yet.'));
 const lanes=section(parent,'Open lanes');for(const lane of row.lanes||[])lanes.append(el('p','coord-tool',lane));if(!(row.lanes||[]).length)lanes.append(el('p','empty','No lanes named in the latest run.'));
 const shipped=section(parent,'Last shipped');
 for(const c of row.commits||[]){const node=el('p','coord-change');node.append(el('span','coord-tag',c.checkout));segments(node,[{kind:'sha',sha:c.sha,checkout:c.checkout,text:c.sha.slice(0,8)},{text:` ${c.subject} · ${clock(c.at)}`}]);shipped.append(node);}
 if(!(row.commits||[]).length)shipped.append(el('p','empty','Nothing shipped in the recent runs.'));
}
export async function coordinator(parent){
 try{pick=localStorage.getItem(PICK)||pick;}catch{}
 const master=section(parent,'Coordinators');const facts=el('div','coord-facts');
 const head=el('div','coord-head');const status=el('p','muted','Reading the coordinator…');
 const tabs=el('div','coord-switch');let view='chat';let rows=[];
 const name=el('div','eyebrow','Coordinator');
 async function drawMaster(){
  const box=el('div','stack');const data=await overview(box,id=>{pick=id;try{localStorage.setItem(PICK,id);}catch{}parent.replaceChildren();coordinator(parent);});
  rows=data.coordinators;master.replaceChildren(...box.children);const row=rows.find(r=>r.id===(pick||rows[0].id));
  name.textContent=`Coordinator · ${row?.name||''}`;detail(facts,row);
 }
 await drawMaster().catch(error=>failure(master,error));
 head.append(name,status,tabs);parent.append(facts,head);
 const stream=el('div','coord-stream');stream.setAttribute('aria-live','polite');parent.append(stream);
 const form=el('form','coord-compose');const input=el('textarea');input.rows=2;input.placeholder=`Steer ${rows.find(r=>r.id===(pick||rows[0]?.id))?.name||'the coordinator'}`;input.setAttribute('aria-label','Message the coordinator');
 const send=el('button','primary','Send');send.type='submit';form.append(input,send);parent.append(form);
 const clearDraft=restoreDraft(input,pick&&pick!=='tbs'?['coordinator',pick]:['coordinator']);
 let signature='',data=null;
 for(const [key,label] of [['chat','Conversation'],['changes','Changes']]){const b=button(label,()=>{view=key;signature='';stream.dataset.drawn=key==='chat'?'':'1';draw();if(key!=='chat')scrollTo(0,0);});b.dataset.view=key;tabs.append(b);}
 function draw(){
  for(const b of tabs.children)b.setAttribute('aria-pressed',String(b.dataset.view===view));
  if(!data)return;const next=view+JSON.stringify(data.items);if(next===signature)return;signature=next;
  const follow=view==='chat'&&(stream.dataset.drawn!=='1'||innerHeight+scrollY>=document.body.scrollHeight-160);
  stream.replaceChildren();(view==='chat'?conversation:changes)(stream,data);stream.dataset.drawn='1';
  if(follow)requestAnimationFrame(()=>scrollTo(0,document.body.scrollHeight));
 }
 let busy=false,idle=false,seen='';
 async function refresh(){
  if(busy)return;busy=true;
  try{
   data=await api('/api/coordinator?'+q());idle=!data.items.some(item=>item.live);const shown=JSON.stringify({...data,as_of:''});const live=data.items.some(item=>item.live);
   const skip=data.last_skip?` · last check ${clock(data.last_skip.at)}: ${data.last_skip.reason}`:'';
   status.textContent=`${live?'Running now':'Idle'}${data.unread?` · ${data.unread} message${data.unread>1?'s':''} waiting`:''}${live?'':skip}`;if(shown!==seen){seen=shown;draw();}  // an unchanged poll keeps selection and open panels
  }catch(error){status.textContent='Mac unreachable; showing the last view. '+error.message;}
  finally{busy=false;}
 }
 async function deliver(){
  const pending=JSON.parse(localStorage.getItem(pendingKey())||'null');if(!pending)return;
  try{await api('/api/coordinator/say',pending);}catch(error){if(error.status>=400&&error.status<500)localStorage.removeItem(pendingKey());throw error;}
  localStorage.removeItem(pendingKey());clearDraft(pending.text);stream.dataset.drawn='';await refresh();
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(!input.value.trim())return;
  if(!localStorage.getItem(pendingKey()))localStorage.setItem(pendingKey(),JSON.stringify({id:crypto.randomUUID(),text:input.value,coordinator:pick||undefined}));
  send.disabled=true;try{await deliver();}catch(error){notice('Not confirmed yet; Send retries the same message. '+error.message);}finally{send.disabled=false;}
 });

 input.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey))form.requestSubmit();});
 await deliver().catch(()=>{});await refresh();
 let ticks=0,masterTicks=0;const timer=setInterval(()=>{if(!parent.isConnected)clearInterval(timer);else{if(!idle||++ticks%5===0)refresh();if(++masterTicks%5===0)drawMaster().catch(()=>{});}},3000);  // 3s while a run is live, 15s idle
}
