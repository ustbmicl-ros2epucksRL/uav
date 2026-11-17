"""Offline demo -> Trustee key transition under the existing checkpoint CAS.

Caller holds platform.lock. The prepared database is durable before CAS. Its
new-key HMAC must match the external head before it can replace persistent.db.
"""
import hmac
import os
import sqlite3
from contextlib import closing

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from security_cabinet import LocalDemoBroker, ReleaseRequest, IntegrityError, canonical
from state_guard import CheckpointUnavailable, StateIntegrityError
from .cabinet_keys import HardwareGuard
from .cabinet_persistence import derive


def open_existing(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA trusted_schema=OFF')
    return db


def sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def migrate_rows(db, source, target, head):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'platform_key_mode' in tables:
        modes = [r[0] for r in db.execute('SELECT mode FROM platform_key_mode')]
        if modes != ['demo']:
            raise StateIntegrityError('Expected a demo cabinet')
    else:
        db.execute('CREATE TABLE platform_key_mode (mode TEXT NOT NULL)')
        db.execute("INSERT INTO platform_key_mode VALUES ('demo')")
    old = LocalDemoBroker(derive(source, 'kek'), allow_insecure_demo=True)
    request = ReleaseRequest('migration', 'operator', 'rewrap', 'offline')
    count = 0
    for table in ('assets', 'jobs', 'cabinet_attachments', 'attachment_chunks'):
        if table not in tables:
            continue
        for row in db.execute(f'SELECT rowid AS rid, aad, envelope FROM {table} ORDER BY rowid'):
            if table == 'cabinet_attachments' and not row['envelope']:
                # External attachments carry the DEK on individual chunk rows.
                if 'attachment_uploads' not in tables or not db.execute(
                    'SELECT 1 FROM attachment_uploads u JOIN cabinet_attachments a ON u.id=a.id WHERE a.rowid=?',
                    (row['rid'],)).fetchone():
                    raise IntegrityError('Missing attachment key')
                continue
            dek = old.unwrap(row['envelope'], row['aad'], request)
            wrapped = target.broker.wrap(dek, row['aad'])
            if not hmac.compare_digest(dek, target.broker.unwrap(wrapped, row['aad'], request)):
                raise IntegrityError('Rewrapped key verification failed')
            db.execute(f'UPDATE {table} SET envelope=? WHERE rowid=?', (wrapped, row['rid']))
            count += 1
    old_auth = AESGCM(derive(source, 'account-credentials'))
    new_auth = AESGCM(derive(target, 'account-credentials'))
    for uid, envelope in db.execute('SELECT id,credential FROM auth_users'):
        token = old_auth.decrypt(envelope[:12], envelope[12:], uid.encode())
        nonce = os.urandom(12)
        db.execute('UPDATE auth_users SET credential=? WHERE id=?',
                   (nonce + new_auth.encrypt(nonce, token, uid.encode()), uid))
    # Fresh login also invalidates leases tied to prior login cookies.
    db.execute('UPDATE auth_sessions SET revoked=1')
    old_key = derive(source, 'audit')
    old_prev = new_prev = '0' * 64
    seq = 0
    for row in db.execute('SELECT * FROM audit ORDER BY seq'):
        seq += 1
        event = row['event']
        expected = hmac.digest(old_key, old_prev.encode() + event, 'sha256').hex()
        if seq != row['seq'] or row['prev'] != old_prev or not hmac.compare_digest(expected, row['mac']):
            raise IntegrityError('Source audit chain mismatch')
        mac = hmac.digest(target.audit_key, new_prev.encode() + event, 'sha256').hex()
        db.execute('UPDATE audit SET prev=?, mac=? WHERE seq=?', (new_prev, mac, seq))
        old_prev, new_prev = expected, mac
    event = canonical(dict(seq=seq+1, time=head['time'], actor='operator', action='demo_keys_migrated',
                           details=dict(source_head=head['digest'], source_version=head['version'],
                                        previous_audit_mac=old_prev, envelopes=count)))
    mac = hmac.digest(target.audit_key, new_prev.encode()+event,'sha256').hex()
    db.execute('INSERT INTO audit VALUES (?,?,?,?)', (seq+1, event, new_prev, mac))
    db.execute("UPDATE platform_key_mode SET mode='hardware'")


def migrate(folder, source, target):
    if not isinstance(target, HardwareGuard) or source.namespace != target.namespace:
        raise ValueError('Migration requires a hardware target in the existing namespace')
    if source.checkpoint.url != target.checkpoint.url or source.key == target.key:
        raise ValueError('Use the same checkpoint origin and an independent target state key')
    active = folder/'persistent.db'
    staged = folder/'hardware-migration.pending.db'
    if active.is_symlink() or staged.is_symlink():
        raise ValueError('Expected regular cabinet database paths')
    # Replacing a WAL database without its sidecars can mix database epochs.
    # This platform uses the default SQLite DELETE journal.
    with closing(open_existing(active)) as db:
        if db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise ValueError('Migration requires the platform DELETE journal mode')
    if source.checkpoint.token == target.checkpoint.token:
        raise ValueError('Provision a fresh checkpoint token in KBS')
    try:
        source.checkpoint.head()
    except CheckpointUnavailable:
        # After CAS only the new token works, even if the CAS reply was lost.
        head = target.checkpoint.head()
        for candidate in (active, staged):
            if not candidate.is_file():
                continue
            with closing(open_existing(candidate)) as db:
                if target.commitment(db) != head['digest']:
                    continue
                target.verify(db)
            if candidate == staged:
                os.replace(staged, active)
                sync(folder)
                return 'committed hardware cabinet recovered'
            return 'hardware cabinet already committed'
        raise StateIntegrityError('No local database matches the hardware checkpoint')
    with closing(open_existing(active)) as db:
        head = source.verify(db)
        # Only a verified, still-active source authorizes discarding a partial
        # candidate and its rollback journal from an interrupted staging write.
        for part in (staged, staged.with_name(staged.name + '-journal')):
            if part.exists():
                part.unlink()
        fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(open_existing(staged)) as copy:
            db.backup(copy)
            if source.commitment(copy) != head['digest']:
                raise StateIntegrityError('Source copy mismatch')
            with copy:
                migrate_rows(copy, source, target, head)
            digest = target.commitment(copy)
    sync(staged)
    sync(folder)
    source.checkpoint.rotate(head['version'], head['digest'], digest, target.checkpoint.token)
    with closing(open_existing(staged)) as db:
        target.verify(db)
    os.replace(staged, active)
    sync(folder)
    return 'hardware cabinet committed'
