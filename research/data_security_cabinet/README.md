# 数据安全柜 v0.3 远程证明与独立密钥服务

## 授权状态保护更新

v0.3 新增数据库完整状态 HMAC、独立 HTTPS 检查点 CAS、服务端时间校验及快照/分叉攻击测试。详细部署、故障恢复与限制见 [授权状态保护说明](AUTHORIZATION_STATE.md)。真实接入脚本现在强制要求检查点配置。

## 当前实现与边界

本目录提供可运行的业务原型、真实 Trustee KBS 客户端适配器、独立 KBS/AS/RVPS 部署生成器及硬件验证流程。**尚未完成真实 SNP/TDX 设备的远程证明、服务端容器运行和宿主 root 攻击验证，不能称为已实现生产级防 root。** 软件测试替身只验证接口及失败关闭行为，不模拟“硬件验证成功”。

四个核心模块保持一致：

| 模块 | 本次实现 | 待实机验收 |
|---|---|---|
| 数据安全柜 | DEK 加密数据、KBS 提供 KEK、版本化信封、读取资源白名单 | 新增完整状态保护和外部版本锚；待实机验证及密钥轮换迁移 |
| 防 root 窃取 | 通过上游客户端请求新鲜证明并取密钥；HTTPS、客户端哈希固定；服务端默认拒绝 | 硬件签名、背书证书链、TCB、镜像及策略度量、攻击验证 |
| 多方交易 | 原有身份、授权、到期、撤销、额度控制；失败事务回滚 | 授权控制面移入可信域；检查点服务独立部署及撤销传播 |
| 模型可用数据 | 原有固定 JSONL 特征加工器，在可信执行器内运行 | 真实数据接入、视频/雷达流水线及 GPU 保护 |

运行核心依赖 Python 3.10+、cryptography；部署配置自测使用 Python 3.11+。源代码本身不存放生产密钥。

```sh
python3 -m venv /tmp/cabinet-venv
/tmp/cabinet-venv/bin/pip install -r requirements.txt
/tmp/cabinet-venv/bin/python security_cabinet.py selftest
/tmp/cabinet-venv/bin/python test_trustee.py
/tmp/cabinet-venv/bin/python security_cabinet.py demo
```

以上命令在本目录执行。`demo` 仍为合成数据本地演示，不调用硬件。

## 系统与信任边界

```text
数据所有者／使用者
       │ 端到端认证连接，可信端加密接入
       ▼
计算宿主机〔宿主 root 属于攻击者〕
  └─ SNP / TDX 机密虚拟机〔客体系统、应用属于可信计算基〕
       ├─ Cabinet：身份与交易授权、额度及版本
       ├─ 固定的数据加工器
       ├─ TrusteeKeyBroker：只在可信内存处理 KEK/DEK
       └─ 已度量的 kbs-client：硬件证据、临时密钥、RCAR
                │ HTTPS + 硬件证据绑定的响应加密
                ▼
独立信任主机〔不同管理员、凭据和故障域〕
  KBS：资源授权、密钥提供
       └─ AS：硬件证据、证书链和安全状态验证
            └─ RVPS：经批准的镜像与平台参考值
  独立管理入口：策略、参考值、资源、密钥版本
       └─ 可选 HSM／外部 KMS、外部防回滚审计
```

KBS 主机上运行 Docker 是服务部署方式，不是隔离其管理员的手段。KBS root 当前仍受信；若也要防它，需额外 HSM/独立审批/可信服务设计。TPM 保护密钥封装和启动度量，**不能单独保护普通进程解密后的内存**。目标是约定威胁模型下抵抗计算宿主 root，不是抵抗所有 root、物理攻击、所有侧信道或拒绝服务。

必须同时解决以下问题：

