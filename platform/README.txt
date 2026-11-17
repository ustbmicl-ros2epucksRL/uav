多模态感知网络智能规划平台

示范验证（新增）
  示范总览 /demonstration；专属页 /demonstration/electromagnetic、/demonstration/corridor、/demonstration/urban。
  地面站挂载时统一加 /network 前缀，地面站顶栏已有“示范应用”入口。
  三场景任务、部署快照、数据导入、联合回放、评价对比、离线校正候选与证据导出。
  简明设计和研究点对应：docs/示范应用基础平台设计与验收.txt。
  独立启动脚本默认端口 8767；使用该脚本时访问 http://127.0.0.1:8767/demonstration。

相邻系统
  低空服务平台和飞机飞控平台运行在 Ubuntu 容器中，不替代本规划平台。
  说明和服务器测试：ubuntu/README.txt
  执行：sh ubuntu/test.sh

启动
  本机页面在 iCloud 目录中。platform/.venv 里的包一旦变成“仅云端”文件，
  bash scripts/run.sh 和 .venv/bin/python 会在 import 时永久卡住，页面表现为连不上。
  此时改用本机缓存环境，见文末“2026-10-17 页面连不上”。
  在 platform 目录运行：bash scripts/run.sh
  浏览器从地面站进入：http://127.0.0.1:8772/ ，顶栏「感知网络规划」
  同一端口的规划页：http://127.0.0.1:8772/network/
  申报书规划工作台：http://127.0.0.1:8772/network/workbench
  更换端口：SENSE_PORT=8768 bash scripts/run.sh
  首次安装：uv pip install --python .venv/bin/python -r requirements-dev.txt
  运行测试：.venv/bin/python -m pytest tests -q
  前端逻辑测试（服务启动后）：
    curl -fsSL http://127.0.0.1:8772/network/api/scenes/urban/report -o /tmp/lowair-report.json
    node --test tests/test_frontend.cjs tests/test_workbench.cjs

功能
  1. 三个可重复场景：建筑区、物流廊道、复杂电磁区。
  2. 模拟多传感器标准化位置量测，空间聚类、质量加权融合和卡尔曼跟踪。
  3. 量测、融合航迹和独立真值分层回放，支持观测中断后的预测滑行。
  4. 身份待核验、登记不允许、重点区进入、场景高度、偏航和滞留规则。
  5. 风险事件来源、不确定度、核验记录与状态闭环。
  6. 设备状态和挂载参数编辑，覆盖切片和薄弱区诊断。
  7. 几何覆盖补点、飞行任务观测信息补点，预算约束与同任务效果比较。
  8. 标准化量测 JSON 导入，配置、告警和实验报告导出。
  导入量测的回放和核验记录也保存在本机数据库，重新导入同一数据可恢复记录。

能力边界
  场景与设备参数均为合成数据。RF 按发射源可观测建模，静默目标不能依赖 RF 发现。
  合作报文只服务匹配合作识别码的目标，不计入非合作目标几何覆盖。
  输入是已标准化的位置量测，未实现雷达原始信号处理、无线电测向/定位反演。
  干扰通过预设丢测概率与量测噪声模拟，尚无实测传播或电磁干扰模型。
  三维覆盖使用原有 50 m 网格与简化建筑遮挡，不能据此宣称申报精度达标。
  位置融合和跟踪为基线算法，近距离目标和长时间遮挡可能误关联或丢轨。
  不确定度来自滤波模型，未做真实传感器校准。
  风险事件按每航迹每规则第一次触发记录；业务核验记录不改变算法原始评测。
  对未知身份发出待核验提示，不自动认定黑飞。场景重点区不是法定管制空域。
  偏航通过登记的水平航路 Y 和容差演示，正式航路需拓展为三维航线及有效时段。
  标准化量测导入不带独立真值，因此不计算导入数据的准确率或误差。

