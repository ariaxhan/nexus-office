import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const source=fs.readFileSync(new URL('../client/phone/office.js',import.meta.url),'utf8');
const start=source.indexOf('function sessionGroups(');
const end=source.indexOf('async function projectRoster(',start);
assert.ok(start>=0&&end>start);

function node(text=''){
 return {textContent:text,children:[],isConnected:true,append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;}};
}
function harness(responses){
 let tick,timeout;
 const api=async()=>{const next=responses.shift();if(next instanceof Error)throw next;return next;};
 const el=(_tag,_class,text='')=>node(text);
 const section=(parent,title)=>{const group=node(title);parent.append(group);return group;};
 const card=(title,detail)=>node(`${title}: ${detail}`);
 const empty=(parent,message)=>parent.append(node(message));
 const setInterval=callback=>{tick=callback;return 1;};
 const setTimeout=callback=>{timeout=callback;return 1;};
 const {sessionGroups,allAgentSessions}=new Function('api','el','section','card','empty','conversation','setInterval','setTimeout','clearTimeout',`${source.slice(start,end)};return {sessionGroups,allAgentSessions}`)(api,el,section,card,empty,()=>{},setInterval,setTimeout,()=>{});
 return {sessionGroups,allAgentSessions,tick:()=>tick?.(),timeout:()=>timeout?.()};
}

test('all rows group by repo with blocked repos and sessions first',()=>{
 const {sessionGroups}=harness([]);
 const groups=sessionGroups([
  {repo:'Alpha',name:'a',status:'active'},
  {repo:'Zulu',name:'z',status:'inactive'},
  {repo:'Alpha',name:'b',status:'blocked'},
  {repo:'Beta',name:'c',status:'blocked'},
 ]);
 assert.deepEqual(groups.map(group=>[group.repo,...group.items.map(item=>item.name)]),[['Alpha','b','a'],['Beta','c'],['Zulu','z']]);
});

test('timed-out poll retains the last rows',async()=>{
 const h=harness([{state:'ok',at:'2026-10-02T12:00:00Z',sessions:[{repo:'Alpha',name:'Claude',tool:'claude',status:'active'}]},new Promise(()=>{})]);
 const parent=node();await h.allAgentSessions(parent);
 h.tick();await new Promise(resolve=>setImmediate(resolve));h.timeout();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(parent.children[1].children[0].children[0].textContent.includes('Claude'),true);
 assert.match(parent.children[0].textContent,/Sessions stale · last good .*Request timed out/);
});

test('failed poll retains rows and one stale line with last-good time',async()=>{
 const h=harness([{state:'ok',at:'2026-10-02T12:00:00Z',sessions:[{repo:'Alpha',name:'Claude',tool:'claude',status:'blocked'}]},new Error('offline')]);
 const parent=node();await h.allAgentSessions(parent);
 const [status,rows]=parent.children;
 assert.equal(rows.children[0].children[0].textContent.includes('Claude'),true);
 h.tick();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(rows.children[0].children[0].textContent.includes('Claude'),true);
 assert.match(status.textContent,/Sessions stale · last good .*offline/);
 assert.equal(parent.children.filter(child=>child.textContent.includes('Sessions stale')).length,1);
});
