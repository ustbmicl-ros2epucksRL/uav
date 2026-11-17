"""Measured cost adapter for existing HEFT, not a new scheduling algorithm."""
import math


class Feedback:
    def __init__(self):
        self.samples = {}
        self.revision = 0

    def observe(self, task, node, response, roundtrip_s):
        cold = max(0, response.get('output', {}).get('model_load_s', 0))
        value = max(.001, roundtrip_s - cold)
        if not math.isfinite(value):
            return
        key = (task, node)
        old = self.samples.get(key)
        self.samples[key] = dict(seconds=value if old is None else .3*value+.7*old['seconds'],
                                 count=1+(old or {}).get('count', 0), cold_s=cold)
        self.revision += 1

    def matrix(self, config, loads):
        result, evidence = {}, {}
        for task in config['tasks']:
            ident = task['id']
            pooled = [v['seconds'] for (t, _), v in self.samples.items() if t == ident]
            result[ident], evidence[ident] = {}, {}
            for node in config['nodes']:
                key = node['id']
                observed = self.samples.get((ident, key))
                base = observed['seconds'] if observed else sum(pooled)/len(pooled) if pooled else task['gcycles']/node['gcycles_s']
                # Measured local-worker occupancy penalizes saturated execution slots.
                load = min(1, max(0, loads.get(key, 0)))
                result[ident][key] = base*(1+load)
                evidence[ident][key] = dict(base_s=base, estimated_s=result[ident][key],
                    load_10s=load, samples=(observed or {}).get('count', 0),
                    source='node-ewma' if observed else 'local-peer-ewma' if pooled else 'configured')
        return result, evidence
