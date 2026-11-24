const root = location.pathname.startsWith('/network') ? '/network' : '';
const $ = id => document.getElementById(id);
const workbench = typeof window !== 'undefined' && window.parent && window.parent !== window &&
    (window.frameElement?.dataset?.frame === 'planning' || new URLSearchParams(location.search || '').get('workbench') === '1');
if (workbench) document.body?.classList?.add?.('workbench-embedded');
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
const meters = v => v == null ? '—' : v.toFixed(1) + ' m';
const SCENE_LABELS = {urban: '建筑区', corridor: '物流主干道', electromagnetic: '复杂电磁区'};
const BSRC_LABELS = {synthetic: '新区高密楼宇模型', xiongan_gba: '雄安真实建筑轮廓', beijing_osm: '高层真实轮廓(OSM)'};
const MODE_LABELS = {
    counterfactual: '五方案反事实规划推演',
    coverage: '三维覆盖与遮挡盲区场',
    precision: '干扰衰减与协同精度场',
    flight: '无人机航路与跨站跟踪孪生',
};
const STATE = {
    ok: ['#2a9d6f', '可定位且理论误差合格'], imprecise: ['#e0a030', '理论误差超限'],
    unlocatable: ['#d9792b', '单站，无法定位'], blind: ['#c0392b', '漏看（遮挡/干扰/超视场）'], invalid: ['#7f8c8d', '轨迹点在建筑内'],
};
const TYPE_COLOR = {RADAR: '#5dade2', EO: '#f5b041', RF: '#af7ac5', IR: '#ec7063', ADSB: '#48c9b0'};
const POINT_VIEW = {
    1: ['ignore_occlusion', 'method'],
    2: ['ignore_interference', 'method'],
    3: ['coverage_only', 'method'],
    4: ['method', 'baseline'],
    5: ['method', 'baseline'],
};
let record = null, selectedPoint = 1, timer = null, run = null, poll = null;
let segmentRange = null, recordRevision = 0, segmentRevision = 0, spaceRevision = 0;
let currentMode = 'counterfactual';
let spaceData = null, flightTracks = [], activeFlightIndex = 0;
let engine3d = null;

const session = localStorage.getItem('twin-session') || (() => {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(8)), b => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem('twin-session', id);
    return id;
})();

function mountEngineIfReady() {
    if (engine3d || typeof window === 'undefined' || !window.TwinEngine3D || !$('twin3d-viewport')) return;
    try {
        engine3d = window.TwinEngine3D.mount($('twin3d-viewport'));
        engine3d.onTileStatus = text => { if ($('twin3d-tile-status')) $('twin3d-tile-status').textContent = text; };
        engine3d.onInspect = text => { if ($('twin3d-inspect')) $('twin3d-inspect').textContent = text; };
        engine3d.onPickSample = index => {
            if (currentMode === 'flight') {
                $('time').value = index;
                draw();
            } else {
                selectContinuousSegmentAt(index);
            }
        };
        if (record) draw();
    } catch (error) {
        if ($('twin3d-tile-status')) $('twin3d-tile-status').textContent = '三维引擎降级为平面模式：' + error.message;
    }
}
if (typeof window !== 'undefined') {
    window.addEventListener?.('twin-engine3d-ready', mountEngineIfReady);
    mountEngineIfReady();
}

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

