const root = location.pathname.startsWith('/network') ? '/network' : '';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
const meters = v => v == null ? '—' : v.toFixed(1) + ' m';
const STATE = {
    ok: ['#2a9d6f', '可定位且理论误差合格'], imprecise: ['#e0a030', '理论误差超限'],
    unlocatable: ['#d9792b', '单站，无法定位'], blind: ['#c0392b', '漏看（遮挡/干扰/超视场）'], invalid: ['#7f8c8d', '轨迹点在建筑内'],
};
const TYPE_COLOR = {RADAR: '#5dade2', EO: '#f5b041', RF: '#af7ac5', IR: '#ec7063'};
const POINT_VIEW = {1: ['ignore_occlusion', 'method'], 2: ['ignore_interference', 'method'], 3: ['method', 'baseline'], 4: ['method', 'baseline'], 5: ['method', 'baseline']};
let record = null, selectedPoint = 1, timer = null, run = null, poll = null;
const session = localStorage.getItem('twin-session') || (() => {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(8)), b => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem('twin-session', id);
    return id;
})();

async function api(path, options = {}) {
    const response = await fetch(root + path, {headers: {'Content-Type': 'application/json'}, ...options});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
    return body;
}

function status(text, error = false) {
    $('status').textContent = text;
    $('status').className = error ? 'error' : '';
}

