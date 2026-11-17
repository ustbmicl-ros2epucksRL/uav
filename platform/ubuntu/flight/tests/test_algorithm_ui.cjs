const test=require('node:test'),assert=require('node:assert/strict');
const C=require('../app/static/algorithm-core.js');
test('unreachable plans remain unavailable and selected snapshot owns data',()=>{
 const r={snapshots:[{position:[1,2,3],comparisons:[{policy:'cloud',status:'unreachable',makespan_s:null,rows:[]}]}]};
 assert.equal(C.display(C.selected(r,0,'cloud').comparison.makespan_s),'不可达');
 assert.deepEqual(C.bars(C.selected(r,0,'cloud').comparison),[]);
 assert.equal(C.selected(r,3,'heft').comparison,undefined);
});
test('timeline bars use actual schedule positions and durations',()=>{
 const rows=C.bars({makespan_s:10,rows:[{id:'detect',start_s:2,compute_s:3}]});
 assert.equal(rows[0].left,20);assert.equal(rows[0].width,30);
});