评测
  仿真真值仅用于模拟观测生成和离线评测，不传入跟踪或风险规则算法。
  每秒每目标一次样本。量测更新后的估计距真值不超过 200 m 计为有效观测。
  使用一对一真值匹配，定位 RMSE 只计算匹配样本，必须与观测率一起看。
  首尾缺测计入最长缺测，预测滑行不计入有效观测。
  身份切换是在真值匹配序列中跟踪编号变化的次数。片段数统计不同编号数。
  预警评测仅针对有独立真值的重点区进入和高度阈值事件，不评估身份合法性。
  同一场景、设备、目标和时间固定随机种子，补点前后保留原设备噪声序列。
  方案比较单独报告前后共同匹配样本的定位 RMSE，区分精度变化与观测样本增加。
  任务补点以测试路径的观测信息增益为代理目标，不保证真实跟踪或告警指标改善。
  默认三条测试路径分别模拟合作物流飞行、静默未知目标、发射信号的未知目标。

研究定位
  应用目标：支持黑飞、乱飞风险的发现、核验和研判。
  研究问题：遮挡、干扰、目标跨站移动与成本约束下，如何持续获得可信目标证据。
  建议贡献：三维有效感知能力建模、观测质量约束下的多站协同定位跟踪、
            面向任务效果和成本的规划以及运行反馈优化。
  当前平台提供可复核实验基线。科研贡献仍须具体方法创新、消融对照和外场验证。

数据
  设备配置、规则、登记、方案及核验记录保存在 data/platform.sqlite3。
  SENSE_DATA_DIR 可将数据放入其他本机目录。服务仅监听本机，不含生产鉴权。

监管口径参考
  无人驾驶航空器飞行管理暂行条例（中国民航局）：
  https://www.caac.gov.cn/XXGK/XXGK/FLFG/202401/t20240115_222642.html

2026-10-06 申报书工程工作台
  入口 /workbench；保留原场景与目标，根目录正式 PDF 为依据。
  建筑、设备组合与候选站址 JSON 编辑，含挂载高度、方位、俯仰、水平/垂直视场。
  10 m / 2 m 区域网格、建筑盒射线遮挡，Pd / SINR / FIM-CRLB 三维点云。
  共信道、通信干扰、天气衰减参数；均为示例模型，尚未经设备/场景标定。
  光电/RF/红外方向信息、雷达距离+方向信息；不可观测位置明确输出 null。
  有限候选子集逐方案比较成本、区域/重点区覆盖、精度合格比例。
  草案应用与场景版本/校正版本校验；配置改变后旧草案拒绝应用。
  XYZ 残差校正及顺序留后验证；噪声尺度为原生测角/测距噪声的代理。
  五类任务优先级、带宽/算力约束调度，只有本地模拟回执。
  工程报告持久化，记录范围、场景版本、校正、假设与原考核目标。
  五种标准化量测类型接口已支持；真实设备集成数量仍为 0。
  单次效能场最多 150000 网格；方案比较最多 15000 网格与 10 个候选。
  三维可旋转点云仅抽样显示，统计来自所选区域全部计算网格。
  原 50 m 切片与既有回放基线保留；不能将局部细网格当成全域达标。
  计算内核已封装到 app/coverage_sub1，独立容器不再读取工作区外部申报目录。
  UAV-DETR 论文归档在 tutorial-低空感知数据/refs/2026-UAV-DETR。
  详见 平台功能实现与验收对照.txt。

2026-10-07 五页规划流程
  简明设计与验证：docs/五页实施设计.txt
  地面站入口 /network/tasks；独立服务入口 /tasks。
  tasks → resources → diagnosis → planning → plans。
  任务与方案写入现有 SQLite；资源编辑会更新对应场景版本。
  方案确认不改变设备部署；历史快照可导出 JSON 和部署 CSV。
  前端逻辑验证增加 node --test tests/test_portal.cjs。

2026-10-07 V1 功能补全
  五页内已整合地图资源配置、型号模板、资产标识、工程容量、二维/三维诊断、
  自动与人工规划、前后覆盖对比、航路故障推演、确认应用及历史归档。
  方案“应用”只更新本地场景；不会向现场设备或飞机发送指令。
  38 项规划后端、11 项前端逻辑、17 项地面站回归通过。
  真实浏览器验收 tests/accept_portal.cjs 已通过：8 组操作、14 张截图、无控制台错误。
  截图与报告：../outputs/planning-v1-acceptance/验收报告.html。

