"""Execute generated resource policy with OPA; synthetic claims, not evidence.
CABINET_OPA_BIN=/absolute/path/to/opa python test_policy.py
Production KBS uses Regorus: repeat these tests there and with verified tokens.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from render_resource_policy import render


@unittest.skipUnless(os.environ.get('CABINET_OPA_BIN'), 'OPA binary required')
class TestPolicy(unittest.TestCase):
    def check_platform(self, tee):
        with tempfile.TemporaryDirectory(prefix='cabinet-policy-test-') as t:
            root = Path(t)
            policy = root/'policy.rego'
            init_value = 'b'*(64 if tee=='snp' else 96)
            policy.write_text(render(tee, init_value, 'a'*96, 'cabinet'))
            claims = ({'measurement':'a'*96, 'policy_debug_allowed':False} if tee=='snp'
                      else {'quote':{'body':{'mr_td':'a'*96}}, 'td_attributes':{'debug':False}})
            cpu = {'ear_status':'affirming', 'ear_attester_claims':{
                'tee':tee, 'init_data':init_value, 'claims':claims}}
            good_input = {'submods':{'cpu0':cpu}}
            good_data = {'plugin':'resource', 'resource-path':['cabinet','keys','kek-v1']}
            cases = [('approved', good_input, good_data, True)]
            for field, value in [('tee','sample'),('init_data','wrong')]:
                bad = copy.deepcopy(good_input)
                bad['submods']['cpu0']['ear_attester_claims'][field]=value
                cases.append((field,bad,good_data,False))
            bad = copy.deepcopy(good_input);bad['submods']['cpu0']['ear_status']='contraindicated'
            cases.append(('untrusted-tcb',bad,good_data,False))
            bad = copy.deepcopy(good_input);del bad['submods']['cpu0']['ear_attester_claims']['init_data']
            cases.append(('missing-init',bad,good_data,False))
            cases.append(('empty-claims',{},good_data,False))
            bad = copy.deepcopy(good_input)
            hw = bad['submods']['cpu0']['ear_attester_claims']['claims']
            if tee=='snp':hw['measurement']='b'*96
            else:hw['quote']['body']['mr_td']='b'*96
            cases.append(('measurement',bad,good_data,False))
            bad = copy.deepcopy(good_input)
            hw = bad['submods']['cpu0']['ear_attester_claims']['claims']
            if tee=='snp':hw['policy_debug_allowed']=True
            else:hw['td_attributes']['debug']=True
            cases.append(('debug',bad,good_data,False))
            for name, path in [('namespace',['other','keys','kek-v1']),
                               ('resource',['cabinet','keys','unauthorized']),
                               ('path-length',['cabinet','keys'])]:
                data = copy.deepcopy(good_data);data['resource-path']=path
                cases.append((name,good_input,data,False))
            for name, inp, data, allowed in cases:
                with self.subTest(tee=tee, case=name):
                    (root/'input.json').write_text(json.dumps(inp))
                    (root/'data.json').write_text(json.dumps(data))
                    run = subprocess.run([os.environ['CABINET_OPA_BIN'], 'eval','--format=json',
                        '--data',str(policy),'--data',str(root/'data.json'),
                        '--input',str(root/'input.json'),'data.policy.allow'],
                        check=True, capture_output=True, text=True)
                    self.assertEqual(json.loads(run.stdout)['result'][0]['expressions'][0]['value'], allowed)

    def test_snp_policy(self):
        self.check_platform('snp')

    def test_tdx_policy(self):
        self.check_platform('tdx')


if __name__ == '__main__':
    unittest.main(verbosity=2)
