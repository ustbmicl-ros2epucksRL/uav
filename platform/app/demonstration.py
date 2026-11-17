"""Demonstration evidence workflow. Imported ground truth is evaluation-only."""
import copy
import hashlib
import json
import math
import uuid
from bisect import bisect_left
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field, model_validator
from . import store
from .planning_portal import Strict, inputs, resources, read_record, records

router = APIRouter()
KINDS = {'anti_interference':'抗干扰验证','positioning':'定位与测速验证','tracking':'跨站连续跟踪','blind_fill':'建筑区补盲','recognition':'多模态融合识别'}
RESEARCH = [
    {'task':'一：三维覆盖建模','pages':'26','support':'场景与部署快照、既有覆盖/盲区计算、覆盖模型结果归档','boundary':'当前几何与概率基线需实测标定'},
    {'task':'二：干扰与多站协同','pages':'27','support':'干扰条件记录、分来源定位/测速残差、信号质量与模型版本追溯','boundary':'误差统计不等同于已验证干扰机理或协同增益'},
    {'task':'三：按需规划与多目标优化','pages':'28','support':'关联规划任务与方案快照、基准与改进试验对比','boundary':'既有有限候选寻优；大规模算法待研究接入'},
    {'task':'四：平台研发','pages':'28–29、40–44','support':'观测与真值分离接入、数据校验、三维回放、残差反馈、版本化算法结果','boundary':'云边端执行、硬件协议和在线模型修正仍需现场联调'},
    {'task':'五：示范应用','pages':'29–30、44–46','support':'三场景五类任务、飞行验证数据、同条件评价和可下载证据报告','boundary':'合成示例仅验证软件流程，现场成果以实测记录为准'},
]

def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def save(kind, data):
    store.put(f'portal:{kind}:{data["id"]}',data)
    return data

class Trial(Strict):
    name: str = Field(min_length=1,max_length=100)
    task_id: str = Field(min_length=1,max_length=40)
    plan_id: str | None = None
    kinds: list[Literal['anti_interference','positioning','tracking','blind_fill','recognition']] = Field(min_length=1,max_length=5)
    objective: str = Field(min_length=1,max_length=1500)
    conditions: str = Field(min_length=1,max_length=1500)
    coordinate_frame: str = Field(min_length=1,max_length=300)
    truth_source: str = Field(min_length=1,max_length=300)
    sync_tolerance_s: float = Field(default=.25,gt=0,le=5)
    max_gap_s: float = Field(default=2,gt=0,le=60)
    position_limit_m: float = Field(default=30,gt=0,le=1000)

class Truth(Strict):
    t: float = Field(ge=0,le=86400)
    target_id: str = Field(min_length=1,max_length=80)
    x: float = Field(ge=0,le=2000)
    y: float = Field(ge=0,le=2000)
    z: float = Field(ge=0,le=300)
    speed_mps: float | None = Field(default=None,ge=0,le=300)
    category: str | None = Field(default=None,max_length=80)

class Observation(Truth):
    # target_id denotes the external evaluation association, never a tracker input.
    source_id: str = Field(min_length=1,max_length=80)
    track_id: str = Field(min_length=1,max_length=80)
    device_ids: list[str] = Field(min_length=1,max_length=50)
    snr_db: float | None = Field(default=None,ge=-100,le=150)
    received_t: float | None = Field(default=None,ge=0,le=86500)

class Event(Strict):
    t: float = Field(ge=0,le=86400)
    device_id: str = Field(min_length=1,max_length=80)
    kind: Literal['online','offline','alert','strategy','ack']
    detail: str = Field(min_length=1,max_length=500)

class AlgorithmResult(Strict):
    research_task: Literal[1,2,3,4]
    name: str = Field(min_length=1,max_length=100)
    version: str = Field(min_length=1,max_length=100)
    input_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    summary: str = Field(min_length=1,max_length=2000)
    metrics: dict[str,float] = Field(default_factory=dict,max_length=50)
    @model_validator(mode='after')
    def finite(self):
        if any(not math.isfinite(v) for v in self.metrics.values()): raise ValueError('算法指标必须为有限数值')
        return self

