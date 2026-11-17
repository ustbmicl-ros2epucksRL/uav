"""Run existing baseline components on identical synthetic inputs; not field validation.
Run from repository root: PYTHONPATH=platform python platform/scripts/audit_coverage_baseline.py
"""
import hashlib
import json
import time
from pathlib import Path
import numpy as np
from app import coverage_core as core, analysis, engineering
from app.scenes import seed
from app.store import now

out=Path(__file__).resolve().parents[2]/'outputs'/'baseline-status-audit'
out.mkdir(parents=True,exist_ok=True)
request=core.default_volume()
points=core.grid(request)
report={'started_at':now(),'model_version':core.MODEL_VERSION,'settings':request.model_dump(),
        'data_source':'内置合成场景 seed；无实测真值',
        'interpretation':'仅对比模型假设敏感性，差异不是预测准确率或新算法提升。B0/B1在本审查脚本组合现有几何函数，尚非平台独立对照入口。',
        'definitions':{'B0':'标称距离+方向视场，无建筑遮挡；统一排除建筑内部和无效空域',
                       'B1':'B0+三维建筑射线遮挡，几何并集',
                       'B2':'平台现有简化损耗+示例探测概率+独立融合'},
        'code_sha256':{name:hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes()).hexdigest() for name in ['app/coverage_core.py','app/scenes.py','app/analysis.py','app/engineering.py','scripts/audit_coverage_baseline.py']},'scenes':[]}
for name in ['urban','corridor','electromagnetic']:
    started=time.perf_counter();scene=seed(name);valid=core.valid_points(scene,points)
    masks=[]
    for buildings in [[],scene['buildings']]:
        covered=np.zeros(len(points),bool)
        for d in scene['devices']:
            if d['status']=='online' and d['type']!='ADSB':covered|=core.visible(points,d,buildings)[0]
        masks.append(covered&valid)
    f=core.fields(scene,points,request);masks.append((f['pd']>=request.pd_threshold)&valid)
    diagnostic=analysis.compute(scene,request);volume=engineering.volume(scene,request)
    keys=['coverage','focus_coverage','mean_pd','valid_cells','covered_cells','precision_pass_rate']
    equal=all(diagnostic['stats'][k]==volume['stats'][k] for k in keys)
    assert equal
    result={'scene':name,'input_snapshot':scene,'valid_cells':int(valid.sum()),
            'baselines':{k:{'covered_cells':int(m.sum()),'coverage':float(m[valid].mean())} for k,m in zip(['B0','B1','B2'],masks)},
            'diagnosis_engineering_equal':equal,'seconds':time.perf_counter()-started}
    report['scenes'].append(result)
    print(name,json.dumps({k:round(v['coverage'],6) for k,v in result['baselines'].items()}), 'same_inputs_equal=',equal)
report['completed_at']=now()
(out/'runs.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
