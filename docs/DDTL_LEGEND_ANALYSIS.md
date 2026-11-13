# 外部仓库档案：原版 / 自有 fork / ddtl-legend 三源核验（2026-11-13）

本文记录一次对外部仓库的**只读核验**结论。所有论断均给出可复现的验证命令/文件行号，后续轮次可直接引用，不必重查。

## 1. 仓库与 remote 全景

| remote | URL | 角色 | 权限 |
|---|---|---|---|
| `origin` | `https://github.com/jootm2/jootm2` | 原版上游（远澜），`main` 基线 | 只读 |
| `fork` | `git@github.com:notmmao/jootm2.git` | 我方 fork | 可推送 |
| `gitee` | `git@gitee.com:notmmao/pymir2.git` | 我方 Gitee 仓库 | 可推送 |
| `ddtl` | `https://github.com/suyuan168/ddtl-legend` | 第三方 fork（"打到天亮"） | **无推送权限，只 fetch** |

已执行操作：`git push fork feat/python-server` 新建远端分支（当时 HEAD `cd54909`）。`ddtl` 已 fetch，分支两个：`ddtl/main` 与 `ddtl/archive/rpg-before-3v3-20261111`（后者是 3v3 事件前的自我存档）。

## 2. ddtl-legend 是什么

- **分叉点**：`217e125`（= 我方 main 基线的 HUD 对齐提交）。`git merge-base ddtl/main origin/main` 验证同值。
- **分叉后规模**：22 个新提交（`git log 217e125..ddtl/main`），560 文件 +4203/−8817；其中 `art/raw` 372 个、`frontend/assets/ui` 108 个——主体是美术文件。
- **项目目标**（他们 `docs/DDTL_PLAN.md` 原话）：改造成**微信小游戏横屏 ARPG**，"美术、名字、地图全部原创，风格向 2000 年代端游看齐"，以规避版权。
- **内容替换清单**：`StdItems.csv` 541→约80、`Monster.csv` 273→约20、`Magic.csv` 39→18、地图 445→7 张自创（dt0 主城"天亮城"/df1 晨雾林等）、`MapInfo.txt`/`MonGen.txt` 重写、`MiniMap.txt` 删除、名字统一走 `gamedata/names.csv` 对照表、UI/音频全自制。数值沿用原版。
- **插曲**：`3e51d04` 曾把整个项目替换成 3v3 喷墨游戏，`e6c1034` 又 revert 回来——对比时以 `ddtl/main` 终态为准。
- **Java 服务端改动极小**：仅 3 文件 17 行——`GameWorld.java` +1（stats 增发 `maxExp`）、`GameService.java` 9 行（新手礼包初始物品/快捷栏）、`ResourcesIndexGenerator.java` +7（索引 `Data/*.json` PNG 图集）。

## 3. 借鉴标准核验结论：可借鉴项 = 0

标准：**bug 修复、原版功能还原**；魔改数据不借鉴。

1. **疑似 bug①「升级同步 maxExp」**——pyserver 早已修复同一问题，见 `pyserver/world.py:1035-1037` 根因注释（"旧实现只发 exp 不发 maxExp，升级后拿着登录时的旧 MaxExp"）。
2. **疑似 bug②「商店售价全写 100」**（他们提交 `e253e2e`）——那是 **Java 简化版**的问题。pyserver 按 Delphi 原版实现：`pyserver/npc_script.py:423` 明确 `[goods]` 第二列是库存非单价；售价走 `CheckItemPrice`（`t.price×1.1`）+ `GetUserPrice`（`price_rate`），见 `npc_script.py:446-447`、`handlers_npc.py:118-146`。
3. **原版功能还原：0 处**。他们方向相反（主动离开原版）。log 里 `fix: …对齐原版` 一批提交（`19c6d74`/`fcfb11a`/`5f7c7d5`/`f4213eb`/`19aef65` 等）经 `git log 217e125..ddtl/main` 核验**全在分叉点之前**，属 main 共享历史，不是他们的工作。

## 4. 引擎/Mir2 耦合层次（本次讨论的核心客观结论）

