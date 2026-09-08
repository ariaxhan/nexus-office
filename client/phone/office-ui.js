import * as userState from './office-state.js';
export const $ = selector => document.querySelector(selector);
export function el(tag, className='', text='') {
  const node=document.createElement(tag);
  if(className) node.className=className;
  if(text!==undefined) node.textContent=String(text);
  return node;
}
export function button(text, action, className='') {
  const node=el('button',className,text); node.type='button';
  node.addEventListener('click',async()=>{node.disabled=true;try{await action();}catch(error){notice(error.message);}finally{node.disabled=false;}});
  return node;
}
export async function api(path, body) {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await response.json();
  if(!response.ok){const error=new Error(data.error||data.message||`Request failed (${response.status})`);error.status=response.status;throw error;}
  return data;
}
export function notice(text){const node=$('#notice');node.textContent=text;node.hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>node.hidden=true,6000);}
export function sheet(title){$('#detail-title').textContent=title;const body=$('#detail-body');body.transcriptCleanup?.();body.onscroll=null;body.replaceChildren();body.dataset.taskId='';if(!$('#detail').open)$('#detail').showModal();return body;}
export function section(parent,title){const part=el('section','section');part.append(el('h2','section-head',title));const body=el('div','stack');part.append(body);parent.append(part);return body;}
export function card(title,detail,action){const node=action?button('',action,'card'):el('article','card');node.append(el('h3','',title));if(detail)node.append(el('p','muted',detail));return node;}
export function intro(parent,eyebrow,title,description){const node=el('div','intro');node.append(el('div','eyebrow',eyebrow),el('h1','',title));if(description)node.append(el('p','',description));parent.append(node);}
export function empty(parent,text){parent.append(el('p','empty',text));}
export function failure(parent,error){parent.append(el('p','error',error.message||error));}
export function link(label,url){const node=el('a','',label);const parsed=new URL(url,location.origin);if(!['http:','https:'].includes(parsed.protocol))return el('span','muted','Unavailable link');node.href=parsed.href;node.target='_blank';node.rel='noopener noreferrer';return node;}
export function field(parent,label,node){
 const wrap=el('label','field');node.setAttribute('aria-label',label);wrap.append(el('span','',label),node);
 if(node.tagName==='SELECT'){
  const selected=el('small','selected-value');wrap.append(selected);
  const update=()=>{const text=node.selectedOptions[0]?.textContent||'';selected.textContent=text;selected.hidden=text.length<30;};
  node.addEventListener('change',update);node.addEventListener('input',update);update();requestAnimationFrame(update);
 }
 parent.append(wrap);return node;
}
export function select(options,value){const node=el('select');for(const [id,label] of options){const option=el('option','',label);option.value=id;node.append(option);}node.value=value;return node;}
export function time(seconds){const s=Math.max(0,Math.floor(Number(seconds)||0));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;}
let rememberObjects=true;
document.addEventListener('office-preferences',event=>{rememberObjects=event.detail.remember;if(!rememberObjects){localStorage.removeItem('office-recent');userState.discardRemembered();}});
export function rememberDetail(kind,id){
 const value=JSON.stringify({kind,id});const url=new URL(location.href);
 if(url.searchParams.get('detail')!==value){const parent=url.searchParams.get('detail');url.searchParams.set('detail',value);history.pushState({officeDetail:true,officeParent:parent},'',url);}
 if(rememberObjects){userState.record('recent',JSON.stringify([kind,id]),{kind,id,at:Date.now()});const prior=JSON.parse(localStorage.getItem('office-recent')||'[]').filter(item=>item.kind!==kind||item.id!==id);prior.unshift({kind,id,at:Date.now()});localStorage.setItem('office-recent',JSON.stringify(prior.slice(0,200)));}
}
export function saveObject(kind,id,title){userState.record('saved',JSON.stringify([kind,id]),{kind,id,title});const prior=JSON.parse(localStorage.getItem('office-saved')||'[]');if(!prior.some(item=>item.id===id&&item.kind===kind))prior.unshift({kind,id,title});localStorage.setItem('office-saved',JSON.stringify(prior));notice('Saved in Library');}
export function clearDetail(){const url=new URL(location.href);url.searchParams.delete('detail');history.replaceState({},'',url);$('#detail').close();}

export function backDetail(){if(history.state?.officeParent)history.back();else clearDetail();}

export function transcriptNavigation(body,content){
 const scroller=body.closest('#detail-body')||body;
 const nav=el('nav','transcript-nav');nav.setAttribute('aria-label','Conversation navigation');
 const picker=select([], '');picker.setAttribute('aria-label','Jump to message');
 let following=true,nodes=[],index=0;
 function jump(next){index=Math.max(0,Math.min(nodes.length-1,next));following=false;if(nodes[index])scroller.scrollTop+=nodes[index].getBoundingClientRect().top-nav.getBoundingClientRect().bottom-16;picker.value=String(index);}
 nav.append(button('First',()=>jump(0)),button('Previous',()=>jump(index-1)),button('Next',()=>jump(index+1)),button('Latest',()=>{following=true;index=nodes.length-1;picker.value=String(index);scroller.scrollTop=scroller.scrollHeight;}),picker);
 picker.addEventListener('change',()=>jump(Number(picker.value)));
 body.insertBefore(nav,body.firstChild);
 function update(){
  nodes=[...content.children].filter(node=>node.textContent.trim());
  picker.replaceChildren();
  nodes.forEach((node,i)=>{const option=el('option','',`Message ${i+1}`);option.value=String(i);picker.append(option);});
  picker.value=String(Math.min(index,nodes.length-1));
  if(following)requestAnimationFrame(()=>{if(nav.isConnected){index=nodes.length-1;picker.value=String(index);scroller.scrollTop=scroller.scrollHeight;}});
 }
 function scroll(){
  following=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<80;
  const top=nav.getBoundingClientRect().bottom;
  const visible=nodes.findIndex(node=>node.getBoundingClientRect().bottom>top+8);
  index=following?nodes.length-1:Math.max(0,visible);picker.value=String(index);
 }
 scroller.addEventListener('scroll',scroll,{passive:true});
 const observer=new MutationObserver(update);observer.observe(content,{childList:true,subtree:true,characterData:true});
 scroller.transcriptCleanup=()=>{observer.disconnect();scroller.removeEventListener('scroll',scroll);};
 update();return nav;
}

// Acknowledging one send must never erase text typed while it was in flight.
export function restoreDraft(input, identity) {
 const key='office-message-draft:'+JSON.stringify(identity);
 input.value=localStorage.getItem(key)||'';
 input.addEventListener('input',()=>localStorage.setItem(key,input.value));
 return sent=>{
  if(input.value!==sent||localStorage.getItem(key)!==sent)return;
  input.value='';localStorage.removeItem(key);
 };
}
