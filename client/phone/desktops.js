import RFB from '@novnc/novnc';
// The pinned package exports RFB only; bundle its own key tables by file path.
import KeyTable from '../../node_modules/@novnc/novnc/core/input/keysym.js';
import keysyms from '../../node_modules/@novnc/novnc/core/input/keysymdef.js';

const $=id=>document.getElementById(id);
let rfb=null,selected=null,command=false,composing=false,credentials=[];
let stored=null,authTry=null,failedStored=false;
function status(text){$('status').textContent=text;}
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

function releaseCommand(){
  if(command)rfb?.sendKey(KeyTable.XK_Meta_L,'MetaLeft',false);
  command=false;$('command').setAttribute('aria-pressed','false');
}
function stop(){
  releaseCommand();const previous=rfb;rfb=null;previous?.disconnect();
  $('password').value='';if($('login').open)$('login').close();
  stored=null;authTry=null;failedStored=false;
  $('typing').value='';$('typing-label').hidden=true;$('screen').replaceChildren();
}
function leave(){stop();$('session').hidden=true;$('chooser').hidden=false;selected=null;history.replaceState(null,'','/desktops');refresh();}

// A Mac's login is entered once. The door keeps it in the Studio's
// keychain and this page supplies it at connect time; the prompt only
// appears when there is no saved login, or when the saved one was
// refused - and then it says why.
async function loadStored(machine){
  try{
    const response=await fetch(`/api/desktop/${encodeURIComponent(machine.id)}/credentials`,{cache:'no-store'});
    if(!response.ok)return null;
    const data=await response.json();
    return data.stored?{username:data.username||'',password:data.password||''}:null;
  }catch{return null;}
}
async function saveStored(machine,creds){
  try{await fetch(`/api/desktop/${encodeURIComponent(machine.id)}/credentials`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(creds)});}catch{}
}
async function forgetStored(machine){
  try{await fetch(`/api/desktop/${encodeURIComponent(machine.id)}/credentials/forget`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch{}
}

async function open(machine,afterFailure=false){
  stop();selected=machine;$('chooser').hidden=true;$('session').hidden=false;
  $('machine-name').textContent=machine.name;status('Connecting…');history.replaceState(null,'',`#${machine.id}`);
  stored=afterFailure?null:await loadStored(machine);
  const socket=new URL(`/api/desktop/${encodeURIComponent(machine.id)}/socket`,location.href);
  socket.protocol=location.protocol==='https:'?'wss:':'ws:';
  const current=new RFB($('screen'),socket.href);rfb=current;
  current.scaleViewport=true;current.clipViewport=true;current.resizeSession=false;current.qualityLevel=6;current.compressionLevel=2;
  $('fit').setAttribute('aria-pressed','true');$('pan').setAttribute('aria-pressed','false');
  current.addEventListener('connect',()=>{if(rfb!==current)return;status('Connected');$('screen').focus();
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
    const retry=node('button','Reconnect');retry.addEventListener('click',()=>open(machine));$('screen').replaceChildren(retry);
  });
}

$('refresh').addEventListener('click',refresh);
$('disconnect').addEventListener('click',leave);
$('cancel-login').addEventListener('click',leave);
$('login').addEventListener('cancel',event=>{event.preventDefault();leave();});
$('login-form').addEventListener('submit',event=>{
  event.preventDefault();if(!rfb)return;
  const creds={username:credentials.includes('username')?$('username').value:'',password:$('password').value};
  authTry={source:'typed',username:creds.username,password:creds.password};
  rfb.sendCredentials(creds);
  $('password').value='';$('login').close();status('Signing in…');
});
$('keyboard').addEventListener('click',()=>{$('typing-label').hidden=!$('typing-label').hidden;if(!$('typing-label').hidden)$('typing').focus();else $('screen').focus();});
$('fit').addEventListener('click',()=>{if(!rfb)return;rfb.scaleViewport=!rfb.scaleViewport;$('fit').setAttribute('aria-pressed',String(rfb.scaleViewport));});
$('pan').addEventListener('click',()=>{if(!rfb)return;rfb.dragViewport=!rfb.dragViewport;$('pan').setAttribute('aria-pressed',String(rfb.dragViewport));});
$('command').addEventListener('click',()=>{if(!rfb)return;command=!command;rfb.sendKey(KeyTable.XK_Meta_L,'MetaLeft',command);$('command').setAttribute('aria-pressed',String(command));});
function sendKey(name){rfb?.sendKey(KeyTable[`XK_${name}`]);releaseCommand();}
for(const button of document.querySelectorAll('[data-key]'))button.addEventListener('click',()=>sendKey(button.dataset.key));
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
window.addEventListener('pagehide',stop);
refresh();
