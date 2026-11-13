# DB 对比报告：本项目（1.76/1.80 血统）vs GEEM2（GeeM2 引擎库）

> 触发：用户提供 `D:\work\me\game\传奇\Mir2-GeeM2\数据库\GEEM2.db.sql`（含同名 `.db`，SQLite），
> 要求与本项目 `gamedata/mud2/DB/*.csv` 对一份报告。
> 原始对比材料（导出的 CSV + 中间结果）落在 `data/db_compare/geem2/`（`data/` 已 gitignore，不入库）。
> 复现命令见 §6。

## 0. 材料与工具

| 项 | 说明 |
|---|---|
| 对方材料 | `GEEM2.db.sql`（204KB，UTF-8，标准 SQLite 方言：`CREATE TABLE` + `INSERT INTO "T" VALUES …`）。同目录 `GEEM2.db` 只有 StdItems 一张表（686 行），Magic/Monster 仅存在于 .sql 里，以 .sql 为准 |
| 导出 | `scripts/mir2_db_sql_export.py`（本次新写）：把 SQL dump 灌进内存 sqlite3 → 逐表导出 UTF-8 CSV 到 `data/db_compare/geem2/` |
| 对比 | 复用 `scripts/compare_mir2_db.py`（applem2 那轮的脚本，`FIELD_ALIAS` 处理 `Level/Lvl`、`WalkSpeed/WALK_SPD` 等命名差异）；拆分字段（`AcMin/AcMax` vs `Ac/Ac2`）等深挖分析用一次性脚本 `data/db_compare/geem2_deep.py`，输出 `data/db_compare/geem2_deep_out.txt` |

三张表规模：

| 表 | 本项目 行数 | GEEM2 行数 | 共有名 | 仅本项目 | 仅GEEM2 |
|---|---|---|---|---|---|
| Monster | 273（唯一 267） | 378（唯一 378） | 141 | 126 | 237 |
| StdItems | 541（唯一 532） | 686（唯一 684） | 266 | 266 | 418 |
| Magic | 39 | 104 | 32 | 7 | 72 |

## 1. GEEM2 是什么血统

- **GeeM2 引擎的商业私服库**，比 applem2（1.85）更晚、功能更全：
  - **英雄/合击版**：72 个仅 GEEM2 的技能里 47 个是 `英雄*` 前缀（英雄火球术、英雄合击系），典型的合击版引擎数据。
  - **技能支持 15 级**：Magic 表比本项目多 `NeedL4..L15Train / MaxTrainLv / CanUpgrade / MaxUpgradeLv` 26 列。
  - **物品表 62 列**（本项目 24）：多出 `Color/OverLap/HP/MP/Light/Horse/Element1..24/Expand1..5/InsuranceCurrency/InsuranceGold` 等引擎扩展列。**但绝大多数是空列**（非零占用：仅 `Color` 全填、`HP/MP` 各 8 行、其余全 0）——schema 膨胀，数据侧没怎么用。
  - 内容是开过一段时间的**高版本商业服**：`南蛮*` 系 88 条、`古代*` 副本系 30 条、天龙/星王/雷炎神装、`(残)/(真)` 戒指系列、`精酿[1h/3h/5h]` 增益药、`双倍宝典/秘籍`、各种凭证/入场卷，还有恶搞物品（MP3随身听、上网卡、数码相机、AMD芯片）。
- **与本项目同源**（1.76→1.80 血统的再改版），证据见 §2/§3 的同源指标。

## 2. Monster（共有 141 条）

### 2.1 同源指标

| 指标 | 结果 |
|---|---|
| Level 相同 | 121/141（86%） |
| HP 相同 | **92/141（65%）** —— 与 applem2 那轮的 64% 几乎一致 |
| Exp 相同 | 仅 8/141（6%） |

HP 连非整数值（沃玛教主 2200 等）都对得上，同源结论与 applem2 轮互相印证。

### 2.2 被改过的地方

- **Exp 被双方反向调整**：我们/GEEM2 倍数 median **2.167**、区间分布：<1x 14 条、=1x 8、1~2x 30、**2~3x 74**、3~4x 10、≥4x 5。即 GEEM2 也整体下调了经验（约打 4.6 折），但没有 applem2 那么狠（applem2 轮实测 79 条恰为我们/3.000）。
- **CoolEye 语义不同**：我们用多档阈值（0/10/20/30/40/50/60/100，隐身探测等级），GEEM2 基本归一成 0/1 布尔（0 共 109、1 共 21）。35% 条目不同，**不能逐值对拷**。
- **Race 有 12 条不同**：GEEM2 大量改用 race 81 当通用/占位 AI（天狼蜘蛛 118→81、花吻蜘蛛 119→81、护卫 52→81、虹魔教主 92→81），另有定向重映射（猎鹰 81→53、粪虫 106→90、虎卫 81→100、变异骷髅0 87→104）。**借用 GEEM2 怪物数据时 race 必须人工核对**。
- 小数值（AC/MAC/DC/DCMax/Speed/Hit/WalkSpeed/AttackSpeed）差异在 8%~33%，属于私服调平衡，无系统性方向。

## 3. StdItems（共有 266 条）

### 3.1 schema 差异（同 applem2 结论：不能逐列对拷）

本项目把泛用槽语义化（`AcMin/AcMax/…` + `SpecialPwr/ItemDesc/Stock`），GEEM2 沿用泛用槽（`Ac/Ac2`、`Dc/Dc2`）+ 38 个引擎扩展列。逐字段对照（我们 Min/Max vs 对方 X/X2）：

