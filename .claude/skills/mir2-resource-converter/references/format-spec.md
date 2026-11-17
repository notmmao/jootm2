# WIL/WIX 图库格式与精灵帧号契约（规范全文）

> 本文件是 mir2-resource-converter skill 的自包含规范，实证来源：jootm2 仓库
> `frontend/gfx/wil.mjs`（前端解码器，2026-11-17 @ 28c1477 前后）、原版 Delphi 客户端
> `MirClient/Actor.pas`、对 58 个原版 WIL 库的逐字节探测。 skill 内的
> `scripts/wil_pack.py`、`references/palette.json`、`references/tables.json`
> 均以本文为准；在 jootm2 仓库内工作时，仓库源文件是更上位的对照基准。

## 1. 文件配对

一个图库 = 一对文件：`X.wil`（图数据）+ `X.WIX`（索引）。后缀大小写在配对时不敏感
（原版库混用 `.WIX/.wix`），配对文件名主干必须一致。Jootm2 资源清单
`resources.json` 的 key = 文件名小写去扩展名（如 `Deco..wix` 的双点特例 → key `deco`）。

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
（skill 打包为 `references/palette.json`，源自 jootm2 `frontend/gfx/palette.mjs`）。
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

- 帧与帧之间**不得留填充字节**，否则位深误判（16 位图被读成 8 位花屏）。
- 末帧没有「下一帧偏移」，用**文件总大小**判定。
- 8 位帧是否按 BMP 4 字节行对齐也由字节数反推（`像素字节数 ≠ w×h 且 = (w+pad)×h`）；
  写新文件**不补对齐**（`wil_pack.py` 如此，解码端两种都认）。

### 3.4 空图的两种形态

1. WIX 偏移 < 1080；
2. `h == 1` 的占位图（原版库大量用 4×1 占位帧填跳帧位，解码端直接跳过）。

### 3.5 px/py 锚点语义

```
绘制左上角 = 实体锚点 + (px, py)
```

- 实体锚点 = 实体所在格的**左上角**屏幕坐标。原版 Delphi `Actor.pas` 的
  `DrawChr`（`dx + m_nPx + m_nShiftX`，Actor.pas:2089 附近）与 jootm2 前端
  `PlayScene.mjs:1293`（`anchorX + layer.px`）语义一致，都是**相加**。
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
skill 内 `references/tables.json`；在 jootm2 仓库内则以后端源码
`frontend/actor/actorData.mjs` 为权威（`wil_pack.py --tables` 可实时解析）。

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
  遮挡关系由 WORDER 表按 cf 逐帧决定（jootm2 `ActorSprite.mjs` 的 `WORDER[sex, cf]`）。
- 一个新衣服外观 = 一整段 600 帧槽位（八个动作全做，缺动作段则该动画为空）。

### 5.2 怪物（mon1~mon23，多数外观槽位 360 帧）

- 外观号 appr 决定图库与偏移（原版 GetMonImg/GetOffset）：
  - 图库：`n = appr // 10`；n==0 → mon1；1..20 → mon(n+1)；n==90 → effect。
  - 偏移（`npos = appr % 10`）：多数种族 = `npos × 360`；特例：
    n=0 → `npos×280`；n=1 → `npos×230`；n=13 且 npos∈{1,2,3} → 360/440/550；
    n=4 且 npos=1 → 600；n=18 → {0:0, 1:520, 2:950}；n=19 →
    [0,370,810,1250,1630,2010,2390]；n=20 → [0,360,720,1080,1440,1800,2350,3060]；
    n=90 → [80,168,184,200]。
- 动作段按服务端 `race` 字段选 MA9~MA52 表（全表见 `references/tables.json`）；
  同一图库里不同怪的动作布局可不同，**帧偏移是硬约定**——改动作表会破坏所有
  已按它排布的怪。
- `wil_pack.py --layout mon --race MA11 --appr 205` 自动算全部目标索引并防越界。

### 5.3 NPC（npc 库，每外观 60 帧）

- 偏移：`60 × appr`（appr ≤ 22）；appr 23 → 1380；appr ≥ 24 → `1470 + 60×(appr−24)`。
- 动作段 = MA50（stand 4 帧 / attack 10 帧 / struck 1 帧）。

### 5.4 其他库（索引直取，无方向块结构）

- `tiles`（96×64 大地砖）/ `smtiles` / `objects`：索引来自 `.map` 地图文件解析
  （列优先、0x8000 阻挡位、大地砖仅存于偶数格角点且索引+1、物件索引+1）。
  地图格式以 jootm2 `frontend/map/Maps.mjs` / `MapActor.mjs` 与
  `pyserver/gamedata.py` 的实现为准，不要凭猜。
- `items`：物品 `looks` 字段即 Items.wil 索引；`stateitem` 是装备栏 UI 图。
- `magic / magic2~5`：特效帧基址按技能 EffectNumber 计算（原版 GetEffectBase），
  新增特效前必须对照原版客户端调用点，**不要按顺序堆帧**。

## 6. 帧数据之外的字节（gap）

原版 WIL 里帧与帧之间偶有未引用的死字节（合并工具留下的空隙）。解码端按
WIX 偏移跳读，不依赖连续性；merge 搬运会丢弃这些 gap（无损，因为 gap 不被任何
帧引用）。
