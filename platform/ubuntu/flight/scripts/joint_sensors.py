import gymnasium as gym
import numpy as np
from app.aoi_joint import JointQueue

class JointSensors(gym.Env):
    observation_space=gym.spaces.Box(0,1,(18,),dtype=np.float32)
    action_space=gym.spaces.Box(-1,1,(8,),dtype=np.float32)

    def reset(self,*,seed=None,options=None):
        super().reset(seed=seed)
        self.queue=JointQueue(int(self.np_random.integers(2**31)))
        return self.queue.observation(),{}

    def step(self,action):
        event=self.queue.move_and_schedule(action)
        return self.queue.observation(),-event['mean_age_s']/10,False,self.queue.tick==64,event
