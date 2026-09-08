import * as userState from './office-state.js';
import {attachments} from './office-attachments.js';
import {restoreDraft,transcriptNavigation,rememberDetail,clearDetail,backDetail,$,api,el,button,sheet,section,card,intro,empty,failure,field,select,notice,link} from './office-ui.js';
import {loadSettings,settings} from './office-settings.js';
import {mediaList,mediaDetail} from './office-media.js';
import {browse,openFile} from './office-files.js';
import {markdownView} from './office-markdown.js';
import {newTask,taskList,permissions,taskDetail,permissionCard} from './office-tasks.js';
let attention={items:[],errors:[]};
let snapshot=null,snapshotReadAt=0;
async function world(){if(!snapshot||Date.now()-snapshotReadAt>15000){const data=await api('/api/world');snapshot=data.world;snapshotReadAt=Date.now();}return snapshot;}
async function guarded(parent,work){try{await work();}catch(error){failure(parent,error);}}
function subtitle(data){return [data.state,data.detail,data.at].filter(Boolean).join(' · ');}
async function today(parent){
 intro(parent,'Your office, wherever you are','A little room to think.','The Mac holds the work. Everything you need to see and steer lives here.');
 const needs=section(parent,'Needs you');needs.id='needs-list';await guarded(needs,()=>attentionList(needs));
 const active=section(parent,'Running now');await runningNow(active);
 const recent=section(parent,'Since you were here');await guarded(recent,()=>podcastNotifications(recent));await guarded(recent,()=>activitySinceVisit(recent));
 const listen=section(parent,'Something to listen to');await guarded(listen,async()=>{const data=await api('/api/media?kind=podcast');const episode=data.items[0];if(episode)listen.append(card(episode.title,`${Math.round(episode.duration_s/60)} minutes · ${episode.date}`,()=>mediaDetail(episode.id)));else empty(listen,'Your next episode will appear here when published.');});
 const poem=section(parent,'A small interruption');await guarded(poem,async()=>{const data=await api('/api/media?kind=substrate');const item=data.items[0];if(item)poem.append(card(item.title,item.excerpt,()=>mediaDetail(item.id)));else empty(poem,'No Substrate pieces published yet.');});
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
}
function projectDesks(data,local){
 const stations=data.stations||[];
 const rows=local.map(root=>({...stations.find(desk=>desk.repo===root.name),repo:root.name,root:root.id,folder:root.folder_id||btoa(JSON.stringify([root.id,''])).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,''),checkoutPath:root.path,taskCapable:root.task_capable!==false,label:root.label}));
 for(const desk of stations)if(!local.some(root=>root.name===desk.repo))rows.push(desk);
 return rows;
}
async function project(desk){
 rememberDetail('project',JSON.stringify({repo:desk.repo,root:desk.root}));
 const body=sheet(desk.repo);body.append(el('p','muted',desk.label||desk.detail||''));
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
 body.append(button('Ask an agent about this change',()=>githubContext({kind:'change',repo,number:item.number,head:data.head.sha,base:data.base.sha})),button('Reviews & inline discussion',()=>githubReviews(repo,item.number)),button('Review this change',()=>reviewChange(repo,item.number,data.head.sha)),button('Merge checked pipeline change',()=>githubAction({action:'merge',repo,number:item.number,head:data.head.sha})));body.append(el('p','muted','Merge requires a pipeline branch, current reviewed commit, passing verify check, and GitHub approval.'));const diff=el('details');diff.append(el('summary','','Full diff'),el('pre','',data.diff||'No textual diff'));body.append(diff);}
 body.append(button('Full timeline',()=>githubTimeline(repo,item.number)));
 body.append(button('Edit labels',()=>editLabels(repo,item.number,data.labels||[])));
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
  else{body.append(el('p','muted',`Blob ${data.object.sha}`),el('pre','',data.object.text));if(data.object.readable)body.append(button('Ask an agent about this file',()=>githubContext({kind:'file',repo,path,ref:revision,blob:data.object.sha})));if(data.object.html_url)body.append(link('Open source',data.object.html_url));}
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
 const health=section(parent,'Your Mac');await guarded(health,async()=>{const data=await api('/api/health');health.append(card(data.ok?'Connected':'Needs attention',`Serving ${data.revision?.slice(0,10)} · snapshot ${data.snapshot_at}`));});
 await guarded(health,async()=>{const data=await api('/api/system/machine');health.append(card('Machine',data.uptime),card('Memory',data.memory),card('Storage',`${(data.disk.free/1073741824).toFixed(1)} GB free`));});
 const observed=section(parent,'Other Mac agent processes');await guarded(observed,async()=>{const data=await api('/api/live');observed.append(el('p','muted',`Observed ${data.as_of}. Process visibility does not prove account or conversation identity.`));for(const item of data.sessions)observed.append(card(`${item.engine} · PID ${item.pid}`,`${item.cwd} · observed only`,()=>{const body=sheet('Observed Mac process');body.append(el('p','',`PID ${item.pid} · started ${item.started}`),el('p','muted','Control and transcript association are unproven. Open an exact retained conversation in Work.'));}));});
 const scheduleBox=el('details');scheduleBox.append(el('summary','','Machine schedules and services'));parent.append(scheduleBox);const existing=section(scheduleBox,'Registered jobs');await guarded(existing,async()=>{const data=await api('/api/system/jobs');if(data.state!=='ok')existing.append(el('p','error',data.detail||data.state));for(const job of data.jobs||[])existing.append(card(job.id,`${job.state} · ${job.schedule}`,()=>{const body=sheet(job.id);body.append(el('p','',job.detail),el('p','',`Last attempt: ${job.last_attempt||'none'} · exit ${job.last_rc??'unknown'}`),el('pre','',job.command),el('p','muted',job.note),button('Read log',()=>jobLog(job.id)),button('Run receipts',()=>jobReceipts(job.id)));}));});
 const plans=section(parent,'Nexus automations');await guarded(plans,async()=>{const data=await api('/api/system/plans');for(const plan of data.items)plans.append(card(plan.name,`${plan.enabled?'Enabled':'Paused'} · ${plan.kind}`,()=>planDetail(plan)));});
 const history=section(parent,'Run history');await guarded(history,()=>runs(history));
 await officeSections(parent,['flows','cost','webhook','care-grader']);
 parent.append(button('Settings',settings));
}
async function runs(parent,cursor=0){const data=await api(`/api/system/runs?cursor=${cursor}`);for(const item of data.items)parent.append(card(item.title||item.plan,`${item.state} · ${new Date(item.created_at*1000).toLocaleString()}`,()=>flightDetail(item.id)));if(data.next_cursor!==null)parent.append(button('Older runs',()=>runs(parent,data.next_cursor)));}
async function flightDetail(id){rememberDetail('flight',id);const data=await api(`/api/system/flight?id=${id}`);const body=sheet(data.title||id);body.append(el('p','muted',`${data.state} · ${data.plan} · attempt ${data.attempt}`));body.append(markdownView(data.objective||''));for(const action of (['failed','cancelled'].includes(data.state)?['retry']:['running','queued'].includes(data.state)?['cancel']:[]))body.append(button(action,()=>systemCommand(action,id)));const log=section(body,'Log');await flightLogs(log,id);const events=section(body,'Events');await eventPage(events,id);const artifacts=section(body,'Artifacts');for(const item of data.artifacts)artifacts.append(card(item.kind,item.ref,()=>{const view=sheet(item.ref);view.append(link('Open or download artifact',`/api/system/artifact?id=${encodeURIComponent(item.id)}`));const frame=el('iframe','preview');frame.src=`/api/system/artifact?id=${encodeURIComponent(item.id)}`;frame.setAttribute('sandbox','allow-scripts');frame.title=item.ref;view.append(frame);}));}
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
async function route(){
 const page=location.hash.slice(1)||'today';const views={today,work,library,system};const parent=$('#content');parent.replaceChildren();
 for(const item of document.querySelectorAll('.tabs a'))item.setAttribute('aria-current',item.hash===`#${page}`?'page':'false');
 const view=el('div');parent.append(view);await guarded(view,()=> (views[page]||today)(view));
}
$('#settings').addEventListener('click',settings);$('#new-task').addEventListener('click',()=>newTask().catch(error=>notice(error.message)));$('#close-detail').addEventListener('click',backDetail);
$('#search-go').addEventListener('click',()=>search());$('#global-search').addEventListener('keydown',event=>{if(event.key==='Enter')search();});window.addEventListener('hashchange',route);
$('#settings').disabled=false;$('#new-task').disabled=false;
async function connection(){try{const data=await api('/api/health');$('#connection').textContent=data.ok?'Mac connected':'Mac needs attention';}catch{$('#connection').textContent='Mac unreachable';}}
await userState.synchronize();await loadSettings().catch(error=>notice(error.message));await connection();await route();if(new URL(location.href).searchParams.has('detail')&&!$('#detail').open)await restoreFromURL();setInterval(connection,30000);