2026-10-08 研究问题可视化
  /network/research/1 至 /5：对应申请书五个研究子任务。
  每页：核心问题、代表性交互、3 个难点、对照验证设计、已有方案及研究缺口。
  地面站和示范场景已增加导航。草稿本机保存，支持设计 JSON 导出。
  图示与算例不是实测结论，真实实验需独立证据。
  简明设计：docs/研究问题页面设计与验收.txt。
  浏览器验收：tests/accept_research.cjs。

2026-10-09 15 个难点独立实验
  原五页中的每个难点均增加独立参数、数值对照、失败边界及极值检查表。
  参数随草稿保存，支持导出当前实验及全部 15 个算例的可复算结果。
  模型代码 static/research-lab.js；公式及适用条件在页面明示。
  算例证据类型 synthetic_only，不代表真实设备或现场科研验证。
  验收报告：../outputs/research-labs-acceptance/验收报告.html。

2026-10-11 DeepSeek 研究智能体
  地面站 /network/agents；独立服务 /agents。
  默认复用本机 Ollama deepseek-r1:32b，支持单智能体和研究/复核双角色。
  模型选参 → 平台计算 → 结果分析 → 可选证据复核；记录支持查询、停止和导出。
  官方代码：third_party/DeepSeek-V3.2-Exp；R1 发布资料：third_party/DeepSeek-R1。
  源码来源与版本：third_party/SOURCES.json；简明设计：docs/低空研究智能体设计.txt。
  当前接入 15 个合成研究算例和任务摘要，尚未做低空领域微调或真实现场验证。

2026-10-17 页面连不上
  现象：浏览器打不开 http://127.0.0.1:8772/ 或 http://127.0.0.1:8767/。
  8772 没有 LISTEN。8767 虽在 LISTEN，但 curl 超时、响应 0 字节。
  原因：仓库在 iCloud。platform/.venv 的 uvicorn、fastapi、starlette、anyio
  等文件被清成仅云端（st_flags 含 0x40000000）。进程停在 read()，
  调用栈是 import 或 _Py_read。brctl download 不能在短时间内把文件拉回本地，
  对卡住的进程再读一次仍会一直等待。不要为此重启 Mac。
  另一类提示“无法读取遥测：signal is aborted without reason”是浏览器取消了请求
  （AbortError，网络面板为 canceled），不是文件损坏，也不是服务返回了 4xx/5xx。
  若 curl 根本连不上，则是服务没在听端口，与这条提示不是同一件事。

  正确启动（Python 放在 iCloud 之外）：
    环境：/Users/duan/Library/Caches/sensing-venv/bin/python
    地面站，工作目录 platform/ubuntu/flight，端口 8772：
      PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
      /Users/duan/Library/Caches/sensing-venv/bin/python \
      -m uvicorn app.main:app --host 127.0.0.1 --port 8772
    规划平台，工作目录 platform，端口 8767：
      PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
      /Users/duan/Library/Caches/sensing-venv/bin/python \
      -m uvicorn app.main:app --host 127.0.0.1 --port 8767
  缓存环境不存在时，用 Homebrew Python 3.12 重建，并安装
  fastapi、uvicorn、numpy、scipy、httpx、pypdf、python-multipart
  （版本上限与 requirements.txt 相同）。
  确认：lsof 能看到 127.0.0.1:8772 和 127.0.0.1:8767 的 LISTEN，
  并且 curl 在数秒内得到 200。地面站再查 /api/station/state。
  启动超过约 20 秒仍无 “Uvicorn running” 日志，说明又在读仅云端文件，
  结束该进程后改用上面的缓存解释器，不要再等。
  结束进程要用 kill -9。普通 kill 后 lsof 可能已经看不到 LISTEN，
  但进程仍在。不要结束本项目以外的 uvicorn。
  在本仓库里做全目录搜索会因 iCloud 卡住；只搜索明确的小目录。

