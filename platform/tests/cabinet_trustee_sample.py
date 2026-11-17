"""Isolated upstream Trustee sample-attester deployment for Mac/Ubuntu QA only."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import time

from app import cabinet_demo as api
from prepare_trustee import prepare, write
from trustee_broker import TRUSTEE_COMMIT


class SampleTrustee:
    def __init__(self, folder, namespace, stack):
        self.folder = folder
        self.namespace = namespace
        prefix = 'cabinet-test-' + secrets.token_hex(5)
        self.kbs = prefix + '-kbs'
        self.reader = prefix + '-reader'
        self.admin = prefix + '-admin'
        network = prefix + '-net'
        source = Path(os.environ['CABINET_TRUSTEE_SOURCE'])
        prepare(source, folder, 'kbs')
        cfg = folder/'kbs/config.toml'
        config = cfg.read_text().replace('type = "coco_as_grpc"\nas_addr = "http://as:50004"', '''type = "coco_as_builtin"
[attestation_service.attestation_token_broker]
duration_min = 1
[attestation_service.attestation_token_broker.signer]
key_path = "/run/cabinet/token.key"
cert_path = "/run/cabinet/token.pem"
[attestation_service.rvps_config]
type = "BuiltIn"''')
        cfg.write_text(config)
        self.run('network', 'create', network)
        stack.callback(self.cleanup,'network','rm',network)
        for name, image in ((self.kbs,'kbs'),(self.reader,'kbs-client-image'),(self.admin,'kbs-client-image')):
            ref = f'ghcr.io/confidential-containers/staged-images/{image}:{TRUSTEE_COMMIT}'
            args = ['create','--network',network,'--name',name]
            if name == self.kbs:
                args += ['-p','127.0.0.1::8443','-e','RUST_LOG=warn,attestation_service=info,actix_web::middleware::logger=info','-e','NO_COLOR=1','--network-alias','kbs',ref,'/usr/local/bin/kbs','--config-file','/run/cabinet/config.toml']
            else:
                args += [ref,'sleep','infinity']
            self.run(*args)
            stack.callback(self.cleanup,'rm','-f',name)
        for name in ('token.key','token.pem'):
            shutil.copyfile(folder/'as'/name,folder/'kbs'/name)
        self.run('cp',str(folder/'kbs'),self.kbs+':/run/cabinet')
        write(folder/'admin/checkpoint-v1',os.urandom(32))
        write(folder/'admin/deny.rego','package policy\ndefault allow = false\n')
        policy = '''package policy
default allow = false
allow if {
    data.plugin == "resource"
    input.submods.cpu0.ear_attester_claims.tee == "sample"
    trace_matches
    count(data["resource-path"]) == 3
    data["resource-path"][0] == %s
    data["resource-path"][1] == "keys"
    data["resource-path"][2] in {"state-v1","operator-v1","audit-v1","checkpoint-v1","kek-v1"}
}
trace_matches if { count(data.query) == 0 }
trace_matches if { data.query.binding == input.submods.cpu0.ear_attester_claims.claims.svn }
''' % json.dumps(namespace)
        write(folder/'admin/sample.rego',policy)
        write(folder/'admin/require-snp.rego', policy.replace('== "sample"', '== "snp"'))
        # Only synthetic inputs needed by the administrative CLI enter this container.
        staging = folder/'provision'
        staging.mkdir(mode=0o700)
        labels = ('state-v1','operator-v1','audit-v1','checkpoint-v1','kek-v1')
        for name in (*labels,'ca.pem','admin-token','sample.rego','require-snp.rego','deny.rego'):
            shutil.copy2(folder/'admin'/name,staging/name)
        self.run('cp',str(staging),self.admin+':/run/admin')
        self.run('cp',str(staging/'ca.pem'),self.reader+':/run/ca.pem')
        self.run('start',self.kbs,self.reader,self.admin)
        self.wait_ready()
        for label in labels:
            self.configure('set-resource','--path',f'{namespace}/keys/{label}','--resource-file','/run/admin/'+label)
        self.policy('sample')
        self.make_reader()

    @staticmethod
    def run(*args):
        # Do not forward CLI output that could contain resource material.
        proc = subprocess.run(['docker',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        if proc.returncode:
            raise RuntimeError(f'Docker {args[0]} failed: '+proc.stderr.decode(errors='replace')[-350:])
        return proc.stdout

    @staticmethod
    def cleanup(*args):
        subprocess.run(['docker',*args],capture_output=True,timeout=45,check=True)

    def configure(self,*args):
        self.run('exec',self.admin,'kbs-client','--url','https://kbs:8443','--cert-file','/run/admin/ca.pem',
                 'config','--admin-token-file','/run/admin/admin-token',*args)

    def policy(self,name):
        self.configure('set-resource-policy','--policy-file',f'/run/admin/{name}.rego')

    def wait_ready(self):
        for attempt in range(30):
            try:
                self.policy('deny')
                return
            except RuntimeError:
                if attempt == 29: raise
                time.sleep(1)

    def make_reader(self):
        docker = shutil.which('docker')
        endpoint = self.run('context','inspect','--format','{{.Endpoints.docker.Host}}').decode().strip()
        if not endpoint.startswith('unix://'):
            raise ValueError('QA requires a local Docker socket')
        self.wrapper = self.folder/'sample-reader'
        # Mac executes the pinned Linux kbs-client in its reader container.
        # No admin credential or resource volume is mounted into that container.
        cmd = [docker,'--host',endpoint,'exec',self.reader,'kbs-client','--url','https://kbs:8443','--cert-file','/run/ca.pem']
        shutil.copyfile(self.folder/'as/token.pem',Path(os.environ['CABINET_QA_AUDIT'])/'as-signer.pem')
        script = Path(__file__).resolve().parents[1]/'scripts/cabinet_sample_reader.py'
        cfg = self.folder/'reader.json'
        write(cfg, json.dumps(dict(docker=[docker,'--host',endpoint], reader=cmd, kbs=self.kbs,
            source=self.run('inspect','--format','{{.Id}}',self.kbs).decode().strip(),
            archive=os.environ['CABINET_QA_AUDIT'],ca=str(self.folder/'admin/ca.pem'),
            as_cert=str(self.folder/'as/token.pem'),port=int(self.run('port',self.kbs,'8443/tcp').decode().rsplit(':',1)[1]))))
        write(self.wrapper,'#!/bin/sh\nexec '+shlex.join([sys.executable,str(script),str(cfg)])+' "$@"\n')
        self.wrapper.chmod(0o700)

    def target(self, monkeypatch):
        old_config = api.store.DATA/'config.json'
        source = api.demo_guard(old_config)
        config = json.loads(old_config.read_text())
        hw = self.folder/'target.json'
        hw.write_text(json.dumps(dict(namespace=self.namespace,url='https://kbs:8443',
            client=str(self.wrapper),client_sha256=hashlib.sha256(self.wrapper.read_bytes()).hexdigest(),
            ca_file=str(self.folder/'admin/ca.pem'),checkpoint_url=config['url'],checkpoint_ca_file=config['ca_file'])))
        monkeypatch.setenv('SENSE_CABINET_KEY_MODE','hardware')
        monkeypatch.setenv('SENSE_CABINET_ATTESTATION_CONFIG',str(hw))
        monkeypatch.setattr(sys,'argv',['admin','migrate-demo-keys','--source-checkpoint',str(old_config)])
        return source

    def offline(self):
        self.run('stop', self.kbs)

    def online(self):
        self.run('start', self.kbs)
        self.wait_ready()
        cfg = self.folder/'reader.json'
        settings = json.loads(cfg.read_text())
        settings['port'] = int(self.run('port',self.kbs,'8443/tcp').decode().rsplit(':',1)[1])
        replacement = cfg.with_suffix('.new')
        write(replacement,json.dumps(settings))
        replacement.replace(cfg)

