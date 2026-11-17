"""Executed, snapshot-backed B0/B1/B2 comparisons. No reference truth is assumed."""
import hashlib
import json
import time
import uuid
from pathlib import Path
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import Field
from fastapi.responses import FileResponse
from . import coverage_core as core, store

router=APIRouter()
DEFINITIONS={'B0':'标称距离与方向视场，不计建筑遮挡','B1':'三维建筑遮挡后的几何并集','B2':'简化损耗、示例探测概率与独立融合'}

class ComparisonRequest(core.Volume):
    candidate_budget:float=Field(default=50,ge=0,le=1000)


def impacts(scene,request,p,valid,focus,masks,f):
    """Fixed-budget, single-device marginal ranking. Not joint deployment optimization."""
    disagree=valid&((masks[0]!=masks[1])|(masks[1]!=masks[2]))
    b=request.bounds;regions=[]
    for ix in range(2):
        for iy in range(2):
            for iz in range(2):
                bounds=[];region=valid.copy()
                for axis,half in enumerate((ix,iy,iz)):
                    lo,hi=b[2*axis:2*axis+2];mid=(lo+hi)/2
                    left,right=(lo,mid) if half==0 else (mid,hi)
                    bounds.extend([left,right]);region&=(p[:,axis]>=left)&(p[:,axis]<right)
                n=int(region.sum());diff=int((region&disagree).sum())
                regions.append({'name':('西' if ix==0 else '东')+('南' if iy==0 else '北')+('低层' if iz==0 else '高层'),
                                'bounds':bounds,'valid_cells':n,'disagreement_cells':diff,'disagreement_rate':diff/n if n else None,
                                'focus_disagreement_cells':int((region&focus&disagree).sum())})
    regions.sort(key=lambda r:(-r['disagreement_cells'],r['name']))
    budget=getattr(request,'candidate_budget',50);rows=[];excluded=[]
    installed={d['id'] for d in scene['devices']}
    for d in scene.get('candidates',[]):
        reason=None
        if d['id'] in installed:reason='已部署'
        elif d['status']!='online':reason='设备离线'
        elif d['type']=='ADSB':reason='合作报文不计入非合作覆盖'
        elif d['type']=='RF' and not request.target_emitting:reason='静默目标不贡献RF覆盖'
        elif d['cost']>budget:reason='超过单设备预算'
        if reason:excluded.append({'id':d['id'],'reason':reason});continue
        added=[]
        for buildings in ([],scene['buildings']):added.append(core.visible(p,d,buildings)[0]&valid)
        pd=core.device_field(scene,p,d,request,valid)[0]
        after=[masks[0]|added[0],masks[1]|added[1],(1-(1-f['pd'])*(1-pd)>=request.pd_threshold)&valid]
        gains={}
        for i,key in enumerate(DEFINITIONS):
            new=after[i]&~masks[i]&valid
            gains[key]={'new_cells':int(new.sum()),'focus_new_cells':int((new&focus).sum()),
                        'disagreement_new_cells':int((new&disagree).sum()),
                        'gain_per_cost':float(new.sum()/d['cost']) if d['cost']>0 else None,
                        'after_coverage':float(after[i][valid].mean()) if valid.any() else None}
        rows.append({'id':d['id'],'type':d['type'],'cost':d['cost'],'gains':gains})
    rankings={};top_sets={}
    for key in DEFINITIONS:
        usable=[r for r in rows if r['gains'][key]['new_cells']>0]
        def score(r):return (r['cost']==0, r['gains'][key]['new_cells'] if r['cost']==0 else r['gains'][key]['gain_per_cost'])
        ranked=sorted(usable,key=lambda r:(-int(score(r)[0]),-score(r)[1],r['cost'],r['id']))
        rankings[key]=[r['id'] for r in ranked]
        top_sets[key]=[r['id'] for r in ranked if score(r)==score(ranked[0])] if ranked else []
    return {'disagreement_cells':int(disagree.sum()),'focus_disagreement_cells':int((disagree&focus).sum()),
            'regions':regions,'candidate_budget':budget,'candidates':rows,'excluded':excluded,
            'rankings':rankings,'top_sets':top_sets,'top_sets_differ':len({tuple(sorted(v)) for v in top_sets.values()})>1,
            'note':'仅比较预算内单个候选设备的新增覆盖体素/设备成本；不部署设备，不是组合最优，未纳入站址新增费用与挂载/供电约束。零成本且正增益优先，并列最优全部保留。无正增益不推荐。分歧不代表实际预测错误。'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def versions():
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in ('coverage_core.py','baseline_comparison.py')}

