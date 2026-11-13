# DB 对比报告：本项目（1.76/1.80 血统）vs CrystalDB（mir2ServerRelease / C# Crystal 引擎）

> 触发：用户指到 `D:\work\me\game\传奇\mir2ServerRelease`（C# Legend of Mir 服务端发行包，找不到数据库文件）。
> 库文件即根目录的 **`Server.MirDB`**（452KB，.NET BinaryWriter 顺序序列化，**不是** Paradox/SQLite）；
> `Server.MirADB` 是账号/角色库，与本次对比无关。原始材料落 `data/db_compare/cjlaaa*/`（gitignore，不入库）。复现命令见 §7。

## 0. 材料与工具

| 项 | 说明 |
|---|---|
| 对方材料 | `mir2ServerRelease/Server.MirDB`，版本头 **105**（C# Crystal 引擎，github.com/cjlaaa/mir2 发行包，2022-07 构建） |
| 解析 | `scripts/crystal_mirdb_export.py`（**本次新写**，零依赖）：按 suprcode/crystal v105 commit `485e59afab` 的各模型 `Load(BinaryReader)` 逐字段镜像，导出 StdItems/Monster/Magic/Npcs/Maps 五张 CSV（NPC 的 MapIndex 已解析成地图文件名）。**正确性验证 = 整个文件恰好消费到 EOF**（452106 字节分毫不差） |
| 对比 | `scripts/compare_mir2_db.py`（`FIELD_ALIAS` 本轮扩展了 Crystal 血统映射：`AC→MaxAC`、`DC→MinDC`、`Speed→Agility`、`WalkSpeed→MoveSpeed`、`Exp→Experience`、`NeedLevel→RequiredAmount`、`NeedL1→Level1`、`L1Train→Need1`、`Power→PowerBase` 等） |
| 参照 | `176-mir2-red/REDM2.db`（1.76 参照，上轮发现）用于仲裁学习级/修炼值谁更接近原版 |

三张表规模：

| 表 | 本项目 | CrystalDB | 共有名 | 仅本项目 | 仅CrystalDB |
|---|---|---|---|---|---|
| Monster | 273（唯一 267） | 544（唯一 544） | 139 | 128 | 405 |
| StdItems | 541（唯一 532） | 1349（唯一 1344） | 210 | 322 | 1134 |
| Magic | 39 | 107 | 33 | 6 | 72 |

另有 maps 389 / NPCs 309 / Quests 157 / GameShop 82（元宝/点券商城）——Crystal 引擎的全量内容库。

## 1. 解码 Server.MirDB 的过程（留档，下次直接用脚本）

格式踩坑记录（全部已固化进 `crystal_mirdb_export.py`）：

1. `cjlaaa/mir2` GitHub master 是 v77，本地 `传奇/Crystal/` 是 v117，发行包是 **v105**——三边都不一样，必须按 v105 当时的 commit（`485e59afab`，2022-05-04，`public const int Version = 105`）镜像 Load。
2. 两处与源码字面不符、靠数据反推钉死的地方：
   - **MovementInfo 实测 31 字节**（5×int32 + 2 bool + ConquestIndex + ShowOnBigMap + Icon）；
   - **MapInfo 的 13 个标记位是「2 bool + NoReconnectMap 字符串 + 10 bool」**——把 NoReconnectMap 当 bool 漏掉会整体错位（尾巴里藏着 `D002`/`hyunwol3` 这类回城地图名才暴露）。
3. Crystal `Stats` 对象 = `count + (byte stat, int value)[]`，Stat 枚举实测 `MinAC=0 … Luck=15`（`Shared/Data/Stat.cs`）。
4. pypxlib 那套不适用；本轮纯手写二进制解析，EOF 精确消费是唯一硬验证。

## 2. CrystalDB 是什么血统

**国际版 C# Crystal 引擎的官方内容库 + 中文 1.76/1.80 底子的混合**，不是中文商业合击服：