const deployment = key => record.result.deployments.find(row => row.key === key);
const table = (head, rows, classes = []) => `<table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map((row, i) => `<tr class="${classes[i] || ''}">${row.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;

$('navigation').innerHTML = Object.entries({tasks: '规划任务', resources: '资源配置', diagnosis: '感知诊断', planning: '网络规划', plans: '方案管理', acceptance: '任务验证', twin: '研究必要性孪生', research: '研究问题', demonstration: '示范应用'})
    .map(([path, label]) => `<a href="${root}/${path}" class="${path === 'twin' ? 'active' : ''}">${label}</a>`).join('');

async function loadHistory(selected) {
    const {experiments} = await api('/api/twin-experiments');
    $('history').innerHTML = experiments.map(row => `<option value="${row.id}">${escapeHtml(row.created_at)} · 预算 ${row.budget} 万元 · ${row.seeds} 种子 · ${row.id.slice(0, 8)}</option>`).join('');
    if (selected) $('history').value = selected;
    return experiments;
}

async function open(id) {
    status('正在读取实验…');
    record = await api('/api/twin-experiments/' + id);
    render();
    status(record.scene_changed ? '场景已在实验后修改；本页显示的是冻结输入下的结果，需要时请重新运行。' : '实验已加载。');
}

async function runExperiment() {
    $('run').disabled = true;
    status('正在计算：规划四种部署并做成对蒙特卡洛评价…');
    try {
        const body = {scene: 'urban', budget: +$('budget').value, seed_count: +$('seeds').value, precision_m: +$('precision').value};
        record = await api('/api/twin-experiments', {method: 'POST', body: JSON.stringify(body)});
        await loadHistory(record.id);
        render();
        status('实验完成并已存档。');
    } catch (error) {
        status('实验未完成：' + error.message, true);
    } finally {
        $('run').disabled = false;
    }
}

async function recompute() {
    if (!record) return;
    $('recompute').disabled = true;
    status('正在从冻结输入复算…');
    try {
        record.reproducibility = await api(`/api/twin-experiments/${record.id}/recompute`, {method: 'POST'});
        renderRepro();
        status(record.reproducibility.matches ? '复算结果与存档一致。' : '复算结果与存档不一致，请检查模型版本。', !record.reproducibility.matches);
    } catch (error) {
        status('复算失败：' + error.message, true);
    } finally {
        $('recompute').disabled = false;
    }
}

function renderRepro() {
    const check = record.reproducibility;
    $('repro').innerHTML = `输入摘要 <span class="mono">${record.input_digest.slice(0, 16)}</span> · 结果摘要 <span class="mono">${record.result_digest.slice(0, 16)}</span>` +
        (check ? ` · ${escapeHtml(check.checked_at)} 复算${check.matches && check.input_matches ? '<b class="pass"> 一致</b>' : '<b class="fail"> 不一致</b>'}` : ' · 尚未复算核对');
}

function render() {
    const inputs = record.inputs;
    $('context').innerHTML = [
        ['系统', '感知网络规划'], ['场景', `${escapeHtml(inputs.scene.name || inputs.scene.id)}（版本 ${inputs.scene_revision}）`],
        ['实验', record.id.slice(0, 8)], ['指标版本', inputs.kpi_version], ['覆盖模型', inputs.model_version],
        ['参考轨迹', `${escapeHtml(inputs.trajectory.id)}，${inputs.trajectory.points.length} s`], ['证据等级', '合成孪生'],
    ].map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join('');
    $('assistant-context').textContent = `场景：建筑区 · 实验 ${record.id.slice(0, 8)} · 助手读取最近一次实验摘要与考核指标，只读不改。`;
    $('budget').value = inputs.budget;
    $('seeds').value = inputs.seeds.length;
    $('precision').value = inputs.environment.precision_m;
    renderRepro();
    renderChain();
    renderTable();
    const options = record.result.deployments.map(row => `<option value="${row.key}">${escapeHtml(row.label)}</option>`).join('');
    for (const id of ['primary', 'compare', 'segment-deployment']) $(id).innerHTML = options;
    $('primary').value = 'method';
    $('compare').value = 'baseline';
    $('segment-deployment').value = 'baseline';
    $('time').max = inputs.trajectory.points.length - 1;
    $('time').value = 0;
    $('limitations').innerHTML = record.result.limitations.map(item => `<li>${escapeHtml(item)}</li>`).join('');
    $('legend').innerHTML = Object.values(STATE).map(([color, label]) => `<span><i style="background:${color}"></i>${label}</span>`).join('') +
        Object.entries(TYPE_COLOR).slice(0, 3).map(([type, color]) => `<span><i style="background:${color};border-radius:50%"></i>${type}</span>`).join('') +
        '<span><i style="border:2px solid #fff;background:transparent;border-radius:50%"></i>新增设备</span>';
    renderSegments();
    draw();
}

function supported(point) {
    return !/未得到支持|无法判断/.test(point.necessity);
}

function renderChain() {
    const points = record.result.research_points;
    $('chain').innerHTML = points.map((p, i) => `${i ? '<span class="arrow">→</span>' : ''}<button class="point ${p.id === selectedPoint ? 'active' : ''}" data-point="${p.id}">
        <small>${escapeHtml(p.subproject)}</small><strong>${escapeHtml(p.title)}</strong>
        <span class="verdict ${p.id <= 3 ? (supported(p) ? 'supported' : 'unsupported') : ''}">${p.id <= 3 ? (supported(p) ? '本实验支持其必要性' : '本实验未支持') : escapeHtml(p.evidence_level)}</span></button>`).join('');
    $('relations').innerHTML = record.result.relations.map(r => `<li>子项目${'一二三四五'[r.source - 1]} → 子项目${'一二三四五'[r.target - 1]}：${escapeHtml(r.meaning)}</li>`).join('');
    const p = points.find(item => item.id === selectedPoint);
    $('point-detail').innerHTML = `<h3>${escapeHtml(p.subproject)} · ${escapeHtml(p.title)}</h3><dl>
        <dt>研究问题</dt><dd>${escapeHtml(p.question)}</dd>
        <dt>本实验发现</dt><dd>${escapeHtml(p.finding)}</dd>
        <dt>必要性（反事实对比）</dt><dd>${escapeHtml(p.necessity)}</dd>
        <dt>上下游</dt><dd>${p.depends_on ? '依赖子项目' + p.depends_on.join('、') : '链条起点'}；${p.feeds ? '输出给子项目' + p.feeds.join('、') : '链条终点'}</dd>
        <dt>待补证据</dt><dd>${p.pending.map(escapeHtml).join('；')}</dd>
        <dt>证据等级</dt><dd>${escapeHtml(p.evidence_level)}：${escapeHtml(p.evidence_note)}</dd></dl>
        <div class="actions"><button class="secondary" id="show-point">在回放中对照</button><button class="secondary" id="ask-point">让助手说明这一项</button></div>`;
    $('show-point').onclick = () => {
        const [primary, compare] = POINT_VIEW[p.id];
        $('primary').value = primary;
        $('compare').value = compare;
        $('segment-deployment').value = primary;
        jumpToDifference();
        renderSegments();
        $('replay').scrollIntoView({behavior: 'smooth'});
    };
    $('ask-point').onclick = () => {
        $('question').value = `${p.subproject}「${p.title}」的必要性在最近一次孪生实验里得到支持了吗？请引用对比方案的数值，并说明还缺哪些证据。`;
        $('question').focus();
    };
}

function renderTable() {
    const rows = record.result.deployments;
    $('deployments').innerHTML = table(
        ['方案', '新增设备', '新增费用 / 万元', '轨迹覆盖', '理论合格', '理论最长连续不可用', '模拟定位合格', '模拟最长缺口（中位数）', 'CRLB 中位数', '模拟误差中位数', '不可用时长按原因'],
        rows.map(row => {
            const m = row.metrics;
            return [`<b>${escapeHtml(row.label)}</b><br><span class="muted small">${escapeHtml(row.description)}</span>`,
                row.added.length ? row.added.map(escapeHtml).join('<br>') : '无', m.added_cost, pct(m.track_coverage), pct(m.theoretical_pass),
                m.longest_gap_theoretical_s + ' s', pct(m.simulated_pass), m.simulated_longest_gap_median_s + ' s', meters(m.crlb_median_m), meters(m.simulated_median_m),
                Object.entries(m.unavailable_seconds_by_cause).map(([k, v]) => `${escapeHtml(k)} ${v} s`).join('<br>') || '无'];
        }), rows.map(row => row.key === 'method' ? 'method' : ''));
}

function renderSegments() {
    if (!record) return;
    const row = deployment($('segment-deployment').value);
    $('segments').innerHTML = row.segments.length ? table(['时段', '时长', '原因', '位置 (x, y, z)', ''],
        row.segments.map((s, i) => [`${s.start}–${s.end} s`, s.duration_s + ' s', `<span style="color:${STATE[s.state][0]}">●</span> ${escapeHtml(s.cause)}`,
            s.position.map(v => Math.round(v)).join(', '), `<button class="secondary" data-segment="${i}">定位到回放</button>`]))
        : '<p class="empty">该方案在参考轨迹上没有理论缺测段。</p>';
}

function bounds() {
    const scene = record.inputs.scene;
    const xs = [], ys = [];
    for (const b of scene.buildings) { xs.push(b.x, b.x + b.w); ys.push(b.y, b.y + b.d); }
    for (const d of [...scene.devices, ...scene.candidates]) { xs.push(d.x); ys.push(d.y); }
    for (const [x, y] of record.inputs.trajectory.points) { xs.push(x); ys.push(y); }
    return {x0: Math.min(...xs) - 40, x1: Math.max(...xs) + 40, y0: Math.min(...ys) - 40, y1: Math.max(...ys) + 40};
}

function drawMap(t) {
    const canvas = $('map'), ctx = canvas.getContext('2d');
    const b = bounds(), pad = 16;
    const scale = Math.min((canvas.width - 2 * pad) / (b.x1 - b.x0), (canvas.height - 2 * pad) / (b.y1 - b.y0));
    const X = x => pad + (x - b.x0) * scale, Y = y => canvas.height - pad - (y - b.y0) * scale;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const scene = record.inputs.scene;
    for (const bd of scene.buildings) {
        const shade = Math.min(1, bd.h / 110);
        ctx.fillStyle = `rgba(160,180,200,${0.25 + 0.5 * shade})`;
        ctx.fillRect(X(bd.x), Y(bd.y + bd.d), bd.w * scale, bd.d * scale);
    }
    ctx.strokeStyle = 'rgba(255,255,255,.35)';
    for (const c of scene.candidates) {
        ctx.strokeRect(X(c.x) - 4, Y(c.y) - 4, 8, 8);
    }
    const primary = deployment($('primary').value);
    const added = new Set(primary.added);
    for (const d of primary.devices) {
        const color = TYPE_COLOR[d.type] || '#ccc';
        const reach = Math.min(d.range || 300, 260) * scale, az = (d.azimuth || 0) * Math.PI / 180, half = Math.min(d.fov || 360, 359) * Math.PI / 360;
        ctx.fillStyle = color + '22';
        ctx.beginPath();
        ctx.moveTo(X(d.x), Y(d.y));
        ctx.arc(X(d.x), Y(d.y), reach, -Math.PI / 2 + az - half, -Math.PI / 2 + az + half);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(X(d.x), Y(d.y), added.has(d.id) ? 7 : 5, 0, 2 * Math.PI);
        ctx.fill();
        if (added.has(d.id)) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke(); ctx.lineWidth = 1; }
    }
    const points = record.inputs.trajectory.points;
    for (let i = 1; i < points.length; i++) {
        ctx.strokeStyle = STATE[primary.timeline[i].state][0];
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(X(points[i - 1][0]), Y(points[i - 1][1]));
        ctx.lineTo(X(points[i][0]), Y(points[i][1]));
        ctx.stroke();
    }
    ctx.lineWidth = 1;
    const [px, py, pz] = points[t];
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(X(px), Y(py), 8, 0, 2 * Math.PI);
    ctx.fill();
    ctx.fillStyle = '#112e43';
    ctx.font = 'bold 12px system-ui';
    ctx.fillText('✈', X(px) - 6, Y(py) + 4);
    ctx.fillStyle = '#fff';
    ctx.font = '13px system-ui';
    ctx.fillText(`${primary.label} · t=${t} s · 高度 ${pz} m`, 12, 20);
}

