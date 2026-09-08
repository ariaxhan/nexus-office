import {openFile} from './office-files.js';
import {audioTransport} from './office-native.js';
import * as userState from './office-state.js';
import {rememberDetail,saveObject,$,api,el,button,sheet,section,card,empty,link,time,select,notice} from './office-ui.js';
import {prefs,setPlaybackSpeed} from './office-settings.js';
const audio=audioTransport($('#audio'));let current=null;let lastSave=0;
function positions(){try{return JSON.parse(localStorage.getItem('office-listening')||'{}');}catch{return {};}}
function remember(){if(!current||!prefs.remember)return;const data=positions();data[current.id]=audio.currentTime;userState.record('listening',current.id,audio.currentTime);localStorage.setItem('office-listening',JSON.stringify(data));}
function seek(delta){audio.currentTime=Math.min(audio.duration||Infinity,Math.max(0,audio.currentTime+delta));}
export async function play(episode){
 if(current?.id!==episode.id){remember();current=episode;audio.configure?.(episode);audio.src=episode.url;audio.playbackRate=prefs.speed;
 audio.addEventListener('loadedmetadata',()=>{audio.currentTime=prefs.remember?(userState.value('listening',episode.id)??positions()[episode.id]??0):0;},{once:true});}
 renderPlayer();await audio.play();mediaSession();
}
function mediaSession(){
 if(audio.isNative||!('mediaSession' in navigator))return;
 navigator.mediaSession.metadata=new MediaMetadata({title:current.title,artist:'Nexus Office',album:'Your private briefing'});
 const handlers={play:()=>audio.play(),pause:()=>audio.pause(),seekbackward:()=>seek(-15),seekforward:()=>seek(15),seekto:event=>audio.currentTime=event.seekTime};
 for(const [name,handler] of Object.entries(handlers))try{navigator.mediaSession.setActionHandler(name,handler);}catch{/* Older Safari omits some actions. */}
}
function renderPlayer(){
 const player=$('#player');player.hidden=!current||!prefs.mini;
 if(!current)return;
 player.replaceChildren();const row=el('div','row');const heading=el('div');
 heading.append(button(current.title,()=>mediaDetail(current.id),'title'),el('p','muted','Your private podcast'));
 const controls=el('div','transport');
 controls.append(button('−15',()=>seek(-15)),button(audio.paused?'Play':'Pause',()=>audio.paused?audio.play():audio.pause(),'play-toggle'),button('+15',()=>seek(15)));
 row.append(heading,controls);player.append(row);
 const slider=el('input');slider.type='range';slider.min=0;slider.max=audio.duration||1;slider.value=audio.currentTime;slider.setAttribute('aria-label','Podcast position');slider.id='mini-seek';slider.addEventListener('input',()=>audio.currentTime=Number(slider.value));player.append(slider);
}
for(const event of ['play','pause'])audio.addEventListener(event,()=>{for(const node of document.querySelectorAll('.play-toggle'))node.textContent=node.dataset.episodeId&&node.dataset.episodeId!==current?.id?'Play episode':audio.paused?'Play':'Pause';if(!audio.isNative&&'mediaSession' in navigator)navigator.mediaSession.playbackState=audio.paused?'paused':'playing';});
audio.addEventListener('timeupdate',()=>{
 const slider=$('#mini-seek');if(slider){slider.max=audio.duration||1;slider.value=audio.currentTime;}
 const stamp=$('#podcast-time');if(stamp&&stamp.dataset.episodeId===current?.id)stamp.textContent=`${time(audio.currentTime)} / ${time(audio.duration)}`;
 if(Date.now()-lastSave>3000){remember();updatePositionState();lastSave=Date.now();}
});
audio.addEventListener('error',()=>notice('Audio could not load. Check your connection to the Mac and retry.'));
window.addEventListener('pagehide',remember);
document.addEventListener('office-preferences',()=>{audio.playbackRate=prefs.speed;renderPlayer();if(!prefs.remember)localStorage.removeItem('office-listening');});
export async function mediaList(parent,kind='all',cursor=0){
 const data=await api(`/api/media?kind=${encodeURIComponent(kind)}&cursor=${cursor}`);
 for(const error of data.errors)parent.append(el('p','error',`${error.source}: ${error.error}`));
 for(const item of data.items){const node=card(item.title||item.file,`${item.kind==='podcast'?Math.round(item.duration_s/60)+' min · ':''}${item.date||''}`,()=>mediaDetail(item.id));if(item.excerpt)node.append(el('p','muted',item.excerpt));parent.append(node);}
 if(!data.total)empty(parent,'Nothing published here yet.');
 if(data.next_cursor!==null)parent.append(button('Load more',async()=>mediaList(parent,kind,data.next_cursor)));
}
export async function mediaDetail(id){
 rememberDetail('media',id);
 const data=await api(`/api/media/detail?id=${encodeURIComponent(id)}`);const body=sheet(data.title||'Substrate');body.append(button('Save to Library',()=>saveObject('media',id,data.title||'Substrate')));
 if(data.kind==='substrate'){showPoem(body,data);return;}
 const controls=el('div','actions episode-transport');controls.append(button('Back 15s',()=>seekEpisode(data,-15)),button(current?.id===id&&!audio.paused?'Pause':'Play episode',()=>current?.id===id&&!audio.paused?audio.pause():play(data),'play-toggle'),button('Ahead 15s',()=>seekEpisode(data,15)));
 controls.querySelector('.play-toggle').dataset.episodeId=id;const speed=select([['0.75','0.75×'],['1','1×'],['1.15','1.15×'],['1.25','1.25×'],['1.5','1.5×'],['2','2×']],String(prefs.speed));speed.setAttribute('aria-label','Playback speed');speed.addEventListener('change',()=>setPlaybackSpeed(Number(speed.value)).catch(error=>notice(error.message)));controls.append(speed);body.append(controls);
 const stamp=el('p','muted',`${time(current?.id===id?audio.currentTime:0)} / ${time(data.duration_s)}`);stamp.id='podcast-time';stamp.dataset.episodeId=id;body.append(stamp);
 const chapters=section(body,'Chapters');for(const chapter of data.chapters||[])chapters.append(button(`${time(chapter.start_s)} · ${chapter.title}`,async()=>{await play(data);audio.currentTime=chapter.start_s;}));
 if(!data.chapters?.length)empty(chapters,'This older episode has no chapter markers.');
 const transcript=section(body,'Transcript');transcript.append(el('div','reading',data.text||'Transcript unavailable.'));
 const sources=section(body,'Sources');for(const source of data.sources||[])sources.append(link(source.title,source.url));
}