def calculate(scene,request):
    start=time.perf_counter();started=store.now();p=core.grid(request);valid=core.valid_points(scene,p)
    counts=[np.zeros(len(p),int),np.zeros(len(p),int)]
    for d in scene['devices']:
        if d['status']!='online' or d['type']=='ADSB' or (d['type']=='RF' and not request.target_emitting):continue
        for i,buildings in enumerate(([],scene['buildings'])):counts[i]+=core.visible(p,d,buildings)[0]&valid
    f=core.fields(scene,p,request)
    masks=[counts[0]>0,counts[1]>0,(f['pd']>=request.pd_threshold)&valid]
    z=scene['zone'];focus=valid.copy()
    for axis,key in enumerate(('x','y','z')):focus&=(p[:,axis]>=z.get(key+'0',0))&(p[:,axis]<=z.get(key+'1',300 if axis==2 else 2000))
    shape=[round((request.bounds[i+1]-request.bounds[i])/request.cell) for i in (0,2,4)]
    def rate(mask,region):return float(mask[region].mean()) if region.any() else None
    def layers(mask):return np.where(valid,mask.astype(int),-1).reshape(shape).transpose(2,0,1).tolist()
    models={k:{'definition':DEFINITIONS[k],'coverage':rate(m,valid),'focus_coverage':rate(m,focus),'covered_cells':int(m.sum()),'blind_cells':int((valid&~m).sum()),'layers':layers(m)} for k,m in zip(DEFINITIONS,masks)}
    changes={}
    for a,b in [(0,1),(1,2)]:
        # 1 = lost under next model; 2 = gained under next model. Not accuracy labels.
        codes=np.where(masks[a]&~masks[b],1,np.where(~masks[a]&masks[b],2,0))
        changes[f'B{a}_B{b}']={'lost_cells':int((codes==1).sum()),'gained_cells':int((codes==2).sum()),'layers':np.where(valid,codes,-1).reshape(shape).transpose(2,0,1).tolist()}
    impact=impacts(scene,request,p,valid,focus,masks,f)
    snapshot={'scene':scene,'settings':request.model_dump()}
    return {'id':uuid.uuid4().hex,'status':'completed','started_at':started,'completed_at':store.now(),'seconds':time.perf_counter()-start,
            'scene_id':scene['id'],'scene_revision':scene['revision'],'model_version':core.MODEL_VERSION,'code_sha256':versions(),
            'input_digest':digest(snapshot),'snapshot':snapshot,'valid_cells':int(valid.sum()),'focus_valid_cells':int(focus.sum()),
            'grid':{'shape':shape,'bounds':request.bounds,'cell':request.cell,'altitudes':np.unique(p[:,2]).tolist()},
            'models':models,'changes':changes,'impact':impact,'data_mode':'simulation_prediction',
            'note':'实际运行的模型假设对照；参数未标定，无独立观测真值。预测差异不等于准确率或算法提升。三个基线使用相同设备、网格、目标发射条件及有效空域。B0/B1为几何覆盖，B2为概率阈值覆盖。'}

@router.get('/baselines')
def page():return FileResponse(store.ROOT/'static'/'baselines.html')

@router.post('/api/scenes/{scene_id}/baselines')
def run(scene_id:str,request:ComparisonRequest):
    from .scene_service import scene_for
    try:r=calculate(scene_for(scene_id),request)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    store.put('baseline-run:'+r['id'],r)
    return r

@router.get('/api/scenes/{scene_id}/baselines')
def history(scene_id:str):
    from .scene_service import scene_for
    scene_for(scene_id)
    with store.connection() as c:rows=c.execute("SELECT value FROM state WHERE key LIKE 'baseline-run:%'").fetchall()
    runs=[json.loads(x[0]) for x in rows]
    return sorted([{k:r[k] for k in ('id','scene_id','scene_revision','completed_at','model_version')} for r in runs if r['scene_id']==scene_id],key=lambda r:r['completed_at'],reverse=True)

@router.get('/api/baselines/{run_id}')
def saved(run_id:str):
    from .scene_service import scene_for
    r=store.get('baseline-run:'+run_id)
    if not r:raise HTTPException(404,'运行记录不存在')
    current={'scene':scene_for(r['scene_id']),'settings':r['snapshot']['settings']}
    r['stale']=digest(current)!=r['input_digest'] or r['code_sha256']!=versions()
    return r
