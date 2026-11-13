---
name: mir2-db-compare
description: 对比第三方传奇 Mir2 私服数据库（SQL dump / SQLite .db / Paradox .DB）与本项目 gamedata/mud2/DB 的 CSV，产出 docs/DB_COMPARE_*.md 报告。当用户提供另一套私服 DB 要求对比、评估能否借用内容、找数值差异/同源关系时使用。已完成的先例：applem2（Paradox）、GEEM2（SQL dump）。
---

# Mir2 第三方 DB 对比

## 目标

把用户给的第三方私服 DB 与本项目 `gamedata/mud2/DB/*.csv`（Monster/StdItems/Magic 三张表）做逐表对比，产出可留档的报告，回答三个问题：**对方是什么血统、哪些数值被改过、有什么可以借用**。

## 硬性约束

1. **材料落位**：导出物与中间结果放 `data/db_compare/<tag>/`（`data/` 已 gitignore，**不入库**）；报告放 `docs/DB_COMPARE_<TAG>.md`（入库）；一次性深挖脚本放 `data/db_compare/` 里，不进 `scripts/`。
2. **解释器用 `D:/Python310/python.exe -I`**（零第三方依赖的脚本不挑版本，但保持项目约定）。
3. **不动本项目 `gamedata/mud2/DB/*.csv`**——它是对比基准。
4. Windows 控制台与重定向默认 **GBK**：跑输出中文的脚本一律加 `PYTHONIOENCODING=utf-8`；重定向文件乱码时按 GBK 读回再转 UTF-8。

## 流程

### 第 1 步：识别对方格式并导出成 CSV

| 对方格式 | 工具 |
|---|---|
| SQL dump（SQLite 方言：`CREATE TABLE` + `INSERT INTO "T" VALUES …`） | `scripts/mir2_db_sql_export.py <dump.sql> -o data/db_compare/<tag>`（内存 sqlite3 灌入导出，UTF-8 读取） |
| Paradox `.DB`（原版 M2 格式） | `scripts/mir2_db_export.py <对方DB目录> -o data/db_compare/<tag>`（需 `pip install pypxlib`，表头取 DB 自身字段名） |
| 直接是 SQLite `.db` 文件 | 用 `python -c "import sqlite3; …"` 查 `sqlite_master` + `pragma table_info` 即可读，可仿照 `mir2_db_sql_export.py` 导出（先例：`176-mir2-red/REDM2.db`，可作 1.76 参照库） |
| C# Crystal 引擎的 `Server.MirDB`（.NET BinaryWriter 顺序格式） | `scripts/crystal_mirdb_export.py <Server.MirDB> -o data/db_compare/<tag>`（按 suprcode/crystal v105 commit `485e59afab` 镜像 Load；**EOF 精确消费 = 正确性唯一验证**。导出 StdItems/Monster/Magic/**Npcs/Maps** 五张 CSV。先例：`mir2ServerRelease`，版本头 105；报告见 `docs/DB_COMPARE_CRYSTALDB.md` §1 的反推记录） |
| 已是 CSV | 直接放入 `data/db_compare/<tag>/`，确认 UTF-8 编码 |
| 静态 HTML 资料站 | 先盘点页面家族/编码（见 `data/db_compare/html_site_survey.py`），再正则提取 `<tr>/<td>` 与 tooltip 标签对（见 `data/db_compare/html_site_extract.py`，先例 ruchu8 站）。**显示口径必查**：站点常把 持久=DuraMax÷1000、商店价=出售价×2、技能书阅读等级混进 NeedLevel——对照前先换算/抽验 3~5 个已知物品。若站点自称标准版本（如"176"），用 REDM2 三方对照验证它是不是干净原版镜像（本站=REDM2 100% 即可当基线用）。 |

注意：同名 `.db` 不一定含全部表（GEEM2.db 只有 StdItems），以内容最全的文件为准。

pypxlib 的两个坑（`mir2_db_export.py` 已内置修复，仍需注意）：

1. **不支持中文路径**：`Table()` 按 ascii 编码路径，源文件在含中文的目录（如 `传奇/`）时先 `cp` 到 `data/db_compare/<tag>_src/` 这类纯 ASCII 路径。
2. **字段值固定 strict 解码**：`px_decode_errors` 只作用于字段名；脚本里的 `patch_alpha_decode()` 已包一层容错。若仍见 `'gbk' codec` 报错，先确认跑的是修过的脚本版本。

### 第 2 步：跑通用对比

```bash
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/compare_mir2_db.py \
    --ours gamedata/mud2/DB --theirs data/db_compare/<tag> --label <TAG> \
    --out data/db_compare/<tag>_raw.md
```

字段名对不上时扩展 `compare_mir2_db.py` 的 `FIELD_ALIAS`（本项目 `Level` vs 对方 `Lvl`、`WalkSpeed` vs `WALK_SPD` 这类）。

### 第 3 步：深挖（写一次性脚本，输出落 data/db_compare/）

