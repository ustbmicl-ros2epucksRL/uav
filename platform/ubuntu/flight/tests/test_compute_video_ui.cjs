const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(options={}){
 const elements={};
 const element=tag=>({tag,children:[],style:{},attrs:{},namespaceURI:'http://www.w3.org/2000/svg',append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},setAttribute(k,v){this.attrs[k]=v},addEventListener(){}});
 const $=id=>elements[id]??=element('div');
 const layer={addTo(){return this},clearLayers(){}};
 const window={};
 vm.runInNewContext(fs.readFileSync(__dirname+'/../app/static/compute-live.js','utf8'),{$,window,fetch:options.fetch,map:{},L:{layerGroup:()=>layer},document:{createElement:element,createElementNS:(ns,tag)=>element(tag)},Date});
 return {$,window};
}
test('recorded frame and boxes render together; historical actions stay locked',()=>{
 const s=setup();
 const result={model:'SSDLite',detections:[{box:[1,2,10,20],score:.9,label:'person'}],inference_s:.1,model_load_s:0,label:'测试录像',frame:{width:100,height:50,url:'/api/test-video/frames/abc',captured_at:10,video_time_s:1}};
 s.window.renderComputeTelemetry({algorithm:{},historical:true,execution_id:'ex',phase:'completed',runtime:{state:'completed',counts:{completed:1,failed:0,cancelled:0,retries:1},nodes:[],last_batch:{id:'batch',status:'completed',latency_s:.3,tasks:{},output:result}}});
 const flatten=e=>[e,...e.children.flatMap(flatten)],items=flatten(s.$('compute-runtime'));
 assert.equal(items.find(e=>e.tag==='img').src,result.frame.url);
 assert.equal(items.find(e=>e.tag==='rect').attrs.width,9);
 assert.equal(items.find(e=>e.tag==='text').textContent,'person 90%');
 assert(s.$('compute-start').disabled&&s.$('compute-stop').disabled&&s.$('compute-fault').disabled);
 assert(items.some(e=>e.tag==='details'));
});

test('node selection expands the compute panel itself, not its nested fault controls',()=>{
 const s=setup();s.window.ComputeTopology={project:()=>({at:10,nodes:[{id:'edge-a',label:'边 · edge-a',load:.25}],batch_id:'batch-123'})};
 s.window.renderComputeTelemetry({algorithm:{},historical:true,runtime:null});
 s.window.showComputeNode('edge-a');assert.equal(s.$('twin-compute').open,true);assert.match(s.$('compute-action').textContent,/25%/);
 s.$('twin-compute').open=false;s.window.showComputeNode('edge-a',false);assert.equal(s.$('twin-compute').open,false);
});

test('migration detail selects its own batch and hides future results while scrubbing',()=>{
 const s=setup(),migration={batch_id:'specific',task:'detect',at:9,label:'迁移',node:'device'};
 s.window.ComputeTopology={project:()=>({migrations:[migration],links:[],nodes:[]})};
 const batch={id:'specific',finished_at:12,tasks:{detect:{id:'detect',attempts:[{sent_at:8,received_at:10,node:'edge-a',status:'failed',error:'link_disconnected'}]}},output:{detections:[{label:'person',score:.9}],frame:{url:'/correct-frame'}}};
 const display={algorithm:{},historical:true,cursor_at:11,runtime:{at:11,state:'running',counts:{completed:1,failed:0,cancelled:0,retries:1},nodes:[],current:batch,last_batch:{id:'different',tasks:{}}}};
 s.window.renderComputeTelemetry(display);s.window.showComputeDetail('migration','specific|detect|9');
 let items=s.$('compute-selection').children;
 assert(items.some(e=>e.textContent?.includes('specific')));assert(items.some(e=>e.textContent?.includes('链路中断')));
 assert(!items.some(e=>e.tag==='img'));assert(items.some(e=>e.textContent?.includes('未来')));
 assert(items.some(e=>e.textContent?.includes('worker 往返 —')));assert(!items.some(e=>e.textContent?.includes('worker 往返 0 ms')));
 s.window.renderComputeTelemetry({...display,cursor_at:13,runtime:{...display.runtime,at:13}});
 items=s.$('compute-selection').children;assert.equal(items.find(e=>e.tag==='img').src,'/correct-frame');
 assert(s.$('compute-demo').disabled&&s.$('compute-demo-cancel').disabled);
});


test('link details find archived traffic and exclude future batches',async()=>{
 const make=(id,at)=>({id,created_at:at,finished_at:at+1,tasks:{detect:{id:'detect',attempts:[{node:'edge-a',sent_at:at,received_at:at+1,status:'completed',transfers:[{started_at:at,path:['device','edge-a']}]}]}},output:{detections:[],frame:{url:'/'+id}}});
 const s=setup({fetch:async()=>({ok:true,json:async()=>({batches:[make('past',2),make('future',20)]})})});
 s.window.ComputeTopology={project:()=>({links:[{source:'device',target:'edge-a',label:'弱网'}],migrations:[],nodes:[]})};
 s.window.renderComputeTelemetry({algorithm:{},historical:true,cursor_at:10,runtime:{at:10,record_id:'record',task_id:'task',state:'running',counts:{completed:1},nodes:[]}});
 s.window.showComputeDetail('link','device→edge-a');await new Promise(resolve=>setImmediate(resolve));
 const items=s.$('compute-selection').children;assert(items.some(e=>e.textContent?.includes('past')));assert.equal(items.find(e=>e.tag==='img').src,'/past');
});