if('serviceWorker' in navigator)navigator.serviceWorker.register('/office-sw.js').catch(error=>notice('Offline shell unavailable: '+error.message));

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
 const refresh=async()=>{const data=await api(`/api/chat?bot=${encodeURIComponent(bot.id)}`);history.replaceChildren();for(const turn of data.turns||[]){const row=card(turn.role,turn.content||turn.text);for(const item of turn.attachments||[])if(item.office_id)row.append(link(item.name||'Attached file',`/api/uploads/content?id=${encodeURIComponent(item.office_id)}&revision=${item.revision}`));history.append(row);}};
 await refresh();if(!body.isConnected)return;transcriptNavigation(body,history);const message=field(body,'Message',el('textarea'));
 const clearMessage=restoreDraft(message,['bot',bot.id]);const key='office-bot-uploads:'+bot.id;
 const attached=attachments(body,JSON.parse(localStorage.getItem(key)||'[]'),items=>localStorage.setItem(key,JSON.stringify(items)));
 body.append(button('Send',async()=>{const payload={bot:bot.id,message:message.value,uploads:attached.references()};await api('/api/chat',payload);clearMessage(payload.message);const stored=JSON.parse(localStorage.getItem(key)||'[]').map(({id,revision})=>({id,revision}));if(JSON.stringify(stored)===JSON.stringify(payload.uploads)&&JSON.stringify(attached.references())===JSON.stringify(payload.uploads))attached.clear();notice('Agent turn queued');await refresh();},'primary'),button('Refresh conversation',refresh));
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

