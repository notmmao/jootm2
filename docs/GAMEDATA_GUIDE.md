# gamedata 目录结构与文件作用关系指南

> **定位**：本仓库 `gamedata/` 目录的**唯一结构参考**——每个文件是什么格式、被哪段代码
> 加载、运行时被谁消费、文件之间靠什么键关联。面向 AI Agent 与人类，所有消费点都给出
> `文件:行号`，可直接跳转核验。
>
> **核验日期**：2026-11-17（基线提交 `3ad46df`；同日商人脚本清理+重命名迁移后复核，
> 见 §5.4 与 HANDOFF-PYSERVER §98）。行号会随代码演进漂移，以符号名（函数/类名）为准重新 grep。
>
> **与其他文档的分工**：
> - `HANDOFF.md`（永不编辑）：玩法数值、协议语义、特效帧号的**权威参考**（Java 对照版口径）。
> - `HANDOFF-PYSERVER.md`：Python 端强制规则 / 已知缺陷 / 每轮改动记录。
> - `docs/GAME_DATA_REFERENCE/`：**外部**实测资料（B 站视频等），不是本目录的说明。
> - 本文：`gamedata/` 目录本身的结构与关系。

---

## 0. 30 秒速览（Agent 先读这里）

1. **两大数据源**：`mud2/DB/*.csv`（物品/怪物/技能**模板**，每次启动重灌进 SQLite）
   + `Mir200/Envir/*`（地图/刷怪/NPC/掉落/任务等**世界配置**，启动时解析进内存）。
2. **改任何 gamedata 文件都要重启服务端才生效**——CSV 重灌、Envir 解析、NPC 脚本缓存
   全部发生在启动阶段（`db.py::seed_game_data`、`world.py::GameWorld.__init__`、
   `world_extra.py::init_extra`）。
3. **文本全部 UTF-8**（2026-11 批量转换，备份在 gitignored 的 `.gbk-backup/`）。
   服务端 `gamedata.py::decode_text` UTF-8 优先、失败回退 GBK，两种都能读。
   注意**对照资料仍是 GBK**：本地 Delphi 源码、外部私服包。
4. **`Mir200/Map` 是 NTFS junction → `resources/Map`，绝对不要递归删除**（CLAUDE.md 铁律 2）。
   服务端阻挡判定与前端渲染读的是**同一批 `.map` 文件**（656 张）。
5. **文件之间靠"名字"连接**（怪物名/物品名/脚本名/图号），没有外键。改名要全链路 grep，
   见 §4 连接关系。
6. **调数值优先走 `config/game_rates.json` 倍率表**（不在 gamedata 内），原始数据保持
   原版口径；修正私服魔改数据才动 MonItems/CSV 本体（阶段 a/b 之分，见 §8.4）。
7. **部分文件是"死配置"**（AdminList/GuardList/MakeItem 等），原版有用途、本项目两栈
   都没消费，见 §6；另有若干"解析了但没消费/没持久化"的已知缺口，见 §7——**不要当新 bug 重复排查**。

---

## 1. 目录总览

```
gamedata/                          ← config/server.properties 的 gamedata.root（可切树，见 §8.2）
├── Mir200/
│   ├── Envir/                     世界配置 + NPC/任务脚本（UTF-8 文本）
│   │   ├── MapInfo.txt            地图定义：图号→标题/标志 + 过门连接行      [消费]
│   │   ├── StartPoint.txt         出生点列表（Python 只取第一行）            [部分消费]
│   │   ├── MiniMap.txt            图号→小地图索引（1-based！）               [消费]
│   │   ├── MonGen.txt             刷怪表：图号 x y 怪名 范围 数量 间隔        [消费]
│   │   ├── MonItems/              掉落表 257 个文件，文件名=怪物模板名        [消费]
│   │   ├── Npcs.txt               非商人 NPC 刷新表                          [消费]
│   │   ├── MerChant.txt           商人 NPC 刷新表                            [消费]
│   │   ├── Npc_Def/               非商人 NPC 脚本 24 个（传送员/任务 NPC）    [消费]
│   │   ├── Market_Def/            商人脚本 201 个（含 [goods] 货物表；2026-11-17
│   │   │                          清理魔改 85 行+合并后，原 283）              [消费]
│   │   ├── MapQuest.txt           地图任务触发条件（9 条）                    [部分消费]
│   │   ├── MapQuest_Def/          任务脚本 9 个（Q1401.txt 等）               [消费]
│   │   ├── UnbindList.txt         卷包解包：shape→物品名                      [消费]
│   │   ├── Castle/
│   │   │   ├── Sabuk.txt          沙巴克初始配置（城门/城墙 HP、归属行会）    [消费，不回写]
│   │   │   ├── SabukW.txt         原版城堡运行时配置                          [未消费]
│   │   │   ├── AttackSabukWAll.txt 攻城行会名单（当前为空文件）               [未消费]
│   │   │   └── 沙巴克配置备份文件.txt  数据备份                               [未消费]
│   │   ├── GuildBase/             行会落盘目录（空，gitignored；Java 专用）   [Python 未消费]
│   │   ├── Startup/StartupQuest.txt  私服引擎登录脚本槽位                    [未消费]
│   │   ├── AdminList.txt          GM 名单（私服广告文本）                     [未消费]
│   │   ├── GuardList.txt          大刀卫士刷新表                              [未消费]
│   │   └── MakeItem.txt           制造配方（药粉合成）                        [未消费]
│   ├── Map@ → resources/Map       junction！656 张 .map（阻挡 + 渲染三层 tile）[双端消费]
│   └── Notice/
│       ├── Notice.txt             登录公告（健康游戏忠告）                    [消费]
│       └── LineNotice.txt         滚动跑马灯公告（当前为空）                  [未消费]
├── mud2/DB/                       模板三表（CSV，UTF-8，首行表头）
│   ├── StdItems.csv               物品模板 541 行（24 列）                    [消费]
│   ├── Monster.csv                怪物模板 273 行（22 列）                    [消费]
│   └── Magic.csv                  技能模板 33 行（19 列）                     [消费]
└── .gbk-backup/                   GBK→UTF-8 转换备份（gitignored）            [工具产物]
```