const deployment = key => record.result.deployments.find(row => row.key === key) || record.result.deployments[0];
const table = (head, rows, classes = []) => `<table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map((row, i) => `<tr class="${classes[i] || ''}">${row.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;

$('navigation').innerHTML = Object.entries({
    overview: '研究总览', twin: '三维数字孪生', tasks: '规划任务', resources: '资源配置',
    diagnosis: '感知诊断', planning: '网络规划', plans: '方案管理', acceptance: '任务验证',
    research: '研究问题', demonstration: '示范应用',
}).map(([path, label]) => `<a href="${root}/${path}" class="${path === 'twin' ? 'active' : ''}">${label}</a>`).join('');

async function loadHistory(selected) {
    const {experiments} = await api('/api/twin-experiments');
    $('history').innerHTML = experiments.map(row => {
        const label = SCENE_LABELS[row.scene] || row.scene || '建筑区';
        const bsrc = row.building_source && row.building_source !== 'synthetic' ? ` · ${BSRC_LABELS[row.building_source] || row.building_source}` : '';
        return `<option value="${row.id}">${escapeHtml(label)}${escapeHtml(bsrc)} · ${escapeHtml(row.created_at)} · 预算 ${row.budget} 万 · ${row.id.slice(0, 8)}</option>`;
    }).join('');
    if (selected) $('history').value = selected;
    return experiments;
}

async function refreshSpaceLayer() {
    if (!record) return;
    const rev = ++spaceRevision;
    const inputs = record.inputs;
    const sceneId = inputs.scene.id || 'urban';
    const bsrc = inputs.scene.building_source || $('building-source')?.value || 'synthetic';
    const altVal = $('field-altitude')?.value;
    try {
        const res = await api('/api/twin-space', {
            method: 'POST',
            body: JSON.stringify({
                mode: currentMode,
                scene: sceneId,
                building_source: bsrc,
                experiment_id: record.id,
                deployment: $('primary')?.value || 'baseline',
                altitude_m: (currentMode === 'coverage' || currentMode === 'precision') && altVal ? Number(altVal) : null,
                cell_m: altVal ? 65 : 100,
                precision_m: inputs.environment.precision_m,
            }),
        });
        if (rev !== spaceRevision) return;
        spaceData = res;
        if (currentMode === 'coverage' || currentMode === 'precision') {
            engine3d?.setVoxels(res.voxels, currentMode, res.cell_m, res.pd_threshold, res.precision_m);
            const s = res.stats || {};
            if ($('mode-kpi-strip')) {
                $('mode-kpi-strip').innerHTML = [
                    `有效空域体素 <b>${(s.valid_cells || 0).toLocaleString()}</b>`,
                    `全域覆盖 <b>${pct(s.coverage)}</b>`,
                    `重点区覆盖 <b>${pct(s.focus_coverage)}</b>`,
                    `定位精度合格（≤${res.precision_m}m） <b>${pct(s.precision_pass_rate)}</b>`,
                ].join(' · ');
            }
        } else {
            engine3d?.clearVoxels();
            if (currentMode === 'flight') {
                flightTracks = res.tracks || [];
                const sel = $('flight-track-select');
                if (sel) {
                    sel.innerHTML = flightTracks.map((tr, idx) => `<option value="${idx}">${escapeHtml(tr.label)}（${escapeHtml(tr.origin)}）</option>`).join('');
                    activeFlightIndex = Math.min(activeFlightIndex, Math.max(0, flightTracks.length - 1));
                    sel.value = String(activeFlightIndex);
                }
                syncFlightTimeline();
            }
        }
        draw();
    } catch {
        // Keep base experiment playback working even if /api/twin-space is mocked out in unit tests
    }
}

function syncFlightTimeline() {
    if (currentMode !== 'flight' || !flightTracks.length) return;
    const tr = flightTracks[activeFlightIndex] || flightTracks[0];
    $('time').min = 0;
    $('time').max = Math.max(0, tr.points.length - 1);
    $('time').value = 0;
    if ($('mode-kpi-strip')) {
        $('mode-kpi-strip').innerHTML = [
            `航迹来源 <b>${escapeHtml(tr.origin)}</b>`,
            `沿线覆盖率 <b>${pct(tr.summary.coverage_rate)}</b>`,
            `协同定位合格率 <b>${pct(tr.summary.precision_pass_rate)}</b>`,
            `最长连续失锁 <b>${tr.summary.longest_gap_s} s</b>`,
        ].join(' · ');
    }
}

function setMode(mode) {
    if (!MODE_LABELS[mode]) return;
    currentMode = mode;
    if ($('twin-modes')) {
        for (const btn of $('twin-modes').querySelectorAll?.('[data-mode]') || []) {
            btn.classList?.toggle?.('active', btn.dataset.mode === mode);
        }
    }
    if ($('mode-summary-pill')) $('mode-summary-pill').textContent = MODE_LABELS[mode];
    if ($('mode-toolbar')) $('mode-toolbar').hidden = mode === 'counterfactual';
    if ($('altitude-slice-wrap')) $('altitude-slice-wrap').hidden = !(mode === 'coverage' || mode === 'precision');
    if ($('flight-track-wrap')) $('flight-track-wrap').hidden = mode !== 'flight';
    if (mode !== 'flight' && record && segmentRange) {
        $('time').min = segmentRange.start;
        $('time').max = segmentRange.end;
        $('time').value = segmentRange.start;
    }
    refreshSpaceLayer();
}

async function open(id, range) {
    const revision = ++recordRevision;
    status('正在读取实验…');
    const archive = await api('/api/twin-experiments/' + id);
    if (revision !== recordRevision) return;
    record = archive;
    if (!Array.from($('history').options).some(option => option.value === id)) {
        const label = SCENE_LABELS[archive.inputs?.scene?.id] || '合成场景';
        $('history').add(new Option(`${label} · ${archive.created_at} · ${id.slice(0, 8)}`, id));
    }
    $('history').value = id;
    render(range);
    status(record.scene_changed ? '场景已在实验后修改；本页显示的是冻结输入下的结果，需要时请重新运行。' : '数字孪生档案已加载。');
}

async function runExperiment() {
    $('run').disabled = true;
    const chosenScene = $('scene')?.value || 'urban';
    const chosenBsrc = $('building-source')?.value || 'synthetic';
    status(`正在计算「${SCENE_LABELS[chosenScene] || chosenScene} · ${BSRC_LABELS[chosenBsrc] || chosenBsrc}」：五方案规划与成对蒙特卡洛评价…`);
    const revision = ++recordRevision;
    try {
        const body = {
            scene: chosenScene,
            building_source: chosenBsrc,
            budget: +$('budget').value,
            seed_count: +$('seeds').value,
            precision_m: +$('precision').value,
        };
        const archive = await api('/api/twin-experiments', {method: 'POST', body: JSON.stringify(body)});
        if (revision !== recordRevision) return;
        record = archive;
        await loadHistory(record.id);
        if (revision !== recordRevision) return;
        render();
        status('数字孪生实验完成并已归档。');
    } catch (error) {
        status('实验未完成：' + error.message, true);
    } finally {
        $('run').disabled = false;
    }
}

async function recompute() {
    if (!record) return;
    const archive = record;
    $('recompute').disabled = true;
    status('正在从冻结输入复算…');
    try {
        archive.reproducibility = await api(`/api/twin-experiments/${archive.id}/recompute`, {method: 'POST'});
        if (archive !== record) return;
        renderRepro();
        status(record.reproducibility.matches ? '复算结果与存档一致。' : '复算结果与存档不一致，请检查模型版本。', !record.reproducibility.matches);
    } catch (error) {
        if (archive === record) status('复算失败：' + error.message, true);
    } finally {
        $('recompute').disabled = false;
    }
}

function renderRepro() {
    const check = record.reproducibility;
    $('repro').innerHTML = `输入摘要 <span class="mono">${record.input_digest.slice(0, 12)}</span> · 结果摘要 <span class="mono">${record.result_digest.slice(0, 12)}</span>` +
        (check ? ` · 复算${check.matches && check.input_matches ? '<b class="pass"> 一致</b>' : '<b class="fail"> 不一致</b>'}` : ' · 可复算核验');
}

function render(range) {
    const inputs = record.inputs;
    const sceneId = inputs.scene.id || 'urban';
    const bsrc = inputs.scene.building_source || 'synthetic';
    const shortScene = SCENE_LABELS[sceneId] || inputs.scene.name || sceneId;
    if ($('scene')) $('scene').value = sceneId;
    if ($('building-source')) $('building-source').value = bsrc;
    if ($('page-title')) $('page-title').textContent = `低空多模态感知三维数字孪生中心 · ${shortScene}`;
    if ($('replay-heading')) $('replay-heading').textContent = `${shortScene}实景三维数字孪生沙盘与航段对照`;
    $('context').innerHTML = [
        ['数字孪生底座', 'Three.js + WGS84 实景瓦片'],
        ['应用场景', `${escapeHtml(inputs.scene.name || sceneId)}（版本 ${inputs.scene_revision}）`],
        ['三维建筑源', `${escapeHtml(BSRC_LABELS[bsrc] || bsrc)}（${inputs.scene.buildings.length} 栋参与遮挡）`],
        ['实验档案', record.id.slice(0, 8)],
        ['指标口径', inputs.kpi_version],
        ['参考轨迹', `${escapeHtml(inputs.trajectory.id)}，${inputs.trajectory.points.length} 点`],
    ].map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join('');
    $('assistant-context').textContent = '请选择已归档的航段。';
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
    $('time').min = 0;
    $('time').max = inputs.trajectory.points.length - 1;
    $('time').value = 0;
    $('limitations').innerHTML = record.result.limitations.map(item => `<li>${escapeHtml(item)}</li>`).join('');
    $('legend').innerHTML = Object.values(STATE).map(([color, label]) => `<span><i style="background:${color}"></i>${label}</span>`).join('') +
        Object.entries(TYPE_COLOR).slice(0, 3).map(([type, color]) => `<span><i style="background:${color};border-radius:50%"></i>${type}</span>`).join('') +
        '<span><i style="border:2px solid #fff;background:transparent;border-radius:50%"></i>规划新增设备</span>';
    renderSegments();
    prepareSegment(range);
    refreshSpaceLayer();
}

function verdict(point) {
    const keyMap = {1: 'ignore_occlusion', 2: 'ignore_interference', 3: 'coverage_only'};
    const counterfactualKey = keyMap[point.id];
    if (counterfactualKey) {
        const otherRow = record.result.deployments.find(row => row.key === counterfactualKey);
        if (!otherRow) return point.id === 3 ? '同预算覆盖规划对照' : point.evidence_level;
        const method = deployment('method').metrics;
        const other = otherRow.metrics;
        const rateGain = method.theoretical_pass - other.theoretical_pass;
        const gapGain = other.longest_gap_theoretical_s - method.longest_gap_theoretical_s;
        if (rateGain >= 0 && gapGain >= 0 && (rateGain > 0 || gapGain > 0)) return '本航线对照显示改善';
        if (rateGain <= 0 && gapGain <= 0) return '本航线未显示改善';
        return '两项指标变化方向不同';
    }
    return point.evidence_level;
}

function renderChain() {
    const points = record.result.research_points;
    $('chain').innerHTML = points.map((p, i) => `${i ? '<span class="arrow">→</span>' : ''}<button class="point ${p.id === selectedPoint ? 'active' : ''}" data-point="${p.id}">
        <small>${escapeHtml(p.subproject)}</small><strong>${escapeHtml(p.title)}</strong>
        <span class="verdict">${escapeHtml(verdict(p))}</span></button>`).join('');
    $('relations').innerHTML = record.result.relations.map(r => `<li>子项目${'一二三四五'[r.source - 1]} → 子项目${'一二三四五'[r.target - 1]}：${escapeHtml(r.meaning)}</li>`).join('');
    const p = points.find(item => item.id === selectedPoint);
    $('point-detail').innerHTML = `<h3>${escapeHtml(p.subproject)} · ${escapeHtml(p.title)}</h3><dl>
        <dt>研究问题</dt><dd>${escapeHtml(p.question)}</dd>
        <dt>本实验发现</dt><dd>${escapeHtml(p.finding)}</dd>
        <dt>必要性（反事实对比）</dt><dd>${escapeHtml(p.necessity)}</dd>
        <dt>上下游</dt><dd>${p.depends_on ? '依赖子项目' + p.depends_on.join('、') : '链条起点'}；${p.feeds ? '输出给子项目' + p.feeds.join('、') : '链条终点'}</dd>
        <dt>待补证据</dt><dd>${p.pending.map(escapeHtml).join('；')}</dd>
        <dt>证据等级</dt><dd>${escapeHtml(p.evidence_level)}：${escapeHtml(p.evidence_note)}</dd></dl>
        <div class="actions"><button class="secondary" id="show-point">在三维沙盘中对照此项</button><button class="secondary" id="ask-point">让助手说明这一项</button></div>`;
    $('show-point').onclick = () => {
        if (currentMode !== 'counterfactual') setMode('counterfactual');
        const [primaryCandidate, compare] = POINT_VIEW[p.id];
        const primary = record.result.deployments.some(row => row.key === primaryCandidate) ? primaryCandidate : 'baseline';
        $('primary').value = primary;
        $('compare').value = compare;
        $('segment-deployment').value = primary;
        jumpToDifference();
        renderSegments();
        refreshSpaceLayer();
        $('replay').scrollIntoView({behavior: 'smooth'});
    };
    $('ask-point').onclick = () => {
        if (workbench) {
            $('assist-segment').click();
            return;
        }
        $('question').value = `${p.subproject}「${p.title}」在所选航段上有哪些对照证据？请引用方案数值并说明验证边界。`;
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
            s.position.map(v => Math.round(v)).join(', '), `<button class="secondary" data-segment="${i}">查看此段</button>`]))
        : '<p class="empty">该方案在参考轨迹上没有理论缺测段。</p>';
}

