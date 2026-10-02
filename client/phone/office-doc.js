// The one document on Watch: every open item, with a plain text area under each where the
// operator writes guidance. The file is ordinary Markdown in the owning repo; a line that is
// exactly **Aria:** opens that item's guidance, which runs to the next heading. Saving goes
// through /api/context, so a concurrent writer turns into a re-read and one retry, never a clobber.
import {api,el,notice} from './office-ui.js';
import {markdownView} from './office-markdown.js';

const REPO='ariaxhan/thinking-brain-school',PATH='_meta/state/office-doc.md',MARK='**Aria:**';
const source=()=>`/api/context?repo=${encodeURIComponent(REPO)}&path=${encodeURIComponent(PATH)}`;

export function parse(text){
  const parts=[];let part={lines:[],note:null};
  for(const line of String(text).split('\n')){
    if(/^#{1,3} /.test(line)){if(part.lines.length||part.note!==null)parts.push(part);part={lines:[line],note:null};}
    else if(part.note===null&&line.trim()===MARK)part.note=[];
    else if(part.note!==null)part.note.push(line);
    else part.lines.push(line);
  }
  parts.push(part);return parts;
}
export function compose(parts){
  return parts.map(part=>part.note===null?part.lines.join('\n'):[...part.lines,MARK,...part.note].join('\n')).join('\n');
}
const written=note=>note.join('\n').replace(/\s+$/,'');

let node=null,saved='',parts=[],dirty=new Map(),timer=0,saving=false;
function fit(area){area.style.height='auto';area.style.height=Math.max(44,area.scrollHeight+2)+'px';}
function draw(status){
  const body=node.querySelector('.office-doc-body');body.replaceChildren();
  parts.forEach((part,index)=>{
    if(!part.lines.length&&part.note===null)return;
    const item=el('article','office-doc-item'+(/^### /.test(part.lines[0]||'')?' is-item':''));
    item.append(markdownView(part.lines.join('\n')));
    if(part.note!==null){
      const area=el('textarea','office-doc-note');area.placeholder='Your guidance';area.value=written(part.note);area.rows=1;
      area.addEventListener('input',()=>{dirty.set(part.lines[0],area.value);fit(area);status.textContent='Typing…';clearTimeout(timer);timer=setTimeout(()=>save(status),1200);});
      area.addEventListener('blur',()=>{if(dirty.size){clearTimeout(timer);save(status);}});
      item.append(area);requestAnimationFrame(()=>fit(area));
    }
    body.append(item);
  });
}
function apply(){for(const part of parts)if(part.note!==null&&dirty.has(part.lines[0]))part.note=[...dirty.get(part.lines[0]).split('\n'),''];}
async function save(status,retried=false){
  if(saving)return;saving=true;
  try{
    apply();const text=compose(parts);
    try{await api('/api/context',{repo:REPO,path:PATH,text,expected:saved});saved=text;dirty.clear();status.textContent='Saved';}
    catch(error){
      if(retried)throw error;
      const fresh=await api(source());saved=fresh.text;parts=parse(saved);saving=false;return save(status,true);
    }
  }catch(error){status.textContent='Not saved';notice(error.message);}
  finally{saving=false;}
}
export function officeDocument(){
  if(node){if(!dirty.size)void load(node.querySelector('.office-doc-status'));return node;}
  node=el('section','office-doc');
  const status=el('p','office-doc-status muted','Loading…');
  node.append(status,el('div','office-doc-body'));
  void load(status);return node;
}
async function load(status){
  try{
    const fresh=await api(source());
    if(fresh.text===saved&&node.querySelector('.office-doc-item'))return;
    if(dirty.size)return;
    saved=fresh.text;parts=parse(saved);draw(status);status.textContent='';
  }catch(error){status.textContent='The document is unavailable: '+error.message;}
}
