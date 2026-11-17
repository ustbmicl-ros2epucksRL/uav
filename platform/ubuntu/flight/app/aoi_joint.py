"""Two synthetic survey lanes and a shared three-source status queue (local metres)."""
import hashlib
from pathlib import Path
import numpy as np
from .aoi_policy import StatusQueue

MODEL = Path(__file__).resolve().parents[1]/'data/aoi-policy/joint.pt'
PURPOSE = 'AoI 双机巡检仿真 '
LANES = [0., 20.]
SOURCES = np.array([[18.,0.],[45.,10.],[66.,20.]])

class JointQueue(StatusQueue):
    def __init__(self, seed):
        super().__init__(seed)
        self.x = np.array([6.,6.])

    def observation(self):
        distance = np.hypot(self.x[:,None]-SOURCES[:,0],np.array(LANES)[:,None]-SOURCES[:,1])
        self.quality = np.clip(.97-distance/90,.10,.97)
        ages = np.stack([np.minimum((self.tick-self.received)/64,1),
                         np.minimum((self.tick-self.generated)/4,1),
                         np.minimum((self.generated-self.received)/64,1)],axis=1)
        return np.concatenate([ages.flatten(),self.quality.flatten(),[self.x[0]/72,self.x[1]/72,self.tick%12/12]]).astype('float32')

    def move_and_schedule(self, action, measured=False):
        start=self.x.copy()
        self.x=np.clip(start+6*action[:2],0,72)
        # Each lane has one slot. Duplicate requests coalesce into one delivered timestamp.
        sensors=np.argmax(action[2:].reshape(2,3),axis=1)
        draws=self.rng.random((2,3))
        if not measured:
            self.observation()
        delivered=draws[np.arange(2),sensors]<self.quality[np.arange(2),sensors]
        for source in sensors[delivered]:
            self.received[source]=self.generated[source]
        self.tick+=1
        self.generated[self.tick%np.array([2,3,4])==0]=self.tick
        return dict(slot=self.tick,sensor=(sensors+1).tolist(),delivered=delivered.tolist(),
                    start_x=start.tolist(),next_x=self.x.tolist(),mean_age_s=float(np.mean(self.tick-self.received)))

def policy():
    import torch
    return torch.jit.load(str(MODEL),map_location='cpu').eval()

def decide(actor, queue):
    import torch
    obs=queue.observation()
    with torch.no_grad():
        action=actor(torch.from_numpy(obs[None])).numpy()[0]
    return dict(queue.move_and_schedule(action),observation=obs.tolist(),action=action.tolist())

def plans():
    queue=JointQueue(1701)
    actor=policy()
    events=[decide(actor,queue) for _ in range(12)]
    routes=[]
    for i,y in enumerate(LANES):
        points=[(0.,0.,0.),(0.,0.,40.),(6.,y,40.)]
        for event in events[3::4]:
            point=(round(event['next_x'][i],2),y,40.)
            if point!=points[-1]:
                points.append(point)
        points.extend([(0.,0.,40.),(0.,0.,0.)])
        routes.append([dict(x=x,y=y,z=z) for x,y,z in points])
    return routes,events

def sample(state,now):
    binding=state['execution_binding']
    if not state['telemetry'].get('armed'):
        return None
    role=int(binding['purpose'][-1])-1
    run=state.get('_aoi_run')
    if not run or run['id']!=binding['execution_id']:
        run=dict(id=binding['execution_id'],actor=policy(),queue=JointQueue(1701),last=0,
                 digest=hashlib.sha256(MODEL.read_bytes()).hexdigest())
        state['_aoi_run']=run
    if now-run['last']>=1:
        telemetry=state['telemetry']
        anchor=state['anchor']
        east=(telemetry['longitude']-anchor['longitude'])*111320*np.cos(np.radians(anchor['latitude']))
        run['queue'].x[role]=np.clip(east,0,72)
        run['event']=dict(decide(run['actor'],run['queue']),at=now,execution_id=run['id'],
                          model_sha256=run['digest'],role=role+1,actual_east_m=float(east))
        run['last']=now
    return run['event']
