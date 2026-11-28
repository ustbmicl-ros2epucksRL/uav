const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('twin 3D ray occlusion and verdict support all three scenes and coverage_only counterfactual', async () => {
    const elements = {}, listeners = {};
    class Element {
        constructor(id) {
            this.id = id;
            this.value = '';
            this.textContent = '';
            this.innerHTML = '';
            this.hidden = false;
            this.disabled = false;
            this.checked = true;
            this.width = 960;
            this.height = 420;
            this.options = [];
            this.dataset = {};
            this.handlers = {};
        }
        add(opt) { this.options.push(opt); }
        replaceChildren(...items) { this.options = items; this.children = items; }
        addEventListener(name, fn) { this.handlers[name] = fn; }
        getBoundingClientRect() { return {left: 0, top: 0, width: this.width, height: this.height}; }
        getContext() {
            return new Proxy({}, {get: () => () => {}});
        }
        insertAdjacentHTML() {}
        scrollIntoView() {}
        focus() {}
        click() {}
    }
    const get = id => elements[id] ??= new Element(id);
    get('scene').value = 'electromagnetic';
    get('budget').value = '50';
    get('seeds').value = '6';
    get('precision').value = '30';
    get('twin3d-jamming').checked = false;

    const samplePoint = state => ({state, cause: '测试', pd: 0.9, crlb_m: 18, stations: 2, simulated_median_error_m: 14, simulated_fix_fraction: 1});
    const makeRow = (key, label, pass, gap) => ({
        key, label, description: label, added: key === 'baseline' ? [] : ['C-01-RADAR-000'],
        devices: [{id: 'R-1', name: '塔站', type: 'RADAR', x: 100, y: 100, z: 110, azimuth: 45, fov: 180, range: 1000, status: 'online'}],
        metrics: {added_cost: key === 'baseline' ? 0 : 28, added_devices: key === 'baseline' ? 0 : 1, track_coverage: 0.95,
                  theoretical_pass: pass, longest_gap_theoretical_s: gap, simulated_pass: pass, simulated_longest_gap_median_s: gap,
                  crlb_median_m: 18, simulated_median_m: 15, unavailable_seconds_by_cause: {}},
        planning_history: [], segments: [], timeline: [samplePoint('ok'), samplePoint('blind')],
    });
    const archive = {
        id: 'a'.repeat(32), created_at: '2026-11-23T10:00:00Z', evidence_level: 'synthetic_twin',
        input_digest: '1'.repeat(64), result_digest: '2'.repeat(64), scene_changed: false,
        inputs: {
            scene: {id: 'electromagnetic', name: '园区复杂电磁区', buildings: [{x: 180, y: 180, w: 80, d: 80, h: 95}], devices: [], candidates: [], zone: {x0: 150, y0: 150, x1: 300, y1: 300}},
            scene_revision: 1, kpi_version: 'proposal-2026-11-v2', model_version: 'v1', budget: 50, seeds: [1, 2, 3, 4],
            environment: {precision_m: 30}, trajectory: {id: 'UAV-003-LOW', times: [0, 1], dt_s: 1, points: [[120, 120, 60], [300, 300, 50]]},
        },
        result: {
            deployments: [
                makeRow('baseline', '现有部署', 0.4, 20),
                makeRow('method', '方法方案', 0.85, 4),
                makeRow('ignore_occlusion', '反事实：规划时忽略遮挡', 0.65, 10),
                makeRow('ignore_interference', '反事实：规划时忽略干扰', 0.70, 9),
                makeRow('coverage_only', '反事实：仅按覆盖规划', 0.60, 12),
            ],
            research_points: [1, 2, 3, 4, 5].map(id => ({
                id, subproject: '子项目' + id, title: '标题' + id, question: 'Q', finding: 'F',
                necessity: 'N', pending: ['待补'], evidence_level: '合成孪生', evidence_note: '非外场',
            })),
            relations: [{source: 1, target: 2, meaning: '输入'}],
            limitations: ['合成场景'],
        },
    };
    const segmentReport = {
        experiment_id: archive.id, scene: 'electromagnetic', start: 0, end: 1, start_s: 0, end_s: 1, sample_count: 2, dt_s: 1,
        budget: 50, precision_m: 30, deployments: archive.result.deployments.map(d => ({
            key: d.key, label: d.label, added_devices: 1, added_cost: 28, blind_count: 0, pass_count: 2,
            theoretical_pass: d.metrics.theoretical_pass, longest_gap_s: d.metrics.longest_gap_theoretical_s, causes: [],
        })),
        comparisons: [{key: 'coverage_only', equal_cost: true, pass_delta_pp: 25, gap_reduction_s: 8}],
    };
    const context = vm.createContext({
        location: {pathname: '/network/twin', search: '?scene=electromagnetic', href: 'http://127.0.0.1:8894/network/twin?scene=electromagnetic', origin: 'http://127.0.0.1:8894'},
        window: {parent: null, history: {replaceState() {}}, addEventListener: (k, fn) => { listeners[k] = fn; }},
        document: {getElementById: get, querySelector: () => new Element('q'), createElement: () => new Element('el'), body: {classList: {add() {}}}},
        localStorage: {getItem: () => 'abcd1234ef001122', setItem() {}},
        crypto: {getRandomValues: arr => arr},
        Option: class { constructor(text, value) { this.text = text; this.value = value; } },
        TwinSegment: {analyze: async () => segmentReport},
        fetch: async url => {
            if (url.includes('/api/twin-experiments?')) return {ok: true, json: async () => ({experiments: [{id: archive.id, scene: 'electromagnetic', created_at: archive.created_at, budget: 50, seeds: 4}]})};
            if (url.endsWith('/api/twin-experiments')) return {ok: true, json: async () => ({experiments: [{id: archive.id, scene: 'electromagnetic', created_at: archive.created_at, budget: 50, seeds: 4}]})};
            if (url.includes('/api/twin-experiments/' + archive.id)) return {ok: true, json: async () => archive};
            if (url.includes('/research-agent/status')) return {ok: true, json: async () => ({available: false, provider: 'Ollama', model: 'local', active_runs: []})};
            return {ok: true, json: async () => ({runs: []})};
        },
        URL, URLSearchParams, console, setTimeout, clearTimeout, setInterval, clearInterval, Math, Number,
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/twin.js'), 'utf8'), context);
    for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r));

    assert.match(get('page-title').textContent, /复杂电磁区/);
    assert.match(get('assistant-context').textContent, /复杂电磁区/);
    assert.equal(vm.runInContext('verdict({id: 3, evidence_level: "合成孪生"})', context), '本航线对照显示改善');
    const hit = vm.runInContext('rayOccludedByBuildings(100, 100, 60, 300, 300, 50, [{x:180, y:180, w:80, d:80, h:95}])', context);
    assert.ok(Array.isArray(hit) && hit.length === 3);
    const clear = vm.runInContext('rayOccludedByBuildings(100, 100, 130, 300, 300, 120, [{x:180, y:180, w:80, d:80, h:95}])', context);
    assert.equal(clear, null);
    vm.runInContext("setMode('coverage')", context);
    assert.equal(get('mode-summary-pill').textContent, '三维覆盖与遮挡盲区场');
    assert.equal(get('mode-toolbar').hidden, false);
    vm.runInContext("setMode('flight')", context);
    assert.equal(get('mode-summary-pill').textContent, '无人机航路与跨站跟踪孪生');
    assert.match(get('twin3d-cm-hud').innerHTML, /感反协同与合规引导/);
    get('twin3d-jamming').checked = true;
    vm.runInContext('draw()', context);
    assert.match(get('twin3d-cm-hud').innerHTML, /开启\(RF通道受抑关闭\)/);
});

