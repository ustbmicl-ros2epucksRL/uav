#!/usr/bin/env python3
"""Deploy a real, persistent SeaweedFS S3 service; no Moto or cabinet DB mutations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import shlex
import getpass
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'research/data_security_cabinet'))
from prepare_trustee import certificate,pem,write
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from object_store import S3Store
from object_operations import check

VERSION='4.52'


def download(folder):
    system=platform.system().lower();machine={'arm64':'arm64','aarch64':'arm64','x86_64':'amd64'}.get(platform.machine())
    if system not in ('linux','darwin') or not machine:raise ValueError('Provide --weed for this OS')
    asset=f'{system}_{machine}.tar.gz';url=f'https://github.com/seaweedfs/seaweedfs/releases/download/{VERSION}/{asset}'
    archive=folder/asset
    urllib.request.urlretrieve(url,archive)
    checksum=urllib.request.urlopen(url+'.md5',timeout=30).read(4096).decode().split()[0]
    if hashlib.md5(archive.read_bytes()).hexdigest()!=checksum:raise ValueError('Upstream download checksum mismatch')
    # MD5 is upstream's transport checksum, not a signature. Record SHA-256 too.
    with tarfile.open(archive) as tar:
        member=next(m for m in tar.getmembers() if m.name in ('weed','./weed') and m.isfile())
        with tar.extractfile(member) as src:write(folder/'weed',src.read())
    (folder/'weed').chmod(0o700)
    write(folder/'download.json',json.dumps({'url':url,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'version':VERSION}))
    return folder/'weed'


def prepare(folder,host,port,weed=None):
    folder=Path(folder).resolve()
    if (folder/'deployment.json').exists():return json.loads((folder/'deployment.json').read_text())
    if folder.exists() and any(folder.iterdir()):raise ValueError('Refusing nonempty uninitialized deployment directory')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700);folder.chmod(0o700)
    if not 1024<=port<=50000 or not host or any(c in host for c in '/:@ \n'):raise ValueError('Invalid host or base port')
    for name in ('data','server','client','admin','bin'):(folder/name).mkdir(mode=0o700)
    binary=Path(weed).resolve() if weed else download(folder/'bin')
    version=subprocess.check_output([str(binary),'version'],text=True,stderr=subprocess.STDOUT)
    if f' {VERSION} ' not in version:raise ValueError('Expected pinned SeaweedFS '+VERSION)
    ca_key=ec.generate_private_key(ec.SECP256R1());ca=certificate(ca_key,'Cabinet object store CA')
    key=ec.generate_private_key(ec.SECP256R1());cert=certificate(key,host,ca_key,ca,host)
    ca_pem=ca.public_bytes(serialization.Encoding.PEM)
    write(folder/'admin/ca.key',pem(ca_key));write(folder/'client/ca.pem',ca_pem)
    write(folder/'server/tls.key',pem(key));write(folder/'server/tls.pem',cert.public_bytes(serialization.Encoding.PEM)+ca_pem)
    admin_key,admin_secret=secrets.token_hex(12),secrets.token_urlsafe(36)
    app_key,app_secret=secrets.token_hex(12),secrets.token_urlsafe(36)
    bucket='cabinet-ciphertext';prefix='chunks'
    policy={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Action':['s3:GetObject','s3:PutObject'],'Resource':[f'arn:aws:s3:::{bucket}/{prefix}/*']}]}
    identity={'identities':[
        {'name':'cabinet-operator','account':{'id':'100000000001'},'credentials':[{'accessKey':admin_key,'secretKey':admin_secret}],'actions':['Admin']},
        {'name':'cabinet-platform','account':{'id':'100000000001'},'credentials':[{'accessKey':app_key,'secretKey':app_secret}],'policyNames':['cabinet-chunks']}],
        'policies':[{'name':'cabinet-chunks','content':json.dumps(policy)}]}
    write(folder/'server/identities.json',json.dumps(identity))
    for where,access,secret in [('client',app_key,app_secret),('admin',admin_key,admin_secret)]:
        write(folder/where/'access',access);write(folder/where/'secret',secret)
        cfg={'endpoint':f'https://{host}:{port}','bucket':bucket,'prefix':prefix,'ca_file':str(folder/'client/ca.pem'),
             'access_key_file':str(folder/where/'access'),'secret_key_file':str(folder/where/'secret')}
        write(folder/where/'objects.json',json.dumps(cfg,indent=2))
    config={'folder':str(folder),'host':host,'port':port,'weed':str(binary),'version':version.strip()}
    write(folder/'deployment.json',json.dumps(config,indent=2))
    compose={'services':{'objects':{'image':f'chrislusf/seaweedfs:{VERSION}','command':arguments(config,'/deployment','0.0.0.0')[1:],
        'restart':'unless-stopped','ports':[f'{port}:{port}'],'volumes':['./data:/deployment/data','./server:/deployment/server:ro'],
        'security_opt':['no-new-privileges:true'],'cap_drop':['ALL'],'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}}}
    write(folder/'compose.json',json.dumps(compose,indent=2))
    unit='[Unit]\nDescription=Cabinet ciphertext object service\nAfter=network-online.target\n\n[Service]\nType=simple\nUser='+getpass.getuser()+'\nExecStart='+shlex.join(arguments(config))+'\nRestart=on-failure\nRestartSec=3\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nReadWritePaths='+shlex.quote(str(folder/'data'))+'\n\n[Install]\nWantedBy=multi-user.target\n'
    write(folder/'cabinet-objects.service',unit)
    return config


def arguments(config,root=None,bind='127.0.0.1'):
    folder=root or config['folder'];p=config['port']
    return [config['weed'],'mini',f'-dir={folder}/data','-ip=127.0.0.1',f'-ip.bind={bind}',
        f'-master.port={p+1}',f'-filer.port={p+2}',f'-volume.port={p+3}',
        f'-admin.port={p+4}','-admin.port.legacy=0','-admin.ui=false','-webdav=false','-master.telemetry=false',
        '-s3.port=0',f'-s3.port.https={p}',f'-s3.port.grpc={p+10000}','-s3.port.iceberg=0','-s3.port.lance=0',
        '-s3.iam.readOnly=true','-s3.autoCreateBucket=false','-s3.allowedOrigins=',
        f'-s3.config={folder}/server/identities.json',f'-s3.cert.file={folder}/server/tls.pem',f'-s3.key.file={folder}/server/tls.key']


def initialize(folder,timeout=60):
    from botocore.exceptions import ClientError
    admin=S3Store(folder/'admin/objects.json');until=time.monotonic()+timeout
    while True:
        try:admin.client.list_buckets();break
        except Exception:
            if time.monotonic()>=until:raise RuntimeError('Object service did not become ready; inspect service.log') from None
            time.sleep(.5)
    try:admin.client.head_bucket(Bucket=admin.bucket)
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode']!=404:raise
        admin.client.create_bucket(Bucket=admin.bucket)
    return check(folder/'client/objects.json',probe=True)


def native_up(config):
    folder=Path(config['folder']);pid_file=folder/'service.pid'
    if pid_file.exists():
        pid=int(pid_file.read_text())
        command=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True).stdout
        if f"-dir={folder}/data" in command:return initialize(folder)
        if command.strip():raise ValueError('PID was reused; inspect it before removing stale service.pid')
    log=open(folder/'service.log','ab');os.chmod(folder/'service.log',0o600)
    env={k:v for k,v in os.environ.items() if not k.startswith(('AWS_','WEED_')) and k!='S3_BUCKET'}
    process=subprocess.Popen(arguments(config),stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True,env=env)
    log.close();pid_file.write_text(str(process.pid));pid_file.chmod(0o600)
    return initialize(folder)


def native_stop(config):
    folder=Path(config['folder']);file=folder/'service.pid'
    if not file.exists():return
    pid=int(file.read_text());command=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True).stdout
    if command.strip() and f"-dir={folder}/data" not in command:raise ValueError('PID identity mismatch; refusing to stop unrelated process')
    if command.strip():
        os.kill(pid,signal.SIGTERM)
        for _ in range(100):
            result=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not result or result.startswith('Z'):break
            time.sleep(.1)
        else:raise RuntimeError('Service still stopping; do not start another instance')
    file.unlink()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','up','stop','restart','check'])
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=9443)
    parser.add_argument('--weed');parser.add_argument('--mode',choices=['native','compose'],default='native')
    args=parser.parse_args();folder=args.directory.resolve()
    if args.action in ('up','prepare'):config=prepare(folder,args.host,args.port,args.weed)
    else:config=json.loads((folder/'deployment.json').read_text())
    if args.action in ('stop','restart'):
        if args.mode=='native':native_stop(config)
        else:subprocess.run(['docker','compose','-f',str(folder/'compose.json'),'stop'],check=True)
    if args.action in ('up','restart'):
        if args.mode=='native':result=native_up(config)
        else:
            subprocess.run(['docker','compose','-f',str(folder/'compose.json'),'up','-d'],check=True)
            result=initialize(folder)
    elif args.action=='check':result=check(folder/'client/objects.json',probe=True)
    else:result={'action':args.action,'directory':str(folder)}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
