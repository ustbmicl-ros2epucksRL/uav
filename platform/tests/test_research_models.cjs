const test=require('node:test'),assert=require('node:assert/strict'),m=require('../static/research-models.js');
test('occlusion crosses the actual line of sight boundary',()=>{
 const ray=m.coverage(0,80).rayHeight;
 assert.equal(m.coverage(ray-.01,80).blocked,false);
 assert.equal(m.coverage(ray+.01,80).blocked,true);
 assert.equal(m.coverage(95,150).blocked,false);
});
test('enumerated optimum respects budget and exposes a greedy counterexample',()=>{
 assert.equal(m.plan(6).greedy.cells.length,8);
 assert.equal(m.plan(6).best.cells.length,10);
 let previous=0;
 for(let budget=0;budget<=12;budget++){
  const {greedy,best}=m.plan(budget);
  assert(greedy.cost<=budget && best.cost<=budget);
  assert(best.cells.length>=greedy.cells.length && best.cells.length>=previous);
  previous=best.cells.length;
 }
});
test('missing samples and late arrivals remain distinct',()=>{
 assert.equal(m.arrivals(0,false).late,false);
 assert.equal(m.arrivals(2,false).late,true);
 assert.equal(m.arrivals(0,true).missing,1);
 assert.equal(m.arrivals(0,true).late,false);
 assert(!m.arrivals(4,true).order.some(f=>f.captured===3));
});