class Run(Strict):
    name: str = Field(min_length=1,max_length=100)
    provenance: Literal['synthetic','field']
    source: str = Field(min_length=1,max_length=500)
    configuration: str = Field(min_length=1,max_length=500)
    deployed_device_ids: list[str] | None = Field(default=None,max_length=50)
    model_version: str = Field(min_length=1,max_length=200)
    actual_conditions: str = Field(min_length=1,max_length=1500)
    coordinate_frame: str = Field(min_length=1,max_length=300)
    time_basis: Literal['seconds_since_trial_start'] = 'seconds_since_trial_start'
    association_method: str = Field(min_length=1,max_length=500)
    truth: list[Truth] = Field(min_length=1,max_length=10000)
    observations: list[Observation] = Field(default_factory=list,max_length=30000)
    events: list[Event] = Field(default_factory=list,max_length=3000)
    algorithm_results: list[AlgorithmResult] = Field(default_factory=list,max_length=20)
    @model_validator(mode='after')
    def unique(self):
        for rows,fields in [(self.truth,('target_id','t')),(self.observations,('source_id','target_id','t'))]:
            keys=[tuple(getattr(p,k) for k in fields) for p in rows]
            if len(keys)!=len(set(keys)):raise ValueError('同一目标/来源不允许重复时间戳')
        if len({p.source_id for p in self.observations})>50 or len({p.target_id for p in self.truth})>100:raise ValueError('单次最多 50 个观测来源、100 个参考目标')
        if any(p.received_t is not None and p.received_t < p.t for p in self.observations):raise ValueError('接收时间不能早于观测时间')
        return self

@router.get('/demonstration')
def page(): return FileResponse(store.ROOT/'static'/'demonstration-overview.html')

@router.get('/demonstration/{scene}')
def scene_page(scene:str, request:Request):
    if scene not in ('urban','corridor','electromagnetic'):raise HTTPException(404,'示范场景不存在')
    html=(store.ROOT/'static'/'demonstration.html').read_text()
    return HTMLResponse(html.replace('<head>', '<head><base href="'+request.scope.get('root_path','')+'/">'))

@router.get('/api/demonstration/research')
def research(): return {'research':RESEARCH,'kinds':KINDS,'page_numbering':'PDF 文件页序，正文页码减 2'}

@router.get('/api/demonstration/trials')
def trials(): return [{k:v for k,v in t.items() if k!='snapshot'} for t in records('trial')]

@router.post('/api/demonstration/trials')
def create_trial(request:Trial):
    task,scene,calibration=inputs(request.task_id)
    snapshot={'task':task,'scene':scene,'resources':resources(task['scene'],scene),'calibration':calibration}
    if request.plan_id:
        p=read_record('plan',request.plan_id)
        if p['task_id']!=request.task_id:raise HTTPException(422,'方案不属于此规划任务')
        snapshot=copy.deepcopy(p['snapshot'])
        from .planning_assessment import planned_devices
        snapshot['scene']['devices'] = copy.deepcopy(planned_devices(snapshot['scene'], p['selected'], p.get('updated_devices')))
        if p.get('updated_devices'):
            snapshot['resources']['devices'] = copy.deepcopy(planned_devices(snapshot['resources'], p['selected'], p['updated_devices']))
        snapshot['plan']=p
    data=dict(request.model_dump(),id=uuid.uuid4().hex[:12],scene=snapshot['task']['scene'],created_at=store.now(),snapshot=snapshot)
    data['snapshot_digest']=digest(snapshot)
    return save('trial',data)

@router.get('/api/demonstration/trials/{trial_id}')
def trial(trial_id:str): return read_record('trial',trial_id)