> 行数为 2026-11-17 实测（含表头/注释行）。`[消费]` 指 Python 端（pyserver）实际读取并
> 影响运行时行为；`[未消费]` 见 §6 的原版用途考证。

---

## 2. 数据流总览

```
                       ┌────────────────────────── 启动阶段 ──────────────────────────┐
 mud2/DB/*.csv ──► db.py::seed_game_data ──► SQLite 模板表(std_items/monsters/magics)
                       │  每次启动 DELETE+重灌，CSV 是唯一事实源        │
                       │                                              ▼
 Mir200/Envir/*.txt ──► gamedata.py 解析器 + world_extra.py 加载器 ──► GameWorld 内存
   MapInfo/StartPoint/MiniMap/MonGen/Notice        （world.py:1011-1047）
   MonItems/Npcs/MerChant/脚本/MapQuest/Castle/Unbind（world_extra.py::init_extra）
                       └──────────────────────────────────────────────────────────────┘
                                              │
        ┌─────────────────┬───────────────────┼────────────────────┬─────────────────┐
        ▼                 ▼                   ▼                    ▼                 ▼
   刷怪/重生          NPC 对话/商店        掉落/挖肉            过门/亮度/挖矿      登录公告
   MonGen×Monster.csv  Npcs/MerChant×脚本   MonItems×StdItems.csv  MapInfo×.map       Notice.txt
        │                 │                   │                    │
        └────────┬────────┴─────────┬─────────┘                    │
                 ▼                  ▼                              ▼
            WS JSON 消息 ──► 前端 frontend/（Canvas 2D + DOM）
                                   ▲
 resources/{Data,Map,Wav} ──► gamedata.py::generate_resources_index ──► resources.json
   （启动时扫描生成，--no-resource-index 可跳过）        前端 gfx/resource.mjs 加载，
 Mir200/Map（junction）与 resources/Map 是同一批 .map：  按 URL 取 WIL/WIX 图库与 .map
   服务端 read_map 只取阻挡/门；前端 Maps.mjs 取全部渲染 tile
```

---

## 3. 文件速查表

