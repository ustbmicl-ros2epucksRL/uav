(function(root){
 'use strict';
 const names={heft:'HEFT',local:'全部端侧',edge:'固定边缘',cloud:'固定云端'};
 function selected(result,index,policy){const snap=result?.snapshots?.[Number(index)];return {snap,comparison:snap?.comparisons.find(r=>r.policy===policy)};}
 function bars(comparison){const total=Math.max(comparison?.makespan_s||0,.001);return (comparison?.rows||[]).map(r=>({...r,left:100*r.start_s/total,width:100*r.compute_s/total}));}
 function display(value,digits=2){return Number.isFinite(value)?value.toFixed(digits):'不可达';}
 const api={names,selected,bars,display};if(typeof module!=='undefined')module.exports=api;else root.AlgorithmCore=api;
})(typeof window!=='undefined'?window:globalThis);
