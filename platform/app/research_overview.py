"""研究问题、研究内容关系、重要性、必要性与四维可行性总览接口（只读聚合）。

汇总正式申请书（end-1124）结构锚点与平台内冻结/归档的可复算证据，明确区分：
1. 申报书目标承诺（原文页码锚点）；
2. 平台已实现的模型、规划、三维数字孪生与跨平台仿真支撑（合成/仿真证据）；
3. 立项后待完成的真实异构设备接入、外场实飞标定与第三方检验边界。
"""
import hashlib
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from . import analysis, coverage_core as core, proposal_kpi, proposal_source, store
from .scene_service import scene_for
from .scenes import SCENES, TYPES

router = APIRouter()

SCENE_IDS = tuple(item['id'] for item in SCENES)

IMPORTANCE_PILLARS = [
    dict(
        id='safety_baseline',
        badge='安全底线',
        title='低空经济“管得住才能放得开”的态势感知前提',
        summary='低空物流配送、城市安防巡检与应急作业高频次常态化运行，要求对合作与非合作低空目标实现连续探测、稳定跟踪与精准定位；遮挡盲区与跨站丢轨将直接构成运行安全隐患。',
        evidence_anchor='PDF 第 5–9、19 页',
    ),
    dict(
        id='xiongan_environment',
        badge='环境挑战',
        title='高密度新建楼宇遮挡与 5G 共址复杂电磁耦合',
        summary='新区重点片区高层建筑密集、空间结构立体，导致低空视距频繁受阻；同时 5G 通信基站密集共址引发同频/邻频与回传链路干扰，传统二维自由空间圆盘覆盖模型严重高估真实感知能力。',
        evidence_anchor='PDF 第 9–14、20–21 页',
    ),
    dict(
        id='intensive_reuse',
        badge='集约建设',
        title='“通信+感知”基础设施集约复用与按需降本增效',
        summary='雷达、光电、红外、无线电侦测与报文设备能力互补但造价差异显著。充分复用既有通信铁塔、楼宇天面及供电回传资源，按任务风险与预算约束联合优化站址、机型、挂高与朝向，可避免盲目重复建站。',
        evidence_anchor='PDF 第 15–18、21、31 页',
    ),
]

SCIENTIFIC_QUESTIONS = [
    dict(
        id='Q1',
        title='复杂三维建筑遮挡下多模态异构感知空间的统一表征与融合机理',
        problem='雷达、光电、无线电等异构传感器物理机理、视场形态与探测维度各异，受三维建筑群遮挡后呈现高度非均匀空域盲区，如何建立多尺度三维时空网格下的统一发现概率融合模型？',
        subprojects=[1],
        challenges=['1.1 异构设备能力口径不一致', '1.2 三维遮挡计算与网格尺度矛盾', '1.3 独立探测假设在共视走廊失真'],
        pdf_pages=proposal_source.TASK_PAGES[1],
        platform_entry='/research/1',
    ),
    dict(
        id='Q2',
        title='多源共址电磁干扰与观测几何耦合下的多站协同精度标量场刻画',
        problem='有覆盖不等于可定位：单站仅能提供测角或距离约束，共址同频与通信回传干扰会抬升量测噪声，如何联合刻画信干噪比衰减与多站几何构型（FIM/CRLB）对三维定位精度的耦合影响？',
        subprojects=[2],
        challenges=['2.1 同频与共址通信干扰耦合', '2.2 单站不可解算与几何退化（GDOP）', '2.3 理论误差界与实际估计误差偏离'],
        pdf_pages=proposal_source.TASK_PAGES[2],
        platform_entry='/research/2',
    ),
    dict(
        id='Q3',
        title='预算、站址工程容量与覆盖—精度双约束下的按需选址与多参数协同规划',
        problem='站址挂高、槽位、供电、回传与专项经费均受严格约束，如何在组合爆炸空间内协同决策站址初筛、异构机型配置、挂高与朝向，兼顾广域覆盖、重点区加密与多站协同定位合格率？',
        subprojects=[3],
        challenges=['3.1 多目标与离散工程约束耦合', '3.2 仅按覆盖贪心导致低空定位缺口', '3.3 大规模细网格验算与搜索效率折中'],
        pdf_pages=proposal_source.TASK_PAGES[3],
        platform_entry='/research/3',
    ),
    dict(
        id='Q4',
        title='模型失配条件下的运行数据动态修正、云边端协同与渐进示范闭环机制',
        problem='静态规划模型不可避免存在环境与硬件参数偏差，如何利用运行回放残差与数字孪生档案驱动参数标定、滚动补盲，并在三类典型场景中形成可复核、可推广的闭环验证体系？',
        subprojects=[4, 5],
        challenges=['4.1 多源快照版本一致性与可复算性', '4.2 偏差校正过拟合与独立留出检验', '5.1–5.3 三场景控制变量对照与外场真值对齐'],
        pdf_pages=sorted(set(proposal_source.TASK_PAGES[4] + proposal_source.TASK_PAGES[5])),
        platform_entry='/twin',
    ),
]