| 文件 | 作用 | 格式 | 加载代码 | 运行时消费点 | 状态 |
|---|---|---|---|---|---|
| `mud2/DB/StdItems.csv` | 物品模板（属性/价格/外观） | CSV 24 列 | `db.py:131 _import_csv` → `templates.py:174 item_from_csv` | 背包/装备/商店/掉落全链路 `world.item_templates` | ✅ |
| `mud2/DB/Monster.csv` | 怪物模板（种族/属性/经验） | CSV 22 列 | `db.py:134` → `templates.py:191 monster_from_csv` | 刷怪/战斗/AI `world.monster_templates` | ✅ |
| `mud2/DB/Magic.csv` | 技能模板（威力/修炼点/延迟） | CSV 19 列 | `db.py:135` → `templates.py:204 magic_from_csv` | 施法/升级 `world.magic_templates`；修炼点受 `game_rates.json magicTrain` 折算 | ✅ |
| `Envir/MapInfo.txt` | 图号→标题/标志；过门连接 | `[图号 标题] FLAG` + `src x,y -> dst x,y` | `gamedata.py:130 parse_map_info` | 标题/亮度/挖矿/禁药（`world.py:1561-1640`）；过门 `gate_at`/`change_map`；大地图入口标注 | ✅（SAFE/FIGHT 标志未消费，§7） |
| `Envir/StartPoint.txt` | 出生点列表 | `图号 x y` 每行一条 | `gamedata.py:117 parse_start_point` | **只取第一行**：新角色出生 + 默认地图 + 回城兜底（`service.py:320`、`world_extra.py:1091`） | ⚠️ 部分 |
| `Envir/MiniMap.txt` | 图号→小地图索引 | `图号 索引`（**1-based**） | `gamedata.py:164 parse_mini_map` | `mapSnapshot.miniMapIndex`（`world.py:1636`）→ 前端 `PlayScene.mjs:2025` 取 `mmap.wil[索引-1]` | ✅ |
| `Envir/MonGen.txt` | 刷怪表 | `图号 x y 怪名 范围 数量 间隔(分)` | `gamedata.py:154 parse_mon_gen` | 地图懒加载时 `spawn_monsters`（`world.py:1148`）；间隔→重生计时 | ✅ |
| `Envir/MonItems/*.txt` | 怪物掉落表 | `n/m 物品名 [数量]` 每行一条 | `world_extra.py:269 load_drop_tables` | 死亡散落 `drop_loot:655`；可挖动物挖空 `roll_butch_loot:688`；倍率在判定时乘（`drop_rates.py`） | ✅ |
| `Envir/Npcs.txt` | 非商人 NPC 刷新 | `名称 代码 地图 x y flag appr` | `world_extra.py:295 load_npc_spawns` | `_load_npcs:425` 挂到地图，脚本取自 `Npc_Def/` | ✅ |
| `Envir/MerChant.txt` | 商人 NPC 刷新（163 有效行） | `编码-名字 地图 x y 名称 数量 代码 刷新时间` | 同上（merchant=True 分支） | 同上，脚本取自 `Market_Def/` | ✅ |
| `Envir/Npc_Def/*.txt` | 非商人 NPC 脚本 | `[@label]` + `#IF/#ACT/#SAY` | `npc_script.py:237 load_script` | 对话/传送/任务 `NpcScriptEngine`（`handlers_npc.py`） | ✅ |
| `Envir/Market_Def/*.txt` | 商人脚本 + 货物表 | 脚本头 `%N +N (@buy…)` + `[goods]` 段 | 同上 | 买卖/修理价格 `npc_script.py:544 goods`（StdItems.Price×1.1×%N） | ✅ |
| `Envir/MapQuest.txt` | 地图任务触发条件 | `图号 [旗标] 值 怪名 物品名 脚本名 [GROUP]` | `gamedata.py:206 parse_map_quest` | **杀怪触发** `trigger_map_quest`（`world_extra.py:401`，击杀时 `:805` 调用） | ⚠️ 拾取触发未实现（§7） |
| `Envir/MapQuest_Def/*.txt` | 任务脚本 | 同 NPC 脚本引擎 | `world_extra.py:392 _load_map_quests` | 杀怪满足旗标条件时执行，`#SAY` 走系统消息 | ✅ |
| `Envir/UnbindList.txt` | 卷包解包映射 | `shape 物品名` | `world_extra.py:316 load_unbind_list` | stdMode=31 卷包使用（`handlers_items.py:235`），拆 6 个 | ✅ |
| `Envir/Castle/Sabuk.txt` | 沙巴克初始配置 | INI `key=value` | `world_extra.py:332 load_castle` | 攻城战（`declare_siege:1227`/城门城墙 HP）；行会回城卷落点（`:1120`） | ✅ 只读不回写（§7） |
| `Notice/Notice.txt` | 登录公告 | 纯文本每行一条 | `gamedata.py:173 parse_notice` | 登录时发 `{type:"notice",lines}`（`service.py:361`） | ✅ |
| `Mir200/Map/*.map` | 地图栅格（阻挡+渲染） | 52 字节头 + 12/14 字节 tile，列优先 | 服务端 `gamedata.py:293 read_map`；前端 `Maps.mjs:9 loadMap` | 服务端：行走/飞行阻挡、门；前端：三层 tile 渲染、静态光源 | ✅ 双端 |
| `Envir/AdminList.txt` 等 8 项 | 见 §6 | — | 无 | 无 | ❌ 未消费 |

---

## 4. 名字连接关系（改数据前必看的"外键"）

所有关联都是**字符串名字匹配**，没有数据库外键。改名/新增时沿链条 grep：