2026-10-26 架构与后台计算改造
  后端拆分场景、核心接口、工程接口和地图接口，保留已有路径。
  工作台效能场和方案比较改为独立子进程计算，支持取消、超时和历史快照重跑。
  新入口 /computations；地面站 /network/computations，工作台顶栏已有链接。
  地面站显式管理规划子应用生命周期；容器规划库、地图配置增加持久化卷。
  启动脚本优先本机缓存环境，绕开 iCloud 字节码缓存，增加 20 秒导入检查。
  备份/校验/迁移工具：scripts/data_admin.py；不自动移动旧数据。
  模块职责、任务状态、接口、启动及数据恢复：docs/架构改造与运行说明.txt。

2026-11-02 雄安建筑参考图层
  一键工具：bash scripts/buildings.sh；只检查已有数据：加 --check-only。
  生成 outputs/building-validation.json；候选数据通过校验后才替换正式图层。
  完整操作说明：docs/建筑数据处理工具说明.txt。
  地面站保留默认二维视图；右上角“三维”可勾选本地历史建筑轮廓及高度。
  雄安 GBA 历史轮廓默认关闭：核对发现部分旧村轮廓与卫星图中的新建区不一致。
  不做人为平移或修改高度来伪装为当前建筑；独立预览默认展示北京国贸。
  左侧“区域三维预览 · 雄安 / 北京”打开独立预览页，可切换雄安两处范围
  与北京国贸对照区域；不会修改飞行器、航线或任务的地理锚点。
  预览地址：地面站 /network/static/building-preview.html，
           规划服务 /static/building-preview.html。
  三维地图左下角“仅看轮廓（核对底图）”隐藏体块，转为俯视黄线轮廓，
  取消勾选恢复斜视。规划平台使用同一份数据和渲染组件。
  static/buildings.geojson 是按两处演示区域裁剪的 WGS84 数据，
  不是后端仿真障碍库，不会改变覆盖、航线规划或绕障结果。

  来源：https://github.com/zhu-xlab/GlobalBuildingAtlas
  引用：Zhu, Chen, Zhang, Shi & Wang (2025), GlobalBuildingAtlas,
       Earth System Science Data 17, 6647–6668, doi:10.5194/essd-17-6647-2025。
  论文说明主要影像基期为 2019，部分使用 2018；雄安新建区域可能缺失。
  高度是机器学习估计，保留 height_estimated、height_source、height_variance；
  非测绘、非带外墙纹理的实景模型，底部仍统一放在局部参考平面。
  GBA.LoD1/Polygon 和高度采用 CC BY-NC 4.0；ODbLPolygon 单独采用 ODbL。
  每条记录保留 footprint_license；仅用于本项目非商业研究演示。

  重建（在 platform 目录运行；不是启动服务的必要步骤）：
    python3 -m venv /tmp/gba-venv
    /tmp/gba-venv/bin/python -m pip install -r requirements-geodata.txt
    /tmp/gba-venv/bin/python scripts/prepare_gba_buildings.py --workers 6
  脚本使用官方 HuggingFace 发布文件，不访问禁止自动查询的展示用 WFS。
  瓦片 e115_n40_e120_n35 约 12.2 GB：以 HTTP Range 分段扫描，只落盘
  当前分段和裁剪结果。默认缓存 /tmp/gba-xiongan-cache，可断点继续。
  修改 BOUNDS、数据源版本或解析规则时应使用新的 --cache 目录。
  处理顺序：EPSG:3857 范围裁剪 → EPSG:4326 → source+id+region 关联高度
  → 原子写入 GeoJSON。浏览器再用与卫星图相同的局部东/北坐标转换。
  metadata 保存原始地址、范围、时间、坐标系与高度基准；终端输出 SHA256。
  测试：
    /tmp/gba-venv/bin/python tests/test_prepare_gba_buildings.py
    node --test tests/test_building_data.cjs tests/test_real_buildings.cjs

  北京对照来源：OpenStreetMap API 0.6 map，范围
  116.452,39.901,116.468,39.916。署名 © OpenStreetMap contributors，ODbL。
  static/buildings-beijing.geojson 保留 OSM 高度/楼层标签；无高度时楼层×3m，
  无楼层时9m示意，灰蓝表示标注高度，米黄表示估算/缺失高度示意。
  转换会处理 multipolygon 外环/内院并去重成员 way；不完整几何计数保留。
  已下载 map XML 时可重建：
    /tmp/gba-venv/bin/python scripts/prepare_osm_preview.py \
      /tmp/beijing-buildings-osm.xml static/buildings-beijing.geojson
  验证：/tmp/gba-venv/bin/python tests/test_prepare_osm_preview.py

  本次数据验收：雄安 1,646 个要素 / 1,648 个 Polygon 分量，全部关联到
  GBA 模型高度（0.46–11.35 m），无统一高度补齐。地面站可见 853 个分量，
  感知规划可见 795 个。北京 269 个要素 / 271 个分量，23 个使用数值高度
  标签，248 个为楼层推算或9m示意；各类来源在界面与点击详情中区分。
  视觉核对发现雄安旧村轮廓与卫星影像中的新道路、施工区存在年代差异，
  因此历史轮廓默认关闭，保留主动核对；不宣称其为当前完整建筑现状。

