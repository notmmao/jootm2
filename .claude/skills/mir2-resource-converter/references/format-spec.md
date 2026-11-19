# WIL/WIX 图库格式与精灵帧号契约（规范全文）

> 本文件是 mir2-resource-converter skill 的自包含规范，实证来源：Mir2 前端解码器
> （逐字快照见 `upstream/wil.mjs`，快照时间 2026-11-17）、原版 Delphi 客户端
> （火炬 `Source/Client/Actor.pas` 等）、对 58 个原版 WIL 库的逐字节探测。 skill 内的
> `scripts/wil_pack.py`、`references/palette.json`、`references/tables.json`
> 均以本文为准。

## 1. 文件配对

一个图库 = 一对文件：`X.wil`（图数据）+ `X.WIX`（索引）。后缀大小写在配对时不敏感
（原版库混用 `.WIX/.wix`），配对文件名主干必须一致。Jootm2 资源清单
`resources.json` 的 key = **文件名按第一个点截断后小写**（实现：
`name.split(".", 1)[0].lower()`，如 `Deco..wix` 的双点特例 → key `deco`，
并非简单去掉最后一个扩展名）。

## 2. WIX 索引格式

| 偏移 | 大小 | 内容 |
|---|---|---|
| 0 | 44B | 标题 `#INDX v1.0-WEMADE Entertainment inc.`（其后至 44 字节是垃圾数据，忽略） |
| 44 | u32 | 帧数 N |
| 48 | u32×N | 每帧在 .wil 里的绝对字节偏移 |

- 偏移值 `< 1080` 视为**空图**（约定写 0；原版库有时写其他小值，语义相同）。
- 偏移表**不保证升序**——解码端显式排序后使用；写新文件建议升序。

## 3. WIL 图数据格式

### 3.1 文件头（1080 字节）

| 偏移 | 大小 | 内容 |
|---|---|---|
| 0 | 44B | 标题 `#ILIB v1.0-WEMADE Entertainment inc.` |
| 44 | u16 | 帧数（应与 WIX 一致） |
| 46 | u16 | colorcount（实测恒 0） |
| 48 | u32 | 256 |
| 52 | u32 | 1024 |
| 56 | 256×4B | 内嵌调色板，每项字节序 **B, G, R, 0**（= COLORREF 0x00RRGGBB 的小端存储） |

**关键**：前端解码器**不读** WIL 内嵌调色板，统一使用代码内硬编码的 256 色 RGBA 表
（skill 打包为 `references/palette.json`，快照源码见 `upstream/palette.mjs`）。
转换时必须把颜色量化到这张表；`wil_pack.py` 写头时会按 COLORREF 格式回写一份
内嵌调色板（仅为兼容其他第三方工具，游戏不消费）。

### 3.2 图记录（从 1080 起，紧密排列）

| 字段 | 类型 | 说明 |
|---|---|---|
| w | u16 | 宽 |
| h | u16 | 高 |
| px | i16 | 锚点偏移 X（见 §5） |
| py | i16 | 锚点偏移 Y |
| data | w×h（8bit）或 w×h×2（16bit） | 像素，**行序自底向上**（文件第一行 = 图片最下一行） |

### 3.3 位深判定（无标志位，按字节数反推）

`下一帧偏移 − 本帧偏移 ≥ 8 + w×h×2` → **16 位 RGB565**（R5 G6 B5，小端 u16；
黑色 = 透明）；否则 **8 位索引色**（每字节查调色板，索引 0 = 透明）。
推论：

- 帧与帧之间**不得留填充字节**（gap 会撑大「下一帧偏移 − 本帧偏移」）：
  8 位帧的字节差一旦达到 `8 + w×h×2`，就会被**误判成 16 位 RGB565**而花屏。
  位深只靠字节差反推、没有标志位。
- 末帧没有「下一帧偏移」，用**文件总大小**判定。
- 8 位帧是否按 BMP 4 字节行对齐也由字节数反推（`像素字节数 ≠ w×h 且 = (w+pad)×h`）；
  写新文件**不补对齐**（`wil_pack.py` 如此，解码端两种都认）。

### 3.4 空图的两种形态

