import {api,el,button,notice,link} from './office-ui.js';
export function attachments(parent,initial,onChange){
 let values=[...(initial||[])];const panel=el('div','stack');parent.append(panel);
 const input=el('input');input.type='file';input.multiple=true;input.setAttribute('aria-label','Attach photos or files');
 const list=el('div','stack');panel.append(el('p','muted','Photos or files · up to eight, 5 MiB each. Uploaded to your Mac.'),input,list);
 function draw(){list.replaceChildren();for(const item of values){const row=el('div','row');row.append(link(item.name||'Attached file',`/api/uploads/content?id=${encodeURIComponent(item.id)}&revision=${item.revision}`),button('Remove',()=>{values=values.filter(other=>other.id!==item.id);onChange(values);draw();}));list.append(row);}}
 input.addEventListener('change',async()=>{
  input.disabled=true;
  try{for(const file of input.files){
   if(values.length>=8)throw Error('Attach at most eight files.');
   if(file.size>5*1024*1024)throw Error(`${file.name} exceeds 5 MiB.`);
   const encoded=await encode(file);const receipt=await api('/api/uploads',{name:file.name,base64:encoded});
   if(!values.some(item=>item.id===receipt.id))values.push(receipt);onChange(values);draw();
  }}catch(error){notice(error.message);}finally{input.disabled=false;input.value='';}
 });draw();
 return {references:()=>values.map(({id,revision})=>({id,revision})),clear:()=>{values=[];onChange(values);draw();}};
}
function encode(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('Could not read the selected file'));reader.readAsDataURL(file);});}
