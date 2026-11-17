"""Local account authentication and organisation policy in committed cabinet state."""
import hashlib
import hmac
import json
import re
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from .cabinet_persistence import derive

ROLES = ('owner', 'dataset', 'stats', 'org_admin')
SESSION_TTL = 8 * 3600
SCHEMA = (
    'CREATE TABLE auth_orgs (id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL)',
    'CREATE TABLE auth_users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, org TEXT NOT NULL, role TEXT NOT NULL, salt BLOB NOT NULL, password BLOB NOT NULL, credential BLOB NOT NULL, enabled INTEGER NOT NULL, version INTEGER NOT NULL)',
    'CREATE TABLE auth_sessions (sid_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, version INTEGER NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL)',
    'CREATE TABLE auth_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL)',
)


def install(cab):
    """Explicit offline migration only; never trust or adopt legacy demo identities."""
    with cab._transaction() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='auth_users'").fetchone():
            raise ValueError('Account schema already installed')
        for statement in SCHEMA:
            db.execute(statement)
        db.execute('DELETE FROM platform_sessions')
        cab._audit(db, 'auth_schema_installed', 'operator')


def require_schema(cab):
    with cab._transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='auth_users'").fetchone():
            raise HTTPException(503, '请先执行离线账号迁移；演示身份入口已关闭。')


def username(value):
    value = value.strip().lower()
    if not re.fullmatch('[a-z0-9][a-z0-9_.@-]{2,79}', value):
        raise ValueError('用户名需为 3–80 位小写字母、数字或 _.@-')
    return value


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256*1024*1024, dklen=32)


def validate_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError('密码须为 12–128 个字符')


def add_org(cab, name):
    if not 1 <= len(name) <= 100:
        raise ValueError('组织名称须为 1–100 字符')
    oid = secrets.token_hex(16)
    with cab._transaction() as db:
        db.execute('INSERT INTO auth_orgs VALUES (?,?,1)', (oid, name))
        cab._audit(db, 'org_created', 'operator', org=oid)
    return oid


def add_user(cab, org, name, password, role, admin=None):
    name = username(name); validate_password(password)
    if role not in ROLES or (admin and role == 'org_admin'):
        raise HTTPException(403, '不允许分配此角色')
    if admin and (admin['role'] != 'org_admin' or admin['org'] != org):
        raise HTTPException(403, '只能管理本组织成员')
    salt = secrets.token_bytes(16); hashed = password_hash(password, salt)
    uid, token = secrets.token_hex(16), secrets.token_urlsafe(32)
    nonce = secrets.token_bytes(12)
    encrypted = nonce + AESGCM(derive(cab.state_guard, 'account-credentials')).encrypt(nonce, token.encode(), uid.encode())
    with cab._transaction() as db:
        if not db.execute('SELECT 1 FROM auth_orgs WHERE id=? AND enabled=1', (org,)).fetchone():
            raise HTTPException(403, '组织不可用')
        if db.execute('SELECT 1 FROM auth_users WHERE username=?', (name,)).fetchone():
            raise HTTPException(409, '用户名不可用')
        db.execute('INSERT INTO identities VALUES (?,?,?)', (uid, name, hashlib.sha256(token.encode()).hexdigest()))
        db.execute('INSERT INTO auth_users VALUES (?,?,?,?,?,?,?,1,1)', (uid, name, org, role, salt, hashed, encrypted))
        cab._audit(db, 'user_created', admin['id'] if admin else 'operator', subject=uid, org=org, role=role)
    return uid


def public(user):
    return {k:user[k] for k in ('id', 'username', 'org', 'role', 'enabled')}


def csrf(cab, sid):
    return hmac.new(derive(cab.state_guard, 'csrf'), sid.encode(), 'sha256').hexdigest()


