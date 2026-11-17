"""Immutable encrypted chunks; upload manifests and preview leases are checkpointed.

Only bounded plaintext chunks exist in process memory. A chunk is never released
until the full manifest transaction has passed the independent checkpoint CAS.
"""
import hashlib
import math
import re
import secrets
import media_attachments as media
from security_cabinet import Denied, IntegrityError, ReleaseRequest, canonical
from object_store import configured_store

CHUNK_BYTES=1024*1024
MAX_BYTES=2*1024**3
CAPACITY=20*1024**3
LEASE_SECONDS=300


def installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='attachment_uploads'").fetchone())


def ready(db):
    if not installed(db): raise media.MediaUnavailable('请先执行 migrate-chunks 迁移分块清单')


def install(cab):
    with cab._transaction() as db:
        if not media.installed(db): raise ValueError('Migrate attachments first')
        if installed(db): raise ValueError('Chunks already installed')
        db.execute('CREATE TABLE attachment_uploads (id TEXT PRIMARY KEY, owner TEXT NOT NULL, template_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, field TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL)')
        db.execute('CREATE TABLE attachment_chunks (upload TEXT NOT NULL, idx INTEGER NOT NULL, object_key TEXT UNIQUE NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, cipher_sha256 TEXT NOT NULL, aad BLOB NOT NULL, nonce BLOB NOT NULL, envelope BLOB NOT NULL, PRIMARY KEY(upload,idx))')
        db.execute('CREATE TABLE attachment_streams (id TEXT PRIMARY KEY, subject TEXT NOT NULL, user_version INTEGER NOT NULL, login_hash TEXT NOT NULL, asset TEXT NOT NULL, attachment TEXT NOT NULL, grant_id TEXT, expires REAL NOT NULL)')
        cab._audit(db,'chunk_schema_installed','operator')


def start(cab,user,tid,version,field,mime,size):
    with cab._transaction() as db:
        ready(db)
        if user['role']!='owner': raise Denied('只有所有者可上传')
        row,spec=media.published_field(db,user,tid,version,field)
        limit=MAX_BYTES if mime.startswith('video/') else media.MAX_BYTES
        if type(size)!=int or not 0<size<=min(limit,spec['misc']['max_bytes']) or mime not in spec['misc']['mime']:
            raise ValueError('附件类型或大小不符合模板')
        count=db.execute("SELECT count(*) FROM attachment_uploads WHERE owner=? AND state='uploading'",(user['id'],)).fetchone()[0]
        total=db.execute('SELECT COALESCE(sum(size),0) FROM attachment_uploads').fetchone()[0]
        if count>=16 or total+size>CAPACITY: raise ValueError('上传会话或存储配额不足，请取消未完成上传')
        configured_store()  # Configuration must exist before accepting a reservation.
        mid=secrets.token_hex(16)
        db.execute('INSERT INTO attachment_uploads VALUES (?,?,?,?,?,?,?,?,?,?)',
            (mid,user['id'],tid,version,row['digest'],field,mime,size,cab.clock()+86400,'uploading'))
        cab._audit(db,'chunk_upload_started',user['id'],attachment=mid,size=size)
    return {'upload_id':mid,'chunk_bytes':CHUNK_BYTES,'size':size}


def own(db,cab,user,mid):
    ready(db)
    row=db.execute('SELECT * FROM attachment_uploads WHERE id=?',(mid,)).fetchone()
    if not row or user['role']!='owner' or row['owner']!=user['id']: raise Denied('上传会话不属于当前所有者')
    template,_=media.published_field(db,user,row['template_id'],row['version'],row['field'])
    if template['digest']!=row['digest']: raise IntegrityError('上传模板摘要不一致')
    if row['state']=='uploading' and row['expires']<=cab.clock(): raise Denied('上传会话已过期，请取消后重新建立')
    return row


def status(cab,user,mid):
    with cab._transaction() as db:
        row=own(db,cab,user,mid)
        return {**{k:row[k] for k in ('id','template_id','version','field','mime','size','expires','state')},
            'chunk_bytes':CHUNK_BYTES,'chunks':[dict(r) for r in db.execute('SELECT idx,size,sha256 FROM attachment_chunks WHERE upload=? ORDER BY idx',(mid,))]}