2026-11-03 独立课程学习
  入口：规划服务 /learning；地面站 /network/learning。
  课程学习有独立目录、PDF 阅读、版本切换、页码书签、状态、笔记和 JSON 导出。
  已收录 HKU DATA8010 第 1—8 讲及 3 份教程；第 1 讲另有中文学习译本。
  学习目标是整理提示，原文来源、页数、下载时间在 catalog.json 中记录。
  资料：resources/courses/hku-data8010；不依赖工作区 outputs 或演示场景。
  记录：SENSE_DATA_DIR/learning.sqlite3（默认 data/learning.sqlite3），独立于平台数据库。
  当前是本机共享档案，无多用户账号；学习状态由用户手动标记，不由打开 PDF 自动判定。
  PDF.js 本地渲染，翻页会更新待保存的书签；点击保存后持久化。切换版本时重置为 1。
  备份学习功能时需同时保留资料目录和 learning.sqlite3；也可从页面导出记录 JSON。
  后端验证：python -m pytest tests/test_learning.py -q。
  浏览器验收：LEARNING_BASE=http://127.0.0.1:8876/network node tests/accept_learning.cjs。
  浏览器测试会写入验收笔记，只能针对隔离的测试数据目录执行。

2026-11-04 多课程资料库
  课程选择与跨课程检索：/learning?course=<课程ID>&lesson=<章节ID>。
  GET /api/learning/courses 提供课程和外部课程入口；原 catalog/files/records/export
  接口增加可选 course 查询参数，默认 hku-data8010，旧地址与原有笔记保持兼容。
  新课程各有 resources/courses/<课程ID>/catalog.json 和原版 PDF。
  新课程笔记使用“课程ID::章节ID”键，与原 HKU 章节记录隔离；导出为当前课程记录。
  已收录 JHU 网络视频具身学习、CMU 具身智能安全的公开课件；JHU 官网公开分片
  依照其网页解码方式合并为 PDF，未改写内容。补充引用课件单独标记为补充资料。
  ETH 当前和往年课程、北大、港中深的官方入口在 references.json；需要校内账号
  或只有课程介绍的内容明确标记，不计入本地讲义数。每份文件保留来源、页数与哈希。

中文学习译稿与中英对照
--------------------
课程页提供 PDF 阅读、中英对照、中文逐页学习。对照模式始终按英文原页索引；
中文 PDF 的 source_page_map 将原页映射到译稿实际页码，含封面及续页也不偏移。
英文原稿和原有中文第一讲均保留，既有 resource/page 学习记录无需迁移。

