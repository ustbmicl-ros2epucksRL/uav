"""Bounded, wall-clock link emulator. This never changes host network settings.

Directed store-and-forward links share capacity equally among active transfers.
Payloads remain in the coordinator until upload/download simulation completes.
"""
import copy
import math
from .algorithm_compute import transfer


class Network:
    def __init__(self, links):
        self.base = copy.deepcopy(links)
        self.profile = 'normal'
        self.until = None
        self.mbps = 1
        self.latency_s = .2
        self.revision = 0
        self.flows = {}

    def configure(self, profile, now, duration_s=10, mbps=1, latency_ms=200):
        self.profile = profile
        self.until = now+duration_s if profile != 'normal' else None
        self.mbps, self.latency_s = mbps, latency_ms/1000
        self.revision += 1

    def links(self):
        if self.profile == 'disconnected':return []
        result = copy.deepcopy(self.base)
        if self.profile == 'weak':
            for link in result:
                link['mbps'] = min(link['mbps'], self.mbps)
                link['latency_s'] = max(link['latency_s'], self.latency_s)
        return result

    def begin(self, token, source, target, size_bytes, nodes, now):
        size = size_bytes*8/1e6
        _, path = transfer(source,target,size,nodes,self.links())
        flow = dict(token=token,source=source,target=target,path=path,size_bytes=size_bytes,
                    mbit=size,remaining_mbit=size,hop=0,started_at=now,updated_at=now,
                    status='moving' if len(path)>1 else 'completed' if path else 'failed',
                    error=None if path else 'link_unreachable',revision=self.revision)
        links={(l['source'],l['target']):l for l in self.links()}
        flow['latency_left_s']=links[path[0],path[1]]['latency_s'] if len(path)>1 else 0
        self.flows[token]=flow
        return flow

    def advance(self, now, nodes):
        links={(l['source'],l['target']):l for l in self.links()}
        moving=[f for f in self.flows.values() if f['status']=='moving']
        counts={}
        for f in moving:
            # Validate the whole reserved route: a break invalidates this attempt.
            route=list(zip(f['path'],f['path'][1:]))
            if any(e not in links or (links[e]['max_range_m'] and
                   math.dist(nodes[e[0]]['position'],nodes[e[1]]['position'])>links[e]['max_range_m']) for e in route):
                f.update(status='failed',error='link_disconnected');continue
            edge=route[f['hop']]
            counts[edge]=counts.get(edge,0)+1
        for f in moving:
            dt=max(0,now-f['updated_at']);f['updated_at']=now
            if f['status']!='moving':continue
            edge=(f['path'][f['hop']],f['path'][f['hop']+1])
            latency=min(dt,f['latency_left_s']);f['latency_left_s']-=latency;dt-=latency
            f['remaining_mbit']=max(0,f['remaining_mbit']-dt*links[edge]['mbps']/counts[edge])
            if f['remaining_mbit']<=1e-10 and f['latency_left_s']<=1e-10:
                f['hop']+=1
                if f['hop']==len(f['path'])-1:f['status']='completed'
                else:
                    edge=(f['path'][f['hop']],f['path'][f['hop']+1])
                    f['remaining_mbit']=f['mbit'];f['latency_left_s']=links[edge]['latency_s']

    def snapshot(self):
        return dict(profile=self.profile,revision=self.revision,restore_at=self.until,
                    links=[dict(l,enabled=self.profile!='disconnected',
                                effective_mbps=0 if self.profile=='disconnected' else min(l['mbps'],self.mbps) if self.profile=='weak' else l['mbps'],
                                effective_latency_s=max(l['latency_s'],self.latency_s) if self.profile=='weak' else l['latency_s']) for l in self.base],
                    flows=copy.deepcopy(list(self.flows.values())),source='local-link-emulator')
