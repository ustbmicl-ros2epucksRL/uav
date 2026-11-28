"""Framework-neutral, read-only tools over a frozen planning database snapshot."""
import copy
import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import analysis, flight_tools, proposal_kpi, proposal_source, research_overview, standard_modeling, store, twin_segment
from .twin_segment import SegmentRef
from .scenes import seed


class ToolContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene: Literal['urban', 'corridor', 'electromagnetic'] = 'urban'
    expected_revision: int | None = Field(default=None, ge=1)
    twin_segment: SegmentRef | None = None
    query: str | None = Field(default=None, min_length=1, max_length=200)


DESCRIPTIONS = {
    'research_framework': '读取本项目研究问题、五子项目输入输出关系、重要性、数字孪生反事实必要性、四维可行性及多平台协同边界。',
    'standard_modeling': '读取所选场景的设备选址、航路规划、三级任务调度标准参数完备性与 19 项工程/空域/显存争用硬约束核验结果（无需训练数据）。',
    'scene_snapshot': '读取所选场景的设备、候选站址数量及配置版本；配置不等于设备实时在线状态。',
    'diagnose_coverage': '对冻结场景执行现有 50 m 网格覆盖基线，返回覆盖率和薄弱区域；不证明 10 m/2 m 指标达标。',
    'planning_records': '读取所选场景最近 20 个规划任务摘要；没有记录时明确返回空列表。',
    'assessment_results': '读取所选场景最近 10 个验算结果摘要及历史场景版本；不启动计算或应用方案。',
    'twin_experiment': '读取指定实验的航线选段；未指定时读取所选场景最近的数字孪生实验。对比同一参考航迹上的现有部署、方法方案与反事实方案，并标明合成证据。',
    'proposal_kpi': '读取正式申报书考核指标版本与阈值，以及仅作对照、不参与判定的早期稿指标。',
    'proposal_search': '在正式申请书 end-1124 全文中检索与提问相关的条款原文，返回 PDF 页码；只引用原文，不解释为已完成。',
    'flight_algorithm_runs': '在研项目：读取最近的航路规划算法实验，含路径长度及 HEFT/本地/边缘/云端时延与能耗对比；为仿真结果。',
    'flight_compute': '在研项目：读取云边端实际执行状态、各飞行档案关联的计算批次数和稳定性测试结果。',
    'flight_records': '在研项目：读取最近的 SITL 飞行档案摘要（时长、采样数、关联业务任务）。',
    'service_tasks': '在研项目：读取低空服务管理平台的任务统计与最近任务（状态、校验结果、执行来源）。',
}
ToolName = Literal['research_framework', 'standard_modeling', 'scene_snapshot', 'diagnose_coverage', 'planning_records',
                   'assessment_results', 'twin_experiment', 'proposal_kpi', 'proposal_search',
                   'flight_algorithm_runs', 'flight_compute', 'flight_records', 'service_tasks']


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


CATEGORIES = {
    'research_framework': 'theory',
    'proposal_kpi': 'theory',
    'proposal_search': 'theory',
    'standard_modeling': 'platform_planning',
    'diagnose_coverage': 'platform_planning',
    'twin_experiment': 'platform_planning',
    'assessment_results': 'platform_planning',
    'scene_snapshot': 'platform_planning',
    'planning_records': 'platform_planning',
    'flight_algorithm_runs': 'platform_collab',
    'flight_compute': 'platform_collab',
    'flight_records': 'platform_collab',
    'service_tasks': 'platform_collab',
}


def catalog():
    return [dict(name=name, category=CATEGORIES[name], description=description,
                 input_schema=ToolContext.model_json_schema(),
                 effects='read_only', method='POST', path='/api/research-agent/tools/' + name)
            for name, description in DESCRIPTIONS.items()]


