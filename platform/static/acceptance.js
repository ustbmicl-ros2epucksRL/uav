'use strict';
const root = location.pathname.startsWith('/network') ? '/network' : '';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/申请书|申报书/g, '任务').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
const percent = value => value == null ? '无有效样本' : (value * 100).toFixed(2) + '%';
const difference = (before, after) => before == null || after == null ? '无有效样本' : ((after-before)*100).toFixed(3);
const statuses = {queued:'排队中', running:'计算中', succeeded:'已完成', failed:'失败', cancelled:'已取消', interrupted:'已中断'};
const labels = {simulation_pass:'本次仿真通过', simulation_fail:'本次仿真未通过', pending_evidence:'待补验收证据'};
let tasks = [], task = null, current = null, viewer = null, flightViewer = null, timer = null, busy = false, sequence = 0;
let assessmentHistory = [];
const query = new URLSearchParams(location.search);

function localTime(value) {
    if (!value) return '时间待记录';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '时间待记录' : date.toLocaleString('zh-CN', {hour12:false});
}
function recordName(job) {
    return `${job.version ? '第' + job.version + '版 · ' : ''}${job.title || '规划计算'}`;
}
function recordOutcome(job) {
    if (job.status !== 'succeeded') return statuses[job.status] || job.status;
    if (job.low_altitude) return `低空 ${percent(job.low_altitude.rate)} · ${job.low_altitude.passed ? '达到目标' : '尚有缺口'}`;
    return job.feasible == null ? '计算完成' : job.feasible ? '任务约束通过' : '任务约束有缺口';
}