def evaluate(trial, run):
    truth=sorted(run['truth'],key=lambda p:(p['target_id'],p['t']))
    by_target={}
    for p in truth:by_target.setdefault(p['target_id'],[]).append(p)
    indexes={k:[p['t'] for p in v] for k,v in by_target.items()}
    groups={}
    for p in run['observations']:groups.setdefault(p['source_id'],[]).append(p)
    results={};residuals=[]
    for source,observations in sorted(groups.items()):
        used=set();matched={};unmatched=0
        # Match nearest reference independently per source, one-to-one, within tolerance.
        candidates=[]
        for oi,o in enumerate(observations):
            times=indexes.get(o['target_id'],[]);i=bisect_left(times,o['t'])
            for ti in (i-1,i):
                if 0<=ti<len(times) and abs(times[ti]-o['t'])<=trial['sync_tolerance_s']:
                    candidates.append((abs(times[ti]-o['t']),oi,ti))
        obs_used=set()
        for dt,oi,ti in sorted(candidates):
            o=observations[oi];key=(o['target_id'],ti)
            if oi in obs_used or key in used:continue
            used.add(key);obs_used.add(oi);p=by_target[o['target_id']][ti]
            delta=[o[k]-p[k] for k in ('x','y','z')];err=math.sqrt(sum(v*v for v in delta))
            matched[key]=(o,err)
            residuals.append({'source_id':source,'target_id':p['target_id'],'t':p['t'],'dt_s':dt,'dx':delta[0],'dy':delta[1],'dz':delta[2],'error_m':err})
        errors=[v[1] for v in matched.values()]
        speeds=[];categories=[];latencies=[];reacquisitions=[];longest=0;breaks=0;switches=0;handoffs=0
        for target,points in by_target.items():
            prev=None;segment_start=None;had_match=False;broken=False;gap_start=None
            for i,p in enumerate(points):
                hit=matched.get((target,i))
                if hit:
                    o,err=hit
                    if gap_start is not None:reacquisitions.append(p['t']-gap_start);gap_start=None
                    if p.get('speed_mps') is not None and o.get('speed_mps') is not None:speeds.append(o['speed_mps']-p['speed_mps'])
                    if p.get('category') is not None and o.get('category') is not None:categories.append(p['category']==o['category'])
                    if o.get('received_t') is not None:latencies.append(o['received_t']-o['t'])
                    continuous=prev is not None and p['t']-prev[0]<=trial['max_gap_s'] and o['track_id']==prev[1]['track_id']
                    if prev is not None:
                        if o['track_id']!=prev[1]['track_id']:switches+=1
                        if set(o['device_ids'])!=set(prev[1]['device_ids']):handoffs+=1
                    if not continuous:
                        if had_match and not broken:breaks+=1
                        segment_start=p['t']
                    longest=max(longest,p['t']-segment_start)
                    prev=(p['t'],o);had_match=True;broken=False
                else:
                    if prev is not None:breaks+=1;broken=True;gap_start=p['t']
                    prev=None;segment_start=None
        def rmse(xs):return math.sqrt(sum(x*x for x in xs)/len(xs)) if xs else None
        r=[p for p in residuals if p['source_id']==source]
        bias={k:sum(p[k] for p in r)/len(r) if r else None for k in ('dx','dy','dz')}
        results[source]={'matched_samples':len(errors),'reference_samples':len(truth),'sample_detection_rate':len(errors)/len(truth),
            'position_rmse_m':rmse(errors),'position_pass_rate':sum(e<=trial['position_limit_m'] for e in errors)/len(errors) if errors else None,
            'speed_rmse_mps':rmse(speeds),'speed_pairs':len(speeds),'recognition_accuracy':sum(categories)/len(categories) if categories else None,'recognition_pairs':len(categories),
            'mean_latency_s':sum(latencies)/len(latencies) if latencies else None,'latency_pairs':len(latencies),
            'longest_continuous_s':longest,'track_breaks':breaks,'identity_switches':switches,'station_set_changes':handoffs,
            'unmatched_observations':len(observations)-len(obs_used),'position_bias_m':bias,
            'mean_snr_db':sum(o['snr_db'] for o in observations if o.get('snr_db') is not None)/sum(o.get('snr_db') is not None for o in observations) if any(o.get('snr_db') is not None for o in observations) else None,
            'reacquisition_intervals':len(reacquisitions),'mean_reacquisition_s':sum(reacquisitions)/len(reacquisitions) if reacquisitions else None}
    return {'method':'demonstration-evaluation-v1','sources':results,'residuals':residuals,
        'definitions':['样本检出比例＝时间匹配成功的参考样本数 / 全部参考样本数；仅代表本次航迹采样，不等同于空间覆盖率或严格探测概率。',
        '每个来源独立按外部目标关联和最近时间一对一匹配；真值仅用于事后评价，未作为定位或跟踪输入。',
        '连续段在缺测、间隔超过 max_gap_s 或航迹身份变化时结束；单点段时长为零。',
        '未匹配观测数不等同于虚警率；站点集合变化不等同于跨站接续成功率。',
        '定位与测速误差、分类准确率及延迟仅用具备相应字段的匹配样本；无样本返回空值。',
        '重捕获时长从已跟踪目标的首个缺测参考时刻算至再次匹配；末尾未恢复缺测不进入均值。',
        '残差均值为校正候选依据，未经独立数据验证不会自动修改规划模型。']}

