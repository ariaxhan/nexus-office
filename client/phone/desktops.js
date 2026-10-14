import RFB from '@novnc/novnc';
// The pinned package exports RFB only; bundle its own key tables by file path.
import KeyTable from '../../node_modules/@novnc/novnc/core/input/keysym.js';
import keysyms from '../../node_modules/@novnc/novnc/core/input/keysymdef.js';

const $=id=>document.getElementById(id);
let rfb=null,selected=null,command=false,composing=false,credentials=[];
let stored=null,authTry=null,failedStored=false;
let toastTimer=null,idleTimer=null;
function status(text){
  $('status').textContent=text;
  const toast=$('toast');toast.classList.remove('gone');
  clearTimeout(toastTimer);
  if(text==='Connected')toastTimer=setTimeout(()=>toast.classList.add('gone'),2600);
}
function node(tag,text){const element=document.createElement(tag);element.textContent=text;return element;}

async function refresh(){
  $('refresh').disabled=true;$('list-status').textContent='Looking for your Macs…';
  try{
    const response=await fetch('/api/desktops',{cache:'no-store'});
    if(!response.ok)throw Error('Could not reach your Macs. Check your Office connection.');
    const data=await response.json();$('machines').replaceChildren();
    for(const machine of data.items){
      const card=node('article','');card.className='machine';
      card.append(node('h2',machine.name),node('p',machine.detail));
      const connect=node('button',`Open ${machine.local?'this Mac':'desktop'}`);connect.disabled=machine.state!=='ready';
      connect.addEventListener('click',()=>open(machine));card.append(connect);$('machines').append(card);
    }
    $('list-status').textContent=data.items.length===1?'One Mac found.':'Your Macs';
  }catch(error){$('list-status').textContent=error.message;}
  finally{$('refresh').disabled=false;}
}

// ── view state: fit, or zoomed via noVNC's own display scale ────────
// Zoom drives the pinned noVNC Display (scale + viewportChangePos), so
// the pointer mapping (absX divides by the same scale) stays exact at
// every zoom level. No CSS transforms anywhere near the canvas.
const view={mode:'fit',fit:0};
function display(){return rfb?rfb._display:null;}
function screenPoint(event){
  const rect=$('screen').getBoundingClientRect();
  return {x:event.clientX-rect.left,y:event.clientY-rect.top};
}
function setFit(){
  if(!rfb)return;
  const d=display();if(d&&view.fit===0)view.fit=d.scale;
  rfb.scaleViewport=true;rfb.clipViewport=true;view.mode='fit';
  $('size-label').textContent='Fit screen';
}
function setZoom(scale,cx,cy){
  const d=display();if(!d)return;
  if(view.fit===0)view.fit=d.scale;
  const fit=view.fit||1;
  const target=Math.min(3,scale);
  if(target<=fit*1.02){setFit();return;}
  // The framebuffer point under the anchor stays under the anchor.
  let fbX,fbY;
  if(view.mode==='fit'){fbX=cx/fit;fbY=cy/fit;}
  else{fbX=d._viewportLoc.x+cx/d.scale;fbY=d._viewportLoc.y+cy/d.scale;}
  rfb.scaleViewport=false;rfb.clipViewport=true;
  d.scale=target;view.mode='zoom';
  const locX=fbX-cx/target,locY=fby(fbY,cy,target);
  d.viewportChangePos(locX-d._viewportLoc.x,locY-d._viewportLoc.y);
  $('size-label').textContent='Actual size';
}
function fby(fbY,cy,target){return fbY-cy/target;}
function toggleSize(cx,cy){
  if(view.mode==='fit')setZoom(1,cx,cy);else setFit();
}

