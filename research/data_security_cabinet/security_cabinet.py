#!/usr/bin/env python3
"""数据安全柜设计与实现 v0.3 —— 单文件研究原型（非生产安全产品）。

四个模块
========
1. 数据安全柜：SQLite 元数据及密文对象；AES-256-GCM 每对象随机 DEK；
   DEK 由外部提供的 KEK 封装；对象 ID、用途和内容摘要通过 AAD 绑定。
2. 防 root 窃取接口：KeyBroker 默认拒绝释放；LocalDemoBroker 仅显式测试用。
   真实防护必须把本执行器及明文放进经证明的 TEE，将 broker 部署到独立
   信任域。必须验证硬件证明的新鲜性、镜像、配置、任务与通道绑定。
   trustee_broker.py 接入固定版本 Trustee 客户端；prepare_trustee.py 准备独立服务。
   本文件自身不执行硬件证明，普通主机运行不防 root；详见 README.md。
3. 一份数据多方交易：持有者令牌身份、数据所有者授权、用途/期限/次数控制；
   BEGIN IMMEDIATE 串行化授权校验、扣次、结果入库和审计。交易指使用权授权，
   不含支付、合同或法律意义的确权。撤销阻止后续执行和结果读取，已导出结果
   无法收回；已开始事务完成后撤销才生效。大规模部署应采用短租约及分片密钥。
4. 模型可用数据：固定 JSONL 加工器校验 features/label/group_id，去重、
   分组划分 train/validation/test，生成带来源、版本与统计的 JSON 数据集。
   stats 任务只输出固定汇总。无任意代码执行、网络接口和外部程序调用。

可信范围与限制
==============
- operator_token 属于独立控制面的操作者；API 调用者只用身份 bearer token，
  不允许用自报用户名替代认证。令牌仅创建时返回，数据库保存其 SHA-256。
- KEK、审计密钥、operator_token 由调用者从外部秘密服务注入，不写入数据库。
  KeyBroker 是进程内协议；TrusteeKeyBroker 经证明客户端访问独立 KBS。
  该远程路径尚须在真实硬件验收；调用它的本执行器必须受信。
- 入库 API 接收的明文已在本进程中；真实部署应在可信采集端/TEE 内执行入库。
- 单独的审计链不防整体回滚；启用 state_guard 后，每次事务验证完整状态并
  向独立服务推进版本。部署与故障恢复见 AUTHORIZATION_STATE.md；未启用时
  仅可用于本地演示，不具备授权状态防回滚能力。
- 未授权拒绝、加工失败不扣额度；认证及执行阶段记录失败审计。数据库不可写时请求失败关闭。
- SQLite 的资产名称、主体、摘要、授权及用量是明文元数据。文件权限只提供
  本机普通用户隔离，不是 root 防护。全量内存处理上限 16 MiB，不支持大视频、
  GPU 训练、远程标注、物理攻击、侧信道、拒绝服务或隐私预算保证。
- 无自制密码算法：仅用 cryptography 的 AESGCM；敏感缓冲区在 Python 中不能
  保证清零。导出数据、模型记忆泄露和侧信道需另设输出治理策略。

运行（Python >= 3.10；唯一第三方依赖 cryptography >= 41）
========================================================
  python3 -m venv /tmp/cabinet-venv
  /tmp/cabinet-venv/bin/pip install 'cryptography>=41'
  /tmp/cabinet-venv/bin/python security_cabinet.py selftest
  /tmp/cabinet-venv/bin/python security_cabinet.py demo
  python security_cabinet.py design

selftest/demo 仅在临时目录中使用明确标注的合成数据，结束自动清理；不产生
持久密钥。业务集成可导入 Cabinet 与 KeyBroker；不要把 demo broker 当成 TEE。
验收：自测覆盖密文、授权隔离、撤销/到期、并发额度、完整性、防篡改、失败
回滚、模型样本与分组隔离。硬件 root 防护与吞吐/时延须另行测试，不声称 1%。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
except ImportError:
    raise SystemExit("缺少依赖：请在独立虚拟环境安装 cryptography>=41")

MAX_BYTES = 16 * 1024 * 1024
PIPELINE_VERSION = 'grouped-features-v1'
TASKS = frozenset({'stats', 'dataset', 'preview'})


class Denied(Exception):
    """认证、授权或密钥释放被拒绝。"""


class IntegrityError(Exception):
    """对象或审计完整性校验失败。"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ReleaseRequest:
    asset_id: str
    principal: str
    purpose: str
    job_id: str