function prepareSegment(range) {
    const times = record.inputs.trajectory.times;
    const last = times.length - 1;
    ++segmentRevision;
    segmentRange = null;
    $('segment-result').hidden = true;
    $('send').disabled = true;
    if (timer) togglePlay();
    $('segment-picker').disabled = false;
    $('segment-end').min = $('segment-start').min = times[0];
    $('segment-end').max = $('segment-start').max = times[last];
    const preset = $('segment-preset');
    preset.replaceChildren(new Option('整条参考航线', `0,${last}`));
    for (const segment of deployment('baseline').segments) {
        preset.add(new Option(`${segment.start}–${segment.end} s · ${segment.cause}`,
            `${times.indexOf(segment.start)},${times.indexOf(segment.end)}`));
    }
    preset.add(new Option('自选起止时刻', 'custom'));
    if (range && (![range.start, range.end].every(value => /^\d+$/.test(value)) ||
            +range.start > +range.end || +range.end > last)) {
        $('segment-start').value = times[0];
        $('segment-end').value = times[last];
        $('segment-status').textContent = '链接中的航段超出档案范围，请重新选择。';
        draw();
        return;
    }
    selectSegment(range ? +range.start : 0, range ? +range.end : last);
}

function segmentLinks(start, end) {
    const station = new URL('/station', location.origin);
    station.search = new URLSearchParams({twin_experiment: record.id, start, end});
    const chat = new URL('/', location.origin);
    chat.search = new URLSearchParams({platform: 'planning', experiment: record.id, start, end, assistant: 1});
    $('station-segment').href = station.href;
    $('assist-segment').href = chat.href;
    document.querySelector('.segment-actions').hidden = !root;
}

