# Delphi 参考蓝本变更：火炬源码 vs GameOfMir（2026-11-17）

## 0. 结论（先读这段）

**从今天起，「原版对齐」的权威蓝本改为 `D:\work\me\game\传奇\火炬源码+配套服务端`。**
`D:\work\me\game\传奇\MIR2\GameOfMir` 降级为**旁证 / 新引擎特性参考**：它来自另一条更晚的分支（老 LF 系），与本项目 Java 上游所依据的源码不是同一套。两者冲突时**以火炬为准**；火炬缺失的单元才用 GameOfMir 补充，并在文档中注明「此依据来自旁支，非上游蓝本」。

原因不是主观偏好，而是三条硬证据：

1. Java 上游（main 分支，原作者「云中双月」）的 `HANDOFF.md` 明确写着服务端/客户端源码来自 `火炬源码+配套服务端/Source/M2Server`、`Source/Client`，且 `gamedata/Mir200/Map` 来自火炬服务端的 `Mir200/Map`。
2. 本项目 `gamedata/Mir200/Envir/` 与火炬 `MirServer.zip` 内 `Mir200/Envir/` 同源：抽样 `Market_Def/1Bme-0.txt`（57 行）、`MiniMap.txt`（182 行）**逐行文本完全一致**（仅编码/换行不同），MapInfo/MonGen/Market_Def 中多个脚本也同目录同名存在。
3. 项目里几处关键常量与表**逐值来自火炬**：101 级经验表 `NEEDEXPS` 101/101 项完全相同；`WORDER` 男女各 600 位、共 1200 位完全相同；`MAXLEVEL=101` 与火炬一致，而 GameOfMir 是 `MAXLEVEL=500` + 可配置倍率方案。

---

## 1. 两套源码档案

| | 火炬（新蓝本） | GameOfMir（旧蓝本，旁证） |
|---|---|---|
| 路径 | `D:\work\me\game\传奇\火炬源码+配套服务端` | `D:\work\me\game\传奇\MIR2\GameOfMir` |
| 源码根 | `Source/` | 仓库根 |
| 组成 | `Source/{Client,Common,M2Server,DBsrv200,Logsrv,LoginGate,RunGate,SelGate}` + `Component/{DelphiX,Fastnet,Zura}` + `Release/*.exe` + `MirServer.zip`（13.9 MB，1653 项运行时数据） | `Client/`、`MirClient/`、`M2Server/`、`Common/` + 大量管理工具、SDK、Demo |
|  Delphi 版本 | D7（`说明.txt`：「D7测试正常编译，D2007没试」）；客户端版本号 `VERSION_YEAR=2003, MON=4, DAY=22` | 后出的老 LF 系修改版（`说明.txt`：「在老LF以前的源码基础上进行修改」） |
| `.pas` 总数 | 99 | 376 |
| 编码 | GBK/GB18030；注释含韩文源遗留（部分显示为乱码） | GBK/GB18030，中文注释较完整 |
| 客户端单元 | `Source/Client` 21 个；DirectX 能力来自 `Component/DelphiX` | `Client` 38 个 / `MirClient` 39 个；内置 `DirectX/DX*`、`JSocket`、`MShare` 等 |
| 服务端单元 | `Source/M2Server` 28 个 | `M2Server` 61 个（多出 GM 工具、配置器、会员/IP/攻城管理、机器人等） |

---

## 2. 文件级差异（脚本实测）

复跑：`D:/Python310/python.exe scripts/compare_delphi_sources.py --json data/delphi_source_diff.json`
（同名单元按 GB18030 解码、去空白后做行级相似度；`auto-junk` 关闭。）

| 对照域 | 火炬单元数 | GameOfMir 单元数 | 同名 | 字节完全相同 | 相似度≥0.9 | 火炬独有 | GameOfMir 独有 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 客户端 | 21 | 39 | 19 | 0 | 3 | 2 | 20 |
| M2Server | 28 | 61 | 26 | 0 | 0 | 2 | 35 |
| Common | 5 | 9 | 4 | 0 | 1 | 1 | 5 |
| DBsrv200 ↔ DBServer | 25 | 30 | 19 | 0 | 0 | 6 | 11 |
| Logsrv ↔ LoginSrv/LogDataServer | 13 | 18 | 10 | 0 | 0 | 3 | 8 |
| LoginGate / RunGate / SelGate | 2/3/2 | 5/7/5 | 0 | — | — | 2/3/2 | 5/7/5 |

