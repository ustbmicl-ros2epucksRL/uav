'use strict';
const root = location.pathname.startsWith('/network') ? '/network' : '';
const $ = id => document.getElementById(id);
const activeKey = 'research-agent-active:' + root;
const agentQuery = new URLSearchParams(location.search || '');
const embedded = agentQuery.get('embed') === '1';
if (embedded) {
    document.body.classList.add('agent-embedded');
    window.parent.postMessage({type:'platform-assistant-ready'}, '*');
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.stopPropagation();
            window.parent.postMessage({type:'platform-assistant-close'}, '*');
        }
    });
}
const initialScene = agentQuery.get('scene');
if (['urban','corridor','electromagnetic'].includes(initialScene)) $('scene').value = initialScene;
let record = null, timer = null, generation = 0;
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
    $('run').disabled = active; $('cancel').disabled = !active;
    $('history').disabled = active;
    for (const id of ['workflow','scene','task','mode']) $(id).disabled = active;
}
const sceneNames = {urban:'园区高密度建筑区', corridor:'物流主干道路', electromagnetic:'园区复杂电磁区'};
const taskNames = ['三维覆盖', '干扰与协同', '按需规划', '运行平台', '示范验证'];
const workspaceNames = {station:'区域地面站', planning:'感知网络规划', service:'低空服务管理'};
const toolNames = {
    scene_snapshot:'场景配置', diagnose_coverage:'覆盖诊断', planning_records:'规划任务',
    assessment_results:'历史验算', twin_experiment:'孪生实验', proposal_kpi:'申报指标',
    read_research_task:'研究任务摘要', run_research_experiment:'研究算例'
};
const statusNames = {running:'进行中', completed:'已完成', failed:'失败', cancelled:'已停止', interrupted:'中断'};
const provenanceNames = {
    configuration_snapshot:'配置快照', synthetic_calculation:'合成计算', stored_records:'业务记录',
    stored_simulation_results:'历史仿真记录', synthetic_twin_experiment:'合成孪生记录',
    proposal_document:'申报书', synthetic_only:'合成算例'
};
const workspace = workspaceNames[agentQuery.get('workspace')];
$('host-context').textContent = workspace || '研究助手';

function node(tag, value) {
    const element = document.createElement(tag);
    element.textContent = value;
    return element;
}

function scopeOf(request) {
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
    return link;
}

function report(id, result, data) {
    const target = $(id);
    target.replaceChildren();
    if (!result) {
        target.textContent = '尚未生成';
        return;
    }
    for (const [key,title] of Object.entries({conclusion:'结论', evidence:'依据', evidence_ids:'引用证据',
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
    if (Array.isArray(data.devices)) return '配置设备 ' + data.devices.length + ' 台';
    if (Array.isArray(data.tasks)) return '已读取 ' + data.tasks.length + ' 条规划任务';
    if (Array.isArray(data.assessments)) return '已读取 ' + data.assessments.length + ' 条历史验算';
    if (Number.isFinite(data.stats?.coverage)) return '合成覆盖率 ' + (data.stats.coverage * 100).toFixed(1) + '%';
    if (item.tool === 'twin_experiment') return data.available ? '实验 ' + data.id : data.note;
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
async function refreshHistory() {
    const data = await api('runs');
    const selected = $('history').value || record?.id;
    $('history').replaceChildren();
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '选择历史记录';
    $('history').append(placeholder);
    for (const item of data.runs) {
        const option = document.createElement('option'); option.value = item.id;
        option.textContent = [(item.request?.question || item.id).slice(0,65), statusNames[item.status] || item.status,
            scopeOf(item.request || {}), localTime(item.created_at)].join(' · ');
        $('history').append(option);
    }
    $('history').value = selected || '';
    $('history-status').textContent = data.runs.length ? '最近 ' + data.runs.length + ' 条记录' : '尚无历史记录。';
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
    $('scene-label').hidden = !platform; $('task-label').hidden = platform;
    $('scope-summary').textContent = scopeOf({workflow:$('workflow').value, scene:$('scene').value, task_id:$('task').value})
        + ($('mode').value === 'review' ? ' · 含复核' : '');
};
for (const id of ['scene','mode','task']) $(id).onchange = $('workflow').onchange;
$('run').onclick = async () => {
    const question = $('question').value.trim();
    if (question.length < 2) {
        $('stage').textContent = '请输入至少两个字符的问题。';
        $('question').focus();
        return;
    }
    clearTimeout(timer); const version = ++generation;
    controls(true); $('cancel').disabled = true; $('stage').textContent = '提交智能体任务…';
    try {
        const data = await api('runs', {question, task_id:Number($('task').value),
            mode:$('mode').value, workflow:$('workflow').value, scene:$('scene').value});
        if (version !== generation) return;
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