async function selectSegment(start, end) {
    const times = record.inputs.trajectory.times;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start > end || end >= times.length) {
        $('segment-status').textContent = '请在本条航线的采样范围内选择起止时刻。';
        return;
    }
    const revision = ++segmentRevision;
    const selected = record;
    segmentRange = {start, end};
    if (timer) togglePlay();
    const preset = $('segment-preset');
    const key = `${start},${end}`;
    preset.value = Array.from(preset.options).some(option => option.value === key) ? key : 'custom';
    $('segment-start').value = times[start];
    $('segment-end').value = times[end];
    if (currentMode !== 'flight') {
        $('time').min = start;
        $('time').max = end;
        $('time').value = start;
    }
    const url = new URL(location.href);
    url.searchParams.set('experiment', record.id);
    url.searchParams.set('start', start);
    url.searchParams.set('end', end);
    window.history.replaceState(null, '', url);
    $('segment-status').textContent = '正在核对选定航段…';
    $('send').disabled = true;
    $('segment-result').hidden = true;
    draw();
    try {
        const report = await TwinSegment.analyze(selected, start, end);
        if (revision !== segmentRevision || selected !== record) return;
        renderSegmentReport(report);
        segmentLinks(start, end);
        $('segment-status').textContent = '';
        $('segment-result').hidden = false;
        const shortScene = SCENE_LABELS[record.inputs.scene.id] || '合成场景';
        $('assistant-context').textContent = `${shortScene} · 实验 ${record.id.slice(0, 8)} · ${report.start_s}–${report.end_s} s。新提问引用本航段。`;
        $('send').disabled = false;
    } catch (error) {
        if (revision === segmentRevision && selected === record) {
            $('segment-status').textContent = '航段分析未加载，请重试。' + error.message;
        }
    }
}