- **引擎能力**：多职业（弓手/刺客，见独有技能 BackStep/CrippleShot/DoubleShot/VampireShot）、宠物/灵物系统（BabyDragon/BabyPig 蛋、Creature 系列道具）、攻城（ConquestInfo）、任务（157 个）、商城（82 项 + CreditToken 点券）。
- **中文底子**：与本项目共有的 139 怪 / 210 物品 / 33 技能全是中文 1.76/1.80 经典内容。
- **独有内容以英文命名**：Frozen 系（FrozenKnight/FrozenArcher…）、Tucson 系、HellKnight/HellPirate、Manectric 系、Baekdon/Cheongbo（韩系命名）、宠物蛋、EXP100% 卷轴等——官方国际服内容。
- **与 GEEM2 共享同一个中文技能包**：72 个独有技能里冰焰术/分身术/召唤月灵/寒冰掌/护身气幕/无极真气/毒云/气功波/流星火雨/深延术/火龙气焰/狮子吼/瘟疫/阴阳盾/诅咒术… 与 GEEM2 独有清单几乎逐一重合（但 CrystalDB **没有 `英雄*` 合击系**）。

## 3. 与本项目逐表对比

### 3.1 Monster（共有 139 条）

| 指标 | 结果 |
|---|---|
| HP 相同 | 80/139（58%）——同源 |
| Exp | median 我们/Crystal **2.143**（2~3x 69 条、1~2x 36、<1x 21、≥3x 9），无统一倍率 |

注意 CrystalDB ∩ GEEM2（152 条）的 Exp/HP/Level **各自 115/152、124/152、125/152 完全相同**——CrystalDB 的中文怪数值基本照搬了与 GEEM2 同源的底子，改动是零星的（半兽勇士1: crystal 1000 / geem2 720）。

### 3.2 StdItems（共有 210 条）

- **Price 65% 不同**、DuraMax 35%、Weight 18%——商业向调整（万年雪霜 4000→2500、中型盔甲 5500→10000）。
- **NeedLevel 27% 不同，且与 GEEM2 高度一致**（∩GEEM2 332 条中 256 条 NeedLevel 相同；乌木剑 1→4、井中月 28→30 两边完全一样）。
- Shape 47% 不同——Crystal 的 Shape 语义与 Delphi 版不同源（外观 id 体系不同），**不可对拷**；Looks(Image) 仅 2% 不同，可对照。
- 装备攻防（AcMin..ScMax）差异极小——1.76 底子原样继承（同 GEEM2 结论）。

### 3.3 Magic（共有 33 条）——本轮最重要的发现

| 对照（学习级/修炼值） | ours | CrystalDB | GEEM2 | REDM2(1.76 参照) |
|---|---|---|---|---|
| 刺杀剑术 NeedL | **25/27/29** | 22/24/27 | 22/24/27 | **25/27/29** |
| 半月弯刀 NeedL | **28/31/34** | 26/28/31 | 26/28/31 | **28/31/34** |
| 冰咆哮 L1-3Train | 100/400/800 | **400/800/1200** | **400/800/1200** | **400/800/1200** |
| 基本剑术 L1-3Train | 10/20/30 | 27/60/130 | 27/60/130 | 100/400/800 |
| 治愈术 L1-3Train | 10/20/30 | 15/35/70 | 15/35/70 | 50/150/300 |

1. **CrystalDB 与 GEEM2 的修炼值 7/7 抽样技能完全一致**——两者共享同一套技能数值包；ours 独树一帜（修炼值被大幅压低，加速练技能）。
2. **学习级（NeedL）：ours = REDM2 原版口径**，CrystalDB/GEEM2 是略降过的另一套。
3. **修正前轮结论**：`DB_COMPARE_GEEM2.md` §4 说「GEEM2 整体调高修炼值」——有了 REDM2/CrystalDB 参照后应理解为「ours 调低/改写了修炼曲线，GEEM2 保留的是与其同源套件一致的值」；GEEM2 报告已加勘误注。
4. **Spell/Delay 100%「不同」是假象**：Spell 两边是不同引擎的枚举空间（Delphi 魔法 id vs Crystal Spell 枚举），Delay 单位不同（ours 原版 ×10 口径，Crystal 是毫秒）——**这两列不可直接对比**，已从可比字段中排除。
5. Power/MaxPower/DefSpell/DefMaxPower 有 30~70% 差异，且 Crystal 的 PowerBonus/MPowerBonus 语义（每级加成）与原版 MaxPower（上限）不完全等价，方向性结论仅作参考。

