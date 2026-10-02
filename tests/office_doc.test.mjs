// The Watch document must never lose or move operator text: parse then compose is the identity,
// and a note edit changes only that item's note.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
const source=fs.readFileSync(new URL('../client/phone/office-doc.js',import.meta.url),'utf8')
  .split('\n').filter(line=>!line.startsWith('import ')).join('\n').replace(/export /g,'');
const {parse,compose}=new Function(source+';return {parse,compose};')();
const doc='# Open work\n\nintro\n\n## Standing\n\n**Aria:**\nrule one\n\n## Group\n\n### [a#1](https://x) First\n- **What:** thing\n**Aria:**\n\n### [a#2](https://x) Second\n- **What:** other\n**Aria:**\ndo it\nsecond line\n';
test('parse then compose is the identity',()=>{assert.equal(compose(parse(doc)),doc);});
test('a note edit changes only that note',()=>{
  const parts=parse(doc);const first=parts.find(part=>part.lines[0].includes('First'));
  first.note=['close this',''];
  const next=compose(parts);
  assert.ok(next.includes('**Aria:**\nclose this\n\n### [a#2]'));
  assert.equal(next.replace('close this\n',''),doc);
});
test('text before any heading and items without a note survive',()=>{
  const plain='lead\n# T\n### x\nbody\n';assert.equal(compose(parse(plain)),plain);
});
