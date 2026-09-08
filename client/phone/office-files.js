import * as userState from './office-state.js';
import {rememberDetail,saveObject,api,el,button,sheet,section,card,empty,link,notice} from './office-ui.js';
import {markdownView} from './office-markdown.js';
import {prefs} from './office-settings.js';
export async function browse(parent,id='',cursor=0){
 if(cursor===0&&parent.closest('dialog'))rememberDetail('folder',id);
 const data=await api(`/api/objects?id=${encodeURIComponent(id)}&cursor=${cursor}`);
 if(cursor===0){parent.replaceChildren();if(id)parent.append(button('↑ Parent folder',()=>browse(parent,data.parent||'')));if(data.path)parent.append(el('p','muted',data.path));}
 for(const item of data.items){parent.append(card(item.name,item.kind==='folder'?(item.path||''):`${item.mime} · ${Math.ceil(item.bytes/1024)} KB`,()=>item.kind==='folder'?browse(sheet(item.name),item.id):openFile(item.id)));}
 if(!data.items.length)empty(parent,'This folder has no visible work files.');
 if(data.next_cursor!==null)parent.append(button('More files',()=>browse(parent,id,data.next_cursor)));
}
function draft(id){try{return JSON.parse(localStorage.getItem(`office-draft:${id}`)||'null');}catch{return null;}}
export async function openFile(id,offset=0){
 rememberDetail('file',id);
 const data=await api(`/api/objects/detail?id=${encodeURIComponent(id)}&offset=${offset}`);const body=sheet(data.name);
 body.append(el('p','muted',`${data.project} / ${data.path}`),el('p','muted',`Revision ${data.revision.slice(0,12)}${data.is_text?` · lines ${data.line_start}–${data.line_end}`:''}`));
 const actions=el('div','actions');actions.append(link('Download',data.content_url),button('Save to Library',()=>saveObject('file',id,data.name)),button('Ask an agent',()=>document.dispatchEvent(new CustomEvent('office-file-task',{detail:data}))));body.append(actions);
 if(data.mime==='text/html')actions.append(button('Preview',()=>preview(sheet(data.name),data)));
 if(data.editable)actions.append(button('Edit file',()=>editFile(data)));
 if(data.is_text){actions.append(button('View source',()=>{const source=sheet(data.name+' source');source.append(numberedSource(data));source.append(contextSelection(data));}));const text=data.name.endsWith('.md')?markdownView(data.text):el('pre','',data.text);text.classList.add('reading');body.append(text);restoreReading(body,id);}
 else preview(body,data);
 if(data.is_text)body.append(contextSelection(data));
 if(data.next_offset!==null)body.append(button('Next part',()=>openFile(id,data.next_offset)));
 if(offset>0)body.append(button('Start of file',()=>openFile(id)));
}
function preview(body,data){
 if(data.mime.startsWith('image/')){const image=el('img');image.src=data.content_url;image.alt=data.name;image.style.maxWidth='100%';body.append(image);return;}
 if(data.mime.startsWith('video/')||data.mime.startsWith('audio/')){const media=el(data.mime.startsWith('video/')?'video':'audio');media.controls=true;media.src=data.content_url;media.style.width='100%';body.append(media);return;}
 if(data.mime==='application/pdf'||data.mime==='text/html'){const frame=el('iframe','preview');frame.title=data.name;frame.src=data.content_url;frame.setAttribute('sandbox','allow-scripts');body.append(frame);return;}
 empty(body,'This format is available as a download.');
}
function restoreReading(body,id){
 if(!prefs.remember)return;
 body.scrollTop=Number(userState.value('reading',id)??localStorage.getItem(`office-read:${id}`)??0);
 let timer;body.onscroll=()=>{if(!prefs.remember)return;const position=body.scrollTop;localStorage.setItem(`office-read:${id}`,String(position));clearTimeout(timer);timer=setTimeout(()=>userState.record('reading',id,position),300);};
}
function editFile(data){
 const body=sheet(`Edit ${data.name}`);const prior=draft(data.id);const editor=el('textarea','code-editor');editor.setAttribute('aria-label',`Edit ${data.name}`);editor.value=prior?.text??data.text;
 let revision=prior?.revision??data.revision;
 if(prior&&prior.revision!==data.revision){body.append(el('p','error','Your saved draft is based on an older file. Compare with the current file before replacing it.'));const compare=el('details');compare.append(el('summary','','Current file'),el('pre','',data.text));body.append(compare);body.append(button('Use current file as comparison base',()=>{revision=data.revision;notice('Base updated. Review your draft before saving.');}));}
 editor.addEventListener('input',()=>localStorage.setItem(`office-draft:${data.id}`,JSON.stringify({text:editor.value,revision})));
 body.append(editor);const actions=el('div','actions');
 const preview=el('pre');body.append(preview);actions.append(button('Preview changes',async()=>{const dataDiff=await api('/api/objects/diff',{id:data.id,text:editor.value,revision});preview.textContent=dataDiff.diff||'No changes';}),button('Save changes',async()=>{await api('/api/objects/save',{id:data.id,text:editor.value,revision});localStorage.removeItem(`office-draft:${data.id}`);notice('Saved on your Mac');await openFile(data.id);},'primary'),button('Discard draft',()=>{localStorage.removeItem(`office-draft:${data.id}`);return openFile(data.id);}));body.append(actions,el('p','muted','Drafts stay on this device until saved. A conflicting Mac edit is never silently overwritten.'));
}

document.addEventListener('office-preferences',()=>{if(!prefs.remember)for(const key of Object.keys(localStorage))if(key.startsWith('office-read:'))localStorage.removeItem(key);});

function numberedSource(data){
 const body=el('div');const lines=data.text.split('\n');let count=0;
 const list=el('ol','code-lines');list.start=data.line_start||1;body.append(list);
 const more=button('More source lines',append);body.append(more);
 function append(){for(const line of lines.slice(count,count+500)){const item=el('li');item.append(el('code','',line||' '));list.append(item);}count+=500;more.hidden=count>=lines.length;}
 append();return body;
}
function contextSelection(data){
 const box=el('details');box.append(el('summary','','Ask about selected lines'));
 const start=el('input'),end=el('input');
 for(const [input,label,value] of [[start,'First line',data.line_start],[end,'Last line',data.line_end]]){
  input.type='number';input.min=1;input.value=value;input.setAttribute('aria-label',label);const field=el('label');field.append(el('span','',label),input);box.append(field);
 }
 box.append(button('Ask an agent about these lines',()=>document.dispatchEvent(new CustomEvent('office-file-task',{detail:{...data,start_line:Number(start.value),end_line:Number(end.value)}}))));
 return box;
}
