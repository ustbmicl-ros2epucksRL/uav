"""Durable cabinet keys and explicit legacy schema compatibility.

Imported after the cabinet prototype path is configured by cabinet_demo.
No session/authorization mapping outside the committed database is authoritative.
"""
import hmac
from security_cabinet import Cabinet, LocalDemoBroker
from state_guard import StateIntegrityError
from template_registry import Policy
from . import cabinet_keys as keys


def derive(guard, label):
    return hmac.digest(guard.key, ('platform-v1:'+guard.namespace+':'+label).encode(), 'sha256')


def open_cabinet(folder, guard):
    path = folder/'persistent.db'
    initialize = not path.exists()
    # Missing local DB is never permission to reset an existing remote head.
    mode = keys.selected_mode()
    if mode == 'hardware':
        if not isinstance(guard, keys.HardwareGuard):
            raise keys.KeyConfigurationError('硬件模式要求远程证明密钥')
        audit_key, operator_token, broker = guard.audit_key, guard.operator_token, guard.broker
    else:
        if isinstance(guard, keys.HardwareGuard):
            raise keys.KeyConfigurationError('演示模式收到硬件配置')
        audit_key = derive(guard, 'audit')
        operator_token = derive(guard, 'operator').hex()
        broker = LocalDemoBroker(derive(guard, 'kek'), allow_insecure_demo=True)
    cab = Cabinet(path, audit_key=audit_key, operator_token=operator_token, broker=broker,
                  state_guard=guard, initialize_state=initialize, container_policy=Policy())
    cab.key_mode = mode
    with cab._transaction() as db:
        marked = db.execute("SELECT 1 FROM sqlite_master WHERE name='platform_key_mode'").fetchone()
        if marked:
            rows = db.execute('SELECT mode FROM platform_key_mode').fetchall()
            if len(rows) != 1 or rows[0][0] != mode:
                raise StateIntegrityError('Cabinet key mode mismatch')
        elif mode == 'hardware' and not initialize:
            raise StateIntegrityError('Existing legacy cabinet requires explicit key migration')
        elif initialize:
            db.execute('CREATE TABLE platform_key_mode (mode TEXT NOT NULL)')
            db.execute('INSERT INTO platform_key_mode VALUES (?)', (mode,))
        if initialize:
            db.execute('CREATE TABLE platform_sessions (sid_hash TEXT PRIMARY KEY, payload BLOB NOT NULL, expires REAL NOT NULL)')
        elif not db.execute("SELECT 1 FROM sqlite_master WHERE name='platform_sessions'").fetchone():
            raise StateIntegrityError('Unsupported registry schema; explicit migration required')
    return cab
