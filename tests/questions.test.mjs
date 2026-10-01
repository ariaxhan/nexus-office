import {test} from 'node:test';
import assert from 'node:assert/strict';
import {coordinatorQuestions} from '../client/phone/office-questions.js';
test('Watch shows coordinator questions without promoting unrelated historical decisions',()=>{
 const issue={number:522,bot_last:true,last_word:'<!-- aria-question:5e8f541f90ac -->',decision:{question:'Next?',options:[{n:1,label:'Retry'}]}};
 const world={stations:[{repo:'ariaxhan/thinking-brain-school',issues:[issue,{...issue,number:1,bot_last:false},{...issue,number:2,last_word:'old decision'},{...issue,number:3,decision:null}]}]};
 const rows=coordinatorQuestions(world);
 assert.equal(rows.length,1);assert.equal(rows[0].item.id,'ariaxhan/thinking-brain-school#522');
 assert.deepEqual(rows[0].item.decision.options,issue.decision.options);
 assert.deepEqual(coordinatorQuestions({}),[]);
});
