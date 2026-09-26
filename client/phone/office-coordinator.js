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
    node.append(el('small','',`You · ${clock(item.at)} · ${item.acted_at?`acted ${clock(item.acted_at)}: ${item.action}`:item.read_at?'read by the coordinator '+clock(item.read_at):'queued for the coordinator'}`));
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
const SYSTEM_NAME={tbs:'Thinking Brain School',matra:'Matra',office:'Office'};
const SYSTEM_OUTCOME={tbs:'Keeping lessons healthy and ready for families.',matra:'Fixing app issues and delivering tested improvements.',office:'Moving Office issues, releases, failures and stabilization through Tower.'};
const needsAttention=row=>row.thrashing||['failing','stalled','error'].includes(row.health);
const systemName=row=>SYSTEM_NAME[row.id]||row.name;
const systemOutcome=row=>SYSTEM_OUTCOME[row.id]||'Moving its assigned work forward.';
export function healthLine(row){
 const node=el('span','coord-health');node.dataset.health=needsAttention(row)?'error':'ok';
  node.textContent=needsAttention(row)?'Needs attention':row.health==='idle'?'Idle':row.health==='running'?'Working':'Working normally';
 return node;
}
function detail(parent,row){
  parent.replaceChildren();if(!row)return;
  if(row.error){parent.append(el('p','error',row.error));return;}
  parent.append(coordinatorSummary(row));
 const evidence=el('details','coord-evidence');evidence.append(el('summary','','Coordinator notes and work'));parent.append(evidence);
 const doing=section(evidence,'Latest coordinator notes');doing.append(row.working_on?markdownView(row.working_on):el('p','empty','No output from the latest run yet.'));
 const lanes=section(evidence,'Open lanes');for(const lane of row.lanes||[])lanes.append(el('p','coord-tool',lane));if(!(row.lanes||[]).length)lanes.append(el('p','empty','No lanes named in the latest run.'));
 const shipped=section(evidence,'Recent changes');
 for(const c of row.commits||[]){const node=el('p','coord-change');node.append(el('span','coord-tag',c.checkout));segments(node,[{kind:'sha',sha:c.sha,checkout:c.checkout,text:c.sha.slice(0,8)},{text:` ${c.subject} · ${clock(c.at)}`}]);shipped.append(node);}
 if(!(row.commits||[]).length)shipped.append(el('p','empty','No recent changes recorded.'));
}
function coordinatorSummary(row){
  const summary=el('div','coord-outcome');
  const note=row.id==='office'?`Last run ${row.age_s==null?'never':Math.max(0,Math.floor(row.age_s/60))+' min ago'} · ${row.failures||0} recent failures · ${row.changes||0} recent changes · ${row.unread||0} queued messages`:needsAttention(row)?'I’m checking what needs attention.':'Nothing needed from you.';
  summary.append(el('h1','coord-system-name',systemName(row)),el('p','coord-outcome-title',systemOutcome(row)),healthLine(row),el('p','muted',note));
  if(row.id==='office'){const inspect=el('a','','Inspect Office');inspect.href='#system';summary.append(inspect);}
  return summary;
}
export async function coordinator(parent){
 try{pick=localStorage.getItem(PICK)||pick;}catch{}
 const facts=el('div','coord-facts');
 const activity=el('details','coord-activity');activity.append(el('summary','','See activity'));
 const head=el('div','coord-head');const status=el('p','muted','Checking status…');
 const tabs=el('div','coord-switch');let view='chat';let rows=[];
 const name=el('div','eyebrow','System activity');
 async function drawMaster(){
  const data=await api('/api/coordinators');
  rows=data.coordinators||[];const row=rows.find(r=>r.id===(pick||rows[0]?.id))||rows[0];
  if(row&&!pick){pick=row.id;try{localStorage.setItem(PICK,pick);}catch{}}
  name.textContent=row?systemName(row):'System activity';status.textContent=row?(needsAttention(row)?'Needs attention':'Working normally'):'Status unavailable';
  const evidenceOpen=!!facts.querySelector('.coord-evidence')?.open;detail(facts,row);
  if(evidenceOpen){const evidence=facts.querySelector('.coord-evidence');if(evidence)evidence.open=true;}
 }
 await drawMaster().catch(error=>failure(facts,error));
 parent.append(facts);
 head.append(name,status,tabs);activity.append(head);
 const stream=el('div','coord-stream');stream.setAttribute('aria-live','polite');parent.append(stream);
 activity.append(stream);parent.append(activity);
 const message=el('details','coord-message');message.append(el('summary','','Message this system'));
 const form=el('form','coord-compose');const input=el('textarea');input.rows=2;input.placeholder=`Message ${systemName(rows.find(r=>r.id===(pick||rows[0]?.id))||{id:pick,name:'the system'})}`;input.setAttribute('aria-label','Message the coordinator');
 const send=el('button','primary','Send');send.type='submit';form.append(input,send);message.append(form);parent.insertBefore(message,activity);
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
   if(shown!==seen){seen=shown;draw();}  // an unchanged poll keeps selection and open panels
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