class KeyBroker(Protocol):
    """远程生产实现须在可信通道内封装/释放，并自行验证证明与授权。"""
    def wrap(self, key: bytes, aad: bytes) -> bytes: ...
    def unwrap(self, envelope: bytes, aad: bytes, request: ReleaseRequest) -> bytes: ...


class DenyBroker:
    def wrap(self, key, aad):
        raise Denied('尚未配置可信密钥服务')

    def unwrap(self, envelope, aad, request):
        raise Denied('没有可验证的可信执行环境，拒绝密钥释放')


class LocalDemoBroker:
    """只验证加密流程，不验证硬件证明。密钥位于普通 Python 内存。"""
    def __init__(self, kek: bytes, *, allow_insecure_demo=False):
        if not allow_insecure_demo:
            raise Denied('必须显式允许不具备 root 防护的本地演示')
        if len(kek) != 32:
            raise ValueError('KEK 必须为 32 字节')
        self.cipher = AESGCM(kek)

    def wrap(self, key, aad):
        nonce = secrets.token_bytes(12)
        return nonce + self.cipher.encrypt(nonce, key, aad)

    def unwrap(self, envelope, aad, request):
        if not isinstance(request, ReleaseRequest):
            raise Denied('缺少任务上下文')
        return self.cipher.decrypt(envelope[:12], envelope[12:], aad)


SCHEMA = '''
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    version INTEGER NOT NULL, sha256 TEXT NOT NULL, aad BLOB NOT NULL,
    nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, envelope BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY, asset TEXT NOT NULL, subject TEXT NOT NULL,
    purpose TEXT NOT NULL, expires REAL NOT NULL, quota INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, subject TEXT NOT NULL,
    asset TEXT NOT NULL, purpose TEXT NOT NULL, aad BLOB NOT NULL,
    nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, envelope BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY, event BLOB NOT NULL, prev TEXT NOT NULL, mac TEXT NOT NULL);
'''


