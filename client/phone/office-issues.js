import {$,api,el,button,section,card,empty,field,select,notice} from './office-ui.js';
// Every open issue across TBS, Matra and Tower, with the denominator that makes a total honest.
const STATES=[['','Any state'],['untriaged','Untriaged (no labels)'],['ready','Ready'],['active','Tower attempting'],['needs_you','Needs you'],['held','Held'],['owned','Owned'],['in_pr','In PR'],['labeled','Labeled, not queued']];
const SORTS=[['priority','Priority'],['updated','Recently updated'],['idle','Least recently updated'],['age','Oldest'],['repo','Repository'],['number','Issue number']];
const RANK={p0:0,p1:1,p2:2};
const ORDER={priority:(a,b)=>(RANK[a.priority]??3)-(RANK[b.priority]??3)||(b.age_days-a.age_days),updated:(a,b)=>b.updated_at.localeCompare(a.updated_at),idle:(a,b)=>a.updated_at.localeCompare(b.updated_at),age:(a,b)=>a.created_at.localeCompare(b.created_at),repo:(a,b)=>a.repo.localeCompare(b.repo)||a.number-b.number,number:(a,b)=>b.number-a.number};
export async function issueInventory(parent,open,fresh=false){
 const data=await api('/api/github/inventory'+(fresh?'?fresh=1':''));
 parent.append(el('p','muted',`Observed ${data.generated_at}${data.refreshing?' · refreshing in the background':''} · ${data.source}`));
 if(data.registry_error)parent.append(el('p','error',data.registry_error));
 parent.append(button('Refresh from GitHub',async()=>{parent.replaceChildren();await issueInventory(parent,open,true);}));
 const filters={group:'',repo:''};
 const groups=el('div','grid');parent.append(groups);
 for(const group of data.groups)groups.append(groupCard(group,repo=>{filters.group=group.name;filters.repo=repo;draw();}));
 const list=section(parent,'Open issues');
 const search=field(list,'Search',el('input'));search.type='search';
 const state=field(list,'State',select(STATES,''));const sort=field(list,'Sort',select(SORTS,'priority'));
 const scope=el('p','muted');const rows=el('div','stack');list.append(scope,rows);
 let limit=60;
 function draw(){
  const q=search.value.toLowerCase();
  const matches=data.issues.filter(item=>(!filters.group||item.groups.includes(filters.group))&&(!filters.repo||item.repo===filters.repo)&&(!state.value||item.state===state.value)&&(!q||`${item.repo}#${item.number} ${item.title} ${item.labels.join(' ')}`.toLowerCase().includes(q))).sort(ORDER[sort.value]);
  scope.replaceChildren(el('span','',`${matches.length} of ${data.issues.length} issues${filters.group?` · ${filters.group}`:''}${filters.repo?` · ${filters.repo}`:''} `));
  if(filters.group||filters.repo)scope.append(button('Show all',()=>{filters.group='';filters.repo='';draw();}));
  rows.replaceChildren();for(const item of matches.slice(0,limit))rows.append(issueCard(item,open));
  if(!matches.length)empty(rows,'No open issue matches.');
  if(matches.length>limit)rows.append(button(`Show all ${matches.length}`,()=>{limit=matches.length;draw();}));
 }
 for(const input of [search,state,sort])input.addEventListener('input',draw);draw();
}
function groupCard(group,pick){
 const node=el('article','card');node.append(el('h3','',group.denominator),el('p','muted',group.source));
 const counts=Object.entries(group.counts).map(([key,value])=>`${value} ${key.replace('_',' ')}`).join(' · ');
 node.append(el('p','',`${counts||'no open issues'} · ${group.stale} untouched 30+ days`));
 if(group.oldest)node.append(el('p','muted',`Oldest: ${group.oldest.repo}#${group.oldest.number} · ${group.oldest.age_days} days · ${group.oldest.title}`));
 const repos=el('details');repos.append(el('summary','',`Per repository (${group.repos_configured})`));
 for(const row of group.repos)repos.append(button(`${row.repo} · ${row.open??'unknown'} open · ${row.status}${row.error?` · ${row.error}`:''}${row.status!=='fresh'&&row.fetched_at?` · as of ${row.fetched_at}`:''}`,()=>pick(row.repo),'link-row'));
 node.append(repos);
 const outside=[...group.excluded.map(row=>`${row.repo}: ${row.reason}`),...(group.registry_only||[]).map(repo=>`${repo}: in registry, not in the org`),...(group.org_only||[]).map(repo=>`${repo}: in the org, missing from registry (counted)`)];
 if(outside.length){const away=el('details');away.append(el('summary','',`Not counted or mismatched (${outside.length})`));for(const line of outside)away.append(el('p','muted',line));node.append(away);}
 node.append(button(`Show ${group.name} issues`,()=>pick('')));
 return node;
}
function issueCard(item,open){
 const tower=item.tower?` · Tower: ${item.tower.state}${item.tower.detail?` (${item.tower.detail})`:''}`:'';
 const facts=[item.state.replace('_',' '),item.priority,item.labels.filter(label=>!(label.toLowerCase() in RANK)).join(', '),`${item.age_days}d old`,`updated ${item.idle_days}d ago`,`${item.comments} comments`].filter(Boolean).join(' · ');
 return card(`${item.repo}#${item.number} ${item.title}`,facts+tower,()=>open(item.repo,item));
}
// Triage on one issue, every write through the receipt-backed GitHub door.
export function issueControls(body,repo,data,act,reload){
 const labels=(data.labels||[]).map(label=>typeof label==='string'?label:label.name);
 const box=section(body,'Triage');
 const bump=el('div','stack');box.append(bump);bumpControls(bump,repo,data.number,act,reload);
 const name=field(box,'Label',el('input'));name.placeholder='label name';
 box.append(button('Add label',async()=>{await act({action:'label_add',repo,number:data.number,labels:[name.value.trim()]});await reload();}));
 for(const label of labels)box.append(button(`Remove ${label}`,async()=>{await act({action:'label_remove',repo,number:data.number,labels:[label]});await reload();}));
 box.append(button('Edit title and description',()=>editIssue(repo,data,act,reload)));
}
async function bumpControls(parent,repo,number,act,reload){
 const priority=field(parent,'Priority',select([['p0','p0'],['p1','p1'],['p2','p2']],'p1'));
 const verdict=el('p','muted','Checking whether Tower would select this…');parent.append(verdict);
 const go=button('Bump into Tower queue',async()=>{await act({action:'bump',repo,number,priority:priority.value});await reload();},'primary');parent.append(go);
 async function check(){try{const answer=await api(`/api/github/bump-check?repo=${encodeURIComponent(repo)}&number=${number}&priority=${priority.value}`);verdict.textContent=answer.eligible?`Tower would select this: adds ${['ready',priority.value].join(', ')}`:`Bump refused: ${answer.reason}`;go.hidden=!answer.eligible;}catch(error){verdict.textContent=error.message;go.hidden=true;}}
 priority.addEventListener('change',check);await check();
}
function editIssue(repo,data,act,reload){
 const body=el('div','stack');$('#detail-body').append(body);
 const title=field(body,'Title',el('input'));title.value=data.title||'';
 const text=field(body,'Description',el('textarea'));text.value=data.body||'';
 body.append(button('Save to GitHub',async()=>{const change={action:'edit',repo,number:data.number};if(title.value!==data.title)change.title=title.value;if(text.value!==(data.body||''))change.body=text.value;if(Object.keys(change).length===3){notice('Nothing changed');return;}await act(change);await reload();},'primary'));
 title.focus();
}