// ── gestures on the desktop surface (capture phase, before noVNC) ───
// Single taps pass through untouched (tap = click). A pinch is
// recognised by its spread, so a two-finger scroll still reaches
// noVNC; once a pinch engages it owns both pointers. While zoomed,
// one-finger drag pans when the Pan toggle is on. Double-tap toggles
// fit ↔ actual size.
const pointers=new Map();
let pinch=null,panDrag=null,lastTap=null,tapStart=null;
function panArmed(){return $('pan-toggle').getAttribute('aria-pressed')==='true';}
function closeChrome(){$('stack').hidden=true;$('keyspop').hidden=true;}
function wake(){
  document.body.classList.remove('idle');
  clearTimeout(idleTimer);
  idleTimer=setTimeout(()=>{if(selected)document.body.classList.add('idle');},3600);
}
const screenEl=$('screen');
screenEl.addEventListener('pointerdown',event=>{
  wake();
  pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,type:event.pointerType});
  if(pointers.size===2){
    const [a,b]=[...pointers.values()];
    pinch={d:Math.hypot(a.x-b.x,a.y-b.y),scale:display()?display().scale:1,engaged:false,
           cx:(a.x+b.x)/2,cy:(a.y+b.y)/2};
    panDrag=null;
  }else if(pointers.size===1){
    closeChromeSoft(event);
    tapStart={x:event.clientX,y:event.clientY,t:Date.now()};
    if(panArmed()&&view.mode==='zoom'&&rfb){
      panDrag={x:event.clientX,y:event.clientY};
      event.preventDefault();event.stopPropagation();
    }
  }
},true);
function closeChromeSoft(event){
  // A plain tap on the desktop closes the pill stack / keys popover,
  // like the mock-up; the tap itself still reaches the desktop.
  if(event.target===screenEl||event.target.tagName==='CANVAS')closeChrome();
}
screenEl.addEventListener('pointermove',event=>{
  if(!pointers.has(event.pointerId))return;
  pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,type:event.pointerType});
  if(pinch&&pointers.size===2){
    const [a,b]=[...pointers.values()];
    const nd=Math.hypot(a.x-b.x,a.y-b.y);
    if(!pinch.engaged&&Math.abs(nd-pinch.d)>12)pinch.engaged=true;
    if(pinch.engaged&&rfb){
      const at=screenPoint({clientX:(a.x+b.x)/2,clientY:(a.y+b.y)/2});
      setZoom(pinch.scale*nd/pinch.d,at.x,at.y);
      event.preventDefault();event.stopPropagation();
    }
  }else if(panDrag&&rfb){
    const d=display();const s=d.scale;
    d.viewportChangePos(-(event.clientX-panDrag.x)/s,-(event.clientY-panDrag.y)/s);
    panDrag={x:event.clientX,y:event.clientY};
    event.preventDefault();event.stopPropagation();
  }
},true);
function endPointer(event){
  const wasPinch=!!pinch&&pointers.has(event.pointerId);
  const wasPan=!!panDrag;
  pointers.delete(event.pointerId);
  if(pointers.size<2)pinch=null;
  if(pointers.size===0)panDrag=null;
  if(wasPinch){lastTap=null;tapStart=null;return;} // a pinch is never half of a double-tap
  if(wasPan)return; // consumed interactions are not taps
  if(event.type!=='pointerup')return;
  // A tap is short and nearly still; a drag on the desktop is neither.
  if(!tapStart||Date.now()-tapStart.t>300||Math.hypot(event.clientX-tapStart.x,event.clientY-tapStart.y)>10){tapStart=null;return;}
  tapStart=null;
  const now=Date.now();
  if(lastTap&&now-lastTap.t<450&&Math.hypot(event.clientX-lastTap.x,event.clientY-lastTap.y)<44){
    const at=screenPoint(event);
    if(view.mode==='fit'||view.mode==='zoom')toggleSize(at.x,at.y);
    lastTap=null;
  }else{
    lastTap={t:now,x:event.clientX,y:event.clientY};
  }
}
screenEl.addEventListener('pointerup',endPointer,true);
screenEl.addEventListener('pointercancel',endPointer,true);

// ── keyboard strip ───────────────────────────────────────────────────
function kbHeight(){
  const vv=window.visualViewport;
  if(!vv)return 0;
  return Math.max(0,window.innerHeight-vv.height-vv.offsetTop);
}
function applyKbHeight(){
  $('session').style.setProperty('--kb',kbHeight()+'px');
}
if(window.visualViewport){
  window.visualViewport.addEventListener('resize',applyKbHeight);
  window.visualViewport.addEventListener('scroll',applyKbHeight);
}
function kbOpen(){$('typing').focus();}
function kbClose(){$('typing').blur();}
$('typing').addEventListener('focus',()=>{$('strip').hidden=false;applyKbHeight();$('kb-open').classList.add('active');});
$('typing').addEventListener('blur',()=>{setTimeout(()=>{if(document.activeElement!==$('typing')){$('strip').hidden=true;$('session').style.setProperty('--kb','0px');$('kb-open').classList.remove('active');}},60);});
// Strip and popover taps must not blur the typing input (that would
// drop the keyboard mid-sequence).
for(const el of [$('strip'),$('keyspop')])el.addEventListener('pointerdown',event=>event.preventDefault());