async function seekEpisode(episode,delta){if(current?.id!==episode.id)await play(episode);seek(delta);}

function updatePositionState(){
 if(audio.isNative||!navigator.mediaSession?.setPositionState||!Number.isFinite(audio.duration)||audio.duration<=0)return;
 navigator.mediaSession.setPositionState({duration:audio.duration,playbackRate:audio.playbackRate,position:Math.min(audio.duration,Math.max(0,audio.currentTime))});
}

function showPoem(body,data){
 body.append(el('p','muted',data.date||''));
 const frame=el('iframe','preview');frame.src=data.url;frame.title=data.title||'Computational poem';frame.setAttribute('sandbox','allow-scripts');
 body.append(frame,el('div','reading',data.text));
 if(data.provenance)poemProvenance(body,data.provenance);
}

function poemProvenance(body,source){
 const details=el('details');details.append(el('summary','','Source & publishing'));
 details.append(el('p','muted',source.publication),el('p','muted',source.generation_receipt));
 for(const [key,label] of [['source','Read source'],['manifest','Read catalog'],['pipeline','How this is generated']])if(source[key])details.append(button(label,()=>openFile(source[key])));
 details.append(el('p','muted','File SHA256: '+source.sha256));
 if(source.source_commit)details.append(el('p','muted',`Latest recorded source change: ${source.source_commit.subject} · ${source.source_commit.at} · ${source.source_commit.sha}`));
 for(const job of source.configured_producers||[]){const url=new URL(location.href);url.searchParams.set('detail',JSON.stringify({kind:'job-history',id:job}));details.append(link('Configured producer history: '+job,url.pathname+url.search+url.hash));}
 body.append(details);
}