1. 采集明文只能进入可信采集端或 TEE 内的接入端；宿主代理不能终止明文数据通道。
2. 普通 SQLite 授权表若放在宿主可改写的目录，root 可以修改额度、身份或撤销状态。持久业务状态应放到独立可信控制面的数据库，或经过认证且有外部版本检查点的存储。仅“磁盘加密”不能阻止旧快照回滚。v0.3 已加入独立检查点服务检测授权文件回滚；服务自身必须位于不可随计算节点回滚的信任域，详见授权状态保护说明。
3. 锁定客体镜像、内核、应用、证明客户端、CA 和配置；禁用调试入口、宿主注入 shell、任意代码和明文共享目录。init-data 的指定值只有与实际执行策略和镜像强制校验相结合才有意义：宿主自报一个正确摘要不能证明实际执行了正确程序。
4. 本版 KBS 将执行器级 KEK 释放给批准的可信执行器，主体授权仍由该执行器强制执行。`ReleaseRequest` 不会自动变成 KBS 的用户级交易凭证。要求 KBS 每任务独立裁决时，应增加独立授权服务签发、与证明通道绑定的短期任务票据及服务端校验插件；本版没有声称完成此功能。
5. 资源策略撤销阻止新的 KEK 获取，不能收回已释放的密钥或导出的明文。当前不做 KEK 持久缓存，每次封装/解封均重新调用客户端；执行中的任务仍需配合终止/短租约/分片密钥。

## 远程证明流程

1. 在可信客体内运行固定 SHA-256 的上游 `kbs-client`，验证 KBS 的 HTTPS 身份。
2. 客户端与 KBS 执行 RCAR 挑战协议，在可信环境内部生成临时密钥，将新鲜挑战及公钥绑定到硬件证据。
3. AS 验证证据和厂商信任链，并根据参考值评价度量、安全版本、调试及迁移等状态。
4. KBS 按具体资源、平台、可信状态、批准的测量和 init-data 放行或拒绝。
5. KBS 返回仅持有已证明临时私钥的客户端可解密的资源响应；上游客户端完成响应解密。
6. 适配器严格读取客户端输出的 base64 编码资源，要求解码后恰好 32 字节，不打印输出、不保存临时私钥，不允许导入外来证明 token，不降级到 demo broker。

