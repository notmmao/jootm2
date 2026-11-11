# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

浏览器版传奇（Mir 2 / 热血传奇 1.80 风格）移植项目：Delphi 原版服务端 → Java 11 + Netty（`src/`，仅作对照，不再演进）→ **Python 3.10 纯标准库 asyncio**（`pyserver/`，当前默认）。前端为原生 Canvas 2D + DOM（ES Module，无框架）。当前分支 `feat/python-server` 的工作是：维护 Python 后端、修 bug、对照 Delphi 原版补齐未完成功能。

**个人学习/怀旧项目，不商用；资源文件（WIL/Map/Wav）因版权不分发，不在 git 内。**

## 必读文档与交接规则（最重要）

| 文档 | 角色 |
|---|---|
| `HANDOFF-PYSERVER.md` | **本分支的增量交接文档，每次改动后必须更新**：把本轮改动、新规则、踩坑按现有分节格式（每轮一节，如 §16/§17/§18）追加进去，供下一个 Agent 快速接手 |
| `HANDOFF.md` | 远澜原版 Java 交接文档，**保持与上游基线零改动、永不编辑**；玩法数值、协议语义、特效帧号的权威参考 |
| `README.md` | 运行方式与资源准备 |
| `docs/` | 客户端分析、GUI 系统等专题文档 |

改动收尾流程：跑回归 → 更新 `HANDOFF-PYSERVER.md`（新增一节记录根因/改动/验证结果）→ 提交。

## 常用命令

```bash
python -m pyserver                                   # 启动（默认 8080，读 config/server.properties）
python -m pyserver --port 8081 --db data/jootm2-py.db

# 回归脚本（全部零第三方依赖）
python scripts/test_pyserver.py                      # 离线单元测试
python scripts/test_pyserver.py --live 8080          # 单元 + 对运行中服务的实时自检
python scripts/test_all_messages.py --live 8080      # 全部消息 handler + 技能分支
python scripts/test_tick_systems.py                  # tick / 装备属性 / MapQuest
python scripts/smoke_ws.py --port 8080               # WS 冒烟
python scripts/verify_resources.py resources         # 资源体检
python scripts/compare_stacks.py --java-port 8080 --py-port 8081 --full   # 双栈对照
python scripts/convert_audio.py --check --probe 20   # 音频产物校验
node scripts/test_audio.mjs                          # 音频前端链路

# Java 对照版（需要时）
"/d/bin/apache-maven-3.6.1/bin/mvn" -q -DskipTests package
"/d/bin/jdks/corretto-17.0.19/bin/java.exe" -jar target/jootm2.jar

# 前端语法检查（改任何 .mjs 后必做）
node --check frontend/<file>.mjs
```

浏览器测试必须 **Ctrl+F5 硬刷新**（`.mjs` 是 no-cache 但 ES module 有缓存）。

## 解释器约定（关键）

- **服务端复现、回归、live 测试一律用 `D:/Python310/python.exe`（3.10）**——本机 8080 服务就是它。用 3.13 跑测试会掩盖 3.10 专属差异（曾因此漏检 `slots dataclass + 零参数 super()` 的 TypeError，见 HANDOFF-PYSERVER §14）。
- Playwright/浏览器 E2E 用 `D:/Python39/python.exe`。
- 依赖本机 Playwright 的诊断脚本放在 `data/`（gitignore，不提交），如 `data/flicker_probe*.py`。

## 架构

### pyserver/（Python 后端，17 模块约 6300 行）

- **纯标准库**（asyncio/socket/sqlite3/struct/json），**禁止引入第三方包**——本机装不上，Docker 镜像因此只需 `python:3.12-slim`。
- **单事件循环串行世界模型**：Java 版用 `synchronized(world.lock)`，Python 的 asyncio 单循环天然等价，**不要引入线程池**。
- 入口 `__main__.py` → `net.py`（HTTP 静态/REST/Range 206/CORS/WS 握手）→ `service.py`（`GameService` 消息分派）→ `world.py`（`GameWorld`/`GameMap`/`Actor`/`Player`/`Monster` + 完整 tick）。
- **Mixin 架构**：`GameService(ItemHandlers, NpcHandlers, CombatHandlers, SocialHandlers)`；`GameWorld(WorldExtra)` 同理。构建分派表时校验 `set(_handlers) == set(MIGRATED_TYPES)`，不一致抛 `RuntimeError`。新增消息时同步更新 `MIGRATED_TYPES`、`scripts/test_all_messages.py` 的消息清单与执行顺序。
- 数据层：`db.py`（SQLite + CSV 导入）、`gamedata.py`（GBK 文本配置、`.map` 解析）、`templates.py`（dataclass 复刻 Gson 字段名）、`npc_script.py`（`#IF/#ACT/#SAY` 直译）。

