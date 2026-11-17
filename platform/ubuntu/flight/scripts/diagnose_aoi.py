"""Paired interventions on the saved single-aircraft actor; write all trace summaries."""
import csv
import sys
from pathlib import Path
import numpy as np
import torch
from train_aoi import Sensors, MODEL

def main():
    actors=[('previous',MODEL),('extended',Path(sys.argv[1])/'AttnSAC/actor.pt')]
    with (Path(sys.argv[1])/'diagnostic.csv').open('w') as stream:
        writer=csv.writer(stream)
        writer.writerow(['actor','rule','trace','mean_age','largest_sensor_share','redundant_fraction'])
        for label,path in actors:
            actor=torch.jit.load(str(path)).eval()
            for rule in ('actor','age-priority','age-priority+actor'):
                for seed in range(100,108):
                    env=Sensors()
                    obs,_=env.reset(seed=seed)
                    counts=np.zeros(3)
                    ages=[]
                    redundant=0
                    for _ in range(64):
                        with torch.no_grad():
                            logits=actor(torch.from_numpy(obs[None])).numpy()[0]
                        features=obs.reshape(3,3)
                        gain=features[:,2]*np.maximum(0,20*features[:,0]-6*features[:,1])
                        action=logits if rule=='actor' else gain if rule=='age-priority' else gain+.25*logits
                        sensor=int(np.argmax(action))
                        redundant+=int(env.queue.generated[sensor]==env.queue.received[sensor])
                        counts[sensor]+=1
                        obs,_,_,_,info=env.step(action)
                        ages.append(info['mean_age_s'])
                    writer.writerow([label,rule,seed,np.mean(ages),counts.max()/64,redundant/64])
if __name__=='__main__':
    main()