document.addEventListener('office-flight-detail',event=>flightDetail(event.detail).catch(error=>notice(error.message)));

async function githubCollection(repo,kind,cursor=1,parent=null){const body=parent||sheet(repo+' '+kind);const data=await api(`/api/github/collection?repo=${encodeURIComponent(repo)}&kind=${kind}&cursor=${cursor}`);for(const item of data.items)body.append(card(`#${item.number} ${item.title}`,item.state,()=>githubDetail(repo,item,kind)));if(data.next_cursor)body.append(button('Older items',()=>githubCollection(repo,kind,data.next_cursor,body)));}
async function githubReviews(repo,number,cursor=1,parent=null,inline=false){const body=parent||sheet(repo+' reviews');const data=await api(`/api/github/reviews?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}&inline=${inline}`);for(const item of data.items){body.append(commentView(item));if(item.diff_hunk)body.append(el('p','muted',`${item.path} · ${item.commit_id}`),el('pre','',item.diff_hunk));}if(data.next_cursor)body.append(button('More reviews',()=>githubReviews(repo,number,data.next_cursor,body,inline)));if(!parent)body.append(button('Inline comments',()=>githubReviews(repo,number,1,body,true)));}

async function refreshAttention(){
 const results=await Promise.allSettled([api('/api/tasks/permissions'),api('/api/gates'),world()]);
 const items=[],errors=[];
 for(const [index,result] of results.entries()){
  if(result.status==='rejected'){errors.push(result.reason.message);continue;}
  items.push(...attentionItems(index,result.value));
 }
 attention={items,errors};
 const badge=$('#detail-attention');badge.title=`${items.length} items need you`;badge.setAttribute('aria-label',`${items.length} items need you${errors.length?'; some sources unavailable':''}`);
 const label=$('.tabs a[href="#today"] span');label.textContent='Today';
 label.parentElement.setAttribute('aria-label','Today');label.parentElement.setAttribute('aria-description',`${items.length} items need you`);
 const node=$('#needs-list');if(node)drawAttention(node);
}
function drawAttention(parent){
 const existing=new Map([...parent.querySelectorAll('[data-attention-key]')].map(node=>[node.dataset.attentionKey,node]));
 const nodes=attention.errors.map(error=>el('p','error',error));
 if(!attention.items.length&&!attention.errors.length)nodes.push(el('p','empty','Nothing waiting for you.'));
 const shown=parent.id==='needs-list'?attention.items.slice(0,3):attention.items;
 for(const entry of shown){
  const key=`${entry.kind}:${entry.item.id}`;
  const node=entry.kind==='permission'?(existing.get(key)||permissionCard(entry.item)):attentionCard(entry);
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
 if(entry.kind==='gate')return card(entry.item.question||entry.item.title||'Pending decision',entry.item.repo,()=>gateDetail(entry.item));
 return card(entry.item.title,entry.repo,()=>githubDetail(entry.repo,entry.item,'issues'));
}
async function attentionList(parent){await refreshAttention();drawAttention(parent);}
setInterval(refreshAttention,10000);

function attentionItems(index,value){
 if(index===0)return value.items.map(item=>({kind:'permission',item}));
 if(index===1)return value.gates.map(item=>({kind:'gate',item}));
 const result=[];for(const desk of value.stations||[])for(const item of desk.issues||[])if(item.bot_last===true)result.push({kind:'issue',repo:desk.repo,item});return result;
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
 for(const source of coverage.sources||[])details.append(el('p','muted',`${source.source} · ${source.kind}: ${source.indexed} indexed · ${source.coverage}${source.state==='partial'?' · partial':''}`));
 for(const repo of coverage.github||[])details.append(el('p','muted',`${repo.repo} · GitHub ${repo.state} · ${repo.indexed}/${repo.fetched} records${repo.error?' · '+repo.error:''}`));
 parent.append(details);
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