1. WIX 偏移 < 1080；
2. `h == 1` 的占位图（不限宽度；原版常见 4×1，用来填跳帧位，
   解码端 `height === 1` 一律跳过并视为空帧）。

### 3.5 px/py 锚点语义

```
绘制左上角 = 实体锚点 + (px, py)
```

- 实体锚点 = 实体所在格的**左上角**屏幕坐标。
  原版 Delphi `Client/Actor.pas:1635` 的 `TActor.DrawChr` 用
  `dx + px + ShiftX, dy + py + ShiftY`（`dx/dy` 由 `Client/PlayScn.pas:1102`
  按 `Rx*UNITX, Ry*UNITY` 传入，正是格左上角）；jootm2 前端
  `PlayScene.mjs` 用 `mapActor.screenX(x)+px, screenY(y)+py`。
  两边语义一致，都是**相加**。注意 Delphi 字段名是 `px` / `ShiftX`
  （本规范旧版写成 `m_nPx/m_nShiftX` 且标错行号 2089，已修正）。
- px/py 是逐帧有符号数，原版美术数据**并不统一**（例：Hum 帧 0，60×71，px=8 py=-48）。
- 新素材缺省约定（`wil_pack.py` 默认）：角色/怪物/NPC「水平居中、脚踩格底」=
  `px = (48−w)//2, py = 32−h`；plain（物品/UI/地面）= (0,0)。
- 要与原版精灵混排时，先用 `wil_pack.py info --indices` 量几帧同类精灵的 px/py
  再决定；`--px/--py` 可全局覆盖。

## 4. 调色板与透明的硬规则

- 全部 8 位图共用**一张** 256 色调色板（`references/palette.json`）。
- 索引 0 = 全透明（alpha 0），其余 255 项 alpha=255：**没有逐像素半透明**。
- 源图转 RGBA 时 alpha<128 的像素 → 索引 0；不透明像素按最近邻（RGB 欧氏距离）
  量化到 alpha=255 的候选色。
- 半透明/发光效果靠绘制端 blend 模式（加色叠层）实现，不是靠 alpha——美术上
  「把要发光的部分画成亮色」即可。
- 16 位 RGB565 图（个别原版库）可读可 merge 搬运，但新帧一律写 8 位。

## 5. 精灵帧号契约（最重要）

一帧在库里的**绝对索引 = 外观偏移 + 相对帧号 cf**：

```
cf = 段.start + dir × (段.frame + 段.skip) + k        （k = 0..段.frame−1, dir = 0..7）
```

即每个动作先按 8 个方向分块（块大小 = frame + skip），块内连续 frame 帧；
skip 是方向块间的空隙（常被 4×1 占位帧填充）。动作段数据（HA/MA 全表）见
skill 内 `references/tables.json`（快照源码 `upstream/actorData.mjs`，
`wil_pack.py --tables` 可传 actorData.mjs 实时解析）。

### 5.1 人物（hum / hair / weapon 三层，每外观 600 帧）

HA 动作表：

| 动作 | start | frame | skip | ftime(ms) |
|---|---|---|---|---|
| stand | 0 | 4 | 4 | 200 |
| walk | 64 | 6 | 2 | 90 |
| run | 128 | 6 | 2 | 120 |
| hit（攻击） | 200 | 6 | 2 | 85 |
| spell（施法） | 392 | 6 | 2 | 60 |
| struck（受击） | 472 | 3 | 5 | 70 |
| sitdown（挖肉） | 456 | 2 | 0 | 300 |
| die | 536 | 4 | 4 | 120 |

- 身体层：`hum 库索引 = 600 × dress + cf`，dress = `shape×2 + sex`（无装备时 = sex）。
- 头发层：`hair 库索引 = 600 × (hair×2 + sex) + cf`；hair 先钳到 0..2，仅 >0 才画。
  hair 库共 3600 帧 = 3 发型 × 2 性别 × 600。
- 武器层：`weapon 库索引 = 600 × weapon + cf`；weapon≥2 才画；武器与身体的前后
  遮挡关系由 WORDER 表按 cf 逐帧决定（见 `upstream/actorData.mjs` 的 `WORDER[sex, cf]`）。
