const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const origin = 'http://127.0.0.1:8894';
function script(name) {
    return readFileSync(path.join(__dirname,'../app/static',name),'utf8');
}

test('ordinary links between workspaces keep the requested page and query', () => {
    let load, click, paint;
    const routed = [];
    const doc = {location:new URL(origin + '/service/'),baseURI:origin + '/service/',defaultView:{addEventListener(){}},
        addEventListener(name, fn) { if (name === 'click') click = fn; }};
    const frame = {dataset:{frame:'service'},contentDocument:doc,isConnected:true,
        addEventListener(name, fn) { if (name === 'load') load = fn; }};
    const scope = vm.createContext({URL,location:new URL(origin),requestAnimationFrame(fn){paint=fn;},cancelAnimationFrame(){}});
    vm.runInContext(script('workspace-business-links.js'),scope);
    scope.bindWorkspaceBusinessLinks(frame,(name,url)=>routed.push([name,url]));
    paint(); // The user can click before all the map assets have finished loading.
    function follow(href, options={}) {
        let intercepted = false;
        const link = {href,id:options.id,hasAttribute:()=>Boolean(options.download)};
        click({button:0,...options,target:{closest:()=>link},preventDefault(){intercepted=true;}});
        return intercepted;
    }
    assert(follow('/?view=supervision&task=1'));
    load();
    assert(follow('/network/?tab=planning#plans'));
    assert.deepEqual(routed,[['station','/station?view=supervision&task=1'],['planning','/network/?tab=planning#plans']]);
    for (const args of [
        ['/service/safety'],['/network/api/scenes'],['/network/static/buildings.geojson'],
        ['https://example.org/network/'],['/network/',{metaKey:true}],
        ['/network/',{download:true}],['/?view=supervision',{id:'situation-open'}]
    ]) assert.equal(follow(...args),false);
});

test('station messages select only a listed algorithm and leave execution to the operator', () => {
    let handler,viewClicks=0,changes=0;
    const parent = {postMessage(message, target) {
        assert.equal(message.type,'workbench-business-ready');
        assert.equal(target,origin);
    }};
    const window = {parent,frameElement:{dataset:{frame:'station'}},
        addEventListener(name, fn) { if (name==='message') handler=fn; }};
    const view={classList:{contains:()=>false},click(){viewClicks++;}};
    const picker={value:'bitstar',options:[{value:'astar'},{value:'bitstar'}],
        dispatchEvent(event){assert.equal(event.type,'change');changes++;},focus(){}};
    const document={querySelector:()=>view,getElementById:()=>picker};
    vm.runInNewContext(script('workspace-station.js'),{window,location:{origin},document,Event});
    const data={type:'workbench-algorithm',algorithm:'astar'};
    handler({source:parent,origin:'https://example.org',data});
    handler({source:{},origin,data});
    handler({source:parent,origin,data:{type:'workbench-algorithm',algorithm:'unsupported'}});
    assert.equal(viewClicks,0);
    handler({source:parent,origin,data});
    assert.deepEqual([picker.value,viewClicks,changes],['astar',1,1]);
});
