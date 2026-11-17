---
name: mir2-resource-converter
description: 把外部美术资源（PNG 帧序列、其他私服的 WIL/WZL 图库、自绘精灵）转换为本项目可直接加载的 WIL/WIX 图库格式，并生成 HTML 动画预览验证。凡涉及「新怪物/新装备/新 NPC/新特效的美术接入」「WIL/WIX 格式问题」「精灵帧序/帧号/方向布局」「提取或替换游戏贴图」时都必须使用本 skill，即使用户没有明说「转换资源」。
---

# Mir2 WIL/WIX 资源转换

## 目标与工具

把外部美术转换成前端 `frontend/gfx/wil.mjs` 能直接解码的 **WIL/WIX 图库**（8 位索引色），用 `scripts/wil_pack.py`（纯标准库）完成打包、自校验与 HTML 预览。

**先读代码再动手**——格式细节的单一数据源是这三个文件，本文只讲结论：

| 文件 | 权威内容 |
|---|---|
| `frontend/gfx/wil.mjs` | WIL/WIX 解码器（位深判定、行序、空图规则） |
| `frontend/gfx/palette.mjs` | 256 色调色板（转换的唯一取色来源） |
| `frontend/actor/actorData.mjs` | 帧号契约：HA/MA 动作表、HUMANFRAME、appr→图库/偏移 |

## 快速工作流（最常见路径）

以「新增一只怪物」为例：

1. **准备 PNG 帧**，命名 `<动作>_<方向>_<帧号>.png`（方向 0-7，帧号从 0 起），
   动作名按怪物动作表选：`stand/walk/attack/struck/die/death`（完整清单看
   `actorData.mjs` 的 MA 表，如 MA11 是 stand(4帧)/walk(6)/attack(6)/struck(2)/die(10)/death(1)）。
2. **选外观槽位**：新怪需要 Monster.csv 里 `race`（→MA 表）与外观 `appr`。
   `appr` 决定图库与帧偏移：`n = appr // 10`，`n==0 → mon1`，`1..20 → mon(n+1)`，
   `90 → effect`；偏移 = `(appr % 10) × 360`（多数种族）。选一个**空槽位**
   （用 `info` 查目标库的空闲区间），然后 merge 进该库：
   ```bash
   PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/wil_pack.py pack <帧目录> \
     --out resources/Data/Mon21 --layout mon --race MA11 --appr 205 --merge
   ```
   `--merge` 把新帧拼进现有库、原有图字节级保真；输出 `.wil/.WIX/.manifest.json` 并自动读回校验。
3. **预览验证**（用同一解码路径读回，能发现绝大多数问题）：
   ```bash
   PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/wil_pack.py preview resources/Data/Mon21
   ```
   浏览器打开生成的 `<lib>.preview.html`：每个动作 × 8 方向一段动画，蓝色虚线框 =
   实体所在 48×32 格（左上角是锚点），帧按 px/py 偏移贴图，与游戏渲染一致。
4. **接入数据**：Monster.csv 加一行（race 选 MA 表、appr 即上面的外观号）；
   重启服务端会自动重建 `resources.json`（`pyserver/gamedata.py:357` 扫 `Data/*.WIX`）。
5. 浏览器 **Ctrl+F5** 进游戏确认（ES module 有缓存）。

新装备外观（衣服/武器）：同理，hum 库以 600 帧为一个外观槽位（`--layout hum
--appearance N`），dress = `shape*2+sex`；武器 `weapon` 字段对应 Weapon.wil 的
600 帧槽位。NPC：60 帧一外观（`--layout npc --appr N`）。

## WIL/WIX 二进制格式（实测核验，2026-11）

### WIX（索引）

```
0    44B  标题 "#INDX v1.0-WEMADE Entertainment inc."（后段是垃圾字节，忽略）
44   u32  帧数 N
48   u32×N  每帧在 .wil 里的绝对偏移；值 < 1080 视为「空图」（前端 wil.mjs:73）
```
偏移表**不必有序**（前端显式排序后用，wil.mjs:77）；写新文件建议升序。
WIX 大小写后缀混用（`.WIX/.wix`）无影响，但配对文件名必须一致（注意现存
`Deco..wix` 双点名的特例，resources.json 里 key=小写去扩展名）。

### WIL（图数据）

