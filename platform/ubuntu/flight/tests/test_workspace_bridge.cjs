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

test('unified workspace groups collapsible sections, nests station algorithms, and separates theory vs platform skills', () => {
    const html = script('unified-workspace.html');
    const js = script('unified-workspace.js');
    const css = script('unified-workspace.css');
    assert.match(html, /unified-workspace\.css\?v=/);
    assert.match(html, /unified-workspace\.js\?v=/);
    assert.match(html, /<section id="chat-dock" class="collapsed"/);
    assert.match(css, /--rail:\s*#dfe7ef/);
    assert.match(css, /--accent:\s*#16526d/);
    assert.match(css, /border-radius:\s*11px/);
    for (const groupId of ['group-platforms', 'group-proposal', 'group-theory-skills', 'group-algo-skills']) {
        assert.match(html, new RegExp(`<details[^>]*id="${groupId}"`), groupId);
    }
    const stationDrawerPos = html.indexOf('id="station-algorithms"');
    const planningBtnPos = html.indexOf('data-platform="planning"');
    assert.ok(stationDrawerPos > html.indexOf('data-platform="station"') && stationDrawerPos < planningBtnPos,
        'station route algorithms must nest directly under station platform entry');
    for (const subPath of ['/network/overview', '/network/twin', '/network/acceptance', '/network/tasks', '/network/demonstration']) {
        assert.ok(html.includes(`data-planning-path="${subPath}"`), subPath);
    }
    assert.ok(html.includes('id="theory-skill-list"'));
    assert.ok(html.includes('id="skill-list"'));
    assert.ok(html.includes('id="platform-synergy"'));
    assert.ok(js.includes("['research_framework', '研究体系与四维可行性']"));
    assert.ok(js.includes("selectTheoryMechanismSkill"));
    assert.ok(js.includes("selectPlatformSkill"));
});

test('unified workspace runtime renders two-tier skills, syncs collapsible badges, and dispatches both workflows', async () => {
    const elements = {}, chatMessages = [], stationMessages = [];
    class Element {
        constructor(id = '') {
            this.id = id;
            this.dataset = {};
            this.children = [];
            this.handlers = {};
            this.attrs = {};
            this.classes = new Set();
            this.style = {setProperty() {}};
            this.textContent = '';
            this.open = id === 'group-platforms';
            this.hidden = false;
            this.clientHeight = 800;
            this.contentWindow = id === 'chat-frame'
                ? {postMessage: (msg, target) => chatMessages.push({msg, target})}
                : {location: {href: origin + '/station?workbench=1'}, postMessage: (msg, target) => stationMessages.push({msg, target})};
        }
        append(...items) { this.children.push(...items); }
        replaceChildren(...items) { this.children = items; }
        addEventListener(name, fn) { this.handlers[name] = fn; }
        setAttribute(k, v) { this.attrs[k] = v; }
        removeAttribute(k) { delete this.attrs[k]; }
        hasPointerCapture() { return false; }
        getBoundingClientRect() { return {height: 260, bottom: 800}; }
        querySelector(sel) {
            if (sel === '[aria-current]') return this.children.find(c => c.attrs?.['aria-current']) || null;
            return null;
        }
        querySelectorAll(sel) {
            if (sel === '[data-platform]') return this.platformButtons || [];
            if (sel === '[data-planning-path]') return this.planningButtons || [];
            if (sel === '[data-algorithm]') return this.algoButtons || [];
            return [];
        }
    }
    const get = id => elements[id] ??= new Element(id);
    for (const k of ['station', 'planning', 'service']) {
        const btn = new Element('p-' + k);
        btn.dataset.platform = k;
        (get('platform-list').platformButtons ??= []).push(btn);
    }
    for (const [subPath, shortLabel] of [['/network/overview', '研究体系'], ['/network/twin', '数字孪生']]) {
        const btn = new Element('v-' + shortLabel);
        btn.dataset.planningPath = subPath;
        btn.dataset.shortLabel = shortLabel;
        btn.classList = {toggle(cls, on) { on ? btn.classes.add(cls) : btn.classes.delete(cls); }};
        (get('planning-views').planningButtons ??= []).push(btn);
    }
    for (const alg of ['bitstar', 'acs_ds', 'astar']) {
        const btn = new Element('a-' + alg);
        btn.dataset.algorithm = alg;
        (get('algorithm-list').algoButtons ??= []).push(btn);
    }
    for (const id of ['workspace', 'business-stage', 'chat-dock', 'group-platforms', 'group-proposal', 'group-theory-skills', 'group-algo-skills']) {
        const el = get(id);
        el.classList = {
            toggle(cls, on) { const v = on ?? !el.classes.has(cls); v ? el.classes.add(cls) : el.classes.delete(cls); return v; },
            contains: cls => el.classes.has(cls),
            add: cls => el.classes.add(cls),
            remove: cls => el.classes.delete(cls),
        };
    }
    let windowMessage;
    const catalogPayload = {
        tools: [
            {name: 'research_framework', category: 'theory', effects: 'read_only', description: '体系'},
            {name: 'proposal_kpi', category: 'theory', effects: 'read_only', description: '指标'},
            {name: 'proposal_search', category: 'theory', effects: 'read_only', description: '原文'},
            {name: 'diagnose_coverage', category: 'platform_planning', effects: 'read_only', description: '诊断'},
            {name: 'twin_experiment', category: 'platform_planning', effects: 'read_only', description: '孪生'},
            {name: 'flight_algorithm_runs', category: 'platform_collab', effects: 'read_only', description: '航路'},
        ],
        theory_mechanisms: [
            {id: 'theory-task-1', task_id: 1, workflow: 'research', title: '子项目一 · 遮挡机理', description: '1-0', question: '运行子项目一算例'},
            {id: 'theory-task-2', task_id: 2, workflow: 'research', title: '子项目二 · 干扰机理', description: '2-0', question: '运行子项目二算例'},
        ],
    };
    const scope = vm.createContext({
        location: new URL(origin + '/'),
        history: {pushState() {}, replaceState() {}},
        matchMedia: () => ({matches: false, addEventListener() {}}),
        ResizeObserver: class { observe() {} },
        MutationObserver: class { observe() {} disconnect() {} },
        URL, URLSearchParams,
        fetch: async () => ({ok: true, json: async () => catalogPayload}),
        window: {addEventListener(name, fn) { if (name === 'message') windowMessage = fn; }},
        document: {
            getElementById: get,
            createElement: tag => new Element(tag),
            createTextNode: text => ({textContent: text}),
            addEventListener() {},
        },
    });
    vm.runInContext(script('unified-workspace.js'), scope);
    for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r));

    assert.equal(get('badge-platforms').textContent, '区域地面站');
    assert.equal(get('badge-theory-skills').textContent, '立项与机理 · 5');
    assert.equal(get('badge-algo-skills').textContent, '规划与协同 · 3');

    get('algorithm-list').algoButtons[0].handlers.click();
    assert.equal(get('station-algorithms').open, true);

    get('planning-views').planningButtons[0].handlers.click();
    assert.equal(get('group-proposal').open, true);
    assert.equal(get('badge-proposal').textContent, '研究体系');
    assert.equal(get('badge-platforms').textContent, '感知网络规划');
    assert.equal(get('station-algorithms').open, false);

    windowMessage({origin, source: get('chat-frame').contentWindow, data: {type: 'platform-assistant-ready'}});
    const theoryMechBtn = get('theory-skill-list').children.find(el => el.dataset?.theorySkill === 'theory-task-2');
    assert.ok(theoryMechBtn, 'theory mechanism button rendered');
    theoryMechBtn.handlers.click();
    assert.deepEqual({...chatMessages.at(-1).msg}, {
        type: 'workbench-skill', workflow: 'research', taskId: 2, question: '运行子项目二算例',
    });

    const algoBtn = get('skill-list').children.find(el => el.dataset?.skill === 'twin_experiment');
    assert.ok(algoBtn, 'platform algorithm skill button rendered');
    algoBtn.handlers.click();
    assert.equal(chatMessages.at(-1).msg.workflow, 'platform');
    assert.equal(chatMessages.at(-1).msg.skill, 'twin_experiment');
});