### 3.5 NPC 数据对比（Server.MirDB 内 NPCInfoList + Envir/NPCs 脚本）

**Crystal 有 NPC 数据，两个位置**：

| 数据 | 位置 | 数量 |
|---|---|---|
| NPC 定义（位置/脚本名/地图/外观/营业时间/等级限制） | `Server.MirDB` 里的 **NPCInfoList**（二进制节，已导出 `data/db_compare/cjlaaa/Npcs.csv`，MapIndex 已换成地图 FileName） | **309** |
| NPC 脚本 | `Envir/NPCs/` 按地图分子目录（`BichonProvince/BichonWall/Butcher-0.txt`），**中文内容、与本地同一套 `[@MAIN]/#IF/#SAY/#ACT` 语法** | 326 个 txt |

本地对应：`Envir/Npcs.txt`（52 固定 NPC）+ `Envir/MerChant.txt`（251 商人）= **303**；脚本 `Market_Def/`（283）+ `Npc_Def/`（24）= 307。

**地图对应关系**（Crystal MapFileName 与本地 map 号同空间，已逐一核对标题）：

| map | 本地标题 | Crystal 脚本目录 |
|---|---|---|
| 0 | 比奇省 | BichonProvince |
| 1 | 沃玛森林 | WoomyonWoods |
| 2 | 毒蛇山谷 | SerpentValley |
| 3 | 盟重省 | MongchonProvince |
| 4 | 封魔谷 | Castle-GI |
| 5 | 月崕岛 | PrajnaIsland（苍月岛部分，Crystal 标题「苍月岛」） |
| 6 | （本地未定义） | PastBichon（远古比奇） |
| 11 | 白日门 | PrajnaIsland |

**对比结果**：同地图 + 坐标 ≤2 格匹配 **103 对**（本地 303 / Crystal 309），多数距离 **d=0**。这是三方全部对比里**最硬的同源证据**——不只是数值像，是同一张地图上同一个坐标站着同一个功能的 NPC：

| 本地（中文） | Crystal（英文） | 位置 |
|---|---|---|
| 屠夫 | Butcher / Merchant_Kim | 比奇省 0102(9,7)、0(313,271)、0(293,603) |
| 铁匠铺老板 | Blacksmith_Bill / Merchant_Smith | 0(302,219)、0(297,612) |
| 安家布衣 / 白家服装老板 | Clothing/Anne、Drapery/Whitney | 0106(19,6)、0(305,607) |
| 戒指/手镯/项链店老板 | Ring/Bracelet/Necklace Merchant | 0105 三连位 (18,6)(12,12)(6,18) |
| 仓库保管员 | InnKeeper（Warehouse） | 0(301,257)、0140(8,9) |
| 传送员 | Teleport_Gilbert / Transport_Chandler | 0(287,614)、2(499,483) |
| 比奇国王 | Administrator_Edward | 0122(29,32) |
| 书店老板 | Librarian_Steven/Brian | 0104(5,8)、0132(5,18) |

**本地独有 200（私服扩展）**：封魔谷 map4 一家占 47（本地把封魔谷做成商铺集中地，Crystal 该图只有 8 个）、传送员体系 20+（会议厅/决战场/蚁后巢穴/静寂/新洞穴等）、活动地图 NPC（T0004 地魔、T0005 相思女、T0009 惊寂、恶魔树×4）。

**Crystal 独有 206（本地缺的官方 NPC）**：连本地都有的地图上就缺——比奇省 30 个（**Master_Wa 武学大师**、HighPriest_Jude/Shinsu、HighAssassin_Cloud、Prison_Guard、Bichon_Teleport1-3、Merchant_Bull 杂货）、苍月岛 17、远古比奇 13、盟重省 11、白日门 9；另有本地没有的地图 R001(23)、HELL00 废弃之地(7)、NAMMAN2 南蛮城(7)、WhiteVillage(7)、UMM(5)。