批量生成脚本：scripts/translate_courses.py
- 公开讲义正文由本地模型完整翻译；扫描/图片页使用本地图像识别，保留完整原图。
- 本地 Ollama qwen3:8b（文本）及 qwen2.5vl:3b（图像识别） 分别生成完整译文、学习总结和批注。
- 当前批处理不调用外部翻译服务，也不会读取或上传学习笔记。
- 逐页缓存和进度位于 ../outputs/course-translations/，支持断点续跑和失败页重试。
- 每讲完成后用 XeLaTeX 编译 PDF，将 *-zh-study.pdf 和 *-study.json 写入课程目录。
- 译稿为非官方机器辅助学习资料，未逐句人工校对；图表、公式与研究结论应对照原文。
- 中文 LaTeX 源码、原页映射、来源摘要和编译日志均保留，便于校订与重建。

启动制作：python3 scripts/translate_courses.py
需已有 Ollama、模型、Poppler、XeLaTeX，以及项目中的 Source Han 字体文件。
不需要 OpenAI API 密钥；程序不会读取该密钥。

检查：python -m pytest tests/test_learning.py -q
只读浏览器全库验收：node tests/accept_learning_bilingual.cjs（从工作区根目录执行时请加 platform/）
可设置 LEARNING_BASE 为服务入口，PLAYWRIGHT_MODULE 为 Playwright 模块绝对路径。
ALLOW_PARTIAL=1 只检查已生成版本；最终验收必须不设置此变量，确保所有讲义有中文版。
文本块提取依赖见 scripts/requirements-translation.txt；请安装到 iCloud 外的独立 Python 环境。
本次执行使用 PYTHONPATH=/tmp/course-pdf-tools。换机器或清理临时目录后，需在所选环境中重新安装依赖。
最终文件检查/按当前排版重建：python3 scripts/check_course_translations.py --rebuild
该命令只使用已保存译稿编译，不调用翻译模型；原始英文 PDF 保持不变。

扫描页检查：程序检测不可见 OCR 文字层，对这些页重新从原图识别；不直接翻译错误的隐藏文本。
source-analysis.json 记录这类页码，最终检查要求其使用重新识别的译文或已核对的人工修订。
文字抽查候选：python3 scripts/inspect_course_translation_text.py（数量级与残留英文提示，需人工结合原文判断）。
早期学习辅助复核：python3 scripts/review_course_metadata.py；最终重建前应等待复核结束。

数据安全柜账号服务：
/data-cabinet 已启用独立账号与组织权限，旧演示身份 API 已关闭。
需配置独立检查点并离线执行账号表迁移；详见 docs/数据安全柜账号与组织权限部署.txt。

容器模板注册表升级：停服务后运行 python -m app.cabinet_admin migrate-templates；详见 docs/容器模板注册审核与版本管理.txt。

图片视频附件：需 migrate-attachments、Pillow 和可信 ffprobe；详见 docs/图片视频附件加密入柜与预览.txt。预览需独立 preview 授权。

分块附件升级：migrate-chunks + SENSE_CABINET_OBJECT_CONFIG，前端支持断点续传与视频 Range；详见 docs/分块加密上传与独立对象存储部署.txt。

真实对象服务：scripts/cabinet_object_service.py 支持 SeaweedFS 一键部署、重启和检查；离线迁移命令与真实故障验收见 docs/真实对象存储部署与迁移验收.txt。

Mac 开发与硬件迁移边界：docs/Mac开发演示与Ubuntu可信计算迁移方案.txt，包含四模块状态、SNP/TDX 条件、密钥路径改造、阶段部署与 H01—H12 待执行验收。

云边端一键 SITL 回归（2026-11-13）
---------------------------------
从仓库根目录运行：
  /tmp/lowair-algorithms-venv/bin/python platform/ubuntu/flight/scripts/run_compute_regression.py
可选：--url http://127.0.0.1:8773 --output /tmp/my-new-regression-report
--output 必须是尚不存在的目录，避免覆盖旧证据。

前置条件：本机 flight 测试服务已启动（默认 8773），ArduCopter SITL 已构建，
测试视频与 SSDLite 模型资源已经准备好，当前 Python 有项目依赖且 PATH 有 Node。
在隔离测试服务上运行，会保留新增资源、申请、任务和飞行档案供回查。
入口仅接受本机 HTTP 地址；同地址禁止并行运行。正在飞行或计算时拒绝执行。
回归会通过 /api/sitl/restart 重启闲置、已落地且未解锁的自管 SITL，恢复电池初始状态，
保留旧档案。不会跳过审批、电量、航路或空域检查，也不接真实飞控。