function drawStrips(t) {
    const canvas = $('strips'), ctx = canvas.getContext('2d');
    const rows = record.result.deployments, n = record.inputs.trajectory.points.length;
    const left = 150, h = (canvas.height - 10) / rows.length, w = (canvas.width - left - 6) / n;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = '12px system-ui';
    rows.forEach((row, r) => {
        ctx.fillStyle = row.key === $('primary').value ? '#146d80' : '#526b7c';
        ctx.fillText(row.label.replace('反事实：', ''), 4, r * h + h / 2 + 8);
        row.timeline.forEach((s, i) => {
            ctx.fillStyle = STATE[s.state][0];
            ctx.fillRect(left + i * w, r * h + 6, Math.ceil(w), h - 6);
        });
    });
    ctx.strokeStyle = '#112e43';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(left + (t + 0.5) * w, 0);
    ctx.lineTo(left + (t + 0.5) * w, canvas.height);
    ctx.stroke();
    ctx.lineWidth = 1;
}

function drawErrors(t) {
    const canvas = $('errors'), ctx = canvas.getContext('2d');
    const primary = deployment($('primary').value), compare = deployment($('compare').value);
    const limit = record.inputs.environment.precision_m, top = limit * 3, n = primary.timeline.length;
    const left = 40, right = 8, pad = 14;
    const X = i => left + i * (canvas.width - left - right) / (n - 1);
    const Y = v => canvas.height - pad - Math.min(v, top) / top * (canvas.height - 2 * pad);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#637b8d';
    ctx.font = '11px system-ui';
    for (const v of [0, limit, 2 * limit, top]) { ctx.fillText(v + ' m', 2, Y(v) + 4); }
    ctx.strokeStyle = '#a13c28';
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(left, Y(limit)); ctx.lineTo(canvas.width - right, Y(limit)); ctx.stroke();
    ctx.setLineDash([]);
    const line = (row, color, width) => {
        ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath();
        let pen = false;
        row.timeline.forEach((s, i) => {
            if (s.crlb_m == null) { pen = false; return; }
            pen ? ctx.lineTo(X(i), Y(s.crlb_m)) : ctx.moveTo(X(i), Y(s.crlb_m));
            pen = true;
        });
        ctx.stroke(); ctx.lineWidth = 1;
    };
    if (compare.key !== primary.key) line(compare, '#a9b8c4', 1.5);
    line(primary, '#146d80', 2);
    ctx.fillStyle = '#e0a030';
    primary.timeline.forEach((s, i) => {
        if (s.simulated_median_error_m != null) { ctx.beginPath(); ctx.arc(X(i), Y(s.simulated_median_error_m), 2.2, 0, 2 * Math.PI); ctx.fill(); }
    });
    ctx.strokeStyle = '#112e43';
    ctx.beginPath(); ctx.moveTo(X(t), pad / 2); ctx.lineTo(X(t), canvas.height - pad / 2); ctx.stroke();
}