```
① 怪物链（刷怪→模板→掉落→显示名）
   MonGen.txt 怪名 ──精确匹配──► Monster.csv Name（主键，含"僵尸1/僵尸2"数字后缀变体）
   Monster.csv Name ──文件名匹配──► MonItems/<同名>.txt（尾随空格会被 strip，world_extra.py:291）
   下发客户端的显示名走 FilterShowName 去后缀（world.py:860）；掉落表按模板名查（:661）

② 物品链（掉落/商店/解包→模板）
   MonItems 物品名 ──item_template_by_name──► StdItems.csv Name
   Market_Def [goods] 物品名 ──同上──► StdItems.csv（价格=Price×1.1×%N/100）
   UnbindList 物品名 ──同上──► StdItems.csv；键 shape ↔ StdItems.Shape（stdMode=31 卷包）
   查不到模板 → 静默不掉/不卖（drop_loot:681、goods:574），不报错

③ NPC 链（刷新表→脚本）
   Npcs.txt 名称 ──► Npc_Def/<名称>-<地图>.txt（优先）或 <名称>.txt（大小写不敏感）
   MerChant.txt 脚本名列 ──► Market_Def/<脚本名>-<地图>.txt 或 <脚本名>.txt
   （2026-11-17 起脚本名 = 原编码-名字，如 1Gme-屠夫 → 1Gme-屠夫-0.txt）
   脚本内 #CALL [商人\xx.txt] @标签 ──► Envir/QuestDiary/ 子脚本（npc_script.py:295 _load_sub）
   ⚠️ 主树没有 QuestDiary 目录（176 副本树才有），主树 #CALL 静默落空

④ 任务链（触发→旗标→脚本）
   MapQuest.txt 脚本名 ──► MapQuest_Def/<脚本名>.txt
   MapQuest.txt [旗标号] ↔ NPC/任务脚本的 check/set [旗标号] ↔ Player.quest_params
   ⚠️ quest_params 不持久化（§7），重登清零

⑤ 地图链（图号贯穿一切）
   MapInfo.txt [图号 …] ──► MonGen/Npcs/MerChant/MapQuest 的图号列
                        ──► MiniMap.txt 图号 ──1-based 索引──► resources/Data/mmap.wil 帧
                        ──► Mir200/Map/<图号>.map（服务端阻挡 + 前端渲染）
   MapInfo 连接行两端图号都必须有 .map 文件，否则该门被丢弃（world.py:1024）

⑥ 技能链
   Magic.csv MagId ──► 玩家 skills 表 / 技能书 StdItems（stdMode=4，Shape=MagId）
   Magic.csv L1/L2/L3Train ──game_rates.json magicTrain 折算──► 升级所需修炼点
```

---

## 5. 分组格式详解

### 5.1 mud2/DB 模板三表（CSV）

列义权威出处：`HANDOFF.md:610`；Python 映射：`templates.py:174-208`（与 Java
`Database.mapStdItem/mapMonster/mapMagic` 逐列一致）。

**StdItems.csv（24 列）**
`Idx,Name,StdMode,Shape,Weight,AniCount,SpecialPwr,ItemDesc,Looks,DuraMax,AcMin,AcMax,MacMin,MacMax,DcMin,DcMax,McMin,McMax,ScMin,ScMax,Need,NeedLevel,Price,Stock`

- `Idx` = SQLite `std_items` 主键 = 客户端物品 index；`Looks` = `Items.wil` 帧号（前端图标）。
- **字段语义按 StdMode 重载**（原版约定，不是 bug）：
  - 药品 0/1/2：`AcMin`=HP 恢复量、`MacMin`=MP 恢复量（0 即时、1/2 缓释，
    `handlers_items.py:156-179`）。样例：`1,金创药(小量),0,…,398,1,20,0,…` →  instant HP+20。
  - 装备：AC/MAC 按部位映射成准确/幸运/攻速/魔躲等（`HANDOFF.md:500`，
    `ApplyItemParameters` 对照）；DC/MC/SC 所有部位都累加。
  - stdMode=3 卷轴/油按 `Shape` 分流：1/2/3/5 传送卷、4 祝福油、9 修复油、10 战神油、
    12 增益药水（`handlers_items.py:129-145`）。
  - 25=毒药粉（Shape 1 灰/2 黄）、31=卷包（配 UnbindList）、4=技能书、40=肉。
- `Price` 是**原价**；商店实际售价 = `round(Price×1.1)×%N/100`（见 5.4）。
- CSV 只有 24 列；`ItemTemplate` 的扩展字段（hpAdd/mpAdd/atkSpd 等）CSV 不供值，默认 0
  （仅 CSV 缺失时的内置兜底模板用到，`db.py:166`）。

**Monster.csv（22 列）**
`Name,Race,RaceImg,Appr,Level,Undead,CoolEye,Exp,HP,MP,AC,MAC,DC,DCMax,MC,SC,Speed,Hit,WalkSpeed,WalkStep,WalkWait,AttackSpeed`

- `Name` 是主键；同名怪用数字后缀区分变体（僵尸/僵尸1/僵尸2…），MonGen 用带后缀名索引，
  下发显示名去后缀（`world.py:166,860`）。
- `Race` 决定 AI/行为类（原版 ObjMon 各构造分支）；`Appr` = `Mon*.wil` 外观帧；
  `RaceImg` 决定图库编号。可挖肉动物 = race 51/52/53/82/84/85/90 + appr 白名单
  （`world_extra.py:728-759`）。
- ⚠️ `WalkStep`/`WalkWait` 两列**未被映射**（`monster_from_csv` 跳过 r[19]/r[20]）。
- `Exp` 受 `game_rates.json exp` 倍率；`HP/DC/…` 是战斗基础值。

**Magic.csv（19 列）**
`MagId,MagName,EffectType,Effect,Spell,Power,MaxPower,DefSpell,DefPower,DefMaxPower,Job,NeedL1,L1Train,NeedL2,L2Train,NeedL3,L3Train,Delay,Descr`

- 除 `Descr` 外全部按数字存进 `attrs`，`MagicTemplate.resolve()`（`templates.py:148`）
  按位取：needLevel=[NeedL1,L2,L3]、maxTrain=[L1Train,…]、**Delay×10**（毫秒）。