**没有任何一个同名 `.pas` 是字节相同的**——不是「换了个目录的同一份代码」，是两条独立演进的分支。客户端高相似度单元：`DWinCtl.pas` 0.978、`cliUtil.pas` 0.956、`hutil32.pas` 0.922；服务端最高的 `NoticeM.pas` 也只有 0.450——两套服务端基本不可互相 substitute。

- 火炬独有（关键）：客户端 `ButtonListBox.pas`、`ConfirmDlg.pas`；服务端 `ConfirmDlg.pas`、`MasSock.pas`；公共 `MfdbDef.pas`。
- GameOfMir 独有（关键）：客户端 `DirectX/DX*`、`MShare.pas`、`GShare.pas`、`JSocket.pas`、`SDK.pas`、`Wave.pas`；服务端 `GameCommand.pas`、`ConfigMerchant.pas`、`ConfigMonGen.pas`、`ObjMon3.pas`、`ObjRobot.pas`、`IP.pas`、`MD5Unit.pas`、`View*.pas` 等。

---

## 3. 关键语义差异（带源码行号）

### 3.1 命名体系不同，但核心算法常常等价

| 语义 | 火炬 | GameOfMir | 结论 |
|---|---|---|---|
| 实体基类 | `TCreature` | `TBaseObject` | 命名差异 |
| 可挖肉标记 | `BoAnimal`（`ObjMon.pas:563/575/665/677` 等 17 处） | `m_boAnimal`（`ObjBase.pas:1284`、`ObjMon.pas:650/664/752/767` 等 17 处） | 同语义、不同字段名；两边都是 17 处赋值 |
| 挖肉判定 | `ObjBase.pas:13431`：`animal.Death and not animal.BoSkeleton and animal.BoAnimal` | `ObjBase.pas:17476`：`m_boDeath and not m_boSkeleton and m_boAnimal` | 等价 |
| 每刀磨损 | `n := 5+Random(16); m := 100+Random(201)`（`ObjBase.pas:13434-13435`） | `Random(16)+5; Random(201)+100`（`ObjBase.pas:17478-17479`） | **完全一致**；GameOfMir 额外加 `dwTurnIntervalTime` 超速计数 |
| 掉落散布 | `GetDropPosition`（`ObjBase.pas:6577`）半径 1 起环形扩散 | `GetDropPosition`（`ObjBase.pas:1534`）同算法，变量名 `n24/n28/n2C` | 一致 |
| 小地图索引 | `MiniMapIndex := mapindex - 1`（`Client/ClMain.pas:6301`） | `g_nMiniMapIndex := mapindex - 1`（`Client/ClMain.pas:6049`） | **都证明 MiniMap.txt 是 1-based**，结论不受换蓝本影响 |
| 协议号 | `CM_BUTCH=1007`、`SM_BUTCH=637`、`SM_DEATH=32`、`SM_SKELETON=33`、`RC_ANIMAL=50`、`RC_MONSTER=80`、`RC_ARCHERGUARD=112` | 同值 | 全部一致，协议层无需改动 |

### 3.2 经验体系：这是两版最实质的分歧

- 火炬：`M2Server/M2Share.pas:71` 定义 `MAXLEVEL = 101`，`:72` 是**写死的 `NEEDEXPS[1..101]` 常量表**；`ObjBase.pas:3805` `GetNextLevelExp(lv)` 直接查表；`ChangeLevel` 限制 `1..40`。
- GameOfMir：`Common/Grobal2.pas:1086` `MAXLEVEL = 500`；`ObjBase.pas:19184` `GetLevelExp` 查 `g_Config.dwNeedExps[]`；`GameConfig.pas` 提供 19 种倍率方案（`s_OldLevelExp`、`s_2Mult`…`s_300Mult`）和可视化编辑器。
- 本项目 `pyserver/world.py:81` 的 `NEED_EXPS` 与火炬 `NEEDEXPS` **101 项逐值相同**（首 15 项 100/200/300/400/600/900/1200/1700/2500/6000/8000/10000/15000/30000/40000，末项 2140000000）。
- 行动：保留 101 级固定表；**不要**引入 GameOfMir 的可配置倍率/500 级设定。