SUBPROJECT_LINKS = {
    1: dict(role='基础底座（覆盖场）', entry='/diagnosis', twin_counterfactual='ignore_occlusion',
            deliverables=['10 m / 2 m 多尺度三维时空网格', '三维建筑射线遮挡与多模态融合 Pd 场']),
    2: dict(role='机理核心（干扰与精度场）', entry='/twin', twin_counterfactual='ignore_interference',
            deliverables=['同频/通信/天气三类衰减耦合模型', 'Pd / SINR / FIM-CRLB 三类感知效能标量场']),
    3: dict(role='决策引擎（按需约束规划）', entry='/acceptance', twin_counterfactual='coverage_only',
            deliverables=['站址—机型—挂高—朝向 4 类参数联合搜索', '预算与供电/回传/槽位工程约束方案集']),
    4: dict(role='系统载体（平台与反馈闭环）', entry='/tasks', twin_counterfactual='method',
            deliverables=['任务—资源—诊断—规划—方案—验算全流程平台', 'SHA-256 冻结可复算档案、云边端协同与残差校正']),
    5: dict(role='应用出口（三场景闭环示范）', entry='/demonstration', twin_counterfactual='method',
            deliverables=['高密度建筑区、物流主干道、复杂电磁区三场景验证', '“典型场景组网验证—重点片区集约规划—新区场景拓展应用”路径']),
}

RELATIONS = [
    dict(source=1, target=2, kind='forward', label='三维可视性与单设备探测概率 Pd',
         detail='子项目一输出各传感器在三维体素上的遮挡可见性与探测概率，决定子项目二中哪些基站能提供有效观测信息矩阵。'),
    dict(source=1, target=3, kind='forward', label='广域/重点区覆盖盲区分布',
         detail='子项目一识别出的三维遮挡阴影与低空漏检区，直接作为子项目三候选站址初筛与补盲加密的空间输入。'),
    dict(source=2, target=3, kind='forward', label='干扰衰减与多站 CRLB 精度约束',
         detail='子项目二输出的 SINR 退化与多站协同定位误差界，构成子项目三区分“单站仅覆盖”与“多站可精准定位”的核心约束。'),
    dict(source=3, target=4, kind='forward', label='约束优化部署方案与设备参数表',
         detail='子项目三生成的站址、机型、挂高、朝向及成本清单进入子项目四管控平台，冻结为可复算、可交接的数字孪生方案档案。'),
    dict(source=4, target=5, kind='forward', label='三维数字孪生底座与试验评价流水线',
         detail='子项目四为子项目五提供统一的三场景试验模板、航段回放、同步容差匹配与残差统计工具链。'),
    dict(source=5, target=4, kind='feedback', label='示范运行残差反哺标定与滚动重规划',
         detail='子项目五在三类场景中积累的观测偏差、缺测区段与跨站中断统计回流至子项目四，触发离线/在线参数修正与下一轮补点优化。'),
]