@router.get('/api/demonstration/trials/{trial_id}/runs')
def runs(trial_id:str):
    read_record('trial',trial_id)
    return [{k:v for k,v in r.items() if k not in ('truth','observations','events','evaluation','algorithm_results')} for r in records('run') if r['trial_id']==trial_id]

@router.post('/api/demonstration/trials/{trial_id}/runs')
def create_run(trial_id:str,request:Run):
    t=read_record('trial',trial_id);r=request.model_dump()
    if r['coordinate_frame']!=t['coordinate_frame']:raise HTTPException(422,'坐标基准与示范任务不一致，请先完成坐标转换')
    devices={d['id'] for d in t['snapshot']['scene']['devices']}
    if r['deployed_device_ids'] is not None:
        if not set(r['deployed_device_ids'])<=devices:raise HTTPException(422,'试验部署引用了快照外设备')
        devices=set(r['deployed_device_ids'])
    else:r['deployed_device_ids']=sorted(devices)
    if any(not set(o['device_ids'])<=devices for o in r['observations']) or any(e['device_id'] not in devices for e in r['events']):raise HTTPException(422,'观测或事件引用了部署快照之外的设备')
    if any(a['input_digest']!=t['snapshot_digest'] for a in r['algorithm_results']):raise HTTPException(422,'算法结果输入摘要与任务快照不一致')
    r['truth']=sorted(r['truth'],key=lambda p:(p['target_id'],p['t']))
    r['observations']=sorted(r['observations'],key=lambda p:(p['t'],p['source_id'],p['target_id']))
    r['events']=sorted(r['events'],key=lambda p:p['t'])
    r.update(id=uuid.uuid4().hex[:12],trial_id=trial_id,created_at=store.now(),snapshot_digest=t['snapshot_digest'])
    r['data_digest']=digest(request.model_dump());r['reference_digest']=digest(r['truth'])
    r['evaluation']=evaluate(t,r)
    return save('run',r)

@router.get('/api/demonstration/runs/{run_id}')
def run(run_id:str):return read_record('run',run_id)

@router.get('/api/demonstration/runs/{run_id}/report')
def report(run_id:str):
    r=read_record('run',run_id);t=read_record('trial',r['trial_id'])
    return {'report_version':'1.0','generated_at':store.now(),'trial':t,'run':r,'calibration_candidates':[c for c in records('demo-calibration') if c['run_id']==run_id],'research_mapping':RESEARCH,
        'evidence_status':'合成数据软件验证' if r['provenance']=='synthetic' else '用户导入实测数据，来源与标定精度需现场核验'}

@router.get('/api/demonstration/compare/{a}/{b}')
def compare(a:str,b:str,mode:Literal['controlled','conditions']='controlled'):
    left=read_record('run',a);right=read_record('run',b)
    if a==b:raise HTTPException(422,'请选择两次不同试验')
    keys=('trial_id','reference_digest','provenance','coordinate_frame','association_method')
    if mode=='controlled':keys+=('actual_conditions',)
    for key in keys:
        if key not in left or key not in right or left[key]!=right[key]:raise HTTPException(409,'试验条件、参考航迹、数据性质或关联方法不同，不能直接比较')
    common=sorted(set(left['evaluation']['sources'])&set(right['evaluation']['sources']))
    if not common:raise HTTPException(409,'没有共同的评价来源')
    rows=[]
    for s in common:
        x=left['evaluation']['sources'][s];y=right['evaluation']['sources'][s]
        for k in ('sample_detection_rate','position_rmse_m','speed_rmse_mps','longest_continuous_s','track_breaks','recognition_accuracy'):
            rows.append({'source_id':s,'metric':k,'before':x[k],'after':y[k],'delta':y[k]-x[k] if x[k] is not None and y[k] is not None else None})
    # Shared-reference RMSE avoids presenting changes in matched sample selection as accuracy gains.
    paired=[]
    for s in common:
        lr={(p['target_id'],p['t']):p['error_m'] for p in left['evaluation']['residuals'] if p['source_id']==s}
        rr={(p['target_id'],p['t']):p['error_m'] for p in right['evaluation']['residuals'] if p['source_id']==s}
        keys=lr.keys()&rr.keys()
        paired.append({'source_id':s,'samples':len(keys),'before_rmse_m':math.sqrt(sum(lr[k]**2 for k in keys)/len(keys)) if keys else None,'after_rmse_m':math.sqrt(sum(rr[k]**2 for k in keys)/len(keys)) if keys else None})
    return {'before':a,'after':b,'rows':rows,'common_sample_accuracy':paired,'mode':mode,'before_conditions':left.get('actual_conditions'),'after_conditions':right.get('actual_conditions'),'note':('不同实际条件的描述性对比，不能将差异直接归因于规划或算法改善。' if mode=='conditions' else '同一冻结任务和参考数据下的对比；配置和模型版本可不同。因果效果仍需控制现场条件。')}

