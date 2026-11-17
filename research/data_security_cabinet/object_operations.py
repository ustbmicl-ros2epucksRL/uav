"""Offline verified object-store probes and migrations; never reset a checkpoint."""
import hashlib
import json
import os
from pathlib import Path
import secrets
from object_store import S3Store
from media_attachments import MediaUnavailable
from security_cabinet import IntegrityError, ReleaseRequest, canonical
import chunked_attachments as chunks


def check(config_file=None, probe=False):
    store=S3Store(config_file)
    for name in ('access_key_file','secret_key_file'):
        path=Path(store.config[name])
        if not path.is_absolute() or not path.read_text().strip() or path.stat().st_mode & 0o077:
            raise ValueError('Credential files must be absolute, nonempty and mode 0600')
    result={'configuration':'valid','endpoint':store.config['endpoint'],'bucket':store.bucket,'prefix':store.prefix}
    if not probe: return result
    from botocore.exceptions import ClientError
    key=secrets.token_hex(32);data=secrets.token_bytes(128)
    store.put(key,data)
    if store.get(key,len(data))!=data: raise IntegrityError('Object readback mismatch')
    try:
        store.client.put_object(Bucket=store.bucket,Key=store.key(key),Body=b'x'*128,IfNoneMatch='*')
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode']!=412: raise MediaUnavailable('Conditional write must reject existing objects with 412') from None
    else: raise IntegrityError('Object service does not enforce If-None-Match')
    if store.get(key,len(data))!=data: raise IntegrityError('Conditional write changed existing data')
    # Test exact provider behaviour rather than assuming S3-compatible means safe.
    from boto3 import client
    from botocore import UNSIGNED
    from botocore.config import Config
    anon=client('s3',endpoint_url=store.config['endpoint'],verify=store.config.get('ca_file',True),
        config=Config(signature_version=UNSIGNED,connect_timeout=3,read_timeout=5,proxies={}))
    try: anon.get_object(Bucket=store.bucket,Key=store.key(key))
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode'] not in (401,403): raise MediaUnavailable('Anonymous access did not fail with 401/403') from None
    else: raise IntegrityError('Anonymous object read is enabled')
    try: store.client.delete_object(Bucket=store.bucket,Key=store.key(key))
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode']!=403: raise MediaUnavailable('Application credential must be denied DeleteObject') from None
    else: raise IntegrityError('Application credential has object deletion rights')
    result.update(read_write='passed',conditional_write='passed',anonymous_denied=True,delete_denied=True,probe_key=store.key(key))
    # Harmless unreachable random object retained: platform credential cannot delete.
    return result


def copy_verified(source,target,row):
    raw=source.get(row['object_key'],row['size']+16)
    if hashlib.sha256(raw).hexdigest()!=row['cipher_sha256']: raise IntegrityError('Source object hash mismatch')
    from botocore.exceptions import ClientError
    try:
        target.client.put_object(Bucket=target.bucket,Key=target.key(row['object_key']),Body=raw,IfNoneMatch='*',ContentType='application/octet-stream')
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode']!=412: raise MediaUnavailable('Destination copy failed') from None
    if target.get(row['object_key'],len(raw))!=raw: raise IntegrityError('Destination object mismatch; never overwrite it')


def move_store(cab,target_config,apply=False):
    source=S3Store();target=S3Store(target_config)
    if source.config_file==target.config_file: raise ValueError('Target must use a separate configuration file')
    # One process lock is held by the CLI throughout. Before commit, active source
    # remains untouched. Failure leaves only extra target ciphertext; retry is safe.
    with cab._transaction() as db:
        chunks.ready(db)
        rows=list(db.execute('SELECT * FROM attachment_chunks ORDER BY upload,idx'))
        if not apply: return {'chunks':len(rows),'cipher_bytes':sum(r['size']+16 for r in rows),'apply':False}
        check(target_config,probe=True)
        for row in rows: copy_verified(source,target,row)
        cab._audit(db,'object_store_copied','operator',chunks=len(rows),target=hashlib.sha256(canonical({k:target.config[k] for k in ('endpoint','bucket','prefix')})).hexdigest())
    # Copy configuration atomically only after independent checkpoint commit.
    # Failure here keeps source usable. Neither original objects nor state are reset.
    data=json.dumps(target.config,indent=2).encode()
    temp=source.config_file.with_name(source.config_file.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,source.config_file)
        parent=os.open(source.config_file.parent,os.O_RDONLY)
        try:os.fsync(parent)
        finally:os.close(parent)
    finally:
        if temp.exists():temp.unlink()
    return {'chunks':len(rows),'apply':True,'switched':True}


def move_inline(cab,apply=False):
    store=S3Store();moved=0
    with cab._transaction() as db:
        chunks.ready(db)
        ids=[r[0] for r in db.execute('SELECT id FROM cabinet_attachments WHERE id NOT IN (SELECT id FROM attachment_uploads)')]
    if not apply:return {'inline_attachments':len(ids),'apply':False}
    check(probe=True)
    for mid in ids:
        with cab._transaction() as db:
            if db.execute('SELECT 1 FROM attachment_uploads WHERE id=?',(mid,)).fetchone():continue
            row=db.execute('SELECT * FROM cabinet_attachments WHERE id=?',(mid,)).fetchone()
            expected=canonical({'type':'attachment',**{k:row[k] for k in ('id','owner','template_id','version','digest','field','mime','size','sha256')}})
            if row['aad']!=expected:raise IntegrityError('Legacy attachment binding mismatch')
            raw=cab._open(row,ReleaseRequest(row['asset'] or mid,row['owner'],'preview',mid))
            if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise IntegrityError('Legacy attachment digest mismatch')
            db.execute('INSERT INTO attachment_uploads VALUES (?,?,?,?,?,?,?,?,?,?)',
                tuple(row[k] for k in ('id','owner','template_id','version','digest','field','mime','size','expires'))+('complete',))
            for index,start in enumerate(range(0,len(raw),chunks.CHUNK_BYTES)):
                part=raw[start:start+chunks.CHUNK_BYTES];sha=hashlib.sha256(part).hexdigest();aad=chunks.aad_for(row,index,len(part),sha)
                nonce,ciphertext,envelope=cab._seal(part,aad);key=secrets.token_hex(32)
                store.put(key,ciphertext)
                if store.get(key,len(ciphertext))!=ciphertext:raise IntegrityError('Migration readback failed')
                db.execute('INSERT INTO attachment_chunks VALUES (?,?,?,?,?,?,?,?,?)',
                    (mid,index,key,len(part),sha,hashlib.sha256(ciphertext).hexdigest(),aad,nonce,envelope))
            db.execute("UPDATE cabinet_attachments SET aad=x'',nonce=x'',ciphertext=x'',envelope=x'' WHERE id=?",(mid,))
            cab._audit(db,'inline_attachment_migrated','operator',attachment=mid)
        moved+=1
    return {'migrated':moved,'apply':True}
