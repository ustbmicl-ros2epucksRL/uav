"""Train matched SB3 policies and evaluate on common held-out channel traces."""
import argparse
import csv
import sys
from pathlib import Path
import gymnasium as gym
import numpy as np
import torch
from torch import nn
from stable_baselines3 import SAC, PPO, DDPG
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.aoi_policy import MODEL, StatusQueue

class Sensors(gym.Env):
    observation_space = gym.spaces.Box(0,1,(9,),dtype=np.float32)
    action_space = gym.spaces.Box(-1,1,(3,),dtype=np.float32)

    def obs(self):
        return self.queue.observe(20+20*np.sin(self.queue.tick/9+self.phase)).reshape(-1)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.queue = StatusQueue(int(self.np_random.integers(2**31)))
        self.phase = self.np_random.uniform(0,6.28)
        return self.obs(), {}

    def step(self, action):
        event = self.queue.apply(action,self.obs().reshape(3,3))
        return self.obs(), -event['mean_age_s']/10, False, self.queue.tick==64, event

class FreshSensors(Sensors):
    """Use the expected age reduction as a prior, with learned residual priority."""
    def step(self, action):
        tokens=self.obs().reshape(3,3)
        gain=tokens[:,2]*np.maximum(0,20*tokens[:,0]-6*tokens[:,1])
        return super().step(gain+.25*action)

class Attention(BaseFeaturesExtractor):
    def __init__(self, observation_space):
        super().__init__(observation_space, observation_space.shape[0]//3*8)
        self.embed = nn.Linear(3,8)
        self.attend = nn.MultiheadAttention(8,2,batch_first=True)

    def forward(self, x):
        tokens = self.embed(x.reshape(x.shape[0],-1,3))
        values,_ = self.attend(tokens,tokens,tokens,need_weights=False)
        return (values+tokens).flatten(1)

class Actor(nn.Module):
    def __init__(self, actor):
        super().__init__()
        self.actor=actor

    def forward(self, x):
        return self.actor(x,deterministic=True)

def main():
    torch.set_num_threads(1)
    MODEL.parent.mkdir(parents=True,exist_ok=True)
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('--algorithm',choices=['AttnSAC','SAC','PPO','DDPG'])
    parser.add_argument('--steps',type=int,default=1024)
    parser.add_argument('--seeds',type=int,nargs='+',default=[11,23,37])
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--joint',action='store_true')
    mode.add_argument('--fresh-prior',action='store_true')
    args=parser.parse_args()
    if args.steps < 128 or args.steps % 128:
        parser.error('steps must be a positive multiple of 128')
    out=args.output
    if args.joint:
        from joint_sensors import JointSensors
    environment=JointSensors if args.joint else FreshSensors if args.fresh_prior else Sensors
    out.mkdir(parents=True,exist_ok=True)
    with (out/'evaluation.csv').open('w') as stream:
        writer=csv.writer(stream)
        writer.writerow(['algorithm','train_seed','steps','test_seed','slot','mean_aoi_s','sensor','delivered'])
        for name,cls in [('AttnSAC',SAC),('SAC',SAC),('PPO',PPO),('DDPG',DDPG)]:
            if args.algorithm and name!=args.algorithm:
                continue
            for seed in args.seeds:
                policy=dict(net_arch=[32,32])
                if name=='AttnSAC':
                    policy['features_extractor_class']=Attention
                opts=dict(n_steps=64,n_epochs=5) if cls==PPO else dict(learning_starts=64,buffer_size=4096)
                model=cls('MlpPolicy',environment(),seed=seed,device='cpu',policy_kwargs=policy,batch_size=32,**opts)
                for steps in (0,args.steps//2,args.steps):
                    if steps:
                        model.learn(args.steps//2,reset_num_timesteps=False)
                    env=environment()
                    for test_seed in range(100,108):
                        obs,_=env.reset(seed=test_seed)
                        for slot in range(64):
                            act,_=model.predict(obs,deterministic=True)
                            obs,_,_,_,info=env.step(act)
                            writer.writerow([name,seed,steps,test_seed,slot+1,info['mean_age_s'],info['sensor'],info['delivered']])
                if name=='AttnSAC' and seed==11:
                    actor=Actor(model.actor).eval()
                    dim=env.observation_space.shape[0]
                    example=torch.rand(1,dim)
                    traced=torch.jit.trace(actor,example,check_trace=False)
                    test=torch.rand(32,dim)
                    torch.testing.assert_close(traced(test),actor(test))
                    traced.save(str(out/'actor.pt' if args.algorithm or args.joint else MODEL))
                stream.flush()
                print(name,seed,flush=True)

if __name__=='__main__':
    main()