@router.get('/api/demonstration/trials/{trial_id}/template')
def template(trial_id:str,variant:Literal['baseline','improved']='baseline'):
    t=read_record('trial',trial_id);devices=t['snapshot']['scene']['devices']
    if not devices:raise HTTPException(422,'请先在规划任务中配置设备，再建立示范任务')
    truth=[];obs=[]
    for i in range(61):
        p=dict(t=float(i),target_id='UAV-TEST-01',x=200+i*25,y=900+80*math.sin(i/10),z=140.,speed_mps=25.,category='无人机')
        if t['scene']=='urban':p['y']=900+160*math.sin(i/6)
        elif t['scene']=='electromagnetic':p['y']=900+220*math.sin(i/15)
        truth.append(p)
        if (variant=='baseline' and 20<=i<=27) or (variant=='improved' and i==24):continue
        error=8 if variant=='baseline' else 3
        obs.append(dict(p,x=p['x']+error,y=p['y']+error*.5,speed_mps=25+error*.1,source_id='fusion',track_id='TRACK-01',device_ids=[devices[(i//20)%len(devices)]['id']],snr_db=14.,received_t=i+.12))
    return dict(name='合成基准' if variant=='baseline' else '合成改进',provenance='synthetic',source='平台生成的软件验收样例；误差和缺测为人工设定',configuration=variant,model_version='synthetic-fixture-v1',actual_conditions='固定合成航迹，无真实环境变化；噪声与缺测按样例设定',coordinate_frame=t['coordinate_frame'],time_basis='seconds_since_trial_start',association_method='合成样例固定目标标签，仅事后评价',truth=truth,observations=obs,events=[{'t':20.,'device_id':devices[0]['id'],'kind':'alert','detail':'示例事件：进入遮挡测试航段'}],algorithm_results=[])


class CalibrationRequest(Strict):
    source_id: str = Field(min_length=1,max_length=80)

@router.get('/api/demonstration/runs/{run_id}/calibrations')
def calibrations(run_id:str):
    read_record('run',run_id)
    return [c for c in records('demo-calibration') if c['run_id']==run_id]

@router.post('/api/demonstration/runs/{run_id}/calibrations')
def calibration_candidate(run_id:str,request:CalibrationRequest):
    r=read_record('run',run_id)
    rows=sorted([p for p in r['evaluation']['residuals'] if p['source_id']==request.source_id],key=lambda p:p['t'])
    times=sorted({p['t'] for p in rows})
    if len(times)<6:raise HTTPException(422,'至少需要 6 个不同时间的匹配样本才能划分训练与留出验证')
    split=times[len(times)//2]
    train=[p for p in rows if p['t']<split];test=[p for p in rows if p['t']>=split]
    bias={k:sum(p[k] for p in train)/len(train) for k in ('dx','dy','dz')}
    before=math.sqrt(sum(p['error_m']**2 for p in test)/len(test))
    after=math.sqrt(sum(sum((p[k]-bias[k])**2 for k in bias) for p in test)/len(test))
    c=dict(id=uuid.uuid4().hex[:12],run_id=run_id,source_id=request.source_id,created_at=store.now(),
        method='constant-xyz-bias-holdout-v1',input_digest=r['data_digest'],model_version=r['model_version'],provenance=r['provenance'],
        correction_m={k:-v for k,v in bias.items()},train_samples=len(train),holdout_samples=len(test),split_time_s=split,
        before_rmse_m=before,after_rmse_m=after,holdout_improved=after<before,
        scope='按时间前半段估计固定 XYZ 偏差，后半段留出验证；仅为离线候选，不更新设备或规划模型，正式采用需独立场次验证。')
    return save('demo-calibration',c)


@router.get('/api/demonstration/overview')
def overview():
    trials=records('trial');runs=records('run');result=[]
    for scene in ('electromagnetic','corridor','urban'):
        ts=[t for t in trials if t['scene']==scene];ids={t['id'] for t in ts}
        rs=[r for r in runs if r['trial_id'] in ids]
        deployment=resources(scene)
        result.append(dict(scene=scene,tasks=len(ts),runs=len(rs),field_runs=sum(r['provenance']=='field' for r in rs),
            synthetic_runs=sum(r['provenance']=='synthetic' for r in rs),devices=len(deployment['devices']),
            online_devices=sum(d['status']=='online' for d in deployment['devices']),latest_trial=ts[0]['id'] if ts else None))
    return {'scenes':result,'note':'设备状态来自当前场景配置；实测数量按导入声明统计，现场验收状态未作推断。'}

@router.get('/api/demonstration/trials/{trial_id}/coverage')
def trial_coverage(trial_id:str):
    from . import engineering
    t=read_record('trial',trial_id);snap=t['snapshot'];scene=copy.deepcopy(snap['scene'])
    if snap.get('plan', {}).get('assessment_job_id'):
        from .planning_portal import preview
        comparison = preview(snap['plan']['id'])
        before_scene = copy.deepcopy(snap['plan']['snapshot']['scene'])
        return dict(scene=scene, before_scene=before_scene, before=comparison['before'], after=comparison['after'],
                    note='关联方案的冻结细网格验算，体积加权统计；现场观测结果在运行记录中核验。')
    scene['zone']=copy.deepcopy(snap['task'].get('focus',scene['zone']))
    request=engineering.Volume(**snap['task']['volume']);cal=snap.get('calibration')
    after=engineering.volume(scene,request,cal)
    plan=snap.get('plan')
    if plan:
        added={d['id'] for d in plan['selected']}
        scene['devices']=[d for d in scene['devices'] if d['id'] not in added]
        before=engineering.volume(scene,request,cal)
    else:before=None
    return dict(scene=snap['scene'],before_scene=scene,before=before,after=after,
        note='冻结输入上的覆盖模型计算；有规划方案时显示补盲前后同网格对比。结果不是实测空间覆盖率。')

@router.get('/api/demonstration/runs/{run_id}/insights')
def run_insights(run_id:str):
    r=read_record('run',run_id);t=read_record('trial',r['trial_id']);result={}
    for source,metrics in r['evaluation']['sources'].items():
        obs=[o for o in r['observations'] if o['source_id']==source]
        matched={(p['target_id'],p['t']) for p in r['evaluation']['residuals'] if p['source_id']==source}
        gaps=[];changes=[]
        for target in sorted({p['target_id'] for p in r['truth']}):
            refs=sorted([p for p in r['truth'] if p['target_id']==target],key=lambda p:p['t']);start=None;count=0
            for p in refs:
                if (target,p['t']) not in matched:
                    if start is None:start=p['t']
                    count+=1
                elif start is not None:
                    gaps.append(dict(target_id=target,start=start,end=p['t'],missing_samples=count,recovered=True));start=None;count=0
            if start is not None:gaps.append(dict(target_id=target,start=start,end=refs[-1]['t'],missing_samples=count,recovered=False))
            previous=None
            for o in sorted([o for o in obs if o['target_id']==target],key=lambda p:p['t']):
                if previous and set(previous['device_ids'])!=set(o['device_ids']):
                    changes.append(dict(target_id=target,t=o['t'],before=previous['device_ids'],after=o['device_ids'],
                        identity_kept=previous['track_id']==o['track_id'],interval_s=o['t']-previous['t']))
                previous=o
        bins=[]
        for label,lo,hi in [('低于 0 dB',-math.inf,0),('0–10 dB',0,10),('10–20 dB',10,20),('20 dB 及以上',20,math.inf)]:
            values=[o['snr_db'] for o in obs if o.get('snr_db') is not None and lo<=o['snr_db']<hi]
            bins.append(dict(label=label,count=len(values),mean_snr_db=sum(values)/len(values) if values else None))
        result[source]=dict(metrics=metrics,gaps=gaps,station_changes=changes,snr_bins=bins,
            modalities=sorted({d['type'] for d in t['snapshot']['scene']['devices'] if any(d['id'] in o['device_ids'] for o in obs)}))
    return dict(scene=t['scene'],sources=result,note='缺测区段基于本次参考样本；设备集合变化仅记录观测变化，不等同于已验证的跨站成功率。')