```
0    44B  标题 "#ILIB v1.0-WEMADE Entertainment inc."
44   u16  帧数（与 WIX 一致）
46   u16  colorcount（实测恒 0）
48   u32  256
52   u32  1024
56   256×4B 内嵌调色板（每项字节序 B,G,R,0）——★前端不读它，见下节
1080 起图数据，每帧：
     u16 w, u16 h, i16 px, i16 py, 然后 w×h 字节（8bit）或 ×2 字节（16bit）
```

**位深判定没有标志位**：由字节数推断——`下一帧偏移 − 本帧偏移 ≥ 8 + w×h×2` 则
16 位 RGB565（黑色=透明），否则 8 位索引色（wil.mjs:249）。因此：
- 写文件时帧与帧之间**不要留填充**（工具已保证）；
- 末帧用文件总大小判定（RANGE_SLACK 512B 是前端为服务端 Range 钳位留的余量）。

**行序自底向上**：文件里第一行是图片最下面一行。是否按 BMP 4 字节行对齐由
字节数反推（wil.mjs:259）；用 `wil_pack.py` 写入时**不补对齐**，最稳。

**空图两种**：WIX 偏移 <1080，或 `h==1` 的占位图（前端都跳过）。原版库大量
使用 4×1 占位帧填充跳帧位，merge 搬运会原样保留。

### px/py 锚点语义

`绘制左上角 = 实体锚点 + (px, py)`，锚点 = 实体所在格的**左上角**（原版
`Actor.pas:2089` `dx + m_nPx`，前端 `PlayScene.mjs:1293` 一致，都是**相加**）。
`px/py` 是逐帧有符号数，原版美术数据并不统一（如 Hum 帧0：60×71 px=8 py=-48）。
`wil_pack.py` 缺省锚点：角色/怪/NPC 用「水平居中、脚踩格底」= `(48−w)//2, 32−h`；
plain 用 (0,0)。要和原版精灵混排时，先用 `info --indices` 量几帧同类精灵的
px/py 再定；`--px/--py` 可全局覆盖。

## 帧号契约（精灵组织形式）——比二进制格式更重要

一帧在库里的索引 = `外观偏移 + 相对帧号 cf`，cf 由「动作段 + 方向块」决定：

```
cf = 段.start + dir × (段.frame + 段.skip) + k      （k = 0..段.frame-1）
```

即**每个动作先按方向分块（8 块），块内连续帧**；skip 是方向块之间的空隙
（常被 4×1 占位图填充）。各布局：

### 人物（hum / hair / weapon 三层，每外观 600 帧）

动作段（HA 表，`actorData.mjs:18`）：

| 动作 | start | frame | skip | ftime |
|---|---|---|---|---|
| stand | 0 | 4 | 4 | 200 |
| walk | 64 | 6 | 2 | 90 |
| run | 128 | 6 | 2 | 120 |
| hit(攻) | 200 | 6 | 2 | 85 |
| spell | 392 | 6 | 2 | 60 |
| struck | 472 | 3 | 5 | 70 |
| sitdown(挖肉) | 456 | 2 | 0 | 300 |
| die | 536 | 4 | 4 | 120 |

- 身体层：`hum 库索引 = 600 × dress + cf`，dress = 服务端 `shape*2 + sex`（无装备 = sex）。
- 头发层：`hair 库 = 600 × (hair×2 + sex) + cf`；hair 先钳到 0..2，仅 >0 才画
  （`ActorSprite.mjs:145`；hair 库共 3600 帧 = 3 发型 × 2 性别）。
- 武器层：`weapon 库 = 600 × weapon + cf`；weapon≥2 才画；武器/身体前后关系
  由 WORDER 表按 cf 决定（`ActorSprite.mjs:135`）。
- **一个新衣服外观 = 一整段 600 帧槽位**（8 动作全做；缺的动作段该处动画会空）。

### 怪物（mon1~mon23，外观槽位一般 360 帧）

- `appr → 图库/偏移`：见 `actorData.mjs` 的 `getMonImgLib`/`getMonOffset`
  （多数字种族偏移 = `(appr%10)×360`，特例表在 13/4/18/19/20/90 号种族）。
- 动作段按服务端 `race` 字段选 MA9~MA52 表（`actorData.mjs:33`）；同一图库里
  不同怪的动作布局可以不同，**帧偏移是硬约定**，改 MA 表会破坏所有用它的怪。
