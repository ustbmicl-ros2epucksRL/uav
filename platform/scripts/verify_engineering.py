"""Live local workbench smoke run; saves synthetic results, never applies plans."""
import httpx,json,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root.parent/'outputs/platform-engineering';out.mkdir(parents=True,exist_ok=True)
with httpx.Client(base_url='http://127.0.0.1:8767',timeout=60) as c:
 for scene in ('urban','corridor','electromagnetic'):
  start=time.perf_counter();base=f'/api/scenes/{scene}/engineering'
  v={'bounds':[700,900,700,900,120,160],'cell':10}
  for endpoint,body in [('volume',v),('plan',{'volume':v,'budget':50}),('schedule',{'bandwidth':5,'compute':5,'tasks':[{'id':f'T{i}','kind':k,'priority':5-i,'bandwidth':2,'compute':1} for i,k in enumerate(('discovery','localization','tracking','handover','warning'))]})]:
   r=c.post(base+'/'+endpoint,json=body);r.raise_for_status()
  report=c.get(base+'/report');report.raise_for_status();data=report.json()
  (out/f'{scene}-engineering-report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
  print(scene,round(time.perf_counter()-start,2),'s','cells',data['volume']['evaluated_cells'],'feasible',data['plan']['feasible'],'subsets',data['plan']['evaluated_subsets'])
 r=c.post('/api/scenes/urban/engineering/volume',json={'bounds':[700,780,700,780,120,140],'cell':2});r.raise_for_status()
 (out/'urban-focus-2m.json').write_text(json.dumps(r.json(),ensure_ascii=False))
 print('focus 2m cells:',r.json()['evaluated_cells'])
 assert c.get('/workbench').status_code==200
