/* Prototype interactions only. Every answer and post is an explicitly dated example. */
const demoWorldPosts = [
  {id:'world-pool',topic:'Politics',by:'Sep 22 briefing · US',title:'A press access fight widened to the TV pool',body:'Four networks suspended participation in presidential TV pool coverage after CNN was excluded. The networks objected to access restrictions; the White House disputed their reporting.',source:'https://au.variety.com/2026/tv/global/tv-networks-suspend-white-house-pool-protest-trump-cnn-ban-40556/',sourceLabel:'Variety · network statement',sources:[['Variety · report and network statement','https://au.variety.com/2026/tv/global/tv-networks-suspend-white-house-pool-protest-trump-cnn-ban-40556/']],region:'United States',edition:'Morning briefing · Sep 22'},
  {id:'world-srilanka',topic:'World',by:'Sep 22 briefing · South Asia',title:'Verdicts in Sri Lanka’s Easter bombings case',body:'A court found fifteen men guilty in a case about planning or supporting the 2019 attacks. It is separate from the case concerning former officials and intelligence warnings.',source:'https://apnews.com/article/45283b5f6fe37ed2ab07c903214001b5',sourceLabel:'Associated Press',sources:[['Associated Press · court report','https://apnews.com/article/45283b5f6fe37ed2ab07c903214001b5']],region:'South Asia',edition:'Morning briefing · Sep 22'},
  {id:'world-south-sudan',topic:'Politics',by:'Sep 22 briefing · Africa',title:'South Sudan changed rules ahead of its planned vote',body:'A new law removes the need to finish a constitution and census before the planned election. Opposition groups objected to the process; voter registration had not begun when reported.',source:'https://www.streetinsider.com/Reuters/South%2BSudans%2Bpresident%2Bsigns%2Belection%2Blaw%2Bchanges%2Bahead%2Bof%2BDecember%2Bvote/27086658.html',sourceLabel:'Reuters report',sources:[['Reuters · election law','https://www.streetinsider.com/Reuters/South%2BSudans%2Bpresident%2Bsigns%2Belection%2Blaw%2Bchanges%2Bahead%2Bof%2BDecember%2Bvote/27086658.html'],['Eye Radio · government response on aid dispute','https://www.eyeradio.org/article/s-sudan-govt-responds-to-us-concerns-over-humanitarian-operations']],region:'Africa',edition:'Morning briefing · Sep 22'}
];
feedPosts.unshift(...demoWorldPosts);
feedPosts.find(p=>p.id==='heat-amoeba').format='figure';
feedPosts.find(p=>p.id==='world-south-sudan').format='timeline';
feedPosts.find(p=>p.id==='podcast-post').format='listen';
const demoNewsState={mode:'For you'};
try{state.saved=new Set(JSON.parse(localStorage.getItem('officePrototypeSaved')||'[]'))}catch{}
const demoFeedback={votes:{},replies:{}};
try{Object.assign(demoFeedback,JSON.parse(localStorage.getItem('officePrototypeFeedback')||'{}'))}catch{}
function saveDemoFeedback(){localStorage.setItem('officePrototypeFeedback',JSON.stringify(demoFeedback))}
const originalRender=render;
render=function(){
  if(state.view==='ask'){
    const parent=$('#main');parent.replaceChildren();$('#breadcrumb').textContent='Ask';
    document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('selected',b.dataset.view==='ask'));
    askPage(parent);
  }else originalRender();
};
const originalHome=home;
home=function(parent){
  originalHome(parent);
  const ask=node('section','ask-watch-card');
  ask.append(node('span','ask-watch-eyebrow','YOUR OFFICE MANAGER'),node('h2','','Ask once. Office checks the work.'),node('p','','Status, blockers, instructions, and follow-through in one conversation.'));
  const prompts=node('div','ask-watch-actions');
  prompts.append(button('What are they doing?',()=>openOfficeChat('What are the coordinators doing?'),'primary'),button('Ask anything →',()=>openOfficeChat(),'quiet-link'));
  ask.append(prompts);
  parent.querySelector('.decision-card').after(ask);
};
work=function(parent){
  heading(parent,'Feed','The world, AI, and your work in one place.');
  const status=node('div','feed-status');status.append(node('span','feed-live-dot'),node('span','', 'Archive preview · Sep 22 source stories · no live news polling'));
  parent.append(status);
  const tabs=node('div','feed-modes');
  ['For you','Latest','Politics','World','AI','Science','Culture','Work','Listen','Saved'].forEach(mode=>tabs.append(button(mode,()=>{demoNewsState.mode=mode;render()},'feed-mode'+(demoNewsState.mode===mode?' selected':''))));
  parent.append(tabs);
  if(['World','Politics'].includes(demoNewsState.mode)){
    const context=node('div','coverage-context');
    context.append(node('strong','','World desk'),node('span','','A developing event appears once, with multiple publications and perspectives inside it. The live build will update stories throughout the day.'));
    parent.append(context);
  }
  const category=post=>post.topic==='AI & research'?'AI':post.topic==='Discoveries'?'Science':post.topic==='History & ideas'?'Culture':post.topic;
  const posts=feedPosts.filter(post=>['For you','Latest'].includes(demoNewsState.mode)||demoNewsState.mode==='World'&&['Politics','World'].includes(category(post))||category(post)===demoNewsState.mode||demoNewsState.mode==='Saved'&&state.saved.has(post.id));
  const stream=node('div','feed-stream');posts.forEach(post=>stream.append(officePost(post)));parent.append(stream);
  if(!posts.length)parent.append(node('p','empty','No posts here yet. Save a story to find it again.'));
};
function officePost(post){
  const card=node('article','feed-entry post');const meta=node('div','feed-entry-head');
  meta.append(node('span','feed-kind',post.topic==='World'?post.region:post.topic),node('span','feed-owner',post.by));
  card.append(meta,node('h2','',post.title));
  if(post.format==='figure'){
    const figure=node('figure','post-figure');figure.append(node('strong','','63°C'),node('figcaption','','Observed reproduction temperature · NASA report linked below'));
    card.append(figure);
  }
  if(post.format==='timeline'){
    const timeline=node('div','post-timeline');timeline.append(node('span','','Law changed'),node('span','','Voter registration still pending'));
    card.append(timeline);
  }
  if(post.format==='listen'){
    const listen=button('▶  Listen to the episode',()=>openDetail(post.item),'post-listen');
    listen.append(node('span','audio-bars','▁▃▆▄▂▅▇▃▂▄▆▃▁'));
    card.append(listen);
  }
  card.append(node('p','',post.body));
  const actions=node('div','post-actions');
  if(post.sources)actions.append(button(`${post.sources.length} source${post.sources.length===1?'':'s'} · context →`,()=>openStory(post),'quiet-link'));
  else if(post.source){const link=node('a','quiet-link',post.sourceLabel+' ↗');link.href=post.source;link.target='_blank';link.rel='noopener noreferrer';actions.append(link)}
  if(post.item)actions.append(button(post.item.kind==='issue'?'Open issue →':post.topic==='Listen'?'Open episode →':'Open detail →',()=>openDetail(post.item),'quiet-link'));
  actions.append(postIcon('save',state.saved.has(post.id),()=>{state.saved.has(post.id)?state.saved.delete(post.id):state.saved.add(post.id);localStorage.setItem('officePrototypeSaved',JSON.stringify([...state.saved]));render()}));
  actions.append(postIcon('appreciate',demoFeedback.votes[post.id]==='up',()=>{demoFeedback.votes[post.id]=demoFeedback.votes[post.id]==='up'?null:'up';saveDemoFeedback();render()}));
  actions.append(postIcon('dislike',demoFeedback.votes[post.id]==='down',()=>{demoFeedback.votes[post.id]=demoFeedback.votes[post.id]==='down'?null:'down';saveDemoFeedback();render()}));
  actions.append(postIcon('reply',!!demoFeedback.replies[post.id]?.length,()=>openReply(post)));
  card.append(actions);return card;
}
function postIcon(kind,active,fn){
  const labels={save:active?'Remove from saved':'Save story',appreciate:active?'Remove appreciation':'Appreciate story',dislike:active?'Remove dislike':'Dislike story',reply:'Reply to story'};
  const label=labels[kind];
  const b=button('',fn,'post-icon'+(active?' active':''));b.setAttribute('aria-label',label);b.title=label;b.setAttribute('aria-pressed',String(active));
  const icons={save:'<path d="M6 4.5h12v15l-6-4-6 4z"/>',appreciate:'<path d="M12 20s-8-4.8-8-10.6a4.4 4.4 0 0 1 8-2.5 4.4 4.4 0 0 1 8 2.5C20 15.2 12 20 12 20z"/>',dislike:'<path d="M8 3h10v11h-5l-2 6-3-1v-5H5V7z"/>',reply:'<path d="M4 5h16v11H9l-5 4z"/>'};
  b.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+icons[kind]+'</svg>';
  return b;
}
function openReply(post){
  $('#detail-heading').textContent='Reply to post';const content=$('#detail-content');content.replaceChildren();
  content.append(node('h2','story-title',post.title),node('p','detail-intro','Your reply is feedback for the future editorial system. This prototype saves it only in this browser.'));
  const form=node('form','reply-form');const input=node('textarea');input.placeholder='What did this miss, get wrong, or make you curious about?';input.required=true;
  const send=node('button','primary','Save reply');send.type='submit';form.append(input,send);content.append(form);
  const list=node('section','reply-list');list.append(node('h3','','Your replies'));
  (demoFeedback.replies[post.id]||[]).forEach(reply=>list.append(node('p','',reply.text)));content.append(list);
  form.addEventListener('submit',event=>{event.preventDefault();const value=input.value.trim();if(!value)return;(demoFeedback.replies[post.id]??=[]).push({text:value,at:new Date().toISOString()});saveDemoFeedback();$('#detail').close();toast('Reply saved in this prototype');render()});
  $('#detail').showModal();input.focus();
}
function openStory(post){
  $('#detail-heading').textContent='World story';const content=$('#detail-content');content.replaceChildren();
  content.append(node('div','story-kicker',`${post.region} · ${post.edition}`),node('h2','story-title',post.title),node('p','detail-intro',post.body));
  const box=node('section','story-sources');box.append(node('h3','','Coverage & source material'));
  post.sources.forEach(([label,url])=>{const a=node('a','source-row',label+' ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';box.append(a)});
  content.append(box,node('p','story-note','This is an archived example, not a current breaking-news report. A live story will show publication time, last checked time, updates, corrections, and what remains uncertain.'));
  $('#detail').showModal();
}
const officePrompts=['What are the coordinators doing?','Did TBS finish #546?','Why is Matra blocked?','Tell TBS to prioritize #546'];
let officeHistory=[];
try{officeHistory=JSON.parse(localStorage.getItem('officePrototypeChat')||'[]');if(!Array.isArray(officeHistory))officeHistory=[]}catch{officeHistory=[]}
function saveOfficeHistory(){localStorage.setItem('officePrototypeChat',JSON.stringify(officeHistory.slice(-30)))}
function askPage(parent){
  const shell=node('section','ask-page');
  const thread=node('div','ask-thread');thread.id='ask-thread';shell.append(thread);
  const form=node('form','ask-compose');form.id='ask-form';
  const input=node('input');input.id='ask-input';input.autocomplete='off';input.setAttribute('aria-label','Ask Office');input.placeholder='Ask a question or give an instruction…';
  const send=node('button','primary','Send');send.type='submit';form.append(input,send);shell.append(form);parent.append(shell);
  form.addEventListener('submit',e=>{e.preventDefault();const q=input.value.trim();if(!q)return;input.value='';officeHistory.push({role:'you',text:q},{role:'office',...officeAnswer(q)});saveOfficeHistory();drawOfficeThread()});
  drawOfficeThread();
}
function openOfficeChat(seed){showView('ask');if(seed)$('#ask-input').value=seed;$('#ask-input').focus()}
function officeAnswer(q){
  const low=q.toLowerCase();
  if(/tell|ask|prioriti[sz]e|steer|send|assign/.test(low))return{text:'I can route that instruction to TBS. In this prototype, it stays a draft until you choose Send. The live version will show queued → read → acted on → outcome.',source:'TBS coordinator inbox · demo snapshot',action:'Send instruction',draft:q};
  if(/matra|block|xcode/.test(low))return{text:'Matra’s last run finished. The next phone proof was blocked by the build Mac’s Xcode setup in the sampled state. There is no active run in this snapshot.',source:'Matra coordinator · sampled Sep 23, 6:15 AM',target:'matra'};
  if(/546|finish|done|ship|close/.test(low))return{text:'I cannot call #546 finished from this sample. TBS had recent HomeClass commits, but its priority instruction was still unread. Open the exact issue and current checks before treating it as done.',source:'TBS coordinator · sampled Sep 23, 7:44 AM',issue:true};
  if(/coordinator|status|doing|working/.test(low))return{text:'TBS was running a HomeClass review; Matra was idle after a completed run and waiting on Xcode for phone proof. TBS had one unread priority instruction. These are dated samples, not live status.',source:'Coordinator snapshot · Sep 23, 7:44 AM',target:'tbs'};
  return{text:'This prototype can show the conversation shape, but it has no live Office manager behind it yet. The build must answer with current evidence, then offer a specific next action.',source:'Prototype only'};
}
function drawOfficeThread(){
  const thread=$('#ask-thread');thread.replaceChildren();
  if(!officeHistory.length){thread.append(node('p','ask-intro','Ask Office a question or give an instruction.'));
    const prompts=node('div','ask-prompts');officePrompts.forEach(q=>prompts.append(button(q,()=>{officeHistory.push({role:'you',text:q},{role:'office',...officeAnswer(q)});saveOfficeHistory();drawOfficeThread()},'ask-prompt')));thread.append(prompts)}
  officeHistory.forEach(entry=>{const bubble=node('article','ask-bubble '+entry.role);bubble.append(node('span','ask-role',entry.role==='you'?'You':'Office'),node('p','',entry.text));
    if(entry.source)bubble.append(node('small','ask-source','Source: '+entry.source));
    if(entry.target)bubble.append(button('Inspect '+entry.target.toUpperCase()+' →',()=>watchDetail(coordinators.find(c=>c.id===entry.target)),'quiet-link'));
    if(entry.issue)bubble.append(button('Open #546 →',()=>openDetail(feedPosts.find(p=>p.id==='work-age').item),'quiet-link'));
    if(entry.action)bubble.append(button(entry.action,()=>{entry.action='';entry.text='Instruction queued in this prototype. Delivery, read, and action are still unverified.';entry.source='Prototype receipt · no coordinator message sent';saveOfficeHistory();drawOfficeThread()},'ask-action'));
    thread.append(bubble)});
  thread.scrollTop=thread.scrollHeight;
}
if(location.hash==='#ask')state.view='ask';
render();
window.addEventListener('hashchange',()=>{if(location.hash==='#ask'&&state.view!=='ask'){state.view='ask';render()}});
