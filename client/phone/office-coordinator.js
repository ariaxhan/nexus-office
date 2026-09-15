// The one conversation with the TBS coordinator. Everything shown is read from disk on the Mac;
// a message is kept on this device until the Mac confirms it, and the Mac drops a repeated id.
import {api,el,button,sheet,link,notice,restoreDraft,failure} from './office-ui.js';
import {openFile} from './office-files.js';
const PENDING='office-coordinator-pending';
const clock=at=>at?new Date(at).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';
function segments(parent,list){
 for(const part of list||[]){
  if(!part.kind){parent.append(document.createTextNode(part.text));continue;}
  if(part.kind==='url'){parent.append(link(part.text,part.href));continue;}
  const open={issue:()=>document.dispatchEvent(new CustomEvent('office-github-detail',{detail:{repo:part.repo,number:part.number}})),
   file:()=>openFile(part.id),sha:()=>commit(part.sha,part.checkout)}[part.kind];
  const node=el('button','coord-link',part.text);node.type='button';node.dataset.kind=part.kind;
  node.title={issue:`${part.repo} #${part.number}`,file:part.path,sha:`Commit in ${part.checkout}`}[part.kind];
  node.addEventListener('click',()=>Promise.resolve(open()).catch(error=>notice(error.message)));parent.append(node);
 }
 return parent;
}
export async function commit(sha,checkout){
 const body=sheet(`Commit ${sha.slice(0,10)}`);
 try{
  const data=await api(`/api/coordinator/commit?sha=${encodeURIComponent(sha)}&checkout=${encodeURIComponent(checkout)}`);
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
   const node=el('article','coord-you');segments(node.appendChild(el('div','coord-text')),item.segments);
   node.append(el('small','',`You · ${clock(item.at)} · ${item.read_at?'read by the coordinator '+clock(item.read_at):'waiting for the coordinator'}`));
   parent.append(node);continue;
  }
  parent.append(runHead(item));
  if(item.truncated)parent.append(el('p','coord-status','Earlier output from this run is in '+item.log));
  if(!item.events.length)parent.append(el('p','coord-status',item.live?'Starting…':'This run wrote no output.'));
  for(const event of item.events){
   if(event.kind==='tool'){parent.append(el('p','coord-tool',event.text));continue;}
   const node=el('article',event.kind==='result'?'coord-say coord-result':'coord-say');segments(node,event.segments);parent.append(node);
  }
  for(const hold of item.holds){const node=el('article','coord-say coord-hold');node.append(el('strong','','Hold · '));segments(node,hold.segments);parent.append(node);}
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
export async function coordinator(parent){
 const head=el('div','coord-head');const status=el('p','muted','Reading the coordinator…');
 const tabs=el('div','coord-switch');let view='chat';
 head.append(el('div','eyebrow','Coordinator'),status,tabs);parent.append(head);
 const stream=el('div','coord-stream');stream.setAttribute('aria-live','polite');parent.append(stream);
 const form=el('form','coord-compose');const input=el('textarea');input.rows=2;input.placeholder='Steer the coordinator';input.setAttribute('aria-label','Message the coordinator');
 const send=el('button','primary','Send');send.type='submit';form.append(input,send);parent.append(form);
 const clearDraft=restoreDraft(input,['coordinator']);
 let signature='',data=null;
 for(const [key,label] of [['chat','Conversation'],['changes','Changes']]){const b=button(label,()=>{view=key;signature='';stream.dataset.drawn=key==='chat'?'':'1';draw();if(key!=='chat')scrollTo(0,0);});b.dataset.view=key;tabs.append(b);}
 function draw(){
  for(const b of tabs.children)b.setAttribute('aria-pressed',String(b.dataset.view===view));
  if(!data)return;const next=view+JSON.stringify(data.items);if(next===signature)return;signature=next;
  const follow=view==='chat'&&(stream.dataset.drawn!=='1'||innerHeight+scrollY>=document.body.scrollHeight-160);
  stream.replaceChildren();(view==='chat'?conversation:changes)(stream,data);stream.dataset.drawn='1';
  if(follow)requestAnimationFrame(()=>scrollTo(0,document.body.scrollHeight));
 }
 async function refresh(){
  try{
   data=await api('/api/coordinator');const live=data.items.some(item=>item.live);
   const skip=data.last_skip?` · last check ${clock(data.last_skip.at)}: ${data.last_skip.reason}`:'';
   status.textContent=`${live?'Running now':'Idle'}${data.unread?` · ${data.unread} message${data.unread>1?'s':''} waiting`:''}${live?'':skip}`;draw();
  }catch(error){status.textContent='Mac unreachable; showing the last view. '+error.message;}
 }
 async function deliver(){
  const pending=JSON.parse(localStorage.getItem(PENDING)||'null');if(!pending)return;
  try{await api('/api/coordinator/say',pending);}catch(error){if(error.status>=400&&error.status<500)localStorage.removeItem(PENDING);throw error;}
  localStorage.removeItem(PENDING);clearDraft(pending.text);stream.dataset.drawn='';await refresh();
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(!input.value.trim())return;
  if(!localStorage.getItem(PENDING))localStorage.setItem(PENDING,JSON.stringify({id:crypto.randomUUID(),text:input.value}));
  send.disabled=true;try{await deliver();notice('Sent. The coordinator reads it first on its next check.');}catch(error){notice('Not confirmed yet; Send retries the same message. '+error.message);}finally{send.disabled=false;}
 });
 input.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey))form.requestSubmit();});
 await deliver().catch(()=>{});await refresh();
 const timer=setInterval(()=>{if(!parent.isConnected)clearInterval(timer);else refresh();},3000);
}