| 属性 | 不同 | 样例（名字: 我们(Min,Max) / 对方(Lo,Hi)） |
|---|---|---|
| Ac | 24/266 (9%) | 万年雪霜: (100,0)/(150,0)；太阳水: (30,0)/(70,0)；勇士头盔: (2,6)/(0,6) |
| Mac | 17/266 (6%) | 万年雪霜: (100,0)/(250,0)；太阳水: (40,0)/(110,0)；井中月: (0,0)/(0,12) |
| Dc | 14/266 (5%) | 屠龙: (5,35)/(5,40)；修罗: (0,20)/(2,20) |
| Sc | 14/266 (5%) | 三眼手镯: (1,3)/(0,2)；泰坦戒指: (2,5)/(0,5) |
| Mc | 10/266 (4%) | 嗜魂法杖: (2,8)/(2,10) |

**装备属性几乎没动**（差异 ≤9%，且多为 ±1~2 的微调），说明 GEEM2 对基础 1.76/1.80 装备数值基本原样继承。按 StdMode 分组看十项属性合计，差异集中在武器 StdMode 5（10/26）和项链 20（7/28）；**32 本技能书（StdMode 4）一字未改**。

### 3.2 被改过的地方

- **Price 42% 不同**（111/266）：商业服通胀/回收价调整，无规律可搬。
- **药水效果加强**：万年雪霜 Ac 100→150、Mac 100→250；太阳水 30→70 / 40→110。
- NeedLevel 9%、Need 3%、DuraMax 10% 微调。
- 杂项 StdMode 被重映射：介绍信 50→46、命运之书 41→47、山洞凭证 47→46（GeeM2 引擎对这些任务杂项的归类不同）。
- GEEM2 有完整新手装备链（木剑/铁剑/青铜剑/短剑/布衣/蜡烛/金创药(小量) 全在）——applem2 缺失的那批这里都有。

## 4. Magic（共有 32 条）——同源性最强的表

- **所有战斗数值字段 32 条全部一致**：EffectType / Effect / Spell / Power / MaxPower / DefSpell / DefPower / DefMaxPower / Job / Delay **零差异**。
- 差异只在成长线：
  - NeedL1/L2/L3（学级）：GEEM2 略降（刺杀剑术 25/27/29 → 22/24/27），5/13/11 条不同；
  - L1Train/L2Train/L3Train（修炼值）：GEEM2 整体调高（基本剑术 100→400、刺杀剑术 200→500、治愈术 20→40），28/30/32 条不同——商业服把练技能拉慢。
- 仅本项目有的 7 个技能：GM 变体（天尊之兽/惊寂之光/惊寂之雷/静寂圣剑/天尊之疗/天尊之盾，源码 0 匹配的占位）+ **群体治疗术**——GEEM2 有同一个技能但叫**群体治愈术**（命名差异，不是缺）。

## 5. 名称集合差异摘要

| 类别 | 说明 |
|---|---|
| 仅 GEEM2 怪物 237 | `南蛮*` 88、`古代*` 30、`一六~五一×男女战法道` 28（倍率命名的假人/练功系）、SabukDoor/SabukW1-3（**= 本项目的沙巴克城门/四段城墙，命名不同**）、其余为高版本怪 |
| 仅本项目怪物 126 | 大量是我们自己的 `0/10` 后缀变体（变异骷髅0、祖玛卫士00…）+ GM 改动物 + 沙巴克城墙（中文命名） |
| 仅 GEEM2 物品 418 | 高版本装备/药水/凭证/恶搞物品（见 §1） |
| 仅本项目物品 266 | `GM*` 11、`New*` 39、占位壳等私服魔改（与 applem2 轮结论一致） |
| 仅 GEEM2 技能 72 | `英雄*` 47 + 1.85+ 技能（逐日剑法/寒冰掌/灭天火/噬血术/召唤月灵/分身术…）。注意 GEEM2 同时收了 `嗜血术` 和 `噬血术` 两个异写 |

## 6. 结论与复现

**三句话结论**：
1. GEEM2 与本项目同源（1.76/1.80 血统），同源证据比 applem2 更强：32 个共有技能战斗数值零差异、32 本技能书零差异、65% 怪物 HP 一致。
2. GEEM2 是更晚的商业服口径：物品/怪物池大一圈但基础装备数值基本没动；改动集中在 Exp（下调）、药水（加强）、Price（通胀）、技能修炼值（调高）、race 重映射（81 占位）。
3. 如要**借用 GEEM2 的内容**：技能和基础装备可直接对值；怪物要注意 race 重映射和 CoolEye 布尔化；Price/Exp 无搬移价值；物品 62 列 schema 只有 Color/HP/MP 真正用到，其余扩展列可忽略。

复现：

```bash
D:/Python310/python.exe -I scripts/mir2_db_sql_export.py \
    "D:/work/me/game/传奇/Mir2-GeeM2/数据库/GEEM2.db.sql" -o data/db_compare/geem2
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/compare_mir2_db.py \
    --ours gamedata/mud2/DB --theirs data/db_compare/geem2 --label GEEM2 \
    --out data/db_compare/geem2_raw.md
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I data/db_compare/geem2_deep.py \
    > data/db_compare/geem2_deep_out.txt   # 一次性深挖（Exp 倍数/拆分字段/race 明细）
```

> 注意：Windows 控制台重定向默认 GBK，输出文件乱码时按 GBK 读或加 `PYTHONIOENCODING=utf-8`。