def aad_for(row,index,size,sha):
    return canonical({'type':'attachment-chunk-v1','id':row['id'],'owner':row['owner'],
        'template_id':row['template_id'],'version':row['version'],'digest':row['digest'],
        'field':row['field'],'mime':row['mime'],'total':row['size'],'index':index,'size':size,'sha256':sha})


def put(cab,user,mid,index,raw):
    with cab._transaction() as db:
        row=own(db,cab,user,mid)
        if row['state']!='uploading': raise Denied('已完成的上传不能修改')
        count=math.ceil(row['size']/CHUNK_BYTES)
        if index<0 or index>=count or len(raw)!=min(CHUNK_BYTES,row['size']-index*CHUNK_BYTES):
            raise ValueError('分块序号或长度非法')
        sha=hashlib.sha256(raw).hexdigest()
        old=db.execute('SELECT sha256 FROM attachment_chunks WHERE upload=? AND idx=?',(mid,index)).fetchone()
        if old:
            if old['sha256']!=sha: raise IntegrityError('已提交的分块不可替换，请建立新的上传会话')
            return {'index':index,'sha256':sha,'reused':True}
        aad=aad_for(row,index,len(raw),sha)
        nonce,ciphertext,envelope=cab._seal(raw,aad)
        key=secrets.token_hex(32);configured_store().put(key,ciphertext)
        db.execute('INSERT INTO attachment_chunks VALUES (?,?,?,?,?,?,?,?,?)',
            (mid,index,key,len(raw),sha,hashlib.sha256(ciphertext).hexdigest(),aad,nonce,envelope))
        cab._audit(db,'chunk_uploaded',user['id'],attachment=mid,index=index,size=len(raw))
    return {'index':index,'sha256':sha,'reused':False}


def decrypt(cab,store,row,chunk,user,asset):
    expected=aad_for(row,chunk['idx'],chunk['size'],chunk['sha256'])
    if chunk['aad']!=expected: raise IntegrityError('分块绑定不一致')
    ciphertext=store.get(chunk['object_key'],chunk['size']+16)
    if hashlib.sha256(ciphertext).hexdigest()!=chunk['cipher_sha256']: raise IntegrityError('分块密文摘要错误')
    opened=cab._open({**dict(chunk),'ciphertext':ciphertext},ReleaseRequest(asset,user['id'],'preview',row['id']))
    if len(opened)!=chunk['size'] or hashlib.sha256(opened).hexdigest()!=chunk['sha256']: raise IntegrityError('分块明文摘要错误')
    return opened


def finish(cab,user,mid):
    with cab._transaction() as db:
        row=own(db,cab,user,mid)
        if row['state']=='complete': return {'attachment_id':mid,'size':row['size'],'field':row['field']}
        chunks=list(db.execute('SELECT * FROM attachment_chunks WHERE upload=? ORDER BY idx',(mid,)))
        if [r['idx'] for r in chunks]!=list(range(math.ceil(row['size']/CHUNK_BYTES))): raise ValueError('分块尚未上传完整')
        sha=hashlib.sha256();prefix=bytearray();store=configured_store()
        for chunk in chunks:
            raw=decrypt(cab,store,row,chunk,user,mid);sha.update(raw)
            if len(prefix)<media.MAX_BYTES: prefix.extend(raw[:media.MAX_BYTES-len(prefix)])
        # Images are fully checked. Videos must expose their codec metadata within
        # the first 8 MiB (faststart/fragmented MP4 or WebM). This is not full decoding.
        media.validate_media(bytes(prefix),row['mime'])
        db.execute('INSERT INTO cabinet_attachments VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)',
            (mid,row['owner'],row['template_id'],row['version'],row['digest'],row['field'],row['mime'],row['size'],sha.hexdigest(),cab.clock()+86400,b'',b'',b'',b''))
        db.execute("UPDATE attachment_uploads SET state='complete' WHERE id=?",(mid,))
        cab._audit(db,'chunk_upload_completed',user['id'],attachment=mid,size=row['size'],sha256=sha.hexdigest())
    return {'attachment_id':mid,'size':row['size'],'field':row['field']}


