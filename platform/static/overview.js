'use strict';
const root = location.pathname.startsWith('/network') ? '/network' : '';
const $ = id => document.getElementById(id);
if (typeof window !== 'undefined' && window.parent && window.parent !== window &&
    (window.frameElement?.dataset?.frame === 'planning' || new URLSearchParams(location.search || '').get('workbench') === '1')) {
    document.body?.classList?.add?.('workbench-embedded');
}
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
const pct2 = v => v == null ? '—' : (v * 100).toFixed(2) + '%';

const NAV_ITEMS = {
    overview: '研究总览',
    tasks: '规划任务',
    resources: '资源配置',
    diagnosis: '感知诊断',
    planning: '网络规划',
    plans: '方案管理',
    acceptance: '任务验证',
    twin: '研究必要性孪生',
    research: '研究问题',
    demonstration: '示范应用',
    agents: '研究智能体',
};
$('navigation').innerHTML = Object.entries(NAV_ITEMS)
    .map(([k, label]) => `<a href="${root}/${k}" class="${k === 'overview' ? 'active' : ''}">${esc(label)}</a>`)
    .join('');

function resolveHref(path) {
    if (!path) return root || '/';
    if (path === '/' || path.startsWith('/station') || path.startsWith('/service')) return path;
    if (path.startsWith('/network/')) return root ? path : path.slice('/network'.length);
    if (path.startsWith('/')) return root + path;
    return root + '/' + path;
}

let state = {data: null, selectedSubproject: 1};

function renderDag(data) {
    const svg = $('dag-svg');
    const positions = {
        1: {x: 40, y: 34, w: 185, h: 82},
        2: {x: 40, y: 154, w: 185, h: 82},
        3: {x: 305, y: 94, w: 195, h: 84},
        4: {x: 575, y: 94, w: 195, h: 84},
        5: {x: 835, y: 94, w: 175, h: 84},
    };
    const defs = `<defs>
        <marker id="arrow-fwd" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#5fd3e3"/>
        </marker>
        <marker id="arrow-fb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#f5be58"/>
        </marker>
    </defs>`;

    const edgePaths = [
        {d: 'M 132 116 L 132 152', stroke: '#5fd3e3', marker: 'arrow-fwd', tx: 140, ty: 138, text: '遮挡与可见性 Pd'},
        {d: 'M 225 72 C 265 72, 268 116, 303 122', stroke: '#5fd3e3', marker: 'arrow-fwd', tx: 232, ty: 60, text: '三维盲区分布'},
        {d: 'M 225 195 C 265 195, 268 156, 303 150', stroke: '#5fd3e3', marker: 'arrow-fwd', tx: 228, ty: 215, text: '干扰与 CRLB 精度界'},
        {d: 'M 500 136 L 573 136', stroke: '#5fd3e3', marker: 'arrow-fwd', tx: 506, ty: 124, text: '约束规划方案'},
        {d: 'M 770 136 L 833 136', stroke: '#5fd3e3', marker: 'arrow-fwd', tx: 774, ty: 124, text: '冻结孪生档案'},
        {d: 'M 922 178 C 922 246, 672 246, 672 180', stroke: '#f5be58', marker: 'arrow-fb', dash: '6 4', tx: 712, ty: 252, text: '运行残差反哺参数标定与滚动重规划（闭环反馈）'},
    ];

    const edgesMarkup = edgePaths.map(e => `
        <path d="${e.d}" fill="none" stroke="${e.stroke}" stroke-width="2" ${e.dash ? `stroke-dasharray="${e.dash}"` : ''} marker-end="url(#${e.marker})"/>
        <text x="${e.tx}" y="${e.ty}" fill="${e.dash ? '#ffd584' : '#b7d9ea'}" font-size="11.5">${esc(e.text)}</text>
    `).join('');

    const nodesMarkup = data.subprojects.map(sp => {
        const pos = positions[sp.id];
        const active = sp.id === state.selectedSubproject;
        const fill = active ? '#1b5f73' : '#163b50';
        const stroke = active ? '#7cf0df' : '#4a7c96';
        return `<g class="dag-node" data-subproject="${sp.id}" tabindex="0" role="button" aria-label="${esc(sp.code + ' ' + sp.short)}">
            <rect x="${pos.x}" y="${pos.y}" width="${pos.w}" height="${pos.h}" rx="10" fill="${fill}" stroke="${stroke}" stroke-width="${active ? 2.6 : 1.4}"/>
            <text x="${pos.x + 12}" y="${pos.y + 23}" fill="#8ee8db" font-size="11.5" font-weight="600">${esc(sp.code)} · PDF 页 ${esc(sp.pdf_page_label)}</text>
            <text x="${pos.x + 12}" y="${pos.y + 46}" fill="#ffffff" font-size="14" font-weight="700">${esc(sp.short)}</text>
            <text x="${pos.x + 12}" y="${pos.y + 66}" fill="#bed8e6" font-size="11.5">${esc(sp.role)}</text>
        </g>`;
    }).join('');

    svg.innerHTML = defs + edgesMarkup + nodesMarkup;
    svg.querySelectorAll('[data-subproject]').forEach(node => {
        const select = () => {
            state.selectedSubproject = Number(node.dataset.subproject);
            renderDag(data);
            renderInspector(data);
        };
        node.addEventListener('click', select);
        node.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); }
        });
    });
}

