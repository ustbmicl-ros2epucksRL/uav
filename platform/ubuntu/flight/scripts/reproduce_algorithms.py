"""Reproduce open-source components and read-only API integration."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    with tempfile.TemporaryDirectory(prefix='lowair-algorithm-repro-') as temp:
        for name,folder in [('FLIGHT_DATA_DIR','flight'),('SENSE_DATA_DIR','planning'),('FLIGHT_ALGORITHM_DIR','runs')]:os.environ[name]=temp+'/'+folder
        from app.algorithm_routes import solve_route,validate_path
        from app.algorithm_compute import default_compute,evaluate
        from app.main import app
        from fastapi.testclient import TestClient
        bounds=dict(xyz_min=[0,0,30],xyz_max=[1000,1000,280])
        start=[100,500,120];goal=[900,500,120]
        cases={'open':[], 'barrier':[dict(id='TEMP-BARRIER',kind='no_fly',lower=[400,250,0],upper=[600,750,300])],
               'sealed':[dict(id='sealed',kind='no_fly',lower=[400,-20,0],upper=[600,1020,300])]}
        results=[]
        for case,boxes in cases.items():
            for planner in ('bitstar','acs_ds','astar'):
                for seed in (1,7,23):
                    began=time.monotonic();r=solve_route(start,goal,bounds,boxes,planner,seed,.5)
                    safe=r['status']=='ok' and validate_path(r['points'],start,goal,bounds,boxes)
                    assert (r['status']=='no_path') if case=='sealed' else safe
                    results.append(dict(case=case,seed=seed,elapsed_s=time.monotonic()-began,safe=safe,**r))
        cfg=default_compute(bounds);compute=[]
        for label,pos,links in [('near',[100,100,120],cfg['links']),('far',[900,900,120],cfg['links']),('outage',[100,100,120],[])]:
            config={**cfg,'links':links};a=[evaluate(config,pos,p) for p in ('heft','local','edge','cloud')]
            assert a==[evaluate(config,pos,p) for p in ('heft','local','edge','cloud')]
            compute.append(dict(case=label,position=pos,results=a))
        client=TestClient(app);before=client.get('/api/vehicles').json()
        ex=client.post('/api/algorithms/runs',json={'planner':'bitstar'});assert ex.status_code==200,ex.text
        r=ex.json();assert client.get('/api/algorithms/runs/'+r['id']).json()==r
        assert client.get('/api/vehicles').json()==before
        output=dict(source='simulation',scope='component reproduction and platform adaptation, not full paper metrics',
                    versions={p:importlib.metadata.version(p) for p in ('ompl','numpy','fastapi','pydantic')},
                    vendor=json.loads((ROOT/'platform/ubuntu/flight/app/vendor/sources.json').read_text()),
                    route_cases=results,compute_cases=compute,api_experiment=r,
                    assertions=dict(route_checks=len(results),deterministic_compute=True,archive_roundtrip=True,no_flight_mutation=True),
                    excluded=dict(hatto_full='ModuleNotFoundError: ufog_network.env; not shipped upstream',
                                  airfogsim_full='Reviewed source; SUMO/CUDA-oriented environment not reproduced in this integration'))
        target=ROOT/'outputs/algorithm-selection/2026-11-13';target.mkdir(parents=True,exist_ok=True)
        (target/'reproduction.json').write_text(json.dumps(output,ensure_ascii=False,allow_nan=False,indent=2))
        print(json.dumps(output['assertions'],ensure_ascii=False));print(target/'reproduction.json')
if __name__=='__main__':main()
