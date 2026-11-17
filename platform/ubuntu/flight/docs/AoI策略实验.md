# AoI 策略调度仿真

从 flight 目录运行。使用独立 Python 环境安装 requirements-aoi.txt。训练输出逐时隙评估记录，固定导出训练种子 11 的 AttnSAC actor 到 data/aoi-policy/actor.pt。重新训练会更新该模型；已有回放使用归档动作。

```sh
python scripts/train_aoi.py ../../../outputs/aoi-scheduler
python scripts/plot_aoi.py ../../../outputs/aoi-scheduler
python scripts/test_business_sitl_flow.py --aoi-policy --report ../../../outputs/aoi-scheduler/sitl.json
```

四个策略共享三路周期传感器、单传输时隙和相同训练步数。每步选取输出得分最大的传感器，其数据到达时更新接收端时间戳。评估使用相同的留出随机种子 100–107，每条轨迹 64 个一秒时隙。图为 3 个训练种子的均值与种子间标准差。横轴是训练环境步数。训练中的高度来自合成轨迹，SITL 调用时来自实际仿真遥测。SITL 使用墙钟约一秒触发一个合成传输时隙；报告中的 AoI 单位为模型秒。

仅用途为“AoI 策略调度仿真”的托管 SITL 任务启用此实验。回放保存每次调用的模型 SHA-256、执行编号、输入、连续动作、被调度传感器、交付结果及接收端平均 AoI。飞行航线沿用批准航点。训练奖励为负平均 AoI 除以 10。当前数据集没有保密中断事件或综合效能定义。

## 扩展训练与双机巡检

从 flight 目录执行以下命令。每个对比组保持环境、交互步数和测试轨迹一致，分组 CSV 保存每时隙的传感器选择、送达结果和平均 AoI。默认模型仍支持原单机任务。

```sh
for algo in AttnSAC SAC PPO DDPG; do
    python scripts/train_aoi.py "../../../outputs/aoi-extended/$algo" --algorithm "$algo" --steps 4096 --seeds 11 23 37 41 53
    python scripts/train_aoi.py "../../../outputs/aoi-joint/$algo" --algorithm "$algo" --joint --steps 4096 --seeds 11 23 37 41 53
done
python scripts/diagnose_aoi.py ../../../outputs/aoi-extended
python scripts/train_aoi.py ../../../outputs/aoi-fresh/AttnSAC --algorithm AttnSAC --fresh-prior --steps 4096 --seeds 11 23 37 41 53
python scripts/plot_aoi.py ../../../outputs/aoi-extended
python scripts/plot_aoi.py ../../../outputs/aoi-joint
cp ../../../outputs/aoi-joint/AttnSAC/actor.pt data/aoi-policy/joint.pt
python scripts/test_business_sitl_flow.py --joint-role 1 --report ../../../outputs/aoi-joint/sitl-1.json
python scripts/test_business_sitl_flow.py --joint-role 2 --report ../../../outputs/aoi-joint/sitl-2.json
```

双机环境由两条平行巡检走廊、两个发射时隙和三个状态源组成。策略同时给出两个纵向位移和两组三源优先级，送达概率随位置变化，每个源保留最新时间戳。固定航线的先验消融单独评估。其先验是“送达概率 × 接收端与发送端的时间戳差”，时间戳差由截断后的观测还原，学习策略提供四分之一幅度的优先级修正。

为使用单个托管 SITL 实例，从同一模型和相同场景种子生成两条计划，分别审批执行两个角色。飞行中用当前成员的实测东向位置更新策略观测，另一成员位置由模型推进。回放地图显示当前任务的批准航线与遥测航迹，时间轴显示同一帧对应的资源选择和 AoI 历史曲线。实时位移动作为策略建议，审批后的航点负责飞行控制。两个任务的在线状态队列独立，不能将其 AoI 拼接为同时双机实测数据。