> 注意：Crystal 比奇省 NPC 脚本（`Butcher-0.txt` 等）与本地 `1Bme-0.txt` 是同一份脚本的措辞改写版（`...我绝对不会为你这样的恶人服务...` vs `...我不想和你这样的人打交道...`），**可直接作为内容移植参考**；但脚本文件名是两套命名（Crystal 英文描述名，本地 Mir200 地图编码名），不能按文件名匹配。可执行的匹配键是 **地图 + 坐标**。

## 4. 名称集合差异摘要

| 类别 | 内容 |
|---|---|
| 仅CrystalDB 怪物 405 | 英文国际服内容（Frozen 系、Tucson 系、HellKnight 系、Manectric 系、Baekdon/Cheongbo 韩系、乌龟/猫系宠物怪、HellBomb 塔防怪）+ 少量中文（00、体迅风…） |
| 仅CrystalDB 物品 1134 | 弓/弩武器系（*Bow）、宠物蛋、EXP/属性卷轴、Cheongbo/Evil龙 系列套装、矿石/材料、点券 token |
| 仅CrystalDB 技能 72 | 弓手/刺客英文技能 + 与 GEEM2 同源的 1.85 中文技能包（无英雄合击） |
| 仅本项目 | 与前几轮一致：`0/10` 后缀变体、GM* / New* 占位、沙巴克城墙中文命名、GM 变体技能 |
| 值得注意 | 沙巴克城门/城墙在本轮**共有**（中文命名一致）；虹魔教主/石墓尸王仅本项目有（对方命名或拆分不同） |

## 5. 结论

1. CrystalDB = **C# Crystal 引擎国际服官方库**叠在中文 1.76/1.80 底子上；与本项目同源（HP 58% 一致、装备攻防几乎没动），但它的独有内容是引擎自带的国际服/宠物/弓刺体系，与中文私服生态平行。
2. **最有价值的关系**：CrystalDB ↔ GEEM2 共享同一套中文技能数值包（修炼值完全一致）和 NeedLevel 口径（256/332 相同）——三方互证后可以确定「ours 的技能修炼曲线与学习级是独立改过的两处」。
3. 借用时的坑：Shape/Spell/RaceImg 是各引擎自己的枚举/外观空间，**不可跨引擎对拷**；Delay 单位不同；Price/DuraMax 是商业口径；装备攻防与怪物 HP/Exp 可以按名对值。
4. **NPC**：Crystal 的 309 个 NPC 定义在 `Server.MirDB` 内（`Npcs.csv`），与本地 303 个按「地图+坐标」匹配 103 对、多数 d=0——同一地图同一坐标同一功能，同源证据比怪物数值更硬。本地缺的官方 NPC（Master_Wa 武学大师、HighPriest、Prison_Guard、3 个比奇传送员等 200 个）和其**中文脚本**（`Envir/NPCs/`，同一套语法）是现成可移植内容。

## 6. 工具沉淀（本轮）

| 文件 | 说明 |
|---|---|
| `scripts/crystal_mirdb_export.py` | 新增：Server.MirDB（C# Crystal v105）→ 三张 CSV，EOF 精确消费验证 |
| `scripts/compare_mir2_db.py` | FIELD_ALIAS 扩展 Crystal 血统映射（Exp→Experience、AC→MaxAC、NeedL1→Level1、L1Train→Need1、Power→PowerBase 等）；标记 Spell/Delay 这类不可跨引擎比对的字段 |
| `data/db_compare/cjlaaa_src/` | v105 commit `485e59afab` 的参照源码（gitignore，不入库） |

## 7. 复现

```bash
cp "D:/work/me/game/传奇/mir2ServerRelease/Server.MirDB" data/db_compare/
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/crystal_mirdb_export.py \
    data/db_compare/Server.MirDB -o data/db_compare/cjlaaa
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/compare_mir2_db.py \
    --ours gamedata/mud2/DB --theirs data/db_compare/cjlaaa --label CrystalDB \
    --out data/db_compare/cjlaaa_raw.md
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I data/db_compare/cjlaaa_deep.py \
    > data/db_compare/deep_raw.txt   # 输出 GBK，按 GBK 读回转 UTF-8
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I data/db_compare/npc_compare.py \
    > data/db_compare/npc_raw.txt    # NPC 坐标匹配（同样 GBK 回转）
```
