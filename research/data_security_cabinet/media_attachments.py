"""Bounded encrypted media stored in the same externally committed SQLite state.

No filesystem paths, public URLs or plaintext spooling. Staged objects cannot be
previewed. Binding is performed inside Cabinet.put's transaction.
"""
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import warnings
from security_cabinet import Denied, IntegrityError, ReleaseRequest, canonical

MAX_BYTES = 8 * 1024 * 1024
MAX_PER_ASSET = 8
MAX_STORED_BYTES = 128 * 1024 * 1024
MIMES = {4: ('image/png','image/jpeg'), 5: ('video/mp4','video/webm')}


class MediaUnavailable(Exception):
    pass


def installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='cabinet_attachments'").fetchone())


def install(cab):
    import template_registry
    with cab._transaction() as db:
        if not template_registry.installed(db): raise ValueError('Migrate templates first')
        if installed(db): raise ValueError('Attachments already installed')
        db.execute('CREATE TABLE cabinet_attachments (id TEXT PRIMARY KEY, owner TEXT NOT NULL, template_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, field TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, expires REAL NOT NULL, asset TEXT, aad BLOB NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, envelope BLOB NOT NULL)')
        cab._audit(db,'attachment_schema_installed','operator')


def field_specs(schema,path=()):
    for name in schema.get('_ord',schema.get('_head',[])):
        spec=schema[name]; location=path+(name,)
        if spec['t'] in (4,5): yield '.'.join(location),spec
        elif spec['t'] in (8,9): yield from field_specs(spec['misc'],location)


def references(schema,content,path=()):
    for name in schema.get('_ord',schema.get('_head',[])):
        if name not in content: continue
        spec=schema[name]; location=path+(name,); value=content[name]
        if spec['t'] in (4,5): yield '.'.join(location),value['attachment_id']
        elif spec['t']==9: yield from references(spec['misc'],value,location)
        elif spec['t']==8:
            for row in value: yield from references(spec['misc'],row,location)


def validate_spec(kind,misc):
    if (set(misc)!={'mime','max_bytes'} or not isinstance(misc['mime'],list) or not misc['mime']
            or any(m not in MIMES[kind] for m in misc['mime'])
            or len(set(misc['mime']))!=len(misc['mime'])
            or type(misc['max_bytes']) is not int or not 1<=misc['max_bytes']<=(2*1024**3 if kind==5 else MAX_BYTES)):
        raise ValueError('Invalid media MIME allowlist or size limit')


def published_field(db,user,tid,version,field):
    import template_registry
    row=template_registry.get(db,tid,version)
    if row['status']!='published' or row['org']!=user['org']: raise Denied('模板未发布、已停用或不属于当前组织')
    definition=json.loads(row['definition'])
    spec=dict(field_specs(definition['schema'])).get(field)
    if not spec: raise Denied('字段不是已审核附件字段')
    if not installed(db): raise MediaUnavailable('请先迁移附件表')
    return row,spec