PLATFORMS = [
    dict(
        id='planning',
        name='多模态感知网络智能规划平台',
        role='本申报书核心研发平台',
        entry='/network/',
        sub_entries=[
            dict(label='研究总览与可行性', path='/network/overview'),
            dict(label='三维实景数字孪生中心', path='/network/twin'),
            dict(label='细网格指标验证', path='/network/acceptance'),
            dict(label='五步规划任务流', path='/network/tasks'),
            dict(label='三场景应用示范', path='/network/demonstration'),
            dict(label='五项研究问题拆解', path='/network/research/1'),
        ],
        capabilities=[
            '广域 10 m / 重点区 2 m 多尺度三维空域网格（单场景全空域 13,132,800 体素）与三维射线遮挡计算',
            '软件定义 RADAR / EO / RF / IR / ADSB 共 5 类异构感知设备与站址供电、回传、槽位、挂高工程约束',
            '统一 Three.js 实景三维数字孪生中心：融合卫星正射影像、雄安/OSM 真实建筑轮廓遮挡计算、三维 CRLB 误差椭球与四类应用模式切换',
            '正式申请书（end-1124）考核指标逐项对照、历史稿透明留痕与可复算 SHA-256 证据包导出',
        ],
        boundary='当前计算与数字孪生基于合成场景与物理参数化模型；真实传感器协议联调与外场实测标定待立项后接入。',
    ),
    dict(
        id='station',
        name='区域地面站与云边端航路平台',
        role='飞行执飞、航路算法与云边端算力协同支撑平台',
        entry='/station',
        sub_entries=[
            dict(label='区域地面站主控台', path='/station'),
            dict(label='统一多平台工作台', path='/'),
        ],
        capabilities=[
            '三维避障航路规划算法对照（OMPL BIT*、ACS-DS、A*）与安全电子围栏诊断',
            '机载—边缘—云端三层算力网络建模与 5 任务 DAG 调度策略对比（HEFT 对比本地/边缘/云端）',
            'ArduPilot SITL 闭环仿真飞行、外部 MAVLink 只读数字孪生监视及感知规划归档航段同步回放',
            '通过只读桥接接口将仿真飞行轨迹关联至感知规划验算方案，支撑子项目四“云边端协同与航路推演”验证',
        ],
        boundary='提供无人机侧轨迹真值与云边端调度仿真支撑；不直接替代地面多模态感知设备的独立量测。',
    ),
    dict(
        id='service',
        name='低空综合服务管理平台',
        role='低空业务任务申报、审批与运行态势协同平台',
        entry='/service/',
        sub_entries=[
            dict(label='低空服务任务门户', path='/service/'),
        ],
        capabilities=[
            '物流配送、安防巡检等低空飞行任务创建、航线空间校验、审批流转与执行状态跟踪',
            '与区域地面站共享任务执行状态，为感知网络规划提供面向真实业务流量的重点保障空域需求输入',
        ],
        boundary='承载业务任务流转闭环演示，正式空域监管审批需对接法定军民航与地方低空管理平台。',
    ),
    dict(
        id='agent',
        name='只读证据驱动研究与诊断智能体',
        role='跨平台证据核查、原文溯源与研究辅助智能体',
        entry='/network/agents',
        sub_entries=[
            dict(label='研究智能体对话台', path='/network/agents'),
            dict(label='运行审计与管理台', path='/network/agents/manage'),
        ],
        capabilities=[
            '基于单事务冻结快照调用 12 项只读白名单工具（研究体系总览、数字孪生对照、细网格验算、申请书 PDF 原文检索、跨平台运行记录）',
            '支持“分析员 + 同模型证据复核员”双阶段工作流，强制校验 ev-... 证据摘要引用并自动附带工具适用边界',
            '内嵌于统一工作台底部 Chat Dock 与各业务子页面，支持绑定特定孪生航段做定量归因问答',
        ],
        boundary='严格只读隔离：不修改场景配置、不审批飞行任务、不下发任何无人机或传感器控制指令。',
    ),
]

