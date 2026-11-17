"""Isolated manual/browser QA only. Never use Moto as production object storage.
Run from platform: python tests/cabinet_chunks_qa_server.py
"""
import json
import os
from pathlib import Path
import tempfile
import threading

root=Path(tempfile.mkdtemp(prefix='cabinet-chunks-qa-'))
os.environ['SENSE_DATA_DIR']=str(root/'data')
os.environ['SENSE_CABINET_ALLOW_HTTP']='1'
from app import cabinet_demo as api
from app import cabinet_auth as auth
from prepare_checkpoint import prepare
from checkpoint_service import Authority,make_server
p=prepare(root/'checkpoint','127.0.0.1','browser-test')
server=make_server(Authority(p/'authority.db'),'127.0.0.1',0,p/'tls.pem',p/'tls.key')
threading.Thread(target=server.serve_forever,daemon=True).start()
config=json.loads((p/'platform.json').read_text());config['url']=f'https://127.0.0.1:{server.server_port}'
(p/'platform.json').write_text(json.dumps(config));os.environ['SENSE_CABINET_CHECKPOINT_CONFIG']=str(p/'platform.json')
cab=api.persistence.open_cabinet(root/'data'/'cabinet-demo',api.configured_guard());auth.install(cab)
import template_registry,media_attachments,chunked_attachments
template_registry.install(cab);media_attachments.install(cab);chunked_attachments.install(cab)
if os.environ.get('CABINET_QA_OBJECT_CONFIG'):
    os.environ['SENSE_CABINET_OBJECT_CONFIG']=os.environ['CABINET_QA_OBJECT_CONFIG']
    from object_operations import check
    check(probe=True)
elif os.environ.get('CABINET_QA_MOTO')=='1':
    from moto.server import create_backend_app
    from werkzeug.serving import make_server as s3_server
    objects=s3_server('127.0.0.1',0,create_backend_app('s3'),threaded=True,ssl_context=(str(p/'tls.pem'),str(p/'tls.key')))
    threading.Thread(target=objects.serve_forever,daemon=True).start()
    (root/'access').write_text('synthetic-access');(root/'secret').write_text('synthetic-secret')
    (root/'objects.json').write_text(json.dumps({'endpoint':f'https://127.0.0.1:{objects.server_port}','bucket':'cabinet-browser','prefix':'chunks','ca_file':str(p/'ca.pem'),'access_key_file':str(root/'access'),'secret_key_file':str(root/'secret')}))
    os.environ['SENSE_CABINET_OBJECT_CONFIG']=str(root/'objects.json')
    from object_store import configured_store
    configured_store().client.create_bucket(Bucket='cabinet-browser')
else:
    raise ValueError('Set CABINET_QA_OBJECT_CONFIG for real storage; Moto requires explicit CABINET_QA_MOTO=1')
org=auth.add_org(cab,'Synthetic owner organisation');partner=auth.add_org(cab,'Synthetic partner organisation')
password=os.environ['CABINET_QA_PASSWORD']
for name,role,oid in [('owner','owner',org),('alice','dataset',partner),('bobby','stats',partner),('admin','org_admin',org)]:
    auth.add_user(cab,oid,name,password,role)
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
app=FastAPI();app.include_router(api.router);app.mount('/static',StaticFiles(directory=str(api.store.ROOT/'static')))
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('CABINET_QA_PORT','8790')),log_level='warning')
