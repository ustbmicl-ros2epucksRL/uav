# e-puck 少锚点 UWB/IMU 协同定位：因子图＋共识 ADMM 原型

本目录实现面向 e-puck 类平面轮式机器人的**少锚点（1–2 锚点）UWB/IMU 分布式因子图＋边可分流形共识 ADMM＋同源偏差解耦消息调度**原型。本模块为纯解析统计估计与分布式优化实现，不含 FGNN 或任何神经网络组件。

完整数学建模与架构说明见 [DESIGN.md](DESIGN.md)。

## 目录结构

- `se2_math.py`：$\mathbb{R}^2 \times SO(2)$ 流形运算、轮速＋2D 陀螺仪预积分（解析右雅可比与零偏一阶修正）、带杆臂少锚点/跨机点对点 UWB 测距因子、固定参考流形先验 `FixedSE2Prior`
- `linear_consensus.py`：带 `sources` 溯源去重的列均衡 SVD 因子图、可辨识子空间投影、边可分共识 ADMM（严格分离临时协调惩罚与物理 Fisher 信息）
- `nonlinear_admm.py`：多机器人 $\mathbb{R}^2 \times SO(2) \times \mathbb{R}^q$ 非线性因子图＋边可分流形 ADMM（支持私有位姿滑窗 SVD 消元且拒绝吞入临时 ADMM 对偶先验）
- `message_selection.py`：通信字节计费契约、反向“弱方向—偏差混淆信标”（Confounding-Beacon）及 GLRT 残差认证门控（含 7 种对比策略）
- `benchmark.py`：4 台 e-puck 双共线带偏锚点非线性轨迹协同仿真 ＋ 4 工况去留验证基准
- `tests/test_epuck_consensus.py`：解析雅可比有限差分、同步/异步 ADMM 集中式等价、秩亏不确定性诚实度、滑窗消元隔离与基准冒烟测试

## 运行测试与基准复现

在仓库根目录 `/Users/duan/Developer/uav` 下执行：

```sh
source .venv/bin/activate
python -m pytest research/epuck_consensus/tests/test_epuck_consensus.py -v
python -m research.epuck_consensus.benchmark ../2026年度-感知网络规划/outputs/epuck-consensus-admm-20261124
```

> 说明：当前实验基于参数化物理仿真生成，用于验证共识架构等价性、同源偏差可辨识与有限通信选择机理，不代表真实 e-puck 硬件外场实测指标。
