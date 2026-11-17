---
name: mir2-resource-converter
description: 把外部美术资源（PNG 帧序列、其他私服的 WIL/WZL 图库、自绘精灵）转换为本项目可直接加载的 WIL/WIX 图库格式，并生成 HTML 动画预览验证。凡涉及「新怪物/新装备/新 NPC/新特效的美术接入」「WIL/WIX 格式问题」「精灵帧序/帧号/方向布局」「提取或替换游戏贴图」时都必须使用本 skill，即使用户没有明说「转换资源」。
---

# Mir2 WIL/WIX 资源转换

## 目标

把外部美术转换成 Mir2 类游戏前端能直接解码的 **WIL/WIX 图库**（8 位索引色），并生成 HTML 动画预览验证。本 skill **自包含可分发**：格式规范、调色板、动作表、转换工具全部内置于本目录，不依赖任何仓库外部文件。

## 目录结构

```
mir2-resource-converter/
├── SKILL.md                      本文件：工作流与用法
├── references/
│   ├── format-spec.md            WIL/WIX 二进制格式 + 精灵帧号契约（规范全文，先读）
│   ├── palette.json              256 色调色板（index 0 = 透明）
│   └── tables.json               HA/MA 动作表快照（人物/怪物动作段）
└── scripts/
    └── wil_pack.py               info / dump / pack / preview（纯标准库）
```

**动手前先读 `references/format-spec.md`**——特别是「精灵帧号契约」一节：格式错误只是花屏，帧号排错则动画整体错位，且二进制格式没有位深标志位、帧号没有元数据，全靠约定。

## 快速工作流（以新增一只怪物为例）

1. **准备 PNG 帧**：命名 `<动作>_<方向>_<帧号>.png`（方向 0-7、帧号从 0 起）。动作名按目标怪物的 MA 表选（`stand/walk/attack/struck/die/death` 等，完整清单看 tables.json；例如 MA11 = stand 4 帧 / walk 6 / attack 6 / struck 2 / die 10 / death 1）。源图超 256 色会自动最近邻量化，alpha<128 → 透明。
2. **选外观槽位**：外观号 appr 决定图库与帧偏移（`n = appr//10`，n==0 → mon1，1..20 → mon(n+1)，90 → effect；偏移多数字种族 = `(appr%10)×360`，完整特例表见 format-spec.md §5.2）。用 `info` 查目标库的空闲区间后 merge 进去：
   ```
   python <skill>/scripts/wil_pack.py pack <帧目录> --out resources/Data/Mon21 --layout mon --race MA11 --appr 205 --merge
   ```
   `--merge` 把新帧拼进现有库、原有图字节级保真（16 位帧、对齐行都安全）。输出 `<基名>.wil/.WIX/.manifest.json` 并自动读回校验（量化像素逐帧比对）。
3. **预览验证**：
   ```
   python <skill>/scripts/wil_pack.py preview resources/Data/Mon21
   ```
   浏览器打开生成的 `<lib>.preview.html`：每个动作 × 8 方向一段动画，蓝色虚线框 = 实体所在 48×32 格（左上角是锚点），帧按 px/py 偏移贴图，与游戏渲染一致。预览走与解码器相同的读回路径，能发现绝大多数打包错误。
4. **接入游戏数据**（在 jootm2 项目内）：Monster.csv 加一行（`race` 选 MA 表、外观 `appr` 即上面的槽位号）；重启服务端自动重建 `resources/resources.json`（`pyserver/gamedata.py` 扫 `Data/*.WIX`，key = 小写文件名）；浏览器 **Ctrl+F5** 硬刷新进游戏确认（ES module 有缓存）。

新装备（衣服/武器）外观：hum/weapon 库以 600 帧为一个外观槽位（`--layout hum --appearance N`，dress = `shape×2+sex`）。NPC：60 帧一外观（`--layout npc --appr N`）。

## 工具用法

```
info    <lib> [--indices 0-59]                  图库概览：帧数/位深/每帧 w×h px,py 字节
dump    <lib> [--indices a-b] --out DIR         提取帧为 PNG（占位帧跳过）
pack    <帧目录> --out <基名> [--layout plain|hum|mon|npc]
        [--base N | --appearance N | --appr N] [--race MA11]
        [--px N] [--py N] [--merge | --force] [--no-verify] [--tables <actorData.mjs>]
preview <lib> [--layout ... --race ...] [--indices a-b] [--out x.html]
```

- 帧目录命名：布局模式用 `<动作>_<方向>_<帧>.png`；plain（物品/UI/地面）任意名按文件序，`--base` 指定起始索引。
- `--merge` 必须指向现有 WIL；`--force` 才允许覆盖整库（防止误毁原版资源）。
- 缺省锚点：角色/怪/NPC = 水平居中、脚踩格底（`px=(48−w)//2, py=32−h`）；plain = (0,0)。要和原版精灵混排时先用 `info` 量几帧同类精灵的 px/py 再定（原版美术数据不统一）。
- `--tables`：传 `frontend/actor/actorData.mjs` 可实时解析动作表（仓库内表更新后保持新鲜）；缺省用内置 `references/tables.json` 快照。
- 短库名（如 `Hum`）会在当前目录的 `resources/Data/` 下兜底查找；其余用显式路径。

## 验证清单（收尾必做）

1. `pack` 自带 verify 全过（输出 `[verify] 读回比对通过`）。
2. `preview` HTML 在浏览器里逐动作过一遍：方向、脚底/锚点、透明边缘脏色、帧错位（如 die 接到 death 上）。
3. 游戏内实测（项目内）：resources.json 重建后能查到新库，Ctrl+F5 进图看实体。
4. merge 进原版库后跑资源体检（jootm2：`python scripts/verify_resources.py resources`）。

## 常见坑

- **WZL/WZX（加密/新格式）解不了也不做**——遇到让用户找老客户端的 WIL/WIX 版本。
- 16 位 RGB565 图库能读能 merge，但新帧一律写 8 位；帧间不留填充（位深靠字节数反推）。
- 半透明没有逐像素 alpha：发光部分画亮色靠 blend 模式，索引 0 才是透明。
- Windows 控制台 GBK：脚本输出加 `PYTHONIOENCODING=utf-8`。
- 特殊精灵逻辑（稻草人死亡火焰叠层、骷髅尸体无方向等）在替换其美术前要读 jootm2 `frontend/actor/ActorSprite.mjs` 的对应分支。
- magic 系特效帧基址按技能 EffectNumber 计算，不要按顺序堆帧。

## 与 jootm2 仓库的关系（溯源）

本 skill 的规范与数据固化自 jootm2 仓库（2026-11-17）：解码语义 = `frontend/gfx/wil.mjs`；调色板 = `frontend/gfx/palette.mjs`；动作表/外观偏移 = `frontend/actor/actorData.mjs`；锚点语义对照原版 Delphi `MirClient/Actor.pas`（`dx + m_nPx`，与前端一致相加）。仓库内源文件是最上位基准——发现不一致时以仓库为准并同步更新本 skill 的快照。