def cancel(cab,user,mid):
    with cab._transaction() as db:
        ready(db)
        row=db.execute('SELECT * FROM attachment_uploads WHERE id=?',(mid,)).fetchone()
        attachment=db.execute('SELECT asset FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()
        if not row or row['owner']!=user['id'] or user['role']!='owner' or (attachment and attachment['asset']): raise Denied('只能取消自己的未绑定上传')
        db.execute('DELETE FROM cabinet_attachments WHERE id=?',(mid,))
        db.execute('DELETE FROM attachment_chunks WHERE upload=?',(mid,))
        db.execute('DELETE FROM attachment_uploads WHERE id=?',(mid,))
        cab._audit(db,'chunk_upload_cancelled',user['id'],attachment=mid)
        # Do not delete external objects during a non-atomic checkpoint transaction.
        # Unreferenced ciphertext is reclaimed offline against a verified manifest.


def bound(db,mid,asset):
    ready(db)
    row=db.execute("SELECT u.* FROM attachment_uploads u JOIN cabinet_attachments a ON a.id=u.id WHERE u.id=? AND a.asset=? AND u.state='complete'",(mid,asset)).fetchone()
    if not row: raise Denied('附件不是当前资产的已完成分块附件')
    b=db.execute('SELECT * FROM asset_templates WHERE asset=?',(asset,)).fetchone()
    if not b or (b['template_id'],b['version'],b['digest'])!=(row['template_id'],row['version'],row['digest']): raise IntegrityError('附件模板绑定错误')
    return row


def open_stream(cab,user,asset,mid,login_hash,gid=None):
    with cab._transaction() as db:
        media.access(cab,db,user,asset,gid,consume=True);bound(db,mid,asset)
        sid=secrets.token_hex(24)
        db.execute('DELETE FROM attachment_streams WHERE expires<=?',(cab.clock(),))
        db.execute('INSERT INTO attachment_streams VALUES (?,?,?,?,?,?,?,?)',
            (sid,user['id'],user['version'],login_hash,asset,mid,gid,cab.clock()+LEASE_SECONDS))
        if gid: db.execute('UPDATE grants SET used=used+1 WHERE id=?',(gid,))
        cab._audit(db,'attachment_stream_opened',user['id'],asset=asset,attachment=mid,grant=gid)
    return {'stream_id':sid,'expires_in':LEASE_SECONDS}


class InvalidRange(ValueError): pass


def byte_range(header,total):
    if header is None: return 0,min(total,CHUNK_BYTES)-1
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
    if not match or not any(match.groups()): raise InvalidRange('只接受单一字节范围')
    left,right=match.groups()
    if len(left)>20 or len(right)>20: raise InvalidRange('范围非法')
    if not left:
        suffix=int(right)
        if suffix<=0: raise InvalidRange('范围非法')
        start=max(0,total-suffix);end=total-1
    else:
        start=int(left);end=int(right) if right else total-1
    if start>=total or end<start: raise InvalidRange('范围超出文件')
    return start,min(end,total-1,start+CHUNK_BYTES-1)


def read_stream(cab,user,sid,login_hash,range_header):
    with cab._transaction() as db:
        ready(db)
        lease=db.execute('SELECT * FROM attachment_streams WHERE id=?',(sid,)).fetchone()
        if (not lease or lease['subject']!=user['id'] or lease['user_version']!=user['version']
                or lease['login_hash']!=login_hash or lease['expires']<=cab.clock()): raise Denied('流会话不属于当前登录或已到期')
        media.access(cab,db,user,lease['asset'],lease['grant_id'])
        row=bound(db,lease['attachment'],lease['asset'])
        start,end=byte_range(range_header,row['size']);out=bytearray();store=configured_store()
        for index in range(start//CHUNK_BYTES,end//CHUNK_BYTES+1):
            chunk=db.execute('SELECT * FROM attachment_chunks WHERE upload=? AND idx=?',(row['id'],index)).fetchone()
            if chunk is None: raise IntegrityError('分块清单不完整')
            raw=decrypt(cab,store,row,chunk,user,lease['asset'])
            out.extend(raw[max(0,start-index*CHUNK_BYTES):min(len(raw),end-index*CHUNK_BYTES+1)])
        if len(out)!=end-start+1: raise IntegrityError('范围长度错误')
        cab._audit(db,'attachment_range_read',user['id'],asset=lease['asset'],attachment=row['id'],grant=lease['grant_id'],start=start,end=end)
        result=(bytes(out),row['mime'],start,end,row['size'])
    return result
