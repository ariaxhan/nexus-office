// The one owner of "which document is on screen". Every navigation claims a token; a response that
// arrives after a newer claim is dropped. What the viewer shows is reported by show(), and nothing else
// infers it, so `vaults open` hears exactly what the page decided to draw.
export function viewer(post){
 let token=0,last=null,chain=Promise.resolve();const names=new Map();
 const send=body=>{chain=chain.then(()=>post(body)).catch(()=>{});return chain;};
 return {
  claim:()=>++token,
  current:claimed=>claimed===token,
  // A document route asks by repo+path; the viewer reports it back under the same names.
  name:(id,requested)=>names.set(id,requested),
  label:(id,fallback)=>names.get(id)||fallback,
  show(file){
   token++;
   if(file){last=file;return send({...file,state:'open'});}
   const gone=last;last=null;return gone?send({...gone,state:'closed'}):chain;
  },
  missing(requested,error){token++;last=null;return send({...requested,state:'missing',error});},
 };
}
