// Local cache + retryable writes; timestamps use the Mac's clock offset.
const CACHE='office-object-cache',PENDING='office-object-pending';
let cache=read(CACHE),pending=read(PENDING),clockOffset=0,running=false;
function read(key){try{return JSON.parse(localStorage.getItem(key)||'{}');}catch{return {};}}
function persist(){localStorage.setItem(CACHE,JSON.stringify(cache));localStorage.setItem(PENDING,JSON.stringify(pending));}
export function value(kind,id){return cache[kind]?.[id]?.value;}
export function collection(kind){return Object.values(cache[kind]||{}).filter(item=>item.value!==null).sort((a,b)=>b.recorded_at-a.recorded_at).map(item=>item.value);}
export function record(kind,id,value){
 const recorded_at=Math.max(Date.now()+clockOffset,(cache[kind]?.[id]?.recorded_at||0)+1);
 const item={kind,id,value,recorded_at};(cache[kind]??={})[id]=item;pending[JSON.stringify([kind,id])]=item;persist();flush();
}
export async function synchronize(){
 try{
  const response=await fetch('/api/user-state',{signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error('User state unavailable');const data=await response.json();clockOffset=data.server_time-Date.now();
  for(const [kind,items] of Object.entries(data.items))for(const [id,item] of Object.entries(items)){
   if(!cache[kind]?.[id]||cache[kind][id].recorded_at<item.recorded_at)(cache[kind]??={})[id]=item;
  }
  migrateLegacy();persist();document.dispatchEvent(new Event('office-user-state'));await flush();
 }catch{/* Cached objects stay available; pending writes retry on reconnect. */}
}
async function flush(){
 if(running)return;running=true;
 try{for(const [key,item] of Object.entries(pending)){
  const response=await fetch('/api/user-state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(item),keepalive:true,signal:AbortSignal.timeout(10000)});
  if(!response.ok)break;const data=await response.json();
  if(pending[key]?.recorded_at===item.recorded_at){delete pending[key];(cache[item.kind]??={})[item.id]=data.item;persist();}
 }}catch{/* Preserve uncertain writes with their original timestamp. */}finally{running=false;}
}
window.addEventListener('online',synchronize);window.addEventListener('pagehide',()=>flush());setInterval(synchronize,30000);

export function discardRemembered(){
 for(const kind of ['recent','listening','reading'])delete cache[kind];
 for(const [key,item] of Object.entries(pending))if(['recent','listening','reading'].includes(item.kind))delete pending[key];persist();
}

function migrateLegacy(){
 const remembering=read('office-preferences-cache').preferences?.remember!==false;
 for(const [kind,key] of [['saved','office-saved'],['recent','office-recent']]){
  if(kind==='recent'&&!remembering)continue;
  const items=read(key);if(!Array.isArray(items))continue;
  for(const item of items){const id=JSON.stringify([item.kind,item.id]);if(!cache[kind]?.[id])record(kind,id,item);}
 }
 if(remembering)for(const [id,position] of Object.entries(read('office-listening')))if(!cache.listening?.[id])record('listening',id,position);
}
