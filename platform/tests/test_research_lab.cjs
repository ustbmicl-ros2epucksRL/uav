const test=require('node:test'),assert=require('node:assert/strict'),lab=require('../static/research-lab.js');
test('all 15 experiments are deterministic with explicit synthetic evidence and finite boundary outputs',()=>{
 assert.equal(lab.all.length,15);assert.equal(new Set(lab.all.map(s=>s.key)).size,15);
 for(const s of lab.all)for(const v of [s.min,s.value,s.max]){const r=lab.execute(s.key,v);assert.deepEqual(r,lab.execute(s.key,v));assert.equal(r.evidence_status,'synthetic_only');assert.equal(r.values.length,r.labels.length);assert(r.values.every(x=>x===null||Number.isFinite(x)&&x>=0));assert(r.assumption&&r.boundary);}
 assert.throws(()=>lab.execute('1-0',Infinity));assert.throws(()=>lab.execute('1-0',141));
});
test('correlated sensors lose complementarity and mixed grids converge at full refinement',()=>{
 assert.equal(lab.execute('1-1',0).values[2],.84);assert.equal(lab.execute('1-1',1).values[2],.6);
 const r=lab.execute('1-2',100);assert.equal(r.values[0],r.values[1]);
});
test('geometry, interference and systematic bias expose limiting regimes',()=>{
 assert(Math.abs(lab.execute('2-0',90).values[0]-lab.execute('2-0',90).values[1])<1e-12);
 assert(lab.execute('2-0',5).values[1]>lab.execute('2-0',90).values[1]);
 assert.equal(lab.execute('2-1',1).values[3],1);assert(lab.execute('2-1',2).values[3]>1);
 assert.equal(lab.execute('2-2',0).values[1],.25);
});
test('engineering feasibility and surrogate validation do not inherit optimistic claims',()=>{
 const r=lab.execute('3-1',2);assert.deepEqual(r.raw.invalid,['B','C']);assert.equal(r.raw.feasible.cost,2);assert.equal(r.values[1],2);
 const s=lab.execute('3-2',8);assert.equal(s.raw.selected,3);assert(s.values[0]>=90&&s.values[1]<90);
});
test('association can fail on either model and overlap is explicitly unidentifiable',()=>{
 assert.deepEqual(lab.execute('4-1',0).values,[2,0]);assert.deepEqual(lab.execute('4-1',10).values,[0,2]);assert.deepEqual(lab.execute('4-1',4).values,[null,null]);
 const r=lab.execute('4-2',20);assert(r.values[1]<r.values[0]);assert.deepEqual(lab.execute('4-2',0).values,[lab.execute('4-2',0).values[0],lab.execute('4-2',0).values[0]]);
});
test('held-out samples never fit offset, confounding reverses apparent effect, completeness is not validity',()=>{
 const r=lab.execute('5-0',2),e=r.raw.errors;assert.equal(r.raw.estimatedOffset,e.slice(0,10).reduce((a,b)=>a+b,0)/10);
 const f=lab.execute('5-1',2);assert(f.values[1]>f.values[0]&&f.values[2]<f.values[0]);
 assert.equal(lab.execute('5-2',0).raw.structurallyComplete,true);assert.equal(lab.execute('5-2',0).raw.fieldValidated,false);assert.equal(lab.execute('5-2',5).values[1],0);
});
