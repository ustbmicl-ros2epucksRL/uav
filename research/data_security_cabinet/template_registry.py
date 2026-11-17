"""Versioned, organisation-scoped templates. All writes use Cabinet transactions."""
import hashlib
import json
import re
import secrets
from container_contract import canonical, validate_container, MAX_BYTES, no_duplicates
from security_cabinet import Denied, transform
import media_attachments as media

FORMAT = 'cabinet-registered-container-v1'


def installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='container_versions'").fetchone())


def install(cab):
    with cab._transaction() as db:
        if installed(db): raise ValueError('Template registry already installed')
        db.execute('CREATE TABLE container_versions (template_id TEXT NOT NULL, version INTEGER NOT NULL, org TEXT NOT NULL, author TEXT NOT NULL, definition TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT, PRIMARY KEY(template_id,version))')
        db.execute('CREATE TABLE asset_templates (asset TEXT PRIMARY KEY, template_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL)')
        cab._audit(db,'template_registry_installed','operator')


def schema_check(schema, depth=0, table=False, budget=None):
    budget = [0] if budget is None else budget
    key='_head' if table else '_ord'
    if depth>8 or not isinstance(schema,dict): raise ValueError('Invalid schema depth')
    order=schema.get(key)
    if (not isinstance(order,list) or not order or any(not isinstance(n,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',n) for n in order)
            or len(set(order))!=len(order) or set(schema)!={key,*order}): raise ValueError('Invalid schema field order')
    for name in order:
        budget[0]+=1
        if budget[0]>128: raise ValueError('Too many schema fields')
        spec=schema[name]
        if not isinstance(spec,dict) or set(spec)!={'t','r','misc'} or type(spec['r']) is not bool or type(spec['t']) is not int or not isinstance(spec['misc'],dict): raise ValueError('Invalid field descriptor')
        t,m=spec['t'],spec['misc']
        if t==1:
            if m: raise ValueError('String extensions unsupported')
        elif t==2:
            if set(m)-{'unit'} or not isinstance(m.get('unit',''),str) or len(m.get('unit',''))>32: raise ValueError('Invalid unit')
        elif t in (4,5): media.validate_spec(t,m)
        elif t==6:
            opts=m.get('opt')
            if set(m)!={'opt','grp'} or m['grp']!=[] or not isinstance(opts,list) or not 1<=len(opts)<=100 or any(not isinstance(v,str) or not 1<=len(v)<=128 for v in opts) or len(set(opts))!=len(opts): raise ValueError('Invalid choices')
        elif t in (8,9): schema_check(m,depth+1,t==8,budget)
        else: raise ValueError('Unsupported field type')


def validate_definition(d):
    if not isinstance(d,dict) or set(d)!={'name','schema','mapping','purposes','data_kind'}: raise ValueError('Invalid definition fields')
    if not isinstance(d['name'],str) or not 1<=len(d['name'])<=100 or d['data_kind'] not in ('synthetic','observed'): raise ValueError('Invalid template metadata')
    if len(canonical(d))>65536: raise ValueError('Definition too large')
    schema_check(d['schema'])
    m=d['mapping']; purposes=d['purposes']
    if not isinstance(m,dict) or not isinstance(purposes,list) or not purposes or any(p not in ('dataset','stats','preview') for p in purposes) or len(set(purposes))!=len(purposes): raise ValueError('Invalid purposes')
    if 'preview' in purposes and not list(media.field_specs(d['schema'])): raise ValueError('Preview requires media fields')
    if m=={'processor':'record-summary-v1'}:
        if any(p not in ('stats','preview') for p in purposes): raise ValueError('Record summary only permits stats/preview')
    elif m.get('processor')=='numeric-table-v1':
        if set(m)!={'processor','table','features','label','group'}: raise ValueError('Invalid numeric mapper')
        table=d['schema'].get(m['table']) if isinstance(m['table'],str) else None
        if not table or table['t']!=8 or not table['r']: raise ValueError('Mapper requires a required top-level table')
        cols=table['misc']; features=m['features']
        if not isinstance(features,list) or not 1<=len(features)<=64 or any(not isinstance(f,str) for f in features) or len(set(features))!=len(features): raise ValueError('Invalid features')
        for f in features:
            if f not in cols or not isinstance(cols[f],dict) or cols[f]['t']!=2 or not cols[f]['r']: raise ValueError('Features must be required numeric columns')
        for key in ('label','group'):
            f=m[key]
            if not isinstance(f,str) or f not in cols or not isinstance(cols[f],dict) or cols[f]['t'] not in (1,6) or not cols[f]['r']: raise ValueError('Required label/group column missing')
        if m['label']==m['group']: raise ValueError('Label cannot be the grouping key')
    else: raise ValueError('Unapproved processor; no executable expressions allowed')
    return d


def get(db, tid, version):
    if not installed(db): raise ValueError('Template registry migration required')
    row=db.execute('SELECT * FROM container_versions WHERE template_id=? AND version=?',(tid,version)).fetchone()
    if row is None: raise Denied('模板版本不存在或不可访问')
    if hashlib.sha256(row['definition'].encode()).hexdigest()!=row['digest']: raise Denied('模板摘要不一致')
    return dict(row)


def register(cab,user,definition,tid=None):
    if user['role'] not in ('owner','org_admin'): raise Denied('只有所有者或组织管理员可注册模板')
    validate_definition(definition)
    encoded=canonical(definition).decode(); digest=hashlib.sha256(encoded.encode()).hexdigest()
    with cab._transaction() as db:
        if not installed(db): raise ValueError('Template registry migration required')
        if tid:
            existing=db.execute('SELECT org,MAX(version) AS version FROM container_versions WHERE template_id=? GROUP BY org',(tid,)).fetchone()
            if not existing or existing['org']!=user['org']: raise Denied('不能修改其他组织模板')
            version=existing['version']+1
        else: tid=secrets.token_hex(16); version=1
        db.execute('INSERT INTO container_versions VALUES (?,?,?,?,?,?,?,NULL)',(tid,version,user['org'],user['id'],encoded,digest,'draft'))
        cab._audit(db,'template_registered',user['id'],template=tid,version=version,digest=digest)
    return {'template_id':tid,'version':version,'digest':digest,'status':'draft'}


def transition(cab,user,tid,version,action):
    flow={'submit':('draft','submitted'),'approve':('submitted','approved'),
          'reject':('submitted','rejected'),'publish':('approved','published'),'retire':('published','retired')}
    if action not in flow: raise ValueError('Unknown transition')
    with cab._transaction() as db:
        row=get(db,tid,version)
        if row['org']!=user['org']: raise Denied('不能管理其他组织模板')
        if action=='submit':
            if row['author']!=user['id']: raise Denied('仅作者可提交')
        elif user['role']!='org_admin' or user['id']==row['author']:
            raise Denied('需要本组织另一名管理员审核或发布；禁止自审自发')
        if row['status']!=flow[action][0]: raise ValueError('Invalid template lifecycle transition')
        reviewer=user['id'] if action in ('approve','reject') else row['reviewer']
        db.execute('UPDATE container_versions SET status=?,reviewer=? WHERE template_id=? AND version=?',(flow[action][1],reviewer,tid,version))
        cab._audit(db,'template_'+action,user['id'],template=tid,version=version,digest=row['digest'])
    return {'status':flow[action][1]}


def resolve(db, package, org=None):
    if not isinstance(package,dict) or set(package)!={'format','template_id','template_version','template_sha256','content'} or package['format']!=FORMAT or type(package['template_version']) is not int: raise ValueError('Invalid registered container envelope')
    if (not isinstance(package['template_id'],str) or not re.fullmatch('[a-f0-9]{32}',package['template_id'])
            or package['template_version']<1 or not isinstance(package['template_sha256'],str)
            or not re.fullmatch('[a-f0-9]{64}',package['template_sha256'])):
        raise ValueError('Invalid template identifier or digest')
    if len(canonical(package))>MAX_BYTES: raise ValueError('Container exceeds 1 MiB')
    row=get(db,package['template_id'],package['template_version'])
    if row['status']!='published' or row['digest']!=package['template_sha256'] or (org is not None and row['org']!=org): raise Denied('模板未发布、已停用、摘要不匹配或不属于当前组织')
    d=json.loads(row['definition']);validate_definition(d);validate_container(d['schema'],package['content'])
    return row,d


def mapped_result(definition, content, purpose):
    if purpose=='preview': raise Denied('附件预览必须使用受控预览接口')
    if purpose not in definition['purposes']: raise Denied('模板不允许当前用途')
    m=definition['mapping']
    if m['processor']=='record-summary-v1':
        # Counts only. No content values, field names, identifiers or source text.
        def counts(schema,value):
            fields=tables=rows=0
            for name in schema.get('_ord',schema.get('_head')):
                if name not in value: continue
                fields+=1;spec=schema[name]
                if spec['t']==9:
                    f,t,r=counts(spec['misc'],value[name]);fields+=f;tables+=t;rows+=r
                if spec['t']==8: tables+=1;rows+=len(value[name])
            return fields,tables,rows
        f,t,r=counts(definition['schema'],content)
        return {'purpose':'stats','quality':{'present_fields':f,'tables':t,'table_rows':r}}
    rows=[{'features':[r[f] for f in m['features']], 'label':r[m['label']],'group_id':r[m['group']]} for r in content[m['table']]]
    return transform(b'\n'.join(canonical(r) for r in rows),purpose)


class Policy:
    def on_put(self, db, actor, aid, raw, now):
        try: package=json.loads(raw,object_pairs_hook=no_duplicates)
        except json.JSONDecodeError: return  # legacy JSONL
        if not isinstance(package,dict) or package.get('format')!=FORMAT: return
        user=db.execute('SELECT org FROM auth_users WHERE id=?',(actor,)).fetchone()
        if not user: raise Denied('当前主体不支持注册模板')
        row,d=resolve(db,package,user['org'])
        for purpose in d['purposes']:
            if purpose!='preview': mapped_result(d,package['content'],purpose)
        attachments=media.bind(db,actor,aid,package,d,row,now)
        db.execute('INSERT INTO asset_templates VALUES (?,?,?,?)',(aid,row['template_id'],row['version'],row['digest']))
        return attachments

    def check(self,db,aid,purpose):
        if not installed(db):
            if purpose=='preview': raise Denied('预览必须绑定注册模板')
            return
        binding=db.execute('SELECT * FROM asset_templates WHERE asset=?',(aid,)).fetchone()
        if not binding and purpose=='preview': raise Denied('预览必须绑定注册模板')
        if binding:
            row=get(db,binding['template_id'],binding['version'])
            if row['status']!='published' or row['digest']!=binding['digest'] or purpose not in json.loads(row['definition'])['purposes']: raise Denied('绑定模板已停用或不允许此用途')

    def transform(self,db,aid,raw,purpose):
        try: package=json.loads(raw,object_pairs_hook=no_duplicates)
        except json.JSONDecodeError: return transform(raw,purpose)
        if not isinstance(package,dict) or package.get('format')!=FORMAT: return transform(raw,purpose)
        self.check(db,aid,purpose)
        row,d=resolve(db,package)
        binding=db.execute('SELECT * FROM asset_templates WHERE asset=?',(aid,)).fetchone()
        if not binding or (binding['template_id'],binding['version'],binding['digest'])!=(row['template_id'],row['version'],row['digest']): raise Denied('资产模板绑定不一致')
        result=mapped_result(d,package['content'],purpose)
        result['container']={'format':FORMAT,'template_id':row['template_id'],'template_version':row['version'],
                             'template_sha256':row['digest'],'mapper':d['mapping']['processor'],'data_kind':d['data_kind']}
        return result


def examples():
    from container_contract import TEMPLATE, example, field
    return [
        {'definition':{'name':'低空特征样本','schema':TEMPLATE,'mapping':{'processor':'numeric-table-v1','table':'samples','features':['feature_1','feature_2'],'label':'label','group':'group_id'},'purposes':['dataset','stats'],'data_kind':'synthetic'},'content':example()['content']},
        {'definition':{'name':'设备观测记录','schema':{'_ord':['device','measurement'],
            'device':field(9,_ord=['serial','category'],serial=field(1),category=field(6,opt=['radar','camera'],grp=[])),
            'measurement':field(8,_head=['temperature','voltage'],temperature=field(2,unit='C'),voltage=field(2,unit='V'))},
            'mapping':{'processor':'record-summary-v1'},'purposes':['stats'],'data_kind':'synthetic'},
         'content':{'device':{'serial':'SYNTHETIC-001','category':'radar'},'measurement':[{'temperature':25,'voltage':12}]}},
        {'definition':{'name':'影像附件记录','schema':{'_ord':['description','photo','video'],
            'description':field(1), 'photo':field(4,mime=['image/png','image/jpeg'],max_bytes=media.MAX_BYTES),
            'video':{'t':5,'r':False,'misc':{'mime':['video/mp4','video/webm'],'max_bytes':media.MAX_BYTES}}},
            'mapping':{'processor':'record-summary-v1'},'purposes':['stats','preview'],'data_kind':'synthetic'},
         'content':{'description':'合成图片/视频示例','photo':{'attachment_id':'先上传图片后填写返回的 attachment_id'}}}
    ]