function renderInspector(data) {
    const sp = data.subprojects.find(item => item.id === state.selectedSubproject) || data.subprojects[0];
    const incoming = data.relations.filter(r => r.target === sp.id);
    const outgoing = data.relations.filter(r => r.source === sp.id);
    $('subproject-inspector').innerHTML = `
        <div class="inspector-head">
          <div>
            <span class="badge-pill">${esc(sp.code)} · ${esc(sp.role)}</span>
            <h3>${esc(sp.title)}</h3>
          </div>
          <div class="sub-links">
            <a href="${esc(resolveHref(sp.research_entry))}">研究难点与示意算例 →</a>
            <a href="${esc(resolveHref(sp.entry))}">对应平台功能模块 →</a>
            <a href="${esc(resolveHref('/twin'))}">数字孪生必要性对照 →</a>
          </div>
        </div>
        <dl class="inspector-cols">
          <div>
            <dt>拟解决的核心问题</dt>
            <dd>${esc(sp.question)}</dd>
            <dt>方法与内容主线</dt>
            <dd>${esc(sp.summary)}</dd>
            <dt>覆盖的细分难点（${sp.challenge_count} 项）</dt>
            <dd>${sp.challenges.map(esc).join('；')}</dd>
          </div>
          <div>
            <dt>上游依赖与输入</dt>
            <dd>${esc(sp.input)}${incoming.length ? '<br><span class="muted small">关联边：' + incoming.map(r => `子项目${'一二三四五'[r.source - 1]}→本项（${esc(r.label)}）`).join('；') + '</span>' : ''}</dd>
            <dt>下游支撑与交付产物</dt>
            <dd>${sp.deliverables.map(esc).join('；')}${outgoing.length ? '<br><span class="muted small">输出边：' + outgoing.map(r => `本项→子项目${'一二三四五'[r.target - 1]}（${esc(r.label)}）`).join('；') + '</span>' : ''}</dd>
            <dt>正式申报书页码锚点</dt>
            <dd>PDF 文件第 ${esc(sp.pdf_page_label)} 页${data.project.proposal_pdf_available ? ` · <a href="${root}/api/research-analysis/proposal#page=${sp.pdf_pages[0]}" target="_blank" rel="noopener">查看本地原文 PDF ↗</a>` : '（当前环境未挂载本地 PDF 原件，保留页码锚点）'}</dd>
          </div>
        </dl>
    `;
}

