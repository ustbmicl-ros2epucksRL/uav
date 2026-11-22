'use strict';
const byId = id => document.getElementById(id);
const statuses = {running:'运行中', completed:'已完成', failed:'失败', interrupted:'已中断', cancelled:'已停止'};
const workflows = {platform:'平台诊断', research:'研究算例'};
const scenes = {urban:'园区高密度建筑区', corridor:'物流主干道路', electromagnetic:'园区复杂电磁区'};
const pageSize = 20;
let offset = 0;
let filters = new URLSearchParams();
let queryVersion = 0;
let connectionVersion = 0;

function restoreLocation() {
    const params = new URLSearchParams(location.search);
    byId('query').value = (params.get('q') || '').slice(0, byId('query').maxLength);
    byId('status-filter').value = Object.hasOwn(statuses, params.get('status')) ? params.get('status') : '';
    byId('workflow-filter').value = Object.hasOwn(workflows, params.get('workflow')) ? params.get('workflow') : '';
    filters = formFilters();
    const page = Number(params.get('page'));
    offset = Number.isSafeInteger(page * pageSize) && page >= 1 && Number.isInteger(page)
        ? (page - 1) * pageSize : 0;
}

function writeLocation(replace = false) {
    const params = new URLSearchParams(filters);
    if (offset > 0) params.set('page', offset / pageSize + 1);
    const search = params.size ? '?' + params.toString() : '';
    if (location.search !== search) {
        history[replace ? 'replaceState' : 'pushState'](null, '', location.pathname + search);
    }
}


function element(tag, text) {
    const value = document.createElement(tag);
    value.textContent = text;
    return value;
}

async function read(path) {
    const response = await fetch('api/research-agent/' + path, {cache:'no-store'});
    if (!response.ok) throw Error('HTTP ' + response.status);
    return response.json();
}

function renderSummary(summary) {
    byId('total').textContent = summary.total;
    for (const status of Object.keys(statuses)) {
        byId(status).textContent = summary.by_status[status] ?? 0;
    }
    byId('workflow-counts').textContent = Object.entries(workflows)
        .map(([key, label]) => label + ' ' + (summary.by_workflow[key] ?? 0) + ' 次').join(' · ');
    byId('unreadable').hidden = !summary.unreadable;
    byId('unreadable').textContent = summary.unreadable ? '有 ' + summary.unreadable + ' 份记录无法读取，未计入统计。' : '';
}

function clearSummary() {
    for (const key of ['total', ...Object.keys(statuses)]) byId(key).textContent = '—';
    byId('workflow-counts').textContent = '';
    byId('unreadable').hidden = true;
}

function recordRow(run) {
    const row = document.createElement('article');
    row.className = 'run';
    const info = document.createElement('div');
    const request = run.request || {};
    const link = element('a', request.question || run.id);
    link.href = 'agents?run=' + encodeURIComponent(run.id);
    const heading = document.createElement('h3');
    heading.append(link);
    info.append(heading);
    const scope = request.workflow === 'platform' ? scenes[request.scene] || request.scene
        : (request.task_id ? '子任务 ' + request.task_id : '');
    const parts = [workflows[request.workflow], scope];
    if (Number.isInteger(run.step_count)) parts.push(run.step_count + ' 个步骤');
    if (Number.isInteger(run.tool_count)) parts.push(run.tool_count + ' 条工具记录');
    info.append(element('p', parts.filter(Boolean).join(' · ')));
    if (run.stage && run.status !== 'completed') info.append(element('p', run.stage));
    const created = new Date(run.created_at);
    const time = element('time', run.created_at && !Number.isNaN(created.getTime())
        ? created.toLocaleString('zh-CN', {hour12:false}) : '时间未记录');
    if (run.created_at) time.dateTime = run.created_at;
    const badge = element('span', statuses[run.status] || '状态未记录');
    badge.className = 'badge ' + (Object.hasOwn(statuses, run.status) ? run.status : '');
    row.append(info, time, badge);
    return row;
}

async function loadRuns() {
    const version = ++queryVersion;
    const params = new URLSearchParams(filters);
    params.set('limit', pageSize);
    params.set('offset', offset);
    byId('records-status').textContent = '正在读取运行记录…';
    byId('run-list').replaceChildren();
    byId('page-info').textContent = '';
    byId('previous').disabled = true;
    byId('next').disabled = true;
    try {
        const data = await read('management/runs?' + params);
        if (version !== queryVersion) return;
        if (offset > 0 && offset >= data.total) {
            offset = Math.max(0, (Math.ceil(data.total / pageSize) - 1) * pageSize);
            writeLocation(true);
            return loadRuns();
        }
        renderSummary(data.summary);
        byId('run-list').replaceChildren(...data.runs.map(recordRow));
        byId('records-status').textContent = data.total ? '共 ' + data.total + ' 条，按创建时间倒序'
            : filters.size ? '没有符合当前筛选条件的记录。' : '尚无运行记录，可在上方选择工作流发起分析。';
        byId('page-info').textContent = data.total
            ? '第 ' + (offset / pageSize + 1) + ' / ' + Math.ceil(data.total / pageSize) + ' 页' : '';
        byId('previous').disabled = offset === 0;
        byId('next').disabled = offset + pageSize >= data.total;
    } catch (error) {
        if (version !== queryVersion) return;
        clearSummary();
        byId('records-status').textContent = '运行记录读取失败 · ' + error.message + '。请刷新重试。';
    }
}

function formFilters() {
    const values = new URLSearchParams();
    for (const [param, id] of [['q','query'], ['status','status-filter'], ['workflow','workflow-filter']]) {
        const value = byId(id).value.trim();
        if (value) values.set(param, value);
    }
    return values;
}

function applyFilters(event) {
    event?.preventDefault();
    filters = formFilters();
    return showPage(0);
}

function showPage(nextOffset) {
    offset = nextOffset;
    writeLocation();
    return loadRuns();
}

function refreshConnections() {
    const version = ++connectionVersion;
    read('status').then(status => {
        if (version !== connectionVersion) return;
        byId('model-status').textContent = [status.provider, status.model, status.available ? '模型可用' : '模型未就绪']
            .filter(Boolean).join(' · ');
    }).catch(error => {
        if (version === connectionVersion) byId('model-status').textContent = '模型连接检查失败 · ' + error.message;
    });
    read('tools').then(data => {
        if (version !== connectionVersion) return;
        const entries = data.tools.flatMap(tool => [element('dt',tool.name), element('dd',tool.description)]);
        byId('tools').replaceChildren(...entries);
        byId('tool-count').textContent = '（' + data.tools.length + '）';
        byId('tool-status').textContent = data.tools.length ? '' : '当前未注册工具。';
    }).catch(error => {
        if (version !== connectionVersion) return;
        byId('tools').replaceChildren();
        byId('tool-count').textContent = '';
        byId('tool-status').textContent = '目录读取失败 · ' + error.message;
    });
}

byId('filters').onsubmit = applyFilters;
byId('status-filter').onchange = applyFilters;
byId('workflow-filter').onchange = applyFilters;
byId('previous').onclick = () => showPage(Math.max(0, offset - pageSize));
byId('next').onclick = () => showPage(offset + pageSize);
byId('refresh').onclick = () => {
    refreshConnections();
    return loadRuns();
};
window.addEventListener('popstate', () => { restoreLocation(); loadRuns(); });
window.addEventListener('pageshow', event => {
    if (event.persisted) {
        restoreLocation();
        refreshConnections();
        loadRuns();
    }
});
restoreLocation();
refreshConnections();
loadRuns();