- `Effect` = `MagIcon.wil` 技能图标帧（前端图标 = Effect×2，`gen_website.py` 同口径）。
- ⚠️ GM 变体与正式技能 MagId 重复：`INSERT OR IGNORE` 保留**先出现**的正式技能
  （`db.py:154`）。改技能数值要确认改的是正式行。

**重灌机制**：`seed_game_data`（`db.py:118`）每次启动 DELETE 三表再导入——CSV 是唯一
事实源，改 CSV → 重启即生效，不存在"旧库压住新值"。CSV 缺失才落内置兜底模板。
玩家数据（accounts/characters）不受影响。

### 5.2 地图与移动

**MapInfo.txt**（3566 行）两种行：

```
[0102	肉店]	SAFE            ← 定义行：图号 标题 + 尾部标志（大写 token 集合）
0          346,186              ->         0122       11,41     ← 连接行：过门
```

- 标志解析自原版 `LocalDB.pas:570-790`（TMapFlag）。**已消费**：`MINE/MINE2`（挖矿，
  `world.py:1575`）、`DARK/DAY`（亮度/地下判定，`:1561-1609`）、`NODRUG`（禁药守卫，
  `handlers_items.py:116`，当前数据集无此图）。**未消费**：`SAFE/FIGHT` 等（§7）。
- 连接行 → `gates_by_src`；两端图号都必须有 `.map`（`world.py:1024`）。踩门换图
  `gate_at:1275`；大地图入口只标目标图 max(w,h)≥100 的洞穴/野外（`cave_maps:1034`）。
- 标题还参与地下判定的兜底：含"洞/矿/穴/墓/窟"视为地下（`world.py:1573`，配置层近似）。

**StartPoint.txt**：`图号 x y` 列表（文件注释"安全区/复活点"）。原版把**整表**载入
`g_StartPointList`（`LocalDB.pas:1586+`）；Python `parse_start_point` **只返回第一行**
（`gamedata.py:117`），用作新角色出生点、默认地图、回城兜底。其余行当前无消费（§7）。

**MiniMap.txt**：`图号 索引`，**索引 1-based**——前端取 `mmap.wil` 帧要 `-1`
（`PlayScene.mjs:2019-2026`，原版 `ClMain.pas SM_READMINIMAP_OK` 同口径）。
未登记的图下发 -1，前端用地图 tile 临时生成 1 像素/格兜底小地图。

**.map 文件**（`Mir200/Map/`，junction → `resources/Map/`，656 张）：
52 字节头（前 4 字节 = 宽/高 uint16）+ tile 栅格，**列优先 `i = x*height + y`**。
tile 步进 12/14 字节自适应（`map_tile_stride`，`gamedata.py:255`），带尾块的活动模板图
按 12 读前 w×h 个。**前后端同一套推断**（前端 `Maps.mjs:30-36`）。
`0x8000` 位：bng/mid=普通阻挡、obj=高墙（挡飞行/视线）；doorIndex 带门编号。
服务端只取阻挡/门（`read_map`），前端取全部渲染字段（三层 tile 索引/动画/blend/静态光源）。

### 5.3 刷怪与掉落

**MonGen.txt**（3379 行）：

```
;代码  x     y     名称          范围  数量  时间
0137  21    11    练功师          0     1     10
```

- 7 列：图号 x y 怪名 范围 数量 间隔(分钟，可缺省=0)。解析 `parse_mon_gen`
  （`gamedata.py:154`）；地图**首次加载时**刷怪（`get_or_load_map:1263` →
  `spawn_monsters:1148`），间隔>0 → 死亡后重生计时。
- 怪名必须精确命中 Monster.csv 模板（含后缀变体），查不到静默不刷。
- NPC 脚本 `MonGen` 命令走另一条路：`regen_monster_by_name`（`world.py:1183`，环形找空格）。

**MonItems/<怪物名>.txt**（257 个文件）：

```
1/1                鸡肉          ← n/m 概率 + 物品名
1/3                金币    200   ← 金币带数量参数
```

- 判定 `random < n/m × 倍率`（`drop_rates.py:94 hit`；原版语义见 `HANDOFF.md:80`）。
- 金币数量 = `count/2 + random(count)`（0.5~1.5 倍 count）再乘 goldAmount 倍率；
  物品掉 1 个，耐久随机 20~100%（`world_extra.py:674-685`）。
- **文件名 = 怪物模板名**；原版数据有「洞蛆1 .txt」这类尾随空格文件名，加载时 strip
  （`world_extra.py:289-291`）。`0` 后缀变体（白野猪0/1/2）是独立掉落表。
- 可挖肉动物死亡**不散落**，表留在"尸体背包"，挖空瞬间按同一张表掷骰
  （`roll_butch_loot:688`，原版 `TakeBagItems`）。
- 倍率不改数据本体：`config/game_rates.json` 在判定时相乘（§8.4）。

### 5.4 NPC 与商店