流程：新建资源与申请 → BIT* 规划 → 审批/验证/装订 → 起飞/AUTO → 正常/弱网/
断链/节点掉线/恢复 → 落地同步 → 读取持久化档案与检测帧 SHA256 → 事件与地图
投影校验 → 用新档案执行前端处理器测试（筛选、前后导航、暂停、返回实时）。
正常运行约 2–4 分钟；非零退出代表失败，不能仅凭报告文件存在判断通过。

报告默认保存在 outputs/compute-regression/YYYYMMDD-HHMMSS/：
  report.html / report.json：结果、阶段耗时、失败原因及回放链接
  flow.json / sitl-flow.log：本轮任务、执行、遥测、批次及飞行日志
  replay-checks.json / archive.log：事件、迁移节点、时延、检测关联
  frontend.log：前端处理器测试结果
即使失败仍写报告并保留部分飞行证据。飞行阶段异常会请求本次 SITL 降落；
超时时仅在 execution_id 匹配本轮且仍解锁时请求降落并记录结果。

真实浏览器验收已接入默认流程：安装 Google Chrome，并安装测试依赖：
  /tmp/lowair-algorithms-venv/bin/python -m pip install -r platform/ubuntu/flight/requirements-browser.txt
默认启动独立 Chrome 窗口，不读取或修改个人浏览器配置；需要无界面运行可加 --headless。
浏览器读取本轮新任务档案，实际点击事件类型、前后导航和地图迁移标识、拖动时间轴，
核对地图链路/节点、迁移前后时延、同批次检测图 URL 与解码结果，并测试返回实时与重新读取。
检查不向飞控发送控制请求，不注入页面内部状态。浏览器异常会使整轮回归失败。
browser.json / browser.log 记录检查项和错误；browser-*.png 保存事件、迁移及失败截图，
report.html 内嵌截图，report.json 合并浏览器状态。外部卫星瓦片加载异常单独列为 warnings，
业务地图图层和关联证据仍必须通过。现有页面布局未修改。

连续多轮稳定性回归
------------------
从项目根目录执行（默认三轮）：
  /tmp/lowair-algorithms-venv/bin/python platform/ubuntu/flight/scripts/run_compute_stability.py
设置 → 稳定性回归 → 三轮页面验收，可启动并查看当前步骤、历史统一报告与验收日志和截图。
从命令行启动三轮并验收刷新、关闭重开、历史报告和失败证据入口：
  /tmp/lowair-algorithms-venv/bin/python platform/ubuntu/flight/scripts/test_stability_ui.py
默认连接本机 8773 验收服务。若服务自定义 FLIGHT_STABILITY_DIR，运行命令时需设置同一目录。
页面检查及截图保存在本轮 platform-ui/，结果并入 summary.html 和 summary.json。
失败入口通过隔离的档案断言故障验证，命令遇到验收失败返回非零。
指定五轮：
  /tmp/lowair-algorithms-venv/bin/python platform/ubuntu/flight/scripts/run_compute_stability.py --rounds 5
其他参数：--url http://127.0.0.1:8773、--headless、--output /tmp/new-stability-report
轮数必须是正整数；输出目录必须尚不存在。前置条件同单轮回归。

严格串行执行每轮新建任务、SITL 飞行/故障恢复、归档检查及真实 Chrome 回放验收。
任一轮失败即停止后续轮次并返回非零，保留该轮全部任务档案、日志、截图和报告；
不会清理旧证据，也不会为了继续测试而自动覆盖失败现场。飞行失败仍沿用单轮脚本
对本次 SITL 的降落保护，并非要求飞机保持在空中。

默认结果目录：outputs/compute-stability/时间戳/
  summary.html / summary.json：统一状态、计划/通过/失败/未运行数量、每轮任务、
    总耗时、SITL/浏览器阶段耗时、失败原因、警告、截图及各阶段日志链接。
  round-N/：第 N 轮完整证据；round-N-runner.log：该轮命令输出。