def validate_media(raw,declared):
    if not 0<len(raw)<=MAX_BYTES: raise ValueError('附件大小非法')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'): actual='image/png'
    elif raw.startswith(b'\xff\xd8\xff'): actual='image/jpeg'
    elif len(raw)>=12 and raw[4:8]==b'ftyp': actual='video/mp4'
    elif raw.startswith(b'\x1a\x45\xdf\xa3'): actual='video/webm'
    else: raise ValueError('仅支持 PNG/JPEG/MP4/WebM，拒绝 SVG/HTML 和未知文件')
    if declared!=actual: raise ValueError('Content-Type 与实际文件不符')
    if actual.startswith('image/'):
        try:
            from PIL import Image
        except ImportError:
            raise MediaUnavailable('图片校验依赖 Pillow 未安装') from None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as picture:
                    if picture.width*picture.height>16_000_000 or getattr(picture,'n_frames',1)!=1:
                        raise ValueError('图片超过像素限制或包含动画')
                    if picture.format not in ('PNG','JPEG'): raise ValueError('不支持的图片格式')
                    picture.verify()
                with Image.open(io.BytesIO(raw)) as picture: picture.load()
        except Exception:
            raise ValueError('图片完整性或像素校验失败') from None
    else:
        binary=os.environ.get('SENSE_CABINET_FFPROBE') or shutil.which('ffprobe')
        if not binary or not os.path.isabs(binary) or not os.access(binary,os.X_OK):
            raise MediaUnavailable('视频校验需要可信 ffprobe 绝对路径')
        try:
            probe=subprocess.run([binary,'-v','error','-protocol_whitelist','pipe',
                '-format_whitelist','mov,matroska,webm','-probesize',str(MAX_BYTES),
                '-show_entries','format=format_name:stream=codec_type,codec_name,width,height',
                '-of','json','-i','pipe:0'],input=raw,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                timeout=8,env={'PATH':os.defpath,'LANG':'C'})
            if probe.returncode or len(probe.stdout)>32768: raise ValueError()
            result=json.loads(probe.stdout); streams=result.get('streams',[])
            codecs={'video/mp4':{'h264','aac'},'video/webm':{'vp8','vp9','opus','vorbis'}}[actual]
            fmt=result.get('format',{}).get('format_name','')
            if (actual=='video/mp4' and 'mp4' not in fmt) or (actual=='video/webm' and 'webm' not in fmt): raise ValueError()
            videos=[s for s in streams if s.get('codec_type')=='video']
            if not 1<=len(videos)<=2 or not 1<=len(streams)<=4: raise ValueError()
            for stream in streams:
                if stream.get('codec_type') not in ('video','audio') or stream.get('codec_name') not in codecs: raise ValueError()
            for stream in videos:
                if not 0<stream.get('width',0)<=4096 or not 0<stream.get('height',0)<=2160: raise ValueError()
        except (ValueError,KeyError,subprocess.TimeoutExpired,OSError):
            raise ValueError('视频容器或编码校验失败，仅支持 H264/AAC MP4 或 VP8/VP9 WebM') from None
    return actual


def upload(cab,user,tid,version,field,raw,mime):
    if user['role']!='owner': raise Denied('只有所有者可上传附件')
    # Validate against the published schema before expensive parsing, then again
    # in the commit transaction; the HTTP request also holds the platform lock.
    with cab._transaction() as db:
        _,spec=published_field(db,user,tid,version,field)
        if mime not in spec['misc']['mime'] or len(raw)>spec['misc']['max_bytes']: raise ValueError('附件超出字段类型或大小限制')
    actual=validate_media(raw,mime)
    with cab._transaction() as db:
        row,_=published_field(db,user,tid,version,field)
        if db.execute('SELECT COALESCE(sum(size),0) FROM cabinet_attachments').fetchone()[0]+len(raw)>MAX_STORED_BYTES:
            raise ValueError('当前单节点附件容量上限为 128 MiB')
        if db.execute('SELECT count(*) FROM cabinet_attachments WHERE owner=? AND asset IS NULL',(user['id'],)).fetchone()[0]>=16: raise ValueError('暂存附件已达上限，请删除未入柜附件')
        mid=secrets.token_hex(16);sha=hashlib.sha256(raw).hexdigest()
        aad=canonical({'type':'attachment','id':mid,'owner':user['id'],'template_id':tid,
                       'version':version,'digest':row['digest'],'field':field,'mime':actual,'size':len(raw),'sha256':sha})
        nonce,ciphertext,envelope=cab._seal(raw,aad)
        db.execute('INSERT INTO cabinet_attachments VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)',
            (mid,user['id'],tid,version,row['digest'],field,actual,len(raw),sha,cab.clock()+86400,aad,nonce,ciphertext,envelope))
        cab._audit(db,'attachment_uploaded',user['id'],attachment=mid,template=tid,version=version,size=len(raw))
    return {'attachment_id':mid,'field':field,'mime':actual,'size':len(raw),'expires_in':86400}


