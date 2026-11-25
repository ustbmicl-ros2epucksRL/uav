# e-puck 少锚点 UWB/IMU 协同定位：因子图＋共识 ADMM 设计方案

记录日期：2026-11-24  
代码位置：`uav/research/epuck_consensus/`  
问题边界：**平面轮式机器人（e-puck 类）少锚点（1–2 锚点）UWB/IMU 分布式协同定位**。本方案聚焦纯解析**因子图＋边可分流形共识 ADMM＋偏差解耦消息调度**，不包含 FGNN 或任何神经网络模块。

---

## 1. 场景设定与核心科学问题

### 1.1 为什么“e-puck 少锚点 UWB/IMU 协同”天然需要这一框架？
室内桌面或地面实验场（如 $6\text{ m}\times 6\text{ m}$ 区域）部署 $R$ 台 e-puck 轮式机器人（配备轮式编码器、2D MEMS 陀螺仪/加速度计与单/双天线 UWB 标签），但仅布置 **1 至 2 个固定 UWB 锚点**：

1. **单机瞬时不可观**：单锚点下测距仅约束圆周距离，双锚点存在对称翻转歧义；必须结合跨时刻里程计/IMU 预积分与**跨机器人相对测距（Peer-to-Peer UWB）**才能锁定 $SE(2)$ 位姿。
2. **少锚点下的位置—同源偏差强混淆**：锚点坐标标定误差、公共时钟频偏或走廊遮挡形成**跨机器人共享偏差 $b_s$**。多台机器人在相近扇区测量 $z_{i,a} = \|p_i - p_a\|_2 + b_a + \epsilon_i$ 时，沿视距方向的集体平移 $\Delta p$ 与锚点偏差相反变化 $-\Delta b_a$ 给出几乎相同的测距预测。
3. **传统 ADMM（如 MESA / riMESA）的失效模式**：
   - 若不显式估计共享偏差 $b_s$，带偏锚点通过跨机相对测距把整支机器人编队集体拉偏；
   - 若显式估计 $(x, b)$ 但使用标准各向同性惩罚 $\frac{\rho}{2}\|\theta_i - z_{ij}\|^2$，两台机器人的对偶残差与共识残差会迅速收敛到 $0$，**误把 ADMM 协调弹簧 $\rho I$ 当作物理可观信息**，在弱方向产生严重过度自信（95% 置信椭圆覆盖率崩溃）。
4. **微小型机器人通信受限**：e-puck 板载无线链路有效载荷极小（每步几十至几百字节）。有限字节预算下，必须识别并优先交换**真正能打破“位置—共享偏差混淆”**的观测摘要，同时抵御“偏差来源标错（漏标突发 NLOS）”的污染。

---

## 2. 状态定义与物理因子图模型

### 2.1 变量空间（流形 $\mathcal{M} = (\mathbb{R}^2 \times SO(2))^n \times \mathbb{R}^q$）
每台机器人 $i \in \{1,\dots,R\}$ 维护本地变量集：
- **本体位姿序列（私有为主，交互时刻按需共享）**：$\xi_{i,t} = (p_{i,t}, \theta_{i,t}) \in \mathbb{R}^2 \times SO(2)$，切空间增量 $\delta\xi = (\delta x, \delta y, \delta\theta) \in \mathbb{R}^3$，更新映射：
  $$\xi \oplus \delta\xi = \big(p + [\delta x, \delta y]^\top,\ \operatorname{wrap}(\theta + \delta\theta)\big)$$
  由于 $SO(2)$ 交换群性质，对数映射导数在 $(-\pi, \pi)$ 主值区内恒为单位阵，无三维 $SO(3)$ 曲率畸变。
- **本体陀螺零偏（完全私有，不入通信网）**：$\omega_{\text{bias}, i} \in \mathbb{R}$。
- **物理共享偏差副本（跨机共识变量）**：$b_{i, s} \in \mathbb{R}$（$s \in \mathcal{S}_i$ 为锚点硬件/位置偏差或共享遮挡扇区偏差源）。

### 2.2 四类物理因子（均保留 `sources: frozenset` 溯源集合）
1. **轮速＋2D IMU 预积分因子（本体私有因子）**  
   高频采样 $(dt_k, v_{m,k}, \omega_{m,k})$ 在参考零偏 $\bar{\omega}_{b,i}$ 处预积分为机身坐标系位移 $\Delta \tilde{p}$、转角 $\Delta \tilde{\theta}$、一阶零偏雅可比 $J_{\omega_b} \in \mathbb{R}^{3\times 1}$ 与协方差 $P_{\text{odom}} \in \mathbb{R}^{3\times 3}$：
   $$r_{\text{odom}}(\xi_{i,t}, \xi_{i,t+1}, \omega_{b,i}) = W_{\text{odom}} \begin{bmatrix} R(\theta_{i,t})^\top (p_{i,t+1} - p_{i,t}) - \big(\Delta \tilde{p} + J_{p,\omega_b}(\omega_{b,i}-\bar{\omega}_{b,i})\big) \\ \operatorname{wrap}\big(\theta_{i,t+1} - \theta_{i,t} - \Delta\tilde{\theta} - J_{\theta,\omega_b}(\omega_{b,i}-\bar{\omega}_{b,i})\big) \end{bmatrix}$$