每轮完成后原子更新汇总文件，运行中已完成轮次的证据也可查看。
只有全部计划轮次及各自浏览器阶段均通过，整轮套件才返回 0。

平台内启动与查看稳定性回归
--------------------------
地面站“设置 → 稳定性回归与历史报告”提供运行轮数（1–20）、后台启动、
每轮阶段和任务状态、失败原因，以及原始日志/报告/截图。弹窗关闭或页面刷新
不影响后台运行；每 3 秒更新进度，已展开的证据不会被自动折叠。
历史结果取自 outputs/compute-stability，可用 FLIGHT_STABILITY_DIR 指定目录。
服务应配置 FLIGHT_BASE_URL 为自己的本机 HTTP 地址；默认 8772，测试服务 8773。
仍需当前服务 Python 环境已安装 SITL/检测/Playwright 依赖，系统安装 Chrome。
同一时间只启动一个平台回归；有飞行或计算任务运行时拒绝启动。后台回归依旧
保留安全检查、失败停止及证据；历史报告可在重启服务后读取。

接口：GET/POST /api/stability；GET /api/stability/{id}；
GET /api/stability/{id}/files/{path}（仅允许该回归目录内的报告、日志与 PNG）。
当前展示最近 50 组历史回归，不自动删除原始目录。

申请书指标与规划验证（2026-11-16）
  页面 /acceptance，地面站挂载为 /network/acceptance。
  支持任务快照、广域 10 m / 重点区 2 m 分批验算、设备型号/挂高/朝向联合搜索、指标对照和地面站仿真航迹关联。
  操作、统计口径与复现命令见 docs/申请书指标验证说明.txt。
  三场景复核命令 python scripts/verify_proposal.py --output ../outputs/proposal-verification-20261116
  新增测试 pytest tests/test_planning_assessment.py -q 与 node --test tests/test_acceptance.cjs。

平台密钥模式可选 demo 或 hardware。Mac 演示、Ubuntu Trustee 配置与验证命令见 docs/平台密钥模式与远程证明接入.txt。
存量演示密钥可在停机窗口用 migrate-demo-keys 迁移，Mac 合成演练与 Ubuntu 切换/恢复命令见 docs/平台密钥模式与远程证明接入.txt 末节。

真实 Trustee KBS（sample 证明策略）与 SeaweedFS 大视频迁移一键验收：bash scripts/accept_seaweed_key_migration.sh。环境准备、故障点与 Ubuntu 切换见 docs/真实SeaweedFS大视频密钥迁移验收.txt。
Ubuntu 机密来宾验收：scripts/accept_seaweed_key_migration.sh --mode snp（或 tdx）--config 来宾配置 --controller 独立控制适配器 --report 新JSON文件。部署和权威审计事件接口见 docs/真实SeaweedFS大视频密钥迁移验收.txt。
数据保险箱存储图：docs/figures/cabinet-storage.pdf，独立 XeLaTeX 源文件同名。v3 sample 验收将预览 ID 与取钥调用 ID 绑定至真实 RCAR 会话并记录并发重叠时长。复核命令为 python3 scripts/cabinet_trustee_audit.py <报告>。

智能体与平台工具融合
  /agents 新增“平台诊断”工作流：场景快照、覆盖基线、规划任务和历史验算证据。
  同次运行冻结输入，保存证据编号；提供历史记录、停止与导出，保留原研究算例。
  GET /api/research-agent/tools 提供工具目录；POST /tools/{name} 仅开放白名单只读工具。
  通用框架客户端：scripts/agent_platform_client.py；设计及接入契约：docs/智能体框架融合与工具接口.txt。
  当前没有接通真机控制，也未安装第三方智能体框架运行时。

Copilot 式辅助窗口
  地面站、业务服务及主要规划页右下角新增“平台助手”，默认收起；原完整界面保留。
  支持收起保留草稿、Escape、独立窗口和首次带入规划场景；没有安装 CopilotKit 或 LangGraph。
  设计与验收见 docs/智能体框架融合与工具接口.txt 第八节。
