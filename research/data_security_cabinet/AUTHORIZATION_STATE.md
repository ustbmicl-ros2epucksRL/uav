# 授权数据库防篡改与防回滚 v0.3

## 已实现的保护

`StateGuard` 对数据库业务表和 schema 建立带密钥的完整状态摘要，并在独立 HTTPS 检查点服务保存最新版本和摘要。身份、资产所有者、授权主体、任务类型、期限、额度、已用次数、撤销状态、任务结果、审计记录及触发器定义均被覆盖。状态密钥与数据库分离，只释放给被批准的可信执行器。

每个受保护事务的执行顺序：

1. 将持久数据库复制到可信执行器的内存 SQLite，再在内存副本开启事务。root 可以绕过文件锁，因此不能先校验磁盘页、再继续从磁盘读取权限。
2. 从独立检查点服务读取最新 `(version, digest, time)`，验证当前数据库的 HMAC 摘要；任何不一致立即拒绝，不解密数据、不写入新的合法摘要。
3. 在验证后的快照中做身份认证、授权和额度检查，并执行固定任务。期限判断使用检查点服务时间，而不是计算宿主的墙钟。
4. 计算事务执行后的摘要，调用远端 CAS：只有版本及旧摘要均匹配，才推进到新版本。
5. 远端推进成功后提交内存事务，并将完整内存副本持久化到本地，随后才返回成功结果。即使 root 在校验后修改磁盘，其内容也不会进入本次权限检查或被重新签署；后续篡改会在下次检查被发现。

读取结果也经过同一流程，不能绕过检查点；纯读事务仍推进远端版本，防止两个分叉同时以相同旧版本提交。摘要覆盖 schema，可检测注入触发器、改表、删行及审计尾部删除。全量摘要用于研究原型，每次事务成本随数据库大小增长；尚非大规模 Merkle 存储方案。

## 为什么只加 HMAC 不够

旧数据库及其旧 HMAC 可以同时被恢复。独立服务中的最新摘要/版本不随计算宿主回滚，才使旧快照失效。网络响应通过 TLS 验证服务身份，每次请求具有随机 nonce，并校验响应 nonce；不缓存检查点，不接收 HTTP、不跟随重定向，不继承代理环境。远端 CAS 在 SQLite 事务内执行并持久化，重放已消费版本会被拒绝。

本地不保存可以任意选择的“当前版本”；数据库必须匹配独立服务的当前状态。已批准的同一逻辑状态可以通过读事务推进多个版本，这不造成权限回退。

## 故障语义与恢复

本地 SQLite 与远端检查点不是一个原子数据库事务。本版采用安全优先策略：**先推进远端，再提交本地**。

- 远端未提交：本地事务回滚，可以在网络恢复后重新调用。
- 远端已提交，但响应丢失或本地提交失败：本地状态与远端不一致，后续请求停止；不自动重试签署旧状态，也不把它当成首次初始化。
- 远端不可用、证书错误、认证失败：拒绝执行，不降级到本地 HMAC 或演示模式。
- 不自动合并分叉，不提供 reset、force、忽略版本等在线接口。

恢复时应停止工作负载，由独立运维从可信备份中寻找**摘要与当前权威检查点一致**的数据库；以相同 namespace 和状态密钥计算摘要，在恢复验证通过前不得提供业务服务。若不存在匹配备份，本版没有透明恢复方案：必须保留旧 namespace 的不可用状态，经过数据所有者与独立管理员审计，在新 namespace 中重建资产和重新授予权限；不得复制旧授权并宣称保持撤销语义。生产部署可增加可信事务日志或真正的共识控制面以改善可用性。

将授权业务直接放在独立可信数据库中也是可选架构。本实现用于授权数据库仍位于受保护执行器、持久文件可能被计算宿主读取/替换的场景。

## 信任边界

| 对象 | 要求 |
|---|---|
| 计算宿主 root | 可改写/替换本地数据库文件，但不可读取或修改 TEE 内状态密钥、检查点凭据和程序 |
| 执行器 | 运行在前述 SNP/TDX 环境；镜像、配置、namespace、检查点 URL/CA 均纳入可信部署 |
| 独立检查点服务 | 单独管理域；其 root、时钟、数据库和备份属于可信范围 |
| 状态密钥 | 与审计密钥、数据 KEK 分离，经 Trustee 发放；不得写入计算宿主配置文件 |
| 检查点凭据 | 每 namespace 一份，隔离于数据使用者；只能交给可信执行器 |

若攻击者连独立服务及其历史也能一并回滚，本方案不能检测；因此不能把两个数据库放在同一台宿主的可回滚 VM/磁盘快照中。单节点权威服务还需独立备份、故障监控及组织级恢复约束，生产可替换成强一致/防回滚的管理域服务。仅给权威数据库加本地 HMAC 不能解决其自身整体回滚。

本地软件测试验证文件篡改和磁盘快照恢复，不等于任意运行中虚拟机内存回滚的安全证明。必须禁止未经授权的可信客体快照/迁移和调试，或验证平台支持的防重放迁移机制。已释放的密钥和已导出的结果不能远程收回。事务期限按服务端校验时刻判断；它不是对无限执行时间的租约控制。每次后续读取会再次检查期限及撤销。

## 部署步骤

### 1 独立主机初始化检查点服务

以下命令在受信任 Linux 主机执行，TLS 证书必须包含实际服务 DNS/IP 的 SAN。可使用组织 PKI；不要复用计算宿主 root 能读取的私钥。服务只监听管理/私有网络，并限制可访问来源。