class Cabinet:
    """仅供可信控制面调用的本地库；不向不可信调用者开放 broker 或数据库。"""
    def __init__(self, database, *, audit_key, operator_token, broker=None, clock=time.time,
                 state_guard=None, initialize_state=False, container_policy=None):
        if len(audit_key) < 32 or len(operator_token) < 32:
            raise ValueError('审计密钥和操作者令牌均需至少 32 字节/字符')
        self.path = Path(database)
        self.audit_key = audit_key
        self.operator_token = operator_token
        self.broker = broker if broker is not None else DenyBroker()
        self.container_policy = container_policy
        self.state_guard = state_guard
        self.clock = state_guard.clock if state_guard is not None else clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('数据库不得为符号链接')
        if state_guard is not None and initialize_state and self.path.exists():
            from state_guard import StateIntegrityError
            raise StateIntegrityError('Bootstrap requires a new database path')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = self._connect()
        memory = None
        try:
            if state_guard is not None:
                memory = sqlite3.connect(':memory:', isolation_level=None)
                memory.row_factory = sqlite3.Row
                memory.execute('PRAGMA trusted_schema=OFF')
                if initialize_state:
                    memory.executescript(SCHEMA)
                    state_guard.initialize_empty(memory)
                    memory.backup(db)
                else:
                    db.backup(memory)
                    state_guard.verify(memory)
            else:
                db.executescript(SCHEMA)
        finally:
            if memory is not None:
                memory.close()
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA trusted_schema=OFF')
        return db

    @contextmanager
    def _transaction(self):
        disk = self._connect()
        db = disk
        try:
            if self.state_guard is not None:
                # An attacker can ignore SQLite file locks. Never execute against
                # disk pages after authenticating them: use an immutable-in-TEE copy.
                db = sqlite3.connect(':memory:', isolation_level=None)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA trusted_schema=OFF')
                disk.backup(db)
            db.execute('BEGIN IMMEDIATE')
            head = self.state_guard.verify(db) if self.state_guard is not None else None
            yield db
            if self.state_guard is not None:
                self.state_guard.finish(db, head)
            db.commit()
            if db is not disk:
                db.backup(disk)
        except Exception:
            db.rollback()
            raise
        finally:
            if db is not disk:
                db.close()
            disk.close()

    def _audit(self, db, action, actor, **details):
        last = db.execute('SELECT seq,mac FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        seq, prev = (last['seq'] + 1, last['mac']) if last else (1, '0' * 64)
        event = canonical({'seq': seq, 'time': self.clock(), 'action': action,
                           'actor': actor, 'details': details})
        mac = hmac.new(self.audit_key, prev.encode() + event, hashlib.sha256).hexdigest()
        db.execute('INSERT INTO audit VALUES (?,?,?,?)', (seq, event, prev, mac))

    @contextmanager
    def _request(self, token, action):
        actor = 'unknown'
        try:
            with self._transaction() as db:
                if not isinstance(token, str):
                    raise Denied('身份验证失败')
                row = db.execute('SELECT id FROM identities WHERE token_hash=?',
                                 (digest(token.encode()),)).fetchone()
                if row is None:
                    raise Denied('身份验证失败')
                actor = row['id']
                yield db, actor
        except Exception as error:
            from state_guard import StateIntegrityError
            if isinstance(error, StateIntegrityError):
                raise  # Do not re-sign damaged state or write a failure audit into it.
            # 不记录令牌、数据或异常消息，避免日志泄露。失败事务已回滚。
            with self._transaction() as db:
                self._audit(db, 'request_failed', actor, operation=action,
                            error=type(error).__name__)
            raise

    def enroll(self, operator_token, name):
        if not secrets.compare_digest(operator_token, self.operator_token):
            raise Denied('操作者认证失败')
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError('主体名称须为 1 至 80 个字符')
        identity, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        with self._transaction() as db:
            db.execute('INSERT INTO identities VALUES (?,?,?)',
                       (identity, name, digest(token.encode())))
            self._audit(db, 'enroll', 'operator', identity=identity)
        return {'id': identity, 'token': token}

    def _seal(self, data, aad):
        dek, nonce = AESGCM.generate_key(bit_length=256), secrets.token_bytes(12)
        return nonce, AESGCM(dek).encrypt(nonce, data, aad), self.broker.wrap(dek, aad)

    def _open(self, row, request):
        try:
            dek = self.broker.unwrap(row['envelope'], row['aad'], request)
            return AESGCM(dek).decrypt(row['nonce'], row['ciphertext'], row['aad'])
        except (InvalidTag, ValueError) as error:
            raise IntegrityError('密钥封装或密文认证失败') from error

    def put(self, token, name, data):
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
            raise ValueError('输入必须为非空 bytes，且不超过 16 MiB')
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValueError('资产名称须为 1 至 120 个字符')
        with self._request(token, 'put') as (db, actor):
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM assets '
                                 'WHERE owner=? AND name=?', (actor, name)).fetchone()[0]
            aid, sha = secrets.token_hex(16), digest(data)
            attachments = None
            if self.container_policy is not None:
                attachments = self.container_policy.on_put(db, actor, aid, data, self.clock())
            aad = canonical({'type': 'asset', 'id': aid, 'owner': actor,
                             'name': name, 'version': version, 'sha256': sha})
            nonce, ciphertext, envelope = self._seal(data, aad)
            db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)',
                       (aid, actor, name, version, sha, aad, nonce, ciphertext, envelope))
            self._audit(db, 'put', actor, asset=aid, version=version, sha256=sha)
            if attachments:
                self._audit(db, 'attachment_bound', actor, asset=aid, attachments=attachments)
        return {'asset_id': aid, 'version': version, 'sha256': sha}

    def grant(self, token, asset_id, subject, purpose, *, expires, quota):
        if purpose not in TASKS:
            raise ValueError('只允许 stats、dataset 或独立 preview 用途')
        if type(quota) is not int or not 1 <= quota <= 100000:
            raise ValueError('额度须为 1 至 100000 的整数')
        if type(expires) not in (int, float) or not math.isfinite(expires):
            raise ValueError('到期时间必须是有限时间戳')
        with self._request(token, 'grant') as (db, actor):
            asset = db.execute('SELECT owner FROM assets WHERE id=?', (asset_id,)).fetchone()
            if asset is None or asset['owner'] != actor:
                raise Denied('只有资产所有者可以授权')
            if self.container_policy is not None:
                self.container_policy.check(db, asset_id, purpose)
            if expires <= self.clock():
                raise ValueError('到期时间必须晚于当前时间')
            if db.execute('SELECT 1 FROM identities WHERE id=?', (subject,)).fetchone() is None:
                raise ValueError('被授权主体不存在')
            gid = secrets.token_hex(16)
            db.execute('INSERT INTO grants(id,asset,subject,purpose,expires,quota) '
                       'VALUES (?,?,?,?,?,?)', (gid, asset_id, subject, purpose, expires, quota))
            self._audit(db, 'grant', actor, grant=gid, asset=asset_id, subject=subject,
                        purpose=purpose, expires=expires, quota=quota)
        return gid

    def revoke(self, token, grant_id):
        with self._request(token, 'revoke') as (db, actor):
            row = db.execute('SELECT a.owner FROM grants g JOIN assets a ON a.id=g.asset '
                             'WHERE g.id=?', (grant_id,)).fetchone()
            if row is None or row['owner'] != actor:
                raise Denied('只有资产所有者可以撤销授权')
            db.execute('UPDATE grants SET revoked=1 WHERE id=?', (grant_id,))
            self._audit(db, 'revoke', actor, grant=grant_id)

    def _authorize(self, db, actor, grant_id, *, consume):
        row = db.execute('SELECT * FROM grants WHERE id=?', (grant_id,)).fetchone()
        if row is None or row['subject'] != actor:
            raise Denied('没有对应主体的授权')
        if row['revoked'] or row['expires'] <= self.clock():
            raise Denied('授权已撤销或到期')
        if consume and row['used'] >= row['quota']:
            raise Denied('任务额度已用尽')
        if row['purpose'] not in TASKS:
            raise Denied('任务不在允许列表')
        if self.container_policy is not None:
            self.container_policy.check(db, row['asset'], row['purpose'])
        return row

    def run(self, token, grant_id):
        with self._request(token, 'run') as (db, actor):
            grant = self._authorize(db, actor, grant_id, consume=True)
            if grant['purpose']=='preview': raise Denied('请使用附件预览接口')
            row = db.execute('SELECT * FROM assets WHERE id=?', (grant['asset'],)).fetchone()
            if row is None:
                raise IntegrityError('授权对应资产不存在')
            jid = secrets.token_hex(16)
            request = ReleaseRequest(row['id'], actor, grant['purpose'], jid)
            raw = self._open(row, request)
            expected = canonical({'type': 'asset', 'id': row['id'], 'owner': row['owner'],
                                  'name': row['name'], 'version': row['version'],
                                  'sha256': row['sha256']})
            if row['aad'] != expected or digest(raw) != row['sha256']:
                raise IntegrityError('资产绑定或摘要不一致')
            result = (self.container_policy.transform(db, row['id'], raw, grant['purpose'])
                      if self.container_policy is not None else transform(raw, grant['purpose']))
            result['lineage'] = {'asset_id': row['id'], 'asset_version': row['version'],
                                 'sha256': row['sha256'], 'pipeline': PIPELINE_VERSION}
            payload = canonical(result)
            aad = canonical({'type': 'result', 'id': jid, 'grant': grant_id,
                             'subject': actor, 'asset': row['id'], 'purpose': grant['purpose']})
            nonce, ciphertext, envelope = self._seal(payload, aad)
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                       (jid, grant_id, actor, row['id'], grant['purpose'], aad,
                        nonce, ciphertext, envelope))
            db.execute('UPDATE grants SET used=used+1 WHERE id=?', (grant_id,))
            self._audit(db, 'run', actor, job=jid, grant=grant_id,
                        output_sha256=digest(payload), purpose=grant['purpose'])
        return jid

    def read_result(self, token, job_id):
        with self._request(token, 'read_result') as (db, actor):
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['subject'] != actor:
                raise Denied('结果不属于当前主体')
            grant = self._authorize(db, actor, row['grant_id'], consume=False)
            expected = canonical({'type': 'result', 'id': job_id, 'grant': row['grant_id'],
                                  'subject': actor, 'asset': row['asset'], 'purpose': row['purpose']})
            if (expected != row['aad'] or row['asset'] != grant['asset']
                    or row['purpose'] != grant['purpose']):
                raise IntegrityError('结果绑定不一致')
            payload = self._open(row, ReleaseRequest(row['asset'], actor, row['purpose'], job_id))
            self._audit(db, 'read_result', actor, job=job_id)
            return json.loads(payload)

    def verify_audit(self):
        with self._transaction() as db:
            prev, count = '0' * 64, 0
            for row in db.execute('SELECT * FROM audit ORDER BY seq'):
                count += 1
                mac = hmac.new(self.audit_key, prev.encode() + row['event'], hashlib.sha256).hexdigest()
                if row['seq'] != count or row['prev'] != prev or not hmac.compare_digest(row['mac'], mac):
                    raise IntegrityError('审计链校验失败')
                prev = mac
            return {'events': count, 'head': prev,
                    'external_checkpoint_required': self.state_guard is None}