def login(cab, name, password, old_sid=None):
    name = name.strip().lower()
    key = hashlib.sha256(name.encode()).hexdigest()
    sid = secrets.token_urlsafe(32)
    failure = False; limited = False
    with cab._transaction() as db:
        now = cab.clock()
        # Global bounded rolling bucket, plus per-account failed-attempt lockout.
        global_row = db.execute("SELECT count,until FROM auth_attempts WHERE key='global'").fetchone()
        attempts = db.execute('SELECT count,until FROM auth_attempts WHERE key=?', (key,)).fetchone()
        limited = bool((global_row and global_row['until']>now and global_row['count']>=100) or
                       (attempts and attempts['until']>now and attempts['count']>=5))
        if not limited:
            count = global_row['count']+1 if global_row and global_row['until']>now else 1
            until = global_row['until'] if global_row and global_row['until']>now else now+60
            db.execute('INSERT OR REPLACE INTO auth_attempts VALUES (?,?,?)', ('global', count, until))
            user = db.execute('SELECT u.*,o.enabled AS org_enabled FROM auth_users u JOIN auth_orgs o ON o.id=u.org WHERE username=?', (name,)).fetchone()
            hashed = password_hash(password, user['salt'] if user else bytes(16))
            valid = user and hmac.compare_digest(hashed, user['password']) and user['enabled'] and user['org_enabled']
            if not valid:
                failure = True
                count = attempts['count']+1 if attempts and attempts['until']>now else 1
                db.execute('INSERT OR REPLACE INTO auth_attempts VALUES (?,?,?)', (key, count, now+300))
                cab._audit(db, 'login_failed', user['id'] if user else 'unknown')
            else:
                db.execute('DELETE FROM auth_attempts WHERE key=?', (key,))
                if old_sid:
                    db.execute('UPDATE auth_sessions SET revoked=1 WHERE sid_hash=?', (hashlib.sha256(old_sid.encode()).hexdigest(),))
                db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,0)', (hashlib.sha256(sid.encode()).hexdigest(), user['id'], user['version'], now+SESSION_TTL))
                cab._audit(db, 'login', user['id'])
        db.execute("DELETE FROM auth_attempts WHERE until<?", (now,))
    if limited: raise HTTPException(429, '登录尝试过多，请稍后重试')
    if failure: raise HTTPException(401, '账号或密码错误，或账号不可用')
    return sid


def authenticate(cab, sid):
    with cab._transaction() as db:
        row = db.execute('SELECT u.* FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id JOIN auth_orgs o ON o.id=u.org WHERE s.sid_hash=? AND s.revoked=0 AND s.expires>? AND s.version=u.version AND u.enabled=1 AND o.enabled=1',
                         (hashlib.sha256((sid or '').encode()).hexdigest(), cab.clock())).fetchone()
        if row is None: raise HTTPException(401, '请登录；会话可能已过期或失效')
        user = dict(row)
    envelope = user.pop('credential')
    user['token'] = AESGCM(derive(cab.state_guard, 'account-credentials')).decrypt(envelope[:12], envelope[12:], user['id'].encode()).decode()
    return user


def logout(cab, sid, user):
    with cab._transaction() as db:
        db.execute('UPDATE auth_sessions SET revoked=1 WHERE sid_hash=?', (hashlib.sha256(sid.encode()).hexdigest(),))
        cab._audit(db, 'logout', user['id'])


def set_enabled(cab, user, target, enabled):
    if user['role'] != 'org_admin': raise HTTPException(403, '需要组织管理员权限')
    with cab._transaction() as db:
        row = db.execute('SELECT * FROM auth_users WHERE id=? AND org=?', (target, user['org'])).fetchone()
        if not row or row['role']=='org_admin': raise HTTPException(403, '不能管理该成员')
        db.execute('UPDATE auth_users SET enabled=?,version=version+1 WHERE id=?', (int(enabled), target))
        cab._audit(db, 'user_enabled', user['id'], subject=target, enabled=enabled, org=user['org'])


def change_password(cab, user, old, new):
    validate_password(new)
    if not hmac.compare_digest(password_hash(old, user['salt']), user['password']):
        raise HTTPException(403, '原密码不正确')
    salt = secrets.token_bytes(16); hashed = password_hash(new, salt)
    with cab._transaction() as db:
        db.execute('UPDATE auth_users SET salt=?,password=?,version=version+1 WHERE id=?', (salt, hashed, user['id']))
        cab._audit(db, 'password_changed', user['id'])


def assert_purpose(user, purpose):
    if purpose not in ({'dataset','preview'} if user['role']=='dataset' else {'stats'} if user['role']=='stats' else set()):
        raise HTTPException(403, '当前组织角色不允许此用途')


def set_role(cab, admin, target, role):
    if admin['role'] != 'org_admin' or role not in ('owner','dataset','stats'):
        raise HTTPException(403, '不允许修改此角色')
    with cab._transaction() as db:
        row=db.execute('SELECT * FROM auth_users WHERE id=? AND org=?',(target,admin['org'])).fetchone()
        if not row or row['role']=='org_admin': raise HTTPException(403, '不能管理该成员')
        db.execute('UPDATE auth_users SET role=?,version=version+1 WHERE id=?',(role,target))
        # Old capabilities must not revive after role changes in either direction.
        db.execute('UPDATE grants SET revoked=1 WHERE subject=? OR asset IN (SELECT id FROM assets WHERE owner=?)',(target,target))
        cab._audit(db,'user_role_changed',admin['id'],subject=target,role=role,org=admin['org'])