```sh
umask 077
mkdir -p /srv/cabinet-checkpoint
python checkpoint_service.py provision \
  --db /srv/cabinet-checkpoint/authority.db --namespace cabinet \
  --token-file /srv/cabinet-checkpoint/checkpoint-v1
python checkpoint_service.py serve \
  --db /srv/cabinet-checkpoint/authority.db \
  --host 127.0.0.1 --port 9443 \
  --cert /etc/cabinet-checkpoint/tls.pem --key /etc/cabinet-checkpoint/tls.key
```

首次 provision 拒绝覆盖已有数据库/凭据，生成 32 字节原始凭据文件，不打印秘密。正式跨主机使用时将监听地址调整为被防火墙保护的私有地址，TLS 必须端到端有效。服务进程应由独立的专用 OS 用户通过 systemd 等方式管理，禁止非受信用户写数据目录。HTTP 原型未实现高可用、速率限制或资源耗尽防护，不能直接公开到互联网。

检查点服务没有远程创建 namespace 的接口。丢失权威库时服务拒绝正常请求，运维不得自动 provision 同名新库。

### 2 加入 Trustee 资源

v0.3 部署生成器新增 `admin/state-v1`，是独立随机 32 字节状态密钥。检查点 `checkpoint-v1` 凭据由上一步生成，不由计算宿主生成。对已有 Trustee 服务，在独立管理域生成并保管新状态密钥，不能重复运行生成器覆盖现有部署。

用原 README 的 `set-resource` 方式上传：

- `cabinet/keys/state-v1` ← 独立状态密钥文件。
- `cabinet/keys/checkpoint-v1` ← `/srv/cabinet-checkpoint/checkpoint-v1`。

仍须将 `set-resource` 的 stdout 重定向到 `/dev/null`，避免上游 CLI 输出秘密。更新资源策略为本版本生成的策略，除原来三份密钥外只增加以上两份。不要赋予数据购买方调用检查点服务的凭据。

### 3 可信客体配置与首次运行

配置增加如下字段，并将它们随镜像/执行策略固定：

```json
{
  "namespace": "cabinet",
  "database": "/var/lib/cabinet/vault.db",
  "checkpoint_url": "https://checkpoint.example.internal:9443",
  "checkpoint_ca_file": "/etc/cabinet/checkpoint-ca.pem"
}
```

保留原有 `client`、`client_sha256`、`url`、`ca_file` 字段。`temp_root` 不再使用：检查点必须与持久数据库配对，不能每次删除数据库重新初始化。

```sh
# 仅第一次；远端必须是全新 version=0，本地数据库路径必须尚不存在。
python attested_smoke.py --config /etc/cabinet/worker.json --initialize-state
# 后续运行，禁止再次初始化。
python attested_smoke.py --config /etc/cabinet/worker.json
```

工具使用 KBS 获取状态密钥和检查点凭据，经 HTTPS 服务保护所有业务事务。凭据从原始 32 字节转换为 hex bearer token，与检查点服务离线登记方式一致。该演示会写入合成资产和身份，使用专门测试 namespace，不要在生产数据空间运行。

程序调用示例：

```python
checkpoint = RemoteCheckpoint(checkpoint_url, namespace, checkpoint_token.hex(), ca_file)
guard = StateGuard(namespace, state_key, checkpoint)
cabinet = Cabinet(db_path, audit_key=audit_key, operator_token=operator_token,
                  broker=trustee_broker, state_guard=guard, initialize_state=False)
```

不带 `state_guard` 的 `Cabinet` 仅保留供旧测试和本地 demo 使用，不具备授权状态防回滚。真实接入入口 `attested_smoke.py` 已强制要求检查点配置，不会回退该模式。旧的未保护数据库不能直接初始化为可信状态；必须停机核验、重新建立 namespace 和授权，不自动迁移。

## 测试及验收

```sh
python test_state_guard.py
python security_cabinet.py selftest
python test_trustee.py
CABINET_TRUSTEE_SOURCE=/path/to/pinned/trustee python test_deployment.py
CABINET_OPA_BIN=/path/to/opa python test_policy.py
```

`test_state_guard.py` 在 127.0.0.1 临时端口运行真实 TLS 服务，临时证书、检查点数据库、数据和密钥结束后自动清理。覆盖：

- 修改主体、所有者、用途、额度、已用次数、到期时间、撤销标记。
- 删表、删审计、注入触发器；在解密前拒绝损坏状态。
- 校验后、权限判断时并发改写磁盘：仅使用已认证内存快照，不签署攻击者注入的授权。
- 恢复撤销前、额度消费前快照，进程重启后仍拒绝。
- 两份数据库分叉同时执行，最多一份推进检查点。
- 重放旧 CAS、错误凭据、错误状态密钥、服务不可用。
- 服务端已提交但响应丢失：本地回滚后锁定，不重新签署旧状态。
- 回拨计算宿主时钟不延长授权；合法任务、结果读取和重启正常。

实机验收还需把这些文件攻击用例放到真实 TEE 计算宿主上执行，并检查 namespace/CA/凭据不能被宿主替换。验收日志不得记录秘密。权威服务自身丢盘/回滚演练应由独立运维执行并验证“拒绝恢复旧检查点”流程，而不是声称本原型能独立检测权威域整体回滚。

本次验证结果：15 项状态保护测试通过；原有 27 项测试回归通过，共 42 项。状态服务测试使用真实本机 HTTPS 连接，原有证明客户端仍为契约测试替身；不将这些结果表述为实机 TEE/root 防护认证。