function drawSample(t) {
    $('sample').innerHTML = table(['方案', '状态', '探测概率', '覆盖站数', 'CRLB', '模拟误差中位数', '模拟可定位比例'],
        record.result.deployments.map(row => {
            const s = row.timeline[t];
            return [escapeHtml(row.label.replace('反事实：', '')), `<span style="color:${STATE[s.state][0]}">●</span> ${escapeHtml(s.cause)}`,
                s.pd == null ? '—' : s.pd.toFixed(2), s.stations, meters(s.crlb_m), meters(s.simulated_median_error_m), pct(s.simulated_fix_fraction)];
        }));
}

function draw() {
    if (!record) return;
    const t = +$('time').value;
    $('time-label').textContent = `${t} s / ${record.inputs.trajectory.points.length - 1} s`;
    drawMap(t);
    drawStrips(t);
    drawErrors(t);
    drawSample(t);
}

function jumpToDifference() {
    const a = deployment($('primary').value).timeline, b = deployment($('compare').value).timeline;
    const i = a.findIndex((s, k) => s.state !== b[k].state);
    $('time').value = i < 0 ? 0 : i;
    draw();
}

function togglePlay() {
    if (timer) { clearInterval(timer); timer = null; $('play').textContent = '播放'; return; }
    $('play').textContent = '暂停';
    timer = setInterval(() => {
        const next = (+$('time').value + 1) % (+$('time').max + 1);
        $('time').value = next;
        draw();
    }, 250);
}

