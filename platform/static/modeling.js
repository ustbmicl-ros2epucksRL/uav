'use strict';
(() => {
  const root = location.pathname.startsWith('/network') ? '/network' : '';
  if (typeof window !== 'undefined' && window.parent && window.parent !== window &&
      (window.frameElement?.dataset?.frame === 'planning' || new URLSearchParams(location.search || '').get('workbench') === '1')) {
    document.body?.classList?.add?.('workbench-embedded');
  }
  const $ = id => document.getElementById(id);
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const pct = x => x == null ? '—' : (x * 100).toFixed(1) + '%';

  let currentScene = new URLSearchParams(location.search || '').get('scene') || 'urban';
  if (!['urban', 'corridor', 'electromagnetic'].includes(currentScene)) currentScene = 'urban';
  let state = {spec: null, audit: null, result: null};
  let activeTab = 'siting';
  let busy = false;

  async function api(path, body, method) {
    const res = await fetch(root + '/api' + path, {
      method: method || (body === undefined ? 'GET' : 'POST'),
      headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = typeof data.detail === 'string'
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map(e => (e.msg || '').replace('Value error, ', '')).join('；')
          : JSON.stringify(data.detail);
      throw new Error(msg || ('HTTP ' + res.status));
    }
    return data;
  }

  function setStatus(text, isError = false) {
    const bar = $('status-bar');
    if (!bar) return;
    bar.textContent = text;
    bar.className = isError ? 'status-note error' : 'status-note';
  }

  async function runGuarded(label, fn) {
    if (busy) return;
    busy = true;
    setStatus(label + '…');
    try {
      await fn();
    } catch (err) {
      setStatus(err.message || String(err), true);
    } finally {
      busy = false;
    }
  }

  function numField(id, label, value, min, max, step = 'any') {
    return `<label>${esc(label)}<input id="${id}" type="number" value="${esc(value)}" min="${min}" max="${max}" step="${step}"></label>`;
  }

  function readSpecFromDom() {
    if (!state.spec) return null;
    const spec = JSON.parse(JSON.stringify(state.spec));
    spec.siting.building_source = $('building-source').value;
    const sitingKeys = [
      'terrain_elevation_m', 'noise_floor_dbm', 'rain_fog_attenuation_db_km', 'cochannel_db',
      'communication_db', 'pd_threshold', 'precision_m', 'budget', 'max_new_sites',
      'coverage_target', 'focus_target', 'precision_rate_target',
      'min_radar_separation_m', 'min_radar_rf_isolation_m', 'min_band_overlap_separation_m',
      'min_jammer_rf_isolation_m', 'jammer_suppression_db',
    ];
    for (const k of sitingKeys) {
      const el = $('sit-' + k);
      if (el) spec.siting[k] = k === 'max_new_sites' ? Math.round(Number(el.value)) : Number(el.value);
    }
    spec.siting.sites.forEach((site, idx) => {
      const ld = $(`site-load-${idx}`);
      const pw = $(`site-power-${idx}`);
      const bw = $(`site-bw-${idx}`);
      const sc = $(`site-cost-${idx}`);
      const mn = $(`site-mains-${idx}`);
      const fb = $(`site-fiber-${idx}`);
      const ls = $(`site-lease-${idx}`);
      if (ld) site.max_load_kg = Number(ld.value);
      if (pw) site.power_w = Number(pw.value);
      if (bw) site.bandwidth_mbps = Number(bw.value);
      if (sc) site.setup_cost = Number(sc.value);
      if (mn) site.mains_power_ready = Boolean(mn.checked);
      if (fb) site.fiber_ready = Boolean(fb.checked);
      if (ls) site.lease_approved = Boolean(ls.checked);
    });

    const r = spec.routing;
    const routeKeys = [
      'cruise_speed_mps', 'max_climb_rate_mps', 'max_descent_rate_mps', 'min_turn_radius_m',
      'battery_wh', 'cruise_power_w', 'payload_kg', 'max_payload_kg', 'reserve_soc',
      'cruise_altitude_m', 'obstacle_clearance_m', 'depart_s', 'deadline_s',
      'min_uav_separation_m', 'min_route_coverage', 'max_blind_gap_m', 'sensing_weight',
    ];
    for (const k of routeKeys) {
      const el = $('rt-' + k);
      if (el) r[k] = Number(el.value);
    }
    if ($('rt-wx') && $('rt-wy')) r.wind_xy_mps = [Number($('rt-wx').value), Number($('rt-wy').value)];
    if ($('rt-sx') && $('rt-sy')) r.start_xyz = [Number($('rt-sx').value), Number($('rt-sy').value), 0];
    if ($('rt-gx') && $('rt-gy')) r.goal_xyz = [Number($('rt-gx').value), Number($('rt-gy').value), 0];
    r.no_fly_zones.forEach((z, idx) => {
      for (const f of ['x0', 'y0', 'x1', 'y1', 'z1', 'active_start_s', 'active_end_s']) {
        const el = $(`nfz-${f}-${idx}`);
        if (el) z[f] = Number(el.value);
      }
    });

    const sc = spec.scheduling;
    for (const k of ['pad_capacity', 'pilot_concurrency', 'shared_uav_count']) {
      const el = $('sc-' + k);
      if (el) sc[k] = Math.round(Number(el.value));
    }
    for (const k of ['pad_lock_s', 'turret_horizon_s', 'turret_slew_deg_s', 'turret_dwell_s', 'hard_deadline_s']) {
      const el = $('sc-' + k);
      if (el) sc[k] = Number(el.value);
    }
    sc.missions.forEach((m, idx) => {
      for (const f of ['priority', 'duration_s', 'required_payload_kg', 'energy_wh', 'earliest_start_s', 'deadline_s']) {
        const el = $(`msn-${f}-${idx}`);
        if (el) m[f] = f === 'priority' ? Math.round(Number(el.value)) : Number(el.value);
      }
    });
    sc.nodes.forEach((n, idx) => {
      for (const f of ['gcycles_s', 'memory_gb', 'vram_gb']) {
        const el = $(`node-${f}-${idx}`);
        if (el) n[f] = Number(el.value);
      }
    });
    state.spec = spec;
    return spec;
  }

  function renderForms() {
    const spec = state.spec;
    if (!spec) return;
    $('scene-select').value = currentScene;
    $('building-source').value = spec.siting.building_source;

    const s = spec.siting;
    $('siting-params').innerHTML = [
      numField('sit-budget', '新增总预算 / 万元', s.budget, 0, 2000, 0.5),
      numField('sit-max_new_sites', '最多新建站址 / 处', s.max_new_sites, 0, 20, 1),
      numField('sit-coverage_target', '全域覆盖率目标 (0–1)', s.coverage_target, 0.05, 1, 0.01),
      numField('sit-focus_target', '重点区覆盖率目标 (0–1)', s.focus_target, 0.05, 1, 0.01),
      numField('sit-precision_rate_target', 'CRLB精度达标率目标 (0–1)', s.precision_rate_target, 0, 1, 0.01),
      numField('sit-precision_m', '定位精度门限 / m', s.precision_m, 5, 300, 1),
      numField('sit-pd_threshold', '探测概率门限 Pd', s.pd_threshold, 0.1, 0.99, 0.01),
      numField('sit-min_radar_separation_m', '有源雷达同频最小间距 / m', s.min_radar_separation_m, 0, 1800, 10),
      numField('sit-min_radar_rf_isolation_m', '雷达—RF防阻塞隔离距 / m', s.min_radar_rf_isolation_m, 0, 1200, 5),
      numField('sit-min_band_overlap_separation_m', '同频段设备最小间距 / m', s.min_band_overlap_separation_m, 0, 1800, 10),
      numField('sit-min_jammer_rf_isolation_m', '反制站—RF防阻塞隔离距 / m', s.min_jammer_rf_isolation_m ?? 180, 0, 1800, 10),
      numField('sit-jammer_suppression_db', '反制开启跨频段干扰增量 / dB', s.jammer_suppression_db ?? 6, 0, 35, 0.5),
      numField('sit-terrain_elevation_m', '场地基准高程 / m', s.terrain_elevation_m, 0, 200, 1),
      numField('sit-noise_floor_dbm', '环境电磁底噪 / dBm', s.noise_floor_dbm, -120, -55, 0.5),
      numField('sit-rain_fog_attenuation_db_km', '雨雾比衰减 / dB/km', s.rain_fog_attenuation_db_km, 0, 12, 0.1),
      numField('sit-cochannel_db', '共信道干扰余量 / dB', s.cochannel_db, 0, 30, 0.5),
      numField('sit-communication_db', '通信散射余量 / dB', s.communication_db, 0, 30, 0.5),
    ].join('');

    $('siting-sites-table').innerHTML = `<table><thead><tr>
      <th>站址编号</th><th>名称</th><th>坐标 (X, Y)</th><th>最大承重 kg</th><th>供电上限 W</th><th>回传 Mbps</th><th>建站费 万</th><th>市电</th><th>光纤</th><th>场地准入</th>
    </tr></thead><tbody>${s.sites.map((st, idx) => `<tr>
      <td><code>${esc(st.id)}</code></td>
      <td>${esc(st.name)}</td>
      <td>${st.x}, ${st.y}</td>
      <td><input id="site-load-${idx}" class="inline-input" type="number" value="${st.max_load_kg}" min="5" max="5000"></td>
      <td><input id="site-power-${idx}" class="inline-input" type="number" value="${st.power_w}" min="50" max="200000"></td>
      <td><input id="site-bw-${idx}" class="inline-input" type="number" value="${st.bandwidth_mbps}" min="1" max="100000"></td>
      <td><input id="site-cost-${idx}" class="inline-input" type="number" value="${st.setup_cost}" min="0" max="500" step="0.1"></td>
      <td><input id="site-mains-${idx}" type="checkbox" ${st.mains_power_ready ? 'checked' : ''}></td>
      <td><input id="site-fiber-${idx}" type="checkbox" ${st.fiber_ready ? 'checked' : ''}></td>
      <td><input id="site-lease-${idx}" type="checkbox" ${st.lease_approved ? 'checked' : ''}></td>
    </tr>`).join('')}</tbody></table>`;

    const r = spec.routing;
    $('routing-params').innerHTML = [
      numField('rt-sx', '起点 X / m', r.start_xyz[0], 0, 2000, 10),
      numField('rt-sy', '起点 Y / m', r.start_xyz[1], 0, 2000, 10),
      numField('rt-gx', '终点 X / m', r.goal_xyz[0], 0, 2000, 10),
      numField('rt-gy', '终点 Y / m', r.goal_xyz[1], 0, 2000, 10),
      numField('rt-cruise_altitude_m', '巡航高度 / m', r.cruise_altitude_m, 35, 260, 5),
      numField('rt-obstacle_clearance_m', '建筑/禁飞安全间隔 / m', r.obstacle_clearance_m, 10, 80, 1),
      numField('rt-cruise_speed_mps', '额定真空速 / m/s', r.cruise_speed_mps, 4, 35, 0.5),
      numField('rt-wx', '环境风速 Wx / m/s', r.wind_xy_mps[0], -18, 18, 0.2),
      numField('rt-wy', '环境风速 Wy / m/s', r.wind_xy_mps[1], -18, 18, 0.2),
      numField('rt-max_climb_rate_mps', '最大爬升率 / m/s', r.max_climb_rate_mps, 0.5, 12, 0.1),
      numField('rt-max_descent_rate_mps', '最大下降率 / m/s', r.max_descent_rate_mps, 0.5, 10, 0.1),
      numField('rt-min_turn_radius_m', '最小转弯半径 / m', r.min_turn_radius_m, 0, 150, 1),
      numField('rt-payload_kg', '任务挂载重量 / kg', r.payload_kg, 0, 30, 0.1),
      numField('rt-max_payload_kg', '最大允许载重 / kg', r.max_payload_kg, 0.2, 50, 0.1),
      numField('rt-battery_wh', '电池总容量 / Wh', r.battery_wh, 50, 5000, 10),
      numField('rt-cruise_power_w', '基准巡航功率 / W', r.cruise_power_w, 50, 5000, 10),
      numField('rt-reserve_soc', '最低返航保留电量 SoC', r.reserve_soc, 0.1, 0.6, 0.01),
      numField('rt-depart_s', '起飞时刻 / s', r.depart_s, 0, 3600, 5),
      numField('rt-deadline_s', '最晚到达时刻 / s', r.deadline_s, 20, 7200, 10),
      numField('rt-min_uav_separation_m', '多机最小四维间隔 / m', r.min_uav_separation_m, 15, 300, 5),
      numField('rt-min_route_coverage', '航路最低感知覆盖率', r.min_route_coverage, 0, 1, 0.01),
      numField('rt-max_blind_gap_m', '允许最长连续盲段 / m', r.max_blind_gap_m, 0, 2000, 10),
      numField('rt-sensing_weight', '感知走廊引导权重', r.sensing_weight, 0, 6, 0.2),
    ].join('');

    $('routing-nfz-table').innerHTML = `<table><thead><tr>
      <th>禁飞区编号</th><th>名称</th><th>X0</th><th>Y0</th><th>X1</th><th>Y1</th><th>上限高度 Z1</th><th>生效起始 s</th><th>生效结束 s</th>
    </tr></thead><tbody>${r.no_fly_zones.map((z, idx) => `<tr>
      <td><code>${esc(z.id)}</code></td>
      <td>${esc(z.name)}</td>
      <td><input id="nfz-x0-${idx}" class="inline-input" type="number" value="${z.x0}"></td>
      <td><input id="nfz-y0-${idx}" class="inline-input" type="number" value="${z.y0}"></td>
      <td><input id="nfz-x1-${idx}" class="inline-input" type="number" value="${z.x1}"></td>
      <td><input id="nfz-y1-${idx}" class="inline-input" type="number" value="${z.y1}"></td>
      <td><input id="nfz-z1-${idx}" class="inline-input" type="number" value="${z.z1}"></td>
      <td><input id="nfz-active_start_s-${idx}" class="inline-input" type="number" value="${z.active_start_s}"></td>
      <td><input id="nfz-active_end_s-${idx}" class="inline-input" type="number" value="${z.active_end_s}"></td>
    </tr>`).join('')}</tbody></table>`;

    const sc = spec.scheduling;
    $('scheduling-params').innerHTML = [
      numField('sc-pad_capacity', '机场并发起降机位数', sc.pad_capacity, 1, 10, 1),
      numField('sc-pilot_concurrency', '值班飞手并发监管架次', sc.pilot_concurrency, 1, 10, 1),
      numField('sc-pad_lock_s', '单次起降机位占用时长 / s', sc.pad_lock_s, 10, 300, 5),
      numField('sc-turret_horizon_s', '传感器波束排程周期 / s', sc.turret_horizon_s, 4, 60, 1),
      numField('sc-turret_slew_deg_s', '机械云台最大转速 / °/s', sc.turret_slew_deg_s, 10, 180, 5),
      numField('sc-turret_dwell_s', '单目标最小波束驻留 / s', sc.turret_dwell_s, 0.3, 8, 0.1),
      numField('sc-shared_uav_count', '共享无线链路争用机数', sc.shared_uav_count, 1, 12, 1),
      numField('sc-hard_deadline_s', '云边端 DAG 硬截止期 / s', sc.hard_deadline_s, 0.5, 60, 0.1),
    ].join('');

    $('scheduling-missions-table').innerHTML = `<table><thead><tr>
      <th>任务编号</th><th>任务名称</th><th>优先级 (1-10)</th><th>执行时长 s</th><th>所需载重 kg</th><th>预计耗电 Wh</th><th>最早开始 s</th><th>最晚完成 s</th>
    </tr></thead><tbody>${sc.missions.map((m, idx) => `<tr>
      <td><code>${esc(m.id)}</code></td>
      <td>${esc(m.name)}</td>
      <td><input id="msn-priority-${idx}" class="inline-input" type="number" value="${m.priority}" min="1" max="10"></td>
      <td><input id="msn-duration_s-${idx}" class="inline-input" type="number" value="${m.duration_s}" min="25" max="3600"></td>
      <td><input id="msn-required_payload_kg-${idx}" class="inline-input" type="number" value="${m.required_payload_kg}" min="0" max="40" step="0.1"></td>
      <td><input id="msn-energy_wh-${idx}" class="inline-input" type="number" value="${m.energy_wh}" min="10" max="4000"></td>
      <td><input id="msn-earliest_start_s-${idx}" class="inline-input" type="number" value="${m.earliest_start_s}" min="0" max="7200"></td>
      <td><input id="msn-deadline_s-${idx}" class="inline-input" type="number" value="${m.deadline_s}" min="30" max="14400"></td>
    </tr>`).join('')}</tbody></table>`;

    $('scheduling-nodes-table').innerHTML = `<table><thead><tr>
      <th>节点编号</th><th>层级</th><th>算力 Gcycles/s</th><th>内存上限 GB</th><th>GPU显存上限 GB</th>
    </tr></thead><tbody>${sc.nodes.map((n, idx) => `<tr>
      <td><code>${esc(n.id)}</code></td>
      <td>${esc(n.kind)}</td>
      <td><input id="node-gcycles_s-${idx}" class="inline-input" type="number" value="${n.gcycles_s}" min="0.2" max="1000" step="0.2"></td>
      <td><input id="node-memory_gb-${idx}" class="inline-input" type="number" value="${n.memory_gb}" min="0.5" max="1024" step="0.5"></td>
      <td><input id="node-vram_gb-${idx}" class="inline-input" type="number" value="${n.vram_gb}" min="0" max="512" step="0.5"></td>
    </tr>`).join('')}</tbody></table>`;
  }

  function renderResults() {
    const res = state.result;
    if (!res) {
      $('kpi-strip').innerHTML = `<div class="kpi-card warn"><small>尚未运行三阶段求解</small><strong>待执行</strong><span>点击上方“三阶段联合校验与求解”生成全套结果</span></div>`;
      return;
    }
    const sit = res.siting;
    const rt = res.routing;
    const sch = res.scheduling;
    const cm = sit.countermeasure_assessment;
    const kpis = [
      {
        cls: res.all_feasible ? 'ok' : 'warn',
        label: '全链路硬约束合规',
        value: `${res.passed_checks} / ${res.total_checks} 项`,
        sub: res.all_feasible ? '选址·航路·调度全部满足' : '存在未达标约束，请核查清单',
      },
      {
        cls: sit.feasible ? 'ok' : 'warn',
        label: '阶段一 · 选址后全域/重点覆盖',
        value: `${pct(sit.after.coverage)} / ${pct(sit.after.focus_coverage)}`,
        sub: `新增 ${sit.selected_candidates.length} 台 · 费用 ${sit.added_cost} 万 · 精度达标 ${pct(sit.after.precision_pass_rate)}`,
      },
      {
        cls: !cm || cm.emc_rf_isolated ? 'ok' : 'warn',
        label: '感反电磁兼容与压制态保底覆盖',
        value: cm ? `重点区 ${pct(cm.jamming_active_focus_coverage)}` : '合规',
        sub: cm ? `RF距反制站最小 ${cm.actual_min_rf_jammer_m ?? '无RF'}m (门限≥${cm.min_jammer_rf_isolation_m}m)` : '遵循《条例》第43条受控隔离',
      },
      {
        cls: rt.feasible ? 'ok' : 'warn',
        label: '阶段二 · 耦合航路感知覆盖',
        value: `${pct(rt.summary.route_coverage)}（盲段 ${rt.summary.max_blind_gap_m}m）`,
        sub: `最短路基线覆盖 ${pct(rt.summary.shortest_baseline_coverage)} · 剩余电量 ${pct(rt.summary.remaining_soc)}`,
      },
      {
        cls: sch.feasible ? 'ok' : 'warn',
        label: '阶段三 · 机群排班与边缘 DAG',
        value: `${sch.fleet.assignments.length}/${state.spec.scheduling.missions.length} 任务 · ${sch.compute_dag.makespan_s ?? '不可行'}s`,
        sub: `波束重访达标 ${sch.sensor_turrets.targets.filter(t => t.passed).length}/${sch.sensor_turrets.targets.length} 目标`,
      },
    ];
    $('kpi-strip').innerHTML = kpis.map(k => `<div class="kpi-card ${k.cls}"><small>${esc(k.label)}</small><strong>${esc(k.value)}</strong><span>${esc(k.sub)}</span></div>`).join('');

    $('siting-outcome').innerHTML = `
      <strong>选址求解结果：</strong>${esc(sit.method)}<br>
      已评估可行组合 <b>${sit.evaluated_combinations}</b> 组；入选候选设备：
      <b>${sit.selected_candidates.map(c => `${c.id}@${c.site_id} (${c.model_id})`).join('、') || '无新增'}</b>，
      总新增工程费 <b>${sit.added_cost} 万元</b>。
      全域覆盖由 ${pct(sit.before.coverage)} 提升至 <b>${pct(sit.after.coverage)}</b>，
      CRLB 精度达标率由 ${pct(sit.before.precision_pass_rate)} 提升至 <b>${pct(sit.after.precision_pass_rate)}</b>。
      ${sit.rejected_candidates.length ? `<br><span class="muted">因硬约束被拦截的候选示例：${sit.rejected_candidates.slice(0, 3).map(x => `${esc(x.id)}（${esc(x.reason)}）`).join('；')}</span>` : ''}
    `;

    $('routing-outcome').innerHTML = `
      <strong>航路求解结果：</strong>${esc(rt.method)}<br>
      总航程 <b>${rt.summary.distance_m} m</b>（${rt.waypoints.length} 个三维航点），预计到达时刻 <b>${rt.summary.arrival_s} s</b>，
      风场与挂载修正耗电 <b>${rt.summary.energy_wh} Wh</b>（返航剩余 SoC <b>${pct(rt.summary.remaining_soc)}</b>），
      邻机四维最小间距 <b>${rt.summary.min_peer_separation_m ?? '无邻机'} m</b>，
      沿程感知覆盖率 <b>${pct(rt.summary.route_coverage)}</b>（对比未耦合最短航路 ${pct(rt.summary.shortest_baseline_coverage)}）。
    `;

    $('scheduling-outcome').innerHTML = `
      <strong>三级任务调度结果：</strong>${esc(sch.method)}<br>
      1) <b>机群飞行排班</b>：${sch.fleet.assignments.map(a => `${esc(a.mission_id)}→${esc(a.uav_id)} [${a.start_s}s–${a.end_s}s, 裕度 ${a.slack_s}s]`).join('；') || '无'}
      ${sch.fleet.unassigned.length ? `<span class="badge-fail">未排班 ${sch.fleet.unassigned.length} 项：${esc(sch.fleet.unassigned[0].reason)}</span>` : ''}<br>
      2) <b>地面定向波束驻留</b>：${sch.sensor_turrets.events.length} 个云台指向/驻留时隙，目标最大重访间隔 ${sch.sensor_turrets.targets.map(t => `${esc(t.target_id)}:${t.max_gap_s}s/${t.limit_s}s`).join('，')}<br>
      3) <b>云边端显存与争用 DAG</b>：${sch.compute_dag.rows.map(r => `${esc(r.id)}@${esc(r.node)}(${r.start_s}–${r.end_s}s)`).join(' → ')}
      ${sch.compute_dag.error ? `<span class="badge-fail">${esc(sch.compute_dag.error)}</span>` : `，总完工 <b>${sch.compute_dag.makespan_s} s</b>`}
    `;

    const stageLabel = {siting: '1. 设备选址', routing: '2. 航路规划', scheduling: '3. 任务调度'};
    $('checks-table').innerHTML = `<table><thead><tr>
      <th>阶段</th><th>约束编码</th><th>标准硬约束项</th><th>实测/求解值</th><th>门限/要求</th><th>结论</th>
    </tr></thead><tbody>${res.checks.map(c => `<tr>
      <td>${esc(stageLabel[c.stage] || c.stage)}</td>
      <td><code>${esc(c.code)}</code></td>
      <td>${esc(c.title)}</td>
      <td>${esc(c.actual)}</td>
      <td>${esc(c.limit)}</td>
      <td><span class="${c.passed ? 'badge-pass' : 'badge-fail'}">${c.passed ? '合规通过' : '违约告警'}</span></td>
    </tr>`).join('')}</tbody></table>`;

    drawSpatialCanvas();
    drawGanttCanvas();
    if (typeof window !== 'undefined' && window.Modeling3D) {
      window.Modeling3D.update(state.spec, state.result);
    }
  }
  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('modeling-3d-ready', () => {
      if (state.spec && state.result && window.Modeling3D) window.Modeling3D.update(state.spec, state.result);
    });
  }

  function drawSpatialCanvas() {
    const canvas = $('spatial-canvas');
    if (!canvas || !state.result) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height, pad = 24;
    const scale = Math.min((W - 2 * pad) / 2000, (H - 2 * pad) / 2000);
    const ox = (W - 2000 * scale) / 2;
    const oy = H - pad;
    const tx = x => ox + x * scale;
    const ty = y => oy - y * scale;

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#f3f7fb';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#cbd8e4';
    ctx.strokeRect(tx(0), ty(2000), 2000 * scale, 2000 * scale);

    const sit = state.result.siting;
    const rt = state.result.routing;
    // Focus zone
    if (sit.zone) {
      ctx.strokeStyle = '#d97706';
      ctx.setLineDash([5, 4]);
      ctx.strokeRect(tx(sit.zone.x0), ty(sit.zone.y1), (sit.zone.x1 - sit.zone.x0) * scale, (sit.zone.y1 - sit.zone.y0) * scale);
      ctx.setLineDash([]);
    }
    // Statutory authorized countermeasure sector & RF EMC isolation ring
    const cmSites = sit.countermeasure_assessment?.sites || state.spec.siting?.countermeasure_sites || [];
    const cmIso = (state.spec.siting?.min_jammer_rf_isolation_m ?? 180) * scale;
    for (const cm of cmSites) {
      const rPx = (cm.range_m || 560) * scale;
      const az = (cm.azimuth || 45) * Math.PI / 180;
      const half = (cm.fov || 85) * Math.PI / 360;
      ctx.fillStyle = 'rgba(230, 126, 34, 0.15)';
      ctx.strokeStyle = 'rgba(211, 84, 0, 0.72)';
      ctx.beginPath();
      ctx.moveTo(tx(cm.x), ty(cm.y));
      ctx.arc(tx(cm.x), ty(cm.y), rPx, -Math.PI / 2 + az - half, -Math.PI / 2 + az + half);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = '#16a34a';
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.arc(tx(cm.x), ty(cm.y), cmIso, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // Buildings
    ctx.fillStyle = 'rgba(100, 116, 139, 0.35)';
    for (const b of (sit.buildings || []).slice(0, 260)) {
      ctx.fillRect(tx(b.x), ty(b.y + b.d), Math.max(2, b.w * scale), Math.max(2, b.d * scale));
    }
    // NFZs
    for (const z of state.spec.routing.no_fly_zones) {
      ctx.fillStyle = 'rgba(220, 38, 38, 0.16)';
      ctx.strokeStyle = '#dc2626';
      ctx.fillRect(tx(z.x0), ty(z.y1), (z.x1 - z.x0) * scale, (z.y1 - z.y0) * scale);
      ctx.strokeRect(tx(z.x0), ty(z.y1), (z.x1 - z.x0) * scale, (z.y1 - z.y0) * scale);
    }
    // Peer UAV trajectory
    for (const peer of state.spec.routing.peer_uavs) {
      ctx.strokeStyle = '#6366f1';
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      peer.waypoints.forEach((p, idx) => { idx ? ctx.lineTo(tx(p[0]), ty(p[1])) : ctx.moveTo(tx(p[0]), ty(p[1])); });
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // Deployed devices
    const addedIds = new Set(sit.selected_candidates.map(c => c.id));
    for (const d of sit.deployed_network) {
      const isNew = addedIds.has(d.id);
      ctx.fillStyle = isNew ? '#d97706' : '#0f766e';
      ctx.beginPath();
      ctx.arc(tx(d.x), ty(d.y), isNew ? 5.5 : 4.5, 0, Math.PI * 2);
      ctx.fill();
      if (d.type === 'RADAR') {
        ctx.strokeStyle = isNew ? 'rgba(217, 119, 6, 0.28)' : 'rgba(15, 118, 110, 0.22)';
        ctx.beginPath();
        ctx.arc(tx(d.x), ty(d.y), state.spec.siting.min_radar_separation_m * scale * 0.5, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    // Planned route segments colored by sensing coverage
    const samples = rt.samples || [];
    ctx.lineWidth = 2.6;
    for (let i = 1; i < samples.length; i++) {
      ctx.strokeStyle = samples[i].covered ? '#15803d' : '#dc2626';
      ctx.beginPath();
      ctx.moveTo(tx(samples[i - 1].x), ty(samples[i - 1].y));
      ctx.lineTo(tx(samples[i].x), ty(samples[i].y));
      ctx.stroke();
    }
    ctx.lineWidth = 1;
    $('spatial-pill').textContent = `${state.spec.siting.building_source} · 覆盖 ${pct(rt.summary.route_coverage)}`;
  }

  function drawGanttCanvas() {
    const canvas = $('gantt-canvas');
    if (!canvas || !state.result) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, W, H);

    const mode = $('gantt-mode').value;
    const sch = state.result.scheduling;
    const left = 135, right = W - 22, top = 28, bottom = H - 28;
    ctx.font = '12px system-ui, sans-serif';

    if (mode === 'fleet') {
      const rows = state.spec.scheduling.uavs;
      const maxT = Math.max(400, ...sch.fleet.assignments.map(a => a.turnaround_ready_s));
      const rowH = Math.min(64, (bottom - top) / Math.max(1, rows.length));
      rows.forEach((u, idx) => {
        const y = top + idx * rowH;
        ctx.fillStyle = '#1e3a4c';
        ctx.fillText(`${u.id} (${u.max_payload_kg}kg)`, 12, y + rowH * 0.55);
        ctx.strokeStyle = '#e2e8f0';
        ctx.beginPath(); ctx.moveTo(left, y + rowH * 0.5); ctx.lineTo(right, y + rowH * 0.5); ctx.stroke();
      });
      for (const a of sch.fleet.assignments) {
        const idx = rows.findIndex(u => u.id === a.uav_id);
        if (idx < 0) continue;
        const y = top + idx * rowH + rowH * 0.2;
        const x0 = left + (a.start_s / maxT) * (right - left);
        const x1 = left + (a.end_s / maxT) * (right - left);
        const x2 = left + (a.turnaround_ready_s / maxT) * (right - left);
        ctx.fillStyle = '#16526d';
        ctx.fillRect(x0, y, Math.max(4, x1 - x0), rowH * 0.56);
        ctx.fillStyle = '#94a3b8';
        ctx.fillRect(x1, y + rowH * 0.12, Math.max(2, x2 - x1), rowH * 0.32);
        ctx.fillStyle = '#fff';
        ctx.fillText(a.mission_id, x0 + 5, y + rowH * 0.38);
      }
    } else if (mode === 'turret') {
      const evs = sch.sensor_turrets.events;
      const sensors = [...new Set(evs.map(e => e.sensor_id))];
      const maxT = state.spec.scheduling.turret_horizon_s;
      const rowH = Math.min(44, (bottom - top) / Math.max(1, sensors.length));
      sensors.forEach((sid, idx) => {
        const y = top + idx * rowH;
        ctx.fillStyle = '#1e3a4c';
        ctx.fillText(sid, 12, y + rowH * 0.6);
      });
      const palette = {'TGT-A': '#0f766e', 'TGT-B': '#1d4ed8', 'TGT-C': '#b45309'};
      for (const e of evs) {
        const idx = sensors.indexOf(e.sensor_id);
        const y = top + idx * rowH + rowH * 0.18;
        const x0 = left + (e.start_s / maxT) * (right - left);
        const x1 = left + (e.end_s / maxT) * (right - left);
        ctx.fillStyle = palette[e.target_id] || '#334155';
        ctx.fillRect(x0, y, Math.max(4, x1 - x0), rowH * 0.62);
      }
    } else {
      const nodes = state.spec.scheduling.nodes;
      const rows = sch.compute_dag.rows;
      const maxT = Math.max(state.spec.scheduling.hard_deadline_s, ...(rows.map(r => r.result_s)));
      const rowH = Math.min(58, (bottom - top) / Math.max(1, nodes.length));
      nodes.forEach((n, idx) => {
        const y = top + idx * rowH;
        ctx.fillStyle = '#1e3a4c';
        ctx.fillText(`${n.id} (${n.vram_gb}G显存)`, 10, y + rowH * 0.55);
      });
      for (const r of rows) {
        const idx = nodes.findIndex(n => n.id === r.node);
        if (idx < 0) continue;
        const y = top + idx * rowH + rowH * 0.2;
        const x0 = left + (r.start_s / maxT) * (right - left);
        const x1 = left + (r.end_s / maxT) * (right - left);
        ctx.fillStyle = r.deadline_met ? '#0f766e' : '#dc2626';
        ctx.fillRect(x0, y, Math.max(6, x1 - x0), rowH * 0.56);
        ctx.fillStyle = '#fff';
        ctx.fillText(r.id, x0 + 4, y + rowH * 0.38);
      }
    }
  }

  function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    for (const key of ['siting', 'routing', 'scheduling', 'checks']) {
      const panel = $('panel-' + key);
      if (panel) panel.hidden = key !== tab;
    }
  }

  async function loadScene(sceneId) {
    currentScene = sceneId;
    const data = await api('/modeling/' + sceneId);
    state.spec = data.spec;
    state.audit = data.audit;
    state.result = data.latest_result || null;
    renderForms();
    if (!state.result) {
      const solved = await api('/modeling/' + sceneId + '/solve', {stage: 'all'});
      state.result = solved.result;
    }
    renderResults();
    setStatus(`已加载「${sceneId}」标准建模参数（版本 v${state.spec.revision}），共 ${state.result.passed_checks}/${state.result.total_checks} 项约束通过。`);
  }

  async function solveStage(stage) {
    const spec = readSpecFromDom();
    const solved = await api('/modeling/' + currentScene + '/solve', {stage, spec});
    state.spec = solved.spec;
    state.audit = solved.audit;
    state.result = solved.result;
    renderForms();
    renderResults();
    setStatus(`已完成「${stage === 'all' ? '三阶段联合' : stage}」约束求解（版本 v${state.spec.revision}）：${state.result.passed_checks}/${state.result.total_checks} 项硬约束通过。`);
  }

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
  $('scene-select').addEventListener('change', () => runGuarded('正在切换场景', () => loadScene($('scene-select').value)));
  $('building-source').addEventListener('change', () => runGuarded('正在切换建筑遮挡源并重算', () => solveStage('all')));
  $('gantt-mode').addEventListener('change', drawGanttCanvas);

  $('btn-solve-all').addEventListener('click', () => runGuarded('正在执行三阶段联合校验与求解', () => solveStage('all')));
  $('btn-solve-siting').addEventListener('click', () => runGuarded('正在重算设备选址', () => solveStage('siting')));
  $('btn-solve-routing').addEventListener('click', () => runGuarded('正在重算感知耦合航路', () => solveStage('routing')));
  $('btn-solve-scheduling').addEventListener('click', () => runGuarded('正在重算三级任务调度', () => solveStage('scheduling')));

  $('btn-check').addEventListener('click', () => runGuarded('正在校验参数与静态工程约束', async () => {
    const spec = readSpecFromDom();
    const audit = await api('/modeling/' + currentScene + '/check', spec);
    state.audit = audit;
    const msg = audit.existing_violations.length
      ? `发现 ${audit.existing_violations.length} 项既有约束违约：${audit.existing_violations.join('；')}`
      : `静态工程校验通过：既有设备 ${audit.existing_device_count} 台无违约，单站合规候选 ${audit.feasible_single_candidates}/${audit.candidate_device_count} 台。`;
    setStatus(msg, audit.existing_violations.length > 0);
  }));

  $('btn-save').addEventListener('click', () => runGuarded('正在保存建模参数', async () => {
    const spec = readSpecFromDom();
    const saved = await api('/modeling/' + currentScene, spec, 'PUT');
    state.spec = saved.spec;
    state.audit = saved.audit;
    setStatus(`建模参数已保存（版本 v${state.spec.revision}）。`);
  }));

  $('btn-reset').addEventListener('click', () => runGuarded('正在重置为工程标准模板', async () => {
    const data = await api('/modeling/' + currentScene + '/reset', {});
    state.spec = data.spec;
    state.audit = data.audit;
    state.result = data.latest_result;
    renderForms();
    renderResults();
    setStatus(`已重置为工程标准模板并完成三阶段求解（${state.result.passed_checks}/${state.result.total_checks} 项约束通过）。`);
  }));

  $('btn-export').addEventListener('click', () => {
    const payload = {spec: state.spec, result: state.result};
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `standard-modeling-${currentScene}-v${state.spec?.revision || 1}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  runGuarded('正在初始化标准参数与约束建模层', () => loadScene(currentScene));
})();