LANDING_STAGES = [
    dict(
        stage='第一阶段',
        name='典型场景组网验证',
        focus='模型标定与单场景闭环跑通',
        detail='依托牵头单位自有园区及不少于 500 m² 雄安研发测试场地，围绕高密度建筑区、物流主干道、复杂电磁区开展多模态设备单站标定、遮挡/干扰误差核验与数字孪生基线对齐。',
    ),
    dict(
        stage='第二阶段',
        name='重点片区集约规划',
        focus='“通信+感知”铁塔天面复用降本',
        detail='面向雄安重点建设片区，盘点既有通信铁塔、楼宇天面、杆塔供电与回传容量，运行成本—覆盖—精度双约束规划算法，输出分批补盲与异构协同部署方案。',
    ),
    dict(
        stage='第三阶段',
        name='新区场景拓展应用',
        focus='滚动修正与多平台常态化协同',
        detail='打通感知网络规划平台、区域地面站与低空服务管理平台运行数据链，利用日常巡检与物流飞行数据持续修正环境参数，向临水空域与更大范围片区渐进推广。',
    ),
]

BOUNDARIES = [
    '严格区分“合成/仿真验证”与“外场实测验收”：平台内全部覆盖率、CRLB 理论精度界及成对蒙特卡洛定位误差均基于参数化物理模型与合成场景计算，不宣称为外场实测精度或第三方检测结论。',
    '真实硬件接入边界：平台已在数据模型与规划内核中支持 RADAR / EO / RF / IR / ADSB 5 类异构设备及 4 类决策参数，当前真实物理传感器协议接入数为 0 类，待立项后结合外场组网完成联调。',
    '考核指标版本透明：统一按正式申请书（end-1124 / proposal-2026-11-v2：广域 ≤10 m、重点区 ≤2 m，覆盖 ≥90% / ≥95%）判定；早期草稿（30 m / 10 m，80% / 85%）仅保留作历史演进对照，不参与达标判定。',
    '精度缺口如实披露：在 50 万元预算与高楼遮挡约束下，广域覆盖（>92%）与重点区覆盖（>98%）可满足申报书仿真阈值，但全域 30 m 理论定位精度合格率约 68%–69%（受低空楼宇遮挡导致单站观测几何退化制约），平台如实展示该瓶颈及低位补站改进路径。',
]


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _subprojects():
    raw_tasks = json.loads((store.ROOT / 'static' / 'research-tasks.json').read_text())
    items = []
    for task in raw_tasks:
        tid = task['id']
        meta = SUBPROJECT_LINKS[tid]
        pages = proposal_source.TASK_PAGES[tid]
        upstream = sorted({r['source'] for r in RELATIONS if r['target'] == tid and r['kind'] == 'forward'})
        downstream = sorted({r['target'] for r in RELATIONS if r['source'] == tid and r['kind'] == 'forward'})
        items.append(dict(
            id=tid,
            code=f'子项目{["一", "二", "三", "四", "五"][tid - 1]}',
            short=task['short'],
            title=task['title'],
            role=meta['role'],
            question=task['question'],
            summary=task['summary'],
            input=task['input'],
            output=task['output'],
            deliverables=meta['deliverables'],
            challenge_count=len(task.get('challenges', [])),
            challenges=[c['name'] for c in task.get('challenges', [])],
            upstream=upstream,
            downstream=downstream,
            entry=meta['entry'],
            research_entry=f'/research/{tid}',
            twin_counterfactual=meta['twin_counterfactual'],
            pdf_pages=pages,
            pdf_page_label=proposal_source.page_label(pages),
        ))
    return items