function renderSceneTable(scenes) {
    const head = ['典型场景', '现有配置 / 建筑', '50m 基线覆盖率', '细网格验算（10m / 2m）', '三维数字孪生反事实必要性摘要', '验证入口'];
    const rows = scenes.map(s => {
        const assess = s.latest_assessment;
        const assessCell = assess
            ? `广域 <b>${pct2(assess.coverage)}</b> · 重点区 <b>${pct2(assess.focus_coverage)}</b><br>` +
              `<span class="small muted">精度合格 ${pct2(assess.precision_pass_rate)} · 新增 ${assess.added_cost ?? '—'} 万 · ` +
              `<span class="${assess.simulation_kpi_pass ? 'pass' : 'warn'}">${assess.simulation_kpi_pass ? '满足申报书覆盖指标' : '待优化'}</span></span>`
            : `<span class="muted small">尚无已保存细网格验算 · <a href="${esc(resolveHref('/acceptance'))}">前往计算</a></span>`;
        const twin = s.latest_twin;
        let twinCell;
        if (twin?.available) {
            const parts = [
                `原部署 ${pct(twin.baseline_pass)} → 完整规划 <b>${pct(twin.method_pass)}</b>`,
                twin.gain_vs_ignore_occlusion_pp != null ? `较忽略遮挡 ${twin.gain_vs_ignore_occlusion_pp >= 0 ? '+' : ''}${twin.gain_vs_ignore_occlusion_pp.toFixed(1)} pp` : null,
                twin.gain_vs_ignore_interference_pp != null ? `较忽略干扰 ${twin.gain_vs_ignore_interference_pp >= 0 ? '+' : ''}${twin.gain_vs_ignore_interference_pp.toFixed(1)} pp` : null,
                twin.gain_vs_coverage_only_pp != null ? `较仅按覆盖 ${twin.gain_vs_coverage_only_pp >= 0 ? '+' : ''}${twin.gain_vs_coverage_only_pp.toFixed(1)} pp` : null,
            ].filter(Boolean);
            twinCell = parts.join('<br><span class="small muted">') + (parts.length > 1 ? '</span>' : '');
        } else {
            twinCell = `<span class="muted small">${esc(twin?.note || '尚无归档记录')}</span>`;
        }
        return `<tr>
            <td><b>${esc(s.name)}</b><br><span class="small muted">${esc(s.purpose)}（版本 ${s.revision}）</span></td>
            <td>设备 ${s.device_count} 台 / 候选 ${s.candidate_count} 处<br><span class="small muted">建筑体 ${s.building_count} 栋</span></td>
            <td><b>${pct(s.coarse_50m_coverage)}</b></td>
            <td>${assessCell}</td>
            <td>${twinCell}</td>
            <td><div class="sub-links"><a href="${esc(resolveHref('/twin?scene=' + s.scene))}">数字孪生</a><a href="${esc(resolveHref('/demonstration/' + s.scene))}">场景示范</a></div></td>
        </tr>`;
    }).join('');
    $('scene-evidence-table').innerHTML = `<table><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
}

function render(data) {
    state.data = data;
    const p = data.project;
    $('context-bar').innerHTML = [
        ['指标口径', p.kpi_version],
        ['物理内核', p.model_version],
        ['申报书版本', `${p.proposal_version}（${p.proposal_pdf_available ? `本机已挂载 ${p.proposal_pdf_pages} 页 PDF` : '仅保留页码锚点'}）`],
        ['总览摘要', `<span class="mono">${esc(data.overview_digest.slice(0, 16))}</span>`],
        ['证据等级', '平台档案 + 合成数字孪生（非外场验收）'],
    ].map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join('');

    $('project-title').textContent = p.title;
    $('project-thesis').textContent = p.thesis;
    $('landing-stages').innerHTML = p.landing_stages.map(st => `
        <div class="stage-card">
          <small>${esc(st.stage)} · ${esc(st.focus)}</small>
          <strong>${esc(st.name)}</strong>
          <p>${esc(st.detail)}</p>
        </div>
    `).join('');

    const c = data.record_counts;
    $('kpi-summary').innerHTML = `
        <div>
          <h3>正式申报书核心技术阈值与当前平台档案</h3>
          <p class="small-note">统一按 ${esc(p.kpi_version)} 口径核验；早期草稿阈值仅保留对照。</p>
        </div>
        <div class="kpi-stat-grid">
          <div class="kpi-stat"><span>多尺度网格分辨率</span><strong>广域 ≤10m / 重点 ≤2m</strong></div>
          <div class="kpi-stat"><span>典型规划仿真覆盖率</span><strong>全域 ≥90% / 重点 ≥95%</strong></div>
          <div class="kpi-stat"><span>异构设备与决策参数</span><strong>≥5 类模态 · ≥4 类参数</strong></div>
          <div class="kpi-stat"><span>当前库内可复算记录</span><strong>验算 ${c.succeeded_assessments} · 孪生 ${c.twin_experiments}</strong></div>
        </div>
        <p class="small-note">库内另含规划任务 ${c.tasks} 项、方案 ${c.plans} 份、三场景示范试验 ${c.trials} 项（运行记录 ${c.demo_runs} 条）。</p>
    `;

    $('platforms-grid').innerHTML = data.platforms.map(pl => `
        <article class="platform-card ${pl.id === 'planning' ? 'core' : ''}">
          <div class="platform-top">
            <h3>${esc(pl.name)}</h3>
            <span class="role-tag">${esc(pl.role)}</span>
          </div>
          <ul>${pl.capabilities.map(item => `<li>${esc(item)}</li>`).join('')}</ul>
          <p class="platform-boundary"><b>边界：</b>${esc(pl.boundary)}</p>
          <div class="sub-links">
            ${pl.sub_entries.map(link => `<a href="${esc(resolveHref(link.path))}">${esc(link.label)} →</a>`).join('')}
          </div>
        </article>
    `).join('');

    $('importance-grid').innerHTML = data.importance.map(item => `
        <article class="info-card">
          <span class="badge-pill">${esc(item.badge)}</span>
          <h3>${esc(item.title)}</h3>
          <p>${esc(item.summary)}</p>
          <div class="anchor-meta">对应申报书依据：${esc(item.evidence_anchor)}</div>
        </article>
    `).join('');

    renderDag(data);
    renderInspector(data);

    $('questions-grid').innerHTML = data.scientific_questions.map(q => `
        <article class="info-card">
          <span class="badge-pill">${esc(q.id)} · 支撑子项目 ${q.subprojects.join('、')}</span>
          <h3>${esc(q.title)}</h3>
          <p>${esc(q.problem)}</p>
          <p class="small muted"><b>关键难点：</b>${q.challenges.map(esc).join(' · ')}</p>
          <div class="sub-links">
            <span class="anchor-meta">PDF 页 ${esc(q.pdf_pages.join('、'))}</span>
            <a href="${esc(resolveHref(q.platform_entry))}">进入验证模块 →</a>
          </div>
        </article>
    `).join('');

    $('necessity-design').textContent = data.necessity.counterfactual_design;
    $('necessity-matrix').innerHTML = data.necessity.matrix.map(item => `
        <article class="info-card">
          <span class="badge-pill">${esc(item.id)} · ${esc(item.subproject)}</span>
          <h3>${esc(item.counterfactual)}</h3>
          <p>${esc(item.consequence)}</p>
          <div class="sub-links"><a href="${esc(resolveHref(item.verification_path))}">查看对照证据 →</a></div>
        </article>
    `).join('');
    renderSceneTable(data.necessity.scenes);

    $('feasibility-grid').innerHTML = data.feasibility.map(f => `
        <article class="feasibility-card">
          <div class="feasibility-head">
            <h3>${esc(f.id)} · ${esc(f.dimension)}</h3>
            <span class="status-chip">${esc(f.status_label)}</span>
          </div>
          <p class="small">${esc(f.summary)}</p>
          <ul>${f.metrics.map(m => `<li>${esc(m)}</li>`).join('')}</ul>
          <div class="sub-links"><a href="${esc(resolveHref(f.evidence_link))}">核查对应平台记录 →</a></div>
        </article>
    `).join('');

    $('boundaries-list').innerHTML = data.boundaries.map(b => `<li>${esc(b)}</li>`).join('');
}

(async function init() {
    try {
        const res = await fetch(root + '/api/research-overview');
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
        render(body);
    } catch (error) {
        $('context-bar').textContent = '研究体系总览加载失败：' + error.message;
    }
})();
