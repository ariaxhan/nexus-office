import {api,el,button,notice,link} from './office-ui.js';
export function attachments(parent,initial,onChange,options={}){
 let values=[...(initial||[])];const panel=el('div','stack');parent.append(panel);
 const input=el('input');input.type='file';input.multiple=true;input.setAttribute('aria-label','Attach photos or files');
 if(options.imagesOnly)input.accept='image/png,image/jpeg,image/gif,image/webp';
 const progress=el('p','muted');progress.setAttribute('role','status');panel.append(progress);
 const list=el('div',options.compact?'ask-image-list':'stack');
 if(options.compact){input.hidden=true;panel.append(input,button('＋ Image',()=>input.click(),'ask-attach-button'));}
 else panel.append(el('p','muted','Photos or files · up to eight, 5 MiB each. Uploaded to your Mac.'),input);
 panel.append(list);
 function draw(){list.replaceChildren();for(const item of values){const row=el('div',options.compact?'ask-image-chip':'row');
  const url=`/api/uploads/content?id=${encodeURIComponent(item.id)}&revision=${item.revision}`;
  if(options.imagesOnly){const preview=el('img');preview.src=url;preview.alt='';row.append(preview);}
  row.append(link(item.name||'Attached file',url),button('Remove',()=>{values=values.filter(other=>other.id!==item.id);onChange(values);draw();}));list.append(row);}}
 input.addEventListener('change',async()=>{
  input.disabled=true;progress.textContent='Uploading to your Mac…';
  try{for(const file of input.files){
   if(values.length>=8)throw Error('Attach at most eight files.');
   if(file.size>5*1024*1024)throw Error(`${file.name} exceeds 5 MiB.`);
   if(options.imagesOnly&&!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type))throw Error('Choose a PNG, JPEG, GIF, or WebP image.');
   const encoded=await encode(file);const receipt=await api('/api/uploads',{name:file.name,base64:encoded});
   if(!values.some(item=>item.id===receipt.id))values.push(receipt);onChange(values);draw();
  }}catch(error){notice(error.message);}finally{input.disabled=false;input.value='';progress.textContent='';}
 });draw();
 return {ready:()=>!input.disabled,references:()=>values.map(({id,revision})=>({id,revision})),clear:()=>{values=[];onChange(values);draw();}};
}
function encode(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('Could not read the selected file'));reader.readAsDataURL(file);});}
