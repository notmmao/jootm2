# DB 对比报告：本项目（1.76/1.80 血统）vs ChineseDB（delphimir2 商业服库）

> 触发：用户提供 `D:\work\me\game\传奇\delphimir2\MirServer\Mud2\Chinese DB\`（Paradox `.DB` 三件套），
> 要求与本项目 `gamedata/mud2/DB/*.csv` 对一份报告。
> 原始对比材料落在 `data/db_compare/chinesedb*/`（`data/` 已 gitignore，不入库）。复现命令见 §7。

## 0. 材料与工具

| 项 | 说明 |
|---|---|
| 对方材料 | Paradox 表 `Magic.DB` / `Monster.DB` / `StdItems.DB`（无 `.MB`），来自 delphimir2 整合服务端的 `Mud2\Chinese DB` |
| 导出 | `scripts/mir2_db_export.py`（applem2 轮的工具）。本轮发现并修了两个 pypxlib 局限，见 §6 |
| 对比 | `scripts/compare_mir2_db.py` + 一次性深挖 `data/db_compare/chinesedb_deep.py`（输出 `chinesedb_deep_out.txt`） |
| 额外参照 | 探测中发现的 `D:\work\me\game\传奇\176-mir2-red\Mud2\DB\REDM2.DB`（**SQLite**，StdItems 384 / Monster 375 / Magic 33，1.76 血统）。本轮用它仲裁了武器 StdMode 5/6 之争（§3.3） |

三张表规模：

| 表 | 本项目 | ChineseDB | 共有名 | 仅本项目 | 仅ChineseDB |
|---|---|---|---|---|---|
| Monster | 273（唯一 267） | 433（唯一 432） | 126 | 141 | 306 |
| StdItems | 541（唯一 532） | 794（唯一 794） | 252 | 280 | 542 |
| Magic | 39 | 150 | 32 | 7 | 118 |

**是三轮对比里内容量最大的一套库**（怪 +60%、物品 +47%、技能近 4 倍）。

## 1. ChineseDB 是什么血统

**基于 1.76/1.80 血统的商业合击/转生服库**，证据：

- **合击版引擎数据**：仅 ChineseDB 的 118 个技能里 47 个 `英雄*` 前缀，外加成套合击技（三绝杀/破魂斩/噬魂沼泽/火龙气焰/末日审判/飓风破）和月灵系（召唤月灵 + 月灵普通魔法/月灵重击魔法）。
- **转生体系**：一转~五转证明、七级英雄卷、五转卷轴；怪物 Level 全换成 80/120/180/250/500/800/1000 这类整数（共有怪 108/126 条 Level 不同，top：500×82、800×38——原版 1~99 级的语义被整体废弃）。
- **商业服经济**：元宝、声望/声望卷、会员证书、★经验[一百万~一亿] 系列、双倍卡、千里传音卷、GM宝石/战石/道石/魔石+包、天工神锤、押镖（任务/普通/中等镖车）、宝箱、比武守护兽。
- **服务器水印**：怪物名直接嵌广告 `QQ:13677866`；`新怪物260~273` 占位壳；药名用武侠梗（一日丧命散/含笑半步颠）。
- **与本项目同源**：32 个共有技能的 EffectType/Effect/Spell/Job **零差异**；43 条怪 HP 一字不差（大老鼠 385、楔蛾 220、沃玛勇士 285、巨型多角虫1 900…）；Exp median 我们/对方 2.857，与 applem2 轮的 3 倍模式吻合。

## 2. Monster（共有 126 条）

| 指标 | 结果 |
|---|---|
| HP 相同 | 43/126（34%）——同源锚点，但比例低于 applem2/GEEM2 的 64~65% |
| Exp | median 我们/对方 **2.857**（2~5x 60 条、<1x 37、=1x 15、≥10x 8） |
| Level | median 0.400，108 条不同——对方自制等级体系 |
| DCMax | median 0.267（对方约 3.75 倍） |

被改过的地方（样例：僵尸1 我们/对方）：

- **低级怪全面强化、Boss 级反而缩水**：僵尸1 HP 155→1600、AC 0→80、MAC 0→150、DC 12→50/16→150；而石墓尸王/虹魔教主/巨型多角虫/赤月恶魔1/沃玛教主1 的 HP 只有我们的 1%（如虹魔教主 2200→约 22）。**数值没有统一倍率，是按新等级体系重排的**——不可按倍率换算借用。
- Exp 双向调：僵尸1 480→200（下调），另有 8 条上调 10 倍以上。
- Undead 14% 不同（双头血魔/双头金刚/变异骷髅0 我们=1 对方=0）；CoolEye 50% 不同（对方基本 0/1 布尔化，同 GEEM2）。
- Race 仅 5 条不同（威思而小虫/沙虫 82→81、幻影蜘蛛 116→117），**没有 GEEM2 那种大面积 race 81 占位**。
- WalkWait 4 条 1200→0（双头血魔/双头金刚/邪恶巨人——对方去掉了行走间隔）。

## 3. StdItems（共有 252 条）

### 3.1 schema 与本项目同宽（24 列）

泛用槽（`Ac/Ac2/…`）+ `Source/Reserved/Stock`，没有 GEEM2 的 38 列膨胀。扩展列占用：Stock 全填，Source 15、Reserved 7——`Stock`（库存/堆叠？）列 GEEM2 全 0、这套全填，语义需借数据时再核。

### 3.2 数值改动

- **Price 54% 不同**（136/252）：万年雪霜 4000→10000、井中月 30000→10000——商业服通胀，无搬移价值。
- **NeedLevel 24% 不同且大幅上调**：三眼手镯 22→60、传送戒指 12→130、凝霜 25→40——配合转生等级体系。
- 药水/杂项：万年雪霜 Weight 2→1；雪霜包 Shape 112→125；骰子 StdMode 45→2。
- 装备攻防（十项合计）差异很小：Dc 仅 13 条不同（匕首 4-5→3-8、无极棍 6-18→8-16、血饮 6-16→6-15）——**基础 1.76 装备数值原样继承**，同 GEEM2 结论。

### 3.3 武器 StdMode 5↔6 整体互换（20 条）——三轮发现里最重要的坑

| | StdMode 5 | StdMode 6 |
|---|---|---|
| 本项目 | 刀剑类：木剑/铁剑/井中月/屠龙/龙纹剑/无极棍/银蛇/血饮… | 杖锤斧类：魔杖/裁决之杖/骨玉权杖/嗜魂法杖/炼狱/偃月/海魂/鹤嘴锄 |
| ChineseDB | 杖类+高级武器：裁决之杖/骨玉权杖/嗜魂法杖/恶魔权杖/屠龙/龙纹剑/血饮… | 低级刀剑：乌木剑/铁剑/井中月/匕首/半月/修罗/斩马刀/无极棍/银蛇/降魔/青铜斧/鹤嘴锄 |

**仲裁**：`REDM2.DB`（1.76 参照）与本项目一致（井中月 5、裁决之杖 6、骨玉权杖 6、嗜魂法杖 6、乌木剑 5）。
**即 5=刀剑 / 6=杖锤 是原版口径，ChineseDB 自己做了互换**。本项目 StdMode 6 挂重击动画（`handlers_combat.py:471`，原版 `ObjBase.pas:8819-8847`），直接搬 ChineseDB 武器行会错重击判定，必须先改回 5/6。

## 4. Magic（共有 32 条）——同源性最强，改动集中在成长线

- EffectType / Effect / Spell / Job **零差异**；Delay 仅 1 条（冰咆哮 60→120）。
- Power/MaxPower/DefPower 各 6~7 条微调：地狱雷光 10/30→2、灵魂火符 8→40、火墙 3→2、冰咆哮 12/14→10、爆裂火焰 6→10——个别技能平衡。
- NeedL1-3 仅 2 条不同（困魔咒 28/30/32→60/65/70、爆裂火焰 22/27/31→42/45/48）。
- **L1/L2/L3Train 修炼值被压平成统一的 10/30/50**（29~31 条不同；原版是 100~800 的逐技能曲线，如基本剑术 100/400/800→10/30/50）——练技能大幅简化，商业服节奏。

## 5. 名称集合差异摘要

| 类别 | 内容 |
|---|---|
| 仅ChineseDB 怪物 306 | 卧龙山庄系（庄主/夫人/战将/道将/魔将/守将/御风/斩浪/新月）、幽冥系、暗之系（暗之双头血魔/暗之牛魔王…）、异型系、传奇之王/伏魔法神/光芒道士/圣魔法魂/战神勇士×男女、地藏魔王/九绝剑魔/太上老龟/万年树妖、僵尸6/7/8 变体、镖车/宝箱/比武守护兽/练级怪、QQ:13677866、新怪物260-273 占位 |
| 仅ChineseDB 物品 542 | 转生证明×5、★经验石×8、星珠×7、元宝/声望/会员证书、GM宝石/战石/道石/魔石+包、光芒/圣魔/伏魔/天神怒炎/孤星战月 等套装、天工神锤、镖车/宝箱/回城卷/传送符/千里传音卷、武侠梗药 |
| 仅ChineseDB 技能 118 | `英雄*`×47、合击技、月灵系、群体系（群体施毒术/群体火符术/群体雷电术…）、新技能（万剑归宗/寒冰掌/灭天火/逐日剑法/飓风破…） |
| 仅本项目 怪物 141 | 我们的 `0/10` 后缀变体 + GM 改动物（与前两轮一致）；注意 **沙巴克城门/四段城墙是共有的**（GEEM2 才有 SabukDoor/SabukW 命名分叉），但「护卫」仅本项目有 |
| 仅本项目 物品 280 | `GM*` 11、`New*` 39、占位壳、介绍信/命运之书/山洞凭证（ChineseDB 没有这批杂项） |
| 仅本项目 技能 7 | GM 变体（天尊之兽/惊寂之光/惊寂之雷/静寂圣剑…）+ **召唤神兽**（ChineseDB 用「召唤灵兽」替代，同一个技能位的改名） |

## 6. 工具修复（本轮新增，已改 `scripts/mir2_db_export.py`）

1. **pypxlib 不支持中文路径**：`Table()` 内部按 ascii 编码路径，路径含「传奇」直接炸——先把 `.DB` 拷到纯 ASCII 路径再导。
2. **pypxlib 字段值固定 strict 解码**：`px_decode_errors` 只作用于字段名；`AlphaField._deserialize` 对字段值 strict GBK，ChineseDB 的 StdItems 有一个非 GBK 字节（`回城卷??`，Idx 273）导致整表导出失败。已在脚本内加 `patch_alpha_decode()`（包一层 `AlphaField._deserialize`，失败按 `--errors` 重解码），不改库文件。

## 7. 结论与复现

**三句话结论**：
1. ChineseDB 与本项目同源（1.76/1.80 血统、技能框架零差异、Exp 3 倍模式与 applem2 一致），但是**合击/转生商业服**的深度改版：自定义等级体系（80~1000）、元宝/声望经济、押镖/比武/卧龙山庄等内容包。
2. 数值不可按倍率换算借用：怪物按新等级重排（低级怪×10、Boss÷100 双向），Exp/Price 双向调整；装备攻防和技能框架倒是原样继承。
3. 借内容时的坑：**武器 StdMode 5↔6 被整体互换**（与 1.76 参照 REDM2 和本项目口径相反），搬武器必须先改回；CoolEye 布尔化；它的「新内容」（卧龙/幽冥/转生）要配套它的等级与经济体系才有意义，单搬数值无意义。另外 `176-mir2-red/REDM2.db`（SQLite）可作 1.76 参照库留用。

复现：

```bash
# Paradox 源文件先拷到纯 ASCII 路径（pypxlib 不支持中文路径）
cp "D:/work/me/game/传奇/delphimir2/MirServer/Mud2/Chinese DB/"*.DB data/db_compare/chinesedb_src/
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/mir2_db_export.py \
    data/db_compare/chinesedb_src -o data/db_compare/chinesedb
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/compare_mir2_db.py \
    --ours gamedata/mud2/DB --theirs data/db_compare/chinesedb --label ChineseDB \
    --out data/db_compare/chinesedb_raw.md
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I data/db_compare/chinesedb_deep.py \
    > data/db_compare/deep_raw.txt   # 输出文件是 GBK，按 GBK 读回转 UTF-8
```