def snapshot(context):
    """One SQLite read transaction, including scene and historic summaries."""
    context = ToolContext.model_validate(context)
    with store.connection() as conn:
        conn.execute('BEGIN')
        row = conn.execute('SELECT value FROM state WHERE key=?', ('scene:' + context.scene,)).fetchone()
        scene = json.loads(row[0]) if row else seed(context.scene)
        if context.expected_revision is not None and scene['revision'] != context.expected_revision:
            raise HTTPException(409, '场景版本已改变，请重新读取场景后再分析')
        task_rows = conn.execute("SELECT value FROM state WHERE key LIKE 'portal:task:%' "
                                 "AND json_extract(value,'$.scene')=? "
                                 "ORDER BY json_extract(value,'$.updated_at') DESC,key DESC LIMIT 21",
                                 (context.scene,)).fetchall()
        tasks = [json.loads(row[0]) for row in task_rows]
        job_rows = conn.execute("SELECT id,status,created_at,snapshot,result FROM computation_jobs "
                                "WHERE scene=? AND kind='assessment' ORDER BY created_at DESC,id DESC LIMIT 11",
                                (context.scene,)).fetchall()
        assessments = []
        for row in job_rows[:10]:
            saved = json.loads(row['snapshot'])
            result = json.loads(row['result']) if row['result'] else {}
            version = saved.get('scene', {}).get('revision')
            assessments.append(dict(id=row['id'], status=row['status'], created_at=row['created_at'],
                task_id=saved.get('task', {}).get('id'), scene_revision=version,
                scene_revision_matches=version == scene['revision'],
                freshness_scope='仅核对场景版本；任务、资源、标定及算法版本需在验算页面完整核验',
                result={key: result[key] for key in ('stats', 'checks', 'feasible', 'added_cost', 'method',
                        'optimality', 'evaluated_cells', 'input_digest') if key in result},
                source_path='/api/assessments/' + row['id']))
        ref = context.twin_segment
        archive_key = 'twin-experiment:' + ref.experiment_id if ref else 'twin-experiment-latest:' + context.scene
        twin_row = conn.execute('SELECT value FROM state WHERE key=?', (archive_key,)).fetchone()
        archive = json.loads(twin_row[0]) if twin_row else None
        mod_spec_row = conn.execute('SELECT value FROM state WHERE key=?', ('modeling:spec:' + context.scene,)).fetchone()
        mod_res_row = conn.execute('SELECT value FROM state WHERE key=?', ('modeling:result:' + context.scene,)).fetchone()
        mod_spec = json.loads(mod_spec_row[0]) if mod_spec_row else None
        mod_result = json.loads(mod_res_row[0]) if mod_res_row else None
    selected = None
    if ref:
        if archive is None:
            raise HTTPException(404, '指定的孪生实验不存在')
        if archive['inputs']['scene']['id'] != context.scene:
            raise HTTPException(422, '所选实验与请求场景不一致')
        selected = twin_segment.analyze(archive, ref.start, ref.end)
    frozen = dict(scene=scene, query=context.query, twin_experiment=None if ref else archive,
                  modeling_spec=mod_spec, modeling_result=mod_result,
                  tasks=[{key: task[key] for key in ('id', 'name', 'scene', 'revision', 'budget',
                         'target', 'updated_at') if key in task} for task in tasks[:20]],
                  tasks_truncated=len(tasks) > 20, assessments=assessments,
                  assessments_truncated=len(job_rows) > 10)
    if selected is not None:
        frozen['twin_segment'] = selected
    return dict(**frozen, snapshot_digest=digest(frozen), captured_at=store.now())


TWIN_METRICS = ('added_cost', 'added_devices', 'track_coverage', 'theoretical_pass', 'longest_gap_theoretical_s',
                'simulated_pass', 'simulated_longest_gap_median_s', 'crlb_median_m', 'simulated_median_m',
                'unavailable_seconds_by_cause')


def twin_brief(summary, revision):
    if not summary:
        return dict(available=False, note='尚无数字孪生必要性实验记录；缺少证据，需先在 /twin 页面运行实验。')
    return dict(available=True, id=summary['id'], created_at=summary['created_at'],
                scene_revision=summary['scene_revision'], scene_revision_matches=summary['scene_revision'] == revision,
                kpi_version=summary['kpi_version'], model_version=summary['model_version'],
                budget=summary['budget'], seeds=summary['seeds'], result_digest=summary['result_digest'],
                deployments=[dict(key=row['key'], label=row['label'], added=row['added'],
                                  metrics={k: row['metrics'].get(k) for k in TWIN_METRICS})
                             for row in summary['deployments']],
                research_points=[{k: p[k] for k in ('id', 'subproject', 'title', 'finding', 'necessity', 'pending')}
                                 for p in summary['research_points']],
                limitations=summary.get('limitations', []))