$('chain').addEventListener('click', event => {
    const button = event.target.closest('[data-point]');
    if (!button) return;
    selectedPoint = +button.dataset.point;
    renderChain();
});
$('segments').addEventListener('click', event => {
    const button = event.target.closest('[data-segment]');
    if (!button) return;
    const segment = deployment($('segment-deployment').value).segments[+button.dataset.segment];
    $('primary').value = $('segment-deployment').value;
    $('time').value = segment.start;
    draw();
    $('replay').scrollIntoView({behavior: 'smooth'});
});
$('strips').addEventListener('click', event => {
    const rect = event.target.getBoundingClientRect(), n = record.inputs.trajectory.points.length;
    const x = (event.clientX - rect.left) * event.target.width / rect.width;
    if (x < 150) return;
    $('time').value = Math.max(0, Math.min(n - 1, Math.floor((x - 150) / ((event.target.width - 156) / n))));
    draw();
});
$('time').addEventListener('input', draw);
$('primary').addEventListener('change', draw);
$('compare').addEventListener('change', draw);
$('segment-deployment').addEventListener('change', renderSegments);
$('play').addEventListener('click', togglePlay);
$('run').addEventListener('click', runExperiment);
$('recompute').addEventListener('click', recompute);
$('history').addEventListener('change', () => open($('history').value).catch(error => status(error.message, true)));