def _read_records():
    with store.connection() as conn:
        conn.execute('BEGIN')
        task_count = conn.execute("SELECT COUNT(*) FROM state WHERE key LIKE 'portal:task:%'").fetchone()[0]
        plan_count = conn.execute("SELECT COUNT(*) FROM state WHERE key LIKE 'portal:plan:%'").fetchone()[0]
        trial_count = conn.execute("SELECT COUNT(*) FROM state WHERE key LIKE 'portal:trial:%'").fetchone()[0]
        run_count = conn.execute("SELECT COUNT(*) FROM state WHERE key LIKE 'portal:run:%'").fetchone()[0]
        flight_ev_count = conn.execute("SELECT COUNT(*) FROM state WHERE key LIKE 'portal:flight-evidence:%'").fetchone()[0]
        twin_index_row = conn.execute("SELECT value FROM state WHERE key='twin-experiment-index'").fetchone()
        twin_index = json.loads(twin_index_row[0]) if twin_index_row else []
        twins = {}
        for sid in SCENE_IDS:
            row = conn.execute('SELECT value FROM state WHERE key=?', ('twin-experiment-latest:' + sid,)).fetchone()
            if row:
                twins[sid] = json.loads(row[0])
        job_rows = conn.execute(
            "SELECT id, scene, status, created_at, snapshot, result FROM computation_jobs "
            "WHERE kind='assessment' AND status='succeeded' ORDER BY created_at DESC, id DESC"
        ).fetchall()
    latest_assessments = {}
    for row in job_rows:
        sid = row['scene']
        if sid in latest_assessments or not row['result']:
            continue
        result = json.loads(row['result'])
        snapshot = json.loads(row['snapshot'])
        stats = result.get('stats') or {}
        checks = result.get('checks') or {}
        latest_assessments[sid] = dict(
            job_id=row['id'],
            created_at=row['created_at'],
            task_name=snapshot.get('task', {}).get('name', ''),
            budget=snapshot.get('task', {}).get('budget'),
            added_cost=result.get('added_cost'),
            evaluated_cells=result.get('evaluated_cells'),
            wide_cell=result.get('settings', {}).get('wide_cell'),
            focus_cell=result.get('settings', {}).get('focus_cell'),
            coverage=stats.get('coverage'),
            focus_coverage=stats.get('focus_coverage'),
            precision_pass_rate=stats.get('precision_pass_rate'),
            simulation_kpi_pass=bool(checks.get('resolution') and checks.get('coverage') and checks.get('focus_coverage') and checks.get('modalities_3')),
            feasible=result.get('feasible'),
            kpi_version=result.get('kpi_version'),
        )
    return dict(
        counts=dict(tasks=task_count, plans=plan_count, trials=trial_count,
                    demo_runs=run_count, flight_evidence=flight_ev_count,
                    twin_experiments=len(twin_index), succeeded_assessments=len(job_rows)),
        twins=twins,
        assessments=latest_assessments,
    )


def _scene_Twin_necessity(sid, twin_summary):
    scene_meta = next(item for item in SCENES if item['id'] == sid)
    if not twin_summary:
        return dict(
            scene=sid,
            scene_name=scene_meta['name'],
            available=False,
            note=f'尚未归档「{scene_meta["name"]}」数字孪生实验；可在 /twin 切换至该场景一键生成五方案对照。',
        )
    by_key = {row['key']: row['metrics'] for row in twin_summary.get('deployments', [])}
    base = by_key.get('baseline', {})
    method = by_key.get('method', {})
    no_occ = by_key.get('ignore_occlusion', {})
    no_int = by_key.get('ignore_interference', {})
    cov_only = by_key.get('coverage_only')
    def delta_pp(other):
        if not other or method.get('theoretical_pass') is None or other.get('theoretical_pass') is None:
            return None
        return round((method['theoretical_pass'] - other['theoretical_pass']) * 100, 1)
    return dict(
        scene=sid,
        scene_name=scene_meta['name'],
        available=True,
        experiment_id=twin_summary['id'],
        created_at=twin_summary['created_at'],
        budget=twin_summary['budget'],
        seeds=twin_summary['seeds'],
        result_digest=twin_summary['result_digest'],
        baseline_pass=base.get('theoretical_pass'),
        method_pass=method.get('theoretical_pass'),
        method_added_cost=method.get('added_cost'),
        method_longest_gap_s=method.get('longest_gap_theoretical_s'),
        baseline_longest_gap_s=base.get('longest_gap_theoretical_s'),
        gain_vs_baseline_pp=delta_pp(base),
        gain_vs_ignore_occlusion_pp=delta_pp(no_occ),
        gain_vs_ignore_interference_pp=delta_pp(no_int),
        gain_vs_coverage_only_pp=delta_pp(cov_only) if cov_only else None,
    )