- 一个新衣服外观 = 一整段 600 帧槽位（八个动作全做，缺动作段则该动画为空）。

### 5.2 怪物（mon1~mon21 / effect，多数外观槽位 360 帧）

> ⚠️ 旧版标题写「mon1~mon23」是错的：`getMonImgLib()` 只可能返回
> `mon1`~`mon21` 或 `effect`，其余 n 值一律 fallback 到 `mon1`（jootm2
> `frontend/actor/actorData.mjs`，与上游快照一致）。不存在 mon22/mon23 的入口。

- 外观号 appr 决定图库与偏移（原版 GetMonImg/GetOffset）：
  - 图库：`n = appr // 10`；n==0 → mon1；1..20 → mon(n+1)；n==90 → effect；
    其余 n → mon1（fallback）。
  - 偏移（`npos = appr % 10`）：多数种族 = `npos × 360`；特例：
    n=0 → `npos×280`；n=1 → `npos×230`；n=5 → `npos×430`；n=6 → `npos×440`；
    n=17 → `npos×350`；n=13 且 npos∈{1,2,3} → 360/440/550；
    n=4 且 npos=1 → 600；n=18 → {0:0, 1:520, 2:950}；n=19 →
    [0,370,810,1250,1630,2010,2390]；n=20 → [0,360,720,1080,1440,1800,2350,3060]；
    n=90 → [80,168,184,200]。npos 超出上表长度时回退 0（n=90 回退 80），与
    jootm2 `frontend/actor/actorData.mjs` 的 `getMonOffset()` 一致。
- 动作段按**客户端 `raceImg`（图像种族）**经 `RACE_ACTION` 选 MA 表
  （jootm2 `frontend/actor/actorData.mjs:261`，全表见 `references/tables.json`）：
  例如 raceImg 11→MA11、raceImg 14/17/18/23→MA14、raceImg 98→MA27、99→MA26。
  现有 MA 表为 MA9~MA17、MA19~MA34（缺 MA18）+ NPC 用 MA50/51/52，
  **不是连续的 MA9~MA52**。服务端 `race` 字段只决定 AI/行为，
  **不决定动作表**；旧版规范写「按服务端 race 选表」是错的，按它接线会整只怪动画错位。
  同一图库里不同怪的动作布局可不同，**帧偏移是硬约定**——改动作表会破坏所有
  已按它排布的怪。
- `wil_pack.py --layout mon --race MA11 --appr 205` 自动算全部目标索引并防越界；
  `--race` 传的是**动作表名**（MA11），不是数字 raceImg，也不是服务端 race。

### 5.3 NPC（npc 库，每外观 60 帧）

- 偏移：`60 × appr`（appr ≤ 22）；appr 23 → 1380；appr ≥ 24 → `1470 + 60×(appr−24)`。
  `NPC_PLACEHOLDER_APPR` 里的占位 appr 先回退到 0 再算偏移（见 §7）。
- 动作段**按 appr 选三张表之一**（`npcAction(appr)`，`actorData.mjs:297`）：
  appr 23 → MA51（attack 20 帧）；appr 24/25 → MA52（stand/attack 各 4 帧，
  start 都是 30）；其余 → MA50（stand 4 帧 / attack 10 帧 / struck 1 帧）。
  旧版规范只写「MA50」会漏掉这三类特例，排帧前必须先按 appr 确认。

### 5.4 地图库（tiles / smtiles / objects：索引来自 .map 文件）

`.map` 格式：52 字节头 + 瓦片格，**列优先**（`i = x*height + y`），tile 步长有
12/14 字节两种变体（14 字节的多两字节是 tiles/smtiles 分文件号），部分图带尾块。
每格字段（小端 i16，前三个必有）：

```
bng   大地砖值：& 0x7FFF 为索引（0 = 无图，>0 时实际帧号 = 值−1）；
      0x8000 位 = 阻挡
mid   小地砖值：同上（0x8000 也参与阻挡）
obj   物件值：同上；0x8000 位 = 高墙（阻挡飞行/视线）
objFile   i8：0 → Objects.wil，n>0 → Objects(n+1).wil
bngFile   i8（仅 14 字节变体）：0 → Tiles.wil，n>0 → Tiles(n+1).wil
midFile   i8（仅 14 字节变体）：0 → SmTiles.wil，n>0 → SmTiles(n+1).wil
```