function renderSegmentReport(report) {
    const baseline = report.deployments.find(item => item.key === 'baseline');
    const method = report.deployments.find(item => item.key === 'method');
    $('segment-title').textContent = `${report.start_s}–${report.end_s} s · ${report.sample_count} 个航迹采样点`;
    const summary = baseline.causes.map(c => `${c.cause} ${c.samples} 点`).join('、');
    $('segment-diagnosis').textContent = summary
        ? `现有部署有 ${baseline.blind_count} 个探测盲点，${report.sample_count - baseline.pass_count} 个点未满足理论定位门槛。模型归因包括${summary}。`
        : '按当前模型，现有部署在所选航段均可探测且满足理论定位门槛。';
    const rows = report.deployments.map(item => {
        const cause = item.causes.map(c => `${escapeHtml(c.cause)}（${c.samples} 点）`).join('、');
        return [escapeHtml(item.label), `${item.added_devices} 台 / ${item.added_cost} 万元`,
            item.blind_count, `${item.pass_count}/${report.sample_count}（${pct(item.theoretical_pass)}）`,
            `${item.longest_gap_s} s`, cause || '均合格'];
    });
    $('segment-metrics').innerHTML = table(['布站方案', '全方案新增设备 / 费用', '探测盲点数', '理论定位合格点', '最长连续不合格', '模型归因'], rows,
        report.deployments.map(item => item.key === 'method' ? 'method' : ''));
    const deltas = report.comparisons.filter(c => c.key !== 'baseline').map(c => {
        const other = report.deployments.find(item => item.key === c.key);
        const name = other.label.replace('反事实：', '');
        const cost = c.equal_cost ? `两方案同为 ${method.added_cost} 万元` : `两方案分别为 ${method.added_cost}、${other.added_cost} 万元`;
        const rate = c.pass_delta_pp === 0 ? '持平' : `${c.pass_delta_pp > 0 ? '高' : '低'} ${Math.abs(c.pass_delta_pp).toFixed(1)} 个百分点`;
        const gap = c.gap_reduction_s === 0 ? '持平' : `${c.gap_reduction_s > 0 ? '缩短' : '延长'} ${Math.abs(c.gap_reduction_s)} s`;
        return `与「${name}」相比，${cost}，完整模型的合格比例${rate}，最长不合格时长${gap}。`;
    });
    $('segment-conclusion').replaceChildren(...deltas.map(text => {
        const paragraph = document.createElement('span');
        paragraph.textContent = text;
        return paragraph;
    }));
    $('segment-criterion').textContent = `使用相同航线和 ${report.budget} 万元新增预算上限，定位门槛 ${report.precision_m} m。合格比例按航段采样点统计，连续时长按每点 ${report.dt_s} s 累计。`;
}

