'use strict';
const root = location.pathname.startsWith('/network') ? '/network' : '';
const $ = id => document.getElementById(id);
const activeKey = 'research-agent-active:' + root;
const agentQuery = new URLSearchParams(location.search || '');
const embedded = agentQuery.get('embed') === '1';
const docked = embedded && agentQuery.get('layout') === 'dock';
if (embedded) {
    document.body.classList.add('agent-embedded');
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.stopPropagation();
            window.parent.postMessage({type:'platform-assistant-close'}, docked ? location.origin : '*');
        }
    });
}
const initialScene = agentQuery.get('scene');
if (['urban','corridor','electromagnetic'].includes(initialScene)) $('scene').value = initialScene;
let record = null, timer = null, generation = 0;
let lastPlanningScene = null, pendingPlanningScene = null;
let selectedSkill = null;
let segmentRef = null, segmentLoaded = false;
let busy = false;
const nav = {tasks:'规划任务',resources:'资源配置',diagnosis:'感知诊断',planning:'网络规划',plans:'方案管理',demonstration:'示范应用',research:'研究问题',agents:'研究智能体','agents/manage':'智能体管理'};
for (const [key,label] of Object.entries(nav)) {
    const a = document.createElement('a');
    a.href = root + '/' + key; a.textContent = label;
    if (key === 'agents') a.className = 'active';
    $('navigation').append(a);
}
async function api(path, body) {
    const response = await fetch(root + '/api/research-agent/' + path, body === undefined ? {} : {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    return data;
}
function controls(active) {
    busy = Boolean(active);
    $('run').disabled = active || (segmentRef !== null && !segmentLoaded); $('cancel').disabled = !active;
    $('history').disabled = active;
    for (const id of ['workflow','scene','task','mode']) $(id).disabled = active;
    if (docked) {
        $('skill-accept').disabled = active;
        if (!active) applyPlanningScene();
    }
}
const sceneNames = {urban:'园区高密度建筑区', corridor:'物流主干道路', electromagnetic:'园区复杂电磁区'};
const taskNames = ['三维覆盖', '干扰与协同', '按需规划', '运行平台', '示范验证'];
const workspaceNames = {station:'区域地面站', planning:'感知网络规划', service:'低空服务管理'};
const platformToolNames = {
    scene_snapshot:'场景配置', diagnose_coverage:'覆盖诊断', planning_records:'规划任务',
    assessment_results:'历史验算', twin_experiment:'孪生实验', proposal_kpi:'申报指标',
    proposal_search:'申请书原文', flight_algorithm_runs:'航路算法', flight_compute:'云边端执行',
    flight_records:'飞行档案', service_tasks:'低空服务任务'
};
const toolNames = {
    ...platformToolNames,
    read_research_task:'研究任务摘要', run_research_experiment:'研究算例'
};
const statusNames = {running:'进行中', completed:'已完成', failed:'失败', cancelled:'已停止', interrupted:'中断'};
const provenanceNames = {
    configuration_snapshot:'配置快照', synthetic_calculation:'合成计算', stored_records:'业务记录',
    stored_simulation_results:'历史仿真记录', synthetic_twin_experiment:'合成孪生记录',
    proposal_document:'申报书', synthetic_only:'合成算例', flight_platform_records:'在研平台记录'
};
const workspace = workspaceNames[agentQuery.get('workspace')];
$('host-context').textContent = workspace || '研究助手';

function sameSegment(ref, other) {
    return ref && other && ref.experiment_id === other.experiment_id && ref.start === other.start && ref.end === other.end;
}
function validSegmentRef({experiment_id, start, end}) {
    return typeof experiment_id === 'string' && /^[a-f0-9]{32}$/.test(experiment_id) &&
        Number.isSafeInteger(start) && start >= 0 && Number.isSafeInteger(end) && end >= start;
}
function segmentSourceLink(data) {
    const link = node('a','打开原始选段证据 JSON ↗');
    link.href = root + '/api/twin-experiments/' + data.experiment_id + '/segment?' +
        new URLSearchParams({start:data.start, end:data.end});
    link.target = '_blank';
    link.rel = 'noopener';
    return link;
}
function updateSegmentScope() {
    document.body.dataset.segmentScope = !segmentRef ? '' : sameSegment(segmentRef, record?.request?.twin_segment) ? 'match' : 'selected';
}
function updateSegmentHistory(restoreRun=false) {
    refreshHistory(restoreRun).catch(error => { $('history-status').textContent = '历史记录读取失败 · ' + error.message; });
}
function clearSegment() {
    segmentRef = null;
    segmentLoaded = false;
    $('segment-panel').hidden = true;
    updateSegmentScope();
    controls(busy);
    updateSegmentHistory();
    window.parent.postMessage({type:'workbench-clear-twin-segment'}, location.origin);
}
function renderSegmentArchive(archive, segment) {
    const timeline = archive.inputs.trajectory;
    $('segment-label').textContent = '建筑区 · ' + timeline.id + ' · ' + segment.start_s + '–' + segment.end_s + ' 秒';
    $('segment-status').textContent = '合成孪生 · ' + segment.sample_count + ' 个采样点 · 档案 ' +
        archive.id.slice(0,8) + (archive.scene_changed ? ' · 当前场景已有更新，使用归档快照' : '');
    $('segment-chat').append(node('p','当前提问将引用此选段。可查看执行步骤、同预算证据和本段历史。'));
    const steps = $('segment-steps');
    steps.append(node('h3','档案中的规划步骤'));
    const method = archive.result.deployments.find(row => row.key === 'method');
    const list = document.createElement('ol');
    for (const step of method?.planning_history || []) {
        let label = step.rule;
        if (step.step !== 'compare') {
            const operation = {add:'新增', replace:'替换', remove:'移除'}[step.step] || step.step;
            label = [operation, step.replaced, step.option, '规划合格采样 ' + step.planner_score].filter(Boolean).join(' · ');
        }
        list.append(node('li', label));
    }
    steps.append(list, node('p',method?.planning_history?.length ?
        '规划在任务空域采样点上选址，选段用于评价已选部署。' : '此档案未保存逐步选址记录。'));

    const evidence = $('segment-evidence');
    evidence.append(node('h3','选段证据'), node('p','部署预算上限 ' + segment.budget + ' 万元 · 理论定位门槛 ' +
        segment.precision_m + ' m · 连续时长按每个采样点 ' + segment.dt_s + ' 秒累计'));
    for (const plan of segment.deployments) {
        const row = node('p', plan.label + ' · 理论合格 ' + plan.pass_count + '/' + segment.sample_count +
            ' · 探测盲点 ' + plan.blind_count + ' 个 · 最长连续不合格 ' + plan.longest_gap_s + ' 秒 · 新增 ' + plan.added_cost + ' 万元');
        evidence.append(row);
        if (plan.causes.length) evidence.append(node('p', plan.causes.map(item => item.cause + ' ' + item.samples + ' 点').join('，')));
    }
    const byKey = new Map(segment.deployments.map(plan => [plan.key, plan.label]));
    for (const comparison of segment.comparisons) {
        if (!comparison.equal_cost) continue;
        const delta = comparison.gap_reduction_s;
        const gap = delta > 0 ? '缩短 ' + delta + ' 秒' : delta < 0 ? '延长 ' + -delta + ' 秒' : '相同';
        evidence.append(node('p', '同费用 · 完整方案相对' + byKey.get(comparison.key) +
            '，理论合格比例变化 ' + comparison.pass_delta_pp.toFixed(1) + ' 个百分点，最长连续不合格时间' + gap + '。'));
    }
    const meta = document.createElement('details');
    meta.append(node('summary','冻结输入与结果摘要'), node('p','输入 SHA-256 · ' + archive.input_digest),
        node('p','结果 SHA-256 · ' + archive.result_digest));
    evidence.append(meta, segmentSourceLink(segment));

    const history = $('segment-history');
    history.append(node('h3','实验档案'), node('p', localTime(archive.created_at) + ' · 建筑区实验已归档 · ' + archive.id));
    const check = archive.reproducibility;
    history.append(node('p', check ? localTime(check.checked_at) + ' · 复算' +
        (check.matches && check.input_matches ? '一致' : '存在差异') : '尚无复算记录。'), node('h3','本段智能体历史'));
}
async function openSegment({experimentId, start, end}) {
    const ref = {experiment_id:experimentId, start, end};
    if (!validSegmentRef(ref)) return;
    showDockView('evidence');
    if (sameSegment(ref, segmentRef) && segmentLoaded) { updateSegmentHistory(true); return; }
    segmentRef = ref;
    segmentLoaded = false;
    updateSegmentScope();
    $('segment-panel').hidden = false;
    $('segment-label').textContent = '建筑区合成航段';
    $('segment-status').textContent = '正在读取实验档案…';
    for (const part of ['chat','steps','evidence','history']) $('segment-' + part).replaceChildren();
    controls(busy);
    $('history').replaceChildren();
    $('history-status').textContent = '正在核对选段档案…';
    try {
        const response = await fetch(root + '/api/twin-experiments/' + experimentId);
        if (!response.ok) throw Error('档案读取失败（' + response.status + '）');
        const archive = await response.json();
        if (archive.id !== experimentId || archive.evidence_level !== 'synthetic_twin') throw Error('实验档案不匹配');
        const segment = await window.TwinSegment.analyze(archive, start, end);
        if (segmentRef !== ref) return;
        if (!sameSegment(ref, segment) || archive.result_digest !== segment.result_digest ||
            archive.input_digest !== segment.input_digest) throw Error('区段和档案摘要不一致');
        renderSegmentArchive(archive, segment);
        segmentLoaded = true;
        updateSegmentHistory(true);
    } catch (error) {
        if (segmentRef !== ref) return;
        $('segment-status').textContent = '选段证据暂不可用 · ' + error.message;
        $('history-status').textContent = '档案核验失败，尚未加载关联记录。';
    }
    controls(busy);
}

function showDockView(name) {
    document.body.dataset.dockView = name;
    for (const button of $('dock-tabs').querySelectorAll('[data-dock-view]')) {
        button.setAttribute('aria-pressed', String(button.dataset.dockView === name));
    }
    if (name === 'history') $('history-view').open = true;
    document.querySelector('.assistant-content').scrollTop = 0;
}

function applyPlanningScene() {
    if (!pendingPlanningScene || $('run').disabled) return;
    $('scene').value = pendingPlanningScene;
    pendingPlanningScene = null;
    $('workflow').onchange();
}

function selectSkill(name) {
    selectedSkill = name || null;
    $('selected-skill').hidden = !selectedSkill;
    $('selected-skill-name').textContent = selectedSkill ? 'Skill · ' + platformToolNames[selectedSkill] : '';
}

if (docked) {
    document.body.classList.add('agent-dock');
    const tabs = $('dock-tabs');
    tabs.hidden = false;
    tabs.append($('stage'));
    for (const button of tabs.querySelectorAll('[data-dock-view]')) {
        button.onclick = () => showDockView(button.dataset.dockView);
    }
    showDockView('chat');

    let pendingSkill = null;
    function dismissSkill() {
        pendingSkill = null;
        $('skill-suggestion').hidden = true;
    }
    function applySkill() {
        if (!pendingSkill || $('run').disabled) return;
        if (segmentRef) clearSegment();
        $('workflow').value = pendingSkill.workflow;
        if (pendingSkill.workflow === 'research' && pendingSkill.taskId) {
            $('task').value = String(pendingSkill.taskId);
        }
        $('workflow').onchange();
        selectSkill(pendingSkill.skill);
        $('question').value = pendingSkill.question;
        dismissSkill();
        showDockView('chat');
        $('question').focus();
    }
    $('skill-accept').onclick = applySkill;
    $('clear-segment').onclick = () => { clearSegment(); showDockView('chat'); };
    $('skill-dismiss').onclick = dismissSkill;
    $('clear-skill').onclick = () => { selectSkill(null); $('question').focus(); };
    window.addEventListener('message', event => {
        if (event.origin !== location.origin || event.source !== window.parent || !event.data) return;
        const {type, workspace, scene, workflow, question, taskId, skill} = event.data;
        if (type === 'workbench-twin-segment') { openSegment(event.data); return; }
        if (type === 'workbench-context' && typeof workspace === 'string' && Object.hasOwn(workspaceNames, workspace)) {
            $('host-context').textContent = workspaceNames[workspace];
            if (workspace === 'planning' && typeof scene === 'string' && Object.hasOwn(sceneNames, scene) && scene !== lastPlanningScene) {
                lastPlanningScene = pendingPlanningScene = scene;
                applyPlanningScene();
            }
        }
        if (type !== 'workbench-skill' || !['platform','research'].includes(workflow) ||
            typeof question !== 'string' || question.trim().length < 2 || question.length > 3000 ||
            (taskId !== undefined && (!Number.isInteger(taskId) || taskId < 1 || taskId > 5)) ||
            (skill !== undefined && (workflow !== 'platform' || typeof skill !== 'string' || !Object.hasOwn(platformToolNames, skill)))) return;
        pendingSkill = {workflow, question:question.trim(), taskId, skill};
        if ($('run').disabled || $('question').value.trim()) {
            $('skill-preview').textContent = '所选 Skill 的问题：' + pendingSkill.question;
            $('skill-suggestion').hidden = false;
        } else {
            applySkill();
        }
    });
}

function node(tag, value) {
    const element = document.createElement(tag);
    element.textContent = value;
    return element;
}

function scopeOf(request) {
    if (request.twin_segment && validSegmentRef(request.twin_segment)) {
        const ref = request.twin_segment;
        return '建筑区 · ' + ref.experiment_id.slice(0,8) + ' · 采样 ' + ref.start + '–' + ref.end;
    }
    if (request.workflow === 'platform') return sceneNames[request.scene] || '场景未记录';
    return '研究子任务 ' + (request.task_id || '未记录') + (taskNames[request.task_id - 1] ? ' · ' + taskNames[request.task_id - 1] : '');
}

function localTime(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', {hour12:false});
}

function evidenceLink(data, index, label) {
    const link = node('a', label);
    link.href = location.pathname + (location.search || '') + '#trace-' + data.id + '-' + index;
    if (docked) link.onclick = () => showDockView('evidence');
    return link;
}

function report(id, result, data) {
    const target = $(id);
    target.replaceChildren();
    if (!result) {
        target.textContent = '尚未生成';
        return;
    }
    if (result.numeric_source === 'archived_twin_segment') {
        target.append(node('p', '定量结论来自本段归档采样，验证建议由助手生成。'));
    }
    for (const [key,title] of Object.entries({platform_facts:'平台记录事实', conclusion:'结论', evidence:'依据', evidence_ids:'引用证据',
        limitations:'局限', source_limitations:'工具适用范围', next_steps:'下一步建议'})) {
        if (!result[key]?.length) continue;
        target.append(node('h3', title));
        if (key === 'evidence_ids') {
            for (const citation of result[key]) {
                const index = (data.trace || []).findIndex(item => item.result?.evidence_id === citation);
                target.append(index < 0 ? node('span', citation) : evidenceLink(data, index, citation));
            }
        } else {
            const values = Array.isArray(result[key]) ? result[key] : [result[key]];
            for (const text of values) target.append(node('p', text));
        }
    }
}

function evidenceSummary(item) {
    const result = item.result || {};
    const data = result.data || {};
    if (item.tool === 'proposal_search') {
        return data.passages?.length ? '申请书原文 ' + data.passages.length + ' 段 · 第 ' + data.passages.map(p => p.page).join('、') + ' 页' : (data.note || '未检索到相关原文');
    }
    if (item.tool?.startsWith('flight_') || item.tool === 'service_tasks') {
        if (data.available === false) return '在研平台接口不可达，缺少证据';
        if (item.tool === 'flight_algorithm_runs') return '航路算法实验 ' + data.runs.length + ' 次';
        if (item.tool === 'flight_compute') return '计算回归 ' + data.stability.compute_passed + '/' + data.stability.compute_regressions + ' 次通过 · 主平台档案带计算批次 ' + data.records_with_batches + '/' + data.records.length + ' 条';
        if (item.tool === 'flight_records') return 'SITL 飞行档案 ' + data.items.length + ' / ' + data.total + ' 条';
        return '低空服务任务 ' + data.total + ' 项，已完成 ' + data.performance.completed + ' 项';
    }
    if (Array.isArray(data.devices)) return '配置设备 ' + data.devices.length + ' 台';
    if (Array.isArray(data.tasks)) return '已读取 ' + data.tasks.length + ' 条规划任务';
    if (Array.isArray(data.assessments)) return '已读取 ' + data.assessments.length + ' 条历史验算';
    if (Number.isFinite(data.stats?.coverage)) return '合成覆盖率 ' + (data.stats.coverage * 100).toFixed(1) + '%';
    if (item.tool === 'twin_experiment') {
        if (validSegmentRef(data)) return '合成实验 ' + data.experiment_id + ' · 选段 ' + data.start_s + '–' + data.end_s + ' 秒';
        return data.available ? '实验 ' + data.id : data.note;
    }
    if (data.current?.version) return '指标版本 ' + data.current.version;
    if (Array.isArray(result.labels) && Array.isArray(result.values)) {
        return result.labels.map((label, i) => label + ' ' + (result.values[i] ?? '—')).join(' · ');
    }
    return result.summary || '';
}

function renderEvidence(data, index) {
    const item = data.trace[index];
    const result = item.result || {};
    const card = document.createElement('article');
    card.className = 'evidence-card';
    card.id = 'trace-' + data.id + '-' + index;
    card.tabIndex = -1;
    const title = toolNames[item.tool] || item.tool;
    card.append(node('h4', title + (item.arguments?.key ? ' · ' + item.arguments.key : '')));
    const source = result.provenance || result.evidence_status;
    const meta = [provenanceNames[source] || source || item.source, result.source_path,
        result.scene_revision == null ? '' : '场景版本 ' + result.scene_revision,
        result.evidence_id, result.model_version].filter(Boolean);
    if (meta.length) card.append(node('p', meta.join(' · ')));
    const summary = evidenceSummary(item);
    if (summary) card.append(node('p', summary));
    if (item.tool === 'twin_experiment' && result.data && validSegmentRef(result.data)) card.append(segmentSourceLink(result.data));
    for (const note of result.limitations || []) card.append(node('p', note));
    if (result.boundary) card.append(node('p', result.boundary));
    const details = document.createElement('details');
    details.append(node('summary','原始记录'), node('pre',JSON.stringify(item, null, 2)));
    card.append(details);
    return card;
}

function renderSteps(data) {
    const list = $('steps');
    list.replaceChildren();
    if (!data.steps?.length) {
        const message = Array.isArray(data.steps) ? '尚无已记录步骤' : '旧记录未保存逐步时间，请查看执行证据。';
        list.append(node('li', message));
        return;
    }
    data.steps.forEach((step, i) => {
        const row = document.createElement('li');
        row.className = 'step-' + step.status;
        row.append(node('strong', step.label), node('span',statusNames[step.status] || step.status));
        const stamps = [step.started_at, step.finished_at].filter(Boolean);
        if (stamps.length) {
            const time = node('time', stamps.map(value => {
                const date = new Date(value);
                return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', {hour12:false});
            }).join(' → '));
            time.title = stamps.map(localTime).join(' → ');
            row.append(time);
        }
        (data.trace || []).forEach((item, index) => {
            if (item.step_index === i) row.append(evidenceLink(data, index, '查看证据'));
        });
        list.append(row);
    });
}

let visibleEvidenceCount = 0;
function render(data) {
    const active = data.status === 'running';
    const trace = data.trace || [];
    // Evidence is appended by the runner. Preserve expanded cards across polls.
    if (record?.id !== data.id || visibleEvidenceCount > trace.length) {
        $('trace').replaceChildren();
        visibleEvidenceCount = 0;
    }
    if (!trace.length) $('trace').textContent = '尚无已保存的工具证据。';
    if (trace.length && !visibleEvidenceCount) $('trace').replaceChildren();
    for (; visibleEvidenceCount < trace.length; visibleEvidenceCount++) {
        $('trace').append(renderEvidence(data, visibleEvidenceCount));
    }
    record = data;
    updateSegmentScope();
    if (!embedded && window.history && data.id && agentQuery.get('run') !== data.id) {
        agentQuery.set('run', data.id);
        agentQuery.delete('workflow');
        window.history.replaceState(null, '', location.pathname + '?' + agentQuery);
    }
    $('history').value = data.id;
    $('run-context').textContent = data.request ? scopeOf(data.request) + ' · ' + data.request.question : '';
    $('stage').textContent = [statusNames[data.status] || data.status, data.stage, data.error].filter(Boolean).join(' · ');
    controls(active);
    $('export').disabled = false;
    $('purpose').textContent = data.selection?.purpose || '';
    renderSteps(data);
    report('answer', data.report, data);
    $('review-card').hidden = data.request?.mode !== 'review' && !data.review;
    report('review', data.review, data);
    if (!active) {
        clearTimeout(timer);
        if (localStorage.getItem(activeKey) === data.id) localStorage.removeItem(activeKey);
    }
}
async function refreshHistory(restoreRun=false) {
    const ref = segmentRef;
    const query = ref ? '?' + new URLSearchParams(ref) : '';
    const data = await api('runs' + query);
    if (segmentRef !== ref) return;
    const runs = ref ? data.runs.filter(item => sameSegment(ref, item.request?.twin_segment)) : data.runs;
    const selected = $('history').value || record?.id;
    $('history').replaceChildren();
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '选择历史记录';
    $('history').append(placeholder);
    for (const item of runs) {
        const option = document.createElement('option'); option.value = item.id;
        option.textContent = [(item.request?.question || item.id).slice(0,65), statusNames[item.status] || item.status,
            scopeOf(item.request || {}), localTime(item.created_at)].join(' · ');
        $('history').append(option);
    }
    $('history').value = selected || '';
    $('history-status').textContent = runs.length ? '最近 ' + runs.length + ' 条记录' : ref ? '此选段尚无智能体运行记录。' : '尚无历史记录。';
    if (restoreRun && ref && runs.length && !busy && !sameSegment(ref, record?.request?.twin_segment)) {
        clearTimeout(timer);
        poll(runs[0].id, ++generation);
    }
}
async function poll(id, version) {
    try {
        const data = await api('runs/' + id);
        if (version !== generation) return;
        render(data);
        if (data.status === 'running') timer = setTimeout(() => poll(id,version),2000);
        else refreshHistory().catch(() => {});
    } catch (error) {
        if (version !== generation) return;
        $('history').value = record?.id || '';
        controls(record?.status === 'running' && record?.id === id);
        $('stage').textContent = '读取记录失败 · ' + error.message + '。可重新选择记录或刷新页面。';
        if (record?.status === 'running' && record.id === id) timer = setTimeout(() => poll(id,version),4000);
    }
}
$('workflow').onchange = () => {
    const platform = $('workflow').value === 'platform';
    if (docked && !platform) selectSkill(null);
    $('scene-label').hidden = !platform; $('task-label').hidden = platform;
    $('scope-summary').textContent = scopeOf({workflow:$('workflow').value, scene:$('scene').value, task_id:$('task').value})
        + ($('mode').value === 'review' ? ' · 含复核' : '');
};
for (const id of ['scene','mode','task']) $(id).onchange = $('workflow').onchange;
$('run').onclick = async () => {
    if (segmentRef && !segmentLoaded) return;
    const question = $('question').value.trim();
    if (question.length < 2) {
        $('stage').textContent = '请输入至少两个字符的问题。';
        $('question').focus();
        return;
    }
    clearTimeout(timer); const version = ++generation;
    controls(true); $('cancel').disabled = true; $('stage').textContent = '提交智能体任务…';
    try {
        const request = {question, task_id:Number($('task').value),
            mode:$('mode').value, workflow:$('workflow').value, scene:$('scene').value};
        if (docked && selectedSkill && request.workflow === 'platform') request.skill = selectedSkill;
        if (segmentRef) Object.assign(request, {workflow:'platform', scene:'urban', skill:'twin_experiment', twin_segment:segmentRef});
        const data = await api('runs', request);
        if (version !== generation) return;
        if (docked) {
            if ($('question').value.trim() === question) $('question').value = '';
            selectSkill(null);
        }
        localStorage.setItem(activeKey,data.id); render(data); poll(data.id,version);
    } catch (error) { $('stage').textContent = error.message; controls(false); }
};
$('cancel').onclick = async () => {
    if (!record || record.status !== 'running') return;
    clearTimeout(timer); ++generation;
    try { render(await api('runs/' + record.id + '/cancel',{})); await refreshHistory(); }
    catch (error) { $('stage').textContent = error.message; poll(record.id,generation); }
};
$('history-refresh').onclick = () => refreshHistory().catch(error => { $('history-status').textContent = error.message; });
$('history').onchange = () => {
    if (!$('history').value || $('history').disabled) return;
    if (docked) showDockView('chat');
    clearTimeout(timer); const version = ++generation;
    controls(true); $('cancel').disabled = true;
    poll($('history').value,version);
};
$('export').onclick = () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = '研究智能体-' + record.id + '.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
};
$('memory-rank').onclick = async () => {
    const button = $('memory-rank'); button.disabled = true; $('memory-stage').textContent = '提交记忆增量…';
    try {
        const segments = JSON.parse($('memory-segments').value);
        const data = await api('memory-rank',{question:$('memory-question').value,budget_bytes:Number($('memory-budget').value),segments});
        $('memory-stage').textContent = '已按记录计算增量'; $('memory-result').textContent = JSON.stringify(data,null,2);
    } catch (error) { $('memory-stage').textContent = error.message; }
    finally { button.disabled = false; }
};
if (embedded) window.parent.postMessage({type:'platform-assistant-ready'}, docked ? location.origin : '*');
(async () => {
    const requestedRun = agentQuery.get('run');
    const requestedWorkflow = agentQuery.get('workflow');
    if (['platform', 'research'].includes(requestedWorkflow)) {
        $('workflow').value = requestedWorkflow;
    }
    $('workflow').onchange();
    if (requestedRun !== null) {
        if (/^[a-f0-9]{32}$/.test(requestedRun)) {
            controls(true);
            $('cancel').disabled = true;
            $('stage').textContent = '正在读取所选运行记录…';
            poll(requestedRun, ++generation);
        } else {
            $('stage').textContent = '运行编号无效，请从管理台重新选择。';
        }
    }
    refreshHistory().catch(error => { $('history-status').textContent = '历史记录读取失败：' + error.message; });
    api('tools').then(data => { $('tool-catalog').textContent = data.tools.map(tool => tool.name + '：' + tool.description).join('\n'); })
        .catch(error => { $('tool-catalog').textContent = error.message; });
    try {
        const status = await api('status');
        $('model-status').textContent = status.provider + ' · ' + status.model + ' · ' + (status.available ? '模型已安装，可开始研究' : '模型未就绪；仍可查看历史及调用只读工具');
        const id = status.active_runs[0] || localStorage.getItem(activeKey) || localStorage.getItem('research-agent-active');
        if (requestedRun === null && !requestedWorkflow && id && generation === 0) {
            controls(true);
            $('cancel').disabled = true;
            poll(id, ++generation);
        }
    } catch (error) { $('model-status').textContent = '模型连接检查失败：' + error.message; }
})();
