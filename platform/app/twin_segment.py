"""Read a closed sample interval from a frozen synthetic experiment."""
from collections import Counter

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .twin_experiment import longest_run

SCENE_SHORT = {'urban': '建筑区', 'corridor': '物流廊道', 'electromagnetic': '复杂电磁区'}


class SegmentRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    experiment_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=0, strict=True)


def deployment_slice(row, start, end, dt):
    samples = row['timeline'][start:end + 1]
    passed = [point['state'] == 'ok' for point in samples]
    segments = []
    causes = Counter()
    for index, point in enumerate(samples, start):
        state, cause = point['state'], point['cause']
        if state == 'ok':
            continue
        causes[state, cause] += 1
        if segments and segments[-1]['end'] == index - 1 and segments[-1]['cause'] == cause and segments[-1]['state'] == state:
            segments[-1]['end'] = index
        else:
            segments.append(dict(start=index, end=index, state=state, cause=cause))
    return dict(key=row['key'], label=row['label'], added_cost=row['metrics']['added_cost'],
                added_devices=row['metrics']['added_devices'], pass_count=sum(passed),
                theoretical_pass=sum(passed) / len(samples), blind_count=sum(p['state'] == 'blind' for p in samples),
                longest_gap_s=longest_run((not ok for ok in passed), dt), segments=segments,
                causes=[dict(state=s, cause=c, samples=n) for (s, c), n in causes.most_common()])


def analyze(record, start, end):
    inputs = record['inputs']
    scene_id = inputs['scene']['id']
    if record.get('evidence_level') != 'synthetic_twin' or scene_id not in SCENE_SHORT:
        raise HTTPException(422, '仅支持已归档的合成数字孪生实验')
    trajectory = inputs['trajectory']
    times = trajectory['times']
    if not 0 <= start <= end < len(times):
        raise HTTPException(422, '选段起止必须在这条归档航线内，且起点不能晚于终点')
    rows = [deployment_slice(row, start, end, trajectory['dt_s']) for row in record['result']['deployments']]
    method = next(row for row in rows if row['key'] == 'method')
    comparisons = [dict(key=row['key'],
                        pass_delta_pp=round(100 * (method['theoretical_pass'] - row['theoretical_pass']), 4),
                        gap_reduction_s=row['longest_gap_s'] - method['longest_gap_s'],
                        equal_cost=abs(method['added_cost'] - row['added_cost']) < 1e-8)
                   for row in rows if row['key'] != 'method']
    return dict(experiment_id=record['id'], scene=scene_id,
                scene_name=inputs['scene'].get('name', SCENE_SHORT[scene_id]),
                start=start, end=end, sample_count=end - start + 1,
                start_s=times[start], end_s=times[end], dt_s=trajectory['dt_s'],
                budget=inputs['budget'], cost_unit='万元', precision_m=inputs['environment']['precision_m'],
                deployments=rows, comparisons=comparisons, input_digest=record['input_digest'],
                result_digest=record['result_digest'], evidence_level=record['evidence_level'],
                model_version=inputs['model_version'], scene_revision=inputs['scene_revision'])


def report_facts(segment):
    """Render numerical claims from the selected archive, including ties and losses."""
    rows = {row['key']: row for row in segment['deployments']}
    baseline, method = rows['baseline'], rows['method']
    n = segment['sample_count']
    scene_label = SCENE_SHORT.get(segment.get('scene', 'urban'), '合成场景')
    conclusion = (f"{scene_label}合成航段 {segment['start_s']:g}–{segment['end_s']:g} 秒，"
                  f"现有部署有 {baseline['blind_count']} 个探测盲点。"
                  f"按 {segment['precision_m']:g} 米理论定位门槛，现有部署合格 {baseline['pass_count']}/{n} 点，"
                  f"完整规划合格 {method['pass_count']}/{n} 点。")
    evidence = [f"完整规划新增 {method['added_devices']} 台，模型估算费用 {method['added_cost']:g} 万元，"
                f"预算上限 {segment['budget']:g} 万元。"]
    if baseline['segments']:
        spans = [f"采样点 {part['start']}–{part['end']}（{part['cause']}）" for part in baseline['segments']]
        evidence.append('现有部署定位不合格的连续区间：' + '，'.join(spans) + '。')
    for comparison in segment['comparisons']:
        if comparison['key'] == 'baseline':
            continue
        other = rows[comparison['key']]
        delta, gap = comparison['pass_delta_pp'], comparison['gap_reduction_s']
        rate_text = '合格比例持平' if delta == 0 else f"合格比例{'提高' if delta > 0 else '降低'} {abs(delta):g} 个百分点"
        gap_text = '最长连续不合格时长持平' if gap == 0 else f"最长连续不合格时长{'缩短' if gap > 0 else '增加'} {abs(gap):g} 秒"
        evidence.append(f"对照为「{other['label']}」，估算费用 {other['added_cost']:g} 万元，"
                        f"合格 {other['pass_count']}/{n} 点。完整规划与之相比，{rate_text}，{gap_text}。")
    return conclusion, evidence