适配器不手工实现证明签名验证或自制会话协议；这些由固定版本上游负责。不能将单独的 HTTP 200、JWT 文本、设备节点存在或 `tee=true` 当作有效证明。[上游协议](https://github.com/confidential-containers/trustee/blob/main/kbs/docs/kbs_attestation_protocol.md)

## 硬件要求

| 位置 | 要求 | 验证依据 |
|---|---|---|
| SNP 计算节点 | 明确支持 AMD SEV-SNP 的 EPYC 服务器或对应机密云实例；主板 BIOS、SEV 固件、宿主内核、KVM/QEMU 和客体驱动兼容 | OEM 配置和最新安全通告；实取 SNP 报告并经 AS 验证 |
| TDX 计算节点 | 明确支持 Intel TDX 的 Xeon 服务器或对应机密云实例；BIOS、TDX module、VMM、客体及 quote 生成链完整 | OEM/Intel 支持矩阵；实取并验证 TDX quote |
| 独立信任主机 | 单独 Linux 主机或独立管理域，部署 KBS/AS/RVPS；只发布 TLS KBS 入口 | 不与被防护宿主共享 root、管理员 token 或密钥目录 |
| 存储 | 可采用不可信对象存储保存密文；授权、版本与审计状态另行保护 | 明文扫描、改写/回滚和恢复测试 |
| GPU（后续） | 明确支持机密计算的 GPU、受支持的驱动和 CPU/GPU 组合；设备证据与 CPU 证明绑定 | 联合证明和传输保护；普通 GPU 不自动继承 CPU 防护 |

无需同时采购 SNP 和 TDX，先选择一条路线。TPM 2.0 不是替代品；普通电脑和普通 Docker 可做功能联调，不能据此验收防 root。不要仅凭 CPU 品牌或代际采购，必须让供应商演示证明成功及反例拒绝。

本次查询的 CoCo SNP 文档要求 SEV 固件至少 1.55（报告 v3），其对应宿主路线要求上游内核至少 6.16.1；这些是该文档版本的兼容基线，不代替最新漏洞修复与云厂商支持矩阵。[SNP 部署](https://confidentialcontainers.org/docs/getting-started/prerequisites/hardware/snp/)；[TDX 部署](https://confidentialcontainers.org/docs/getting-started/prerequisites/hardware/tdx/)。

## 部署步骤

### 1 固定源码和信任域

已核对的上游提交为 `22ca43188fb2db632d43d3d5a164e0edbd5ab021`。它是本次接口兼容基线，不是安全认证版本。文档页面可能仍使用旧 EAR 点号字段，而固定源码采用 `ear_status`、`ear_attester_claims`。升级必须重新跑策略和证明测试。

在独立信任主机获取干净源码，不要在包含密钥的目录工作：

```sh
git clone https://github.com/confidential-containers/trustee.git /opt/cabinet-trustee-source
git -C /opt/cabinet-trustee-source checkout 22ca43188fb2db632d43d3d5a164e0edbd5ab021
python prepare_trustee.py --source /opt/cabinet-trustee-source --output /srv/cabinet-trustee --host kbs.example.internal
```

生成目录必须不存在；脚本不会覆盖现有密钥，不会启动服务。生成使用独立 CA 的 TLS 与 AS 证书、短期管理员 JWT、四份相互独立的 32 字节资源和 Compose 配置。示范 CA 有效期一年、叶证书 30 天，生产接入组织 PKI 并制定续期。生成的 CA 私钥和管理员签名私钥仅留在 `admin/`，不挂载至 KBS/AS 容器。

部署副本将上游默认资源策略改成 deny-all，避免默认“只要不是 sample 就放行”造成未审批密钥释放。AS/RVPS 没有宿主端口映射；KBS 初始仅绑定 `127.0.0.1:8443`。不要直接使用上游默认不加密且公开多个端口的示例启动生产服务。

### 2 构建并启动独立服务

```sh
cd /srv/cabinet-trustee
docker compose -f compose.json config --quiet
docker compose -f compose.json build
docker compose -f compose.json up -d
docker compose -f compose.json ps
```

需要 Linux x86_64 Docker 构建环境及访问依赖仓库。按组织网络设置，只将 KBS TLS 入口绑定到选定私有地址并配置防火墙，管理端点限定管理网段；AS/RVPS 内部网络不得加入不可信容器。AS 需要访问厂商背书/吊销资料或经过维护的本地缓存。没有配置可用背书服务时应拒绝放行。

本机尚未执行镜像构建与服务启动。生成器配置依据固定源码核对，实际平台依赖安装、健康检查、证书链和硬件策略须在部署节点验证。数据卷、备份、管理员 token 和访问日志纳入独立信任域；本版 LocalFs 密钥后端不等于 HSM。生产可更换 KMS/HSM 后端。

### 3 构建真实证明客户端

在对应 Linux 构建环境、固定源码目录执行以下一个命令；不要使用 sample-only 构建：

```sh
cargo build --locked --release -p kbs-client --features snp-attester
# TDX 路线改用：cargo build --locked --release -p kbs-client --features tdx-attester
sha256sum target/release/kbs-client
```

安装上游所需平台依赖，将客户端、CA 和应用连同配置放入可度量的客体镜像。记录 SHA-256，固定启动链和工作负载策略，禁用任意 exec/调试和宿主共享明文路径。裸机密虚拟机与 CoCo/Kata 的参考值和策略不同，不可未经核对直接混用默认 AS 策略。[客户端说明](https://confidentialcontainers.org/docs/attestation/client-tool/)

### 4 配置参考值和资源策略

从受控构建/OEM 平台建立可信参考值，不能简单把一个未知来宾的报告全部加入白名单。SNP 至少审核 launch measurement、TCB 和调试配置；TDX 审核 MRTD、启动/运行度量及安全状态；对应固定源码的 AS policy 列出了所需参考值键。

通过上游 `config set-reference-value --type <已配置提取器> --provenance-file <可信参考值来源文件>` 注册，再用固定版本证明 token 核对实际字段。选择 SNP/TDX 与客体运行方式对应的 AS 策略。若缺少参考值，保持拒绝；不要改成 `allow-all` 或仅接受平台类型。

用独立批准的测量值及绑定执行策略的 init-data 生成策略：

```sh
python render_resource_policy.py --tee snp --namespace cabinet \
  --measurement "$APPROVED_MEASUREMENT" --init-data "$APPROVED_INIT_DATA" \
  --output /srv/cabinet-trustee/admin/resource-policy.rego
```

两个环境变量必须由受控构建核验后填写，不是秘密，但不能从任意来宾自报值自动信任。SNP init-data 必须是 64 位非零小写 HOST_DATA hex；TDX 必须是 96 位非零小写 MRCONFIGID hex。本版不支持 MRCONFIGID 全零而仅通过 RTMR 事件绑定的 TDX 路线，需针对该路线扩展并验证策略。字段编码须与固定源码匹配；策略默认拒绝，字段缺失不能退化为放行。本次已在 OPA 1.0.0 执行两类平台共 22 个正反例；尚需在上游 Regorus 和真实已验证 token 上复测后安装。 示例策略只支持 CPU 单设备和五份指定资源；GPU 联合证明必须另写策略。

管理员初始化命令（本目录工具复制到信任主机执行；路径按实际替换）：

```sh
python renew_admin_token.py /srv/cabinet-trustee
kbs-client --url https://kbs.example.internal:8443 --cert-file /srv/cabinet-trustee/admin/ca.pem \
  config --admin-token-file /srv/cabinet-trustee/admin/admin-token \
  set-resource-policy --deny-all >/dev/null
```

在 deny-all 状态先上传 `admin/kek-v1`、`admin/audit-v1`、`admin/operator-v1`，资源路径分别是 `cabinet/keys/kek-v1`、`cabinet/keys/audit-v1`、`cabinet/keys/operator-v1`：

```sh
kbs-client --url https://kbs.example.internal:8443 --cert-file /srv/cabinet-trustee/admin/ca.pem \
  config --admin-token-file /srv/cabinet-trustee/admin/admin-token \
  set-resource --path cabinet/keys/kek-v1 --resource-file /srv/cabinet-trustee/admin/kek-v1 >/dev/null
```

另外两份资源用相同命令替换路径和文件。**该上游版本 set-resource 会将秘密的 base64 输出到 stdout，务必保持重定向，不打开 shell 跟踪，不将输出送入日志。** 完成参考值、策略测试后再安装批准的资源策略：

```sh
kbs-client --url https://kbs.example.internal:8443 --cert-file /srv/cabinet-trustee/admin/ca.pem \
  config --admin-token-file /srv/cabinet-trustee/admin/admin-token \
  set-resource-policy --policy-file /srv/cabinet-trustee/admin/resource-policy.rego >/dev/null
```

管理员凭据永远不复制到计算宿主或工作负载。生成时 token 一小时有效；续签工具生成十分钟 token。需要长期管理时接组织身份系统，不使用永久 token。

### 5 在可信客体内执行真实接入测试

客体配置文件（不包含秘密）：

```json
{
  "client": "/usr/local/bin/kbs-client",
  "client_sha256": "替换为已审核客户端的64位SHA256",
  "url": "https://kbs.example.internal:8443",
  "ca_file": "/etc/cabinet/ca.pem",
  "namespace": "cabinet",
  "database": "/var/lib/cabinet/vault.db",
  "checkpoint_url": "https://checkpoint.example.internal:9443",
  "checkpoint_ca_file": "/etc/cabinet/checkpoint-ca.pem"
}
```

数据库目录应由可信客体管理，配置与 CA 属于被度量镜像；按 AUTHORIZATION_STATE.md 首次初始化独立检查点，并上传 state-v1/checkpoint-v1 两份新资源。随后执行：

```sh
python attested_smoke.py --config /etc/cabinet/worker.json
```

工具会从 KBS 获取审计及控制面秘密，用远程 broker 完成合成数据入库、授权、加工和结果读取，仅输出样本数和审计计数。成功代表在当前服务策略下完成往返，不代表所有 root 攻击已通过。失败不回退本地密钥。

## 验收与故障测试

| 用例 | 操作条件 | 预期与证据 |
|---|---|---|
| 正向证明 | 获批准的真实 SNP/TDX 客体 | RCAR 成功、资源获取、合成任务完成；保存版本与脱敏报告 |
| 非可信环境 | 普通 VM、普通容器或 sample attester | 服务拒绝，业务无结果、额度不增加 |
| 证据伪造/重放 | 修改硬件签名、旧 nonce、交换公钥/会话 | AS/KBS 拒绝；不得仅依赖客户端返回码判定 |
| 运行环境变化 | 修改镜像、init-data、允许的启动参数或打开调试 | 参考值/策略拒绝 |
| 证书与 TCB | 不可信或过期背书、安全版本不合规 | 拒绝或明确安全失败；不得忽略核验错误 |
| 密钥服务异常 | 停 KBS、证书错误、网络超时 | 失败关闭，无本地备用密钥或结果 |
| 资源越权 | 换命名空间、KEK 版本或请求管理员 API | 客户端白名单和服务端策略分别拒绝 |
| 主体越权 | B 使用 A 授权/任务、过期或撤销后调用 | 业务拒绝、留审计记录 |
| 宿主 root | 宿主读取磁盘、共享目录、日志和宿主侧进程内存 | 无真实明文/密钥；测试使用专用 canary，不泄露真实数据 |
| 元数据与快照 | 改授权、回滚撤销前数据库/客体快照 | 必须检测/拒绝；未保护 SQLite 不能满足该条；v0.3 必须启用 StateGuard 和独立检查点 |
| 输出路径 | 产物、异常栈、日志、转储、备份与 swap 检查 | 不通过未经授权路径泄露敏感明文 |

每项记录硬件、BIOS、固件、宿主/客体内核、VMM、镜像摘要、证明客户端摘要、Trustee 提交与镜像 digest、参考值、策略版本、用例条件及结果。负例前后各跑正例，以区分“策略正确拒绝”和“服务始终不可用”。TCB、证书和镜像更新必须重测。宿主内存扫描未发现 canary 仅是测试证据，不是密码学安全证明。

性能分别统计冷启动证明、资源获取、稳态任务时延及 P95；不设未经测试的 1% 承诺。至少三次全流程重复，P95 使用足量调用样本，而非仅三个数据点。

## 已检查与未检查

- 本地 13 项原型自测、8 项远程适配器契约测试、4 项部署配置测试、2 项 OPA 策略测试及 15 项真实本机 HTTPS 状态保护测试全部通过（总计 42 项；策略测试包含 22 个正反子例）。证明客户端测试使用明确的替身，策略输入为合成声明，不是硬件证据。
- 固定源码接口检查、部署生成器、证书/短期 JWT 和 Compose 配置验证见本次交付说明。
- 未运行真实 KBS 容器、未进行硬件证明/重放攻击/宿主 root 验收；需提供相应 Linux/TEE 部署环境。
- 未完成生产级多租户认证服务、支付结算和高可用恢复；v0.3 已增加原型级持久状态校验和独立检查点，仍需硬件验收。
- 旧本地 broker 信封不兼容新远程信封；迁移必须在可信环境中显式解密后重新入柜，禁止静默回退。

参考：[Trustee 架构](https://confidentialcontainers.org/docs/attestation/architecture/)、[资源与证明策略](https://confidentialcontainers.org/docs/attestation/policies/)、[固定版本客户端源码](https://github.com/confidential-containers/trustee/blob/22ca43188fb2db632d43d3d5a164e0edbd5ab021/tools/kbs-client/src/main.rs)。

### 复现新增验证

```sh
CABINET_TRUSTEE_SOURCE=/opt/cabinet-trustee-source python test_deployment.py
CABINET_OPA_BIN=/absolute/path/to/opa python test_policy.py
```

部署测试会在临时目录生成后清理测试密钥，不启动 Docker 服务；Compose 检查不需要 daemon。OPA 工具本次采用官方 v1.0.0 darwin/arm64 静态发行版并核对官方 SHA-256。
