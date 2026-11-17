"""Legacy response adapters; all spatial calculations use coverage_core."""
import copy
import time
import uuid
import numpy as np
from .store import now
from .coverage_sub1.grid import Grid3D
from . import coverage_core as core

LIMITATIONS = [
    '合成园区与示例设备参数；未作实测标定。',
    '全景默认50 m网格；区域计算支持10 m/2 m，网格分辨率不是定位精度。',
    '统一三维建筑射线遮挡；未建模地形、绕射和多径。',
    '融合Pd采用独立探测假设；重叠覆盖不等同于定位精度增益。',
    'RF以目标发射为前提，合作报文不计入非合作覆盖。',
]


def space(scene, request=None):
    request=request or core.default_volume()
    b=request.bounds
    shape=[round((b[i+1]-b[i])/request.cell) for i in (0,2,4)]
    grid=Grid3D(*shape,request.cell,origin=(b[0],b[2],b[4]))
    points=core.grid(request)
    return grid,{'scene':scene,'request':request,'points':points},core.valid_points(scene,points).reshape(grid.shape)


def mask(device,grid,env,air):
    pd=core.device_field(env['scene'],env['points'],device,env['request'])[0]
    return (pd>=env['request'].pd_threshold).reshape(grid.shape)&air


def _stats(scene,request,points,f,shape):
    stats=core.summarize(f,request,points,scene['zone'])
    covered=(f['pd']>=request.pd_threshold)&f['valid']
    stats.update(overlap=float((f['count'][f['valid']]>=2).mean()) if f['valid'].any() else None,
                 blind_voxels=int((~covered&f['valid']).sum()),valid_voxels=int(f['valid'].sum()),
                 covered_voxels=int(covered.sum()),cell=request.cell,
                 altitude_layers=np.unique(points[:,2]).tolist())
    return stats


def compute(scene,request=None):
    started=time.perf_counter();request=request or core.default_volume()
    grid,env,air=space(scene,request);points=env['points']
    f=core.fields(scene,points,request)
    covered=(f['pd']>=request.pd_threshold).reshape(grid.shape)&air
    # Display classes: 0 blind, 1 fused coverage, >=2 individually sufficient devices.
    count=np.where(covered,np.maximum(1,f['count'].reshape(grid.shape)),0)
    layer=[np.where(air[:,:,k],count[:,:,k],-1).tolist() for k in range(grid.nz)]
    hm=np.divide(covered.sum(axis=2),air.sum(axis=2),out=np.zeros(grid.shape[:2]),where=air.sum(axis=2)>0)
    contributions=[{'id':d['id'],'type':d['type'],'coverage':float((f['per_device'][d['id']][f['valid']]>=request.pd_threshold).mean()) if air.any() else None} for d in scene['devices']]
    weak=[]
    for xs,ys,name in [(slice(0,grid.nx//2),slice(0,grid.ny//2),'西南区'),(slice(0,grid.nx//2),slice(grid.ny//2,None),'西北区'),(slice(grid.nx//2,None),slice(0,grid.ny//2),'东南区'),(slice(grid.nx//2,None),slice(grid.ny//2,None),'东北区')]:
        a=air[xs,ys];c=covered[xs,ys]
        weak.append({'name':name,'blind_voxels':int((~c&a).sum()),'coverage':float(c[a].mean()) if a.any() else None})
    return {**core.metadata(request),'stats':_stats(scene,request,points,f,grid.shape),'layers':layer,'heatmap':hm.tolist(),
            'grid':{'shape':list(grid.shape),'origin':list(grid.origin),'cell':grid.cell},
            'layer_definition':'-1无效，0盲区，1融合达标，>=2单设备Pd达标的设备数；不代表定位精度',
            'contributions':contributions,'weak_regions':sorted(weak,key=lambda w:-w['blind_voxels']),
            'revision':scene['revision'],'computed_at':now(),'seconds':round(time.perf_counter()-started,3),
            'limitations':LIMITATIONS}


def optimize(scene,budget,target):
    """Greedy additions, evaluated with the same fused field as diagnosis."""
    request=core.default_volume();points=core.grid(request)
    working=copy.deepcopy(scene);f=core.fields(working,points,request)
    before=compute(scene)['stats'];z=scene['zone']
    focus=(points[:,0]>=z['x0'])&(points[:,0]<=z['x1'])&(points[:,1]>=z['y0'])&(points[:,1]<=z['y1'])&(points[:,2]>=z.get('z0',0))&(points[:,2]<=z.get('z1',300))
    weight=np.where(focus,2.,1.)*f['valid']
    candidates=[d for d in scene['candidates'] if d['status']=='online' and d['id'] not in {v['id'] for v in scene['devices']}]
    chosen=[];spent=0.
    while candidates:
        covered=f['pd']>=request.pd_threshold
        if not f['valid'].any() or covered[f['valid']].mean()>=target:break
        ranked=[]
        for d in candidates:
            if spent+d['cost']>budget:continue
            pd=core.device_field(scene,points,d,request)[0]
            fused=1-(1-f['pd'])*(1-pd)
            gain=float((((fused>=request.pd_threshold)&~covered)*weight).sum())
            ranked.append((gain/max(d['cost'],.01),d))
        if not ranked:break
        score,d=max(ranked,key=lambda row:row[0])
        if score<=0:break
        chosen.append(d);spent+=d['cost'];working['devices'].append(d)
        f=core.fields(working,points,request)
        candidates=[other for other in candidates if other['id']!=d['id']]
    after=compute(working)['stats']
    return {**core.metadata(request),'id':uuid.uuid4().hex[:12],'scene':scene['id'],'scene_revision':scene['revision'],
            'created_at':now(),'before':before,'after':after,'selected':chosen,
            'budget':budget,'added_cost':spent,'target':target,'target_met':after['coverage'] is not None and after['coverage']>=target,
            'method':'统一融合Pd模型下的重点区加权单位成本覆盖增益贪心',
            'note':'演示成本；不保证全局最优。覆盖含简化环境损耗，未约束定位精度。','status':'draft'}