### 前端 frontend/

`main.mjs`（入口 + 离屏双缓冲）→ `scene/PlayScene.mjs`（游戏主场景/HUD/快捷键）→ `map/MapActor.mjs`（地图三层渲染 + 地面离屏滚动缓存 + 增量预取）、`actor/ActorSprite.mjs`（动画帧回退）、`gfx/wil.mjs`（WIL/WIX 解码）、`audio/SoundManager.mjs`（音效/BGM，按 WS 消息本地映射，服务端不下发音效 id）。

### 协议与数据

- WS TextFrame + UTF-8 JSON，`{"type": "..."}` 字符串消息类型（**不是**原版 Delphi 的数字协议号；不兼容原版客户端）。
- 服务端只发必要字段，HP 走 `stats` 单播（与原版 `HealthSpellChanged()` 一致，原版 `SM_STRUCK` 里的 HP 更新是被注释掉的）。
- `gamedata/Mir200/Envir/*.txt`、NPC 脚本、MonItems 是 **GBK**（用 UTF-8 grep 中文会假阴性）；`gamedata/mud2/DB/*.csv` 是 UTF-8。
- SQLite 的 items/skills 等表 data 列是 Gson JSON 字符串；Java 建的账号可直接在 Python 侧登录（`SHA-256(salt+password)` 小写 hex）。
- `.map`：52 字节头 + tile，列优先 `i = x*height + y`，`0x8000` 阻挡；12/14 字节两种 tile 变体 + 66 张活动图带尾块，统一用 `map_tile_stride()`（前后端一致）。
- `MiniMap.txt` 索引是 **1-based**（取图要 `index - 1`）。

## 铁律与踩坑

1. **`HANDOFF.md` 永不编辑**；Java 源码 `src/` 不动（本分支只维护 Python 端）。
2. **`gamedata/Mir200/Map` 是 NTFS junction 指向 `resources/Map`——绝对不要递归删除**（`rm -rf` 会穿透删掉真实地图）。`resources/` 下必须直属 `Data/ Map/ Wav/` 三个目录且三者齐全，`resources.json` 才会生成。
3. **找不到 Delphi 权威依据不硬改**。原版 Delphi 源码在本地 `D:\work\me\game\传奇\MIR2\GameOfMir`（GBK 编码；`MirClient/` 与 `M2Server/`）。修玩法相关 bug 前先对照对应 `.pas` 的具体行号/常量，把依据写进 HANDOFF。
4. Java 数值语义：整数除法向零截断（`util.java_div`）、`Math.round = floor(x+0.5)`（`java_round`）、`getShort() & 0xFFFF` 无符号（struct 用 `H` 不用 `h`）。
5. 新增服务端字段时必须 grep 消费点——历史上出现过 `recompute`/`tick`/MapQuest 整层「写了但没人消费」的隐藏缺口。
6. `PENDING_TYPES` 必须保持为空集；46（现 48）个消息类型全部要有 handler。
7. 后端改动后按「常用命令」一节跑全量回归并记录数字；用户反馈类修复要给出量化验证（Playwright 像素对比 / 断言计数），观感问题标注「仍需用户自测确认」。
8. 对外部资源/版权保持边界：不代下载整包、不绕登录；WZL/WZX 加密格式解不了。

## 已知边界（不要当 bug 重复排查）

详见 `HANDOFF-PYSERVER.md` §13.3/§16.4/§18.3：PvP 无独立命中判定（永不 miss）、逐技能施法音是近似映射、受击/死亡固定男声、尸体只对可挖肉动物保留、大地图/系统菜单是 DOM 覆盖层简化实现、0 伤害保留 `max(1,…)`、`_regen` 回血计时是 world 级共享。
