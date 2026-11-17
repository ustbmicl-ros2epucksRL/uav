"""Opt-in synthetic status-update experiment for managed SITL."""
from pathlib import Path
import hashlib
import numpy as np

MODEL = Path(__file__).resolve().parent.parent/'data/aoi-policy/actor.pt'


class StatusQueue:
    """Three periodic sensors, latest sample buffer, one transmission per second."""
    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.tick = 0
        self.received = np.zeros(3, dtype=int)
        self.generated = self.received.copy()

    def observe(self, height):
        quality = np.clip(np.array([.84, .71, .88]) + .10*np.sin(self.tick/7 + np.arange(3) + height/40), .1, .99)
        return np.stack([np.minimum((self.tick-self.received)/20,1),
                         np.minimum((self.tick-self.generated)/6,1), quality], axis=1).astype('float32')

    def apply(self, action, obs):
        sensor = int(np.argmax(action))
        draws = self.rng.random(3)
        delivered = bool(draws[sensor] < obs[sensor,2])
        if delivered:
            self.received[sensor] = self.generated[sensor]
        self.tick += 1
        fresh = self.tick % np.array([2,3,4]) == 0
        self.generated[fresh] = self.tick
        return dict(slot=self.tick, sensor=sensor+1, delivered=delivered,
                    mean_age_s=float(np.mean(self.tick-self.received)))


def sample(state, now):
    binding = state.get('execution_binding') or {}
    if binding.get('purpose') != 'AoI 策略调度仿真' or not state['telemetry'].get('armed'):
        return None
    import torch
    run = state.get('_aoi_run')
    if not run or run['id'] != binding['execution_id']:
        actor = torch.jit.load(str(MODEL), map_location='cpu').eval()
        run = dict(id=binding['execution_id'], actor=actor, queue=StatusQueue(1701),
                   digest=hashlib.sha256(MODEL.read_bytes()).hexdigest(), last=0)
        state['_aoi_run'] = run
    if now-run['last'] < 1:
        return run['event']
    altitude = float(state['telemetry']['altitude'])
    obs = run['queue'].observe(altitude)
    with torch.no_grad():
        action = run['actor'](torch.from_numpy(obs.reshape(1,-1))).numpy()[0]
    run['event'] = dict(run['queue'].apply(action, obs), at=now, model_sha256=run['digest'],
                        execution_id=run['id'], height_m=altitude, observation=obs.tolist(), action=action.tolist())
    run['last'] = now
    return run['event']
