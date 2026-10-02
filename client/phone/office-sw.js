const CACHE='nexus-office-shell-v17';
const ASSETS=['/','/office.css','/office-v2.css','/office-bundle.js','/office-register-sw.js','/manifest.webmanifest','/office-icon.svg','/office-icon-192.png','/office-icon-512.png'];
self.addEventListener('install',event=>event.waitUntil((async()=>{
 const cache=await caches.open(CACHE);
 for(const asset of ASSETS){
  let response;
  for(let attempt=0;attempt<4;attempt++){
   try{response=await fetch(asset,{cache:'reload'});if(response.ok)break;}catch{}
   await new Promise(resolve=>setTimeout(resolve,250*(attempt+1)));
  }
  if(response?.ok){await cache.put(asset,response.clone());continue;}
  const previous=await caches.match(asset);
  if(previous)await cache.put(asset,previous);
 }
 await self.skipWaiting();
})()));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('nexus-office-shell-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);
 if(event.request.method!=='GET'||url.origin!==self.location.origin||!ASSETS.includes(url.pathname))return;
 event.respondWith((async()=>{
  const cached=await caches.match(event.request);
  const update=fetch(event.request).then(response=>{
   if(response.ok)event.waitUntil(caches.open(CACHE).then(cache=>cache.put(event.request,response.clone())));
   return response;
  }).catch(()=>null);
  if(cached){event.waitUntil(update);return cached;}
  return await update||Response.error();
 })());
});