消费规则：**大地砖只在偶数格角点（x%2==0 且 y%2==0）取图**，一张 tiles 图
96×64 覆盖 2×2 格；小地砖 48×32 每格一张；物件按行遮挡叠放（多帧物件连续占
obj、obj+1…）。手工写 .map 时注意步长变体与列优先顺序，弄错会整图错位。

### 5.5 其余索引直取的库

- `items`：物品 `looks` 字段即 Items.wil 索引；`stateitem` 是装备栏 UI 图。
- `magic / magic2~5`：特效帧基址按技能 EffectNumber 计算（原版 GetEffectBase），
  新增特效前必须对照原版客户端调用点，**不要按顺序堆帧**。

## 6. 帧数据之外的字节（gap）

原版 WIL 里帧与帧之间偶有未引用的死字节（合并工具留下的空隙）。解码端按
WIX 偏移跳读，不依赖连续性；merge 搬运会丢弃这些 gap（无损，因为 gap 不被任何
帧引用）。

## 7. 精灵绘制层与特殊逻辑（替换这些精灵的美术前必读）

以下逻辑是**消费端约定**（对应上游快照 `upstream/actorData.mjs` / `upstream/wil.mjs`，
原版 Delphi 客户端 Actor.pas/AxeMon.pas 同源），排帧时必须遵守：

- **尸体静止帧**（死亡动画播完后的定格）：普通尸体 = die 段末帧
  `die.start + dir×(frame+skip) + (frame−1)`（带方向）；骷髅 = death 段 start
  （**不带方向**，骨架只有一张）。给 die/death 段排帧时别把「末帧」位置挪作他用。
- **出土（digup）与复活（alive）**：正向播放 death 段（按方向取块），播完回 stand。
  death 段因此要做成「可正向播的起身/钻地帧」，不是纯静态。
- **稻草人／死亡火焰（raceImg 18）**：`ActorSprite.mjs:190` 在 die 动画期间
  于身体之上**叠一层死亡火焰**（固定取 `mon3` 库帧 `2860 + min(rel,9)`，
  加色 blend；原版 `AxeMon.pas:810`，`DEATHFIREEFFECTBASE = 2860`）。
  「death 段与火焰帧区重合」**只对 appr=27 的稻草人成立**：其 mon3 偏移
  `7×360 = 2520`，MA14 `death.start=340` → `2520+340 = 2860`，所以旧实现把
  尸体取 death 段会画成一团火，现在尸体一律取 die 段末帧（见上文尸体条）。
  换到别的 appr 后这个「重合」不成立，但**叠火焰仍由 raceImg 18 触发**；
  两件事要分别检查。
- **NPC 占位外观**：npc 库里 appr ∈ {24,25,26,28,33,36..47,51..60} 是 4×1 占位图
  （缺资源），消费端回退到 appr=0 外观。往 npc 库补帧前先确认这些槽位是否已由
  后续版本占用。
- **人物层顺序与遮挡**：身体（hum）、头发（hair）、武器（weapon）三层；
  每帧的武器前后关系由 WORDER[sex][cf] 决定（cf 为 hum 库内 0..599 相对帧）——
  同一把武器在站立帧画在身后、挥砍帧画在身前，靠这张 600 字符的 0/1 表逐帧控制。
  weapon≥2 才画武器层，hair>0 才画头发层（建角协议发 1..5，消费端钳到 0..2）。
- **挖肉（sitdown）**：skip=0，每方向只有 2 帧（不是 8 帧网格）。
- **占位帧**：`h == 1` 帧（原版最常见 4×1）在解码端一律等于「空」，新素材
  不想做某动作时，对应区间放占位帧比留纯空洞更接近原版习惯。
- **魔法/武器特效不在 body 库里**：挥砍光效、施法特效走独立库（Magic/Effect 等），
  帧基址按技能/动作查表，body 层只画本体——排 hum/mon 帧时不要把特效画进身体帧。
