"""Reproducible measurement simulation and an independent position-track baseline.

The tracker consumes standardised XYZ observations, never simulation truth IDs.
RF bearing/range inversion and radar raw-signal processing are NOT implemented.
"""
import hashlib
import json
import math
import numpy as np
from scipy.optimize import linear_sum_assignment
from . import coverage_core as core
from .scenes import trajectories

SIGMA = {"RADAR": 24., "RF": 48., "EO": 12., "IR": 18., "ADSB": 6.}


def run_id(scene, mode, rules):
    value = json.dumps([scene, mode, rules], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def simulate(scene, interference=True):
    truth = trajectories(scene)["tracks"]
    truth[0].update(emitting=True, cooperative_id="DEMO-LOGISTICS-01")
    truth[1].update(emitting=False, cooperative_id=None)
    truth[2].update(emitting=True, cooperative_id=None)
    request=core.default_volume()
    probabilities={target["id"]:{d["id"]:core.device_field(scene,np.asarray(target["points"]),d,request)[0] for d in scene["devices"]} for target in truth}
    frames = []
    for t in range(121):
        observations = []
        for target in truth:
            point = np.array(target["points"][t])
            for d in scene["devices"]:
                if d["status"] != "online":
                    continue
                if d["type"] == "ADSB":
                    visible = target["cooperative_id"] and np.linalg.norm(point-np.array([d["x"],d["y"],d["z"]])) <= d["range"]
                else:
                    visible = probabilities[target["id"]][d["id"]][t]>0
                if not visible or (d["type"] == "RF" and not target["emitting"]):
                    continue
                # Common random numbers make before/after comparisons repeatable.
                key = f'{scene["seed"]}:{t}:{target["id"]}:{d["id"]}'
                rng = np.random.default_rng(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big'))
                stress = interference and scene["id"] == "electromagnetic" and d["type"] in {"RADAR","RF"}
                local = max(0., 1.-float(np.linalg.norm(point[:2]-[1000,1000]))/950.) if stress else 0.
                probability = (.92 if d["type"]=="ADSB" else probabilities[target["id"]][d["id"]][t]) * (1-.5*local)
                if rng.random() > probability:
                    continue
                sigma = SIGMA[d["type"]]*(1+2.5*local)
                measured = point+rng.normal(0,sigma,3)
                observations.append({"sensor_id":d["id"],"type":d["type"],"position":measured.round(3).tolist(),
                                     "sigma":round(sigma,3),"cooperative_id":target["cooperative_id"] if d["type"]=="ADSB" else None})
        frames.append({"time":t,"observations":observations})
    return frames, truth


def fuse(observations):
    """Conservative spatial clustering: one observation per sensor per cluster."""
    groups=[]
    for obs in sorted(observations, key=lambda o:o["sigma"]):
        pos=np.array(obs["position"],float)
        compatible=[]
        for i,g in enumerate(groups):
            ids={o["sensor_id"] for o in g}
            coop={o.get("cooperative_id") for o in g if o.get("cooperative_id")}
            if obs["sensor_id"] in ids or (coop and obs.get("cooperative_id") and obs["cooperative_id"] not in coop):
                continue
            center=np.average([o["position"] for o in g],axis=0,weights=[1/o["sigma"]**2 for o in g])
            dist=np.linalg.norm(center-pos)
            if dist < min(180.,max(65.,3*obs["sigma"])):
                compatible.append((dist,i))
        if compatible:
            groups[min(compatible)[1]].append(obs)
        else:
            groups.append([obs])
    out=[]
    for g in groups:
        weights=np.array([1/o["sigma"]**2 for o in g])
        out.append({"position":np.average([o["position"] for o in g],axis=0,weights=weights),
                    "variance":float(1/weights.sum()),"sources":[o["sensor_id"] for o in g],
                    "cooperative_id":next((o["cooperative_id"] for o in g if o.get("cooperative_id")),None)})
    return out


def track(frames, mode="fusion"):
    """Constant-velocity Kalman filter with gated global nearest-neighbour association."""
    tracks=[]
    history=[]
    H=np.c_[np.eye(3),np.zeros((3,3))]
    last_time=None
    baseline_sensor=next((o["sensor_id"] for f in frames for o in f["observations"] if o["type"]=="RADAR"),None)
    for frame in frames:
        t=frame["time"]
        dt=1. if last_time is None else t-last_time
        last_time=t
        F=np.eye(6); F[:3,3:]=np.eye(3)*dt
        G=np.r_[np.eye(3)*dt*dt/2,np.eye(3)*dt]
        Q=G@G.T*9.
        active=[a for a in tracks if t-a["last_seen"]<=5]
        for a in active:
            a["x"]=F@a["x"]; a["P"]=F@a["P"]@F.T+Q
            a["observed"]=False; a["sources"]=[]
        obs=frame["observations"]
        if mode=="single":
            # Select a fixed sensor for the complete run, never the best sensor at each time.
            obs=[o for o in obs if o["sensor_id"]==baseline_sensor]
        clusters=fuse(obs)
        assigned=set(); matched=set()
        if active and clusters:
            costs=np.full((len(active),len(clusters)),1e6)
            for i,a in enumerate(active):
                for j,c in enumerate(clusters):
                    if a["cooperative_id"] and c["cooperative_id"] and a["cooperative_id"]!=c["cooperative_id"]:
                        continue
                    delta=c["position"]-a["x"][:3]
                    S=H@a["P"]@H.T+np.eye(3)*c["variance"]
                    mahal=float(delta@np.linalg.solve(S,delta))
                    if mahal<25 and np.linalg.norm(delta)<250:
                        costs[i,j]=mahal
            ii,jj=linear_sum_assignment(costs)
            for i,j in zip(ii,jj):
                if costs[i,j]>=1e6:
                    continue
                a,c=active[i],clusters[j]
                R=np.eye(3)*c["variance"]
                K=a["P"]@H.T@np.linalg.inv(H@a["P"]@H.T+R)
                a["x"]+=K@(c["position"]-H@a["x"])
                I=np.eye(6)-K@H
                a["P"]=I@a["P"]@I.T+K@R@K.T
                a.update(last_seen=t,observed=True,sources=c["sources"],cooperative_id=c["cooperative_id"] or a["cooperative_id"])
                a["hits"]+=1
                assigned.add(j); matched.add(i)
        for j,c in enumerate(clusters):
            if j in assigned:
                continue
            # A leftover cluster close to an existing track is often an unassociated
            # noisy modality, not evidence of a new target. Avoid duplicate births.
            if any(np.linalg.norm(c['position']-a['x'][:3])<180 and
                   (not c['cooperative_id'] or not a['cooperative_id'] or c['cooperative_id']==a['cooperative_id']) for a in active):
                continue
            a={"id":f'TRK-{len(tracks)+1:03d}',"x":np.r_[c["position"],[0.,0.,0.]],
               "P":np.diag([c["variance"]]*3+[400.]*3),"last_seen":t,"observed":True,
               "sources":c["sources"],"cooperative_id":c["cooperative_id"],"hits":1}
            tracks.append(a); active.append(a)
        output=[]
        for a in active:
            output.append({"id":a["id"],"position":a["x"][:3].round(2).tolist(),"velocity":a["x"][3:].round(2).tolist(),
                           "uncertainty_m":round(math.sqrt(float(np.trace(a["P"][:3,:3]))),2),
                           "observed":a["observed"],"state":"confirmed" if a["hits"]>=3 and a["observed"] else "tentative" if a["observed"] else "coasting",
                           "sources":a["sources"],"cooperative_id":a["cooperative_id"]})
        history.append({"time":t,"tracks":output,"observation_count":len(obs),"observations":obs})
    return history


def risks(history, rules, registry):
    events=[]; emitted=set(); paths={}
    for frame in history:
        t=frame["time"]
        for a in frame["tracks"]:
            if a["state"]!="confirmed":
                continue
            pos=a["position"]; cid=a["cooperative_id"]
            entry=registry.get(cid) if cid else None
            status=entry["status"] if entry else "unknown"
            path=paths.setdefault(a["id"],[]); path.append((t,pos))
            triggers=[]
            if status=="unknown":
                triggers.append(("identity","关注","身份与任务待核验","未匹配演示任务登记。需要核对登记与飞行活动信息"))
            if status=="denied":
                triggers.append(("authorization","高","登记明确标记不允许","来自输入登记状态，需业务人员核验其有效性"))
            z=rules["zone"]
            inside=z["x0"]<=pos[0]<=z["x1"] and z["y0"]<=pos[1]<=z["y1"] and z["z0"]<=pos[2]<=z["z1"]
            if inside:
                triggers.append(("zone","高","进入重点监测区域","融合位置进入用户配置的三维电子围栏"))
            if pos[2]>rules["altitude_limit"]:
                triggers.append(("altitude","中","超过场景高度阈值",f'相对场景坐标 z 超过 {rules["altitude_limit"]} m，非自动法律认定'))
            if entry and entry.get("route_y") is not None and abs(pos[1]-entry["route_y"])>entry.get("route_tolerance",120):
                triggers.append(("deviation","中","偏离登记航路","融合位置超出演示航路横向容差"))
            recent=[p for tt,p in path if t-tt<=30]
            if len(recent)>=25 and np.linalg.norm(np.array(recent[0])-pos)<rules["loiter_radius"]:
                triggers.append(("loiter","中","疑似持续盘旋或滞留","近 30 秒多次观测位移较小，需回放核验"))
            for kind,level,title,reason in triggers:
                key=(a["id"],kind)
                if key in emitted:
                    continue
                emitted.add(key)
                events.append({"id":f'{a["id"]}-{kind}',"track_id":a["id"],"kind":kind,"level":level,"title":title,
                               "time":t,"position":pos,"sources":a["sources"],"uncertainty_m":a["uncertainty_m"],
                               "identity_status":status,"reason":reason,"status":"pending"})
    return events


def evaluate(history, truth):
    """Offline one-to-one truth matching. Coasting predictions never count as detections."""
    count=len(truth)*len(history); errors=[]; sample_errors={}; seen={a["id"]:[] for a in truth};
    matches={a["id"]:[] for a in truth}; false_positions=0
    for frame in history:
        t=int(frame["time"])
        obs=[a for a in frame["tracks"] if a["observed"]]
        hit={}
        if obs:
            costs=np.array([[np.linalg.norm(np.array(a["position"])-b["points"][t]) for a in obs] for b in truth])
            ii,jj=linear_sum_assignment(costs)
            used=set()
            for i,j in zip(ii,jj):
                if costs[i,j]<=200:
                    errors.append(float(costs[i,j])); hit[truth[i]["id"]]=obs[j]["id"];used.add(j)
                    sample_errors[f'{truth[i]["id"]}:{t}']=float(costs[i,j])
            false_positions+=len(obs)-len(used)
        for b in truth:
            seen[b["id"]].append(b["id"] in hit)
            matches[b["id"]].append(hit.get(b["id"]))
    per=[]
    for b in truth:
        values=seen[b["id"]]; gap=longest=0; switches=0;previous=None
        for valid,tid in zip(values,matches[b["id"]]):
            gap=0 if valid else gap+1;longest=max(longest,gap)
            if tid:
                if previous and previous!=tid:switches+=1
                previous=tid
        per.append({"id":b["id"],"label":b["label"],"observed_rate":round(sum(values)/len(values),4),
                    "longest_gap_s":longest,"identity_switches":switches,"track_fragments":len(set(x for x in matches[b["id"]] if x))})
    return {"observed_rate":round(len(errors)/max(1,count),4),"position_rmse_m":round(math.sqrt(np.mean(np.square(errors))),2) if errors else None,
            "matched_samples":len(errors),"truth_samples":count,"unmatched_estimates":false_positions,"per_target":per,
            "matched_sample_errors_m":sample_errors,
            "definition":"按每秒目标样本计算观测率；仅当有量测更新且估计距真值≤200 m 时匹配。RMSE仅覆盖匹配样本。最长缺测包含首尾，预测滑行不计入观测率。"}


def compare_common_samples(before,after):
    a=before['matched_sample_errors_m'];b=after['matched_sample_errors_m'];keys=set(a)&set(b)
    return {'samples':len(keys),'before_rmse_m':round(math.sqrt(sum(a[k]**2 for k in keys)/len(keys)),2) if keys else None,
            'after_rmse_m':round(math.sqrt(sum(b[k]**2 for k in keys)/len(keys)),2) if keys else None,
            'definition':'仅比较前后都成功匹配的相同目标、相同时间样本。新增观测样本不进入此误差比较。'}


def execute(scene, rules, registry, mode="fusion"):
    frames,truth=simulate(scene,rules.get("interference",True))
    history=track(frames,mode)
    events=risks(history,rules,registry)
    metrics=evaluate(history,truth)
    metrics['risk']=evaluate_risks(events,truth,rules)
    result={"run_id":run_id(scene,mode,{**rules,"registry":registry}),"mode":mode,"data_mode":"synthetic_measurements",
            "frames":history,"events":events,"truth":truth,"metrics":metrics,
            "limitations":["合成园区、设备参数及标准化 XYZ 量测，未接入真实传感器或审批数据。",
                           "干扰为预设丢测与噪声模型，未实现电磁传播或干扰协同增益模型。",
                           "采用空间聚类与卡尔曼跟踪基线，近距离交汇与长遮挡可能误关联或丢轨。",
                           "告警为用户配置规则，身份未知不等于违法飞行，重点区不是法定管制空域。"]}
    return result


def evaluate_risks(events,truth,rules):
    """Evaluate only geometric rule episodes for which independent truth exists."""
    episodes=[]
    z=rules['zone']
    for target in truth:
        for kind in ['zone','altitude']:
            flags=[z['x0']<=x<=z['x1'] and z['y0']<=y<=z['y1'] and z['z0']<=zz<=z['z1'] if kind=='zone' else zz>rules['altitude_limit'] for x,y,zz in target['points']]
            start=None
            for t,value in enumerate(flags+[False]):
                if value and start is None:start=t
                if not value and start is not None:episodes.append({'target':target['id'],'kind':kind,'start':start,'end':t-1});start=None
    matched=set();delays=[];false=0
    for event in events:
        if event['kind'] not in {'zone','altitude'}:continue
        t=int(event['time']);nearest=min(truth,key=lambda a:np.linalg.norm(np.array(a['points'][t])-event['position']))
        if np.linalg.norm(np.array(nearest['points'][t])-event['position'])>200:false+=1;continue
        eligible=[(i,ep) for i,ep in enumerate(episodes) if ep['target']==nearest['id'] and ep['kind']==event['kind'] and ep['start']<=t<=ep['end']]
        if not eligible:false+=1;continue
        i,ep=eligible[0]
        if i not in matched:matched.add(i);delays.append(t-ep['start'])
    return {'episodes':len(episodes),'detected_episodes':len(matched),'recall':round(len(matched)/len(episodes),4) if episodes else None,
            'mean_delay_s':round(float(np.mean(delays)),2) if delays else None,'false_geometric_events':false,
            'definition':'只评测重点区进入与高度阈值事件。连续满足规则的真值区间作为一次事件；区间内、距目标真值≤200 m 的告警计为发现。身份与任务合规性无实测真值，不评准确率。'}


def optimize_tasks(scene,budget,target,rules=None):
    """Greedy task-weighted observation-information gain over declared test routes.

    This is a surrogate objective, not an optimal risk-aware planner. Actual tracking
    metrics are recomputed afterwards and are allowed to become worse.
    """
    from .analysis import optimize
    proposal=optimize(scene,0,target)
    truth=trajectories(scene)['tracks']
    points=np.array([t['points'] for t in truth])
    request=core.default_volume()
    zone=(rules or {}).get('zone',scene['zone']);weights=np.ones(points.shape[:2]);weights[1:]*=1.5
    weights*=np.where((points[:,:,0]>=zone['x0'])&(points[:,:,0]<=zone['x1'])&(points[:,:,1]>=zone['y0'])&(points[:,:,1]<=zone['y1']),2.,1.)
    def information(d):
        if d['status']!='online':return np.zeros(weights.shape)
        if d['type']=='ADSB':
            vis=np.linalg.norm(points-np.array([d['x'],d['y'],d['z']]),axis=2)<=d['range'];vis[1:]=False
        else:
            pd=core.device_field(scene,points.reshape(-1,3),d,request)[0].reshape(weights.shape)
            vis=pd>0
            if d['type']=='RF':vis[1]=False
        sigma=np.full(weights.shape,SIGMA[d['type']]);prob=np.full(weights.shape,.92) if d['type']=='ADSB' else pd.copy()
        if (rules or {}).get('interference',True) and scene['id']=='electromagnetic' and d['type'] in {'RF','RADAR'}:
            local=np.maximum(0,1.-np.linalg.norm(points[:,:,:2]-[1000,1000],axis=2)/950.)
            sigma*=1+2.5*local;prob*=1-.5*local
        return vis*prob*(24/sigma)**2
    def score(info):
        # A saturating information proxy rewards first detection and useful sensor complementarity.
        return float(np.sum(weights*(1-np.exp(-info)))/weights.sum())
    total=sum((information(d) for d in scene['devices']),np.zeros(weights.shape));before=score(total)
    candidates=[(d,information(d)) for d in scene['candidates'] if d['id'] not in {s['id'] for s in scene['devices']}]
    chosen=[];spent=0.
    while candidates:
        available=[((score(total+info)-score(total))/max(d['cost'],.01),d,info) for d,info in candidates if spent+d['cost']<=budget]
        if not available:break
        gain,d,info=max(available,key=lambda x:x[0])
        if gain<=1e-8:break
        chosen.append(d);spent+=d['cost'];total+=info;candidates=[(dd,ii) for dd,ii in candidates if dd['id']!=d['id']]
    after=__import__('copy').deepcopy(scene);after['devices']+=chosen
    from .analysis import compute
    proposal.update(selected=chosen,budget=budget,added_cost=spent,after=compute(after)['stats'],
                    method='声明飞行任务上的加权观测信息增益贪心补点',objective='task',
                    task_proxy_before=round(before,5),task_proxy_after=round(score(total),5),
                    note='代理目标考虑任务路径、重点区、静默目标与模态误差。未直接优化真实预警时延或连续跟踪，不保证全局最优；实际效果见同任务重算。')
    proposal['target_met']=proposal['after']['coverage']>=target
    return proposal
