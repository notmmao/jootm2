---
name: mir2-balance-analysis
description: 用 balance/ 工具对当前生效 gamedata 做数据可玩性模拟（升级时长、练级点、经济收支、装备获取、技能修炼、拥挤与全服规模），并站在 GM 角度按 1-10/10-100/100-1000 人分析不合理数据、给出动态补救方案（只出方案不实施）。当用户改了数值/掉落/倍率想看影响、要做平衡分析、要对比两棵数据树、或问"某级要练多久/哪里练/赚不赚钱"时使用。
---

# Mir2 数据可玩性分析（GM 平衡）

## 目标

基于**当前生效数据**跑模拟 → 出带图表的单文件报告 → 用 GM 视角给结论与动态补救方案。先例：`balance/archive/2026-11-19-727008d/`（主树 vs 176），结论见 `balance/GM-ANALYSIS.md`。

## 硬性约束

1. **工具与游戏代码隔离**：所有东西只在 `balance/` 下。**禁止**修改 `pyserver/`、`gamedata/`、`config/`、数据库；工具只读 import pyserver。Dockerfile 只 COPY `gamedata/ pyserver/ frontend/ config/`，`balance/` 天然不进镜像，不需要也不要改 `.dockerignore`。
2. **公式一律调服务端**：伤害/命中/经验惩罚 `calc_get_exp`/组队/爆率 `DropRates`/回收 `_sell_price`/修理 `_repair_price`/商品 `NpcScriptEngine.goods`/配装 `Player.recompute`/战斗 AI `AutoPilot`。分析代码里**不重写任何游戏公式**；发现服务端行为可疑就记录到报告的「代码」类，不改。
3. **只出方案**：GM 补救方案写进 `balance/GM-ANALYSIS.md`（或新一轮的分析文档），不实施；优先落在现有旋钮（`config/game_rates.json` 全局/按怪/按物品、`weekend.*`、`afk.*`），不够才提新机制，且新机制需注明要先找 Delphi 依据或作为明确新增功能。
4. 纯标准库、Python 3.10；无第三方包（图表是 `balance/mirbal/assets/charts.js` 自写）。
5. 提交标签用 `[分析]`（已登记在 CLAUDE.md 标签表）。

## 结构速查

| 文件 | 作用 |
|---|---|
| `balance/run.py` | CLI：`arena` / `world` / `report` / `all`，`--tree`、`--compare`、`--budget`、`--quick` |
| `mirbal/boot.py` + `clock.py` | 虚拟时钟（替换 `time.time`，须先于 import pyserver）+ 临时 SQLite 无界面起服；副本树缺 Map junction 时借主树地图 |
| `mirbal/data.py` | DataSet：怪物/刷怪/掉落期望/商店/全服最高回收价 |
| `mirbal/gear.py` | 装备档 `none`(木剑+布衣) / `shop` / `best`(商店∪可掉落)，真实 recompute 贪心配装 |
| `mirbal/arena.py` | 引擎 A：AutoPilot 单挑单只怪，测 ttk/死亡/喝药/磨损/护身符/修炼；道士开战前补召宝宝；捕获服务端异常 |
| `mirbal/worldsim.py` | 引擎 B：真实地图+MonGen+N 个托管角色同图，测人均经验/击杀/死亡、η |
| `mirbal/analysis.py` | 合成：练级最优（死亡率≤10% 且喝药≤240 瓶/h）、刷新供给上限、升级时间线、经济、装备 P50/P90、技能、全服规模 |
| `mirbal/gm.py` | 自动体检规则（经验断崖、单级陡增、职业差、一家独大、拥挤、围殴、收支为负、药价倒挂、装备 P90、产金怪、数据异常、服务端异常、规模） |
| `mirbal/report.py` + `assets/` | 单文件 HTML（数据内嵌）+ `snapshot.json` |

## 流程

### 第 1 步：确认要分析什么
- 默认读 `server.properties` 生效树；用户改了数据想看影响 → 先 `git stash`/记下旧指纹，跑新旧两版用 `--compare`；对比副本树 → `--compare 176`。
- 倍率实验：临时改 `config/game_rates.json` 前先问用户（那是游戏配置），或复制数据树到 `data/gamedata/<tag>` 后 `--tree <tag>`。

### 第 2 步：跑模拟（耗时大头）
- 本机直接：`D:/Python310/python.exe balance/run.py all [--compare 176]`（引擎 A 约 10 分钟，引擎 B 约 20 分钟，2 核）。
- **在 Cowork 设备 VM 里跑**：后台进程会在命令结束后被杀，单条命令上限 180s → 必须前台循环：
  `timeout 170 python3 run.py arena --budget 135` 重复直到出现「完成 225/225」，`world` 同理（单场景 >150s 的跑不完，报告用 `report` 时只取已缓存场景，要在文档里注明缺口）。
