"""Task-level methods, measured calculation outputs and outstanding validation work."""


def percentage(value):
    return '无有效样本' if value is None else f'{value:.2%}'


def task_validation(job):
    result = job['result']
    task = job['snapshot']['task']
    linked = job['evidence']
    stats = result['stats']
    low = result.get('low_altitude') or {}
    fractions = result['after'].get('bottleneck', {}).get('fractions', {})
    bands = result['after'].get('bottleneck', {}).get('altitude', [])
    low_band = next((band for band in bands if band['band'] == '30–100 m'), {})
    precision = f"全域理论精度合格 {percentage(stats.get('precision_pass_rate'))}，目标 {percentage(task['precision_rate_target'])}"
    low_result = f"30–100 m理论精度合格 {percentage(low.get('rate'))}"
    if low.get('target') is not None:
        low_result += f"，目标 {percentage(low['target'])}"
        if low.get('rate') is not None:
            low_result += f"，缺口 {max(0, low['target'] - low['rate']) * 100:.2f} 个百分点"
        else:
            low_result += '，缺口待有效样本判定'
    reasons = '、'.join(f"{name} {percentage(low_band.get(key))}" for key, name in
                       [('blind', '探测不足'), ('unobservable', '不可观测'), ('geometry', '理论误差超限')])
    definitions = [
        dict(id=1, name='三维覆盖',
             method='按广域与重点区分区网格逐体素计算遮挡和检出概率，以有效空域体积加权，核验完整区域与分辨率。',
             results=[f"遍历 {result['evaluated_cells']:,} 格，网格 {result['settings']['wide_cell']:g} / {result['settings']['focus_cell']:g} m",
                      f"全域覆盖 {percentage(stats.get('coverage'))}，重点区覆盖 {percentage(stats.get('focus_coverage'))}",
                      '参与计算的模态 ' + '、'.join(result.get('modalities', []))],
             next_tests=['采用测绘建筑与单设备标定数据复核遮挡、视场和检出概率。',
                         '在独立空间采样与更细网格上检查覆盖率收敛，记录偏差。']),
        dict(id=2, name='干扰与协同精度',
             method='在相同空域计算Pd、SINR和信息矩阵，将不合格体积分解为探测不足、不可观测和理论误差超限。',
             results=[precision, low_result, '低空缺口分解 ' + reasons,
                      '全域不可观测体积比例 ' + percentage(fractions.get('unobservable'))],
             next_tests=['逐次抽样随机漏检与共同偏差，使用实际定位估计器统计可定位率和RMSE。',
                         '独立变化干扰强度与观测几何，实测标定误差相关性，核对理论界与实际误差。']),
        dict(id=3, name='约束规划', method=result['method'],
             results=[f"新增建设费用 {result['added_cost']:.2f} / {task['budget']:g} 万元",
                      precision, low_result, '任务约束' + ('通过' if result['feasible'] else '存在缺口'),
                      result['optimality']],
             next_tests=['同预算、同设备数量比较站址和姿态调整，分别检验几何优化与模态组合收益。',
                         '核验站址挂高、供电和回传容量，并通过多起点与小规模枚举检查搜索差距。']),
        dict(id=4, name='平台运行',
             method='关联任务、场景、模型、方案版本和运行证据，检查结果可追溯性及真实设备执行回执。',
             results=[f"任务版本 {task['revision']}，场景版本 {job['snapshot']['scene']['revision']}",
                      f"模型 {result['model_version']}，输入摘要 {result['input_digest'][:12]}",
                      f"关联地面站参考航迹 {len(linked.get('flight_records', []))} 份"],
             next_tests=['完成真实协议接入、时钟配准、设备命令与回执的端到端联调。',
                         '注入断链、时钟偏移和设备故障，统计恢复时间、数据丢失及持续运行情况。']),
        dict(id=5, name='场景示范',
             method='冻结部署与工况，关联独立参考航迹，按场景、任务类型和重复航次评价实际误差与有效观测率。',
             results=[f"关联试验 {len(linked['trials'])} 项、运行记录 {len(linked['runs'])} 份",
                      f"现场标记记录 {linked.get('imported_field_runs', 0)} 份，原始证据待核验",
                      linked['field_status']],
             next_tests=['为三个场景逐项建立现场试验，覆盖五类示范任务并记录目标、航高与环境条件。',
                         '预先确定空间分层、最低样本量和重复航次，将缺测单元纳入未通过统计，提交独立评价报告。']),
    ]
    causes = {'blind': '探测不足', 'unobservable': '不可观测', 'geometry': '理论误差超限'}
    available = {key: low_band[key] for key in causes if low_band.get(key) is not None}
    if available:
        dominant = max(available, key=available.get)
        definitions[1]['next_tests'].insert(0, f'按本次模型分类，低空最大缺口为{causes[dominant]}，占有效体积{percentage(available[dominant])}，优先在对应网格开展观测与误差对照。')
    if low.get('target') is not None and low.get('rate') is not None:
        margin = (low['rate'] - low['target']) * 100
        definitions[2]['next_tests'].insert(0, f'低空合格比例高于目标{margin:.2f}个百分点，需检验参数扰动后的稳定性。' if margin >= 0 else f'保持已有覆盖约束，针对低空{-margin:.2f}个百分点缺口比较补盲与交叉观测方案。')
    for item in definitions:
        rows = [row for row in job['indicators'] if row['research'] == item['id']
                or (item['id'] == 2 and row['id'] in ('task-precision', 'task-low-precision'))]
        item['counts'] = {status: sum(row['status'] == status for row in rows)
                          for status in ('simulation_pass', 'simulation_fail', 'pending_evidence')}
        item['stale'] = job['stale']
        item['evidence_type'] = result.get('evidence_type', 'synthetic_model')
        item['pending_requirements'] = [row['requirement'] for row in rows if row['status'] == 'pending_evidence']
        item['failed_requirements'] = [row['requirement'] for row in rows if row['status'] == 'simulation_fail']
    return definitions