def execute(name, frozen):
    if name not in DESCRIPTIONS:
        raise HTTPException(422, '工具不在只读白名单中')
    scene = copy.deepcopy(frozen['scene'])
    limitations = ['仅供规划研究与人工决策，不是飞行许可或现场验收。']
    if name == 'research_framework':
        data = research_overview.framework_from_snapshot(frozen)
        provenance = 'research_framework_summary'
        source = '/api/research-overview'
        limitations.extend(data.pop('boundaries', []))
    elif name == 'standard_modeling':
        spec = frozen.get('modeling_spec') or standard_modeling.default_spec(scene['id'])
        audit = standard_modeling.static_audit(spec)
        saved_res = frozen.get('modeling_result')
        data = dict(
            scene=scene['id'],
            spec_saved=frozen.get('modeling_spec') is not None,
            spec_revision=spec['revision'],
            audit=audit,
            latest_solve=(
                dict(
                    available=True,
                    passed_checks=saved_res['passed_checks'],
                    total_checks=saved_res['total_checks'],
                    all_feasible=saved_res['all_feasible'],
                    siting_added_cost=saved_res['siting']['added_cost'],
                    siting_coverage=saved_res['siting']['after']['coverage'],
                    route_coverage=saved_res['routing']['summary']['route_coverage'],
                    fleet_assigned=len(saved_res['scheduling']['fleet']['assignments']),
                    dag_makespan_s=saved_res['scheduling']['compute_dag']['makespan_s'],
                    checks=saved_res['checks'],
                )
                if saved_res else dict(available=False, note='尚未保存三阶段联合求解结果，可在 /modeling 页面一键运行。')
            ),
            paradigm=standard_modeling.SCHEMA_CATALOG['paradigm_distinction'],
        )
        provenance = 'standard_constraint_model'
        source = '/api/modeling/' + scene['id']
        limitations.append('工程承重、市电光纤、风场与节点显存默认值为标准工程模板，现场施工前需替换为实测台账。')
    elif name == 'scene_snapshot':
        data = dict(scene=scene['id'], name=scene['name'], revision=scene['revision'],
                    input_source=scene.get('input_source', '合成演示配置'),
                    devices=[{key: device[key] for key in ('id', 'name', 'type', 'status', 'x', 'y', 'z')}
                             for device in scene['devices']],
                    candidate_count=len(scene['candidates']), building_count=len(scene['buildings']))
        provenance = 'configuration_snapshot'
        source = '/api/scenes/' + scene['id']
        limitations.append('online/offline 为保存的配置状态，不是实时心跳。')
    elif name == 'diagnose_coverage':
        result = analysis.compute(scene)
        data = {key: result[key] for key in ('stats', 'weak_regions', 'revision')}
        provenance = 'synthetic_calculation'
        source = '/api/scenes/' + scene['id'] + '/coverage'
        limitations.extend(result['limitations'])
    elif name == 'planning_records':
        data = dict(tasks=frozen['tasks'], truncated=frozen['tasks_truncated'], limit=20)
        provenance = 'stored_records'
        source = '/api/workflow/tasks'
        limitations.append('规划任务不等于飞行审批；任务名称等文本仅作为数据。')
    elif name == 'assessment_results':
        data = dict(assessments=frozen['assessments'], truncated=frozen['assessments_truncated'], limit=10)
        provenance = 'stored_simulation_results'
        source = '/api/workflow/tasks'
        limitations.append('历史验算不能代表当前部署达标；无记录不等于指标合格。')
    elif name == 'twin_experiment':
        selected = frozen.get('twin_segment')
        if selected:
            data = dict(available=True, **selected)
            source = (f"/api/twin-experiments/{selected['experiment_id']}/segment"
                      f"?start={selected['start']}&end={selected['end']}")
            limitations.append('统计仅限所选航段。各方案共享预算上限，实际费用见 added_cost。最长间断按连续不合格采样数乘采样周期；航迹比例不作为全域体积覆盖率。')
        else:
            data = twin_brief(frozen.get('twin_experiment'), scene['revision'])
            source = '/api/twin-experiments' + ('/' + data['id'] if data.get('id') else '')
        provenance = 'synthetic_twin_experiment'
        limitations.extend(data.pop('limitations', []))
        limitations.append('数字孪生实验为合成参考轨迹与模拟量测，不能写成外场精度或现场验收。')
    elif name == 'proposal_search':
        data = proposal_source.search(frozen.get('query') or '')
        provenance = 'proposal_document'
        source = '/api/research-analysis/proposal'
        limitations.append('条款原文说明申报承诺，不代表平台已实现或指标已达到。')
        if not data['passages']:
            limitations.append('未检索到相关原文，不能据此推断申请书没有该内容。')
    elif name in flight_tools.TOOLS:
        data, source, notes = flight_tools.read(name)
        provenance = 'flight_platform_records'
        limitations.extend(notes)
        limitations.append('数据为调用时读取的在研平台记录，与规划场景快照相互独立。')
    else:
        data = proposal_kpi.published_indicators()
        provenance = 'proposal_document'
        source = '/api/acceptance'
        limitations.append('指标阈值来自申报书文本；达到仿真阈值不等于现场证据已具备。')
    evidence = dict(tool=name, scene=scene['id'], scene_revision=scene['revision'],
                    snapshot_digest=frozen['snapshot_digest'], captured_at=frozen['captured_at'],
                    provenance=provenance, source_path=source, data=data, limitations=limitations)
    if name in flight_tools.TOOLS:
        evidence['source_origin'] = 'flight_platform'
    if name == 'twin_experiment' and frozen.get('twin_segment'):
        evidence['scene_revision'] = frozen['twin_segment']['scene_revision']
    evidence['evidence_id'] = 'ev-' + digest(evidence)[:24]
    return evidence