def transform(raw, purpose):
    """严格的特征 JSONL 加工示例；不把演示特征声称为真实视频/雷达处理。"""
    # A container is validated again after authenticated decryption. Legacy JSONL
    # remains supported; format-like input cannot downgrade to unchecked JSONL.
    try:
        candidate = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        candidate = None
    if isinstance(candidate, dict) and ('template_sha256' in candidate or 'format' in candidate):
        from container_contract import unpack
        mapped, container_lineage = unpack(raw)
        result = transform(mapped, purpose)
        result['container'] = container_lineage
        return result
    rows, seen, dimensions, duplicates = [], {}, None, 0
    lines = raw.decode('utf-8').splitlines()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError) as error:
            raise ValueError(f'第 {number} 行不是合法 JSON') from error
        if not isinstance(item, dict) or set(item) != {'features', 'label', 'group_id'}:
            raise ValueError(f'第 {number} 行必须仅含 features、label、group_id')
        features, label, group = item['features'], item['label'], item['group_id']
        if (not isinstance(features, list) or not 1 <= len(features) <= 4096
                or any(type(x) not in (float, int) or not math.isfinite(x) for x in features)):
            raise ValueError('特征必须是有限数值的一维非空向量，长度不超过 4096')
        if not isinstance(label, str) or not 1 <= len(label) <= 128:
            raise ValueError('label 必须为非空短字符串')
        if not isinstance(group, str) or not 1 <= len(group) <= 128:
            raise ValueError('group_id 必须为非空短字符串')
        if dimensions is not None and len(features) != dimensions:
            raise ValueError('样本特征维数不一致')
        dimensions = len(features)
        # 将 1 与 1.0 归一，避免等价特征绕过去重。
        features = [float(x) if x else 0.0 for x in features]
        key = digest(canonical(features))
        if key in seen:
            if seen[key] != (label, group):
                raise ValueError('相同特征存在跨组重复或标签冲突，请先复核')
            duplicates += 1
            continue
        seen[key] = (label, group)
        rows.append({'features': features, 'label': label, 'group_id': group,
                     'sample_id': key})
    if not rows:
        raise ValueError('没有有效样本')
    groups = sorted({x['group_id'] for x in rows}, key=lambda x: digest(x.encode()))
    quality = {'input_rows': len(rows) + duplicates, 'usable_rows': len(rows),
               'duplicates_removed': duplicates, 'groups': len(groups), 'features': dimensions}
    if purpose == 'stats':
        return {'purpose': 'stats', 'quality': quality}
    if purpose != 'dataset':
        raise ValueError('不允许的任务类型')
    if len(groups) < 3:
        raise ValueError('构建训练/验证/测试集至少需要 3 个独立组')
    n_train = max(1, min(len(groups) - 2, int(len(groups) * 0.7)))
    n_val = max(1, int(len(groups) * 0.15))
    mapping = {g: ('train' if i < n_train else 'validation' if i < n_train+n_val else 'test')
               for i, g in enumerate(groups)}
    splits = {k: [] for k in ('train', 'validation', 'test')}
    for item in sorted(rows, key=lambda x: x['sample_id']):
        splits[mapping[item['group_id']]].append(item)
    return {'purpose': 'dataset', 'quality': quality, 'splits': splits,
            'split_method': 'sha256-group-order-70-15-rest-minimum-one-group'}


