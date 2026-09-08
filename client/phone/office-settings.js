import {nativeCommand} from './office-native.js';
import {rememberDetail,$,api,el,button,sheet,section,select,notice} from './office-ui.js';
export let prefs={theme:'auto',background:'',accent:'#b9573c',font:'classic',reading:'bookish',size:100,density:'comfortable',touch:false,haptics:true,motion:false,remember:true,mini:true,speed:1};
let revision=0;
const dark=matchMedia('(prefers-color-scheme: dark)');
export async function loadSettings(){
 const cached=localStorage.getItem('office-preferences-cache');
 if(cached){try{acceptPreferences(JSON.parse(cached));}catch{localStorage.removeItem('office-preferences-cache');}}
 acceptPreferences(await api('/api/preferences'));
}
function acceptPreferences(data){
 prefs={...prefs,...data.preferences};revision=data.revision;
 localStorage.setItem('office-preferences-cache',JSON.stringify(data));apply();
}
export function apply(){
 const body=document.body;
 const theme=prefs.theme==='auto'?(dark.matches?'dark':'light'):prefs.theme;
 body.dataset.theme=prefs.background?(luminance(prefs.background)<.18?'dark':'light'):theme;
 applyContrast(body);
 body.style.setProperty('--accent-ink',luminance(prefs.accent||'#b9573c')>.18?'#171915':'#ffffff');
 body.dataset.motion=String(prefs.motion);
 body.style.setProperty('--accent',prefs.accent||'#b9573c');
 body.style.setProperty('--bg',prefs.background||'');
 body.style.setProperty('--font',{classic:'Georgia,serif',system:'system-ui,sans-serif',literary:'Palatino,Georgia,serif'}[prefs.font]);
 body.style.setProperty('--reading',{bookish:'Georgia,serif',clean:'system-ui,sans-serif',typewriter:'ui-monospace,monospace'}[prefs.reading]);
 body.style.setProperty('--space',{compact:'12px',comfortable:'16px',roomy:'22px'}[prefs.density]);
 body.style.setProperty('--control',prefs.touch?'52px':'44px');
 document.documentElement.style.fontSize=`${16*prefs.size/100}px`;
 nativeCommand({command:'preferences',remember:prefs.remember});
 document.dispatchEvent(new CustomEvent('office-preferences',{detail:prefs}));
}
dark.addEventListener('change',apply);
async function save(update){
 const data=await api('/api/preferences',{revision,preferences:update});acceptPreferences(data);
}
function setting(parent,key,title,control,description=''){
 const row=el('div','setting');const label=el('label','',title);control.id=`pref-${key}`;label.htmlFor=control.id;
 if(description)label.append(el('small','',description));row.append(label,control);parent.append(row);
 control.addEventListener('change',async()=>{control.disabled=true;try{await save({[key]:value(control)});}catch(error){notice(error.message);await loadSettings();}finally{control.disabled=false;}});
 return control;
}
function value(control){if(control.type==='checkbox')return control.checked;if(control.type==='range')return Number(control.value);return control.value;}
function choice(parent,key,title,options){return setting(parent,key,title,select(options,prefs[key]));}
function toggle(parent,key,title,description){const control=el('input');control.type='checkbox';control.checked=prefs[key];return setting(parent,key,title,control,description);}
function range(parent,key,title,min,max,step){const node=el('input');Object.assign(node,{type:'range',min,max,step,value:prefs[key]});return setting(parent,key,title,node,`${prefs[key]}${key==='size'?'%':'×'}`);}
function color(parent,key,title){const node=el('input');node.type='color';node.value=prefs[key]||'#f5f1e8';return setting(parent,key,title,node);}
export function settings(){
 rememberDetail('settings','preferences');
 const body=sheet('Settings');if(window.officeNativeAvailable)body.append(button('Mac connection',()=>nativeCommand({command:'configure'})));const look=section(body,'Appearance');
 choice(look,'theme','Theme',[['auto','Follow device'],['light','Paper'],['dark','Night']]);
 color(look,'background','Background color');look.append(button('Use theme background',()=>save({background:''})));
 color(look,'accent','Accent color');
 const type=section(body,'Typography & spacing');
 choice(type,'font','Interface font',[['classic','Classic'],['system','System'],['literary','Literary']]);
 choice(type,'reading','Reading font',[['bookish','Bookish'],['clean','Clean'],['typewriter','Typewriter']]);
 range(type,'size','Text size',90,150,5);
 choice(type,'density','Density',[['compact','Compact'],['comfortable','Comfortable'],['roomy','Roomy']]);
 toggle(type,'touch','Larger controls','More space for your thumb.');
 const feel=section(body,'Interaction');
 const haptics=toggle(feel,'haptics','Haptics','Short taps on supported devices.');
 if(!navigator.vibrate&&!window.officeNativeAvailable){haptics.disabled=true;haptics.closest('.setting').querySelector('small').textContent='This browser does not provide haptics. Your preference is saved for supported devices.';}
 toggle(feel,'motion','Reduce motion','Keep transitions still.');
 const media=section(body,'Reading & listening');
 toggle(media,'remember','Remember position','Sync reading and podcast progress through your Mac.');
 toggle(media,'mini','Keep player visible','Playback continues when the compact player is hidden.');
 range(media,'speed','Playback speed',.5,2,.05);
 body.append(button('Reset all settings',async()=>{const data=await api('/api/preferences',{revision,reset:true});acceptPreferences(data);settings();notice('Settings reset');}));
}
document.addEventListener('click',event=>{if(!prefs.haptics||!event.target.closest('button,a'))return;if(window.officeNativeAvailable)nativeCommand({command:'haptic'});else navigator.vibrate?.(8);});

function luminance(hex){const channels=hex.slice(1).match(/../g).map(value=>parseInt(value,16)/255).map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4);return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722;}

export async function setPlaybackSpeed(speed){return save({speed});}

function applyContrast(body){
 const night=body.dataset.theme==='dark';const ink=night?'#ffffff':'#000000';
 body.style.setProperty('--ink',prefs.background?ink:'');body.style.setProperty('--muted',prefs.background?ink:'');
 const background=prefs.background||(night?'#232722':'#f5f1e8');const paper=night?'#2c302a':'#fffcf5';
 const accent=prefs.accent||'#b9573c';const contrast=color=>{const a=luminance(accent),b=luminance(color);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
 body.style.setProperty('--accent-text',Math.min(contrast(background),contrast(paper))>=4.5?accent:ink);
}