$('segment-preset').addEventListener('change', event => {
    if (!record) return;
    if (event.target.value === 'custom') {
        $('segment-start').focus();
        return;
    }
    const [start, end] = event.target.value.split(',').map(Number);
    $('primary').value = 'baseline';
    $('compare').value = 'method';
    selectSegment(start, end);
});
$('segment-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!record) return;
    const times = record.inputs.trajectory.times;
    selectSegment(times.indexOf($('segment-start').valueAsNumber), times.indexOf($('segment-end').valueAsNumber));
});
for (const [id, action] of [['assist-segment', 'assistant'], ['station-segment', 'station']]) {
    $(id).addEventListener('click', event => {
        if (!workbench || !record || !segmentRange || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
        event.preventDefault();
        window.parent.postMessage({type: 'workbench-twin-segment', experimentId: record.id, ...segmentRange, action}, location.origin);
    });
}

function bounds() {
    const scene = record.inputs.scene;
    const xs = [], ys = [];
    for (const b of scene.buildings) { xs.push(b.x, b.x + b.w); ys.push(b.y, b.y + b.d); }
    for (const d of [...scene.devices, ...scene.candidates]) { xs.push(d.x); ys.push(d.y); }
    for (const [x, y] of record.inputs.trajectory.points) { xs.push(x); ys.push(y); }
    return {x0: Math.min(...xs) - 40, x1: Math.max(...xs) + 40, y0: Math.min(...ys) - 40, y1: Math.max(...ys) + 40};
}

function mapProjection(canvas) {
    const b = bounds(), pad = 16;
    const scale = Math.min((canvas.width - 2 * pad) / (b.x1 - b.x0), (canvas.height - 2 * pad) / (b.y1 - b.y0));
    return {scale, X: x => pad + (x - b.x0) * scale, Y: y => canvas.height - pad - (y - b.y0) * scale};
}

function rayOccludedByBuildings(sx, sy, sz, tx, ty, tz, buildings) {
    const dx = tx - sx, dy = ty - sy, dz = tz - sz;
    for (const b of buildings) {
        let tmin = 0, tmax = 1;
        const slabs = [[sx, dx, b.x, b.x + b.w], [sy, dy, b.y, b.y + b.d], [sz, dz, 0, b.h]];
        let hit = true;
        for (const [origin, dir, low, high] of slabs) {
            if (Math.abs(dir) < 1e-8) {
                if (origin < low || origin > high) { hit = false; break; }
            } else {
                let t1 = (low - origin) / dir, t2 = (high - origin) / dir;
                if (t1 > t2) [t1, t2] = [t2, t1];
                tmin = Math.max(tmin, t1);
                tmax = Math.min(tmax, t2);
                if (tmin > tmax) { hit = false; break; }
            }
        }
        if (hit && tmin < 0.995 && tmax > 0.005) {
            return [sx + dx * tmin, sy + dy * tmin, sz + dz * tmin];
        }
    }
    return null;
}

function updateTwin3D(t) {
    const scene = record.inputs.scene;
    const primary = deployment($('primary').value);
    const bsrc = scene.building_source || 'synthetic';
    const isFlight = currentMode === 'flight' && flightTracks.length > 0;
    const activeFlight = isFlight ? (flightTracks[activeFlightIndex] || flightTracks[0]) : null;
    const activePoints = isFlight ? activeFlight.points : record.inputs.trajectory.points;
    const activeTimeline = isFlight ? activeFlight.samples : primary.timeline;
    const activeEllipsoids = isFlight
        ? activeFlight.samples.map(s => s.ellipsoid)
        : (spaceData?.ellipsoids || null);
    const clampedIdx = Math.max(0, Math.min(activePoints.length - 1, t));
    const [px, py, pz] = activePoints[clampedIdx];
    const sample = activeTimeline[clampedIdx] || {};

    const anchor = spaceData?.anchor || scene.geographic_anchor || {
        urban: {latitude: 39.0510, longitude: 115.9810, label: '雄安高密建筑片区'},
        corridor: {latitude: 39.0015, longitude: 115.9515, label: '雄安物流主干廊道'},
        electromagnetic: {latitude: 39.0485, longitude: 115.9865, label: '雄安复杂电磁区'},
    }[scene.id] || {latitude: 39.0510, longitude: 115.9810, label: '雄安新区'};

    if ($('twin3d-hud')) {
        const crlbStr = sample.crlb_m == null ? '单站/盲区不可解算' : `${sample.crlb_m.toFixed(1)} m`;
        $('twin3d-hud').innerHTML =
            `<div><b>${escapeHtml(MODE_LABELS[currentMode])}</b> · ${escapeHtml(anchor.label || scene.name)}（${anchor.latitude.toFixed(4)}°N, ${anchor.longitude.toFixed(4)}°E）</div>` +
            `<div>当前方案：<b>${escapeHtml(primary.label)}</b>（新增 ${primary.added.length} 站） · 时刻 <b>t=${clampedIdx} s</b> · 坐标 (${Math.round(px)}, ${Math.round(py)}, <b>${pz} m</b>)</div>` +
            `<div>探测概率 Pd：<b>${sample.pd == null ? '—' : sample.pd.toFixed(2)}</b> · 共视基站：<b>${sample.stations ?? 0} 座</b> · 三维定位界 CRLB：<b>${crlbStr}</b></div>`;
    }

    if (!engine3d) return;
    engine3d.update({
        sceneId: scene.id,
        buildingSource: bsrc,
        anchor,
        buildings: spaceData?.buildings || scene.buildings,
        footprints: spaceData?.footprints || [],
        zone: scene.zone,
        devices: primary.devices,
        addedIds: primary.added,
        points: activePoints,
        timeline: activeTimeline,
        ellipsoids: activeEllipsoids,
        segmentRange: isFlight ? null : segmentRange,
        timeIndex: clampedIdx,
        showImagery: !$('twin3d-imagery') || $('twin3d-imagery').checked,
        showLos: !$('twin3d-los') || $('twin3d-los').checked,
        showEllipsoid: !$('twin3d-ellipsoid') || $('twin3d-ellipsoid').checked,
        showFov: !$('twin3d-fov') || $('twin3d-fov').checked,
        showLabels: !$('twin3d-labels') || $('twin3d-labels').checked,
    });
}

function drawMap(t) {
    const canvas = $('map'), ctx = canvas.getContext('2d');
    const {scale, X, Y} = mapProjection(canvas);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const scene = record.inputs.scene;
    for (const bd of scene.buildings) {
        const shade = Math.min(1, bd.h / 110);
        ctx.fillStyle = `rgba(160,180,200,${0.25 + 0.5 * shade})`;
        ctx.fillRect(X(bd.x), Y(bd.y + bd.d), Math.max(2, bd.w * scale), Math.max(2, bd.d * scale));
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
    const mapIdx = Math.min(points.length - 1, t);
    for (let i = 1; i < points.length; i++) {
        const inRange = segmentRange && i > segmentRange.start && i <= segmentRange.end;
        ctx.strokeStyle = inRange ? STATE[primary.timeline[i].state][0] : '#607584';
        ctx.lineWidth = inRange ? 4 : 1.5;
        ctx.beginPath();
        ctx.moveTo(X(points[i - 1][0]), Y(points[i - 1][1]));
        ctx.lineTo(X(points[i][0]), Y(points[i][1]));
        ctx.stroke();
    }
    ctx.lineWidth = 1;
    const [px, py, pz] = points[mapIdx];
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(X(px), Y(py), 8, 0, 2 * Math.PI);
    ctx.fill();
    ctx.fillStyle = '#112e43';
    ctx.font = 'bold 12px system-ui';
    ctx.fillText('✈', X(px) - 6, Y(py) + 4);
    ctx.fillStyle = '#fff';
    ctx.font = '13px system-ui';
    ctx.fillText(`${primary.label} · t=${mapIdx} s · 高度 ${pz} m`, 12, 20);
}

function drawStrips(t) {
    const canvas = $('strips'), ctx = canvas.getContext('2d');
    const rows = record.result.deployments, n = record.inputs.trajectory.points.length;
    const idx = Math.min(n - 1, t);
    const left = 155, h = (canvas.height - 10) / rows.length, w = (canvas.width - left - 6) / n;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = '12px system-ui';
    rows.forEach((row, r) => {
        ctx.fillStyle = row.key === $('primary').value ? '#146d80' : '#526b7c';
        ctx.fillText(row.label.replace('反事实：', ''), 4, r * h + h / 2 + 8);
        row.timeline.forEach((s, i) => {
            ctx.fillStyle = STATE[s.state][0];
            ctx.fillRect(left + i * w, r * h + 5, Math.ceil(w), h - 6);
        });
    });
    if (segmentRange) {
        ctx.fillStyle = 'rgba(247,250,252,.72)';
        ctx.fillRect(left, 0, segmentRange.start * w, canvas.height);
        ctx.fillRect(left + (segmentRange.end + 1) * w, 0, (n - segmentRange.end - 1) * w, canvas.height);
    }
    ctx.strokeStyle = '#112e43';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(left + (idx + 0.5) * w, 0);
    ctx.lineTo(left + (idx + 0.5) * w, canvas.height);
    ctx.stroke();
    ctx.lineWidth = 1;
}

function drawErrors(t) {
    const canvas = $('errors'), ctx = canvas.getContext('2d');
    const primary = deployment($('primary').value), compare = deployment($('compare').value);
    const limit = record.inputs.environment.precision_m, top = limit * 3, n = primary.timeline.length;
    const idx = Math.min(n - 1, t);
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
    ctx.beginPath(); ctx.moveTo(X(idx), pad / 2); ctx.lineTo(X(idx), canvas.height - pad / 2); ctx.stroke();
}

function drawSample(t) {
    const idx = Math.min(record.inputs.trajectory.points.length - 1, t);
    $('sample').innerHTML = table(['方案', '状态', '探测概率', '覆盖站数', 'CRLB', '模拟误差中位数', '模拟可定位比例'],
        record.result.deployments.map(row => {
            const s = row.timeline[idx];
            return [escapeHtml(row.label.replace('反事实：', '')), `<span style="color:${STATE[s.state][0]}">●</span> ${escapeHtml(s.cause)}`,
                s.pd == null ? '—' : s.pd.toFixed(2), s.stations, meters(s.crlb_m), meters(s.simulated_median_error_m), pct(s.simulated_fix_fraction)];
        }));
}

function draw() {
    if (!record) return;
    const t = +$('time').value;
    const times = record.inputs.trajectory.times;
    $('time-label').textContent = `${currentMode === 'flight' ? t : (times[Math.min(times.length - 1, t)] ?? t)} s`;
    updateTwin3D(t);
    drawMap(t);
    drawStrips(t);
    drawErrors(t);
    drawSample(t);
}

function jumpToDifference() {
    const a = deployment($('primary').value).timeline, b = deployment($('compare').value).timeline;
    const i = a.findIndex((s, k) => k >= +$('time').min && k <= +$('time').max && s.state !== b[k].state);
    $('time').value = i < 0 ? $('time').min : i;
    draw();
}

function togglePlay() {
    if (!record) return;
    if (timer) { clearInterval(timer); timer = null; $('play').textContent = '播放'; return; }
    $('play').textContent = '暂停';
    timer = setInterval(() => {
        const next = +$('time').value < +$('time').max ? +$('time').value + 1 : +$('time').min;
        $('time').value = next;
        draw();
    }, 220);
}

function selectContinuousSegmentAt(index) {
    if (index < 0 || !record) return;
    const {timeline} = deployment($('primary').value);
    const target = timeline[index];
    const same = item => item && item.state === target.state && item.cause === target.cause;
    let start = index, end = index;
    while (same(timeline[start - 1])) start--;
    while (same(timeline[end + 1])) end++;
    selectSegment(start, end);
}

$('twin-modes')?.addEventListener?.('click', event => {
    const btn = event.target?.closest?.('[data-mode]');
    if (btn) setMode(btn.dataset.mode);
});
$('field-altitude')?.addEventListener?.('change', refreshSpaceLayer);
$('flight-track-select')?.addEventListener?.('change', () => {
    activeFlightIndex = Number($('flight-track-select').value) || 0;
    syncFlightTimeline();
    draw();
});
$('twin3d-reset')?.addEventListener?.('click', () => engine3d?.homeView());
$('twin3d-side')?.addEventListener?.('click', () => engine3d?.sideView());
$('twin3d-top')?.addEventListener?.('click', () => engine3d?.topView());
for (const id of ['twin3d-imagery', 'twin3d-los', 'twin3d-ellipsoid', 'twin3d-fov', 'twin3d-labels']) {
    $(id)?.addEventListener?.('change', draw);
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
    const times = record.inputs.trajectory.times;
    selectSegment(times.indexOf(segment.start), times.indexOf(segment.end));
    $('replay').scrollIntoView({behavior: 'smooth'});
});
$('map').addEventListener('click', event => {
    if (!record) return;
    const canvas = $('map');
    const rect = canvas.getBoundingClientRect();
    const {X, Y} = mapProjection(canvas);
    let index = -1, nearest = 18;
    record.inputs.trajectory.points.forEach(([x, y], i) => {
        const screenX = rect.left + X(x) * rect.width / canvas.width;
        const screenY = rect.top + Y(y) * rect.height / canvas.height;
        const distance = Math.hypot(screenX - event.clientX, screenY - event.clientY);
        if (distance < nearest) { nearest = distance; index = i; }
    });
    if (index >= 0) selectContinuousSegmentAt(index);
});
$('strips').addEventListener('click', event => {
    if (!record) return;
    const rect = event.target.getBoundingClientRect(), n = record.inputs.trajectory.points.length;
    const x = (event.clientX - rect.left) * event.target.width / rect.width;
    if (x < 155) return;
    const index = Math.floor((x - 155) / ((event.target.width - 161) / n));
    $('time').value = Math.max(+$('time').min, Math.min(+$('time').max, index));
    draw();
});
$('time').addEventListener('input', draw);
$('primary').addEventListener('change', () => { refreshSpaceLayer(); draw(); });
$('compare').addEventListener('change', draw);
$('segment-deployment').addEventListener('change', renderSegments);
$('play').addEventListener('click', togglePlay);
$('run').addEventListener('click', runExperiment);
$('recompute').addEventListener('click', recompute);
$('history').addEventListener('change', () => open($('history').value).catch(error => status(error.message, true)));

async function switchEnvironment() {
    const chosen = $('scene')?.value || 'urban';
    const bsrc = $('building-source')?.value || 'synthetic';
    try {
        const {experiments} = await api('/api/twin-experiments?scene=' + encodeURIComponent(chosen));
        const match = experiments.find(item => (item.building_source || 'synthetic') === bsrc);
        if (match) await open(match.id);
        else await runExperiment();
    } catch (error) {
        status('切换环境失败：' + error.message, true);
    }
}
$('scene')?.addEventListener?.('change', switchEnvironment);
$('building-source')?.addEventListener?.('change', switchEnvironment);

// ---------- 研究助手 ----------
const CHIPS = [
    '五个子项目之间是什么关系？哪一项的必要性在本实验中证据最强？',
    '规划时忽略遮挡、忽略干扰或仅按覆盖选址，放回参考环境后差多少？',
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
    return `<p><b>${title}</b></p>${report.numeric_source === 'archived_twin_segment' ? '<p class="muted small">定量结论来自本段归档采样，验证建议由助手生成。</p>' : ''}<p>${escapeHtml(report.conclusion)}</p>
        ${report.evidence?.length ? `<p class="small"><b>依据</b></p><ul class="small">${report.evidence.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : ''}
        ${report.limitations?.length ? `<p class="small"><b>局限</b></p><ul class="small">${report.limitations.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : ''}
        ${report.next_steps?.length ? `<div class="suggestions"><b>建议（未执行，需人工决定）</b><ul class="small">${report.next_steps.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul></div>` : ''}
        ${report.evidence_ids?.length ? `<p class="muted small">引用证据：${report.evidence_ids.map(escapeHtml).join('、')}</p>` : ''}`;
}

function runHtml(item) {
    const steps = item.steps || [];
    const evidence = (item.trace || []).filter(t => t.result && t.result.evidence_id).map(t => t.result);
    const sourceLimits = item.report?.source_limitations || [];
    const scope = item.request.twin_segment;
    return `<div class="msg-user">${escapeHtml(item.request.question)}</div><div class="msg-agent">
        <p class="muted small">${scope ? `档案 ${escapeHtml(scope.experiment_id.slice(0, 8))} · 采样点 ${scope.start}–${scope.end}` : '未绑定航段的历史提问'}</p>
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
        '<p class="muted small">选定航段后可就该段提问，回答将引用归档实验的只读证据。</p>';
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
    if (question.length < 2 || run || !record || !segmentRange || $('segment-result').hidden) return;
    busy(true);
    try {
        const item = await api('/api/research-agent/runs', {method: 'POST', body: JSON.stringify({
            question, workflow: 'platform', scene: record.inputs.scene.id || 'urban', skill: 'twin_experiment',
            twin_segment: {experiment_id: record.id, ...segmentRange}, mode: $('review').checked ? 'review' : 'single', session_id: session})});
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

async function restoreAssistant() {
    $('conversation').innerHTML = '<p class="muted small">选定航段后可就该段提问，回答将引用归档实验的只读证据。</p>';
    modelStatus();
    const runs = await loadRuns();
    for (const r of runs.slice(0, 3).reverse()) {
        try {
            const item = await api('/api/research-agent/runs/' + r.id);
            showRun(item);
            if (item.status === 'running') { run = item.id; busy(true); pollRun(item.id); }
        } catch (error) { /* skip unreadable record */ }
    }
}

async function start() {
    if (!workbench) restoreAssistant();
    const query = new URLSearchParams(location.search);
    const id = query.get('experiment');
    const requestedScene = query.get('scene');
    const requestedMode = query.get('mode');
    if (['urban', 'corridor', 'electromagnetic'].includes(requestedScene) && $('scene')) {
        $('scene').value = requestedScene;
    }
    if (MODE_LABELS[requestedMode]) {
        currentMode = requestedMode;
        if ($('twin-modes')) {
            for (const btn of $('twin-modes').querySelectorAll?.('[data-mode]') || []) {
                btn.classList?.toggle?.('active', btn.dataset.mode === requestedMode);
            }
        }
        if ($('mode-summary-pill')) $('mode-summary-pill').textContent = MODE_LABELS[requestedMode];
        if ($('mode-toolbar')) $('mode-toolbar').hidden = requestedMode === 'counterfactual';
        if ($('altitude-slice-wrap')) $('altitude-slice-wrap').hidden = !(requestedMode === 'coverage' || requestedMode === 'precision');
        if ($('flight-track-wrap')) $('flight-track-wrap').hidden = requestedMode !== 'flight';
    }
    try {
        const experiments = await loadHistory(id);
        if (id) {
            if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('实验编号无效');
            const range = query.has('start') || query.has('end')
                ? {start: query.get('start'), end: query.get('end')} : null;
            await open(id, range);
        } else if (requestedScene && ['urban', 'corridor', 'electromagnetic'].includes(requestedScene)) {
            const match = experiments.find(item => item.scene === requestedScene);
            if (match) await open(match.id);
            else await runExperiment();
        } else if (experiments.length) {
            await open(experiments[0].id);
        } else {
            await runExperiment();
        }
    } catch (error) {
        status('无法读取实验档案：' + error.message, true);
    }
}
start();
