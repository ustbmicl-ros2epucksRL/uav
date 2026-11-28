const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('standard modeling UI renders 3-stage forms, edits parameters, switches tabs, and draws canvases', async () => {
  const elements = {};
  const calls = [];
  class Element {
    constructor(id = '') {
      this.id = id;
      this.value = '';
      this.checked = true;
      this.hidden = false;
      this.textContent = '';
      this.innerHTML = '';
      this.className = '';
      this.width = 620;
      this.height = 360;
      this.dataset = {};
      this.handlers = {};
      this.classList = {
        set: new Set(),
        toggle(cls, on) { on ? this.set.add(cls) : this.set.delete(cls); },
        add(cls) { this.set.add(cls); },
      };
    }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    getContext() { return new Proxy({}, {get: () => () => {}}); }
    click() { if (this.handlers.click) return this.handlers.click(); }
  }
  const get = id => elements[id] ??= new Element(id);
  get('scene-select').value = 'urban';
  get('building-source').value = 'synthetic';
  get('gantt-mode').value = 'fleet';

  const tabBtns = ['siting', 'routing', 'scheduling', 'checks'].map(tab => {
    const b = new Element('tab-' + tab);
    b.dataset.tab = tab;
    return b;
  });

  const sampleSpec = {
    schema_version: 'standard-modeling-v1',
    scene: 'urban',
    revision: 1,
    title: '园区标准模型',
    siting: {
      building_source: 'synthetic', terrain_elevation_m: 12, noise_floor_dbm: -95,
      rain_fog_attenuation_db_km: 0.8, cochannel_db: 3, communication_db: 2,
      pd_threshold: 0.5, precision_m: 30, grid_cell_m: 50, budget: 68, max_new_sites: 4,
      coverage_target: 0.8, focus_target: 0.86, precision_rate_target: 0.62,
      min_radar_separation_m: 260, min_radar_rf_isolation_m: 90, min_band_overlap_separation_m: 150,
      sites: [{id: 'SITE-E01', name: '通信塔站址', x: 360, y: 480, min_height: 20, max_height: 165, capacity: 3, max_load_kg: 180, power_w: 2200, bandwidth_mbps: 400, mains_power_ready: true, fiber_ready: true, lease_approved: true, setup_cost: 0}],
      models: [{id: 'MDL-RADAR-X1', name: '雷达', type: 'RADAR', range: 1150, fov: 180, vertical_fov: 95, cost: 28, weight_kg: 62, power_w: 650, bandwidth_mbps: 80, requires_mains: true, requires_fiber: true, freq_low_mhz: 15800, freq_high_mhz: 16200}],
      existing_devices: [{id: 'RADAR-01', name: '西南塔', site_id: 'SITE-E01', model_id: 'MDL-RADAR-X1', z: 115, azimuth: 45, pitch: 10, status: 'online'}],
      candidate_devices: [],
    },
    routing: {
      vehicle_name: 'X4', cruise_speed_mps: 14, max_climb_rate_mps: 3.5, max_descent_rate_mps: 2.8,
      min_turn_radius_m: 22, battery_wh: 380, cruise_power_w: 420, climb_extra_power_w: 220,
      payload_kg: 1.6, max_payload_kg: 3.5, reserve_soc: 0.25,
      start_xyz: [180, 240, 0], goal_xyz: [1820, 1760, 0], cruise_altitude_m: 125,
      altitude_bounds_m: [35, 260], obstacle_clearance_m: 22, wind_xy_mps: [3, -1.5],
      depart_s: 10, deadline_s: 360, min_uav_separation_m: 60, min_route_coverage: 0.85,
      max_blind_gap_m: 180, sensing_weight: 1.6,
      no_fly_zones: [{id: 'NFZ-1', name: '变电站', x0: 840, y0: 860, x1: 1040, y1: 1040, z0: 0, z1: 210, active_start_s: 0, active_end_s: 500}],
      peer_uavs: [{uav_id: 'UAV-P2', name: '邻机', depart_s: 15, speed_mps: 13, waypoints: [[1780, 360, 125], [320, 1520, 125]]}],
    },
    scheduling: {
      pad_capacity: 2, pilot_concurrency: 2, pad_lock_s: 35,
      uavs: [{id: 'UAV-01', name: 'A01', max_payload_kg: 3.5, battery_wh: 380, reserve_soc: 0.25, turnaround_charge_s: 85, available_from_s: 0}],
      missions: [{id: 'MSN-101', name: '巡检', priority: 9, duration_s: 175, required_payload_kg: 1.6, energy_wh: 175, earliest_start_s: 10, deadline_s: 360}],
      turret_horizon_s: 12, turret_slew_deg_s: 45, turret_dwell_s: 1.1,
      targets: [{id: 'TGT-A', name: '目标A', position_xyz: [960, 980, 120], priority: 10, required_modalities: 2, max_revisit_s: 5}],
      shared_uav_count: 2, hard_deadline_s: 4.5,
      nodes: [{id: 'device', kind: 'device', position: [600, 600, 125], gcycles_s: 1.4, power_w: 12, memory_gb: 4, vram_gb: 2, busy_s: 0}],
      links: [{source: 'device', target: 'device', mbps: 100, latency_s: 0, max_range_m: 0}],
      dag_tasks: [{id: 'preprocess', gcycles: 0.8, memory_gb: 1.2, requires_vram_gb: 0, input_mbit: 14, output_mbit: 5, deadline_s: 1.5, parents: []}],
    },
  };

  const sampleResult = {
    passed_checks: 19, total_checks: 19, all_feasible: true,
    siting: {
      feasible: true, method: '强约束边际收益', evaluated_combinations: 12, added_cost: 43.5,
      selected_candidates: [{id: 'C-01', site_id: 'SITE-C01', model_id: 'MDL-RADAR-X1'}],
      deployed_network: [{id: 'RADAR-01', type: 'RADAR', x: 360, y: 480, z: 115}],
      rejected_candidates: [{id: 'C-02', reason: '站址无光纤'}],
      before: {coverage: 0.62, focus_coverage: 0.7, precision_pass_rate: 0.45},
      after: {coverage: 0.86, focus_coverage: 0.92, precision_pass_rate: 0.71},
      buildings: [{x: 200, y: 200, w: 80, d: 80, h: 60}],
      zone: {x0: 700, y0: 700, x1: 1300, y1: 1300},
    },
    routing: {
      feasible: true, method: '感知耦合三维A*', waypoints: [[180, 240, 0], [180, 240, 125], [1820, 1760, 125], [1820, 1760, 0]],
      samples: [{x: 180, y: 240, z: 125, covered: true}, {x: 900, y: 900, z: 125, covered: true}],
      summary: {distance_m: 2380, arrival_s: 215, energy_wh: 240, remaining_soc: 0.368, route_coverage: 0.91, shortest_baseline_coverage: 0.78, max_blind_gap_m: 75, min_peer_separation_m: 145},
    },
    scheduling: {
      feasible: true, method: '三级协同排程',
      fleet: {assignments: [{mission_id: 'MSN-101', uav_id: 'UAV-01', start_s: 10, end_s: 185, turnaround_ready_s: 270, slack_s: 175}], unassigned: []},
      sensor_turrets: {events: [{sensor_id: 'RADAR-01', target_id: 'TGT-A', start_s: 0.5, end_s: 1.6}], targets: [{target_id: 'TGT-A', max_gap_s: 3.2, limit_s: 5, passed: true}]},
      compute_dag: {makespan_s: 2.85, error: null, rows: [{id: 'preprocess', node: 'device', start_s: 0, end_s: 0.57, result_s: 0.57, deadline_met: true}]},
    },
    checks: [{stage: 'siting', code: 'SITE_MAINS_FIBER', title: '市电光纤匹配', actual: '全部匹配', limit: '合规', passed: true}],
  };

  const context = vm.createContext({
    location: {pathname: '/network/modeling', search: '?workbench=1'},
    window: {parent: {}, frameElement: {dataset: {frame: 'planning'}}},
    document: {
      getElementById: get,
      querySelectorAll: sel => sel === '.tab-btn' ? tabBtns : [],
      createElement: () => new Element('a'),
      body: {classList: {add() {}}},
    },
    fetch: async (url, opts) => {
      calls.push({url, opts});
      if (url.endsWith('/api/modeling/urban')) {
        return {ok: true, json: async () => ({spec: sampleSpec, audit: {existing_violations: [], existing_device_count: 1, feasible_single_candidates: 2, candidate_device_count: 2}, latest_result: sampleResult})};
      }
      if (url.endsWith('/api/modeling/urban/solve')) {
        const body = JSON.parse(opts.body);
        return {ok: true, json: async () => ({spec: {...(body.spec || sampleSpec), revision: 2}, audit: {existing_violations: []}, result: sampleResult})};
      }
      return {ok: true, json: async () => ({})};
    },
    Blob: class {}, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    URLSearchParams, JSON, Math, Number, Boolean, String, Set, Array, Object, Error, Promise, setTimeout,
  });

  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/modeling.js'), 'utf8'), context);
  for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r));

  assert.match(get('kpi-strip').innerHTML, /19 \/ 19 项/);
  assert.match(get('siting-params').innerHTML, /有源雷达同频最小间距/);
  assert.match(get('routing-params').innerHTML, /感知走廊引导权重/);
  assert.match(get('scheduling-params').innerHTML, /云边端 DAG 硬截止期/);
  assert.match(get('checks-table').innerHTML, /SITE_MAINS_FIBER/);

  tabBtns[1].click();
  assert.equal(get('panel-routing').hidden, false);
  assert.equal(get('panel-siting').hidden, true);

  await get('btn-solve-all').click();
  assert.ok(calls.some(c => c.url === '/network/api/modeling/urban/solve'));
  assert.match(get('status-bar').textContent, /v2/);
});
