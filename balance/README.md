# balance/ — 数据可玩性分析工具（GM 设计辅助）

**不发布、不进镜像、不改游戏。** Dockerfile 只 COPY `gamedata/ pyserver/ frontend/ config/`，本目录天然不在镜像里；产物在 `balance/out/`（gitignore）。

工具以只读方式 import `pyserver`：伤害、命中、经验惩罚、组队、爆率、修理、回收、托管 AI 全部直接调用服务端代码，不另写公式。数据（CSV / MonGen / MonItems / game_rates.json）一改，报告跟着变。

## 用法（Python 3.10，纯标准库）

```bash
python balance/run.py all                     # 引擎A + 引擎B + 报告（默认读 server.properties 里的生效数据树）
python balance/run.py report --compare 176    # 叠加另一棵数据树做 A/B 对比
python balance/run.py all --tree 176          # 指定数据树：main / 176 / 任意路径
python balance/run.py arena --budget 150      # 分步跑；--budget N 表示跑 N 秒后保存进度并退出，下次续跑
python balance/run.py all --quick             # 少样本快速出图，调试用
```

报告输出到 `balance/out/<树>-<时间>/index.html`（同目录还有 `snapshot.json`），`balance/out/latest.html` 始终指向最新一份。双击即可打开，不需要任何服务。

**缓存**：结果按「数据树 + 倍率表 + pyserver 代码 + 引擎代码」的内容指纹缓存在 `out/cache/`。只要任何一项变化，就会自动重算。全量跑一次的耗时：引擎 A 约 10 分钟，引擎 B 约 20 分钟（2 核）。

## 两个引擎

| 引擎 | 做什么 | 测出什么 |
|---|---|---|
| A 单挑竞技场 `mirbal/arena.py` | 用虚拟时钟启动真实 GameService，由 AutoPilot 托管内核打单只怪。每个「职业 × 等级 × 装备档 × 怪」组合打 8 场 | 击杀秒数、死亡率、喝药数量、武器和装备磨损、护身符消耗、技能修炼点 |
| B 真实地图 `mirbal/worldsim.py` | 真实地图 + MonGen 刷新 + N 个托管角色集中在最密的刷怪点 | 每小时经验/金币/死亡、拥挤衰减、η（纯战斗时间占比） |

`mirbal/analysis.py` 把两个引擎的结果、静态数据和规模假设合成升级时间线、经济收支、装备获取、技能修炼、全服规模等指标；`mirbal/gm.py` 负责自动体检规则；`mirbal/report.py` 加上 `assets/` 生成单文件 HTML（图表库自写，零依赖）。

## 报告内容
总览 · GM 体检 · 经验表 · 升级时间线（职业 × 装备档 × 在线人数 1/10/100/1000）· 练级点热力 · 怪物性价比 · 经济收支（练级 / 打钱两种策略）· 装备获取 P50/P90 · 技能修炼 · 真实地图效率 · 全服规模 · A/B 对比 · 口径说明。

GM 分析与动态补救方案见 [GM-ANALYSIS.md](GM-ANALYSIS.md)。

## 约定
- 不修改 `pyserver/`、`gamedata/`、`config/` 下的任何东西。模拟时如果发现服务端异常，只记录并报告（报告里归在「代码」类）。
- 临时数据库建在系统临时目录，不碰 `data/jootm2*.db`。
- 副本数据树没有 Map junction 时，自动借用主树的地图（两棵树的地图本来就是共享的）。