2. **少锚点 UWB 测距因子（带杆臂 $\ell_i$ 与共享偏差 $b_s$）**  
   $$r_{\text{anc}}(\xi_{i,t}, b_s) = \frac{\sqrt{w_{\text{rob}}(e)}}{\sigma_r}\Big(\|p_{i,t} + R(\theta_{i,t})\ell_i - p_a\|_2 + \gamma_{i,a,t} b_s - z_{i,a,t}\Big)$$
3. **机器人间相对 UWB 测距因子（跨节点耦合因子）**  
   $$r_{\text{p2p}}(\xi_{i,t}, \xi_{j,t}, b_{s_{\text{p2p}}}) = \frac{\sqrt{w_{\text{rob}}(e)}}{\sigma_{\text{p2p}}}\Big(\|(p_{i,t} + R(\theta_{i,t})\ell_i) - (p_{j,t} + R(\theta_{j,t})\ell_j)\|_2 + \gamma_{\text{p2p}} b_{s_{\text{p2p}}} - z_{ij,t}\Big)$$
4. **固定参考流形历史消元先验（`FixedSE2Prior`）**  
   当机器人本地滑窗超出容量 $W$ 时，对退出窗口的**纯私有变量**执行列均衡 SVD 正交补消元（继承 V79/V82 机制），保留边界位姿、陀螺零偏与共享偏差之间的完整交叉信息矩阵，**不对秩亏方向加对角伪正则**。

---

## 3. 边可分流形共识 ADMM 架构（超越标准 MESA 的三项核心设计）

### 3.1 边分裂与偏置先验（Biased Prior）嵌入
对通信图 $\mathcal{G}=(\mathcal{V}, \mathcal{E})$ 每条活跃边 $(i,j)\in\mathcal{E}$ 上的公共变量键 $k \in \mathcal{K}_{ij}$（包含共享偏差 $b_s$ 及发生跨机测距的交互时刻位姿 $\xi_{j,t}$），引入边分裂目标 $z_{ij,k} = z_{ji,k}$ 与对偶乘子 $\lambda_{ij,k}$。  
通过配方法，节点 $i$ 的增广拉格朗日耦合项精确化为外挂在本地因子图上的一元高斯先验：
$$r_{\text{admm},(i,j),k}(\theta_{i,k}) = \sqrt{\rho_{ij}}\Big((\theta_{i,k} \ominus z_{ij,k}) + \frac{\lambda_{ij,k}}{\rho_{ij}}\Big)$$
- **流形中点步（Edge Z-Update）**：
  - 欧氏分量（位置 $p$、偏差 $b$）：$z_{ij} = \frac{1}{2}\big(\theta_i + \frac{\lambda_{ij}}{\rho_{ij}} + \theta_j + \frac{\lambda_{ji}}{\rho_{ij}}\big)$（对偶反对称初值下退化为算术平均 $\frac{1}{2}(\theta_i + \theta_j)$）；
  - 角度分量 $\theta \in SO(2)$：$z_{ij,\theta} = \operatorname{wrap}\big(\theta_i + \frac{1}{2}\operatorname{wrap}((\theta_j + \lambda_{ji,\theta}/\rho_{ij}) - (\theta_i + \lambda_{ij,\theta}/\rho_{ij})) + \frac{\lambda_{ij,\theta}}{2\rho_{ij}}\big)$。
- **对偶更新步（Dual Update）**：$\lambda_{ij,k}^{+} = \lambda_{ij,k} + \rho_{ij}(\theta_{i,k}^{+} \ominus z_{ij,k}^{+})$。

### 3.2 关键防错机制一：临时对偶层与永久物理消元严格隔离
所有 `r_admm` 标记为 `ephemeral=True`（不含物理 `sources`）。本地滑窗淘汰旧状态时，`marginalize()` **仅收集永久物理因子**生成 `FixedSE2Prior`；外挂 ADMM 偏置先验每轮通信原地刷新，彻底杜绝旧惩罚系数 $\rho$ 与旧乘子 $\lambda$ 被吞入历史先验导致重计与发散。

