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
const nav = {tasks:'规划任务',resources:'资源配置',diagnosis:'感知诊断',planning:'网络规划',plans:'方案管理',demonstration:'示范应用',research:'研究问题',agents:'研究智能体'};
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
function report(id, data) {
    const el = $(id); el.replaceChildren();
    if (!data) { el.textContent = '尚未生成'; return; }
    for (const [key,title] of Object.entries({conclusion:'结论',evidence:'依据',evidence_ids:'可核对证据编号',limitations:'局限',source_limitations:'工具声明的适用边界',next_steps:'下一步建议（未执行）'})) {
        if (data[key] === undefined) continue;
        const h = document.createElement('h3'), p = document.createElement('pre');
        h.textContent = title;
        p.textContent = Array.isArray(data[key]) ? data[key].map(x => '• ' + x).join('\n') : data[key];
        el.append(h,p);
    }
}
function controls(active) {
    $('run').disabled = active; $('cancel').disabled = !active;
    $('history').disabled = active;
    for (const id of ['workflow','scene','task','mode']) $(id).disabled = active;
}
function render(data) {
    record = data;
    const scenes = {urban:'园区高密度建筑区',corridor:'物流主干道路',electromagnetic:'园区复杂电磁区'};
    $('run-context').textContent = data.request ? '本次记录：' + (data.request.workflow === 'platform' ? scenes[data.request.scene] || data.request.scene : '研究子任务 ' + data.request.task_id) + ' · ' + data.request.question : '';
    $('stage').textContent = data.stage + (data.error ? '：' + data.error : '');
    const active = data.status === 'running'; controls(active);
    $('export').disabled = false; $('purpose').textContent = data.selection?.purpose || '';
    report('answer',data.report); report('review',data.review);
    $('trace').replaceChildren();
    for (const item of data.trace || []) {
        const d = document.createElement('details'), s = document.createElement('summary'), p = document.createElement('pre');
        s.textContent = item.tool + (item.result?.evidence_id ? ' · ' + item.result.evidence_id : item.arguments?.key ? ' · ' + item.arguments.key : '');
        p.textContent = JSON.stringify(item,null,2); d.append(s,p); $('trace').append(d);
    }
    if (!active) {
        clearTimeout(timer);
        if (localStorage.getItem(activeKey) === data.id) localStorage.removeItem(activeKey);
    }
}
async function refreshHistory() {
    const data = await api('runs');
    const selected = $('history').value;
    $('history').replaceChildren();
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '选择历史记录';
    $('history').append(placeholder);
    for (const item of data.runs) {
        const option = document.createElement('option'); option.value = item.id;
        option.textContent = (item.created_at || '') + ' · ' + item.status + ' · ' + (item.request?.question || item.id).slice(0,65);
        $('history').append(option);
    }
    $('history').value = selected;
}
async function poll(id, version) {
    try {
        const data = await api('runs/' + id);
        if (version !== generation) return;
        render(data);
        if (data.status === 'running') timer = setTimeout(() => poll(id,version),2000);
        else refreshHistory().catch(() => {});
    } catch (error) {
        if (version === generation) $('stage').textContent = '读取状态失败：' + error.message + '；刷新页面可重试。';
    }
}
$('workflow').onchange = () => {
    const platform = $('workflow').value === 'platform';
    $('scene-label').hidden = !platform; $('task-label').hidden = platform;
};
$('run').onclick = async () => {
    clearTimeout(timer); const version = ++generation;
    controls(true); $('cancel').disabled = true; $('stage').textContent = '提交智能体任务…';
    try {
        const data = await api('runs', {question:$('question').value, task_id:Number($('task').value),
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
$('history-refresh').onclick = () => refreshHistory().catch(error => { $('stage').textContent = error.message; });
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
    $('workflow').onchange();
    refreshHistory().catch(error => { $('stage').textContent = '历史记录读取失败：' + error.message; });
    api('tools').then(data => { $('tool-catalog').textContent = data.tools.map(tool => tool.name + '：' + tool.description).join('\n'); })
        .catch(error => { $('tool-catalog').textContent = error.message; });
    try {
        const status = await api('status');
        $('model-status').textContent = status.provider + ' · ' + status.model + ' · ' + (status.available ? '模型已安装，可开始研究' : '模型未就绪；仍可查看历史及调用只读工具');
        const id = status.active_runs[0] || localStorage.getItem(activeKey) || localStorage.getItem('research-agent-active');
        if (id) { controls(true); poll(id,++generation); }
    } catch (error) { $('model-status').textContent = '模型连接检查失败：' + error.message; }
})();