def build_overview(scene: str | None = None):
    if scene is not None and scene not in SCENE_IDS:
        raise HTTPException(422, '不支持的场景编号')
    stored = _read_records()
    target_scenes = (scene,) if scene else SCENE_IDS

    baseline_snapshots = []
    for sid in target_scenes:
        sc = scene_for(sid)
        cov = analysis.compute(sc)
        baseline_snapshots.append(dict(
            scene=sid,
            name=sc['name'],
            purpose=sc['purpose'],
            revision=sc['revision'],
            device_count=len(sc['devices']),
            candidate_count=len(sc['candidates']),
            building_count=len(sc['buildings']),
            coarse_50m_coverage=cov['stats']['coverage'],
            coarse_50m_multi_station=cov['stats'].get('multi_coverage') or cov['stats'].get('overlap'),
            latest_assessment=stored['assessments'].get(sid),
            latest_twin=_scene_Twin_necessity(sid, stored['twins'].get(sid)),
        ))

    necessity_matrix = [
        dict(
            id='N1',
            subproject='子项目一（三维遮挡与多模态覆盖）',
            counterfactual='反事实：规划时忽略建筑遮挡（自由空间假设）',
            consequence='选址偏向开阔高增益方向，放回真实含楼宇环境后在建筑背阴走廊出现连续漏检盲点。',
            verification_path='/twin',
        ),
        dict(
            id='N2',
            subproject='子项目二（共址干扰与多站协同精度）',
            counterfactual='反事实：规划时忽略同频与通信干扰衰减',
            consequence='高估受扰站址有效探测半径与信干噪比，导致复杂电磁区边缘站探测概率跌破门限、定位几何退化。',
            verification_path='/twin?scene=electromagnetic',
        ),
        dict(
            id='N3',
            subproject='子项目三（成本—覆盖—精度按需规划）',
            counterfactual='反事实：同预算仅按覆盖贪心选址（忽略协同定位精度）',
            consequence='大量预算消耗在仅能提供单站覆盖的站址上，“看得见但定不准（单站不可解算或 CRLB 超限）”。',
            verification_path='/acceptance',
        ),
        dict(
            id='N4',
            subproject='子项目四与五（可复算数字孪生与场景反馈闭环）',
            counterfactual='反事实：无输入版本冻结与实测残差反馈',
            consequence='不同任务版本、网格尺度与考核口径混杂，无法复核改进归因，也无法将静态仿真模型平滑迁移到雄安外场。',
            verification_path='/demonstration',
        ),
    ]

    kpi = proposal_kpi.CURRENT
    feasibility = [
        dict(
            id='F1',
            dimension='理论与物理建模可行性',
            status='kernel_verified',
            status_label='核心算子已实现并可复算',
            summary=(f'已统一建模 {", ".join(TYPES.keys())} 共 {len(TYPES)} 类异构设备、三维建筑包络射线遮挡、'
                     f'同频/通信/天气 {kpi["interference_factors"]} 类衰减因素，以及 Pd / SINR / FIM-CRLB '
                     f'{kpi["efficacy_metrics"]} 类标量场（模型版本 {core.MODEL_VERSION}）。'),
            metrics=[
                f'多尺度网格：广域 ≤{kpi["wide_cell_m"]} m / 重点区 ≤{kpi["focus_cell_m"]} m（单场景完整任务空域可解算约 1,313 万体素）',
                f'支持模态：{len(TYPES)} 类软件定义异构设备（申报指标要求 ≥{kpi["platform_devices"]} 类）',
                f'决策变量：站址、机型、挂高、朝向 {kpi["planning_parameters"]} 类参数联合配置',
            ],
            evidence_link='/acceptance',
        ),
        dict(
            id='F2',
            dimension='规划算法与预算效能可行性',
            status='simulation_verified_with_gap_disclosed',
            status_label='仿真覆盖达标 · 低空精度瓶颈透明可查',
            summary='采用“单位成本增益 + 绝对增益双贪心初筛 + 删一加二局部搜索 + 冗余剔除”组合优化，并与小规模精确穷举复核最优差距；在三类场景细网格验算中均可验证预算约束下的覆盖提升与瓶颈定位。',
            metrics=[
                f'已保存细网格成功验算：{stored["counts"]["succeeded_assessments"]} 份；归档数字孪生实验：{stored["counts"]["twin_experiments"]} 份',
                f'申报书仿真覆盖门限：广域 ≥{kpi["coverage"]:.0%}、重点区 ≥{kpi["focus_coverage"]:.0%}（版本 {kpi["version"]}）',
                '科学诚信披露：高楼低空层受单站遮挡制约，全域 30 m 理论定位合格率与任务目标（80%）的差距及低位补站改进路径在验算页完整呈现',
            ],
            evidence_link='/acceptance',
        ),
        dict(
            id='F3',
            dimension='多平台协同与智能体工程可行性',
            status='platform_integrated',
            status_label='三平台 + 只读证据智能体已贯通',
            summary='已建成“感知网络智能规划平台（核心）+ 区域地面站与云边端航路平台（支撑）+ 低空综合服务管理平台（业务）+ 只读研究智能体”一体化软件底座，具备完整的软件工程与自动化测试保障。',
            metrics=[
                f'当前库内业务档案：规划任务 {stored["counts"]["tasks"]} 项、规划方案 {stored["counts"]["plans"]} 份、示范试验 {stored["counts"]["trials"]} 项（运行 {stored["counts"]["demo_runs"]} 次）',
                '跨平台闭环：支持数字孪生异常航段一键跳转地面站只读回放与底部智能体定量溯源',
                '智能体安全边界：12 项只读白名单工具 + SHA-256 快照摘要校验，杜绝越权控制与虚构引用',
            ],
            evidence_link='/network/agents',
        ),
        dict(
            id='F4',
            dimension='雄安渐进落地与团队条件可行性',
            status='implementation_planned',
            status_label='三阶段路径与场地机制明确',
            summary='遵循正式申请书“典型场景组网验证—重点片区集约规划—新区场景拓展应用”三阶段演进路线，依托自有园区及不少于 500 m² 雄安研发测试场地与京沪雄五单位常态化派驻联合攻关机制推进。',
            metrics=[
                '通信+感知集约复用：优先利用既有通信铁塔、楼宇天面及现成电力/回传条件降低外场部署门槛',
                '数据闭环准备：平台已内置标准化实测观测/RTK真值导入模板、同步容差匹配与留出集偏差校准流水线',
                '待立项补齐项：真实异构传感器硬件采购/联调、外场合作无人机实飞采集与第三方权威测试报告',
            ],
            evidence_link='/demonstration',
        ),
    ]

    proposal_pdf = proposal_source.index()
    payload = dict(
        generated_at=store.now(),
        evidence_status='platform_records_and_synthetic_models',
        project=dict(
            title='面向低空经济的多模态感知网络智能规划',
            kpi_version=proposal_kpi.VERSION,
            model_version=core.MODEL_VERSION,
            proposal_version=proposal_source.VERSION,
            proposal_file=proposal_source.FILE_NAME,
            proposal_pdf_available=proposal_pdf is not None,
            proposal_pdf_pages=proposal_pdf['pages'] if proposal_pdf else None,
            thesis='围绕雄安新区低空高密度建筑遮挡、5G 共址复杂电磁干扰与建设成本约束，打通“三维覆盖建模 → 干扰与多站协同精度 → 多目标按需规划 → 数字孪生与运行反馈平台 → 三场景渐进示范”闭环体系。',
            landing_stages=LANDING_STAGES,
        ),
        platforms=PLATFORMS,
        importance=IMPORTANCE_PILLARS,
        scientific_questions=SCIENTIFIC_QUESTIONS,
        subprojects=_subprojects(),
        relations=RELATIONS,
        necessity=dict(
            counterfactual_design='固定同一参考轨迹、同一含遮挡/干扰环境与同一组蒙特卡洛随机种子，仅替换规划阶段的假设模型，将所得方案放回完整参考环境成对比较。',
            matrix=necessity_matrix,
            scenes=baseline_snapshots,
        ),
        feasibility=feasibility,
        record_counts=stored['counts'],
        boundaries=BOUNDARIES,
    )
    payload['overview_digest'] = _digest(payload)
    return payload


