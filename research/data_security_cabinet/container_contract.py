"""Pinned container-template contract adapted from the supplied MGEData format.

This is a small, explicit subset, not execution/import of the archived backend.
Template schema, version and mapper are pinned in trusted code. Values are sealed
as one asset; provenance is never flattened into public search or model outputs.
"""
import copy
import hashlib
import json
import math
import re

FORMAT = 'cabinet-container-v1'
TEMPLATE_ID = 'lowair-synthetic-features'
VERSION = 1
MAPPER = 'lowair-feature-pair-v1'
MAX_BYTES = 1024 * 1024
MAX_ROWS = 10000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def field(kind, **misc):
    return {'t':kind, 'r':True, 'misc':misc}


TEMPLATE = {
    '_ord':['provenance','samples'],
    'provenance':field(9, _ord=['source','data_kind'], source=field(1),
                       data_kind=field(6, opt=['synthetic'], grp=[])),
    'samples':field(8, _head=['group_id','label','feature_1','feature_2'],
                    group_id=field(1), label=field(6, opt=['target','background'], grp=[]),
                    feature_1=field(2,unit='dimensionless'), feature_2=field(2,unit='dimensionless')),
}
TEMPLATE_HASH = hashlib.sha256(canonical(TEMPLATE)).hexdigest()


def describe():
    return {'format':FORMAT, 'template_id':TEMPLATE_ID, 'template_version':VERSION,
            'template_sha256':TEMPLATE_HASH, 'mapper':MAPPER, 'template':copy.deepcopy(TEMPLATE),
            'limits':{'max_bytes':MAX_BYTES,'max_rows':MAX_ROWS},
            'supported_types':[1,2,6,8,9], 'synthetic_only':True}


def validate_container(schema, value, path='$', depth=0):
    if depth>12: raise ValueError('容器嵌套过深')
    order=schema.get('_ord', schema.get('_head'))
    if not isinstance(order,list) or len(set(order))!=len(order):
        raise ValueError('模板字段顺序非法')
    if not isinstance(value,dict) or set(value)-set(order):
        raise ValueError(f'{path} 存在未知字段或非容器值')
    for name in order:
        spec=schema[name]; loc=path+'.'+name
        if name not in value:
            if spec['r']: raise ValueError(f'{loc} 缺少必填字段')
            continue
        item=value[name]; kind=spec['t']; misc=spec['misc']
        if kind==1:
            if not isinstance(item,str) or not 1<=len(item)<=128: raise ValueError(f'{loc} 需要短字符串')
        elif kind==2:
            if type(item) not in (int,float) or not math.isfinite(item): raise ValueError(f'{loc} 需要有限数值')
        elif kind==6:
            if not isinstance(item,str) or item not in misc['opt']: raise ValueError(f'{loc} 不在候选范围')
        elif kind in (4,5):
            if not isinstance(item,dict) or set(item)!={'attachment_id'} or not isinstance(item['attachment_id'],str) or not re.fullmatch('[a-f0-9]{32}',item['attachment_id']):
                raise ValueError(f'{loc} 需要加密附件引用，不能使用 URL 或路径')
        elif kind==9: validate_container(misc,item,loc,depth+1)
        elif kind==8:
            if not isinstance(item,list) or not 1<=len(item)<=MAX_ROWS: raise ValueError(f'{loc} 表格行数非法')
            for row in item: validate_container(misc,row,loc+'[]',depth+1)
        else: raise ValueError('该字段类型尚未支持，禁止隐式转换')


def validate(package):
    if not isinstance(package,dict) or set(package)!={'format','template_id','template_version','template_sha256','mapper','template','content'}:
        raise ValueError('容器包字段不完整或包含未许可字段')
    if (package['format']!=FORMAT or package['template_id']!=TEMPLATE_ID
            or type(package['template_version']) is not int or package['template_version']!=VERSION
            or package['template_sha256']!=TEMPLATE_HASH or package['mapper']!=MAPPER
            or canonical(package['template'])!=canonical(TEMPLATE)):
        raise ValueError('模板版本、摘要或映射器未获当前服务批准')
    if len(canonical(package))>MAX_BYTES: raise ValueError('容器包超过 1 MiB')
    validate_container(TEMPLATE,package['content'])
    return package


def example():
    descriptor=describe()
    package={k:descriptor[k] for k in ('format','template_id','template_version','template_sha256','mapper','template')}
    package['content']={'provenance':{'source':'built-in synthetic fixture','data_kind':'synthetic'},
        'samples':[{'feature_1':i,'feature_2':i*0.25,'label':'target' if i%2 else 'background','group_id':f'scene-{i//2}'} for i in range(12)]}
    return package


def pack(package):
    return canonical(validate(package))


def no_duplicates(pairs):
    value={}
    for key,item in pairs:
        if key in value: raise ValueError('重复 JSON 字段')
        value[key]=item
    return value


def unpack(raw):
    if len(raw)>MAX_BYTES: raise ValueError('容器包超过 1 MiB')
    package=json.loads(raw,object_pairs_hook=no_duplicates)
    validate(package)
    rows=[{'features':[r['feature_1'],r['feature_2']], 'label':r['label'],'group_id':r['group_id']}
          for r in package['content']['samples']]
    lineage={k:package[k] for k in ('format','template_id','template_version','template_sha256','mapper')}
    lineage['data_kind']='synthetic'
    # Source text is deliberately not released, even to the model-dataset role.
    return b'\n'.join(canonical(r) for r in rows),lineage