- **256 色限制只存在于解码层**：调色板隔离在 `frontend/gfx/palette.mjs`（`PALETTE_RGBA`），唯一消费点 `wil.mjs:266-269` 的索引色→RGBA 循环。出了 wil.mjs，上层流动的是标准 RGBA canvas，渲染管线（`ActorSprite`/`MapActor` 的 `drawImage` 合成）与色数无关。实证：ddtl 全彩 PNG 图集穿过同一套上层代码正常运行。
- **WIL/WIX 解析完全在客户端**：服务端只生成清单（`pyserver/gamedata.py` `build_resources_index`，启动时调用）+ 静态伺服（`net.py:470`），从不解析内容。`gamedata.py:341` docstring 拒绝 WZL 的理由"无法被前端 wil.mjs 解码"即旁证。
- **Mir2 强绑定是四个同心圆**（由外向内耦合渐深）：WIL 编码 ⊃ 地图格式（`.map` 52B 头/列优先/`0x8000`/48×32 等距，`Maps.mjs`/`MapActor.mjs`/`config.mjs UNIT_X/Y`）⊃ **帧号契约**（`actorData.mjs`：人物 600 帧/外观、MA9~52 怪表、NPC 60 帧/外观、`appr→图库`映射）⊃ 协议语义（`SM_*` 消息）。ddtl 只剥掉了最外层的 WIL；帧号契约是他们出图脚本必须服从的坐标系（`ActorSprite.mjs` 的 `HUMANFRAME*look+cf` 一字未改）。
- **PNG 图集打回 WIL 的可行性**：结构同构（大图为底+帧号索引表≈WIX），他们的帧号已按原版表排布，技术上可无损逆向、`Images.mjs` 零改动；代价是 256 色量化 + 无逐像素 alpha（正是他们当初逃离的两条）。

## 5. resources.json 两栈对照（实测）

| | 我方（本机自动生成，gitignored） | ddtl（构建产物，提交入库） |
|---|---|---|
| `images` | 58 库，`{"x":"Data/X.WIX","l":"Data/X.wil"}`（原版 WIL 全集） | 9 库，`{"a":"Data/x.json"}` PNG 图集；同名 key 顶替原版（hum/magic/tiles/objects…），原创怪另走新 key `ddmon`/`ddnpc`（appr≥900 分库，`actorData.mjs` 三处 `if(appr>=900)`） |
| `maps` | 656 张原版 `.map` | 7 张自创 |
| `wavs` | 723 个原版 wav | 空 `{}` |
| 消费点 | `frontend/gfx/resource.mjs` `loadResources()`→`imageUrls`→`Images.mjs getLib`；`maps` 供地图加载、`wavs` 供 SoundManager | 同左 + 他们新增 `gfx/atlas.mjs`（接口对齐 WIL：`getTexture(index)→{canvas,px,py,…}`） |

## 6. 音效系统三方时间线（`git ls-tree`/`merge-base` 实测）

1. **原版 jootm2（分叉基线 217e125）没有音效系统**：整个文件树 grep `audio|sound|wav` 零命中；`PlayScene._toggleSound()`（`PlayScene.mjs:430`）只翻转状态打一行系统文本，全前端无 `new Audio()`——继承自上游的空壳开关。
2. **我方补齐**：`frontend/audio/` 首个提交 `fcd36e4`（"全量 WAV 转 MP3 并接通前端音效链路"），`git merge-base --is-ancestor` 验证**只在 feat/python-server，不在 main**。
3. **ddtl 无音效但已立项**：`docs/AUDIO_SPEC.md` 为自制音频规格清单（5 首 BGM+音效，mp3/ogg、44.1kHz、-16 LUFS、无缝循环，"我按名字接进游戏"= 待接线）；`ddres/Wav/` 仅 `.gitkeep`。缺失原因=从基线继承（fork 早于我们的音频工作），非遗漏；因其版权策略本就不能复用我们的转换产物。

## 7. ddtl 的地图管线（格式第三方互证）

`tools/make-map.mjs` 的 `writeMap()` 逐字节按原版规范手写 `.map`：52B 头、`writeUInt16LE(W,0)/(H,2)`、tile 步长 12B、**x 外层 y 内层循环（= 列优先 `i=x*height+y`）**、阻挡 `|=0x8000`、大地砖仅 `x%2==0&&y%2==0` 角点存储且索引 +1、物件索引 +1、小地砖位写 0。同一文件双写 `ddres/Map` 与 `gamedata/Mir200/Map`，**未经修改的 Java 服务端地图解析原样读通**——这是对我们 `map_tile_stride()`/列优先/`0x8000` 实现正确性的独立第三方佐证。

## 8. 可复用的验证命令

```bash
git log --oneline 217e125..ddtl/main              # 他们的全部新工作（22 条）
git diff --stat 217e125..ddtl/main -- src frontend tools docs
git show ddtl/main:tools/make-map.mjs             # writeMap() 原版格式证据
git show ddtl/main:ddres/resources.json           # 图集清单
git show ddtl/main:docs/DDTL_PLAN.md              # 项目目标自述
```

## 9. 最终判断

- ddtl 的本质：**复用 jootm2 引擎（Java 服务端+协议+前端渲染框架），替换全部受版权保护的内容层**（美术/地图/名字/音频），并适配微信小游戏触屏操作。
- 按"bug 修复+原版还原"标准，**无可借鉴项**；两处疑似修复我方 pyserver 已有更正确的 Delphi 语义版本。
- 题外价值一条：其 `AtlasLib` 路线证明了"WIL 可替换、上层无感"的解耦方式。若未来要解决"资源不能分发"的部署困境，可参考其接口适配思路（PNG 图集+JSON 帧表，保住帧号契约），但那是独立决策，不属于原版还原工作。