def framework_from_snapshot(frozen):
    """Deterministic read-only research framework summary over an already-frozen agent snapshot."""
    scene = frozen['scene']
    sid = scene['id']
    cov = analysis.compute(scene)
    assessments = frozen.get('assessments') or []
    succeeded = next((item for item in assessments if item.get('status') == 'succeeded'), None)
    latest_assessment = None
    if succeeded:
        res = succeeded.get('result') or {}
        stats = res.get('stats') or {}
        checks = res.get('checks') or {}
        latest_assessment = dict(
            job_id=succeeded['id'],
            created_at=succeeded['created_at'],
            scene_revision=succeeded['scene_revision'],
            scene_revision_matches=succeeded['scene_revision_matches'],
            added_cost=res.get('added_cost'),
            evaluated_cells=res.get('evaluated_cells'),
            coverage=stats.get('coverage'),
            focus_coverage=stats.get('focus_coverage'),
            precision_pass_rate=stats.get('precision_pass_rate'),
            simulation_kpi_pass=bool(checks.get('resolution') and checks.get('coverage')
                                     and checks.get('focus_coverage') and checks.get('modalities_3')),
        )
    kpi = proposal_kpi.CURRENT
    return dict(
        project=dict(
            title='面向低空经济的多模态感知网络智能规划',
            kpi_version=proposal_kpi.VERSION,
            model_version=core.MODEL_VERSION,
            proposal_version=proposal_source.VERSION,
            landing_stages=LANDING_STAGES,
        ),
        platforms=[{k: p[k] for k in ('id', 'name', 'role', 'entry', 'boundary')} for p in PLATFORMS],
        importance=IMPORTANCE_PILLARS,
        scientific_questions=SCIENTIFIC_QUESTIONS,
        subprojects=_subprojects(),
        relations=RELATIONS,
        scene_evidence=dict(
            scene=sid,
            name=scene['name'],
            revision=scene['revision'],
            device_count=len(scene['devices']),
            candidate_count=len(scene['candidates']),
            building_count=len(scene['buildings']),
            coarse_50m_coverage=cov['stats']['coverage'],
            task_count_sampled=len(frozen.get('tasks') or []),
            latest_assessment=latest_assessment,
            latest_twin=_scene_Twin_necessity(sid, frozen.get('twin_experiment')),
        ),
        feasibility=[
            dict(id='F1', dimension='理论与物理建模可行性',
                 detail=f'{len(TYPES)} 类异构设备、三维建筑射线遮挡、{kpi["interference_factors"]} 类干扰衰减与 Pd/SINR/CRLB {kpi["efficacy_metrics"]} 类标量场（{core.MODEL_VERSION}）。'),
            dict(id='F2', dimension='规划算法与预算效能可行性',
                 detail=f'支持广域 ≤{kpi["wide_cell_m"]} m / 重点区 ≤{kpi["focus_cell_m"]} m 细网格验算与 4 类参数搜索，如实披露低空遮挡下的理论定位精度瓶颈。'),
            dict(id='F3', dimension='多平台协同与智能体工程可行性',
                 detail='感知网络规划平台（申报核心）、区域地面站（航路/云边端/SITL支撑）、低空服务管理平台与只读证据智能体协同运行。'),
            dict(id='F4', dimension='雄安渐进落地可行性',
                 detail='按“典型场景组网验证—重点片区集约规划—新区场景拓展应用”推进，复用既有通信铁塔与楼宇天面。'),
        ],
        boundaries=BOUNDARIES,
    )


@router.get('/overview')
def overview_page():
    return FileResponse(store.ROOT / 'static' / 'overview.html')


@router.get('/api/research-overview')
def research_overview(scene: str | None = Query(default=None)):
    return build_overview(scene)