- 工具 `--layout mon --race MA11` 会自动按表算全部目标索引并防越界。

### NPC（npc 库，每外观 60 帧）

`偏移 = 60 × appr`（appr≤22；23→1380；≥24→1470+60×(appr−24)），动作用 MA50
（stand 4帧 / attack 10帧 / struck 1帧）。

### 其他库（索引直取，无方向块）

- `tiles`（96×64 大地砖）/`smtiles`/`objects`：索引来自 `.map` 文件解析
  （列优先、0x8000 阻挡位、大地砖仅偶数格角点且索引+1）——看
  `frontend/map/Maps.mjs` 与 `pyserver/gamedata.py` 的 map 解析，别凭猜。
- `items`：物品 `looks` 字段直接是 Items.wil 索引；`stateitem` 是装备栏 UI。
- `magic/magic2~5`：特效帧基址按技能 EffectNumber 计算（原版 GetEffectBase），
  新增特效前必须对照原版客户端调用点，不要按顺序堆帧。

## 调色板与透明的硬规则

- 全游戏共用 `palette.mjs` 里那 **1 张** 256 色调色板（8bit 图）；前端**忽略**
  WIL 内嵌调色板。所以转换必须把颜色量化到 palette.mjs 的颜色（`wil_pack.py`
  已内置，别自己另找调色板）。
- **索引 0 = 透明**（alpha=0），其余索引 alpha=255：没有逐像素半透明。
  PNG 里 alpha<128 的像素→索引0；半透明效果靠绘制时的 blend 模式（加色叠层）实现，
  美术上等于「把要发光的部分画成亮色」。
- 源图超 256 色会就近量化（可能失真）；鲜艳渐变/抗锯齿灰阶多的图先减色检查。

## 工具参考（scripts/wil_pack.py）

```
info    <lib> [--indices 0-59]                  # 概览：帧数/位深/每帧 w×h px,py 字节
dump    <lib> [--indices a-b] --out DIR         # 提取帧为 PNG（占位帧跳过）
pack    <帧目录> --out <基名> [--layout plain|hum|mon|npc]
        [--base N | --appearance N | --appr N] [--race MA11]
        [--px N] [--py N] [--merge | --force] [--no-verify]
preview <lib> [--layout ... --race ...] [--indices a-b] [--out x.html]
```
- `pack` 总是写 `<基名>.wil/.WIX/.manifest.json` 并读回校验（量化像素逐帧比对）。
- `--merge` 必须指向现有 WIL；搬运旧帧是**字节级**复制（16 位帧、对齐行都安全）。
- `preview` 优先读 manifest（pack 产物自带），否则按 `--layout` 推算；
  plain 库用 `--indices` 出网格模式。
- 帧目录命名：`<动作>_<方向>_<帧>.png`（布局模式）或任意名按文件序（plain）。

## 验证清单（收尾必做）

1. `pack` 自带 verify 全过（输出 `[verify] 读回比对通过`）。
2. `preview` HTML 在浏览器里逐动作过一遍：方向对不对、脚底/锚点对不对、
   透明边缘有没有脏色、有没有帧错位（比如 die 接到 death 上）。
3. 服务端重启重建 resources.json 后，`GET /resources/resources.json` 里能查到新库。
4. 游戏内 Ctrl+F5 硬刷新实测。
5. 改了图库（尤其 merge 进原版库）→ 跑 `python scripts/verify_resources.py resources`
   做资源体检，并按项目规则更新 HANDOFF-PYSERVER.md。

## 已知边界与坑

- **WZL/WZX（加密/新格式）解不了也不做**——服务端 `gamedata.py` 明确拒绝，
  遇到让用户找老客户端的 WIL/WIX 版本。
- 16 位 RGB565 图库（如部分 Deco）能读能 merge，但**新帧一律写 8 位**。
- Windows 控制台 GBK：脚本输出加 `PYTHONIOENCODING=utf-8`；解释器用
  `D:/Python310/python.exe -I`。
- `gamedata/Mir200/Map` 是 NTFS junction，测试产物别往 `resources/` 里塞，
  用 `data/`（gitignore）做实验目录。
- 原版美术的 px/py 各不相同，混排新美术时先量再定锚点（见上）。
- 稻草人类特殊精灵（死亡火焰叠层、骷髅尸体无方向）逻辑在 `ActorSprite.mjs:87-167`，
  替换它们的美术前先读。
