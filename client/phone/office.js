import * as userState from './office-state.js';
import {attachments} from './office-attachments.js';
import {restoreDraft,transcriptNavigation,rememberDetail,clearDetail,backDetail,$,api,el,button,sheet,section,card,intro,empty,failure,field,select,notice,link} from './office-ui.js';
import {loadSettings,settings} from './office-settings.js';
import {mediaList,mediaDetail} from './office-media.js';
import {browse,openFile,numberedSource} from './office-files.js';
import {markdownView} from './office-markdown.js';
import {coordinator,healthLine,commit as openCoordinatorCommit} from './office-coordinator.js';
import {issueInventory,issueControls} from './office-issues.js';
import {newTask,taskList,permissions,taskDetail,permissionCard} from './office-tasks.js';
let attention={items:[],errors:[],failures:[]};
let snapshot=null,snapshotReadAt=0;
async function world(){if(!snapshot||Date.now()-snapshotReadAt>15000){const data=await api('/api/world');snapshot=data.world;snapshotReadAt=Date.now();}return snapshot;}
async function guarded(parent,work){try{await work();}catch(error){failure(parent,error);}}
function subtitle(data){return [data.state,data.detail,data.at].filter(Boolean).join(' · ');}
async function today(parent){
 intro(parent,'Your office, wherever you are','A little room to think.','The Mac holds the work. Everything you need to see and steer lives here.');
 const reports=section(parent,'Daily reports');await guarded(reports,()=>dailyReports(reports));
 const coords=section(parent,'Coordinators');await guarded(coords,async()=>{const data=await api('/api/coordinators');for(const row of data.coordinators){const node=button('',()=>{location.hash='coordinator';},'card coord-row');node.dataset.id=row.id;node.addEventListener('click',()=>{try{localStorage.setItem('office-coordinator-pick',row.id);}catch{}},{capture:true});node.append(el('h3','',row.name),healthLine(row));coords.append(node);}});
 const needs=section(parent,'Needs you');needs.id='needs-list';await guarded(needs,()=>attentionList(needs));
 const active=section(parent,'Running now');await runningNow(active);
 const recent=section(parent,'Since you were here');await guarded(recent,()=>podcastNotifications(recent));await guarded(recent,()=>activitySinceVisit(recent));
 const listen=section(parent,'Something to listen to');await guarded(listen,async()=>{const data=await api('/api/media?kind=podcast');const episode=data.items[0];if(episode)listen.append(card(episode.title,`${Math.round(episode.duration_s/60)} minutes · ${episode.date}`,()=>mediaDetail(episode.id)));else empty(listen,'Your next episode will appear here when published.');});
 const poem=section(parent,'A small interruption');await guarded(poem,async()=>{const data=await api('/api/media?kind=substrate');const item=data.items[0];if(item)poem.append(card(item.title,item.excerpt,()=>mediaDetail(item.id)));else empty(poem,'No Substrate pieces published yet.');});
}
// The five daily reports, newest reply each, readable without opening a chat.
// The first real sentence, past the bot's own title line and markdown marks.
function reportLead(text){const lines=text.split('\n').map(line=>line.replace(/[*_#>|`]/g,'').trim()).filter(Boolean);return lines.find(line=>line.length>40&&line!==line.toUpperCase())||lines[0]||'';}
async function dailyReports(parent){
 const data=await api('/api/reports');
 for(const row of data.reports){
  const node=el('details','card report');const summary=el('summary');
  const when=row.at?new Date(row.at).toLocaleString([], {weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'no report yet';
  summary.append(el('strong','',row.name),el('span','muted',` · ${when}${row.stale?' · showing the last one that loaded':''}${row.ok===false?' · failed':''}`),el('p','report-lead',reportLead(row.text||row.error||'')));
  node.append(summary);if(row.text)node.append(markdownView(row.text));
  parent.append(node);
 }
}
async function work(parent){
 intro(parent,'Work','Pick up the thread.','Projects, conversations, and the files behind them.');
 const active=section(parent,'Running now');await runningNow(active);const history=section(parent,'Office conversations');await guarded(history,()=>taskList(history));
 const bots=section(parent,'Your office voices');bots.append(button('Open office voices',()=>guarded(bots,async()=>{const data=await api('/api/bots');bots.replaceChildren();for(const bot of data.bots)bots.append(card(bot.name,bot.purpose,()=>botConversation(bot)));})));
 const archive=section(parent,'All retained conversations');archive.append(button('Browse all retained conversations',()=>archives(archive)));
 const projects=section(parent,'Projects');parent.insertBefore(projects.parentElement,active.parentElement);
 await guarded(projects,()=>projectRoster(projects));
}
async function projectRoster(parent){
 const loading=el('p','muted','Loading projects from your Mac…');parent.append(loading);
 const [data,local]=await Promise.all([world(),api('/api/projects')]);
 loading.remove();const desks=projectDesks(data,local.items);
 const filter=el('input');filter.type='search';filter.placeholder='Filter projects';filter.setAttribute('aria-label','Filter projects');parent.append(filter);
 const rows=el('div','grid');parent.append(rows);let limit=8;
 function draw(){
  rows.replaceChildren();const matches=desks.filter(desk=>(desk.repo+' '+(desk.label||'')).toLowerCase().includes(filter.value.toLowerCase()));
  for(const desk of matches.slice(0,limit))rows.append(card(desk.repo,desk.label||desk.detail||desk.outcome,()=>project(desk)));
  if(matches.length>limit)rows.append(button(`Show all ${matches.length} projects`,()=>{limit=matches.length;draw();}));
 }
 filter.addEventListener('input',draw);draw();
 const inactive=(data.stations||[]).filter(desk=>desk.hidden);
 if(inactive.length){
  const away=el('details','find-group');away.append(el('summary','',`Inactive desks (${inactive.length})`));
  for(const desk of inactive)away.append(button(`Bring back ${desk.repo}`,()=>setDeskHidden(desk.repo,false)));
  parent.append(away);
 }
}
async function setDeskHidden(repo,hidden){
 const result=await api('/api/desks',{repo,hidden});if(!result.ok)throw Error('Desk change was not saved');
 snapshot=null;snapshotReadAt=0;
 if($('#detail').open)$('#detail').close();
 notice(hidden?`${repo} put away`:`${repo} is active again`);await route();
}
function projectDesks(data,local){
 const stations=data.stations||[];
 const hidden=new Set(stations.filter(desk=>desk.hidden).map(desk=>desk.repo.toLowerCase()));
 const rows=local.filter(root=>!hidden.has(root.name.toLowerCase())).map(root=>({...stations.find(desk=>desk.repo===root.name),repo:root.name,root:root.id,folder:root.folder_id||btoa(JSON.stringify([root.id,''])).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,''),checkoutPath:root.path,taskCapable:root.task_capable!==false,label:root.label}));
 for(const desk of stations)if(!desk.hidden&&!local.some(root=>root.name===desk.repo))rows.push(desk);
 return rows;
}
async function project(desk){
 rememberDetail('project',JSON.stringify({repo:desk.repo,root:desk.root}));
 const body=sheet(desk.repo);body.append(el('p','muted',desk.label||desk.detail||''));
 if(desk.hidden===false)body.append(button('Put away from active desks',()=>setDeskHidden(desk.repo,true)));
 if(desk.root&&desk.taskCapable)body.append(button('New task in this checkout',()=>newTask(desk.root),'primary'));
 if(desk.root&&!desk.taskCapable)body.append(el('p','muted','This is a file collection. New tasks require a Git checkout so their work can run in an isolated clone.'));
 const nav=el('div','actions');const view=el('div');body.append(nav,view);
 if(desk.root)nav.append(button('Files',()=>projectPanel(view,pane=>browse(pane,desk.folder))),button('Conversations',()=>projectPanel(view,pane=>projectTasks(pane,desk))),button('Agents',()=>projectPanel(view,pane=>projectAgents(pane,desk))),button('Outputs',()=>projectPanel(view,pane=>projectTasks(pane,desk,true))));
 if(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(desk.repo)){
  nav.append(button('GitHub files',()=>githubTree(desk.repo)),button('Issues & pull requests',()=>projectPanel(view,pane=>projectWork(pane,desk))));projectWork(view,desk);
 }else if(desk.root)await projectPanel(view,pane=>browse(pane,desk.folder));
}

function projectWork(parent,desk){
 parent.replaceChildren();parent.append(button('New issue',()=>createIssue(desk.repo)));for(const [key,title] of [['issues','Issues'],['prs','Pull requests']]){const list=section(parent,title);for(const item of desk[key]||[])list.append(card(`#${item.number} ${item.title}`,item.state||'',()=>githubDetail(desk.repo,item,key)));if(!desk[key]?.length)empty(list,'None in the current snapshot.');list.append(button('Browse all open and closed',()=>githubCollection(desk.repo,key)));}
}
async function githubDetail(repo,item,kind){
 rememberDetail('github',JSON.stringify({repo,number:item.number,kind}));
 const body=sheet(`${repo} #${item.number}`);
 await guarded(body,async()=>{const query=`repo=${encodeURIComponent(repo)}&number=${item.number}&kind=${kind}`;const data=await api(`/api/github/detail?${query}`);if(kind!=='prs'&&data.pull_request)return githubDetail(repo,item,'prs');
 body.append(el('h2','',data.title),el('p','muted',`${data.state} · ${data.acting_identity} · observed ${new Date(data.observed_at*1000).toLocaleString()}`),markdownView(data.body||''),link('Open on GitHub',data.html_url));
 if(kind==='prs'){body.append(el('p','muted',`Head ${data.head.sha}`));const checks=section(body,'Checks');await githubChecks(checks,repo,data.head.sha);
 const changes=section(body,'Changed files');githubFiles(changes,repo,item.number,data.files,data.head.sha);if(data.files_next_cursor)changes.append(button('More changed files',()=>githubFilePage(changes,repo,item.number,data.files_next_cursor,data.head.sha)));
 body.append(button('Ask an agent about this change',()=>githubContext({kind:'change',repo,number:item.number,head:data.head.sha,base:data.base.sha})),button('Reviews & inline discussion',()=>githubReviews(repo,item.number)),button('Review this change',()=>reviewChange(repo,item.number,data.head.sha)),button('Merge checked pipeline change',()=>githubAction({action:'merge',repo,number:item.number,head:data.head.sha})));body.append(el('p','muted','Merge requires a pipeline branch, the current head, a passing verify check, and permission under GitHub’s merge rules.'));githubDiff(body,repo,item.number,data);}
 body.append(button('Full timeline',()=>githubTimeline(repo,item.number)));
 body.append(button('Edit labels',()=>editLabels(repo,item.number,data.labels||[])));
 if(kind==='issues'&&data.state==='open')await issueControls(body,repo,data,githubAction,()=>githubDetail(repo,item,kind));
 const comments=section(body,'Discussion');for(const comment of data.comments||[])comments.append(commentView(comment));if(data.comments_next_cursor)comments.append(button('More comments',()=>githubComments(comments,repo,item.number,data.comments_next_cursor)));
 const reply=field(body,'Reply',el('textarea'));const clearReply=restoreDraft(reply,['github',repo,item.number]);body.append(button('Post reply',async()=>{await githubAction({action:'comment',repo,number:item.number,body:reply.value},committed=>clearReply(committed.body));}));
 if(kind==='issues')body.append(button(data.state==='closed'?'Reopen issue':'Close issue',async()=>{await githubAction({action:data.state==='closed'?'reopen':'close',repo,number:item.number});}));
 });
}
function commentView(comment){const node=el('article','card');node.append(el('h3','',comment.user?.login||'Comment'),markdownView(comment.body||''));return node;}
async function githubComments(parent,repo,number,cursor){const data=await api(`/api/github/comments?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}`);for(const item of data.items)parent.append(commentView(item));if(data.next_cursor)parent.append(button('More comments',()=>githubComments(parent,repo,number,data.next_cursor)));}
function githubFiles(parent,repo,number,files,sha){for(const file of files||[]){const details=el('details','card');details.append(el('summary','',`${file.filename} · +${file.additions} −${file.deletions}`),el('pre','',file.patch||'GitHub omitted this patch; open the full file.'),button('Read file at this head',()=>githubTree(repo,file.filename,sha)));parent.append(details);}}
async function githubFilePage(parent,repo,number,cursor,sha){const data=await api(`/api/github/files?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}&head=${sha}`);githubFiles(parent,repo,number,data.items,sha);if(data.next_cursor)parent.append(button('More changed files',()=>githubFilePage(parent,repo,number,data.next_cursor,sha)));}
async function githubTree(repo,path='',ref='HEAD'){
 const host=sheet(repo+' / '+path);const body=el('div');host.append(body);
 await guarded(body,async()=>{
  const data=await api(`/api/github/tree?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`);
  if(!body.isConnected)return;
  const revision=data.ref||ref;rememberDetail('github-tree',JSON.stringify({repo,path,ref:revision}));
  body.append(el('p','muted',data.source),button('Choose branch',()=>githubBranches(repo)));
  if(path)body.append(button('Parent folder',()=>githubTree(repo,path.split('/').slice(0,-1).join('/'),revision)));
  if(data.items){for(const item of data.items)body.append(card(item.name,item.type,()=>githubTree(repo,item.path,revision)));}
  else{body.append(el('p','muted',`Blob ${data.object.sha}`),el('pre','',data.object.text));if(data.object.readable){body.append(button('Ask an agent about this file',()=>githubContext({kind:'file',repo,path,ref:revision,blob:data.object.sha})));githubLineSelection(body,{kind:'file',repo,path,ref:revision,blob:data.object.sha},data.object.text);}if(data.object.html_url)body.append(link('Open source',data.object.html_url));}
 });
}
async function githubBranches(repo,cursor=1,parent=null){
 const body=parent||el('div');if(!parent)sheet(repo+' · branches').append(body);let loaded=false;
 if(!parent)body.append(el('p','muted','Browse the saved commit on GitHub. Your Mac checkout stays on its current branch.'));
 await guarded(body,async()=>{
  const data=await api(`/api/github/branches?repo=${encodeURIComponent(repo)}&cursor=${cursor}`);
  if(!body.isConnected)return;
  for(const branch of data.items)body.append(card(branch.name,branch.sha.slice(0,12),()=>githubTree(repo,'',branch.sha)));
  if(data.next_cursor){const more=button('More branches',async()=>{more.disabled=true;if(await githubBranches(repo,data.next_cursor,body))more.remove();else more.disabled=false;});body.append(more);}
  loaded=true;
 });
 return loaded;
}

async function refreshRunningSource(source){
 if(source.busy)return;
 source.busy=true;const next=el('div');
 try{
  await source.render(next);
  source.rows.replaceChildren(...next.childNodes);
  source.status.textContent=`Checked ${new Date().toLocaleTimeString()}`;
 }catch(error){
  source.status.textContent=`${source.name} unavailable; last view retained. ${error.message}`;
 }finally{source.busy=false;}
}
async function observedProcesses(parent){
 const data=await api('/api/live');
 if(data.state==='unreadable')throw Error(data.detail);
 if(data.detail)parent.append(el('p','error',data.detail));
 parent.append(el('p','muted',`${data.sessions.length} Mac processes · observed separately`));
 for(const item of data.sessions)parent.append(card(`${item.engine} · PID ${item.pid}`,`${item.cwd||'Folder unavailable'} · process observed`,()=>observedProcess(item)));
}
async function runningNow(parent){
 const sources=[
  {name:'Office tasks',render:node=>taskList(node,true)},
  {name:'Agent sessions',render:node=>sessions(node,true)},
  {name:'Mac processes',render:observedProcesses,collapsed:true}
 ];
 for(const source of sources){
  const container=el(source.collapsed?'details':'div');
  if(source.collapsed)container.append(el('summary','','Observed Mac processes'));
  source.status=el('p','muted');source.rows=el('div');
  container.append(source.status,source.rows);parent.append(container);
 }
 function refresh(){
  if(!parent.isConnected)return;
  for(const source of sources)void refreshRunningSource(source);
 }
 refresh();const timer=setInterval(()=>{if(!parent.isConnected)clearInterval(timer);else refresh();},10000);
}
function observedProcess(item){
 const body=sheet(`${item.engine} · PID ${item.pid}`);
 body.append(el('p','',item.cwd),el('p','muted',`Observed process; started ${item.started||'unknown'}. A process can host multiple conversations. Account and control are not inferred from its folder.`));
 if(item.transcript)body.append(button('Read exact open transcript',()=>observedTranscript(body,item.key)));
 else body.append(el('p','muted','No unique open transcript is proven. Retained histories remain available in Work.'));
}
async function observedTranscript(body,key){
 const data=await api(`/api/live/transcript?key=${encodeURIComponent(key)}&offset=-1&limit=100`);
 const turns=el('div','stack');
 const render=(parent,lines)=>{for(const line of lines)parent.append(card(line.who||line.kind,line.text));};
 const earlier=button('Load earlier messages',async()=>{
  const end=Number(earlier.dataset.offset),offset=Math.max(0,end-100);
  const page=await api(`/api/live/transcript?key=${encodeURIComponent(key)}&offset=${offset}&limit=${end-offset}&identity=${encodeURIComponent(data.identity)}`);
  const fragment=el('div');render(fragment,page.lines);const height=body.scrollHeight;
  turns.prepend(...fragment.childNodes);body.scrollTop+=body.scrollHeight-height;
  earlier.dataset.offset=offset;earlier.hidden=offset===0;
 });
 earlier.dataset.offset=data.offset;earlier.hidden=data.offset===0;
 body.append(earlier,turns);render(turns,data.lines);earlier.classList.add("transcript-earlier");transcriptNavigation(body,turns).append(earlier);
}

async function sessions(parent,onlyActive=false){
 const data=await api('/api/sessions');
 if(!['ok','empty'].includes(data.state))throw Error(data.detail||data.state);
 const rows=data.sessions.filter(item=>!onlyActive||item.status!=='inactive');
 for(const item of rows)parent.append(card(item.name,`${item.tool} · hcom reports ${item.status} · ${item.repo||item.directory}`,()=>conversation(item)));
 if(!rows.length)empty(parent,'No addressable sessions currently active.');
}
async function conversation(session){
 rememberDetail('hcom',JSON.stringify({session_id:session.session_id,name:session.name,started_at:session.started_at,directory:session.directory}));
 const body=el('div');sheet(`${session.name} · ${session.tool}`).append(body);body.append(el('p','muted',session.directory));
 const data=await api(`/api/session?name=${encodeURIComponent(session.name)}&last=50`);if(!body.isConnected)return;
 const turns=el('div','stack');body.append(turns);
 for(const turn of data.exchanges){if(turn.you)turns.append(card('You',turn.you));if(turn.them)turns.append(markdownView(turn.them));}
 transcriptNavigation(body,turns);
 const message=field(body,'Steer this session',el('textarea'));message.placeholder='A follow-up, correction, or next step…';
 const clearMessage=restoreDraft(message,['hcom',session.session_id||[session.name,session.started_at,session.directory]]);
 const send=button('Send message',async()=>{const sent=message.value;const result=await api('/api/session/say',{name:session.name,text:sent});notice(result.result||'Queued for the agent; delivery is not yet confirmed.');clearMessage(sent);});send.disabled=!session.reachable;body.append(send);
}
async function library(parent){
 intro(parent,'Library','Everything has a place.','Read, listen, browse. Follow a file back to the work that made it.');
 parent.append(link('Lesson previews','/lessons'));
 parent.append(link('Lesson outlines','/outlines'));
 const filters=el('div','actions');const view=el('div');parent.append(filters,view);
 const show=async(kind)=>{const content=el('div');view.replaceChildren(content);if(kind==='files')await browse(content);else await mediaList(content,kind);};
 for(const [key,label] of [['files','Files & documents'],['podcast','Podcasts'],['substrate','Substrate']])filters.append(button(label,()=>show(key)));
 for(const [key,title] of [['saved','Saved'],['recent','Recently opened']]){
  const group=el('details','card');group.append(el('summary','',title));
  for(const item of userState.collection(key).slice(0,20))group.append(card(item.title||item.kind,'Open saved view',()=>restoreDetail(item)));
  parent.insertBefore(group,view);
 }
 await show('files');
 await officeSections(parent,['library','mail','care','money_swarm']);
}
async function system(parent){
 intro(parent,'System','Behind the scenes.','Connection, schedules, and the complete run history.');
 const towerBox=section(parent,'Tower');await guarded(towerBox,()=>towerWork(towerBox));
 const health=section(parent,'Your Mac');await guarded(health,async()=>{const data=await api('/api/health');health.append(card(data.ok?'Connected':'Needs attention',`Serving ${data.revision?.slice(0,10)} · snapshot ${data.snapshot_at}`));});
 await guarded(health,async()=>{const data=await api('/api/system/machine');health.append(card('Machine',data.uptime),card('Memory',data.memory),card('Storage',`${(data.disk.free/1073741824).toFixed(1)} GB free`));});
 const observed=section(parent,'Other Mac agent processes');await guarded(observed,async()=>{const data=await api('/api/live');observed.append(el('p','muted',`Observed ${data.as_of}. Process visibility does not prove account or conversation identity.`));for(const item of data.sessions)observed.append(card(`${item.engine} · PID ${item.pid}`,`${item.cwd} · observed only`,()=>{const body=sheet('Observed Mac process');body.append(el('p','',`PID ${item.pid} · started ${item.started}`),el('p','muted','Control and transcript association are unproven. Open an exact retained conversation in Work.'));}));});
 const scheduleBox=el('details');scheduleBox.append(el('summary','','Machine schedules and services'));parent.append(scheduleBox);const existing=section(scheduleBox,'Registered jobs');await guarded(existing,async()=>{const data=await api('/api/system/jobs');if(data.state!=='ok')existing.append(el('p','error',data.detail||data.state));for(const job of data.jobs||[])existing.append(card(job.id,`${job.state} · ${job.schedule}`,()=>{const body=sheet(job.id);body.append(el('p','',job.detail),el('p','',`Last attempt: ${job.last_attempt||'none'} · exit ${job.last_rc??'unknown'}`),el('pre','',job.command),el('p','muted',job.note),button('Read log',()=>jobLog(job.id)),button('Run receipts',()=>jobReceipts(job.id)));}));});
 const plans=section(parent,'Nexus automations');await guarded(plans,async()=>{const data=await api('/api/system/plans');for(const plan of data.items)plans.append(card(plan.name,`${plan.enabled?'Enabled':'Paused'} · ${plan.kind}`,()=>planDetail(plan)));});
 const history=section(parent,'Run history');await guarded(history,()=>runs(history));
 await officeSections(parent,['flows','cost','webhook','care-grader']);
 parent.append(button('Settings',settings));
}
async function towerWork(parent){
 const t=(await world()).automation?.tower||{};if(t.state!=='ok'){parent.append(el('p','error',t.detail||'Tower state is unavailable'));return;}
 towerPulse(parent,t);
 for(const run of t.runs||[])parent.append(card(run.plan,`${run.state} for ${run.age}`,()=>flightDetail(run.flight)));
 for(const issue of t.issues||[])parent.append(towerIssueCard(issue));
 if(t.activity==='idle')parent.append(el('p','muted','Idle: no eligible Tower work.'));
 towerVerified(section(parent,'Verified recently'),t.completions||[]);
}
function towerVerified(parent,items){
 if(!items.length)parent.append(el('p','muted','No landed, verified Tower result on record.'));
 for(const item of items)parent.append(card(item.issue||item.flight,`landed ${String(item.sha).slice(0,7)} · ${item.age} ago`,()=>flightDetail(item.flight)));
}
function towerPulse(parent,t){
 const live=t.tower||{},tick=t.tick_age?` · last tick ${t.tick_age} ago`:'';parent.append(card(t.activity||'unknown',live.detail||`tower ${live.state}${tick}`));
 const oldest=t.oldest_queued?` (oldest ${t.oldest_queued})`:'',failed=t.failed?` · ${t.failed} failed`:'';
 parent.append(el('p','muted',`${t.working} working · ${t.queued} queued${oldest} · ${t.held} held · ${t.retrying} retrying${failed}`));
}
function towerIssueCard(issue){
 const facts=[issue.state,issue.detail,issue.next,issue.phase&&`phase ${issue.phase}`,issue.age&&`for ${issue.age}`,issue.progress?`last progress ${issue.progress} ago`:'timing unavailable',issue.obligation&&`Next: ${issue.obligation}`];
 return card(`${issue.id} · ${issue.title}`,facts.filter(Boolean).join(' · '),issue.attempt?()=>flightDetail(issue.attempt):undefined);
}
async function runs(parent,cursor=0){const data=await api(`/api/system/runs?cursor=${cursor}`);for(const item of data.items)parent.append(card(item.title||item.plan,`${item.state} · ${new Date(item.created_at*1000).toLocaleString()}`,()=>flightDetail(item.id)));if(data.next_cursor!==null)parent.append(button('Older runs',()=>runs(parent,data.next_cursor)));}
async function flightDetail(id){rememberDetail('flight',id);const data=await api(`/api/system/flight?id=${id}`);const body=sheet(data.title||id);body.append(el('p','muted',`${data.state} · ${data.plan} · attempt ${data.attempt}`));if(data.source_task?.startsWith('github:')){const target=data.source_task.slice(7).replace('#','/issues/');body.append(link('Source issue',`https://github.com/${target}`));}body.append(markdownView(data.objective||''));for(const action of (['failed','cancelled'].includes(data.state)?['retry']:['running','queued'].includes(data.state)?['cancel']:[]))body.append(button(action,()=>systemCommand(action,id)));const proof=section(body,'Durable evidence');for(const item of data.evidence||[])proof.append(card(item.kind,new Date(item.ts*1000).toLocaleString(),()=>{const view=sheet(item.kind);view.append(el('pre','',JSON.stringify(item.payload,null,2)));}));const log=section(body,'Log');await flightLogs(log,id);const events=section(body,'Events');await eventPage(events,id);const artifacts=section(body,'Artifacts');for(const item of data.artifacts)artifacts.append(card(item.kind,item.ref,()=>{const view=sheet(item.ref);view.append(link('Open or download artifact',`/api/system/artifact?id=${encodeURIComponent(item.id)}`));const frame=el('iframe','preview');frame.src=`/api/system/artifact?id=${encodeURIComponent(item.id)}`;frame.setAttribute('sandbox','allow-scripts');frame.title=item.ref;view.append(frame);}));}
async function flightLogs(parent,id){
 const lanes=await api(`/api/system/log-lanes?id=${id}`);const log=el('div');
 const picker=select([['','Main log'],...lanes.items.map(name=>[name,name])],'');picker.setAttribute('aria-label','Log source');
 picker.addEventListener('change',()=>{log.replaceChildren();logPage(log,id,0,picker.value).catch(error=>failure(log,error));});
 parent.append(picker,log);await logPage(log,id);
}
async function logPage(parent,id,offset=0,lane='',version=''){
 const data=await api(`/api/system/log?id=${id}&offset=${offset}&lane=${encodeURIComponent(lane)}&version=${encodeURIComponent(version)}`);
 parent.append(el('p','muted',data.state),el('pre','',data.text||'No log produced yet.'));
 if(data.next_offset!==null)parent.append(button(data.state==='rotated'?'Read current log':'Continue log',()=>logPage(parent,id,data.next_offset,lane,data.version)));
}

async function eventPage(parent,id,cursor=0){const data=await api(`/api/system/events?id=${id}&cursor=${cursor}`);for(const item of data.items)parent.append(card(item.kind,JSON.stringify(item.payload)));if(data.next_cursor!==null)parent.append(button('More events',()=>eventPage(parent,id,data.next_cursor)));}
function planDetail(plan){const body=sheet(plan.name);body.append(el('p','',plan.objective||''),el('p','muted',`${plan.enabled?'Enabled':'Paused'} · ${plan.kind}`),el('pre','',plan.schedule));for(const action of [plan.enabled?'pause':'resume','run'])body.append(button(action,()=>systemCommand(action,plan.id)));}
function gateDetail(gate){const body=sheet('Needs you');body.append(markdownView(gate.question||gate.text||JSON.stringify(gate)));for(const [answer,label] of [['allow','Allow once'],['deny','Deny']])body.append(button(label,async()=>{await api('/api/gate',{question_id:gate.id,answer});notice('Answer recorded');$('#detail').close();route();}));}

let searchKind='';
async function search(cursor=0){
 const query=$('#global-search').value.trim();if(!query)return route();
 const parent=$('#content');if(cursor===0){parent.replaceChildren();intro(parent,'Everywhere',`“${query}”`,'Names, paths, and contents across your office.');const filters=el('div','actions');for(const [kind,label] of [['','All'],['file','Files'],['conversation','Conversations'],['github','GitHub'],['event','Events'],['log','Logs'],['podcast','Podcasts'],['substrate','Poetry']])filters.append(button(label,()=>{searchKind=kind;search();}));parent.append(filters);}
 await guarded(parent,async()=>{const data=await api(`/api/search/all?q=${encodeURIComponent(query)}&cursor=${cursor}&kind=${searchKind}`);parent.append(el('p','muted',`${data.total||0} results · index ${data.coverage.state} · ${data.coverage.count||0} objects · updated ${data.coverage.finished_at?new Date(data.coverage.finished_at*1000).toLocaleTimeString():'never'}`));showSearchCoverage(parent,data.coverage);for(const problem of data.coverage.errors||[])parent.append(el('p','error',`${problem.source}: ${problem.error}`));if(data.coverage.refresh?.state==='indexing')parent.append(button('Index updating · refresh results',()=>search()));for(const item of data.items)parent.append(card(item.title,`${item.project} / ${item.path}\n${item.excerpt}`,()=>openSearchResult(item)));if(data.next_cursor!==null)parent.append(button('More results',()=>search(data.next_cursor)));});
}
let decisionIndex=0,feedCategory='all';
async function watch(parent){
  await refreshAttention();
  const rows=attention.items.filter(requiresYou);
  const failures=attention.failures;
  let coordinatorRows=[];
  let coordinatorError='';
  try{const data=await api('/api/coordinators');coordinatorRows=data.coordinators||[];}
  catch(error){coordinatorError=error.message;}

  const exceptions=coordinatorRows.filter(row=>['failing','stalled','error'].includes(row.health));
  const healthy=coordinatorRows.length-exceptions.length;
  const overview=el('header','watch-overview');
  overview.append(el('div','eyebrow','Watch'));
  if(attention.errors.length)overview.append(el('h1','','Checking what needs you'));
  else if(rows.length)overview.append(el('h1','',rows.length===1?'One thing needs you':`${rows.length} things need you`));
  else overview.append(el('h1','','Nothing needs you'));
  if(coordinatorError)overview.append(el('p','watch-summary-muted','I can’t confirm system status right now.'));
  else{
   const summary=[];
   if(healthy)summary.push(`${healthy} ${healthy===1?'system is':'systems are'} working normally`);
   if(exceptions.length)summary.push(`${exceptions.length} ${exceptions.length===1?'needs':'need'} attention`);
   if(!summary.length)summary.push('No systems are reporting a problem');
   overview.append(el('p',exceptions.length?'watch-summary-attention':'watch-summary-normal',summary.join(' · ')));
  }
  if(attention.errors.length)overview.append(el('p','watch-summary-muted','The decision checks are unavailable, so I can’t confirm yet.'));
  parent.append(overview);

  if(rows.length||attention.errors.length){
  const decisions=section(parent,'Needs you');decisions.parentElement.classList.add('watch-decisions');
  if(rows.length){
  decisionIndex=Math.max(0,Math.min(decisionIndex,rows.length-1));
  const stack=el('div','decision-stack');decisions.append(stack);
  const draw=()=>{
   const entry=rows[decisionIndex];stack.replaceChildren();
   stack.append(el('p','decision-count',`${decisionIndex+1} of ${rows.length} decisions`));
   const nav=el('div','decision-nav');
   const previous=button('← Previous',()=>{decisionIndex=(decisionIndex-1+rows.length)%rows.length;draw();});
   const next=button('Next →',()=>{decisionIndex=(decisionIndex+1)%rows.length;draw();});
   nav.append(previous,next,button('Full list',()=>{const body=sheet('All decisions');for(const item of rows)body.append(item.kind==='permission'?permissionCard(item.item):attentionCard(item));}));
   stack.append(nav,entry.kind==='permission'?permissionCard(entry.item):attentionCard(entry));
  };draw();
  }else if(attention.errors.length){
   decisions.append(el('p','watch-unconfirmed',"I can't confirm yet. A source for decisions is unavailable."));
  }

  if(attention.errors.length){
   const source=el('details','watch-source-note');source.append(el('summary','','Check details'));
   for(const problem of attention.errors)source.append(el('p','muted',problem));decisions.append(source);
  }
  }

  if(failures.length){
   const stalled=section(parent,'Automation needs repair');
   stalled.append(el('p','muted',`${failures.length} automated ${failures.length===1?'pass needs':'passes need'} diagnosis. Inspect the original work and blocker before retrying.`));
   for(const entry of failures)stalled.append(automationFailureCard(entry));
   stalled.append(link('Track the repair', 'https://github.com/ariaxhan/nexus-office/issues/186'));
  }

  if(coordinatorRows.length){
   const systems=el('section','watch-systems');
   systems.append(el('h2','watch-section-title','Systems'));
   for(const row of coordinatorRows){
    const issue=exceptions.includes(row);
    const names={tbs:'Thinking Brain School',matra:'Matra'};
    const outcomes={tbs:'Keeping lessons healthy and ready for families.',matra:'Fixing app issues and delivering tested improvements.'};
    const item=el('article','watch-system'+(issue?' is-attention':''));
    const head=el('div','watch-system-head');
    head.append(el('h3','',names[row.id]||row.name),el('span',issue?'watch-system-status is-attention':'watch-system-status',issue?'Needs attention':'Working normally'));
    item.append(head,el('p','watch-system-outcome',outcomes[row.id]||'Moving its assigned work forward.'),el('p','watch-system-action',issue?'I’m looking into it.':'Nothing needed from you.'));
    item.append(button('See activity',()=>{localStorage.setItem('office-coordinator-pick',row.id);location.hash='coordinator';},'watch-activity-link'));
    systems.append(item);
   }
   parent.append(systems);
  }else if(coordinatorError){
   const systems=section(parent,'Systems');systems.append(el('p','watch-summary-muted','System status is unavailable.'));
  }

  await guarded(parent,async()=>{
   const done=coordinatorRows.flatMap(row=>(row.commits||[]).map(item=>({...item,checkout:item.checkout||row.id})))
    .filter(item=>item.sha&&item.checkout).sort((a,b)=>new Date(b.at||0)-new Date(a.at||0)).slice(0,3);
   const recent=el('details','watch-state watch-recent');
   const summary=el('summary');summary.append(el('strong','', 'Recent changes'));
   recent.append(summary);
   const list=el('div','watch-done-list');
   for(const item of done){
    const row=button('',()=>openCoordinatorCommit(item.sha,item.checkout),'watch-done-item');
    const names={tbs:'Thinking Brain School','thinking-brain-school':'Thinking Brain School',matra:'Matra'};
    row.append(el('strong','',item.subject||'Committed change'),el('span','muted',`${names[item.checkout]||item.checkout} · ${item.at?formatAge(Date.now()-new Date(item.at).getTime()):'time unavailable'}`));list.append(row);
   }
   if(!done.length)list.append(el('p','muted',coordinatorRows.length?'No recent commit receipts.':'Commit evidence is unavailable right now.'));
   recent.append(list);parent.append(recent);
  });

}
function requiresYou(entry){return entry.kind==='human-ask';}
function formatAge(milliseconds){
 if(!Number.isFinite(milliseconds))return 'not checked yet';
 const minutes=Math.max(0,Math.floor(milliseconds/60000));
 if(minutes<1)return 'just now';if(minutes<60)return `${minutes}m ago`;
 const hours=Math.floor(minutes/60);if(hours<48)return `${hours}h ago`;
 return `${Math.floor(hours/24)}d ago`;
}
function feedAction(label,active,fn,glyph){const control=button('',fn,'feed-icon'+(active?' active':''));control.innerHTML=glyph;control.title=label;control.setAttribute('aria-label',label);control.setAttribute('aria-pressed',String(active));return control;}
async function feedDetail(post){
 const data=await api('/api/feed/detail?id='+encodeURIComponent(post.id));const body=sheet(data.title);
 body.append(el('p','muted',`${data.category} · ${new Date(data.published_at*1000).toLocaleString()} · ${data.model}`),el('p','',data.body));
  const sources=section(body,'Sources');for(const source of data.sources){
   const issue=source.url.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(issues|pull)\/(\d+)/);
    if(source.url==='/#watch')sources.append(button(source.title,()=>{document.querySelector('#detail').close();location.hash='watch';}));
    else if(source.url.startsWith('/api/media/detail?id=')){const id=decodeURIComponent(source.url.split('id=')[1]);sources.append(button(source.title,()=>mediaDetail(id)));}
    else if(source.url.startsWith('/api/buzz/detail?id=')){const id=decodeURIComponent(source.url.split('id=')[1]);sources.append(button(source.title,()=>buzzSourceDetail(id)));}
   else if(issue)sources.append(button(source.title,()=>githubDetail(issue[1],{number:Number(issue[3])},issue[2]==='pull'?'prs':'issues')));
   else sources.append(link(source.title,source.url));
  }
  if(data.media?.length){const media=section(body,'Media');for(const asset of data.media){
   if(asset.kind==='audio'){const player=el('audio');player.controls=true;player.src=asset.url;media.append(player,button('Episode details',()=>mediaDetail(decodeURIComponent(asset.source_url.split('id=')[1]))));continue;}
   const image=el('img');image.src=asset.url||asset.source_url;image.alt=asset.alt;media.append(image,link('Original media',asset.source_url));
  }}
  const followBox=el('details','find-group');followBox.append(el('summary','','Follow an evolving topic'));
  const followInput=el('input');followInput.placeholder='e.g. Mercury, local models';followInput.setAttribute('aria-label','Topic to follow');
  followBox.append(followInput,button('Follow',async()=>{await api('/api/feed/follow',{label:followInput.value,active:true});notice('Following '+followInput.value);feedDetail(post);}));
  const followed=await api('/api/feed/threads');for(const item of followed.items)followBox.append(button('Following '+item.label+' · remove',async()=>{
   await api('/api/feed/follow',{label:item.label,active:false});feedDetail(post);
  }));body.append(followBox);
 const replies=section(body,'Your replies');for(const reply of data.replies)replies.append(el('p','',reply.body));
 const fieldNode=field(body,'Reply',el('textarea'));fieldNode.placeholder='What did this miss or make you curious about?';
 body.append(button('Send reply',async()=>{await api('/api/feed/reply',{id:post.id,body:fieldNode.value});notice('Reply saved');feedDetail(post);},'primary'));
}
async function buzzSourceDetail(id){
 const data=await api('/api/buzz/detail?id='+encodeURIComponent(id));
 const body=sheet('Source conversation');
 for(const row of data.thread){
  const state=['posted',row.mirrored&&'mirrored',row.coordinator_read&&'coordinator read',row.acted&&'acted on'].filter(Boolean).join(' · ');
  body.append(el('p','muted',`${row.author} · #${row.channel} · ${new Date(row.at).toLocaleString()} · ${state}`),el('p','',row.text));
  if(row.issue){const [repo,number]=row.issue.split('#');body.append(link('Open linked issue',`https://github.com/${repo}/issues/${number}`));}
  if(row.receipt)body.append(el('p','muted',`Receipt: ${row.receipt}`));
  body.append(el('p','muted',`Buzz event: ${row.source}`));
 }
}
async function digestDetail(id){
  const data=await api('/api/digests/detail?id='+encodeURIComponent(id));
  const body=sheet(data.title+' · '+data.date);
  body.append(el('p','muted','Your original daily email · saved in Office'),el('div','digest-copy',data.text));
  if(data.links.length){const sources=el('details','find-group');sources.append(el('summary','',`${data.links.length} links in this email`));
   for(const item of data.links)sources.append(link(item.title,item.url));body.append(sources);}
}
async function dailyDigests(parent){
  const data=await api('/api/digests');
  if(!data.items.length)return;
  const latest=data.items[0].date;
  const current=data.items.filter(item=>item.date===latest);
  const box=el('details','digest-strip');box.append(el('summary','',`Daily dispatches · ${latest} · ${current.length} emails`));
  const list=el('div','digest-list');for(const item of current)list.append(button(item.title,()=>digestDetail(item.id)));
  box.append(list);parent.append(box);
}
function renderFeedPost(parent,post){
  const article=el('article','feed-post');
  const kind=post.format==='paper'?' · paper':'';
  article.append(el('p','feed-eyebrow',`${post.category}${kind} · ${new Date(post.published_at*1000).toLocaleString()}`),
                 el('h2','',post.title));
  if(post.media?.length){for(const asset of post.media){
   if(asset.kind==='audio'){const player=el('audio','feed-audio');player.controls=true;player.src=asset.url;article.append(player);}
   else if(asset.kind==='image'){const image=el('img','feed-media');image.src=asset.url||asset.source_url;image.alt=asset.alt;article.append(image);}
  }}
  article.append(el('p','',post.body));
  const actions=el('div','feed-actions');article.append(actions);
  const drawActions=()=>{
   actions.replaceChildren(button(`${post.sources.length} source${post.sources.length===1?'':'s'} · context →`,()=>feedDetail(post),'feed-source'));
   for(const [kind,label,glyph] of [['save','Save','<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 3.75h11A1.75 1.75 0 0 1 19.25 5.5v15L12 16.2l-7.25 4.3v-15A1.75 1.75 0 0 1 6.5 3.75Z"/></svg>'],['love','Love','♡︎'],['dislike','Not for me','↓︎']]){
    actions.append(feedAction(label,!!post.feedback?.reactions?.[kind],async()=>{
     try{const active=!post.feedback?.reactions?.[kind];const result=await api('/api/feed/react',{id:post.id,kind,active});post.feedback=result.feedback;drawActions();}
     catch(error){notice(error.message);}
    },glyph));
   }
   actions.append(feedAction('Reply',false,()=>feedDetail(post),'↩︎'));
  };drawActions();parent.append(article);
}
async function feed(parent){
 intro(parent,'Feed','The world and your work','Small stories worth knowing, seeing, or hearing.');
 const choices=el('div','feed-filters');parent.append(choices);
   for(const [key,name] of [['all','For you'],['latest','Latest'],['following','Following'],['world','World'],['politics','Politics'],['ai','AI'],['science','Science'],['culture','Culture'],['history','History'],['business','Business'],['technology','Tech'],['work','Work'],['listen','Listen'],['saved','Saved']]){
   choices.append(button(name,()=>{feedCategory=key;route();},'feed-filter'+(feedCategory===key?' active':'')));
  }
  await guarded(parent,()=>dailyDigests(parent));
  const stream=el('div','feed-stream');parent.append(stream);
 await guarded(stream,async()=>{
  const data=await api('/api/feed?category='+encodeURIComponent(feedCategory));
  if(!data.items.length)empty(stream,'No published posts in this category yet.');
   for(const post of data.items)renderFeedPost(stream,post);
   if(data.next_cursor!==null){const more=button('More posts',async()=>{more.disabled=true;try{await feedMore(stream,data.next_cursor);more.remove();}catch(error){more.disabled=false;notice(error.message);}});stream.append(more);}
 });
 const podcasts=section(parent,'Listen');await guarded(podcasts,async()=>{const data=await api('/api/media?kind=podcast');for(const item of data.items.slice(0,2))podcasts.append(card(item.title,`${Math.round(item.duration_s/60)} minutes · ${item.date}`,()=>mediaDetail(item.id)));});
}
async function feedMore(parent,cursor){const data=await api(`/api/feed?category=${encodeURIComponent(feedCategory)}&cursor=${cursor}`);for(const post of data.items)renderFeedPost(parent,post);if(data.next_cursor!==null){const more=button('More posts',async()=>{more.disabled=true;try{await feedMore(parent,data.next_cursor);more.remove();}catch(error){more.disabled=false;notice(error.message);}});parent.append(more);}}
async function ask(parent){
  const chat=el('section','ask-page');parent.append(chat);
  const advanced=el('details','ask-advanced');advanced.append(el('summary','','Advanced · model choice'));
  const picker=el('select','ask-model');picker.setAttribute('aria-label','Office model');advanced.append(picker);chat.append(advanced);
 const scrollRegion=el('div','ask-scroll-region');const thread=el('div','ask-thread');
 const jump=button('↓',()=>{thread.scrollTop=thread.scrollHeight;updateJump();},'ask-jump');jump.setAttribute('aria-label','Jump to latest message');jump.hidden=true;
 scrollRegion.append(thread,jump);chat.append(scrollRegion);
  const form=el('form','ask-compose');const input=el('textarea');input.placeholder='Ask what happened, why work is waiting, or what to fix…';input.setAttribute('aria-label','Ask Office');input.rows=2;
 const queueStatus=el('p','ask-queue-status');queueStatus.setAttribute('aria-live','polite');
 const submit=el('button','primary','Send');submit.type='submit';form.append(input,submit);chat.append(queueStatus,form);
 let current=null,rendered=false;
 const distanceFromBottom=()=>thread.scrollHeight-thread.scrollTop-thread.clientHeight;
 const updateJump=()=>{jump.hidden=!rendered||distanceFromBottom()<64;};
 thread.addEventListener('scroll',updateJump);
 await guarded(chat,async()=>{
   const data=await api('/api/ask');
   const clearDraft=restoreDraft(input,['ask']);const pendingKey='office-ask-pending';
   let pending;try{pending=JSON.parse(localStorage.getItem(pendingKey)||'null');}catch{localStorage.removeItem(pendingKey);}
   if(!pending?.request_id||!pending?.text||!pending?.model)pending=null;
   const initial=el('option','',data.selection||data.model);initial.value=data.selection||data.model;picker.append(initial);
  const draw=(state,toBottom=false)=>{
   const follow=toBottom||!rendered||distanceFromBottom()<64,previousTop=thread.scrollTop;
   current=state;thread.replaceChildren();
    if(!state.messages.length){thread.append(el('p','ask-intro','Ask in your own words. Office will bring back the answer and where it came from.'));
     for(const prompt of ['Is anything blocked on me?','What happened while I was asleep?','Why isn’t HomeClass moving?'])
      thread.append(button(prompt,()=>{input.value=prompt;input.focus();},'ask-prompt'));
   }
    const lastAnswer=[...state.messages].reverse().find(row=>row.role==='office'&&row.status==='completed');
    for(const row of state.messages){const bubble=el('article','ask-bubble '+row.role);
    const label=row.role==='user'?'You':row.role==='office'?'Office · '+(row.model||''):row.text;
    const heading=el('small','',label);if(row.role!=='system')heading.append(el('span','ask-status is-'+row.status,row.status));bubble.append(heading);
    if(row.role!=='system'){
     const waiting=row.status==='queued'?'Queued behind earlier messages…':row.status==='working'?'Working…':'';
     const copy=markdownView(row.text||waiting,{text:officeLinkText});copy.classList.add('ask-copy');
     copy.addEventListener('click',event=>{const anchor=event.target.closest('a');if(!anchor)return;
      const match=anchor.href.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(issues|pull)\/(\d+)/);
      if(match){event.preventDefault();githubDetail(match[1],{number:Number(match[3])},match[2]==='pull'?'prs':'issues').catch(error=>notice(error.message));}
      });bubble.append(copy);
      if(row.id===lastAnswer?.id){const rating=el('div','ask-rating');rating.append(el('span','muted','Useful?'));
       for(const [kind,label] of [['helpful','Yes'],['missed','Missed it']])rating.append(button(label,async()=>{
        await api('/api/ask/rate',{reply_id:row.id,kind});draw(await api('/api/ask'));
       },'ask-rate'+(row.rating===kind?' active':'')));
       bubble.append(rating);}
    }
    thread.append(bubble);
   }
   const queued=state.queue?.queued||0,working=state.queue?.working||0;
   queueStatus.textContent=[working?`${working} working`:'',queued?`${queued} queued`:''].filter(Boolean).join(' · ');
   queueStatus.hidden=!working&&!queued;
   thread.scrollTop=follow?thread.scrollHeight:previousTop;
   rendered=true;updateJump();
   };draw(data);
   api('/api/ask/models').then(models=>{const chosen=picker.value;picker.replaceChildren();
    for(const row of models.items){const option=el('option','',row.name);option.value=row.id;picker.append(option);}
    picker.value=chosen;}).catch(error=>notice('Model list: '+error.message));
  const timer=setInterval(async()=>{if(!chat.isConnected){clearInterval(timer);return;}if(!current?.busy)return;
   try{draw(await api('/api/ask'));}catch(error){notice(error.message);}},2500);
  async function sendPending(payload){
   submit.disabled=true;
   try{await api('/api/ask/send',payload);if(JSON.parse(localStorage.getItem(pendingKey)||'null')?.request_id===payload.request_id){localStorage.removeItem(pendingKey);pending=null;}clearDraft(payload.text);draw(await api('/api/ask'),true);}
   catch(error){if([400,403,404,409,413,422].includes(error.status)){localStorage.removeItem(pendingKey);pending=null;}notice(error.message);}
   finally{submit.disabled=false;}
  }
  form.addEventListener('submit',event=>{event.preventDefault();if(pending){void sendPending(pending);return;}const text=input.value.trim();if(!text)return;
   input.value=text;input.dispatchEvent(new Event('input'));pending={request_id:crypto.randomUUID(),text,model:picker.value};localStorage.setItem(pendingKey,JSON.stringify(pending));void sendPending(pending);});
  if(pending)void sendPending(pending);
 });
}
function officeLinkText(node,value){
 const expression=/https:\/\/[^\s<>]+/g;let at=0;
 for(const match of value.matchAll(expression)){
  if(match.index>at)node.append(document.createTextNode(value.slice(at,match.index)));
  const url=match[0].replace(/[.,;!?]+$/,'');node.append(link(url,url));at=match.index+url.length;
 }
 if(at<value.length)node.append(document.createTextNode(value.slice(at)));
}
async function find(parent){
 intro(parent,'Find','Everything has a place','Search, read, and follow an object back to the work that made it.');
 const projects=section(parent,'Projects');await guarded(projects,()=>projectRoster(projects));
 parent.append(card('Every open GitHub issue','TBS, Matra and Tower: counts, filters and triage',()=>{location.hash='issues';}));
 const libraryBox=el('details','find-group');libraryBox.append(el('summary','','Files, podcasts, and saved items'));parent.append(libraryBox);
 libraryBox.addEventListener('toggle',()=>{if(libraryBox.open&&libraryBox.childElementCount===1)library(libraryBox).catch(error=>failure(libraryBox,error));});
 const workBox=el('details','find-group');workBox.append(el('summary','','Tasks and conversations'));parent.append(workBox);
 workBox.addEventListener('toggle',()=>{if(workBox.open&&workBox.childElementCount===1)work(workBox).catch(error=>failure(workBox,error));});
 const systemBox=el('details','find-group');systemBox.append(el('summary','','Schedules, runs, and settings'));parent.append(systemBox);
 systemBox.addEventListener('toggle',()=>{if(systemBox.open&&systemBox.childElementCount===1)system(systemBox).catch(error=>failure(systemBox,error));});
}
async function issues(parent){
 intro(parent,'Issues','Everything still open.','Every open issue across TBS, Matra and Tower, counted against the repos that should be there.');
 const box=el('div','stack');parent.append(box);await guarded(box,()=>issueInventory(box,(repo,item)=>githubDetail(repo,item,'issues')));
}
let documentRequest=0;
async function documentView(parent,params){
 const request=++documentRequest,current=()=>request===documentRequest;
 const repo=params.get('repo')||'',path=params.get('path')||'';
 if($('#detail').open)$('#detail').close();
 intro(parent,repo||'Document',path||'No document was requested.','Opened from an agent handoff.');
 const box=el('div','stack');parent.append(box);
 try{
  if(!repo||!path)throw Error('No document was requested.');
  const found=await api(`/api/objects/locate?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}`);
  if(!current())return;
  box.append(card(`${found.project} / ${found.path}`,'Open this document again',()=>openFile(found.id)));
  if(!await openFile(found.id,0,current))return;
  await api('/api/objects/active',{repo,path,state:'open'});
 }catch(error){if(!current())return;failure(box,error);await api('/api/objects/active',{repo,path,state:'missing',error:error.message}).catch(()=>{});}
}
async function route(){
  const [pageRaw,parameters]=location.hash.slice(1).split('?');const page=pageRaw||'watch';const params=new URLSearchParams(parameters||'');
  if(page==='coordinator'&&['tbs','matra'].includes(params.get('id')))localStorage.setItem('office-coordinator-pick',params.get('id'));
  const views={watch,feed,ask,find,today,work,coordinator,library,system,issues,document:view=>documentView(view,params)};const parent=$('#content');parent.replaceChildren();
  document.body.dataset.page=page;
 for(const item of document.querySelectorAll('.tabs a'))item.setAttribute('aria-current',item.hash===`#${page}`?'page':'false');
  const view=el('div');parent.append(view);await guarded(view,()=> (views[page]||watch)(view));
  if(page==='find'&&params.get('q')){$('#global-search').value=params.get('q');await search();}
}
$('#settings').addEventListener('click',settings);$('#new-task').addEventListener('click',()=>newTask().catch(error=>notice(error.message)));$('#close-detail').addEventListener('click',backDetail);
$('#search-go').addEventListener('click',()=>search());$('#global-search').addEventListener('keydown',event=>{if(event.key==='Enter')search();});window.addEventListener('hashchange',route);
$('#settings').disabled=false;$('#new-task').disabled=false;
async function connection(){try{const data=await api('/api/health');$('#connection').textContent=data.ok?'Mac connected':'Mac needs attention';}catch{$('#connection').textContent='Mac unreachable';}}
await userState.synchronize();await loadSettings().catch(error=>notice(error.message));await connection();await route();if(new URL(location.href).searchParams.has('detail')&&!$('#detail').open)await restoreFromURL();setInterval(connection,30000);

async function archives(parent,cursor=0){
 const data=await api(`/api/archives?cursor=${cursor}`);
 for(const row of data.items)parent.append(card(row.title,`${row.profile} · ${row.engine} · ${row.cwd}`,()=>archiveDetail(row.id)));
 for(const error of data.errors)parent.append(el('p','error',error.error));
 if(data.next_cursor!==null)parent.append(button('Older conversations',()=>archives(parent,data.next_cursor)));
}
function archiveMessages(parent,items){
 for(const item of items){
  const node=item.structured?el('details','card'):el('article','card');
  node.append(el(item.structured?'summary':'h3','',item.role),item.structured?el('pre','',item.text):markdownView(item.text));parent.append(node);
 }
}
async function archiveDetail(id){
 rememberDetail('archive',id);
 const data=await api(`/api/archives/messages?id=${encodeURIComponent(id)}&offset=-1`);
 const body=sheet(`${data.session.profile} · ${data.session.engine}`);
 body.append(el('p','muted',`${data.session.engine_session_id} · ${data.session.cwd}`),button('Raw retained records',()=>archiveRaw(id)));
 const older=button('Load earlier messages',async()=>{
  const prior=await api(`/api/archives/messages?id=${encodeURIComponent(id)}&offset=${older.dataset.offset}`);
  const fragment=el('div');archiveMessages(fragment,prior.items);
  const height=body.scrollHeight;turns.prepend(...fragment.childNodes);
  body.scrollTop+=body.scrollHeight-height;
  older.dataset.offset=prior.previous_offset;older.hidden=prior.previous_offset===null;
 });
 older.dataset.offset=data.previous_offset;older.hidden=data.previous_offset===null;
 const turns=el('div','stack');body.append(older,turns);archiveMessages(turns,data.items);older.classList.add("transcript-earlier");transcriptNavigation(body,turns).append(older);
}
async function archiveRaw(id,offset=0,parent=null){
 const data=await api(`/api/archives/detail?id=${encodeURIComponent(id)}&offset=${offset}`);const body=parent||sheet('Raw retained records');body.append(el('pre','',data.text));
 if(data.next_offset!==null)body.append(button('Continue raw records',()=>archiveRaw(id,data.next_offset,body)));
}

function openSearchResult(item){
 if(item.id.startsWith('job-log:'))return jobLog(item.id.slice(8));
 if(item.id.startsWith('flight-log:')){const [id,lane]=JSON.parse(item.id.slice(11));return logPage(sheet('Run log'),id,0,lane);}
 if(item.id.startsWith('bot-history:'))return botHistory(item.id);
 if(item.id.startsWith('projection:'))return projectionDetail(item.id);
 if(item.kind==='conversation')return archiveDetail(item.id);
 if(['podcast','substrate'].includes(item.kind))return mediaDetail(item.id);
 return openFile(item.id);
}

async function systemCommand(action,id){
 const key='office-system-pending:'+JSON.stringify([action,id]);
 const pending=JSON.parse(localStorage.getItem(key)||'null')||{action,id,request_id:crypto.randomUUID()};localStorage.setItem(key,JSON.stringify(pending));
 let result;
 try{result=await api('/api/system/command',pending);}
 catch(error){if([400,403,404,409,413,422].includes(error.status))localStorage.removeItem(key);throw error;}
 if(result.result?.state!=='unconfirmed')localStorage.removeItem(key);
 notice(JSON.stringify(result.result));
 if(result.result?.state==='unconfirmed'){
  const body=sheet('Check automation delivery');body.append(el('p','',result.result.detail),button('Check current outcome',()=>systemCommand(action,id)));
 }
}
async function officeSections(parent,keys){
 const data=await world();
 for(const key of keys){const value=data.sections?.[key];if(!value)continue;const group=section(parent,key.replaceAll('_',' '));group.append(card(value.card?.title||key,value.state,()=>{const body=sheet(key);body.append(structured(value));}));}
}
function structured(value){
 if(value===null||typeof value!=='object')return el('p','',String(value??'—'));
 const container=el('div');
 for(const [key,item] of Object.entries(value)){if(key==='card')continue;const details=el('details');details.append(el('summary','',key));details.append(structured(item));container.append(details);}
 return container;
}

async function botConversation(bot){
 rememberDetail('bot',bot.id);
 const body=el('div');sheet(bot.name).append(body);const history=el('div');body.append(button('All retained messages',()=>botHistory('bot-history:'+bot.id+'.jsonl')),button('Archived office conversations',botArchives),history);
 let seen='',refreshing=false;
 const refresh=async()=>{if(refreshing||!body.isConnected)return;refreshing=true;try{const data=await api(`/api/chat?bot=${encodeURIComponent(bot.id)}`);if(!body.isConnected)return;const signature=JSON.stringify(data.turns||[]);if(signature===seen)return;seen=signature;history.replaceChildren();for(const turn of data.turns||[]){const row=card(turn.role,turn.content||turn.text);for(const item of turn.attachments||[])if(item.office_id)row.append(link(item.name||'Attached file',`/api/uploads/content?id=${encodeURIComponent(item.office_id)}&revision=${item.revision}`));history.append(row);}}finally{refreshing=false;}};
 let initialError='';try{await refresh();}catch(error){initialError=error.message;}if(!body.isConnected)return;transcriptNavigation(body,history);const message=field(body,'Message',el('textarea'));
 const clearMessage=restoreDraft(message,['bot',bot.id]);const key='office-bot-uploads:'+bot.id;
 const attached=attachments(body,JSON.parse(localStorage.getItem(key)||'[]'),items=>localStorage.setItem(key,JSON.stringify(items)));
 body.append(button('Send',async()=>{if(!attached.ready())throw Error('Wait for the attachment upload to finish.');const payload={bot:bot.id,message:message.value,uploads:attached.references()};await api('/api/chat',payload);clearMessage(payload.message);const stored=JSON.parse(localStorage.getItem(key)||'[]').map(({id,revision})=>({id,revision}));if(attached.ready()&&JSON.stringify(stored)===JSON.stringify(payload.uploads)&&JSON.stringify(attached.references())===JSON.stringify(payload.uploads))attached.clear();notice('Agent turn queued');await refresh();},'primary'),button('Refresh conversation',refresh));
 const status=el('p','muted',initialError?'Connection interrupted; retrying. '+initialError:'');body.append(status);
 async function poll(){if(!body.isConnected||!$('#detail').open)return;try{await refresh();status.textContent='';}catch(error){status.textContent='Connection interrupted; retrying. '+error.message;}if(body.isConnected)setTimeout(poll,3000);}
 setTimeout(poll,3000);
}

async function projectionDetail(id,offset=0,parent=null,revision=''){
 const data=await api(`/api/search/object?id=${encodeURIComponent(id)}&offset=${offset}&revision=${encodeURIComponent(revision)}`);
 if(data.target?.kind==='github')return githubDetail(data.target.repo,{number:data.target.number},'issues');
 const body=parent||sheet(data.title);body.append(el('p','muted',`${data.project} · ${data.coverage}`),el('pre','',data.text));
 if(!offset)for(const run of data.related_flights||[])body.append(button(`${run.state} · ${run.id}`,()=>flightDetail(run.id)));
 if(data.next_offset!==null)body.append(button('Continue',()=>projectionDetail(id,data.next_offset,body,data.revision||'')));
}


async function jobLog(id,offset=0,parent=null,version=''){if(!parent)rememberDetail('job-log',id);const data=await api(`/api/system/job-log?id=${encodeURIComponent(id)}&offset=${offset}&version=${encodeURIComponent(version)}`);const body=parent||sheet(id+' log');body.append(el('p','muted',data.state),el('pre','',data.text));if(data.next_offset!==null)body.append(button(data.state==='rotated'?'Read current log':'Continue log',()=>jobLog(id,data.next_offset,body,data.version)));}
async function jobReceipts(id,offset=0,parent=null){if(!parent)rememberDetail('job-history',id);const data=await api(`/api/system/job-history?id=${encodeURIComponent(id)}&offset=${offset}`);const body=parent||sheet(id+' receipts');for(const item of data.items)body.append(structured(item));for(const error of data.errors||[])body.append(el('p','error',error.error));if(data.next_offset!==null)body.append(button('Continue history',()=>jobReceipts(id,data.next_offset,body)));}

function restoreDetail(item){
 const handlers={file:openFile,media:mediaDetail,task:taskDetail,archive:archiveDetail,flight:flightDetail,'bot-history':botHistory,
  settings,folder:id=>browse(sheet('Files'),id),'job-log':jobLog,'job-history':jobReceipts,
  project:restoreProject,bot:restoreBot,hcom:restoreConversation,
  github:id=>{const ref=JSON.parse(id);return githubDetail(ref.repo,{number:ref.number},ref.kind);},
  'github-tree':id=>{const ref=JSON.parse(id);return githubTree(ref.repo,ref.path,ref.ref);},
 };
 if(!handlers[item.kind])throw Error('This saved view is unavailable');
 return handlers[item.kind](item.id);
}
async function restoreFromURL(){const value=new URL(location.href).searchParams.get('detail');if(!value){$('#detail').close();return;}try{await restoreDetail(JSON.parse(value));}catch(error){notice(error.message);}}
window.addEventListener('popstate',()=>{route();restoreFromURL();});
document.addEventListener('office-file-task',event=>newTask(event.detail.project,event.detail).catch(error=>notice(error.message)));
$('#detail').addEventListener('cancel',clearDetail);

document.addEventListener('office-github-detail',event=>githubDetail(event.detail.repo,{number:event.detail.number},'issues').catch(error=>notice(error.message)));
document.addEventListener('office-flight-detail',event=>flightDetail(event.detail).catch(error=>notice(error.message)));

async function githubCollection(repo,kind,cursor=1,parent=null){const body=parent||sheet(repo+' '+kind);const data=await api(`/api/github/collection?repo=${encodeURIComponent(repo)}&kind=${kind}&cursor=${cursor}`);for(const item of data.items)body.append(card(`#${item.number} ${item.title}`,item.state,()=>githubDetail(repo,item,kind)));if(data.next_cursor)body.append(button('Older items',()=>githubCollection(repo,kind,data.next_cursor,body)));}
async function githubReviews(repo,number,cursor=1,parent=null,inline=false){const body=parent||sheet(repo+' reviews');const data=await api(`/api/github/reviews?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}&inline=${inline}`);for(const item of data.items){body.append(commentView(item));if(item.diff_hunk)body.append(el('p','muted',`${item.path} · ${item.commit_id}`),el('pre','',item.diff_hunk));}if(data.next_cursor)body.append(button('More reviews',()=>githubReviews(repo,number,data.next_cursor,body,inline)));if(!parent)body.append(button('Inline comments',()=>githubReviews(repo,number,1,body,true)));}

async function refreshAttention(){
 const results=await Promise.allSettled([api('/api/human-asks'),world()]);
 const items=[],errors=[],failures=[];
 for(const [index,result] of results.entries()){
  if(result.status==='rejected'){errors.push(result.reason.message);continue;}
   if(index===0)errors.push(...(result.value.errors||[]));
  for(const entry of attentionItems(index,result.value)){
   (automationFailure(entry)?failures:items).push(entry);
  }
 }
 attention={items,errors,failures};
  const needsYou=items.filter(requiresYou).length;
  const badge=$('#detail-attention');badge.title=needsYou?`${needsYou} items need you`:'Nothing needs you right now';badge.setAttribute('aria-label',`${needsYou?`${needsYou} items need you`:'Nothing needs you right now'}${errors.length?'; a decision source is unavailable':''}`);
  const label=$('.tabs a[href="#watch"]');label.setAttribute('aria-label',needsYou?`Watch, ${needsYou} items need you`:'Watch, nothing needs you right now');
 const node=$('#needs-list');if(node)drawAttention(node);
}
function drawAttention(parent){
 const existing=new Map([...parent.querySelectorAll('[data-attention-key]')].map(node=>[node.dataset.attentionKey,node]));
 const nodes=attention.errors.map(error=>el('p','error',error));
 if(!attention.items.length&&!attention.errors.length)nodes.push(el('p','empty','Nothing waiting for you.'));
 const shown=parent.id==='needs-list'?attention.items.slice(0,3):attention.items;
 for(const entry of shown){
  const key=`${entry.kind}:${entry.item.id}`;
   const node=attentionCard(entry);
  node.dataset.attentionKey=key;nodes.push(node);
 }
 if(shown.length<attention.items.length)nodes.push(button(`View all ${attention.items.length} items`,()=>attentionList(sheet('Needs you'))));
 reconcileChildren(parent,nodes);
}
function reconcileChildren(parent,nodes){
 let cursor=parent.firstChild;
 for(const node of nodes){if(node===cursor)cursor=cursor.nextSibling;else parent.insertBefore(node,cursor);}
 while(cursor){const next=cursor.nextSibling;cursor.remove();cursor=next;}
}
function attentionCard(entry){
  if(entry.kind==='human-ask')return humanAskCard(entry.item);
 if(entry.kind==='buzz')return buzzDecisionCard(entry.item);
 if(entry.kind==='gate')return gateAttentionCard(entry.item);
 return issueAttentionCard(entry);
}
function humanAskCard(ask){
 if(ask.source==='office-permission'&&ask.live_request)return permissionCard(ask.live_request);
 if(ask.source==='gate'&&ask.live_request)return gateAttentionCard(ask.live_request);
 const node=el('article','card attention-choice');
 const verified=ask.last_source_verification?` · verified ${formatAge(Date.now()-Date.parse(ask.last_source_verification))}`:' · not verified yet';
 node.append(el('p','attention-source',`${ask.source_ref}${verified}`),el('h3','','A decision needs you'),el('p','attention-question',ask.action));
 if(ask.source_stale)node.append(el('p','muted','The execution owner or source is unavailable; this request remains open until its outcome is verified.'));
 if(ask.source==='github'){
  const [repo,number]=ask.source_ref.split('#');
  node.append(button('Inspect source and answer there',()=>githubDetail(repo,{number:Number(number)},'issues'),'attention-details'));
 }
 return node;
}
function gateAttentionCard(gate){
  const node=el('article','card attention-choice');node.append(el('h3','','Permission needed'),el('p','attention-question',gate.question||gate.title||'Pending decision'));
  const choices=el('div','attention-options');
  for(const [answer,label] of [['allow','Allow once'],['deny','Deny']])choices.append(button(label,async()=>{
   const result=await api('/api/gate',{question_id:gate.id,answer});if(!result.ok)throw Error(result.message||'Answer was not recorded');notice('Answer recorded');if($('#detail').open)$('#detail').close();await route();
  },'attention-option'));
  node.append(choices);return node;
}
function issueAttentionCard(entry){
 const issue=entry.item,decision=issue.decision;
 const node=el('article','card attention-choice');
 const askedAt=Date.parse(issue.last_word_at||'');
 const head=el('div','attention-head');head.append(el('p','attention-source',`${entry.repo.split('/')[1]} #${issue.number}${Number.isFinite(askedAt)?` · asked ${formatAge(Date.now()-askedAt)}`:''}`));
  node.append(head,el('h3','',issue.title));
  const context=reportLead(issue.body||'');if(context&&context!==issue.title)node.append(el('p','attention-context',context.length>200?context.slice(0,199)+'…':context));
  if(issue.decision_context){
   node.append(el('p','attention-question','A product decision is still needed for this issue.'),markdownView(issue.decision_context));
   node.append(button('Inspect evidence and answer in issue',()=>githubDetail(entry.repo,issue,'issues'),'attention-details'));
   return node;
  }
  node.append(el('p','attention-question',decision.question));
 const choices=el('div','attention-options');let busy=false;
 async function decide(payload){
  if(busy)return;busy=true;for(const control of choices.querySelectorAll('button'))control.disabled=true;
  try{
   const result=await api('/api/decision',{repo:entry.repo,issue:String(issue.number),...payload});
   if(!result.ok)throw Error(result.result||'Decision was not applied');
   snapshot=null;snapshotReadAt=0;notice(payload.kind==='close'?'Outdated issue closed':'Choice recorded');
   if($('#detail').open)$('#detail').close();await route();
  }catch(error){busy=false;for(const control of choices.querySelectorAll('button'))control.disabled=false;throw error;}
 }
 for(const option of [...decision.options].sort((a,b)=>Number(b.recommended)-Number(a.recommended)||a.n-b.n)){
  const control=button('',()=>decide({kind:'choose',n:option.n,label:option.label}),'attention-option'+(option.recommended?' is-recommended':''));
  control.append(el('strong','',option.label),el('span','',option.consequence||'Record this choice'));
  choices.append(control);
 }
 const actions=el('div','attention-head-actions');
 actions.append(button('Outdated · close issue',()=>decide({kind:'close',body:'Closing as outdated at Aria’s direction from Office Watch.'}),'attention-close'),button('Put away repo',()=>setDeskHidden(entry.repo,true),'attention-hide'));
 head.append(actions);
 node.append(choices,button('Open issue details',()=>githubDetail(entry.repo,issue,'issues'),'attention-details'));
 return node;
}
function buzzDecisionCard(row){
 const node=el('article','card attention-choice');
 node.append(el('p','attention-source',`${row.author} · TBS #${row.channel} · ${formatAge(Date.now()-Date.parse(row.at))}`),
  el('h3','','A reply needs you'),el('p','attention-question',row.question));
 node.append(button('Inspect source conversation',()=>buzzSourceDetail(row.id),'attention-details'));
 return node;
}
async function attentionList(parent){await refreshAttention();drawAttention(parent);}
setInterval(refreshAttention,10000);

function attentionItems(index,value){
 // Only requests that require a human decision belong in Needs you.
   if(index===0)return (value?.items||[]).map(item=>({kind:'human-ask',item}));
   if(index===1)return (value?.stations||[]).flatMap(station=>(station.issues||[])
      .filter(issue=>issue.bot_last===true&&issue.automation_failure==='missing_decision')
      .map(issue=>({kind:'issue',repo:station.repo,item:{...issue,id:`${station.repo}#${issue.number}`}})));
  return [];
}
function automationFailure(entry){
 return entry.kind==='issue'&&!entry.item.decision_context&&(entry.item.automation_failure==='missing_decision'||String(entry.item.decision?.question||'').startsWith('The automated pass could not resolve this and did not say what to decide.'));
}
function meaningfulFailureLine(text){
 const lines=String(text||'').split('\n').map(line=>line.trim()).filter(line=>line&&!line.startsWith('#')&&!line.startsWith('|')&&!line.startsWith('- ')&&!/^Read[: `]/i.test(line));
 return (lines.find(line=>/\b(stopping|stopped|root cause|concrete cause|no code change|implemented|commit|does not exist|not implemented)\b/i.test(line))||lines.find(line=>line.length>35)||lines[0]||'').replace(/^[*\d.\s]+|[*\s]+$/g,'');
}
function automationFailureCard(entry){
 const issue=entry.item,askedAt=Date.parse(issue.last_word_at||'');
 const node=el('article','card attention-choice');
 node.append(el('h3','',`${entry.repo} #${issue.number} · ${issue.title}`),el('p','muted',`Unexplained pass${Number.isFinite(askedAt)?` · ${formatAge(Date.now()-askedAt)}`:''}`));
 const receipt=el('p','muted','Checking earlier work and failure receipt…');node.append(receipt);
 const actions=el('div','actions');actions.append(button('Inspect history or add guidance',()=>githubDetail(entry.repo,issue,'issues')),link('Open on GitHub',issue.url||`https://github.com/${entry.repo}/issues/${issue.number}`));node.append(actions);
 api(`/api/github/detail?repo=${encodeURIComponent(entry.repo)}&number=${issue.number}&kind=issues`).then(data=>{
  if(!node.isConnected)return;
  const comments=(data.comments||[]).filter(row=>!String(row.body||'').includes('The automated pass could not resolve this and did not say what to decide.')&&!String(row.body||'').includes('<!-- office-request:'));
  const meaningful=comments.at(-1),lead=meaningfulFailureLine(meaningful?.body||'');
  receipt.textContent=lead?`Last substantive report (${formatAge(Date.now()-Date.parse(meaningful.created_at))}): ${lead.slice(0,350)}`:'No substantive run receipt found in the available issue comments. Inspect the history before retrying.';
 }).catch(error=>{if(node.isConnected)receipt.textContent=`Issue history unavailable: ${error.message}. Inspect on GitHub before retrying.`;});
 return node;
}
const layoutObserver=new ResizeObserver(()=>{document.documentElement.style.setProperty('--tabs-height',`${$('.tabs').getBoundingClientRect().height}px`);document.documentElement.style.setProperty('--player-height',`${$('#player').getBoundingClientRect().height}px`);});layoutObserver.observe($('.tabs'));layoutObserver.observe($('#player'));

$('#detail-attention').addEventListener('click',()=>attentionList(sheet('Needs you')));
$('#detail-search').addEventListener('click',()=>{clearDetail();$('#global-search').focus();});

async function restoreProject(id){
 const reference=id.startsWith('{')?JSON.parse(id):{repo:id};
 const [data,local]=await Promise.all([world(),api('/api/projects')]);
 const desk=projectDesks(data,local.items).find(row=>reference.root?row.root===reference.root:row.repo===reference.repo);
 if(!desk)throw Error('Project unavailable');return project(desk);
}

async function botArchives(){const data=await api('/api/bots/archives');const body=sheet('Retained office conversations');for(const item of data.items)body.append(card(item.name,new Date(item.modified*1000).toLocaleString(),()=>botHistory(item.id)));}
async function botHistory(id){
 rememberDetail('bot-history',id);const data=await api(`/api/bots/history?id=${encodeURIComponent(id)}&offset=-1`);const body=sheet(data.name);
 const turns=el('div','stack');
 const render=(parent,page)=>{for(const item of page.items)parent.append(card(item.role||'Record',item.content||JSON.stringify(item)));for(const error of page.errors)parent.append(el('p','error',`${error.error} at byte ${error.offset}`));};
 const earlier=button('Load earlier messages',async()=>{
  const page=await api(`/api/bots/history?id=${encodeURIComponent(id)}&offset=${earlier.dataset.offset}`);
  const fragment=el('div');render(fragment,page);const height=body.scrollHeight;
  turns.prepend(...fragment.childNodes);body.scrollTop+=body.scrollHeight-height;
  earlier.dataset.offset=page.previous_offset;earlier.hidden=page.previous_offset===null;
 },'transcript-earlier');
 earlier.dataset.offset=data.previous_offset;earlier.hidden=data.previous_offset===null;
 body.append(turns);render(turns,data);transcriptNavigation(body,turns).append(earlier);
}

function showSearchCoverage(parent,coverage){
 const details=el('details','card');details.append(el('summary','','What search can see'));
 const groups=new Map();for(const source of coverage.sources||[]){if(!groups.has(source.kind))groups.set(source.kind,[]);groups.get(source.kind).push(source);}
 for(const [kind,rows] of groups)coverageGroup(details,kind,rows,source=>`${source.source}: ${source.indexed} indexed · ${source.coverage} · ${source.state}${source.total===0?' · 0 retained':''}`);
 coverageGroup(details,'GitHub collection',coverage.github||[],repo=>`${repo.repo} · ${repo.state} · ${repo.indexed}/${repo.fetched} records${repo.error?' · '+repo.error:''}`);
 parent.append(details);
}
function coverageGroup(parent,title,rows,describe){
 const group=el('details'),pane=el('div');group.append(el('summary','',`${title} · ${rows.length} sources`),pane);parent.append(group);let loaded=false;
 function page(start){pane.replaceChildren(el('p','muted',`${start+1}–${Math.min(start+40,rows.length)} of ${rows.length}`));for(const row of rows.slice(start,start+40))pane.append(el('p','muted',describe(row)));
  for(const [label,next] of [['Previous sources',start-40],['More sources',start+40]]){if(next<0||next>=rows.length)continue;pane.append(button(label,()=>{page(next);group.scrollIntoView({block:'start'});}));}
 }
 group.addEventListener('toggle',()=>{if(group.open&&!loaded){loaded=true;if(rows.length)page(0);else pane.append(el('p','muted','No declared sources.'));}});
}

async function githubAction(payload,onConfirmed=()=>{}){
 const key='office-github-pending:'+JSON.stringify([payload.action,payload.repo,payload.number||'new']);
 const pending=JSON.parse(localStorage.getItem(key)||'null')||{...payload,request_id:crypto.randomUUID()};localStorage.setItem(key,JSON.stringify(pending));
 let receipt;
 try{receipt=await api('/api/github/command',pending);}
 catch(error){if([400,403,404,409,413,422].includes(error.status))localStorage.removeItem(key);throw error;}
 if(receipt.state==='rejected')localStorage.removeItem(key);
 if(receipt.state==='unconfirmed')githubRecovery(key,pending,receipt);
 if(receipt.state!=='confirmed')throw Error(receipt.error||'GitHub has not confirmed this action. Inspect the source before submitting another.');
 localStorage.removeItem(key);onConfirmed(pending);notice(`Confirmed by GitHub as ${receipt.acting_identity}`);return receipt.result;
}
function createIssue(repo){
 const body=sheet('New issue · '+repo);const key='office-issue-draft:'+repo;const draft=JSON.parse(localStorage.getItem(key)||'{}');
 const title=field(body,'Title',el('input')),description=field(body,'Description',el('textarea'));title.value=draft.title||'';description.value=draft.body||'';
 for(const input of [title,description])input.addEventListener('input',()=>localStorage.setItem(key,JSON.stringify({title:title.value,body:description.value})));
 body.append(button('Create issue',async()=>{const result=await githubAction({action:'create',repo,title:title.value,body:description.value});localStorage.removeItem(key);await githubDetail(repo,result,'issues');},'primary'));
}
function reviewChange(repo,number,head){
 const body=sheet('Review change');body.append(el('p','muted','Reviewed commit '+head));
 const event=field(body,'Review',select([['COMMENT','Comment'],['APPROVE','Approve'],['REQUEST_CHANGES','Request changes']],'COMMENT'));
 const message=field(body,'Review notes',el('textarea'));const key=`office-review-draft:${repo}:${number}:${head}`;message.value=localStorage.getItem(key)||'';message.addEventListener('input',()=>localStorage.setItem(key,message.value));
 body.append(button('Submit review',async()=>{await githubAction({action:'review',repo,number,head,event:event.value,body:message.value});localStorage.removeItem(key);await githubDetail(repo,{number},'prs');},'primary'));
}
function editLabels(repo,number,labels){
 const body=sheet('Issue labels');const input=field(body,'Labels, separated by commas',el('input'));input.value=labels.map(label=>typeof label==='string'?label:label.name).join(', ');
 body.append(button('Save labels',()=>githubAction({action:'labels',repo,number,labels:input.value.split(',').map(label=>label.trim()).filter(Boolean)}),'primary'));
}

async function githubTimeline(repo,number,cursor=1,parent=null){
 const data=await api(`/api/github/timeline?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}`);const body=parent||sheet('Issue timeline');
 for(const item of data.items)body.append(card(item.event||'Comment',`${item.actor?.login||item.user?.login||''} · ${item.created_at||''}\n${item.body||JSON.stringify(item)}`));
 if(data.next_cursor)body.append(button('Older timeline entries',()=>githubTimeline(repo,number,data.next_cursor,body)));
}
async function githubChecks(parent,repo,head,cursor=1,kind='checks'){
 const data=await api(`/api/github/checks?repo=${encodeURIComponent(repo)}&head=${head}&cursor=${cursor}&kind=${kind}`);
 for(const check of data.items){const node=card(check.name||check.context,check.conclusion||check.state||check.status);const url=check.html_url||check.target_url;if(url)node.append(link('Open check',url));parent.append(node);}
 if(data.next_cursor)parent.append(button('More checks',()=>githubChecks(parent,repo,head,data.next_cursor,kind)));
 if(kind==='checks'&&cursor===1)await githubChecks(parent,repo,head,1,'statuses');
}

async function podcastNotifications(parent,cursor=userState.value('seen','podcasts')||0){
 const data=await api(`/api/system/notifications?cursor=${cursor}`);
 for(const item of data.items)parent.append(card('New episode · '+item.payload.title,`${Math.round(item.payload.duration_s/60)} minutes`,()=>mediaDetail(item.payload.media_id)));
 if(data.items.length)userState.record('seen','podcasts',data.items.at(-1).id);
 if(data.next_cursor!==null)parent.append(button('More updates',()=>podcastNotifications(parent,data.next_cursor)));
}

function githubRecovery(key,pending,receipt){
 const body=sheet('Check GitHub delivery');
 body.append(el('p','',receipt.error||'The connection ended before GitHub confirmed this action.'),el('p','muted','This check reads GitHub and never repeats the write.'));
 body.append(button(receipt.next_page?'Check next page':'Check current outcome',async()=>{
  const found=await api('/api/github/reconcile',{request_id:pending.request_id,page:receipt.next_page||1});
  if(found.state!=='confirmed'){githubRecovery(key,pending,found);return;}
  localStorage.removeItem(key);body.replaceChildren(el('p','','Outcome confirmed from GitHub.'));
  if(found.result?.html_url)body.append(link('Open confirmed result',found.result.html_url));
 }));
}

async function activitySinceVisit(parent){
 const since=userState.value('seen','board-visit')||0;
 const view=el('div','stack');parent.append(view);
 const data=await activityPage(view,since);
 if(data.observed_at&&view.isConnected)userState.record('seen','board-visit',data.observed_at);
 parent.append(button('Browse all activity',()=>{const body=sheet('All activity');return activityPage(body,0);}));
}
async function activityPage(parent,since,cursor=''){
 const query=new URLSearchParams({limit:'10',since:String(since),cursor});
 const data=await api('/api/board?'+query);
 if(!['ok','never'].includes(data.state))throw Error(data.detail||'Activity unavailable');
 const visible=data.posts.filter(item=>!since||item.unreadable||!userState.value('seen','board-post:'+JSON.stringify([item.account,item.id])));
 for(const item of visible){parent.append(card(item.text,`${item.account} · ${item.age}`,()=>{const body=sheet(item.text);body.append(markdownView(item.body));}));if(parent.isConnected&&!item.unreadable)userState.record('seen','board-post:'+JSON.stringify([item.account,item.id]),data.observed_at||Date.now()/1000);}
 if(!visible.length)empty(parent,data.next_cursor?'Continue to check more activity.':since?'No more new activity since your last visit.':'No activity published yet.');
 if(data.next_cursor){const more=button('More activity',async()=>{await activityPage(parent,since,data.next_cursor);more.remove();});parent.append(more);}
 return data;
}

async function projectTasks(parent,desk,outputs=false,cursor=0){
 if(!cursor)parent.replaceChildren();
 const data=await api(`/api/tasks?project=${encodeURIComponent(desk.root)}&cursor=${cursor}`);
 for(const item of data.items){
  if(outputs&&!item.flight)continue;
  parent.append(card(item.title,`${item.specification.engine} · ${item.specification.profile} · ${item.phase}`,()=>outputs?flightDetail(item.flight.id):taskDetail(item.id)));
 }
 if(!data.items.length)empty(parent,outputs?'No Office task outputs for this checkout yet.':'No Office conversations for this checkout yet.');
 if(data.next_cursor!==null){const more=button('Older work',async()=>{await projectTasks(parent,desk,outputs,data.next_cursor);more.remove();});parent.append(more);}
}
async function projectAgents(parent,desk){
 parent.replaceChildren();
 const sessionsView=section(parent,'Addressable agents');
 await guarded(sessionsView,async()=>{
  const data=await api('/api/sessions');if(!['ok','empty'].includes(data.state))throw Error(data.detail||data.state);
  const rows=data.sessions.filter(item=>item.directory===desk.checkoutPath);
  for(const item of rows)sessionsView.append(card(item.name,`${item.tool} · ${item.status}`,()=>conversation(item)));
  if(!rows.length)empty(sessionsView,'No addressable agent in this checkout directory.');
 });
 const observed=section(parent,'Observed processes');
 await guarded(observed,async()=>{const data=await api('/api/live');if(data.state==='unreadable')throw Error(data.detail);const rows=data.sessions.filter(item=>item.cwd===desk.checkoutPath);for(const item of rows)observed.append(card(`${item.engine} · PID ${item.pid}`,'Observed in this checkout directory',()=>observedProcess(item)));if(!rows.length)empty(observed,'No observed process in this checkout directory.');});
 parent.append(button('Office tasks in isolated execution folders',()=>projectTasks(parent,desk)));
}

function projectPanel(parent,render){const pane=el('div','stack');parent.replaceChildren(pane);return guarded(pane,()=>render(pane));}

async function restoreBot(id){
 const route=location.href;const data=await api('/api/bots');if(location.href!==route)return;const bot=data.bots.find(item=>item.id===id);
 if(!bot)throw Error('This office voice is no longer available');
 return botConversation(bot);
}
async function restoreConversation(id){
 const route=location.href;const saved=JSON.parse(id);const data=await api('/api/sessions');if(location.href!==route)return;
 const session=data.sessions.find(item=>saved.session_id?item.session_id===saved.session_id:item.name===saved.name&&item.started_at===saved.started_at&&item.directory===saved.directory);
 if(!session)throw Error('This session is no longer addressable. Its retained history is available in Work.');
 return conversation(session);
}

async function githubContext(reference){
 const route=location.href;const data=await api('/api/github/context',reference);
 if(location.href!==route)return;
 const revision=data.attachment.revision;
 return newTask(reference.repo,{id:data.attachment.id,revision,project:reference.repo,path:reference.path||`pull request #${reference.number}`});
}

function githubLineSelection(parent,reference,text){
 const box=el('details','card');box.append(el('summary','','Ask about selected lines'));parent.append(box);
 if(!text){box.append(el('p','muted','This file has no lines to select.'));return;}
 const source=el('details');source.append(el('summary','','Numbered source'));box.append(source);let rendered=false;
 source.addEventListener('toggle',()=>{if(source.open&&!rendered){source.append(numberedSource({text,line_start:1}));rendered=true;}});
 const count=text.split('\n').length-(text.endsWith('\n')?1:0);const fields=[];
 for(const [label,value] of [['First line',1],['Last line',Math.max(1,count)]]){const input=el('input');input.type='number';input.min=1;input.max=count;input.value=value;fields.push(field(box,label,input));}
 box.append(button('Ask an agent about these lines',()=>githubContext({...reference,start_line:Number(fields[0].value),end_line:Number(fields[1].value)})));
}

function githubDiff(body,repo,number,data){
 const details=el('details'),pane=el('div');details.append(el('summary','','Full diff'),pane);body.append(details);
 const show=page=>{pane.replaceChildren(el('p','muted',`${page.diff_offset||0}–${(page.diff_offset||0)+(page.diff||'').length} of ${page.diff_total||0} characters`),el('pre','',page.diff||'No textual diff'));
  for(const [label,cursor] of [['Previous section',page.diff_previous_cursor],['Next section',page.diff_next_cursor]]){if(cursor==null)continue;const control=button(label,async()=>{control.disabled=true;try{const query=new URLSearchParams({repo,number,head:data.head.sha,base:data.base.sha,cursor});const next=await api('/api/github/diff?'+query);if(!details.isConnected)return;show(next);details.scrollIntoView({block:'start'});}finally{control.disabled=false;}});pane.append(control);}
 };show(data);
}