### 3.3 商店价格：同源但有分叉

两版都有「脚本头 `%rate`、goods 行 = `物品名 库存 刷新小时`」：

- 火炬：`LocalDB.pas:1715-1721` 解析 `%` 行写入 `TMerchant.PriceRate`（`>=55` 才生效）；`:1889-1904` 解析 goods 三列 → `pp.Count = Str_ToInt(scount,1)`、`pp.ZenHour`、`pp.ZenTime`；`ObjNpc.pas:1941` `Result := Round(price/100*PriceRate)`，`:1938` 卖出价 `max(60, PriceRate*0.8)`。
- 火炬额外有**动态价格文件**：`Market_Prices\*.prc`（`LocalDB.pas:18/2005-2050` 的 `TPricesInfo`），以及 `Market_Saved\*.sav` 库存快照。这部分 GameOfMir 没有同名机制。
- GameOfMir：`ObjNpc.pas:821` `AddItemPrice(nIndex, Round(StdItem.Price*1.1))`，并新增 `CheckItemPrice` / `GetUserPrice`（`:319/1385/1446/1968/2067/2397/2445`），带会员、信誉度等折扣口径。
- 行动：本项目「售价 = StdItems Price × 脚本头 rate%」的口径**火炬直接支持**；GameOfMir 的会员价/1.1 系数属旁支特性，不要混进来实现。

### 3.4 只在新版出现的系统（不要误当「原版就有」）

以下符号在火炬源码中**零命中**，只在 GameOfMir 存在：`m_boAnimal`（作为字段名）、`CheckItemPrice`、`GetUserPrice`、`GetLevelExp`、`FilterShowName`、`g_nMiniMapIndex`（火炬叫 `MiniMapIndex`）、`MAXLEVEL=500` 配置方案、`GameCommand.pas`、`ObjRobot.pas` 等。

其中需要复核的既有坑位结论：

| 既有结论 | 现状 | 建议 |
|---|---|---|
| 「怪物名数字后缀由 `FilterShowName` 截断」 | 来自 GameOfMir；Java 上游 HANDOFF 反而说 `骷髅精灵10` 等是**源数据本身**如此 | 换蓝本后视为**待复核**，先查火炬 `Client/Actor.pas` 的名字显示与 gamedata 数据，不要继续按 GameOfMir 硬改 |
| 「守卫 race=112 只打红名」 | 两版 `RC_ARCHERGUARD=112` 一致；`IsProperTarget` 两版都有 | 逻辑可保留，但逐行语义应改引火炬 `Source/M2Server/ObjGuard.pas` |
| 「开门动画 10 帧 × 300ms」 | 300ms 是 GameOfMir 改过的参数（`//if ... > 230` 被注释）；火炬是 230ms | 列为待复核项；本次不改，后续用抓帧定量后决定 |
| 「经验 101 级」 | 火炬直接支持 | 保留，且现在有更权威依据 |
| 「挖肉可挖 race 白名单、磨损 50/150」 | 两版算法等价 | 保留，行号改引火炬 |

### 3.5 客户端渲染架构

- 火炬 `Source/Client` 只有 21 个单元，DirectX 封装在 `Component/DelphiX`（DelphiX + Zura UI + Fastnet）；GameOfMir 把 DirectX、JSocket、MShare/GShare 等直接编进客户端（39 单元）。
- `WORDER` 男女各 600 位表在火炬 `Source/Client/Actor.pas:348`，与 `frontend/actor/actorData.mjs:388-391` **1200/1200 位逐位相同**。
- `BoldTextOut`（y 是字顶）、`GetObjsEx` 的 blend 偏移、按行绘制地面物等结论在两版都存在，火炬对应行号：地面掉落 `Client/PlayScn.pas:1070-1093`（在按行循环内，锚点 `ix+HALFX-(w div 2)`／`iy+HALFY-(h div 2)`），闪光 `:1210-1235`（10 帧×20ms），`GetObjsEx` 调用 `:1039`。