function releaseCommand(){
  if(command)rfb?.sendKey(KeyTable.XK_Meta_L,'MetaLeft',false);
  command=false;$('cmd-latch').setAttribute('aria-pressed','false');
}
function stop(){
  releaseCommand();const previous=rfb;rfb=null;previous?.disconnect();
  $('password').value='';if($('login').open)$('login').close();
  stored=null;authTry=null;failedStored=false;
  pointers.clear();pinch=null;panDrag=null;lastTap=null;
  view.mode='fit';view.fit=0;closeChrome();$('strip').hidden=true;
  $('typing').value='';$('screen').replaceChildren();
  document.body.classList.remove('idle');clearTimeout(idleTimer);clearTimeout(toastTimer);
  $('toast').classList.remove('gone');
  const retry=$('reconnect');if(retry)retry.remove();
}
function leave(){stop();$('session').hidden=true;$('chooser').hidden=false;selected=null;history.replaceState(null,'','/desktops');refresh();}

// A Mac's login is entered once. The door keeps it in the Studio's
// keychain and this page supplies it at connect time; the prompt only
// appears when there is no saved login, or when the saved one was
// refused - and then it says why.
async function loadStored(machine){
  // One retry: a request issued in the same burst as a desktop
  // websocket closing can be eaten by a recycled keep-alive connection
  // (the door answers 501 to the garbled remnant); an idempotent GET
  // is safe to ask twice.
  for(let attempt=0;attempt<2;attempt++){
    try{
      const response=await fetch(`/api/desktop/${encodeURIComponent(machine.id)}/credentials`,{cache:'no-store'});
      if(!response.ok)throw Error('not ok');
      const data=await response.json();
      return data.stored?{username:data.username||'',password:data.password||''}:null;
    }catch{if(attempt===0)await new Promise(done=>setTimeout(done,400));}
  }
  return null;
}
async function saveStored(machine,creds){
  try{await fetch(`/api/desktop/${encodeURIComponent(machine.id)}/credentials`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(creds)});}catch{}
}
async function forgetStored(machine){
  try{await fetch(`/api/desktop/${encodeURIComponent(machine.id)}/credentials/forget`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch{}
}

async function open(machine,afterFailure=false){
  stop();selected=machine;$('chooser').hidden=true;$('session').hidden=false;
  $('who').textContent=machine.name;status('Connecting…');history.replaceState(null,'',`#${machine.id}`);
  wake();
  stored=afterFailure?null:await loadStored(machine);
  const socket=new URL(`/api/desktop/${encodeURIComponent(machine.id)}/socket`,location.href);
  socket.protocol=location.protocol==='https:'?'wss:':'ws:';
  const current=new RFB($('screen'),socket.href);rfb=current;
  current.scaleViewport=true;current.clipViewport=true;current.resizeSession=false;current.qualityLevel=6;current.compressionLevel=2;
  $('pan-toggle').setAttribute('aria-pressed','false');
  $('size-label').textContent='Fit screen';view.mode='fit';view.fit=0;
  current.addEventListener('connect',()=>{if(rfb!==current)return;
    const d=display();if(d)view.fit=d.scale;
    status('Connected');$('screen').focus();
    if(authTry&&authTry.source==='typed'){const creds={username:authTry.username,password:authTry.password};authTry={source:'saved'};void saveStored(machine,creds);}
  });
  current.addEventListener('credentialsrequired',event=>{
    if(rfb!==current)return;credentials=event.detail.types;
    if(!authTry&&stored){authTry={source:'stored'};current.sendCredentials({username:credentials.includes('username')?stored.username:'',password:stored.password});status('Signing in…');return;}
    $('username-label').hidden=!credentials.includes('username');$('username').required=credentials.includes('username');
    $('login-title').textContent=`Sign in to ${machine.name}`;
    $('login-message').textContent=afterFailure?"The saved password didn't work — enter the current account name and password for this Mac.":'Use the account name and password you use on this Mac.';
    status('Waiting for your Mac login');
    $('login').showModal();(credentials.includes('username')?$('username'):$('password')).focus();
  });
  current.addEventListener('securityfailure',()=>{if(rfb!==current)return;
    if(authTry&&authTry.source==='stored'){failedStored=true;stored=null;void forgetStored(machine);}
    status('Mac login was refused. Check the account name, password and Screen Sharing access.');});
  current.addEventListener('disconnect',event=>{
    if(rfb!==current)return;rfb=null;releaseCommand();$('password').value='';if($('login').open)$('login').close();
    if(failedStored){void open(machine,true);return;}
    const prior=$('status').textContent;if(!prior.includes('refused'))status(event.detail.clean?'Disconnected':'Connection ended. Check that the Mac is awake and Screen Sharing is on.');
    const retry=node('button','Reconnect');retry.id='reconnect';retry.className='act';
    retry.addEventListener('click',()=>open(machine));$('pillwrap').prepend(retry);
  });
}

// ── pill + chrome wiring ─────────────────────────────────────────────
$('fab').addEventListener('click',()=>{
  const stack=$('stack');const willOpen=stack.hidden;
  stack.hidden=!willOpen;
  if(!willOpen)$('keyspop').hidden=true;
  wake();
});
$('disconnect').addEventListener('click',leave);
$('kb-open').addEventListener('click',()=>{closeChrome();if($('strip').hidden)kbOpen();else kbClose();});
$('size-toggle').addEventListener('click',()=>{
  closeChrome();if(!rfb)return;
  const rect=$('screen').getBoundingClientRect();
  toggleSize(rect.width/2,rect.height/2);
});
$('pan-toggle').addEventListener('click',()=>{
  const next=$('pan-toggle').getAttribute('aria-pressed')!=='true';
  $('pan-toggle').setAttribute('aria-pressed',String(next));closeChrome();
});
$('keys-open').addEventListener('click',()=>{
  $('keyspop').hidden=!$('keyspop').hidden;$('stack').hidden=true;wake();
});
$('kb-hide').addEventListener('click',kbClose);
$('cmd-latch').addEventListener('click',()=>{
  if(!rfb)return;command=!command;
  rfb.sendKey(KeyTable.XK_Meta_L,'MetaLeft',command);
  $('cmd-latch').setAttribute('aria-pressed',String(command));
});

$('refresh').addEventListener('click',refresh);
$('cancel-login').addEventListener('click',leave);
$('login').addEventListener('cancel',event=>{event.preventDefault();leave();});
$('login-form').addEventListener('submit',event=>{
  event.preventDefault();if(!rfb)return;
  const creds={username:credentials.includes('username')?$('username').value:'',password:$('password').value};
  authTry={source:'typed',username:creds.username,password:creds.password};
  rfb.sendCredentials(creds);
  $('password').value='';$('login').close();status('Signing in…');
});
function sendKey(name){rfb?.sendKey(KeyTable[`XK_${name}`]);releaseCommand();}
function sendCombo(letter){
  if(!rfb)return;
  const sym=keysyms.lookup(letter.codePointAt(0));
  rfb.sendKey(KeyTable.XK_Meta_L,'MetaLeft',true);
  rfb.sendKey(sym);rfb.sendKey(KeyTable.XK_Meta_L,'MetaLeft',false);
  releaseCommand();
}
for(const button of document.querySelectorAll('[data-key]'))button.addEventListener('click',()=>sendKey(button.dataset.key));
for(const button of document.querySelectorAll('[data-combo]'))button.addEventListener('click',()=>sendCombo(button.dataset.combo));
function typeText(){
  if(composing)return;
  const text=$('typing').value;$('typing').value='';
  for(const char of text)rfb?.sendKey(keysyms.lookup(char.codePointAt(0)));
  if(text)releaseCommand();
}
$('typing').addEventListener('compositionstart',()=>{composing=true;});
$('typing').addEventListener('compositionend',()=>{composing=false;typeText();});
$('typing').addEventListener('input',event=>{if(event.inputType==='deleteContentBackward')sendKey('BackSpace');else typeText();});
$('typing').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();sendKey('Return');}else if(event.key==='Backspace'){event.preventDefault();sendKey('BackSpace');}});
document.addEventListener('pointerdown',wake,true);
window.addEventListener('pagehide',stop);
refresh();