### 3.3 关键防错机制二：物理信息矩阵 $H_{\text{phys}}$ 与优化惩罚 $\rho$ 严格分账
- **原始步求解**：允许使用 $H_{\text{phys}, i} + H_{\text{admm}, i} + \lambda_{\text{LM}} I$ 保证局部子问题良态可解；
- **不确定性与可辨识审计**：计算消除偏差后的位置边缘信息 $S_x = H_{xx} - H_{xb} H_{bb}^{\dagger} H_{bx}$、弱可观奇异值谱及 95% 置信椭圆时，**严禁纳入 $H_{\text{admm}}$ 与 $\lambda_{\text{LM}} I$**。弱方向在缺少独立物理观测时保持真实大方差（或通过保守包络封顶），绝不因 ADMM 达成共识而虚报精度。

---

## 4. 通信受限下的“混淆信标＋残差认证”消息选择（上层方法创新）

在给定单轮总字节预算 $B_{\max}$（**显式计入信标发现字节＋因子包头部与载荷字节**）下，两阶段协议如下：

1. **反向弱方向混淆信标（Confounding-Beacon，每节点约 16–24 字节）**  
   节点 $i$ 对本地纯物理联合信息矩阵消去私有状态与共享偏差，提取位置边缘信息 $S_{x,i}$ 的最弱单位特征向量 $u_{w,i} \in \mathbb{R}^{d_x}$（`float16` 量化）及其对应的共享偏差混淆载荷 $c_{w,i} = H_{bb,i}^{\dagger} H_{bx,i} u_{w,i} \in \mathbb{R}^{d_b}$。  
   信标 $(u_{w,i}, c_{w,i})$ 宣告：*“节点 $i$ 在方向 $u_{w,i}$ 存在定位模糊，且与偏差组合 $c_{w,i}$ 耦合”*。
2. **发送端闭式协同解耦评分 ＋ GLRT 标错门控**  
   邻居 $j$ 对尚未被节点 $i$ 融合（由 `source_id` 位图判定幂等防重）的候选观测/局部因子摘要 $m = (h_x, h_b, r_m, \sigma_m^2)$ 计算：
   - **跨节点解耦投影强度**：$\gamma_{j\to i}(m) = \frac{(h_x^\top u_{w,i} - h_b^\top c_{w,i})^2}{\sigma_m^2 + \nu_{\text{local}, j}}$；同比例同源观测分子恒为 $0$，独立观测或异构几何观测显著大于 $0$。
   - **偏差标错残差兼容性门控（GLRT / Huber 有效信息折减）**：利用当前可辨识投影残差检验该观测是否携带“未声明的突发独立偏差 $b_{\text{new}}$”，定义折减权重 $\omega_{\text{cert}}(r_m) = \big(1 + (|r_m|/\tau_{\text{cert}})^4\big)^{-1}$。若某条严重 NLOS 被错误标为“无偏差独立观测”，虽 $\gamma_{j\to i}$ 很高，但残差兼容性将其有效增益折减近零，防止选入毒药观测。
   - **单位字节边际方差收益**：
     $$\text{Score}_{j\to i}(m) = \frac{\sigma_{w,i}^4 \cdot \omega_{\text{cert}}(r_m)\gamma_{j\to i}(m)}{\big(1 + \sigma_{w,i}^2 \omega_{\text{cert}}(r_m)\gamma_{j\to i}(m)\big)\cdot \text{Bytes}(m)}$$
3. **幂等因子传输与按需共识更新**  
   节点按得分排序发送预算内最高收益的压缩因子包（含 `source_id` 防环路重计），随后在活跃共享变量集上执行共识 ADMM 更新。

---

## 5. 代码模块划分与验证矩阵

| 模块文件 | 职责 |
|---|---|
| `uav/research/epuck_consensus/se2_math.py` | $\mathbb{R}^2\times SO(2)$ 流形运算、轮速/2D IMU 预积分及解析雅可比、少锚点/跨机 UWB 测距因子、`FixedSE2Prior` |
| `uav/research/epuck_consensus/linear_consensus.py` | 带 `sources` 溯源的列均衡 SVD 因子图、可辨识子空间投影、线性边可分共识 ADMM（同步/异步、物理信息与惩罚分账） |
| `uav/research/epuck_consensus/nonlinear_admm.py` | 多 e-puck 非线性流形滑窗因子图＋边可分 ADMM（`ephemeral` 偏置先验隔离、Huber 鲁棒核） |
| `uav/research/epuck_consensus/message_selection.py` | 严格字节计费契约与 6 种消息选择策略（`random`、`max_admm_residual`、`bias_blind_info`、`local_schur`、`beacon_cert`、`centralized_oracle`） |
| `uav/research/epuck_consensus/benchmark.py` | e-puck 少锚点 UWB/IMU 仿真器、多机 ADMM 收敛对比实验、四工况（全同源 / 稀疏独立 / 跨节点互补解耦 / 偏差来源标错）去留验证 |
| `uav/research/epuck_consensus/tests/test_epuck_consensus.py` | 解析雅可比有限差分核验、SVD 消元无损核验、ADMM 与集中式批量解等价核验、秩亏不确定性诚实度与环路防重计测试 |