按先例值得算的指标：

- **同源判定**：Level/HP 一致率（两轮先例都是 HP ~65% 一致 → 同源）；共有技能 EffectType/Spell/Power/MaxPower/Job/Delay 是否零差异。
- **Exp 倍数分布**：`ours/theirs` 的 median 与区间直方图（用 `fractions.Fraction` 精确分桶，经验值看是否整倍数被批量调过）。
- **StdItems 拆分字段对照**：本项目是语义化列（`AcMin/AcMax/…`），对方多半是泛用槽（`Ac/Ac2/Dc/Dc2`），逐对 (Min,Max) vs (Lo,Hi) 比；schema **不能逐列对拷**。
- **种族/AI 语义字段**：race 重映射（对方可能拿 81 当占位）、CoolEye 可能被布尔化——逐值列出差异明细。
- **对方扩展列非零占用率**：很多引擎扩展列是空壳，全 0 就可以在报告里标注「可忽略」。
- **NPC 对比（如有）**：本地 = `Npcs.txt` + `MerChant.txt`（用 `pyserver.world_extra.load_npc_spawns` 读，别手写解析；注意 **MerChant 列序=脚本 图 x y 名、Npcs 列序=名 kind 图 x y face appr**）；对方常见是 SQL 表或 `Server.MirDB` 的 NPCInfoList。**匹配键 = 地图 + 坐标（±2 格）**，不要按名字（英文 vs 中文）或按脚本文件名（两套命名体系）。先例：Crystal 309 vs 本地 303，103 对匹配、多数 d=0。对方是中文资料站、无坐标时退而比 **MapList 每图 NPC 计数**（ruchu8 先例 82% 一致）。
- **静态站 NPC 商品树/刷怪表（ruchu8 先例，报告 §7/§8）**：`js/npcshuju.js` 每条 `{n,b,c}`，看渲染 js 定语义（本站 **b=消耗/收购、c=给予/出售**），但 c 混 quest 物品，商品集合对照前先剔噪。刷怪提取自 `Mob_*.html`（`图名（x,y）|数量|刷新时间`，title=`传奇{怪}属性_…`）。**地图中文名→本地映射链**：站点图名 → `MapInfo.txt` 名（445 条，先精确、再手工别名——兽人古墓→半兽古墓/洞穴→天然洞穴这类版本命名差——最后 difflib 且要求前两字相同，防"尸魔洞→牛魔寺一层"假匹配）。商店商品在 Market_Def 的 **`[goods]` 段（大小写混用，正则必须 re.I）**，对话式商店（屠夫）没有该段。MonGen/MapInfo/Npcs/MerChant 实测多为 **UTF-8**（与 CLAUDE.md 所说 GBK 不符），逐文件自动探测。分部对照的结论维度：种类×地图对（ruchu8 vs 本地 61%）与密度/间隔（13%/38% 一致，私服重调）分开报。
- **武器 StdMode 5/6 互换检查**：本项目口径 5=刀剑 / 6=杖锤（6 挂重击动画，`handlers_combat.py:471`）；ChineseDB 整体反过来了。有争议时用 `176-mir2-red/REDM2.db`（1.76 参照）仲裁。
- **跨引擎字段不可直比**：Spell/EffectType/Shape/RaceImg 是各引擎自己的枚举/外观空间；Delay 单位不同（ours 原版 ×10 口径 vs Crystal 毫秒）。`compare_mir2_db.py` 的 FIELD_ALIAS 已含 Crystal 血统映射（Exp→Experience、AC→MaxAC、NeedL1→Level1、L1Train→Need1、Power→PowerBase 等），但 Spell/Delay 要从结论里排除。
- **深挖脚本注意双方字段大小写**：本项目 `StdMode/DCMax/Level`，对方常见 `Stdmode/DCMAX/Lvl`——`compare_mir2_db.py` 有别名，但自己写的一次性脚本要显式映射，别共用同一个 key。

### 第 4 步：写报告 docs/DB_COMPARE_<TAG>.md

结构参照 `docs/DB_COMPARE_GEEM2.md` / `docs/DB_COMPARE_APPLEM2.md`：

1. 材料与工具（对方文件、格式、落位）
2. 对方血统判定（世代特征 + 同源指标）
3. 逐表对比（规模表 → 同源指标 → 被改过的地方，样例 ≤5 条）
4. 名称集合差异摘要（按类别归类，别整段罗列）
5. 结论：三句话——血统、改动重点、借内容时的坑
6. 复现命令块

## 报告里的数值语义参照

- 装备槽/StdMode 语义查 `pyserver/util.py` 的 `slot_for()`（5/6=武器、10/11=衣服、15=头盔、19/20/21=项链、22/23=戒指、24/26=手镯、40=肉、43=矿）。
- 与 applem2/GEEM2 两轮结论交叉引用时，只引用对方报告里**实际写了**的数字，不要替旧报告推算新口径（如 median）。
