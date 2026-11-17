"""HEFT upstream adapter: explicit units, DAG dependencies, directed links.

One reserved serial compute lane per node; links have reserved bandwidth (no
contention). Each route sample is an independent static batch, not a claim of
continuous-flight scheduling or remote worker execution.
"""
from functools import lru_cache
import heapq
import math
from .vendor.heft.core import schedule


def default_compute(bounds):
    x0,y0=bounds['xyz_min'][:2]; x1,y1=bounds['xyz_max'][:2]
    return dict(nodes=[
        dict(id='device',kind='device',position=[x0,y0,120],gcycles_s=1,power_w=4,busy_s=0),
        dict(id='edge-a',kind='edge',position=[x0+80,y0+80,10],gcycles_s=8,power_w=35,busy_s=0),
        dict(id='edge-b',kind='edge',position=[x1-80,y1-80,10],gcycles_s=5,power_w=22,busy_s=0.4),
        dict(id='cloud',kind='cloud',position=[x1,y1,0],gcycles_s=24,power_w=80,busy_s=0)],
        links=[dict(source=a,target=b,mbps=rate,latency_s=lat,max_range_m=r)
               for a,b,rate,lat,r in [
                   ('device','edge-a',30,.008,430),('edge-a','device',50,.008,430),
                   ('device','edge-b',20,.012,430),('edge-b','device',35,.012,430),
                   ('edge-a','cloud',100,.06,0),('cloud','edge-a',100,.06,0),
                   ('edge-b','cloud',60,.09,0),('cloud','edge-b',60,.09,0)]],
        tasks=[dict(id=i,gcycles=c,input_mbit=inp,output_mbit=out,deadline_s=6,parents=parents)
               for i,c,inp,out,parents in [
                   ('preprocess',.8,12,5,[]),('detect',8,0,1,['preprocess']),
                   ('segment',6,0,2,['preprocess']),('fusion',2,0,.3,['detect','segment']),
                   ('alert',.3,0,.02,['fusion'])]])


def transfer(source,target,mbit,nodes,links):
    if source==target:
        return 0.0,[source]
    queue=[(0.0,source,[source])]; seen=set()
    while queue:
        time,current,path=heapq.heappop(queue)
        if current==target:return time,path
        if current in seen:continue
        seen.add(current)
        for link in links:
            if link['source']!=current:continue
            dest=link['target']; limit=link['max_range_m']
            if limit and math.dist(nodes[current]['position'],nodes[dest]['position'])>limit:continue
            heapq.heappush(queue,(time+link['latency_s']+mbit/link['mbps'],dest,path+[dest]))
    return None,[]


def evaluate(config,position,policy='heft',costs=None,unavailable=()):
    nodes={n['id']:dict(n) for n in config['nodes']}
    nodes['device']['position']=list(position)
    links=config['links']; tasks={t['id']:t for t in config['tasks']}
    active=[ident for ident in nodes if transfer('device',ident,1,nodes,links)[0] is not None
            and transfer(ident,'device',1,nodes,links)[0] is not None]
    candidates=active if policy=='heft' else ['device'] if policy=='local' else [n for n in nodes if nodes[n]['kind']==policy][:1]
    candidates=[n for n in candidates if n in active and n not in unavailable]
    if not candidates:
        return dict(policy=policy,status='unreachable',rows=[],makespan_s=None,energy_j=None,
                    deadline_met=0,task_count=len(tasks),transfers=[],loads={})
    if policy in ('edge','cloud'):
        # Fixed deployment baseline, same node for the entire DAG.
        candidates=[candidates[0]]
    agents=list(dict.fromkeys(['device']+candidates))
    huge=1e9
    source='__input__'; sink='__return__'
    successors={t:[] for t in tasks}
    for t in tasks.values():
        for p in t['parents']:successors[p].append(t['id'])
    roots=[t for t in tasks if not tasks[t]['parents']]
    leaves=[t for t in tasks if not successors[t]]
    successors[source]=roots;successors[sink]=[]
    for t in leaves:successors[t].append(sink)

    @lru_cache(maxsize=None)
    def cost(job,node):
        if job in (source,sink):return 1e-9 if node=='device' else huge
        return (costs or {}).get(job,{}).get(node,tasks[job]['gcycles']/nodes[node]['gcycles_s']) if node in candidates else huge

    def size(a,b):
        if a==source:return tasks[b]['input_mbit']
        return tasks[a]['output_mbit']

    @lru_cache(maxsize=None)
    def comm(a,b,origin,dest):
        time,_=transfer(origin,dest,size(a,b),nodes,links)
        return huge if time is None else time

    # Preserve upstream HEFT insertion scheduling. Existing reservations are injected
    # into the initial resource calendars, using upstream rank/allocate primitives.
    from functools import partial
    from .vendor.heft.core import ranku,allocate,Event
    from .vendor.heft.util import reverse_dict
    order={n:([Event('__busy__'+n,0,nodes[n]['busy_s'])] if nodes[n]['busy_s'] else []) for n in agents}
    assignment={}; prec=reverse_dict(successors)
    rank=partial(ranku,agents=agents,succ=successors,compcost=cost,commcost=comm)
    for job in sorted(successors,key=lambda j:(rank(j),j),reverse=True):
        allocate(job,order,assignment,prec,cost,comm)
    events={e.job:e for seq in order.values() for e in seq if not e.job.startswith('__busy__')}
    if any(e.end>=huge for e in events.values()):
        return dict(policy=policy,status='infeasible',rows=[],makespan_s=None,energy_j=None,
                    deadline_met=0,task_count=len(tasks),transfers=[],loads={})
    rows=[]; flows=[]
    for a,children in successors.items():
        for b in children:
            origin,dest=assignment[a],assignment[b]
            delay,path=transfer(origin,dest,size(a,b),nodes,links)
            if origin!=dest:
                flows.append(dict(from_task=a,to_task=b,source=origin,target=dest,path=path,
                                  start_s=events[a].end,end_s=events[a].end+delay,mbit=size(a,b)))
    for t in tasks.values():
        ident=t['id'];node=assignment[ident];e=events[ident]
        parents=prec.get(ident,[])
        ready=max((events[p].end+comm(p,ident,assignment[p],node) for p in parents),default=0)
        returned=e.end
        if ident in leaves:returned+=comm(ident,sink,node,'device')
        rows.append(dict(id=ident,node=node,start_s=e.start,end_s=e.end,ready_s=ready,
                         queue_s=max(0,e.start-ready),compute_s=cost(ident,node),
                         deadline_s=t['deadline_s'],deadline_met=returned<=t['deadline_s'],
                         result_available_s=returned,energy_j=cost(ident,node)*nodes[node]['power_w']))
    makespan=events[sink].end
    loads={node:sum(r['compute_s'] for r in rows if r['node']==node)/max(makespan,1e-9) for node in agents}
    return dict(policy=policy,status='ok',rows=sorted(rows,key=lambda r:r['start_s']),
                makespan_s=makespan,energy_j=sum(r['energy_j'] for r in rows),
                deadline_met=sum(r['deadline_met'] for r in rows),task_count=len(tasks),
                transfers=flows,loads=loads,active_nodes=active)