def synthetic_data():
    """仅为演示生成的合成特征，非现场验证数据。"""
    return b'\n'.join(canonical({'features': [i, i * 0.25],
                                'label': 'target' if i % 2 else 'background',
                                'group_id': f'scene-{i // 2}'}) for i in range(12))


class TestCabinet(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cabinet-test-')
        self.now = 1000.0
        self.operator = secrets.token_urlsafe(32)
        self.audit_key = secrets.token_bytes(32)
        self.broker = LocalDemoBroker(secrets.token_bytes(32), allow_insecure_demo=True)
        self.cab = Cabinet(Path(self.temp.name)/'vault.db', audit_key=self.audit_key,
                           operator_token=self.operator, broker=self.broker, clock=lambda: self.now)
        self.owner = self.cab.enroll(self.operator, 'owner')
        self.a = self.cab.enroll(self.operator, 'A')
        self.b = self.cab.enroll(self.operator, 'B')
        self.asset = self.cab.put(self.owner['token'], 'synthetic', synthetic_data())

    def tearDown(self):
        self.temp.cleanup()

    def grant(self, who=None, purpose='stats', quota=2):
        who = who or self.a
        return self.cab.grant(self.owner['token'], self.asset['asset_id'], who['id'],
                              purpose, expires=2000, quota=quota)

    def test_multi_party_and_ciphertext(self):
        ga, gb = self.grant(purpose='dataset'), self.grant(self.b)
        ja, jb = self.cab.run(self.a['token'], ga), self.cab.run(self.b['token'], gb)
        dataset = self.cab.read_result(self.a['token'], ja)
        stats = self.cab.read_result(self.b['token'], jb)
        self.assertNotIn('splits', stats)
        self.assertEqual(dataset['lineage']['asset_id'], stats['lineage']['asset_id'])
        sets = [{r['group_id'] for r in dataset['splits'][k]} for k in ('train','validation','test')]
        self.assertTrue(all(sets))
        self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
        self.assertNotIn(synthetic_data(), self.cab.path.read_bytes())
        with self.assertRaises(Denied):
            self.cab.read_result(self.b['token'], ja)
        self.assertGreater(self.cab.verify_audit()['events'], 0)

    def test_authentication_and_owner(self):
        with self.assertRaises(Denied):
            self.cab.run(self.a['id'], self.grant())
        with self.assertRaises(Denied):
            self.cab.grant(self.b['token'], self.asset['asset_id'], self.b['id'],
                           'stats', expires=2000, quota=1)
        with self.assertRaises(Denied):
            self.cab.enroll('wrong', 'attacker')
        with self.assertRaises(Denied):
            self.cab.run(self.b['token'], self.grant())

    def test_expiry_and_revocation(self):
        g = self.grant()
        job = self.cab.run(self.a['token'], g)
        with self.assertRaises(Denied):
            self.cab.revoke(self.b['token'], g)
        self.cab.revoke(self.owner['token'], g)
        with self.assertRaises(Denied):
            self.cab.run(self.a['token'], g)
        with self.assertRaises(Denied):
            self.cab.read_result(self.a['token'], job)
        g = self.grant()
        self.now = 2000
        with self.assertRaises(Denied):
            self.cab.run(self.a['token'], g)

    def test_atomic_quota(self):
        g = self.grant(quota=1)
        def attempt(_):
            try:
                self.cab.run(self.a['token'], g)
                return True
            except Denied:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(8))), 1)
        self.cab.verify_audit()

    def test_tampering_and_failed_task_rollback(self):
        g = self.grant(quota=1)
        with self.cab._transaction() as db:
            db.execute('UPDATE assets SET ciphertext=? WHERE id=?', (b'tampered', self.asset['asset_id']))
        with self.assertRaises(IntegrityError):
            self.cab.run(self.a['token'], g)
        with self.cab._transaction() as db:
            self.assertEqual(db.execute('SELECT used FROM grants WHERE id=?', (g,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        self.cab.verify_audit()

    def test_audit_tamper(self):
        with self.cab._transaction() as db:
            db.execute('UPDATE audit SET event=? WHERE seq=1', (b'{}',))
        with self.assertRaises(IntegrityError):
            self.cab.verify_audit()

    def test_fail_closed(self):
        with self.assertRaises(Denied):
            LocalDemoBroker(secrets.token_bytes(32))
        self.cab.broker = DenyBroker()
        with self.assertRaises(Denied):
            self.cab.run(self.a['token'], self.grant())
        with self.assertRaises(Denied):
            self.cab.put(self.owner['token'], 'blocked', b'abc')

    def test_versioning_and_input_validation(self):
        newer = self.cab.put(self.owner['token'], 'synthetic', synthetic_data())
        self.assertEqual(newer['version'], 2)
        self.assertNotEqual(newer['asset_id'], self.asset['asset_id'])
        for purpose in ('shell', 'download', 'exec'):
            with self.assertRaises(ValueError):
                self.grant(purpose=purpose)
        with self.assertRaises(ValueError):
            self.grant(quota=True)
        with self.assertRaises(ValueError):
            self.cab.grant(self.owner['token'], self.asset['asset_id'], self.a['id'],
                           'stats', expires=float('inf'), quota=1)
        with self.assertRaises(ValueError):
            transform(b'{"features":[NaN],"label":"a","group_id":"g"}', 'stats')
        with self.assertRaises(ValueError):
            transform(b'{"features":[1],"label":"a","group_id":"g"}', 'dataset')
        with self.assertRaises(ValueError):
            transform(b'{}', 'stats')

    def test_dedup_and_conflicts(self):
        raw = synthetic_data()
        first = raw.splitlines()[0]
        self.assertEqual(transform(raw+b'\n'+first, 'dataset')['quality']['duplicates_removed'], 1)
        conflict = json.loads(first)
        conflict['group_id'] = 'another'
        with self.assertRaises(ValueError):
            transform(raw+b'\n'+canonical(conflict), 'dataset')
        self.assertEqual(transform(raw, 'dataset'), transform(b'\n'.join(reversed(raw.splitlines())), 'dataset'))

    def test_invalid_pipeline_rolls_back(self):
        bad = self.cab.put(self.owner['token'], 'invalid-samples', b'{"features":[]}')
        grant = self.cab.grant(self.owner['token'], bad['asset_id'], self.a['id'],
                               'dataset', expires=2000, quota=1)
        with self.assertRaises(ValueError):
            self.cab.run(self.a['token'], grant)
        with self.cab._transaction() as db:
            self.assertEqual(db.execute('SELECT used FROM grants WHERE id=?', (grant,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        self.cab.verify_audit()

    def test_result_tampering_and_tokens_not_stored(self):
        grant = self.grant()
        job = self.cab.run(self.a['token'], grant)
        with self.cab._transaction() as db:
            db.execute('UPDATE jobs SET ciphertext=? WHERE id=?', (b'tampered', job))
        with self.assertRaises(IntegrityError):
            self.cab.read_result(self.a['token'], job)
        db_bytes = self.cab.path.read_bytes()
        for token in (self.owner['token'], self.a['token'], self.b['token'], self.operator):
            self.assertNotIn(token.encode(), db_bytes)
        self.cab.verify_audit()

    def test_default_broker_rejects_without_opt_in(self):
        other = Cabinet(Path(self.temp.name)/'deny.db', audit_key=self.audit_key,
                        operator_token=self.operator)
        user = other.enroll(self.operator, 'user')
        with self.assertRaises(Denied):
            other.put(user['token'], 'input', synthetic_data())
        other.verify_audit()

    def test_reopen_with_external_keys(self):
        grant = self.grant()
        other = Cabinet(self.cab.path, audit_key=self.audit_key, operator_token=self.operator,
                        broker=self.broker, clock=lambda: self.now)
        job = other.run(self.a['token'], grant)
        self.assertEqual(other.read_result(self.a['token'], job)['quality']['usable_rows'], 12)
        other.verify_audit()


def demo():
    with tempfile.TemporaryDirectory(prefix='cabinet-demo-') as directory:
        operator = secrets.token_urlsafe(32)
        cabinet = Cabinet(Path(directory)/'vault.db', audit_key=secrets.token_bytes(32),
                          operator_token=operator,
                          broker=LocalDemoBroker(secrets.token_bytes(32), allow_insecure_demo=True))
        owner, a, b = [cabinet.enroll(operator, name) for name in ('data-owner', 'trainer', 'analyst')]
        asset = cabinet.put(owner['token'], 'SYNTHETIC-features.jsonl', synthetic_data())
        outputs = {}
        for user, purpose in ((a, 'dataset'), (b, 'stats')):
            grant = cabinet.grant(owner['token'], asset['asset_id'], user['id'], purpose,
                                  expires=time.time()+3600, quota=1)
            job = cabinet.run(user['token'], grant)
            result = cabinet.read_result(user['token'], job)
            outputs[purpose] = {'quality': result['quality'], 'lineage': result['lineage']}
            if purpose == 'dataset':
                outputs[purpose]['split_samples'] = {k: len(v) for k,v in result['splits'].items()}
            cabinet.revoke(owner['token'], grant)
            try:
                cabinet.read_result(user['token'], job)
                raise RuntimeError('撤销校验失败')
            except Denied:
                outputs[purpose]['revoked_access_denied'] = True
        print(json.dumps({'mode': 'INSECURE_LOCAL_DEMO', 'synthetic_data': True,
                          'hardware_root_protection': False, 'outputs': outputs,
                          'audit': cabinet.verify_audit(), 'persistent_secrets_written': False},
                         ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='数据安全柜单文件研究原型；不是 root 安全边界')
    parser.add_argument('command', choices=['design', 'demo', 'selftest'])
    args = parser.parse_args()
    if args.command == 'design':
        print(__doc__)
    elif args.command == 'demo':
        demo()
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestCabinet)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