### 3.6 已发现的实际参数冲突：开门动画帧间隔

- 火炬：`Client/IntroScn.pas:767` 是 `if GetTickCount - StartTime > 230 then Inc(CurFrame)`；`:779` 取 `WChrSel.Images[103+CurFrame-80]`（即 23+frame）；`:774` 只置 `DoFadeOut`，**无淡入**。
- GameOfMir：`MirClient/IntroScn.pas:892` 把旧值注释掉，`:894` 改成 `> 300`，注释写「开门速度」；`:901` 同时置 `g_boDoFadeIn := TRUE`。
- 本项目当前实现是 **10 帧 × 300ms、只淡出**——帧间隔来自旁支 GameOfMir，淡出行为与火炬一致。
- 这是本次换蓝本后第一条明确的数值冲突。**本次不改代码**（用户只要求对比与记录）；后续若要严格对齐火炬，应把帧间隔改回 230ms，并用 Playwright 抓帧确认，不靠肉眼。

---

## 4. 对后续工作的具体约定

1. 排障 / 对齐时，所有 Pascal 出处**默认查火炬**，命令：
   ```bash
   P="D:/Python310/python.exe"
   SK=".workbuddy/skills/jootm2-original-debug/scripts/pas_lookup.py"
   T="D:/work/me/game/传奇/火炬源码+配套服务端/Source"
   "$P" "$SK" "ServerGetButch" --root "$T" --dir M2Server --context 5
   "$P" "$SK" "MiniMapIndex"  --root "$T" --dir Client --context 3
   "$P" "$SK" "GetNextLevelExp" --root "$T" --dir M2Server --context 5
   ```
   （`pas_lookup.py` 已同步更新默认根目录与 `Source/` 布局探测。）
2. 引用 GameOfMir 时必须写明「旁支依据」，且不得用它推翻火炬里能找到的对应实现。
3. `HANDOFF.md`（main 分支）里所有 `/Volumes/未命名/fire/Source/...`、`~/Documents/火炬源码+配套服务端/...` 路径，本机统一映射到 `D:\work\me\game\传奇\火炬源码+配套服务端\Source\...`。
4. 火炬 `MirServer.zip` 是运行时数据宝库：`Mir200/Envir/{MapInfo,MonGen,MiniMap}.txt`、`Market_Def/` 284 个脚本、`Mir200/Map/*.map`、`.prc`/`.sav`；本项目 gamedata 与它同源，遇到配置语义问题优先查这里。
5. 已发现的后续可挖掘项（火炬有、本项目尚未做）：`MeatQuality` 品质卖肉（火炬 NPC 脚本「高价买入品质好的肉」）、`Market_Prices` 动态价格、`QuestDiary/Defines/StartupQuest` 任务链。是否实现另行决策，不属于本次变更。

---

## 5. 复现命令

```bash
# 文件级差异（生成 data/delphi_source_diff.json，被 .gitignore 忽略）
"D:/Python310/python.exe" scripts/compare_delphi_sources.py --json data/delphi_source_diff.json

# 关键证据（项目内已有常量/表）
grep -n "NEED_EXPS" pyserver/world.py
grep -n "export const WORDER" frontend/actor/actorData.mjs

# 上游原文（main 分支）
git show main:HANDOFF.md | grep -n "火炬源码"
git show main:docs/CLIENT_ANALYSIS.md | head -8
```

---

## 6. 未决 / 边界

- 火炬源码注释混有韩文与中文，部分 GB18030 解码后仍是乱码；**不要**根据乱码注释推断语义，只看标识符与控制流。
- 两套源码都存在 `Client` 与 `MirClient`/`Release` 等多份副本，本文表内按 `compare_delphi_sources.py` 的映射（火炬 `Client` ↔ GameOfMir `MirClient` + `Client`）统计。
- 本文只做**源码层面对比**，未逐项重跑行为回归；换蓝本后若出现具体表现差异，按 `jootm2-original-debug` skill 的五步流程单独定位。
