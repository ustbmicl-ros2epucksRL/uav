"""Local HTTP adapter usable from an agent framework without importing the platform."""
import argparse
import ipaddress
import json
from urllib.parse import urlsplit

import httpx


class PlatformTools:
    def __init__(self, base_url='http://127.0.0.1:8772/network', transport=None):
        url = urlsplit(base_url)
        try:
            local = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname or '').is_loopback
        except ValueError:
            local = False
        if (not local or url.scheme not in {'http', 'https'} or url.username or url.password
                or url.query or url.fragment or url.path.rstrip('/') not in {'', '/network'}):
            raise ValueError('请使用本机平台地址；远程服务请先通过受控本机转发接入')
        self.base_url = base_url.rstrip('/') + '/api/research-agent'
        self.transport = transport

    def request(self, method, path, body=None):
        with httpx.Client(timeout=60, trust_env=False, transport=self.transport) as client:
            response = client.request(method, self.base_url + path, json=body)
            response.raise_for_status()
            return response.json()

    def catalog(self):
        return self.request('GET', '/tools')

    def call(self, name, scene='urban', expected_revision=None):
        if name not in {'scene_snapshot', 'diagnose_coverage', 'planning_records', 'assessment_results'}:
            raise ValueError('工具不在平台只读白名单中')
        return self.request('POST', '/tools/' + name, {'scene': scene, 'expected_revision': expected_revision})

    def start(self, question, scene='urban', review=True):
        return self.request('POST', '/runs', {'question': question, 'scene': scene, 'workflow': 'platform',
                                             'mode': 'review' if review else 'single'})

    def read(self, run_id):
        if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise ValueError('无效的运行编号')
        return self.request('GET', '/runs/' + run_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8772/network')
    parser.add_argument('--scene', choices=['urban', 'corridor', 'electromagnetic'], default='urban')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--tool', choices=['scene_snapshot', 'diagnose_coverage', 'planning_records', 'assessment_results'])
    actions.add_argument('--question')
    actions.add_argument('--run-id')
    args = parser.parse_args()
    client = PlatformTools(args.base_url)
    if args.tool:
        result = client.call(args.tool, args.scene)
    elif args.question:
        result = client.start(args.question, args.scene)
    elif args.run_id:
        result = client.read(args.run_id)
    else:
        result = client.catalog()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