def bind(db,cab_actor,aid,package,definition,row,now):
    refs=list(references(definition['schema'],package['content']))
    if not refs: return
    if not installed(db): raise MediaUnavailable('请先迁移附件表')
    if len(refs)>MAX_PER_ASSET or len({mid for _,mid in refs})!=len(refs): raise ValueError('每资产最多 8 个附件，且不可重复引用')
    specs=dict(field_specs(definition['schema']))
    for field,mid in refs:
        attachment=db.execute('SELECT * FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()
        if (not attachment or attachment['owner']!=cab_actor or attachment['asset'] is not None
                or attachment['expires']<=now or attachment['template_id']!=row['template_id']
                or attachment['version']!=row['version'] or attachment['digest']!=row['digest']
                or attachment['field']!=field or attachment['mime'] not in specs[field]['misc']['mime']
                or attachment['size']>specs[field]['misc']['max_bytes']):
            raise Denied('附件不属于当前主体/模板字段，已过期或已绑定其他资产')
        db.execute('UPDATE cabinet_attachments SET asset=? WHERE id=?',(aid,mid))
    return [mid for _,mid in refs]


def access(cab,db,user,asset,gid=None,consume=False):
    if not installed(db): raise MediaUnavailable('请先迁移附件表')
    asset_row=db.execute('SELECT a.owner,u.enabled,u.role,o.enabled AS org_enabled FROM assets a JOIN auth_users u ON u.id=a.owner JOIN auth_orgs o ON o.id=u.org WHERE a.id=?',(asset,)).fetchone()
    if not asset_row or not asset_row['enabled'] or not asset_row['org_enabled'] or asset_row['role']!='owner': raise Denied('附件资产不可访问')
    if gid is not None:
        if user['role']!='dataset': raise Denied('当前主体不允许预览附件')
        grant=cab._authorize(db,user['id'],gid,consume=consume)
        if grant['asset']!=asset or grant['purpose']!='preview': raise Denied('需要当前资产的独立 preview 授权')
    elif user['role']!='owner' or user['id']!=asset_row['owner']:
        raise Denied('不能预览其他所有者的附件')
    # Owners must respect retirement too; their ownership is not an override.
    cab.container_policy.check(db,asset,'preview')


def listing(cab,user,asset,gid=None):
    with cab._transaction() as db:
        access(cab,db,user,asset,gid)
        rows=[dict(r) for r in db.execute('SELECT id,field,mime,size FROM cabinet_attachments WHERE asset=? ORDER BY field',(asset,))]
        import chunked_attachments as chunks
        for row in rows:
            row['chunked']=bool(chunks.installed(db) and db.execute('SELECT 1 FROM attachment_uploads WHERE id=?',(row['id'],)).fetchone())
        return rows


def preview(cab,user,asset,mid,gid=None):
    with cab._transaction() as db:
        access(cab,db,user,asset,gid,consume=True)
        row=db.execute('SELECT * FROM cabinet_attachments WHERE id=? AND asset=?',(mid,asset)).fetchone()
        if row is None: raise Denied('附件不属于授权资产')
        import chunked_attachments as chunks
        if chunks.installed(db) and db.execute('SELECT 1 FROM attachment_uploads WHERE id=?',(mid,)).fetchone():
            raise Denied('分块附件请使用流式预览接口')
        binding=db.execute('SELECT * FROM asset_templates WHERE asset=?',(asset,)).fetchone()
        if not binding or (binding['template_id'],binding['version'],binding['digest'])!=(row['template_id'],row['version'],row['digest']):
            raise IntegrityError('附件与资产模板绑定不一致')
        expected=canonical({'type':'attachment','id':row['id'],'owner':row['owner'],'template_id':row['template_id'],
            'version':row['version'],'digest':row['digest'],'field':row['field'],'mime':row['mime'],'size':row['size'],'sha256':row['sha256']})
        if row['aad']!=expected: raise IntegrityError('附件绑定认证失败')
        raw=cab._open(row,ReleaseRequest(asset,user['id'],'preview',mid))
        if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']: raise IntegrityError('附件内容认证失败')
        if gid: db.execute('UPDATE grants SET used=used+1 WHERE id=?',(gid,))
        cab._audit(db,'attachment_preview',user['id'],asset=asset,attachment=mid,grant=gid)
        return raw,row['mime']


def discard(cab,user,mid):
    import chunked_attachments as chunks
    with cab._transaction() as db:
        chunked=chunks.installed(db) and db.execute('SELECT 1 FROM attachment_uploads WHERE id=?',(mid,)).fetchone()
    if chunked: return chunks.cancel(cab,user,mid)
    with cab._transaction() as db:
        if not installed(db): raise MediaUnavailable('请先迁移附件表')
        row=db.execute('SELECT owner,asset FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()
        if not row or row['owner']!=user['id'] or row['asset'] is not None: raise Denied('只能删除自己的未绑定附件')
        db.execute('DELETE FROM cabinet_attachments WHERE id=?',(mid,))
        cab._audit(db,'attachment_discarded',user['id'],attachment=mid)