async function api(path, body) {
    const response = await fetch(root + '/api' + path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: {'Content-Type':'application/json'},
        body: body === undefined ? undefined : JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    return result;
}
function table(headings, rows) {
    return `<div class="table-wrap"><table><thead><tr>${headings.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
async function perform(action) {
    if (busy) return;
    busy = true;
    $('submit').disabled = true;
    try {
        await action();
        $('status').className = '';
        $('status').textContent = '任务输入与历史计算已加载。';
    } catch (error) {
        $('status').className = 'error';
        $('status').textContent = error.message;
    } finally {
        busy = false;
        $('submit').disabled = !task;
    }
}
function numericList(id) {
    const parts = $(id).value.split(',').map(value => value.trim());
    if (parts.some(value => value === '' || !Number.isFinite(Number(value)))) throw Error('请输入逗号分隔的数值');
    return parts.map(Number);
}
async function selectTask(id, jobId) {
    clearTimeout(timer);
    sequence += 1;
    task = tasks.find(item => item.id === id) || tasks[0] || null;
    current = null;
    viewer?.destroy();
    flightViewer?.destroy();
    flightViewer = null;
    viewer = null;
    $('result').hidden = true;
    $('cancel').hidden = true;
    $('retry').hidden = true;
    $('configuration').hidden = !task;
    $('empty').hidden = !!task;
    if (!task) return;
    $('task-select').value = task.id;
    localStorage.setItem('planning-task', task.id);
    $('task-context').textContent = `单场景新增建设预算 ${task.budget} 万元 · 覆盖目标 ${percent(task.coverage_target)} / ${percent(task.focus_target)} · 精度合格比例目标 ${percent(task.precision_rate_target)} · 任务版本 ${task.revision}`;
    const resource = await api('/workflow/resources/' + task.scene);
    $('models').innerHTML = resource.models.filter(model => model.type !== 'ADSB').map(model => `<label><input type="checkbox" name="model" value="${escapeHtml(model.id)}" checked>${escapeHtml(model.name)}</label>`).join('');
    await refreshHistory(jobId);
}
async function refreshHistory(preferred) {
    if (!task) return;
    const list = await api('/workflow/tasks/' + task.id + '/assessments');
    assessmentHistory = list;
    $('history').innerHTML = list.map(job => `<option value="${job.id}">${escapeHtml(recordName(job))} · ${escapeHtml(recordOutcome(job))}</option>`).join('') || '<option value="">尚无记录</option>';
    const referenceValue = $('reference').value;
    const completed = list.filter(job => job.status === 'succeeded');
    $('reference').innerHTML = '<option value="">仅与已有部署对比</option>' + completed.map(job => `<option value="${job.id}">${escapeHtml(recordName(job))} · ${escapeHtml(recordOutcome(job))}</option>`).join('');
    $('reference').value = completed.some(job => job.id === referenceValue) ? referenceValue : completed[0]?.id || '';
    const id = list.some(job => job.id === preferred) ? preferred : list[0]?.id;
    if (id) {
        $('history').value = id;
        await loadJob(id);
    } else {
        $('job-status').textContent = '提交后可在此查看排队、计算和验收结果。';
    }
}
async function loadJob(id) {
    clearTimeout(timer);
    const token = ++sequence;
    const job = await api('/assessments/' + id);
    if (token !== sequence || job.snapshot.task.id !== task?.id) return;
    current = job;
    const record = assessmentHistory.find(item => item.id === id) || {};
    const summary = {...record, ...job, low_altitude:job.result?.low_altitude, feasible:job.result?.feasible};
    $('job-status').textContent = `${recordName(record)} · ${recordOutcome(summary)}${job.error ? ' · ' + job.error : ''}`;
    const referenceId = job.snapshot.reference?.job_id;
    const reference = assessmentHistory.find(item => item.id === referenceId);
    $('job-context').textContent = `提交于 ${localTime(job.created_at || record.created_at)}（本地时间）。${referenceId ? '与' + (reference ? recordName(reference) : '历史方案') + '对比。' : '与已有基础部署对比。'}${job.status === 'succeeded' ? '计算已结束，是否达标请查看结果判定。' : ''}`;
    $('job-details').textContent = `记录编号 ${job.id}\n完成时间 ${localTime(job.finished_at)}${referenceId ? '\n对照记录编号 ' + referenceId : ''}`;
    const running = ['queued', 'running'].includes(job.status);
    $('cancel').hidden = !running;
    $('retry').hidden = running;
    $('result').hidden = job.status !== 'succeeded';
    if (job.status === 'succeeded') {
        renderResult(job);
        await refreshComparisons();
        if (token !== sequence) return;
    }
    if (running) timer = setTimeout(async () => {
        try {
            await loadJob(id);
            if (current?.id === id && !['queued', 'running'].includes(current.status)) await refreshHistory(id);
        } catch (error) {
        $('status').className = 'error';
        $('status').textContent = '状态读取失败，可点击刷新继续查看。' + error.message;
        }
    }, 2000);
    const url = new URL(location.href);
    url.searchParams.set('task', task.id);
    url.searchParams.set('assessment', id);
    history.replaceState(null, '', url);
}
function renderValidationProgress(job) {
    if (job.task_validation?.length) {
        $('validation-progress').innerHTML = job.task_validation.map(report => {
            const counts = report.counts;
            const items = values => values.map(value => `<li>${escapeHtml(value)}</li>`).join('');
            return `<article class="research-item"><h3>${escapeHtml(report.name)}</h3>
                <p>${report.stale ? '历史版本，需重新验证' : '当前模型计算'} · 仿真通过 ${counts.simulation_pass} 项 · 未通过 ${counts.simulation_fail} 项 · 待补证据 ${counts.pending_evidence} 项</p>
                <h4>测试方法</h4><p>${escapeHtml(report.method)}</p>
                <h4>本次结果</h4><ul>${items(report.results)}</ul>
                ${report.failed_requirements.length ? `<h4>指标缺口</h4><ul>${items(report.failed_requirements)}</ul>` : ''}
                <h4>下一步测试</h4><ul>${items(report.next_tests)}</ul>
                <details><summary>待完成证据 ${report.pending_requirements.length} 项</summary><ul>${items(report.pending_requirements)}</ul></details>
                <a href="${root}/research/${report.id}?task=${encodeURIComponent(task.id)}&assessment=${encodeURIComponent(job.id)}">查看测试设计与计算证据 →</a></article>`;
        }).join('');
        return;
    }
    const groups = [
        ['三维覆盖', '核验网格分辨率、有效空域和异构设备覆盖。', '补充设备标定与空间抽样对照。'],
        ['干扰与协同精度', '检验干扰、相关误差与交叉观测对定位的影响。', '开展随机漏检、共同偏差与实际估计误差对照。'],
        ['约束规划', '检查覆盖、精度、费用与挂载约束。', '对缺口开展同预算设备组合和站址对照。'],
        ['平台运行', '验证设备接入、时空配准与执行回执。', '补充真实协议联调及持续运行记录。'],
        ['场景示范', '核验部署、试验数据和独立参考航迹。', '按场景和任务类型补齐现场测试及成果证据。']
    ];
    $('validation-progress').innerHTML = groups.map(([name, purpose, next], index) => {
        const rows = job.indicators.filter(row => row.research === index + 1);
        const passed = rows.filter(row => row.status === 'simulation_pass').length;
        const failed = rows.filter(row => row.status === 'simulation_fail').length;
        const pending = rows.filter(row => row.status === 'pending_evidence').length;
        const status = rows.length ? `仿真通过 ${passed} 项 · 未通过 ${failed} 项 · 待补证据 ${pending} 项` : '本记录尚无对应指标';
        return `<article class="research-item"><h3>${name}</h3><p>${purpose}</p><p>${status}${job.stale ? ' · 历史输入，需重新验证' : ''}</p><p>${next}</p><a href="${root}/research/${index + 1}?task=${encodeURIComponent(task.id)}&assessment=${encodeURIComponent(job.id)}">查看测试方法与计算证据 →</a></article>`;
    }).join('');
}

function renderResult(job) {
    const result = job.result;
    $('conclusion').textContent = result.simulation_kpi_pass ? (result.feasible ? '本次仿真覆盖与任务精度约束通过' : '任务仿真覆盖通过，任务约束仍有缺口') : '本次典型规划仿真指标存在缺口';
    $('scope').textContent = `${result.scope.replace(/申请书|申报书/g, '任务')}。本次场景新增建设预算 ${job.snapshot.task.budget} 万元，${result.precision_threshold_m} m 定位理论界合格比例目标为 ${percent(job.snapshot.task.precision_rate_target)}。任务附加约束${result.feasible ? '全部满足' : '存在缺口'}。费用与模型参数采用示例配置。`;
    $('stale').hidden = !job.stale;
    $('metrics').innerHTML = [['全域覆盖', percent(result.stats.coverage)], ['重点区覆盖', percent(result.stats.focus_coverage)], ['精度合格比例', percent(result.stats.precision_pass_rate)], ['新增费用', result.added_cost.toFixed(2) + ' 万元']].map(([title, value]) => `<div class="metric">${title}<strong>${value}</strong></div>`).join('');
    if (result.low_altitude?.target != null) $('metrics').innerHTML += `<div class="metric">30–100 m 精度合格<strong>${percent(result.low_altitude.rate)}</strong><span>目标 ${percent(result.low_altitude.target)} · ${result.low_altitude.passed ? '通过' : '存在缺口'}</span></div>`;
    $('comparison').innerHTML = ['coverage', 'focus_coverage', 'precision_pass_rate'].map((key, index) => [result.reference && !result.settings.verify_reference ? 'reference' : 'before','after'].map(stage => `<div class="bar-pair"><span>${['全域覆盖','重点区覆盖','精度合格'][index]} · ${stage === 'after' ? '本次方案' : stage === 'reference' ? '对照方案' : '已有部署'}</span><progress class="${stage === 'after' ? '' : 'baseline'}" max="1" value="${result[stage].stats[key] ?? 0}"></progress><span>${percent(result[stage].stats[key])}</span></div>`).join('')).join('');
    renderPrecision(result);
    $('aggregation').textContent = `${result.aggregation}。遍历 ${result.evaluated_cells.toLocaleString()} 格，有效空域 ${result.stats.valid_volume_m3.toLocaleString()} m³。`;
    const topics = [
        ['遮挡与盲区在哪里', `分区精细网格与射线遮挡，当前全域覆盖 ${percent(result.stats.coverage)}`],
        ['协同后能否定位', `Pd、SINR 与信息矩阵计算，${result.precision_threshold_m} m 理论界合格比例 ${percent(result.stats.precision_pass_rate)}`],
        ['有限预算如何部署', result.settings.verify_reference ? `固定方案完整复核，新增设备 ${result.selected.length} 台` : `比较 ${result.variant_count} 个型号与姿态选项，选中 ${result.selected.length} 台设备`],
        ['规划如何进入运行', `冻结任务版本 ${job.snapshot.task.revision}、场景版本 ${job.snapshot.scene.revision}，关联部署与试验记录`],
        ['结果如何验证', `本方案关联 ${job.evidence.trials.length} 项示范试验、${job.evidence.runs.length} 份示范记录、${(job.evidence.flight_records || []).length} 份地面站参考航迹`]
    ];
    $('research-links').innerHTML = topics.map(([question, value], index) => `<article class="research-item"><a href="${root}/research/${index + 1}?task=${task.id}&assessment=${job.id}">${question}</a><p>${escapeHtml(value)}</p></article>`).join('');
    renderValidationProgress(job);
    $('indicators').innerHTML = table(['指标','任务要求','本次结果或证据','判定','依据'], job.indicators.map(row => [escapeHtml(row.id), escapeHtml(row.requirement), typeof row.value === 'number' ? percent(row.value) : escapeHtml(row.value), `<span class="${row.status === 'simulation_pass' ? 'pass' : row.status === 'simulation_fail' ? 'fail' : 'pending'}">${labels[row.status]}</span>`, row.id.startsWith('task-') ? `<a href="${root}/tasks?task=${encodeURIComponent(task.id)}">本次任务配置</a>` : `<a href="${root}/api/research-analysis/proposal#page=${row.pdf_page}" target="_blank" rel="noopener">指标依据第 ${row.pdf_page} 页</a>`]));
    $('deployment').innerHTML = table(['新增设备','型号','站址','挂高 / m','朝向 / °','俯仰 / °','费用 / 万元'], result.selected.map(device => [escapeHtml(device.id), escapeHtml(device.model_id || device.type), escapeHtml(device.site_id), device.z, device.azimuth, device.pitch, device.cost]));
    $('publish').disabled = job.stale || !!job.evidence.plan_id;
    $('plan-link').hidden = !job.evidence.plan_id;
    $('demo-link').hidden = !job.evidence.plan_id;
    $('plan-link').href = root + '/plans?plan=' + job.id;
    $('demo-link').href = `${root}/demonstration/${task.scene}?task=${task.id}&plan=${job.id}`;
    $('export').href = root + '/api/assessments/' + job.id + '/export';
    $('handoff').href = root + '/api/assessments/' + job.id + '/handoff';
    $('ground-station').hidden = root !== '/network';
    $('ground-station').href = '/?view=legacy&assessment=' + job.id;
    $('evidence').innerHTML = `<p>${escapeHtml(job.evidence.field_status)}</p>` + table(['试验','任务类别','运行记录'], job.evidence.trials.map(trial => [escapeHtml(trial.name), escapeHtml(trial.kinds.join('、')), job.evidence.runs.filter(run => run.trial_id === trial.id).map(run => escapeHtml(run.name) + ' / ' + (run.provenance === 'field' ? '现场来源待核验' : '合成验证')).join('<br>') || '待导入']));
    flightViewer?.destroy();
    flightViewer = null;
    const flights = job.evidence.flight_records || [];
    $('evidence').innerHTML += '<h3>关联地面站运行记录</h3>' + table(['飞行器','记录时间','数据来源','操作'], flights.map(record => [escapeHtml(record.vehicle_id), escapeHtml(record.created_at), '本地仿真，坐标对齐待核验', `<button class="secondary" data-flight="${record.id}">回放参考航迹</button> <a href="${root}/api/assessments/${job.id}/flight-evidence/${record.id}">导出记录</a>`])) + '<div id="flight-replay"></div>';
    document.querySelectorAll('[data-flight]').forEach(button => button.onclick = () => perform(() => showFlight(button.dataset.flight)));
    $('reproducibility').innerHTML = `<p>${escapeHtml(result.method)}</p><p>${escapeHtml(result.optimality)}</p><p>耗时 ${result.seconds} s · 搜索 ${result.search_samples ? result.search_samples + " 个空间样本" : result.search_cell + " m 网格"} · 精细复核 ${result.settings.wide_cell} / ${result.settings.focus_cell} m</p><p>本次新增型号候选 ${(result.settings.model_ids || []).join(" / ") || "全部非合作感知型号"} · 候选挂高 ${(result.settings.heights || []).join(" / ")} m · 初始朝向 ${(result.settings.azimuths || []).join(" / ")}°</p><p class="mono">输入摘要 ${result.input_digest}<br>算法摘要 ${job.snapshot.provenance.source_sha256}</p>` + table(['分区','网格 / m','有效格数','覆盖格数'], result.regions.map(region => [region.region === 'wide' ? '重点区外广域' : '重点区', region.cell, region.after.valid, region.after.covered])) + table(['搜索动作','设备','型号','挂高 / m','朝向 / °','搜索样本覆盖','搜索样本精度合格','搜索样本低空合格'], result.search_history.map(step => [escapeHtml(({add:'增补',replace:'替换',relocate:'跨站替换',mount:'挂载细化',joint_exchange:'多设备协同替换',retune_existing:'既有设备转向',feasible_best:'可行方案择优'})[step.action] || '增补'), step.action === 'joint_exchange' ? '联合设备组' : escapeHtml(step.device_id), step.action === 'joint_exchange' ? '—' : escapeHtml(step.model_id), step.action === 'joint_exchange' ? '—' : step.height, step.action === 'joint_exchange' ? '—' : step.azimuth, percent(step.coverage), percent(step.precision_pass_rate), percent(step.low_altitude_rate)])) + `<ul>${result.limitations.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
    viewer?.destroy();
    viewer = new PlanningMap($('coverage-map'), {onInspect: hit => {
        $('point-detail').textContent = hit.d ? `${hit.d.id} · ${hit.d.type} · 挂高 ${hit.d.z} m · 朝向 ${hit.d.azimuth}° · 俯仰 ${hit.d.pitch}°` : `XYZ (${hit.p.slice(0,3).join(', ')}) m · Pd ${hit.p[3].toFixed(4)} · SINR ${hit.p[4].toFixed(2)} dB · CRLB ${hit.p[5] == null ? '不可观测' : hit.p[5].toFixed(2) + ' m'} · 网格 ${hit.p[6]} m`;
    }});
    updateMap();
}
function renderPrecision(result) {
    const reference = result.settings.verify_reference ? null : result.reference;
    if (result.settings.strategy === 'precision') $('field').value = '5';
    $('precision-analysis').hidden = !result.after.bottleneck;
    $('variant').innerHTML = `<option value="after">${result.settings.verify_reference ? '本次复核方案' : '本次优化方案'}</option>` + (reference ? '<option value="reference">对照方案 · 重新验算</option>' : '') + '<option value="before">已有基础部署</option>';
    $('variant').value = 'after';
    if (!result.after.bottleneck) return;
    const before = reference || result.before;
    $('altitude-layer').value = result.low_altitude?.target != null ? 'low' : 'all';
    const beforeLabel = result.settings.verify_reference ? '已有基础部署' : '优化前';
    const afterLabel = result.settings.verify_reference ? '所选方案复核' : '优化后';
    const rows = [['全域覆盖','coverage'], ['重点区覆盖','focus_coverage'], ['精度合格比例','precision_pass_rate']];
    $('precision-comparison').innerHTML = `<p>${reference ? '对照方案为' + escapeHtml(recordName(assessmentHistory.find(item => item.id === reference.job_id) || {})) + '，已按本次输入、模型和全部网格重新计算。' : result.settings.verify_reference ? '本次固定所选方案设备与挂载，按当前模型复核。下表与已有基础部署比较。' : '对照对象为已有基础部署。'}精度合格要求同时满足探测阈值、可观测性和 ${result.precision_threshold_m} m 理论界。费用沿用资源库中的设备报价及站址建设费。</p>` + table(['指标',beforeLabel,afterLabel,'变化 / 百分点'], rows.map(([name,key]) => [name, percent(before.stats[key]), percent(result.stats[key]), difference(before.stats[key], result.stats[key])])) + `<p>新增费用 ${reference ? reference.added_cost : 0} → ${result.added_cost} 万元。全域精度合格比例目标 ${percent(current.snapshot.task.precision_rate_target)}，本次${result.stats.precision_pass_rate >= current.snapshot.task.precision_rate_target ? '达到' : '尚未达到'}。</p>`;
    const oldLow = before.bottleneck.altitude[0].precise;
    const newLow = result.after.bottleneck.altitude[0].precise;
    $('precision-comparison').innerHTML += table(['30–100 m 低空层',beforeLabel,afterLabel,'变化 / 百分点','目标判定'], [['精度合格比例',percent(oldLow),percent(newLow),difference(oldLow,newLow),result.low_altitude?.target != null ? `${percent(result.low_altitude.target)} · ${result.low_altitude.passed ? '通过' : '存在缺口'}` : '本次未设独立目标']]);
    if (reference) $('precision-comparison').innerHTML += '<details><summary>查看对照方案的设备与挂载参数</summary>' + table(['型号','站址','挂高 / m','朝向 / °','俯仰 / °','费用 / 万元'], reference.selected.map(device => [escapeHtml(device.model_id || device.type), escapeHtml(device.site_id), device.z, device.azimuth, device.pitch, device.cost])) + '</details>';
    const conditions = current.snapshot.task.volume;
    if (conditions) $('precision-comparison').innerHTML += `<p class="muted">当前场景含 ${result.scene.buildings.length} 个建筑体。同频 / 通信 / 天气损耗为 ${conditions.cochannel_db} / ${conditions.communication_db} / ${conditions.weather_db} dB。场景比较以这些实际输入为准。</p>`;
    if (result.cost_breakdown) $('precision-comparison').innerHTML += `<p>设备与站址费用 ${result.cost_breakdown.procurement_and_sites} 万元，转向调试预留 ${result.cost_breakdown.retuning_reserve} 万元，合计 ${result.added_cost} 万元。${escapeHtml(result.cost_breakdown.basis)}。</p>`;
    const lowSites = (current.snapshot.resources?.sites || []).filter(site => site.max_height <= 100);
    if (lowSites.length) {
        const used = new Set(result.selected.map(device => device.site_id));
        const chosen = lowSites.filter(site => used.has(site.id));
        $('precision-comparison').innerHTML += `<h3>低位候选站址</h3><p>本次资源库含 ${lowSites.length} 处低位候选，方案采用 ${chosen.length} 处。${escapeHtml(current.snapshot.resources.source)}</p>`;
        if (chosen.length) $('precision-comparison').innerHTML += table(['站址','平面坐标 / m','挂高范围 / m','建设预留 / 万元'], chosen.map(site => [escapeHtml(site.name),`${site.x}, ${site.y}`,`${site.min_height}–${site.max_height}`,site.setup_cost]));
    }
    if (result.search_alternatives?.length) {
        const alternatives = result.search_alternatives.map(alternative => {
            const counts = {};
            for (const device of alternative.selected) counts[device.type] = (counts[device.type] || 0) + 1;
            return [escapeHtml(Object.entries(counts).map(([type,count]) => `${type} × ${count}`).join(' + ')), alternative.low_site_ids.length,
                alternative.added_cost, percent(alternative.rates.coverage), percent(alternative.rates.focus_coverage),
                percent(alternative.rates.precision_pass_rate), percent(alternative.rates.low_altitude_rate), alternative.existing_constraints_pass ? '满足' : '存在缺口'];
        });
        $('precision-comparison').innerHTML += '<h3>低位站址与设备组合试算</h3><p>各型号先配置一处低位站址，再补齐组合，并参与后续联合搜索。下表采用全域及低空分层搜索样本，展示预算与精度的取舍。上方最终方案采用完整网格复核。</p>' + table(['新增设备组合','低位站址数','费用 / 万元','全域覆盖','重点区覆盖','全域精度','低空精度','现有约束'],alternatives);
    }
    if (result.updated_devices?.length) {
        const original = new Map([...result.scene.devices,...(reference?.updated_devices || [])].map(device => [device.id,device]));
        $('precision-comparison').innerHTML += '<h3>既有设备挂载对比</h3>' + table(['设备','朝向前 → 后 / °','俯仰前 → 后 / °','挂高 / m'], result.updated_devices.map(device => [escapeHtml(device.id),`${original.get(device.id).azimuth} → ${device.azimuth}`,`${original.get(device.id).pitch} → ${device.pitch}`,device.z]));
    }
    const causes = [['探测不足','blind'], ['已探测但不可观测','unobservable'], ['可观测但理论误差超限','geometry']];
    $('precision-bottleneck').innerHTML = '<h3>不合格空域的原因</h3><p>各项占全部有效空域体积，三个原因互斥。分层结果来自完整体素验算。</p>' + table(['原因',beforeLabel,afterLabel], causes.map(([name,key]) => [name, percent(before.bottleneck.fractions[key]), percent(result.after.bottleneck.fractions[key])])) + table(['高度层','优化前合格','优化后合格','优化后探测不足','优化后不可观测','优化后理论误差超限'], result.after.bottleneck.altitude.map((band,index) => [band.band, percent(before.bottleneck.altitude[index].precise), percent(band.precise), percent(band.blind), percent(band.unobservable), percent(band.geometry)]));
}
async function showFlight(identifier) {
    const record = await api('/assessments/' + current.id + '/flight-evidence/' + identifier);
    flightViewer?.destroy();
    const points = record.track.points;
    $('flight-replay').innerHTML = `<h3>地面站参考航迹 · ${escapeHtml(record.vehicle_id)}</h3><p>${escapeHtml(record.scope)}。地面站原点 ${record.geographic_origin.latitude}°, ${record.geographic_origin.longitude}°。</p><canvas id="flight-map" style="width:100%;height:420px" aria-label="关联地面站自身坐标系中的参考航迹"></canvas><label>回放位置<input id="flight-position" type="range" min="0" max="${Math.max(0,points.length-1)}" value="0"></label><p id="flight-point"></p>`;
    const bounds = ['x','y','z'].flatMap(axis => {
        const values = points.map(point => point[axis]);
        return values.length ? [Math.min(...values)-50, Math.max(...values)+50] : [0,2000];
    });
    flightViewer = new PlanningMap($('flight-map'), {scene:{devices:[],buildings:[]}, bounds, trackGap:2});
    flightViewer.mode = '3d';
    const draw = () => {
        const index = Number($('flight-position').value);
        const point = points[index];
        flightViewer.update({tracks:[{color:'#63dbdb',points:points.slice(0,index+1).map(p => ({...p,track_id:record.vehicle_id}))}]});
        $('flight-point').textContent = point ? `仿真时刻 ${point.t} s · XYZ (${point.x}, ${point.y}, ${point.z}) m · ${index+1}/${points.length}` : '暂无航迹点';
    };
    $('flight-position').oninput = draw;
    draw();
}
function updateMap() {
    if (!viewer || !current?.result) return;
    const result = current.result;
    const stage = $('variant').value;
    const scene = structuredClone(result.scene);
    const selected = stage === 'after' ? result.selected : stage === 'reference' ? result.reference.selected : [];
    const updates = stage === 'after' ? result.updated_devices || [] : stage === 'reference' ? result.reference.updated_devices || [] : [];
    const byId = new Map(updates.map(device => [device.id,device]));
    scene.devices = scene.devices.map(device => byId.get(device.id) || device);
    scene.devices.push(...selected);
    viewer.mode = $('view').value;
    viewer.field = Number($('field').value);
    const cloud = $('altitude-layer').value === 'low' ? result[stage].cloud.filter(point => point[2] >= 30 && point[2] < 100) : result[stage].cloud;
    viewer.update({scene, cloud, bounds: result.settings.bounds, cell: result.settings.wide_cell, focus: current.snapshot.task.focus, precision: result.precision_threshold_m, selected:[...selected,...updates]});
    $('point-detail').textContent = '点击当前方案的采样点或设备查看数值与参数。';
    $('legend').textContent = {3:'Pd 由红到绿表示 0–1，覆盖判据采用任务中保存的探测概率阈值。', 4:'SINR 由红到绿表示信号质量升高，单位为 dB。', 5:`CRLB 绿色表示理论界 ≤${result.precision_threshold_m} m，红色表示超阈值，灰色表示不可观测。`}[viewer.field];
}
$('assessment-form').onsubmit = event => {
    event.preventDefault();
    perform(async () => {
        const models = [...document.querySelectorAll('input[name=model]:checked')].map(input => input.value);
        if (!models.length) throw Error('请至少选择一个设备型号');
        const precision = $('strategy').value === 'precision' ? {strategy:'precision', reference_job_id:$('reference').value || null} : {};
        if ($('objective').value === 'low') {
            if ($('strategy').value !== 'precision') throw Error('低空层优化请选择精度优化方法');
            precision.low_altitude_target = Number($('low-target').value) / 100;
            precision.retune_existing = !!$('retune-existing').checked;
            precision.retuning_reserve = Number($('retuning-reserve').value);
            if (!Number.isFinite(precision.retuning_reserve) || precision.retuning_reserve < 0) throw Error('请输入有效的转向调试预留金额');
        }
        if ($('optimize').value === 'reference') {
            if (!precision.reference_job_id) throw Error('请选择待复核的对照方案，并使用精度验证方法');
            precision.verify_reference = true;
        }
        const job = await api('/workflow/tasks/' + task.id + '/assessments', {...precision, wide_cell:Number($('wide-cell').value), focus_cell:Number($('focus-cell').value), bounds:numericList('bounds'), heights:numericList('heights'), azimuths:numericList('azimuths'), model_ids:models, optimize:$('optimize').value === 'true'});
        await refreshHistory(job.id);
    });
};
$('task-select').onchange = () => perform(() => selectTask($('task-select').value));
$('history').onchange = () => perform(() => loadJob($('history').value));
$('refresh').onclick = () => perform(async () => {await refreshHistory(current?.id); await refreshComparisons();});
$('cancel').onclick = () => perform(async () => {await api('/jobs/' + current.id + '/cancel', {}); await loadJob(current.id);});
$('retry').onclick = () => perform(async () => {const job = await api('/jobs/' + current.id + '/retry', {}); await refreshHistory(job.id);});
$('publish').onclick = () => perform(async () => {await api('/assessments/' + current.id + '/plan', {}); await loadJob(current.id);});
for (const id of ['variant','field','view','altitude-layer']) $(id).onchange = updateMap;
$('create-baselines').onclick = () => perform(async () => {
    for (const [scene, name] of [['urban','建筑区'],['corridor','物流廊道'],['electromagnetic','复杂电磁区']]) {
        await api('/workflow/tasks', {name:'任务验证基准 · ' + name, scene});
    }
    await initialize();
});
async function refreshComparisons() {
    const comparisons = await api('/workflow/precision-comparisons');
    $('scene-comparisons').hidden = !Array.isArray(comparisons) || !comparisons.length;
    if (Array.isArray(comparisons)) $('scene-comparison-table').innerHTML = table(['任务 / 场景','全域 / 重点区覆盖','全域精度对照 → 当前','低空精度对照 → 当前','低空目标 / 缺口','费用 / 万元','任务约束','有效性'], comparisons.map(row => [`<a href="${root}/acceptance?task=${row.task_id}&assessment=${row.job_id}">${escapeHtml(row.task_name)}</a>`, `${percent(row.coverage)} / ${percent(row.focus_coverage)}`, `${percent(row.before)} → ${percent(row.after)}`, `${percent(row.low_before)} → ${percent(row.low_after)}`, row.low_target == null ? '未设独立目标' : `${percent(row.low_target)} / ${row.low_after == null ? '无有效样本' : (Math.max(0, row.low_target-row.low_after)*100).toFixed(2)+' 个百分点'}`, `${row.cost} / ${row.budget}`, row.feasible ? '全部通过' : '存在缺口', row.stale ? '历史版本，待复核' : '当前模型已复核']));
}
async function initialize() {
    tasks = await api('/workflow/tasks');
    await refreshComparisons();
    $('task-select').innerHTML = tasks.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('') || '<option>尚无任务</option>';
    await selectTask(query.get('task') || localStorage.getItem('planning-task'), query.get('assessment'));
}
$('navigation').innerHTML = Object.entries({tasks:'规划任务', resources:'资源配置', diagnosis:'感知诊断', planning:'网络规划', plans:'方案管理', acceptance:'任务验证', research:'研究问题', demonstration:'示范应用'}).map(([path,label]) => `<a href="${root}/${path}" class="${path === 'acceptance' ? 'active' : ''}">${label}</a>`).join('');
window.addEventListener('beforeunload', () => {clearTimeout(timer); viewer?.destroy(); flightViewer?.destroy();});
perform(initialize);