- 结果按指纹缓存在 `balance/out/cache/`；指纹 = 数据树 + `game_rates.json` + `pyserver/` + `server.properties` + `arena.py/gear.py/worldsim.py/boot.py`。改这些文件任何一个都会重跑；只改 `analysis.py/gm.py/report.py/assets` 不会。
  - 若只是改了 boot.py 之类不影响模拟结果的地方，可把旧缓存目录改名成新指纹复用（`python -c "...batch.fingerprint(None)"` 取新指纹）；**影响模拟语义的改动必须重跑**。

### 第 3 步：出报告并自查
- `python balance/run.py report [--compare 176]` → `balance/out/latest.html`。
- 必看：页面无 JS 报错；热力图色阶有层次；装备表 ∞ 的行有「打不动/不在等级窗口」来源说明；经济图正负堆叠合理。可用 Playwright 截图各 `section`。
- 数字合理性抽查（先例里踩过的坑，见下）。

### 第 4 步：GM 分析
- 读 `snapshot.json` 的 `findings` 作起点，**不要照抄规则输出**，要人工归因、合并、排序：先「代码/数据错误」，再经济，再职业/曲线/刷怪/装备。
- 每个问题都按规模写：**1-10 人**（本项目真实规模：真人+机器人+离线修炼，NPC 回收是唯一出口）、**10-100 人**（热门点拥挤、刷新不够分）、**100-1000 人**（供给饱和、通胀、垄断）。
- 方案写成：指标 → 规则（含上下限/平滑/冷却/公告）→ 实现落点（哪个函数乘系数）→ 用本工具怎么验证。
- 建议执行顺序 + 已知局限必须写。

### 第 5 步：归档与提交
- 本轮最终报告复制到 `balance/archive/<日期>-<代码提交号>/`：`index.html`、`snapshot.json`、当前指纹的 `cache/`（arena-*、world-*），加一个 README 写命令/指纹/缺口/对应结论文档。**中间调试轮次和过期指纹缓存不归档**（数据是错的，会误导）。
- 设备 VM 里提交前先 `device_request_delete_permission`（git 要删自己的 lock/tmp_obj）；作者用仓库既有身份 `git log -1 --format='%an <%ae>'`。

## 踩过的坑（新改动先对照）

1. **DataSet 必须在清空 `mon_gen_entries` 之前建**：掉落来源依赖刷怪表，否则「本级最强」退化成商店装。
2. **`none` 档要显式穿木剑+布衣**：玩家对象跨组合复用，不重置会继承上一档装备。
3. **道士要补召宝宝**：AutoPilot（在线托管/离线修炼/机器人共用）不会召唤，道士会被低估约 40%；引擎 B 保持托管原样，所以 η 校准排除道士。
4. **刷新供给上限对单人也生效**：`0` 后缀精英怪全服 1~2 只，不加上限会算出「单人每小时百万金币」的假象。
5. **「练级最优」要排除喝药狂刷**（>240 瓶/h）：否则法师会选每小时喝掉百万药钱的怪。
6. **回收价/修理价要遍历全部商人取最优**：单个 NPC（如屠夫）只收肉，会让掉落价值全是 0。
7. **护身符耐久跨场补充会变负值**：耐久快照要在每场补给之后取。
8. **引擎 B 被围殴**：最密刷怪点（采石场东）单人 13~19 级会每 5 秒死一次；死亡率高的场景不进 η 校准，但作为「单挑最优≠真实可刷」的证据保留。
9. **已知服务端缺陷（截至 2026-11-19）**：`_stick_ai`（食人花）→ `_apply_hit_to_player` 目标可能是宝宝 Monster，抛 `AttributeError: mana_to_health`。修复前跑道士会在报告「代码」类看到它。
10. 掉落「打钱」收益按全部卖 NPC 计；规模场景按等级均匀分布、24h 在线——都是压力放大口径，不是在线预测。

## 扩展
- 新体检规则：`gm.py` 里加一个 `_f(kind, sev, title, detail, scale, chart, remedy)`；`chart` 对应报告 section id。
- 新图：`report.py::collect` 加数据 → `assets/report.html` 加 section → `assets/app.js` 用 `Viz.line/bar/heat/scatter` 渲染。配色遵循 CSS 变量（分类色固定顺序、顺序色单色相）。
- 新等级点/职业/档位：`batch.py` 的 `LEVELS/JOB_IDS/TIERS`；引擎 B 场景：`worldsim_batch.py` 的 `LEVELS/NS`。