// ---------- 研究助手 ----------
const CHIPS = [
    '五个子项目之间是什么关系？哪一项的必要性在本实验中证据最强？',
    '规划时忽略遮挡或干扰，放回参考环境后差多少？',
    '补站后的改善和增加的费用分别是多少？',
    '当前考核指标版本是什么？哪些只是历史对照？',
    '这些孪生结果能否写成外场精度？还缺哪些证据？',
];
$('chips').innerHTML = CHIPS.map((q, i) => `<button type="button" data-chip="${i}">${escapeHtml(q.length > 18 ? q.slice(0, 18) + '…' : q)}</button>`).join('');
$('chips').addEventListener('click', event => {
    const button = event.target.closest('[data-chip]');
    if (button) { $('question').value = CHIPS[+button.dataset.chip]; $('question').focus(); }
});

function duration(step) {
    if (!step.started_at) return '';
    const end = step.finished_at ? new Date(step.finished_at) : new Date();
    const seconds = Math.max(0, (end - new Date(step.started_at)) / 1000);
    return seconds < 60 ? seconds.toFixed(1) + ' s' : (seconds / 60).toFixed(1) + ' min';
}

function reportHtml(report, title) {
    if (!report) return '';
    return `<p><b>${title}</b></p><p>${escapeHtml(report.conclusion)}</p>
        ${report.evidence?.length ? `<p class="small"><b>依据</b></p><ul class="small">${report.evidence.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : ''}
        ${report.limitations?.length ? `<p class="small"><b>局限</b></p><ul class="small">${report.limitations.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : ''}
        ${report.next_steps?.length ? `<div class="suggestions"><b>建议（未执行，需人工决定）</b><ul class="small">${report.next_steps.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul></div>` : ''}
        ${report.evidence_ids?.length ? `<p class="muted small">引用证据：${report.evidence_ids.map(escapeHtml).join('、')}</p>` : ''}`;
}

function runHtml(item) {
    const steps = item.steps || [];
    const evidence = (item.trace || []).filter(t => t.result && t.result.evidence_id).map(t => t.result);
    const sourceLimits = item.report?.source_limitations || [];
    return `<div class="msg-user">${escapeHtml(item.request.question)}</div><div class="msg-agent">
        <ul class="steps">${steps.map(s => `<li class="step-${s.status}"><span>${escapeHtml(s.label)}</span><span>${duration(s)}</span></li>`).join('') || `<li class="step-running"><span>${escapeHtml(item.stage)}</span><span></span></li>`}</ul>
        ${evidence.map(e => `<div class="evidence-card"><b>${escapeHtml(e.tool)}</b> · ${escapeHtml(e.provenance)} · <span class="mono">${escapeHtml(e.evidence_id)}</span>
            <details><summary>原始数据（JSON）</summary><pre>${escapeHtml(JSON.stringify(e.data, null, 2))}</pre></details></div>`).join('')}
        ${reportHtml(item.report, '结论')}${reportHtml(item.review, '复核意见（同一模型，不等于独立实测）')}
        ${sourceLimits.length ? `<details><summary class="small">工具附带的适用边界（${sourceLimits.length}）</summary><ul class="small">${sourceLimits.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul></details>` : ''}
        ${item.status === 'failed' || item.status === 'interrupted' ? `<p class="agent-error">${escapeHtml(item.stage)}${item.error ? '：' + escapeHtml(item.error) : ''}</p>` : ''}
        ${item.status === 'cancelled' ? '<p class="agent-error">已停止。</p>' : ''}
        <p class="muted small">${escapeHtml(item.model || '')} · ${escapeHtml(item.created_at || '')}</p></div>`;
}

const shown = new Map();
function showRun(item) {
    shown.set(item.id, item);
    $('conversation').innerHTML = [...shown.values()].sort((a, b) => (a.created_at || '').localeCompare(b.created_at || '')).map(runHtml).join('') ||
        '<p class="muted small">向助手提问。它会冻结当前场景与最近一次孪生实验，选择只读工具取证，再给出带证据编号的回答。</p>';
    $('conversation').scrollTop = $('conversation').scrollHeight;
}

function busy(running) {
    $('send').hidden = running;
    $('stop').hidden = !running;
    $('question').disabled = running;
}

async function pollRun(id) {
    clearTimeout(poll);
    try {
        const item = await api('/api/research-agent/runs/' + id);
        showRun(item);
        if (item.status === 'running') { poll = setTimeout(() => pollRun(id), 2000); return; }
        run = null;
        busy(false);
        loadRuns();
    } catch (error) {
        run = null;
        busy(false);
        $('conversation').insertAdjacentHTML('beforeend', `<p class="agent-error">读取助手进度失败：${escapeHtml(error.message)}</p>`);
    }
}

$('ask').addEventListener('submit', async event => {
    event.preventDefault();
    const question = $('question').value.trim();
    if (question.length < 2 || run) return;
    busy(true);
    try {
        const item = await api('/api/research-agent/runs', {method: 'POST', body: JSON.stringify({
            question, workflow: 'platform', scene: 'urban', mode: $('review').checked ? 'review' : 'single', session_id: session})});
        run = item.id;
        $('question').value = '';
        showRun(item);
        pollRun(item.id);
    } catch (error) {
        busy(false);
        $('conversation').insertAdjacentHTML('beforeend', `<p class="agent-error">助手未启动：${escapeHtml(error.message)}</p>`);
    }
});
$('stop').addEventListener('click', async () => {
    if (!run) return;
    clearTimeout(poll);
    try { showRun(await api(`/api/research-agent/runs/${run}/cancel`, {method: 'POST'})); } catch (error) { /* status refreshed below */ }
    run = null;
    busy(false);
});

async function loadRuns() {
    try {
        const {runs} = await api('/api/research-agent/runs?limit=20&session_id=' + session);
        $('runs').innerHTML = runs.length ? runs.map(r => `<button type="button" data-run="${r.id}">${escapeHtml(r.created_at)} · ${escapeHtml(r.status)}<br>${escapeHtml(r.request.question.slice(0, 40))}</button>`).join('')
            : '<p class="muted small">本浏览器会话尚无提问。</p>';
        return runs;
    } catch (error) {
        $('runs').innerHTML = `<p class="agent-error small">${escapeHtml(error.message)}</p>`;
        return [];
    }
}
$('runs').addEventListener('click', async event => {
    const button = event.target.closest('[data-run]');
    if (button) showRun(await api('/api/research-agent/runs/' + button.dataset.run));
});

async function modelStatus() {
    try {
        const s = await api('/api/research-agent/status');
        $('model-status').innerHTML = `<p>${escapeHtml(s.provider)} · ${escapeHtml(s.model)} · ${s.available ? '<span class="pass">可用</span>' : '<span class="fail">未就绪：' + escapeHtml(s.error || '模型未安装') + '</span>'}</p>`;
        if (s.active_runs?.length) $('model-status').innerHTML += `<p class="muted">有任务正在运行：${s.active_runs.map(id => id.slice(0, 8)).join('、')}</p>`;
    } catch (error) {
        $('model-status').innerHTML = `<p class="fail">无法读取模型状态：${escapeHtml(error.message)}</p>`;
    }
}

async function start() {
    $('conversation').innerHTML = '<p class="muted small">向助手提问。它会冻结当前场景与最近一次孪生实验，选择只读工具取证，再给出带证据编号的回答。</p>';
    modelStatus();
    const runs = await loadRuns();
    for (const r of runs.slice(0, 3).reverse()) {
        try {
            const item = await api('/api/research-agent/runs/' + r.id);
            showRun(item);
            if (item.status === 'running') { run = item.id; busy(true); pollRun(item.id); }
        } catch (error) { /* skip unreadable record */ }
    }
    try {
        const experiments = await loadHistory();
        if (experiments.length) await open(experiments[0].id);
        else await runExperiment();
    } catch (error) {
        status('无法读取实验档案：' + error.message, true);
    }
}
start();
