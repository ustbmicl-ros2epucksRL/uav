const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(__dirname + '/../static/agent-management.js', 'utf8');
const id = '5'.repeat(32);
const run = {id, request:{question:'<em>测试</em>', workflow:'platform', scene:'urban'},
    created_at:'2026-11-21T08:00:00Z', status:'completed', step_count:3, tool_count:1};
const makePage = (runs = [run], total = 23) => ({runs, total,
    summary:{total:32, by_status:{completed:24,running:6,failed:2}, by_workflow:{platform:21,research:11}, unreadable:0}});
const ready = () => new Promise(resolve => setImmediate(resolve));

function setup(search = '', getPage = () => makePage()) {
    class Element {
        children = [];
        value = '';
        maxLength = 200;
        append(...children) { this.children.push(...children); }
        replaceChildren(...children) { this.children = children; }
    }
    const elements = new Map(), urls = [], events = {};
    const location = {pathname:'/network/agents/manage', search};
    const navigate = (_, unused, path) => { location.search = new URL(path, 'http://test').search; };
    const get = id => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    const fetch = async path => {
        const url = new URL(path, 'http://test/network/');
        urls.push(url);
        let data;
        if (url.pathname.endsWith('/status')) data = {provider:'本机',model:'local',available:false};
        else if (url.pathname.endsWith('/tools')) data = {tools:[{name:'example',description:'示例'}]};
        else data = await getPage(url.searchParams);
        return {ok:true,json:async () => data};
    };
    vm.runInNewContext(source, {fetch, document:{createElement:() => new Element(),getElementById:get},
        window:{addEventListener:(name, fn) => { events[name] = fn; }},
        location, history:{pushState:navigate,replaceState:navigate}, URLSearchParams});
    return {get,urls,events,location};
}

test('direct links restore filters and pages; the overview still covers all records', async () => {
    const s = setup('?q=abc&workflow=platform&status=completed&page=2');
    await ready();
    const params = s.urls.find(u => u.pathname.endsWith('/management/runs')).searchParams;
    assert.equal(params.get('q'), 'abc');
    assert.equal(params.get('offset'), '20');
    assert.equal(params.get('workflow'), 'platform');
    assert.equal(s.get('total').textContent,32);
    assert.match(s.get('records-status').textContent,/23 条/);
    assert.match(s.get('model-status').textContent,/未就绪/);
    const link = s.get('run-list').children[0].children[0].children[0].children[0];
    assert.equal(link.href, 'agents?run=' + id);
    assert.equal(link.textContent,'<em>测试</em>');
    await s.get('previous').onclick();
    assert(!s.location.search.includes('page='));
    s.location.search = '?q=old&workflow=research&page=2';
    s.events.popstate();
    await ready();
    assert.equal(s.get('query').value, 'old');
    assert.equal(s.get('workflow-filter').value, 'research');
    assert.match(s.get('page-info').textContent,/2 \/ 2/);
});

test('a delayed old query cannot overwrite a newer empty result or its summary', async () => {
    let release;
    const oldPage = new Promise(resolve => { release = resolve; });
    const s = setup('', params => params.get('q') === 'fresh' ? makePage([], 0) : oldPage);
    s.get('query').value = 'fresh';
    await s.get('filters').onsubmit({preventDefault(){}});
    release(makePage());
    await ready();
    assert.equal(s.get('run-list').children.length, 0);
    assert.match(s.get('records-status').textContent,/没有符合/);
    assert(s.get('previous').disabled);
    assert(s.get('next').disabled);
    assert.equal(s.get('total').textContent,32);
});

test('list failure is recoverable and does not erase the model and tools results', async () => {
    let fail = true;
    const s = setup('', () => { if (fail) throw Error('无法连接'); return makePage([],0); });
    await ready();
    assert.equal(s.get('total').textContent,'—');
    assert.match(s.get('records-status').textContent,/读取失败/);
    assert.equal(s.get('tools').children.length,2);
    fail = false;
    await s.get('refresh').onclick();
    assert.match(s.get('records-status').textContent,/尚无运行记录/);
    assert.equal(s.get('total').textContent,32);
});

test('browser back from run details refreshes a cached list using its saved filters', async () => {
    let status = 'running';
    const s = setup('?q=example', () => makePage([{...run,status}],1));
    await ready();
    const badge = () => s.get('run-list').children[0].children[2].textContent;
    assert.equal(badge(),'运行中');
    status = 'cancelled';
    s.events.pageshow({persisted:true});
    await ready();
    assert.equal(badge(),'已停止');
    assert.equal(s.get('query').value,'example');
});