**Npcs.txt**（非商人）：`名称 代码 地图 x y flag appr`
样例：`比奇国王 1 0122 29 32 0 8`。原版列义（`LocalDB.pas:1436-1475`）：代码
0=TMerchant/1=TGuildOfficial/2=TCastleOfficial，第 6 列 `m_nFlag`（文件注释写"范围"，
pyserver 存为 face），第 7 列外观 appr。pyserver 映射见 `load_npc_spawns:309-312`。

**MerChant.txt**（商人，163 有效行）：`编码-名字 地图 x y 名称 数量 代码 刷新时间`
样例：`1Bme-屠夫 0102 9 7 屠夫 0 11 0`。pyserver 取 p[0] 脚本名 / p[4] 名称 / p[6] appr；
"数量/刷新时间"列未消费（`load_npc_spawns:305-308`）。**脚本名 = 原编码-名字**
（2026-11-17 迁移，如 `1Gme-屠夫`），第一列即脚本基名，按中文名可直接搜索。

**脚本文件命名**：`<脚本名>-<地图>.txt` 优先于 `<脚本名>.txt`，大小写不敏感
（`load_script`，`npc_script.py:237`）。Market_Def 现行为 `<编码>-<名字>-<地图>.txt`
（迁移前是 `<编码>-<地图>.txt`，加载器规则未变、零代码改动）。同一脚本可被多个刷新点
共享（script_cache 按 `M/N:脚本名:地图` 缓存，`world_extra.py:434`）。

**2026-11-17 迁移（HANDOFF-PYSERVER §98）**：① 删除魔改 NPC 85 行——赌场（9Ega 系，
B011 图名即"赌场"）、封魔谷 kl* 测验问答、白日门 9Wae*/9Wtt 道馆（任务物品破关珍剑
不在 StdItems，纯装饰死内容）；② 同名同图多类型合并到有商品模板：屠夫@0→1Gme、
屠夫@3→1Ame、铁匠铺老板@0→2Twe（老人@1 两份均无商品，不合并）；③ 其余 159 个文件
按新格式重命名。孤儿脚本（43 个，含赌场残留）未动；Npc_Def 传送员脚本的
`<比奇赌场/@dcmove>` 按钮仍在（指向已无 NPC 的赌场图），待用户决策。

**脚本引擎**（`npc_script.py`，直译 Java NpcScript/NpcScriptEngine）：
- `[@label]` 分段；`#IF`（条件）`#ACT`（动作）`#SAY`（文本）`#ELSEACT`/`#ELSESAY` 状态机；
  `<按钮文字/@label>` 是客户端选项；`\` 续行。
- 常用条件：`check [旗标] 值`、`random N`、`checkpkpoint`、等级/金币/物品检查；
  常用动作：`give/take`、`mapmove`、`set/reset [旗标]`、`goto`、`break`、`MonGen`。
- `#CALL [商人\肉店.txt] @肉店`：Gee/流通引擎方言（原版 Delphi 无此指令），子脚本在
  `Envir/QuestDiary/` 下找（`_load_sub:295`）。**主树无 QuestDiary**，176 副本树有。

**Market_Def 脚本头与价格**（`npc_script.py:87-95,144-163,544-583`）：

```
%100        ← 价格倍率 m_nPriceRate：只有 N≥55 才接受，钳制 1..1000（LocalDB.pas:3251）
+40         ← 可售 StdMode 分类（CheckItemType 过滤）
+1
(@buy @sell @repair @s_repair)   ← 商人能力声明（LocalDB.pas:3240）
[goods]
木剑 10 0   ← 物品名 库存数量 刷新时间（第二列不是单价！三列缺一不可）
```

买价链路（Delphi 逐字核对，`ObjNpc.pas:798-823/999-1024/1385-1412`）：
**售价 = round(StdItems.Price × 1.1) × %N / 100**。1.76 实测木剑 55 = 50×1.1。
卖价/修理价另走 `GetItemPrice` 记录，详见 HANDOFF 商店章节。

### 5.5 任务

**MapQuest.txt**（9 条）：

```
12      [401]    1      幻影蜘蛛         *               Q1401
;图号   [旗标]   值     杀怪名          拾取物品名       脚本名   [GROUP]
```

- `*`/空 = 该触发条件不用；旗标号可达 400+，对应 `Player.quest_params`。
- **杀怪触发已实现**：击杀时 `trigger_map_quest`（`world_extra.py:401,805`）匹配
  图号+怪名+旗标当前值，执行 `MapQuest_Def/Q1401.txt` 的 `@main`，`#SAY` 发系统消息。
- **拾取触发（物品名列）已解析未实现**（§7）。
- 任务脚本与 NPC 脚本同引擎；旗标由 NPC 脚本 `set [401] 1` 置位、`check [401] 1` 检查。
- ⚠️ `quest_params` **不持久化**（§7）——重登后旗标清零，任务线无法跨会话推进。

### 5.6 沙巴克与杂项

