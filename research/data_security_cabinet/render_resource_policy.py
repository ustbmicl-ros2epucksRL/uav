#!/usr/bin/env python3
"""Generate deny-by-default KBS policy for the pinned EAR-04 schema.
Values must come from independently approved builds, not untrusted guest claims.
This does not replace the AS policy, RVPS references, or hardware verification.
"""
import argparse
import json
from pathlib import Path
import re


def render(tee, init_data, measurement, namespace):
    if tee not in ('snp', 'tdx'):
        raise ValueError('Only SNP/TDX are supported in this CPU-only policy')
    length = 64 if tee == 'snp' else 96
    if not isinstance(init_data, str) or not re.fullmatch('[a-f0-9]{%d}' % length, init_data) or set(init_data) == {'0'}:
        raise ValueError('Nonzero approved HOST_DATA/MRCONFIGID lowercase hex is required')
    if not re.fullmatch('[a-fA-F0-9]{96}', measurement):
        raise ValueError('Expected 48-byte hex measurement from approved build')
    if not re.fullmatch('[A-Za-z0-9_-]+', namespace):
        raise ValueError('Invalid cabinet namespace')
    measurement_field = 'measurement' if tee == 'snp' else 'quote.body.mr_td'
    debug_field = 'policy_debug_allowed' if tee == 'snp' else 'td_attributes.debug'
    return f'''# Schema pinned to Trustee commit 22ca43188fb2db632d43d3d5a164e0edbd5ab021.
# init_data MUST bind the approved workload and locked execution policy.
package policy
import rego.v1

default allow = false

allow if {{
    data.plugin == "resource"
    count(data["resource-path"]) == 3
    data["resource-path"][0] == {json.dumps(namespace)}
    data["resource-path"][1] == "keys"
    data["resource-path"][2] in {{"kek-v1", "audit-v1", "operator-v1", "state-v1", "checkpoint-v1"}}
    count(input.submods) == 1
    cpu := input.submods.cpu0
    cpu.ear_status == "affirming"
    cpu.ear_attester_claims.tee == {json.dumps(tee)}
    cpu.ear_attester_claims.init_data == {json.dumps(init_data)}
    cpu.ear_attester_claims.claims.{measurement_field} == {json.dumps(measurement)}
    cpu.ear_attester_claims.claims.{debug_field} == false
}}
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tee', choices=['snp', 'tdx'], required=True)
    parser.add_argument('--init-data', required=True)
    parser.add_argument('--measurement', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    with Path(args.output).open('x') as output:
        output.write(render(args.tee, args.init_data, args.measurement, args.namespace))