test('overview page renders 5-subproject DAG and 4-dimensional feasibility', async () => {
    const elements = {};
    class Element {
        constructor() { this.innerHTML = ''; this.textContent = ''; }
        querySelectorAll() { return []; }
    }
    const get = id => elements[id] ??= new Element();
    const payload = {
        overview_digest: 'f'.repeat(64),
        project: {
            title: '面向低空经济的多模态感知网络智能规划',
            kpi_version: 'proposal-2026-11-v2', model_version: 'v1', proposal_version: 'end-1124',
            proposal_pdf_available: false, proposal_pdf_pages: null, thesis: '总体主线',
            landing_stages: [{stage: '第一阶段', name: '典型场景组网验证', focus: '标定', detail: '验证'}],
        },
        record_counts: {succeeded_assessments: 3, twin_experiments: 3, tasks: 3, plans: 1, trials: 3, demo_runs: 6},
        platforms: [{id: 'planning', name: '感知网络规划平台', role: '核心', capabilities: ['能力1'], boundary: '合成边界', sub_entries: [{label: '数字孪生', path: '/network/twin'}]}],
        importance: [{badge: '安全', title: '安全底线', summary: '说明', evidence_anchor: 'PDF 第5页'}],
        scientific_questions: [{id: 'Q1', title: '问题1', problem: '描述', subprojects: [1], challenges: ['难点1'], pdf_pages: [20], platform_entry: '/research/1'}],
        subprojects: [1, 2, 3, 4, 5].map(id => ({
            id, code: '子项目' + id, short: '简称' + id, title: '全称' + id, role: '角色',
            question: '问题', summary: '摘要', input: '输入', deliverables: ['产出'],
            challenge_count: 3, challenges: ['c1'], entry: '/twin', research_entry: '/research/' + id,
            pdf_pages: [20], pdf_page_label: '20',
        })),
        relations: [{source: 5, target: 4, kind: 'feedback', label: '闭环反馈'}],
        necessity: {
            counterfactual_design: '反事实说明',
            matrix: [{id: 'N1', subproject: '子项目一', counterfactual: '忽略遮挡', consequence: '漏检', verification_path: '/twin'}],
            scenes: [{scene: 'urban', name: '园区高密度建筑区', purpose: '补盲', revision: 1, device_count: 7, candidate_count: 6, building_count: 35, coarse_50m_coverage: 0.72, latest_assessment: null, latest_twin: {available: false, note: '待运行'}}],
        },
        feasibility: [{id: 'F1', dimension: '理论可行性', status_label: '已实现', summary: '可解算', metrics: ['10m/2m'], evidence_link: '/acceptance'}],
        boundaries: ['合成模型非外场实测'],
    };
    const context = vm.createContext({
        location: {pathname: '/network/overview'},
        document: {getElementById: get},
        fetch: async () => ({ok: true, json: async () => payload}),
        console,
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/overview.js'), 'utf8'), context);
    for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
    assert.match(get('platforms-grid').innerHTML, /感知网络规划平台/);
    assert.match(get('dag-svg').innerHTML, /闭环反馈/);
    assert.match(get('feasibility-grid').innerHTML, /理论可行性/);
    assert.match(get('boundaries-list').innerHTML, /非外场实测/);
});