**Castle/Sabuk.txt**：INI 风格 `key=value`（加载器忽略 `[段名]`，全局扫 k=v）。
已消费键：`CastleName/OwnGuild/CastleMap/MainDoorX/MainDoorY/MainDoorOpen/MainDoorHP/
LeftWallHP/CenterWallHP/RightWallHP`（`load_castle`，`world_extra.py:332`）。
攻城战运行时改内存（`declare_siege:1227`、城门/城墙 HP），**不回写文件**，重启重置（§7）。
行会回城卷（shape 5）落点 = 城堡图 (644,290) 附近，仅占领行会成员（`:1120`）。

**UnbindList.txt**：`shape 物品名`（也兼容 `shape=物品名`）。stdMode=31 卷包使用时按
shape 查名拆 6 个（`handlers_items.py:235`、`world_extra.py:1063`）。
样例：`102 金创药(小量)`、`108 回城卷`。

### 5.7 公告

**Notice/Notice.txt**：登录公告，每行一条（空行剔除），登录时整包下发
`{type:"notice",lines}`（`service.py:361`）。当前内容是健康游戏忠告。
**LineNotice.txt**（跑马灯）未消费，见 §6。

---

## 6. 未消费文件清单（原版用途考证）

以下文件在 **pyserver 与 Java 对照版中都没有加载代码**（2026-11-17 全仓 grep 核验）。
保留它们是为了数据完整性与将来补齐功能时对照，**不要当 bug 排查"为什么没生效"**：

| 文件 | 原版 Delphi 用途（本地源码行号） | 备注 |
|---|---|---|
| `Envir/AdminList.txt` | GM 名单（`LocalDB.pas:96,164`） | 本项目 GM 走 `config.py:16 GM_ACCOUNTS` 硬编码；文件内容已是私服广告文本 |
| `Envir/GuardList.txt` | 大刀卫士刷新表（`LocalDB.pas:216`） | 格式 `名称 区域 x,y : dir`；本项目守卫 AI 已实现但刷新不经此表 |
| `Envir/MakeItem.txt` | 制造配方（`LocalDB.pas:414`），药粉合成材料表 | `[物品名]` 段 + `材料 数量` 行；原版另有 Disable/EnableMakeItem.txt（M2Share.pas:4129+） |
| `Notice/LineNotice.txt` | 滚动跑马灯公告（`svMain.pas:717`、`ObjBase.pas:14225`） | 当前为空文件 |
| `Castle/SabukW.txt` | 原版城堡运行时配置（`Castle.pas:333,437`，TUserCastle 按城堡子目录读） | pyserver 只读 Sabuk.txt |
| `Castle/AttackSabukWAll.txt` | 攻城行会名单（`Castle.pas:540-604 Load/SaveAttackSabukWall`，原版文件名 AttackSabukWall.txt） | 当前为空文件 |
| `Castle/沙巴克配置备份文件.txt` | 数据备份（Sabuk.txt 头注释指引损坏时用它恢复） | 纯备份 |
| `Envir/Startup/StartupQuest.txt` | 私服引擎登录脚本槽位（文件注释"这里是登录脚本"）；**原版 Delphi 无引用** | 两栈均未消费 |
| `Envir/GuildBase/` | Java 版行会落盘目录（`GameWorld.java:363-426`） | 主树为空且 gitignored；**Python 行会另存 `data/pyserver_guilds/*.json`**（见 §7） |

---

## 7. 已知缺口（解析了但没消费 / 没持久化）

**现状记录，不是本轮待办**。与 CLAUDE.md 铁律 5「写了但没人消费」同类，修复前先在
HANDOFF-PYSERVER 立节、找原版依据：

1. **任务旗标不持久化**：`Player.quest_params`（`world.py:355`）不在 `persist()` 的
   存档字段里（`service.py:742-764`，characters 表无 quest 列）→ 重登清零，
   MapQuest/NPC 任务线无法跨会话推进。
2. **MapQuest 拾取触发未实现**：`item_name` 列已解析（`gamedata.py:112`），但只有
   杀怪触发调用点（`world_extra.py:805`），拾取路径无消费。
3. **行会落盘无人读回**：`_save_guild` 写 `data/pyserver_guilds/<名>.json`
   （`world_extra.py:1211`），但启动时 `self.guilds = {}`（`:375`），**没有加载代码**
   → 行会实际是进程内存态，重启即散。
4. **沙巴克状态不回写**：攻城/城门 HP 只改内存，重启按 Sabuk.txt 重置。
5. **StartPoint 只取第一行**：原版是每图安全区/复活点列表（`g_StartPointList`），
   Python 其余行无消费；回城/复活兜底都指向第一行。
6. **SAFE/FIGHT 地图标志无消费**：已解析进 `map_flags`，但 PvP/行走逻辑没有安全区
   判定（与 HANDOFF-PYSERVER §13.3「PvP 无独立命中判定」同属战斗保真缺口）。
7. **Monster.csv WalkStep/WalkWait 列未映射**（`templates.py:191-201`）。
8. **主树无 QuestDiary**：Market_Def 的 `#CALL` 子脚本静默落空（176 副本树才有该目录）；
   主树商人货物表都在脚本自身 `[goods]` 段，不受影响。

---

## 8. 编码、多树与周边工具

### 8.1 编码

- gamedata 文本已全量 UTF-8（2026-11 转换，592 文件零 GBK 残留）；转换备份在
  `gamedata/.gbk-backup/<时间戳>/`（gitignored）。
- 服务端 `decode_text`（`gamedata.py:25`）：BOM/严格 UTF-8 优先 → UTF-16 BOM → GBK
  回退。**新写文件直接用 UTF-8**；从外部私服包（GBK）拷入的文件也能读，但建议跑
  `scripts/convert_gbk_to_utf8.py --apply` 统一。
- CSV 读取固定 `utf-8-sig`（`templates.py:217`），**CSV 必须 UTF-8**。

### 8.2 多数据树（switch_data.py）

```
gamedata/                  主树（git 跟踪，当前私服数据，切换时永不改动）
data/gamedata/176/         1.76 副本树（入库分发；含 QuestDiary；Map junction 被
                           gitignore 排除，use 时自动补建；脚本列含反斜杠子目录，
                           只能本机 Windows 用）
config/server.properties   生效指针：gamedata.root= / db.file= / website.root=
```

- `python scripts/switch_data.py use 176|main`：改指针三行 + 清该库模板三表 + 补
  Map junction。**切树同步换库与静态站，互不混数据**。
- `build-176`：从主树复制出 176 树（CSV 用 REDM2.DB 合并、Envir 照抄 176 包）。
- `scripts/build_176_envir.py`：用 ruchu8 站点数据（1.76 原版口径）重构 176 树的
  MonGen.txt + MonItems/（NPC 脚本保留 176 包）。
- 路径解析：`config.py:66-84`（mud2_dir/envir_dir/map_dir/notice_file 全部相对
  gamedata_root）；CLI `--gamedata` / 环境变量 `JOOTM2_GAMEDATA` 可覆盖。

### 8.3 周边工具一览

| 脚本 | 与 gamedata 的关系 |
|---|---|
| `scripts/convert_gbk_to_utf8.py` | GBK→UTF-8 批量转换（--check 可做 CI 门禁），产出 .gbk-backup |
| `scripts/switch_data.py` | 多树切换（见 8.2） |
| `scripts/build_176_envir.py` | 176 树 MonGen/MonItems 重建 |
| `scripts/gen_website.py` | 读任意 gamedata 树 → `data/website/` 静态站（挂 `/site`，即 wiki）；NPC/商品/地图页直接消费 Npcs/MerChant/Market_Def/CSV |
| `scripts/verify_resources.py` | 客户端资源体检：WIL/WIX 格式、.map 对 MapInfo.txt 的覆盖率 |
| `scripts/crystal_mirdb_export.py` | Crystal/C# 服务端 Server.MirDB → 三张 CSV，供 `compare_mir2_db.py` 对照（产物在 data/db_compare/，不进 gamedata） |
| `scripts/test_pyserver.py` | 离线回归含 StartPoint/MiniMap 解析断言（:97-103） |

### 8.4 倍率表与数据本体的分工（阶段 a/b）

`config/game_rates.json`（加载 `drop_rates.py:114`，旧名 drop_rates.json 兼容回退）：

- **阶段 a（修数据）**：私服魔改失真的数值 → 直接改 MonItems/CSV 本体，恢复原版口径。
- **阶段 b（调节奏）**：整体经验/掉落/修炼速度 → 改倍率表（exp/gold/item/goldAmount/
  magicTrain + monsters/items 按名覆盖），**不动数据文件**。
- 倍率在判定时相乘、概率封顶 100%；文件缺失/非法一律按 1.0，不影响启动。
- 数值改动遵守用户约定：**改完等用户确认再 commit**（掉落/经验/金币/商店类）。

---

## 9. 维护规则清单

- [ ] 改任何 gamedata 文件后**重启服务端**才生效（含脚本——script_cache 启动时构建）。
- [ ] 新增/改名怪物、物品、NPC、脚本前，沿 §4 连接链 grep 全部消费点。
- [ ] 新增服务端字段/配置时 grep 消费点，避免再造「写了但没人消费」（铁律 5）。
- [ ] CSV 保持 UTF-8 + 首行表头；Envir 文本用 UTF-8；不要手工改 .gbk-backup。
- [ ] **绝不递归删除 `Mir200/Map`**（junction 穿透会删掉 resources/Map 真实地图）。
- [ ] MiniMap 索引 1-based；MonItems 文件名注意尾随空格；Magic.csv 注意 GM 变体重号。
- [ ] 玩法行为改动先找 Delphi 原版依据（`D:\work\me\game\传奇\MIR2\GameOfMir`，GBK），
      把 `.pas` 行号写进 HANDOFF；找不到不硬改（铁律 3）。
- [ ] 改动收尾：跑回归 → HANDOFF-PYSERVER.md 追加一节 → 按分类标签提交（本文档类用 `[文档]`）。
