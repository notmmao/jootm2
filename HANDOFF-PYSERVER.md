# jootm2 Python 服务端分支交接文档

> **记录时间**：2026-11-09
> **分支**：`feat/python-server`
> **分支基点 / 上游**：`217e125`（`feat: 底部 HUD 对齐原版…`）＝ 当前 `origin/main` 的 HEAD
> **远程分支名**：`origin/main`（仓库**没有** `origin/master`，口头上说的 master 即指它）
>
> 本文件是 `feat/python-server` 分支**独立新增**的增量交接文档。`HANDOFF.md`（远澜原版交接文档）**保持与上游基线逐字一致、本分支零改动**，所有 Python 相关内容都写在这里，以便后续合并 `origin/main` 时避免冲突。
> `HANDOFF.md` 正文描述的是 Java 版实现细节，**仍然作为玩法数值与协议语义的权威参考**；本文只记录「转换本身」的状态、坑与待办。

---

## 0. 一句话状态

Java 后端已完整迁移为**纯标准库 Python（asyncio）**实现，46 种 WebSocket 消息全部有对应 handler，与 Java 版共用同一套协议 / SQLite schema / 配置文件；默认部署已切到 Python，Java 版仅作为双栈回归对照保留。**代码层面的转换已完成**，剩余的都是「边界项」与「优化项」，见第 6 节。前端另完成音频链路（§10）、渲染防闪烁（§11）、选角闪烁与移动阴影跳变（§12）三轮优化，均为纯前端改动。

---

## 1. 分支与提交

| commit | 内容 |
|---|---|
| `cdfac57` | `feat(pyserver): 纯标准库 Python 服务端骨架 + 双栈协议回归基线`（15 文件 / +4079 行） |
| `75390e1` | `feat(pyserver): 全量迁移 46 种消息并切换默认部署到 Python`（19 文件 / +4497 行） |
| `ae0b59d` | `feat(pyserver): 补齐 tick 全子系统、装备属性与 MapQuest 任务`（7 文件 / +1093 行） |
| `d294744` | `fix(resources): 对齐 1.80 客户端资源布局与 .map tile 步进推断`（7 文件） |

迁移主体共 4 个提交，相对基线改动 32 个文件 / +9614 −29 行，其中 `pyserver/` 17 个模块共 **6343 行**（Java 侧约 10478 行）。之后各有独立提交：§10 音频链路（`feat(audio): …`）、§11 渲染防闪烁（`fix(frontend): …`）、§12 选角/地面缓存（`fix(frontend): …`，2 个前端文件）。这些都只动前端与脚本，**不改 `pyserver/**`，也不改 `src/main/java/**`**。最新提交 hash 用 `git log --oneline -3` 查。

`git log --oneline 217e125..HEAD` 可随时复核。

---

## 2. 架构与关键设计决策

### 2.1 为什么是纯标准库

本机 Python（3.13）没有 `fastapi` / `uvicorn` / `orjson` / `numpy` 等第三方包，也没有稳定的安装渠道。因此实现只依赖 `asyncio` / `socket` / `sqlite3` / `json` / `struct` / `dataclasses`，`pip install` 一步都不需要，Docker 镜像也因此只需 `python:3.12-slim`。

### 2.2 并发模型

Java 侧 `onMessage`、`tick`、`saveAllPlayers`、`handleAdmin` 全部包在 `synchronized (world.lock)` 里，本质是单线程串行世界模型。Python asyncio **单事件循环天然等价**，所有状态只在事件循环内读写，因此不需要任何锁。不要引入线程池去「加速」世界逻辑，那会直接破坏语义一致性。

### 2.3 协议兼容策略

**前端不改一行**（唯一例外见 §4：`frontend/map/Maps.mjs` 的地图 tile 推断 bug 修复）。两个服务端共用：

- WebSocket TextFrame + UTF-8 JSON，`{"type": ...}` 字符串消息类型（**不是**原版 Delphi 的数字协议号）
- 同一份 `config/server.properties`
- 同一套 SQLite schema（账号、角色、模板表），Java 创建的老账号能在 Python 侧直接登录（密码都是 `SHA-256(salt + password)` 小写 hex）
- 同一份 `gamedata/`：`Mir200/Envir/*.txt` 是 **GBK**，`mud2/DB/*.csv` 是 **UTF-8**，SQLite 的 `items/storage/skills/quick_slots` 与模板表 `data` 列是 Gson JSON 字符串

### 2.4 模块清单

| 文件 | 行数 | 职责 |
|---:|---:|---|
| `__main__.py` | 80 | CLI 入口、启动/关闭、生成 `resources.json` |
| `config.py` | 144 | 读 `server.properties` + 环境变量 / CLI 覆盖 |
| `net.py` | 436 | HTTP 静态 / REST / Range 206 / CORS / WebSocket 握手与分帧 / tick 定时 |
| `service.py` | 479 | `GameService`：46 消息分派、账号角色、REST、`sendBagUpdate`、`/api/migration` |
| `world.py` | 1220 | `GameWorld` / `GameMap` / `Actor` / `Player` / `Monster`、`recompute`、**完整 tick** |
| `world_extra.py` | 808 | 掉落 / 地面物品 / NPC / 行会 / 攻城 / 组队 / 仓库 / 交易 / 火墙 / 召唤 / `killMonster` / `addExp` / MapQuest 触发 |
| `handlers_items.py` | 349 | 拾取、使用、穿脱装备、快捷栏、丢弃、移动物品、技能键 |
| `handlers_npc.py` | 132 | NPC 对话、选项、买卖 |
| `handlers_combat.py` | 871 | 物理攻击 / 刺杀 / 半月 / 野蛮冲撞 / **33 个技能分支** / 伤害与经验 |
| `handlers_social.py` | 349 | 行会、攻城、组队、仓库、交易闭环 |
| `npc_script.py` | 409 | 直译 `NpcScript` + `NpcScriptEngine`（`#IF/#ACT/#SAY/#ELSEACT/ELSESAY`、`[goods]`、任务变量） |
| `gamedata.py` | 343 | GBK 文本、`MapInfo`/`MonGen`/`MiniMap`/`MapQuest`、`read_map`、`map_tile_stride`、`resources.json` |
| `db.py` | 307 | SQLite DAO、CSV 导入、密码哈希 |
| `templates.py` | 224 | `ItemTemplate` / `MonsterTemplate` / `MagicTemplate`（dataclass 复刻 Gson 字段名） |
| `util.py` | 179 | 常量表、`java_div` / `java_round`、装备槽、方向、技能解析 |

**Mixin 架构**：`GameService(ItemHandlers, NpcHandlers, CombatHandlers, SocialHandlers)`，`MIGRATED_TYPES = … | ITEM_TYPES | NPC_TYPES | COMBAT_TYPES | SOCIAL_TYPES`，`PENDING_TYPES = frozenset()`，并在构建分派表时校验 `set(_handlers) == set(MIGRATED_TYPES)`，不一致直接 `RuntimeError`。`GameWorld(WorldExtra)` 同理。

**查询迁移进度**：`GET /api/migration` → `implementation=python-stdlib, totalTypes=46, migratedCount=46, pendingCount=0, stage=all-46-types`。

### 2.5 双栈回归

```
java -jar target/jootm2.jar                          # Java 占 8080（需 JDK 17）
python -m pyserver --port 8081 --db data/jootm2-py.db   # Python 占 8081，独立 DB
python scripts/compare_stacks.py --java-port 8080 --py-port 8081 --full
```

`compare_stacks.py` 对比 46 步的响应类型与字段结构；`scripts/golden/protocol_baseline_full.json` 是 Java 侧录制的基线，可 `--golden` 离线回放。

---

## 3. 启动、配置与回归命令

### 3.1 启动

```bash
python -m pyserver                                  # 默认 8080，读 config/server.properties
python -m pyserver --port 8081 --db data/jootm2-py.db
docker compose up --build                           # 默认 Python 镜像
docker compose --profile java up jootm2-java --build   # Java 对照，宿主 8082
```

CLI：`--config --host --port --db --gamedata --web --resources --tick-ms --no-resource-index --version`。
环境变量：`JOOTM2_PORT / JOOTM2_HOST / JOOTM2_DB / JOOTM2_WEB_ROOT / JOOTM2_RESOURCES / JOOTM2_GAMEDATA / JOOTM2_ROOT / JOOTM2_CONFIG`。
启用端点：`/` 静态、`/ws`、`/resources/*`、`/api/status`、`/api/players`、`/api/migration`。

### 3.2 回归脚本（全部零依赖）

```bash
python scripts/verify_resources.py --self-test            # 资源体检脚本自检 10/10
python scripts/verify_resources.py resources              # 真实资源体检
python scripts/smoke_ws.py --port 8081                    # WS 8 步冒烟
python scripts/test_pyserver.py --live 8081               # 单元 + 实时自检
python scripts/test_all_messages.py --live 8081           # 46 handler + 33 技能分支 + 18 成功路径
python scripts/test_tick_systems.py                       # tick / 装备属性 / MapQuest，53 断言
python scripts/compare_stacks.py --java-port 8080 --py-port 8081 --full
python scripts/compare_stacks.py --py-port 8081 --golden scripts/golden/protocol_baseline_full.json
python scripts/convert_audio.py                       # 全量 WAV→MP3（幂等，需 ffmpeg）
python scripts/convert_audio.py --check --probe 20    # 校验 mp3 产出 + ffprobe 抽样
python scripts/convert_audio.py --explain 103         # 查某编号对应的 mp3 / wav URL
python scripts/convert_audio.py --self-test           # 12/12，临时目录自检
node scripts/test_audio.mjs                          # 音频前端链路 18/18
node --check frontend/audio/SoundManager.mjs
# 渲染防闪烁抓帧（需本机 Playwright，脚本在 data/，不提交）：
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe.py
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe2.py
"F:/funasr/.venv/Scripts/python.exe" data/doublebuffer_check.py
```

最后一次全量验证结果（全绿）：`48 PASS`（离线 `test_pyserver.py`，含 2 条地图用例）/ `53 PASS`（live）/ `18/18` 成功路径 / `53/0` tick 断言 / `8/8` WS 冒烟 / `10/10` 体检自检 / `--full` 46 步响应类型全一致、字段结构零差异 / golden 回放零差异 / `node --check frontend/map/Maps.mjs` 通过；音频链路追加：`12/12` 转换自检、`18/18` 前端回归、`723/723` mp3 产出、`20/20` ffprobe 可解码、HTTP 200/206 与 MIME 实测通过；§11 追加：全部 `frontend/**/*.mjs` 语法检查通过、无 console/page error、首屏地图纹理 231/231、强制走路动画 288 次取帧 0 次缺失（详见 §11.4）。

---

## 4. 对「Java 版」的改动清单（合并时最需要看的一节）

### 4.1 Java 源码：**零改动**

`git diff --name-only 217e125 HEAD -- src/` 为空。`src/main/java/**` 一行没动，`pom.xml` 的 `<maven.compiler.release>11</maven.compiler.release>` 也没动。Java 版仍可原样构建，只是 `JAVA_HOME` 默认指向 JDK 8 会报「无效的标记: --release」，需显式使用：

```bash
"/d/bin/jdks/corretto-17.0.19/bin/java.exe" -jar target/jootm2.jar
"/d/bin/apache-maven-3.6.1/bin/mvn" -q -DskipTests package
```

### 4.2 被本分支改动的**上游已有文件**（潜在合并冲突点）

| 文件 | 改动 | 冲突风险 |
|---|---|---|
| `HANDOFF.md` | **已还原为基线，零改动** | 无（用户要求保持远澜原文） |
| `README.md` | +57 行：后端说明、Python 运行方式、回归命令、资源准备、Docker 段落、配置表 | ⚠️ 上游若也改 README 会冲突；**尚未还原，待用户决定** |
| `Dockerfile` | Maven/JRE 构建 → `python:3.12-slim`，`ENTRYPOINT ["python","-m","pyserver"]`，COPY `pyserver/ frontend/ config/ gamedata/` | 上游改动时冲突 |
| `docker-compose.yml` | 默认服务改 Python；Java 移到 `--profile java`（宿主 8082）；补 `./gamedata:/app/gamedata` 挂在与 `/api/status` 健康检查 | 同上 |
| `.gitignore` | 补 `client_original/`、Python 缓存、`.workbuddy/` | 同上 |
| `.dockerignore` | 排除缓存 / DB / `.workbuddy/`；原 Java 版未 COPY `gamedata/` 的坑在 `Dockerfile.java` 里补上 | 同上 |
| `frontend/map/Maps.mjs` | 8 行：`.map` tile 步进改用整数推断（见 §5.3），修 DataView 抛错 | 上游若改地图加载会冲突 |
| `frontend/main.mjs` | §10 音频接线 3 行 + §11 离屏双缓冲（back buffer 每帧一次 blit） | 上游若改入口初始化会冲突 |
| `frontend/scene/PlayScene.mjs` | §10 声音按钮 / `data-sfx` / 喝药 7 行 + §11 加载期保留上一帧、HUD 同步化、坐标拆出重绘 key（见 §11） | 上游若改 HUD/背包/渲染循环会冲突 |
| `frontend/map/MapActor.mjs` | §11 首屏纹理预取、`_getTex` 失败 5 秒重试、`_texVersion`；§12 地面层离屏滚动缓存、移动增量预取、对象层帧回退（见 §11/§12） | 上游若改地图加载/渲染会冲突 |
| `frontend/actor/ActorSprite.mjs` | §11 动画帧回退 `_lastByLib` + 连续帧预热（见 §11） | 上游若改角色动画会冲突 |
| `frontend/ui/SelectCharScene.mjs` | §12 背景/立绘预取、动画帧门控、逐槽帧回退、加载期保留上一帧（见 §12） | 上游若改选角界面会冲突 |
| `config/server.properties` | **未改**，两个服务端共用 | 无 |

### 4.3 新增文件（不会与上游冲突）

`pyserver/**`、`scripts/golden/**`、`Dockerfile.java`、`HANDOFF-PYSERVER.md`（本文件），以及 6 个新回归脚本：`scripts/smoke_ws.py`、`scripts/test_pyserver.py`、`scripts/test_all_messages.py`、`scripts/test_tick_systems.py`、`scripts/verify_resources.py`、`scripts/compare_stacks.py`。音频链路（§10）另新增：`scripts/convert_audio.py`、`scripts/test_audio.mjs`、`frontend/audio/SoundManager.mjs`、`frontend/audio/audio_map.json`；二进制 `resources/Audio/*.mp3` 不提交（`.gitignore` 已忽略 `resources/`）。

> 这些回归脚本在本分支都是**新建**文件，不是修改上游脚本。上游 `scripts/` 里原有的是 `fx_*.py` / `taoist_test.py` / `paradox_to_csv.py` / `highwall_check.py` 等调试脚本，本分支未动它们。

### 4.4 Java 侧**已知但未修**的小毛病（等用户点头才动 Java 代码）

1. `ResourcesIndexGenerator` 会把 `.wzl/.wzx` 也写进 `resources.json`，但前端 `frontend/gfx/wil.mjs` 只支持 WIL/WIX → 「索引生成成功但画面全空」的假兼容。Python 侧 `gamedata.py` 已改为**只索引 WIL/WIX 并告警**。
2. `MapFileReader` 写死 12 字节 tile；前端自适应 12/14。Python 侧 `map_tile_stride` 已完整处理。

另外几项**是 Java 自身既有局限，不算转换遗漏**，Python 与之保持一致：`MessageIds.java`（装备槽 `U_*` 常量，`util.py` 已覆盖）、`makeFireWall(p, targetId, …)` 定义了但全项目无人调用、`questParams` 不落库、`teleportRing` 只声明不消费。音效链路已由前端补齐（§10）：`wavUrl()` 现在作为 mp3 缺失时的回退被调用，`wavCount()` 仍只是预留工具函数。

---

## 5. 遇到的坑（按类别）

### 5.1 环境与构建

- **JDK 8 不支持 `--release 11`**：最初 `JAVA_HOME` 指向 JDK 8，Maven 报 `Fatal error compiling: 无效的标记: --release`。曾在不改原项目的临时副本里做过 `source/target 8` 兼容验证；后来用户提示 `D:\bin\jdks\` 有多个版本，最终用 `corretto-17.0.19` 构建原项目本体，无需任何兼容补丁。
- **Maven 不在 PATH**：显式调用 `/d/bin/apache-maven-3.6.1/bin/mvn`。
- **无第三方 Python 包** → 见 §2.1。
- **Docker daemon 未启动**（只有 CLI）：`docker compose config` 语法校验通过，但无法真机 build。改用「镜像 COPY 集合等价目录」验证：只拷贝 `pyserver/ frontend/ config/ gamedata/` 到独立目录，从该目录以 8083 启动，`gamedata` 正确定位（445 地图定义 / 245 爆率表 / 303 NPC / CSV 535+267+33 条）、REST / 静态 / WS 全通。**真机 Docker 验证仍未做**。
- **Git Bash 的 `/tmp` 与 Windows Python 不互通**（实际落在 `F:/Temp/...`）：做 HTTP 校验时别混用 shell 重定向和 Python 读文件，直接用纯 Python `urllib` 更稳。
- **Bash 里误用 PowerShell here-string**：`git commit -m @'…'@` 把 `@` 混进提交主题，已 `git commit --amend` 修正。

### 5.2 数据格式与 Java 数值语义

- **GBK vs UTF-8**：`Mir200/Envir/*.txt` / NPC 脚本 / MonItems 是 GBK（用 UTF-8 grep 中文会假阴性）；`mud2/DB/*.csv` 是 UTF-8。
- **Java 数值语义**：整数除法向零截断（`java_div`）、`Math.round = floor(x+0.5)`（`java_round`）、`getShort() & 0xFFFF` 是无符号（Python `struct` 必须用 `H` 而非 `h`，否则 `.map` 阻挡位错判）。
- **`.map` 结构**：52 字节头 + tile，列优先 `i = x*height + y`，`0x8000` 表示高墙/阻挡，含门与动画/光照标志。12 / 14 字节两种 tile 变体。
- **WIL/WIX 结构**：WIX 偏移 44 存图片数、48 起为偏移表；WIL 1080 字节头；图像头 `width/height/px/py`；8 位调色板或 16 位 RGB565；像素自下而上；帧号硬编码在前端。**WZL/WZX 是 12 周年后的加密格式，本项目解不了**。
- **HTTP Range**：WIL 按 512 KB 分片 `fetch`，服务端必须返回 `206 + Content-Range` + `Accept-Ranges` + CORS `*`。`.mjs/.json` 用 `no-cache`，大资源用 `max-age=31536000, immutable`。

### 5.3 客户端资源（2026-11-09 最后一轮）

- **位置必须直属**：`resources/` 下要直接是 `Data/ Map/ Wav/`。用户最初放的是 `resources/传奇1.80客户端/`，多套一层 → `resources.json` 不生成、前端 `/resources/resources.json` 404。已上提三个目录，客户端 exe/dll 与 452MB RAR 归档到 `client_original/`（已加 `.gitignore`）。
- **`Wav/` 目录不能整个删掉**（哪怕清空文件）：`generate_resources_index` 要求 `Data/Map/Wav` 三者齐全，否则索引不生成。
- **`gamedata/Mir200/Map` 是 NTFS 目录联接（junction）指向 `resources/Map`**，前后端共用同一批 `.map`。⚠️ **绝对不要对它做递归删除**（`rm -rf` / `Remove-Item -Recurse`），会穿透删掉 `resources/Map` 里的真实地图。`.gitignore` 原本就预留了 `resources` 和 `gamedata/Mir200/Map` 两条软链数据规则。
- **66 张活动模板图带尾块**：`T118 / T218 / EM001…` 是「12 字节 tile + 尾部附加数据块」（如 30×35 的 `T118.map` 长 20692，而 `52+w*h*12=12652`）。旧 `read_map` 用 `len(body) >= w*h*14` 误判成 14 字节 → 整图阻挡错位；前端 `Maps.mjs` 用浮点步进会让 `DataView` 抛错。新增 `map_tile_stride()`，前后端统一按 12 字节读前 `w*h` 个 tile。
- **`Deco..wix` 是原端文件名笔误**（双点）：体检脚本按 `Path.stem` 少算一组（57/58），索引生成器按首段分词本来就是 58/58。已统一为与服务队同一分词规则。
- **服务端地图目录原本是空的**（`gamedata/Mir200/Map/` 0 个 `.map`），一直回退 256×256 全通行图，传送门/阻挡全部失效。联接后启动日志：`0=700×700`、`3=1000×800`、`T118=30×35`，加载 445 地图定义 / 2173 门 / 刷怪 704 只。
- **MapInfo 登记 445 张、缺 71 张**：都是 `T000x` 测试图 / `8000~8055` / `M995~999` 这类活动占位图，原版端也不带，不影响正常地图。
- **体检结论**：WIL/WIX 老格式 58 组、WZL/WZX 0 组；关键帧 4/4 命中（`prguse3[29]=168×234`、`prguse[370]=232×325`、`mon5[2669]=40×26`）；656 张 `.map`、723 个 wav；`resources.json` 1437 条 URL 磁盘全存在。
- **版权边界**：不代下载第三方论坛/网盘整包、不转述提取码、不绕登录；用户本机自备资源后才做解包/体检/软链。

### 5.4 移植过程中修过的真实 bug

| 问题 | 修复 |
|---|---|
| `NpcHandlers.__init__` 作为 mixin 不会被 MRO 调用，`self.npc_engine` 可能不存在 | 改惰性 `_engine()` |
| `world_extra` 用了不存在的 `config.data_dir` | 改 `config.db_file.parent / "pyserver_guilds"`，避免覆盖 Java 行会目录 |
| `kill_monster` 与 `_kill_if_dead` 重复发经验 | 经验统一由战斗层授予 |
| `_sword_flag` 返回 tuple，初版只赋一个变量 | tuple 解包，`sword_hit in (26,7)` 时加 bonus |
| `distance_to()` 只定义在 `Player`，`Monster` 没有 → 新 AI 一调就 `AttributeError` | 上提到 `Actor` |
| 门广播 Python 用 `door`，Java 是 `doorState` | 字段对齐 |
| 复活戒指免死后没损毁装备 → 下次 `recompute` 又能无限复活 | 免死后清掉该装备并重算 |
| 宠物缺 `nameColor` 协议字段 | 补齐 |
| **`Player.recompute` 是简化版**：战斗层引用的 `suck_health_rate` / `paralysis_weapon` / `revival_ring` / `luck` 恒为 0/False，`mana_to_health` / `teleport_ring` 连字段都没有 | 完整移植 StdMode 5/6/19/20/21/23/24/53 分派、三职业套装、武器幸运、护身/吸血/麻痹/复活/传送戒指 |
| **`world.tick` 是简化版**（挂着 `TODO(stage3)`）：Java 的 12 个子系统全缺，怪物打死玩家走「回血+改坐标」，绕过 `kill_player` | 按 Java 调用顺序直译：自然回血 / 治愈术持续回血 / buff 过期 / 红绿毒 / 火墙伤害 / 远程怪延迟飞矢 / 守卫与隐身仇恨 / 随机游走 / 神兽变身 / 宠物升级 / 魔法盾与护身减伤 / 死亡走 `kill_player` / 怪物重生 |
| **MapQuest 完全没接**：`map_quests` 初始化成空列表后再没写过 | `gamedata.py` 新增 `parse_map_quest`（含引号分词），`WorldExtra` 加载 `MapQuest_Def/` 脚本并在 `kill_monster` 触发，复用 NPC 引擎的 `quest_params` |
| `compare_stacks.py --full` 初次只有 45 步 | 补 `deleteCharacter`，重录 full golden |
| logout 后服务端主动关连接导致采集脚本 EOF | `run_stack` 捕获 `EOFError/OSError/BrokenPipeError` |
| `test_all_messages.py` 用 `p.bag[0]` 当装备（角色初始已有多件） | 记录 `equip_inst` / `consumable_inst` 的 `makeIndex` |
| `verify_resources.py` 把「关键帧尺寸命中」当 warning → 完美资源退出码 1 | 去掉误报，加 WZL-only 负例 |
| `.map` 用有符号 `h` 解析 | 改 `<HHHB5x` / `<HHHB7x` |

> **教训**：覆盖度不能只看「消息是否注册」。曾出现一整层「状态被写进去但永远没人消费」的隐藏缺口（`recompute` / `tick` / `MapQuest`），是靠逐文件对照 Java 公开方法清单才发现的。以后加新字段时，务必 grep 消费点。

### 5.5 协议边界（和原版客户端）

jootm2 **完全不兼容**原版 Delphi 客户端：原版是 TCP 多网关 + `TDefaultMessage` + EDcode/6bit/3DES + GB2312 + `CM_*`/`SM_*` 数字协议号；本项目是单端口 Netty/asyncio WebSocket TextFrame + UTF-8 JSON + 字符串 `type`。找 1.80 客户端**只是为了 WIL/WIX/Map/Wav 素材文件**，与通信协议无关。同源证据：`MIR2/GameOfMir/Client/Actor.pas` 的 `WORDER[0..1,0..599]` 与 `frontend/actor/actorData.mjs` 的 1200 位表逐位一致。

---

## 6. 待办（按优先级）

### 6.1 尚未完成 / 需要用户决策

1. **逐条数值级 golden diff**：目前验证是「协议形状 + 成功路径」级，`--full` 为避免战斗/掉落随机性使用中性参数且不开启严格数值断言。若要求伤害、掉落、经验、NPC 脚本条件**逐条绝对一致**，需另做 deterministic 随机种子 + 固定状态的数值对比。
2. **Java 侧两个小修**（§4.4）：WZL/WZX 假兼容、`MapFileReader` 写死 12 字节。之前约定「等用户点头」，**未动**。
3. **浏览器分片缓存**：实测（无头 Chrome + CDP，登录界面、未进游戏）首次清空缓存 33 请求 / **2.654 MB**，二次热缓存 33 请求 / **2.650 MB** —— 只有 2 个 WIX 命中缓存，**3 个 WIL 分片 2.36 MB 原样重下**。原因是 Chrome 不缓存 `fetch()` 发出的 Range 206 分片（即使标了 immutable）。修法：在 `frontend/gfx/scheduler.mjs` 加 Cache API / IndexedDB / Service Worker 层分片缓存。
4. **服务端 304 / ETag**：`.mjs` 与 `resources.json` 是 `no-cache`，Python 服务端不处理 `If-Modified-Since` / `If-None-Match`，每次 200 全量约 290 KB。
5. **磁盘瘦身**（约 723 MB 死数据 + `client_original/` 441 MB）：19 个从未引用的图库 476.1 MB（`deco / dragon / hair2 / humeffect / magic3~5 / mon22 / mon23 / objects2~10 / prguse2`）、Wav 189.1 MB（**已全量预转为 13.57 MB MP3，见 §10**；保留 mp3 的前提下原始 WAV 可裁，但裁掉后 mp3 404 时就没有 WAV 可回退）、282 张 MapInfo 未引用的 `.map` 49.5 MB、`Data` 里非 WIL/WIX 的 8.1 MB。⚠️ **Wav/ 目录要保留（可清空文件）**。删文件前必须先列完整清单再动手，别递归删。
6. **Docker 真机构建验证**：daemon 未启动，目前只有 `compose config` 语法校验 + 等价目录模拟。
7. **浏览器端完整试玩回归**：资源链路 / WS 冒烟 / golden 都通了，但没做「登录 → 建角 → 进图 → 移动 → 战斗」的浏览器端 E2E（Playwright 脚本上游有，见 `scripts/fx_*.py`）。
8. ~~**音效链路**：`wavUrl/wavCount` 只有定义没有调用，`_toggleSound()` 只打日志~~ → **已完成**（2026-11-09 方案 B 全量落地，见 §10）。剩余的人工验证是「浏览器里真听一遍」，用户安排晚上自测。

### 6.2 已完成、「全部完成」的口径

- 46/46 消息注册且全部可执行；33 个技能分支无异常
- `world.tick` 全子系统、`Player.recompute` 全属性、MapQuest 任务触发
- 双栈 46 步协议形状/字段零差异 + golden 离线回放
- 默认部署切 Python、Java 版保留为对照、Docker 缺 `gamedata/` 修复
- 1.80 客户端资源布局与 `.map` 解析修复（此前「客户端资源缺失」这项边界**已解决**）
- 浏览器不会下载 1.5 G：按需懒加载（`resources.json` 35 KB + 前端 `.mjs` 253 KB + WIX 索引几 KB + 覆盖所需图片的 512 KB Range 分片 + 进入某图时该图 `.map` 整文件），且全是 `localhost` 本机读取、不占公网带宽
- 音效链路（§10）：723 个 WAV 全量转 MP3、714 个原版编号可用、7 首 BGM、事件→音效映射、开关/音量持久化；服务端与 WS 协议零改动，Java 版同样可用
- 保真度补齐（§13）：命中帧时序对齐（伤害飘字/受击/命中特效延后到武器接触帧 ~170ms，消除「先爆后砍」）；静态 blend 加色对象解耦（火/光类静态贴图恢复 `lighter` 辉光）；经验表补全到 101 项（解除 65 级后经验需求被钳死的冻结）；动画帧裁剪与预取/回退统一为 32 帧

### 6.3 不算遗漏的项

`MessageIds.java` 常量、`makeFireWall` 未调用、`questParams` 不落库、`teleportRing` 未消费 —— 均为 Java 自身既有局限，Python 与其对齐。

---

## 7. 合并 `origin/main` 的注意事项

**用户核心关切：避免冲突。**

- 仓库远程是 `https://github.com/jootm2/jootm2`，**默认分支叫 `main`，没有 `master`**；本地还有 `main` 分支与 `remotes/origin/main`。
- 当前 `origin/main` = `217e125` = 本分支基点，**此刻合并没有新内容**。上游前进后再合并时：
  - `HANDOFF.md`：本分支零改动 → **必然不冲突**（本文已把说明全部搬走）。
  - 全部新增文件（`pyserver/**`、`HANDOFF-PYSERVER.md`、`Dockerfile.java`、新增 scripts）：除非上游恰好建同名文件，否则不冲突。
  - ⚠️ **可能冲突的 11 个上游文件**：`README.md`、`Dockerfile`、`docker-compose.yml`、`frontend/map/Maps.mjs`、`frontend/main.mjs`、`frontend/scene/PlayScene.mjs`、`frontend/map/MapActor.mjs`、`frontend/actor/ActorSprite.mjs`、`frontend/ui/SelectCharScene.mjs`、`.gitignore`、`.dockerignore`（5 个前端交互文件来自 §10 音频接线 + §11/§12 渲染优化）。建议 `git fetch origin` 后先看 `git diff --stat HEAD..origin/main` 是否碰这些文件；若碰到，取舍原则是「保留 Python 运行说明 + 上游新增玩法记录」。
  - 若希望连 `README.md` 也零冲突，可单独把该文件还原到 `217e125`（代价是 README 会重新写成 Maven/Java 运行方式，运行说明需以本文为准）。**此操作尚未执行，等用户决定。**
- 本地软链数据 `resources/`、`gamedata/Mir200/Map`、`client_original/` 均在 `.gitignore` 中，不要提交；删除 junction 前先看 §5.3 的警告。

---

## 8. 给下一位 agent 的阅读顺序

1. 本文 §4（改动清单）与 §7（合并注意）—— 先知道哪些文件动过、哪些不能碰
2. `HANDOFF.md`（远澜原文）—— 玩法数值、协议字段、特效帧号、GUI 对齐细节的权威参考
3. `README.md` —— 当前 Python 运行方式
4. `pyserver/service.py` 的 `_handlers` 与 `MIGRATED_TYPES` —— 消息分派全貌
5. `pyserver/world.py::tick` + `world_extra.py::tick_extra` —— 世界循环
6. `scripts/compare_stacks.py` + `scripts/golden/` —— 双栈回归基线
7. 本文 §6 待办 —— 决定下一步做什么

---

## 9. 数字速查

| 项 | 值 |
|---|---|
| Java 源码 / Python 源码 | ~10478 行 / 6343 行（17 模块） |
| WebSocket 消息 / 技能分支 | 46 / 33 |
| 分支提交 / 改动文件 | 4 / 32（+9614 −29） |
| 资源图库 / `.map` / wav | 58 组 / 656 张 / 723 个 |
| `resources.json` | 35 KB，1437 条 URL |
| 地图加载 | 445 定义 / 2173 门 / 704 只怪；`0.map` 700×700 |
| 磁盘体积 | `Data` 1167.6 MB + `Map` 201.8 MB + `Wav` 189.1 MB ≈ 1.52 GB；`client_original` 441 MB |
| 理论必需 / 可裁 | ~835.7 MB / ~722.9 MB |
| 浏览器实测传输 | 首次 2.654 MB / 二次 2.650 MB（33 请求，登录界面） |
| 音频（§10） | 723 wav → 723 mp3；189.1 MB → 13.57 MB（13.9×）；714 编号 / 7 BGM / 15 缺失；索引 70.6 KB |
| 渲染（§11） | 4 个前端文件；离屏双缓冲 800×600；首屏纹理 231/231；进游戏亮度跳变 2 → 1（仅加载完成）、动画缺帧 10/470 → 0/288、移动期 HUD 清空 8 → 0 |
| 渲染（§12） | 2 个前端文件；地面缓存 1760×957（38×31=1178 格）；地面 drawImage 200+ → 1 次/帧；选角立绘缺失 6 → 0；强制平移 12 格往返像素差 0.23%；对齐残差静止 0.00 / 平滑卷动 avg 1.59 |

---

## 10. 音频链路（方案 B 全量，2026-11-09 追加）

### 10.1 设计前提

服务端和 WS 协议**零改动**：两个后端都不下发音效 id，但都已支持 `.wav/.mp3/.ogg` 的 MIME + Range 206 + CORS；`Connection._dispatch` 支持 `'*'` 通配 handler，所以音效完全可以由前端按 WS 消息本地映射，Java 版也能直接用。

`resources/Wav/sound.lst` 就是原版 Delphi `SoundUtil.pas::LoadSoundList` 读的编号表（GBK，1001 行：714 条有效 / 15 条指向不存在的文件 / 252 条空值 / 20 行注释），`s_*` 常量可直接套用。723 个 WAV 全是标准 PCM，ffmpeg 4.3.1 自带 libmp3lame；全量转码后 **189.1 MB → 13.57 MB（13.9×）**，8 并发耗时 19.8s。

### 10.2 文件与命令

| 文件 | 作用 |
|---|---|
| `scripts/convert_audio.py` | 离线构建：解析 GBK 编号表、去重转码、生成索引；幂等按 mtime |
| `frontend/audio/audio_map.json` | **提交进仓库**的 70.6 KB 索引（不含二进制） |
| `frontend/audio/SoundManager.mjs` | 前端播放内核 + 事件映射 + BGM + 开关/音量 |
| `scripts/test_audio.mjs` | Node 22 零依赖前端回归（18/18） |
| `resources/Audio/*.mp3` | 723 个衍生资源，`.gitignore` 已忽略 `resources/`，**不提交** |

```bash
python scripts/convert_audio.py                       # 全量转码（幂等，已转且源未变则跳过）
python scripts/convert_audio.py --force               # 忽略时间戳全部重转
python scripts/convert_audio.py --check --probe 20    # 校验产出 + ffprobe 抽样解码
python scripts/convert_audio.py --explain 103         # 查编号 103 的 mp3 / wav URL 与文件大小
python scripts/convert_audio.py --self-test           # 12/12，临时目录自检（含真实 ffmpeg 转码）
node scripts/test_audio.mjs                          # 18/18，mock DOM/Audio 的前端链路
```

编码参数：短音效 `-ac 1 -ar 22050 -b:a 64k -codec:a libmp3lame`；>= 1 MB 的 BGM/长音频 `-ar 44100 -b:a 96k` 保留原声道。`ENCODER_VERSION` 变更会自动让旧产物全部失效重转。

### 10.3 索引与命名

`audio_map.json` = `{version, source, base: "/resources/", audioDir: "Audio/", sounds{id:{mp3,wav}}, bgm{key:{mp3,wav}}, files{wav文件:mp3文件}, missing[15], stats{}}`。

每个**唯一 wav 只转一份 mp3**：被编号表引用的用「引用它的最小编号」命名（`103.wav → Audio/103.mp3`），未引用的用文件名 slug（`Field2.wav → Audio/field2.mp3`）；多个编号指向同一 wav 时在 `sounds` 里指向同一份文件。BGM 不在 `sound.lst` 里，按 Delphi `bmg_*` 常量 + 磁盘真实文件名单独编 7 个语义键（intro / select / field / gameover / theme / heroLogin / heroLogout）。

### 10.4 前端链路

- `main.mjs` 里 `sound.install(conn)`：注册 `'*'` 监听、全局 capture 点击、`pointerdown`/`keydown` 首次手势解锁、异步加载索引；`window.__sound` 是调试入口。
- `play(id, {gap, key, volume})`：编号 → `/resources/Audio/<id>.mp3`；`<audio>` 元素池 + `cloneNode()` 并发，同类 80ms 节流、脚步统一 key 250ms 节流、并发语音 ≤ 24；mp3 404/解码失败自动回退 `Wav/<原文件>`，索引 404 时退化到 `resource.mjs::wavUrl(id)`。
- BGM 独立 `<audio>`：登录界面 intro、登录后/选角 select、**进游戏 play（原版 `s_main_theme` = 编号 102，43.9 s，每 46 s 重播、曲间静音 2.1 s，非无缝 loop）**、连接关闭 → intro；其余键（`field`/`gameover`/`theme`/`heroLogin`/`heroLogout`）用 `__sound.setBgm(key)` 手动切。原版依据与本次修正见 §15。
- 开关与音量存 `localStorage`（`mir2.sound.enabled/volume/bgmVolume`，默认开 / 0.8 / 0.45）；右上角「声音」按钮 `_toggleSound()` 已接 `sound.toggle()`。
- 全局点击命中 `data-sfx` 属性或 `cursor:pointer` 热区就播按钮音；背包格 / 装备格按 `stdMode` 带 `data-sfx`，快捷栏格带 108，喝药播 107。

### 10.5 事件 → 音效编号

| 事件 | 编号 | 原版常量 |
|---|---|---|
| 按钮点击 / 背包物品点击 | 103 / 按 stdMode：108,111,112,113,114,115,116,118 | `s_norm_button_click` / `ItemClickSound` |
| 快捷栏点击 / 使用药品 | 108 / 107 | `s_click_drug` / `s_eat_drug` |
| 自己移动（走 / 跑） | 1,2 / 3,4 交替 | `s_walk_ground_l/r`、`s_run_ground_l/r` |
| 自己攻击 | 52；`swordHit` 7→56、12→132、25→133、26→137 | 攻杀/刺杀/半月/烈火 |
| 自己受击 / 死亡 | 138 / 144 | `s_man_struck` / `s_man_die` |
| 他人攻击（环境音） | 50 | `s_hit_short` |
| 施法 | 火系 137；雷/冰 8301/8302/8226；辅助 101；其它 118 | 近似表 `MAGIC_SFX` |
| 金币增加 / 掉落金币 | 106 | `s_money` |
| 其它物品掉落 | 92 | `s_drop_stonepiece` |
| 传送现身 / 换图 | 110 | `s_spacemove_in` |

### 10.6 踩过的坑

1. **autoplay 策略**：首次用户手势前 `play()` 会抛 `NotAllowedError`。用 `once` 的 pointerdown/keydown 解锁 + 静音 WAV data URI 试播，登录按钮是天然解锁点。
2. **编号表细节**：GBK 编码、`;;` 注释、252 个空值、15 个指向缺失文件的编号要跳过并记日志；`130/132/133/137` 指向 `M7-1.wav`/`M12-1.wav` 这类**非数字文件名**，`wavUrl(132)` 找不到，必须走索引。
3. **BGM 大小写/空格**：`Game over2.wav` 与 `Game-over2.wav` 是两份文件，Delphi 常量用空格版；脚本按磁盘实际文件名（大小写不敏感）解析，不要硬编码。
4. **ffmpeg 临时文件**：临时名若叫 `103.mp3.part` 会报 `Unable to find a suitable output format`；改成 `103.part.mp3` 并显式 `-f mp3`，成功后 `os.replace` 原子落盘。
5. **Range 206 分片不缓存**（§6.1 第 3 项）对音频不适用：mp3 交给 `<audio src>` 由浏览器自己处理，不走前端 scheduler，正好绕开这个缺陷。
6. **原版玩家施法音**来自 Actor 的 `m_nMagicStartSound` 等字段，本项目 `spellCast` 只带 `magicId`，所以 `MAGIC_SFX` 是**可调整的近似映射**，不是逐技能还原。

### 10.7 晚上自测清单（用户）

1. 服务在 8080（`python -m pyserver`），浏览器**硬刷新 Ctrl+F5**（`.mjs` 是 `no-cache`，但保险起见）。
2. F12 Console 应出现 `[sound] 索引已加载：714 个编号 / 7 首 BGM / 15 条缺失`。
3. 首次点击/按键后应出现 `[sound] 浏览器音频已解锁`，并听到登录 BGM（`Log-in-long2`）。
4. 控制台直接验证：`__sound.status()`、`__sound.play(103)`（按钮）、`__sound.play(106)`（金币）、`__sound.setBgm('field')`。
5. 登录成功 → 选角 BGM；进游戏 → 编号 102 主场景主题曲（约 44 秒一轮、曲间停约 2 秒，原版行为）；走两步、打一下怪、喝药、捡金币、点背包，逐项听声。可用 `__sound.setBgm('field')` 切回 191 秒的 `Field2` 长曲做 A/B 对比，各曲对照表见 §15.5。
6. 右上角「声音」按钮是**开关**：默认开启，所以**第一次点击是关闭**，再点一次恢复；日志会提示。音量用 `__sound.setVolume(0.5)` / `__sound.setBgmVolume(0.2)`。
7. 回退链路：临时移走 `resources/Audio/103.mp3`，点按钮应自动改播 `Wav/103.wav`；把 `frontend/audio/audio_map.json` 移走则退化到 `resources.json` 的 `wavUrl()`（非数字文件名音效会缺）。测完记得恢复。
8. HTTP 自查：`/audio/audio_map.json` → 200 `application/json`；`/resources/Audio/103.mp3` → 200 `audio/mpeg`；带 `Range: bytes=0-99` → 206 + `Content-Range`。

### 10.8 已知边界

- 15 个缺失编号（291/292/294/295/943/1503/1922/2103/2141/2151/9224/9234/10110/10111/10521）静默跳过。
- 玩家受击/死亡固定用男声 138/144：WS 消息没带性别，要女声改 139/145。
- 怪物叫声已实现（按 `Appr×10+200+偏移` 公式，见 §13.1「怪物叫声 MonSound」）；逐技能精细施法音仍为 `MAGIC_SFX` 近似映射（WS 仅带 `magicId`，非逐技能 `m_nMagicStartSound`）。
- **未做浏览器端真听 E2E**：本次验证是脚本自检 + Node mock + HTTP 实测，听感与场景切换由用户晚上自测确认。

---

## 11. 渲染防闪烁（2026-11-09 追加）

### 11.1 现象与实测

用户反馈「动画刷新时闪屏严重」。用 Playwright 无头 Chromium 连续抓帧（把 800×600 下采样成 100×75 / 200×150，逐帧比较平均亮度、黑像素比例、相邻帧像素差）复现，修复前：

| 阶段 | 平均亮度 | 亮度跳变 | 中心区最大差异 | 动画帧缺失 | HUD `clearRect` |
|---|---|---|---|---|---|
| 刚进游戏 1.5 s | 0.4 → 144.9 | 2 次（+90.4 / +54.1） | 0.894（89% 像素变） | 72/480 | 1 |
| 稳定静止 1.5 s | 110.2 恒定 | 0 | 0.004 | 0/460 | 0 |
| 移动 1.5 s | 103.4 → 110.3 | 0 | 0.169 | 10/470 | 8 |

结论：静止画面本身不闪，闪集中在 **加载期整屏由黑变亮且瓦片逐块冒出**、**动画切帧时纹理未就绪**、**移动时 HUD 每帧被清空重画** 三个时机。

### 11.2 四个根因

1. **直接在可见 canvas 上「清屏 → 逐块重绘」**：`PlayScene.render` 每帧 `fillRect(0,0,800,600)` 黑底，再画 200+ 瓦片与实体；GPU 合成可能把「清屏后尚未画完」的中间态呈现出来。
2. **纹理全按需懒加载**：`MapActor._getTex` / `ActorSprite._texture` 未就绪返回 `null` 就不画，首屏瓦片一块块出现、动画换帧时角色整人消失一帧；且失败占位永不重试，某些格子会永久黑块。
3. **异步加载期间仍每帧清黑**：`PlayScene.onEnter` 是 `async`，`mapActor.enter()` 完成前 `map` 为 null，`render` 依旧每帧清黑屏。
4. **HUD 的 async 清空空窗**：`_renderVitals()` / `_renderLevelNumber()` 先同步 `clearRect`，再 `await lib.getTexture(...)`；`_hudKey` 含 `x/y`，移动时每帧变化 → 每帧清空两个 DOM canvas，纹理未缓存时留下空白帧。

### 11.3 改动（4 个前端文件，服务端 / WS 协议零改动）

| 文件 | 改动 |
|---|---|
| `frontend/main.mjs` | 新增 `backCanvas`（800×600，`alpha:false`）；主循环 `render(backCtx)` → `ctx.drawImage(backCanvas,0,0)` 一次性 blit，浏览器永远只看到完整帧 |
| `frontend/map/MapActor.mjs` | `enter(mapId, cx, cy, onProgress)` 先 `map=null`，`loadMap` 后 `_prefetchVisible()` 一次性预取首屏地面/小地砖/对象（含动画对象全帧），全部就绪或 6 s 超时才挂 `map`；`_getTex` 失败占位 5 s 后可重试；新增 `_texVersion` |
| `frontend/actor/ActorSprite.mjs` | `getLayers` 逐层回退 `_lastByLib`（最近成功纹理），全缺时回退 `_lastLayers`，绝不整人消失；`_texture` 顺带预取同一图库后续 6 帧（动画帧连续编号） |
| `frontend/scene/PlayScene.mjs` | 加载期不清屏、保留上一帧并叠「正在加载地图资源 x/y…」提示；HUD 提前构建与地图预取并行；`_hudKey` 去掉 `x/y`（地图名/坐标改纯 DOM 文本、仅在变化时写）；`_buildHud` 预取 prguse 4/5/6/30..39 后置 `_hudTexReady`，`_renderVitals` / `_renderLevelNumber` 改**同步**读 `WIL.textures`，清空与重画在同一调用栈完成 |

### 11.4 修复后验证（同一套抓帧脚本）

- 加载期：黑屏带进度提示 → **一次性**切换到完整地图（仅 1 次亮度跳变，约第 21 帧 / 0.35 s），不再逐块冒出。
- 稳定 / 移动：亮度跳变 **0**；强制走路动画 288 次 `getLayers` → **0 次 null**（修复前 10/470）；HUD `clearRect` 在移动期间 **0 次**（修复前 8 次）。
- 首屏地图纹理 **231/231 全就绪**；血球 canvas 8740 个非透明像素、等级数字有内容；无 console / page error。
- 双缓冲未冻结画面：强制平移视口 2 格后可见 canvas **62% 像素变化**；帧间隔稳定 16.7 ms。
- 回归全绿：全部 `frontend/**/*.mjs` 语法检查、`test_audio.mjs` 18/18、`test_pyserver` 48 PASS、`test_all_messages` 18/18、`test_tick_systems` 53/0、`verify_resources --self-test` 10/10、`convert_audio --check --probe 20` 723/723 + 20/20 可解码、`git diff --check` 通过。

### 11.5 复测命令与边界

诊断脚本放在 `data/`（`.gitignore` 忽略，不提交），依赖本机 `F:\funasr\.venv\Scripts\python.exe` 的 Playwright 与系统 Chrome，用 `player` / `123456` / `TestHero` 自动登录抓帧：

```bash
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe.py        # 亮度/差异 + idle.png / walk.png
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe2.py       # 高分辨率 + 动画帧缺失 / HUD 清空计数
"F:/funasr/.venv/Scripts/python.exe" data/doublebuffer_check.py   # 验证 blit 每帧生效
```

- 浏览器必须 **Ctrl+F5 硬刷新** 才能加载新的 ES module。
- 预取让进游戏多约 0.3~0.5 s（显示加载进度，不再是黑屏）；6 s 超时兜底，超时未就绪的格子交给 `_getTex` 按需重试。
- 双缓冲每帧多一次 800×600 blit，GPU 开销略增，换的是「无中间态」。
- 静态地面层离屏缓存当时**未做**（每帧逐块画 200+ 次 `drawImage`）——**已在 §12 完成**，改为按世界格原子的离屏滚动缓存，每帧只贴一次。
- 真实带 GPU 浏览器的观感 E2E 仍需用户确认；本次是无头 Chromium 的定量对比。

---

## 12. 选角闪烁与移动阴影跳变（2026-11-09 第三轮，追加）

### 12.1 用户反馈与实测定位

用户反馈：「好一些，不过人物选择还是闪烁厉害；树和建筑的阴影部分移动时颜色还是会跳变」。用 4 个新诊断脚本定位（都在 `data/`，`.gitignore` 忽略）：`flicker_probe3.py`（选角 110 帧 + 地图 idle/moving 140 帧）、`shadow_probe.py`（追踪固定世界格 RGB + bng/mid/obj 三层就绪状态）、`floor_scroll_check.py`（强制平移 12 格 + 往返像素对比）、`aligned_residual_check.py`（补偿位移后的逐帧残差）。

- 选角界面首圈 110 帧里 `_chrTex` 返回 null **6 次**：10 帧站立动画每 300 ms 换一帧，首圈每帧都要重新发 Range 请求，未就绪就跳过 `drawImage` → 人物一帧帧消失。全部就绪后稳定（`chrNull=0`）。
- 地图静止时固定世界格颜色 **0 跳变**、三层 `readyChanges=0`，说明已加载的格子没问题；问题在**新进入视野的格子**：修复前移动期 `_getTex` 返回 null 59 次，`tiles/957`、`objects/8105`、`objects6/1110` 等都是「首次加载缺失」。
- 原绘制是「先所有 `bng` 大地砖、再所有 `smtiles` 小地砖」两遍循环、各自 `_getTex` 各自就绪就画 → 同一格会先显示**亮地砖**、随后突然叠上**暗阴影 / 树影**，这就是「阴影颜色跳变」；走出首屏预取范围（±10 格）后必现。
- 补充排除：8 位调色板 alpha 只有 0 / 255（无半透明阴影色），所以跳变不是 alpha 混合，而是**整块贴图延迟出现/缺失**；`shiftX/shiftY` 已 `Math.round` 整数化，瓦片绘制坐标也是整数，不是亚像素插值。

### 12.2 根因

1. **选角**：`SelectCharScene._chrTex` 懒加载且无帧回退；背景 `Prguse[65]` 也在 `render` 里首次触发异步加载，加载期每帧清黑。
2. **地面逐层异步到位**：`tiles`（96×64 的 2×2 大图）与 `smtiles`（48×32 阴影/装饰）分两遍循环，没有「按格原子提交」概念。
3. **不做移动增量预取**：`_prefetchVisible` 只在 `enter()` 跑一次，走远后新格子全靠 render 内按需加载。
4. **对象层同理**：动画对象当前帧未就绪时整棵树/建筑消失一帧；`lighter` 混合用全局赋值再还原，异常路径可能泄漏；`getLib()` 返回空时占位永远不标记失败（如 `objects6/1040`）。
5. 视野内只有 1 个动画对象（`(307,637)`，`blend=true`，10 帧），且在屏幕外；动画帧切换不是本次跳变主因。

### 12.3 改动（2 个前端文件，服务端 / WS 协议零改动）

| 文件 | 改动 |
|---|---|
| `frontend/ui/SelectCharScene.mjs` | `_preload()` 预取背景 + `_ensurePortraits()` 按实际角色 job/sex 一次并发预取 10 站立帧 + 冻结帧（无角色时预取武士男/女两组）；`_wanted` / `_checkPortraits()` 门控动画帧推进；`_chrTex(idx, slot)` 逐槽回退最近成功帧、失败 5 s 重试；背景未就绪时**不清屏、保留上一帧 + 加载提示**；`_prgUrl` 加并发去重，避免同一张图重复 PNG 编码 |
| `frontend/map/MapActor.mjs` | 新增**地面层离屏滚动缓存** `_ensureFloor` / `_floorTex` / `_paintFloorCells` / `_syncFloor`：1760×957 画布以世界格窗口为坐标，`cells[]` 标记每格已完整绘制；每格只在 bng 象限 + mid **全部就绪**时一次性画入，视口滚动时把旧内容按格差平移复用、只补新行列；每帧只 `drawImage` 一次（原 200+ 次）。新增 `_maybePrefetch` / `_prefetchAround`：中心移动 ≥4 格时按格原子预取 ±(halfW+6) / ±(halfH+8)，24 格一批、批间让出主线程、单请求 8 s 兜底。新增 `_getObjFrame`：动画对象当前帧未就绪回退该对象已加载的最近帧；`lighter` 改 `ctx.save()/restore()`；`getLib` 为空时明确标记 failed |

窗口冗余 `PX=10, PY=8` 是必须的：`ox = floor((800-48)/2) = 376`、`oy = floor((445-32)/2) = 206`；若 `DX/DY > 0`，屏幕左/上缘会露黑底。实测 `PX=4` 时 `DX=+88`，截图出现黑色竖条；改 10 后 `DX=-200 / DY=-146 / right=1560 / bottom=811`，完整覆盖 800×600。

### 12.4 修复后验证

- **像素级等价**：静止截图 `map_after2.png` 与修复前 `map_idle.png` **MD5 完全相同**，证明 bng 象限裁剪 + 滚动缓存没有改变正常画面。
- 选角：`chrNull 6 → 0`；立绘缓存 22/22 全就绪；`SELECT full` 亮度跳变 0、hard diff max 0.0005。
- 移动期 `top null keys` 从「多个首次加载缺失」变成只剩 `objects6/1040`、`objects6/1055` 两个**永久空图**（1.80 客户端 WIL 里就是空索引），不再有新格子的临时缺失。
- 强制平移 **12 格**：`painted 1178/1178`、无黑块/错位、截图目视正常；往返 12 格后与初始画面像素差 **0.23%**（滚动复用无累积误差）。
- 双缓冲仍生效：强制平移后可见 canvas **61.6%** 像素变化；静止帧差 0；`flicker_probe2` 加载期跳变 1、settled/moving 0、动画缺帧 0/188。
- 对齐平移残差：静止 **0.00**；连续平滑卷动 avg **1.59**、8 个尖峰（25~33）周期约 0.4 s，来自角色/动画对象**正常帧切换**，不是纹理加载突变。
- 回归全绿：全部 `frontend/**/*.mjs` 语法检查、`test_audio.mjs` 18/18、`test_pyserver` 48 PASS、`test_all_messages` 18/18、`test_tick_systems` 53/0、`verify_resources --self-test` 10/10、`convert_audio --check --probe 20` 723/723 + 20/20 解码、`git diff --check` 通过。

### 12.5 复测命令与边界

```bash
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe3.py         # 选角 + 地图 idle/moving 抓帧
"F:/funasr/.venv/Scripts/python.exe" data/shadow_probe.py            # 固定世界格 RGB / 三层就绪状态
"F:/funasr/.venv/Scripts/python.exe" data/floor_scroll_check.py      # 强制平移 12 格 + 往返像素对比
"F:/funasr/.venv/Scripts/python.exe" data/aligned_residual_check.py  # 对齐平移残差
```

- 浏览器必须 **Ctrl+F5 硬刷新**（ES module 有缓存）。探针用 `player` / `123456` / `TestHero` 自动登录；该角色当前在 `(312,605)`，向东是墙，所以平移验证脚本用**强制改 `_viewX`** 而不是发 `move` 消息。
- 地面缓存画布 1760×957 ≈ 6.7 MB，`cells` 38×31=1178 格；换图时整个丢弃重建。
- 增量预取范围 ±(halfW+6) / ±(halfH+8)，每批 24 格并让出主线程，不会卡渲染；极端快速长距离传送仍可能看到边缘格子短暂黑色，但只会「黑 → 完整」一次，不会再「亮 → 暗」两次变色。
- 树/建筑的**对象层**仍每帧按行绘制以保持遮挡关系，未缓存；动画对象帧切换（每 200 ms）是原版行为，观感上仍会有轻微明暗变化。
- 真实带 GPU 浏览器的观感仍需用户确认；本轮全部是无头 Chromium 定量对比 + 截图像素比对。

---

## 13. 保真度补齐（2026-11-09 第四轮，追加）

独立 Agent 对照原版 Delphi `GameOfMir/Client`（与 `GameSrv`，若含）做了系统性差异评估，结论是**高保真移植**：动画帧表/帧推进、调色板/透明/加色、地图三层渲染与深度排序、命中/伤害/暴击数值公式、音效编号与 BGM 均已对齐。本轮只修评估中标注为「中风险、观感/数值偏差」的 3 项（+1 个边界一致性），均为局部改动。

### 13.1 三项修复

| 项 | 根因（Delphi 对照） | 改动 | 文件 |
|---|---|---|---|
| **命中帧时序** | 原版在攻击动作第 2~3 帧（武器接触点，约 170ms，`HA.hit` 6 帧×85ms）才结算命中；此前 `attack` 消息一到达就触发目标受击/伤害飘字/命中特效，观感「先爆后砍」 | 新增 `HIT_FRAME_MS=170`；目标侧 `struck`+伤害飘字+命中特效改 `setTimeout(HIT_FRAME_MS)` 延迟触发（用 `cur=entities.get(targetId)` 取当前坐标，避免目标移动后落点错）；攻击者武器辉光 `_swordHitAt` 顺延 `+HIT_FRAME_MS` | `frontend/scene/PlayScene.mjs` |
| **静态 blend 降级** | 原版 `DrawScrn` 对 `boBlend`（0x80 标志）一律 `lighter` 加色，**与是否动画无关**；此前 `blend` 仅在 `hasAni` 时赋值，导致非动画的火/光类静态贴图丢失辉光 | `const blend = !!t.blend` 与动画解耦；动画帧裁剪改为 `Math.max(1, Math.min(32, t.aniFrame))`，与 `_prefetchAround`/`_getObjFrame` 的 32 帧上限一致（消除 >32 帧对象循环到前 32 帧的缺帧闪烁） | `frontend/map/MapActor.mjs` |
| **等级上限冻结** | `world.py` 早期只抄了 Java `ExperienceTable.NEED_EXPS` 前 65 项，`exp_for_level`/`level_for_exp` 被钳到表尾 → 65 级后经验需求冻结、满级≈65 | `NEED_EXPS` 补全到与 Java **逐值一致**的 101 项（index 0→等级1 … index 100→等级101，末值 2140000000）；`exp_for_level(101)` 校验值不变、升级阈值按原版推进到 101 级 | `pyserver/world.py` |
| **未命中 MISS 表现** | 原版 `SM_STRUCK` 携带 miss 标志，目标位置飘 MISS、不扣血、不播受击；此前服务端 miss 时只给攻击者发系统提示、目标端完全无感（且攻击玩家分支无命中判定、永不 miss） | `_attack_monster` 未命中时额外 `broadcast_around(attack, miss=True)`（不扣血/不 kill/不 send_stats），主击与半月/刺杀第二击两处均补；前端 `attack` 处理：miss 时攻击者不点命中辉光、目标端飘红色 "MISS" 且不播 `struck`；`_spawnDamageNumber` 支持自定义颜色 | `pyserver/handlers_combat.py` + `frontend/scene/PlayScene.mjs` |
| **WIL 末张图位深** | 末张图无 `nextOff`，原回退值 `fileOff+8+width*height` 永远判 8 位；若末张实为 16 位 RGB565 会只读半截而整图花屏 | 末张图 `nextOff` 改用真实 `this.fileSize` 判定位深（与非末张统一），8/16 位均正确 | `frontend/gfx/wil.mjs` |
| **怪物叫声 MonSound** | 原版 `sound.lst` 约定怪物声音索引 = `Appr×10+200`，六态偏移 0 发现/1 站立/2 攻击喊/3 攻击武器/4 挨打/5 死亡；此前缺这份映射，怪物完全无声 | 采用网上多源一致的公式（传奇论坛/博客园/upx8 Wiki 均确认）；前端 `registerEntity` 把实体 `appr` 注册到 `SoundManager`，战斗 `attack`/`death` 消息按公式播放 **攻击(+2)/挨打(+4)/死亡(+5)**；索引不在 `audio_map.json` 的怪物自动静默（不 404、不报错）；玩家砍怪只播怪挨打声、怪咬玩家同时播玩家被击+怪攻击喊、怪咬怪双方各发声、冲撞顶怪只播怪挨打声 | `frontend/audio/SoundManager.mjs` + `frontend/scene/PlayScene.mjs` |

### 13.2 验证

- 经验表：`len(NEED_EXPS)=101`；`exp_for_level(1)=100 / (65)=1700000000 / (101)=2140000000 / (200)=2140000000`，与 Java `ExperienceTable` 逐值一致；`level_for_exp` 现循环 1..101 全表，不再停在 65。
- 前端：`node --check` 两个改动文件均通过；Playwright 实烟（自动登录 `player/123456/TestHero` 进图）**无 console/page 错误**，画面非黑占比 92.9%，地图 700×700、271/276 纹理就绪 —— 证明 blend 解耦与命中帧改动不破坏渲染。
- 回归全绿：`test_pyserver` 48 PASS / `test_all_messages` 18/18 / `test_tick_systems` 53/0 / `test_audio` 18/18 / `compileall` 通过 / `git diff --check` 通过。
- 服务已重启（原 PID 29560 停用，新进程 `python -m pyserver` 已就绪，`/api/migration` 46/46，`onlinePlayers=0`），`world.py` 改动已生效。
- MISS 通道 + WIL 末张图：live 回归 18/18 通过（攻击怪物循环重试命中，miss 广播不破坏「造成伤害」断言）；`node --check` 全部前端改动文件通过；WIL 末张图位深改用真实 `fileSize` 判定，8/16 位均正确。
- 怪物叫声：用真实 `Monster.csv`(273 种) + `audio_map.json` 量化，`Appr×10+200+{2,4,5}` 覆盖 → **191 种三音齐全、20 种部分、62 种资源无索引自动静默**；node 直接 import `SoundManager` 跑路由单测 6/6 通过（玩家砍怪/怪咬玩家/怪咬无音怪/怪死亡/无音怪死亡/冲撞顶怪）。

### 13.3 仍属「已知边界、未在本轮处理」（评估认定的低/中风险残留）

- 战斗「0 伤害」仍保留 `max(1,…)`（与 Java 版对齐的简化；原版防御>攻击可显 0 伤害，需另补 0 伤害通道）；**攻击玩家分支仍无独立命中判定（PvP 永不 miss）**——与攻击怪物分支不一致，属 PvP 行为级改动，本轮未动，待用户确认是否对齐原版。MISS 通道已补齐（见 §13.1 第二项）。
- 施法音为 `MAGIC_SFX` 近似映射（火系→137、雷/冰→8301/8302/8226，非逐技能）；受击/死亡固定男声（WS 未带性别）。**怪物叫声已实现**（见 §13.1「怪物叫声 MonSound」），逐技能精细施法音仍近似。
- 网络层无单连接频率限流与 `maxPlayers`/地图人数硬上限（`net.py` 仅队列 512）；冲撞(rush)无专属身位动画；受击姿势比原版多保持 ~300ms。
- 树/建筑的**对象层**仍每帧按行绘制（保遮挡）；动画对象帧切换（每 200ms）是原版行为，观感仍有轻微明暗变化。
- 真实带 GPU 浏览器的命中帧观感、blend 辉光观感、MISS 飘字观感仍需用户确认；本轮是无头 Chromium 定量对比 + 截图像素比对。

### 13.4 提交

见 `git log --oneline -1`（最新一条 `feat(python-server): ` 提交，4 个文件：2 前端 + `world.py` + 本 handoff；怪物叫声轮为 2 前端 + 本 handoff，服务端零改动）。

---

## 14. 进游戏阻断修复：Python 3.10 + slots dataclass + 零参数 super()（2026-11-09 夜，实测反馈）

### 14.1 症状
选择角色 → 弹出公告(notice) → 点「确定」后卡住、进不了游戏；服务端日志：
`[ws] handler noticeOk failed: TypeError: super(type, obj): obj must be an instance or subtype of type`
（`service.py:170` 的 handler 兜底吞掉异常、只打一行摘要，客户端表现为「没反应」）

### 14.2 根因
- **8080 服务实际运行于 `D:/Python310/python.exe`（Python 3.10.11）**；此前所有复现/回归都用 `C:/Users/Mao/.workbuddy/binaries/python/versions/3.13.12/python.exe`，因此一直没复现，历史 live 回归也全绿。
- `world.py` 的 `Actor`/`Player`/`Monster` 都是 `@dataclass(slots=True)`。`slots=True` 会**重建类对象**；Python 3.10~3.12 不修正零参数 `super()` 捕获的 `__class__` cell（仍指向重建前的旧类），3.13 才修好。
- 实测 3.10：`Player.brief_json.__closure__[0].cell_contents is Player` → **False**；于是 `super().brief_json()` 等价于 `super(旧Player, 新Player实例)` → 抛上述 TypeError。`Player.brief_json`（原 278 行）与 `Monster.brief_json`（原 565 行）两处均中招。
- `_notice_ok → _do_enter → map_snapshot / broadcast_entity_add` 都会调 `brief_json()`，异常被吞 → `enterGame` 快照发不出去 → 卡在公告弹窗。

### 14.3 修复
两处零参数 `super().brief_json()` 改为显式 `Actor.brief_json(self)`（跨 3.10~3.13 语义一致，不依赖 `__class__` cell）。仅改 `pyserver/world.py`，协议/前端零改动。
已排查：全库 `super(` 调用只这两处；所有 `@dataclass(slots=True)` 中只有 `Player`/`Monster` 带继承，无其他同类隐患。

### 14.4 验证
- 3.10 直接调 `Player.brief_json()` / `Monster.brief_json()` 均正常；离线 `noticeOk` 全链路发出 `enterGameResult`/`enterGame`/`stats`/广播。
- 双解释器回归：`test_pyserver` 48/48、`test_tick_systems` 53/0（3.10 与 3.13 各跑一遍）。
- 服务用 3.10 重启（`/api/status` 正常、`/api/migration` 46/46），`test_all_messages --live 8080` 18/18。
- **真实 WS 端到端**（账号 `player/123456/TestHero`）：`login → listCharacters → enterGame(→notice) → noticeOk`，收到 `enterGameResult`/`enterGame`/`stats`/`entityAdd`/`move`；`enterGame` 快照含 `player=TestHero`、`nearby=4`、`map=0`，服务端日志无 handler 异常。

### 14.5 运行环境约定（重要）
服务端实际解释器是 **`D:/Python310/python.exe`（3.10.11）**，直接 `python -m pyserver` 即用它。**所有复现、回归、live 测试必须同样用 3.10 跑**；只用 3.13 会掩盖 3.10 专属的语言/标准库差异（本次即因此漏检）。

---

## 15. 进游戏 BGM 对齐原版 Delphi（2026-11-09 夜，用户反馈「登录游戏的音乐貌似也不对」）

### 15.1 原版权威依据（GameOfMir/MirClient Delphi 源码）

| 源码位置 | 原版行为 | 对应资源 |
| --- | --- | --- |
| `SoundUtil.pas` 常量 | `bmg_intro='wav\log-in-long2.wav'`、`bmg_select='wav\sellect-loop2.wav'`、`bmg_field='wav\Field2.wav'`、`bmg_gameover='wav\game over2.wav'`；`s_main_theme=102` | — |
| `IntroScn.pas::TLoginScene.OpenScene` | `PlayBGM(bmg_intro)` | `Log-in-long2.wav`（34.3 s） |
| `IntroScn.pas::TSelectChrScene.SoundOnTimer` | `PlayBGM(bmg_select)` | `sellect-loop2.wav`（43.9 s） |
| `PlayScn.pas::TPlayScene.SoundOnTimer` | **`PlaySound(s_main_theme)`**，源码注释「游戏主场景的背景音乐（长度：43秒）」，首次播放后 `m_MainSoundTimer.Interval := 46*1000` → 每 46 秒重播一次 | **`sound.lst` 第 102 项 `wav\102.wav`（43.9 s，单声道 44.1 kHz）** |
| `DrawScrn.pas::ChangeScene` | 切场景先 `CloseScene`（`SelectChrScene`/`PlayScene` 均 `SilenceSound`）再 `OpenScene` | — |

关键结论：在 `IntroScn.pas` / `PlayScn.pas` / `DrawScrn.pas` / `clEvent.pas` / `ClMain.pas` 中**均没有 `PlayBGM(bmg_field)` 调用点**；`Field2.wav`（191.5 s / 32.2 MB，社区资料称之为「SD 后来放进去的」）不是原版进游戏默认曲。原版主场景音乐是**编号 102 的 43 秒主题曲**，且不是无缝循环，而是 46 秒定时器重播（曲间约 2 秒静音）。

### 15.2 修正前的问题

`SoundManager._onMessage` 的 `enterGame` 分支是 `setBgm('field')` → 播 `Field2.mp3`（191.5 s 无缝 loop）。登录/选角两首是对的，只有**进游戏这一首错**：曲子长 4 倍、旋律不同、且没有原版的曲间停顿。上一轮 §14 修好 `noticeOk` 后用户第一次真正进到游戏，才听到这首曲子并反馈「不对」。

### 15.3 改动（仅 `frontend/audio/SoundManager.mjs`，服务端/协议/索引文件零改动）

- 新增常量 `BGM_PLAY_ID = 102`、`BGM_PLAY_GAP_MS = 2100`（46 s − 43.9 s）。
- `_loadMap()` 从 `sounds['102']` 派生 `bgm.play`（原版主场景音乐本来就是 `sound.lst` 编号，不在 BGM 表里；避免改 `convert_audio.py` 与已提交的 `audio_map.json`）。派生后 BGM 键为 8 个。
- `_startBgm()`：`play` 键不设 `loop`，改用 `onended` + `setTimeout(2100)` 从头重播，复刻原版 46 秒周期；其它键仍是 HTML5 无缝 loop。切歌/关闭声音时清理重播定时器。
- `enterGame` → `setBgm('play')`；`field` 保留为可手动切换的键。

### 15.4 验证

- `node` 直接 import `SoundManager` + mock `Audio`/`fetch` 的真实路由单测 **12/12 通过**：索引派生 `play→102.mp3`、`loginResult`/`characterList`→select、`enterGame`→play 且 `loop=false`、曲终排 2100 ms 重播并从头播放、`setBgm('field')`→Field2 无缝 loop、`toggle()` 清定时器。
- `/resources/Audio/102.mp3` 实测 **HTTP 200 / audio/mpeg / 527300 bytes**（`field2.mp3` 同样 200）。mp3 与原 wav 时长逐一比对一致（`102`: 43.9 s、`Field2`: 191.5 s、`Log-in-long2`: 34.3 s、`sellect-loop2`: 43.9 s），排除转码错文件/变调。
- 按 §14.5 约定用 **Python 3.10** 跑：`test_pyserver` 48/48、`test_tick_systems` 53/0、`test_all_messages --live 8080` 18/18；8080 服务无需重启（纯前端改动）。

### 15.5 曲目对照（供自测 A/B 试听，控制台 `__sound.setBgm('<key>')`）

| key | 文件 | 时长 | 用途 |
| --- | --- | --- | --- |
| `intro` | `log-in-long2.mp3` | 34.3 s | 登录界面（默认）；与 `Game-over2.wav` **MD5 完全相同** |
| `select` | `sellect-loop2.mp3` | 43.9 s | 选角界面（默认） |
| `play` | `102.mp3` | 43.9 s | **游戏内（本次新默认）**，原版 `s_main_theme`，46 秒周期 |
| `field` | `field2.mp3` | 191.5 s | 后期附加长曲（修正前的默认值） |
| `theme` | `main_theme.mp3` | 36.0 s | 社区「经典选角曲」候选 |
| `gameover` | `game_over2.mp3` | 68.6 s | 社区「经典登录曲」候选 |

### 15.6 仍待用户确认（**未硬改**）

若「不对」指的是**登录/选角那两首不是记忆中的经典旋律**：那是资源包内容问题，不是代码映射错误。博客园/17173 的玩家改名方案是把 `Game over2.wav` 当 `Log-in-long2.wav`、`main_theme.wav` 当 `sellect-loop2.wav` 使用（即上表 `gameover` / `theme` 两键）。本轮**不擅自替换默认曲**，因为无原版源码依据；用户可用上表命令试听后指定，再把对应键设为默认即可。

---

## 16. 四项实测反馈（建角选职业 / 连点无动画 / 条带黑条 / 大地图与系统菜单）（2026-11-09 第五轮）

用户一次性反馈 4 项，要求「如果上面功能是缺失，就参考 delphi 版本补上」。核查结论：**4 项都不是「功能不存在」，而是可见性 / 节流 / 缓存 / 入口四类实现缺口**；服务端 `pyserver/**` 与协议本轮零改动，全部改在前端（+ 一处测试断言）。原版依据：`IntroScn.pas::MakeNewChar / SelChrNewJob / SelChrNewm_btSex`、`FState.pas::DCreateChr`、`ClMain.pas` 的 `FormKeyDown`（TAB / ESC / F9-F11）与 `ViewMiniMapStyle`。

### 16.1 四项修复对照

| 用户问题 | 实际根因 | 改动 | 文件 |
| --- | --- | --- | --- |
| **1. 创建人物时如何选职业?** | 按钮与 `createCharacter{job,sex,hair}` 参数一直存在，但 ① `DCreateChr` 背景图用 `z-index:-1`，父容器未建立层叠上下文 → 整张面板（含职业按钮底图）掉到游戏 canvas 后面，只剩几个「按下态」小图标浮在人物身上；② 选中态不保持、无文字状态、点职业后立绘不换 | 背景图改 `z-index:0` + `pointer-events:none`（与 `PlayScene` NPC 对话框的既有做法一致）；新增 `_refreshCreateSelection()` / `_setJob()` / `_setSex()` / `_setHair()`，选中按钮保持按下态、底部显示「职业／性别／发型」、按钮加 `title` 悬停提示；`_ensurePortraits()` 在建角模式预取当前职业/性别的 10 帧站立 + 冻结帧；`render()` 在建角槽实时绘制所选职业/性别立绘；操作提示移到性别与发型按钮之间的空白带（原 `top:131` 会压住面板自带「职业」二字） | `frontend/ui/SelectCharScene.mjs` |
| **2. 快速点击多次，人物坐标还在位移但不播动画** | `_onMouseDown` 每次点击把 `_lastMoveAt` 归零，绕过 `WALK_INTERVAL` / `RUN_INTERVAL` 节流 → 连点灌入 N 条 `move`，服务端无 cooldown 连续推进；镜头匀速追赶要花 N×间隔，固定 600 ms 动画超时却先把动作切回 `stand` | 新增 `_pendingStep` 布尔：距离上次发送不足一个间隔时只挂 1 个待发步骤（N 次连点最多补 1 步），与按住移动共用 `_lastMoveAt` 节流；render 新增 `selfCatchingUp` 判定，镜头仍在追逻辑坐标时不把自身 `walk/run` 切回 `stand` | `frontend/scene/PlayScene.mjs` |
| **3. 地图出现「一条一条、带间隔」的黑条** | `_floorTex()` 把 `failed` 纹理当成 `ready`，`_paintFloorCells()` 随即把该格涂成纯黑并 `cells[idx]=1` **永久锁定**；首屏预取超时 / Range 抖动留下的 failed 占位就是那些间隔黑条，且永远不会恢复 | 兜底底色 `FLOOR_FALLBACK='#1e1a12'`（初始填充不再是 `#000`）；`failed` 超过 5 秒删占位重新 `_getTex()`；未就绪格改 `_borrowFloorCell()` 借用相邻已绘制格像素且**不写 cells=1**，纹理到位后下一帧覆盖；`tiles` 图不足 96×64（象限越界）时整图铺满当前格 | `frontend/map/MapActor.mjs` |
| **4. 如何看大地图？如何打开系统菜单？** | 只有 HUD 按钮，`_onKeyDown` 未绑定 Tab / V / Esc / F9 / F10 / F11，注释甚至写着「不接快捷键」；没有大地图与系统菜单面板 | 按原版补 `Tab` 三态（关→小地图→大地图→关）、`V`（关→半透明→不透明）、`Esc`（先关最上层窗口，无窗口则开系统菜单）、`F9` 背包 / `F10` 状态 / `F11` 技能；新增 DOM 覆盖层大地图（复用 `mmap` 底图，标白=自己／红=怪／黄=NPC／蓝=玩家）与系统菜单；模态面板首次点击只关闭不穿透成移动/攻击；进游戏给一行快捷键提示；`onExit` 清理新增状态 | `frontend/scene/PlayScene.mjs` |

### 16.2 顺带修掉的两个既有缺陷（都是「验证暴露出来的真问题」）

1. **UI 贴图偶发整片空白**：`wil.mjs::createCanvas()` 原先一律返回 `OffscreenCanvas`。实测无头 Chromium 下 `convertToBlob()` 会抛 `NotReadableError: Readback of the source image has failed`，且**同一画布重试 4 次仍失败**、内容全透明；`Prguse[62]/[64]`（确定/关闭按钮）因此曾完全空白，建角窗口的 e2e 也被这个异常卡住。主线程全部改用 `HTMLCanvasElement`（`toDataURL()` 读回稳定、同样可作 `drawImage` 源），仅在无 `document` 的 worker 里保留 `OffscreenCanvas`。诊断数据：修复后 11 张 Prguse 图全部拿到正常像素（`62`: 2505 可见像素 / `64`: 368），`pageerrors: []`。
2. **大地图标题显示成 `0`**：服务端 `snapshot.map.name` 是内部地图编号，中文名在 `map.title`（HUD 左下角用 `title || name`）。`_renderBigMap()` 统一为 `title || name`，现显示「比奇省　(332, 480)」。
3. **回归脚本里的 §15 遗留断言**：`scripts/test_audio.mjs` 仍断言 `enterGame → bgmKey='field'`，与 §15 已提交的 `play`（102 号主场景主题曲）冲突，导致 `17/18`。改为断言 `play` + `loop=false` + `102.mp3`，现 **18/18 PASS**。

### 16.3 验证

- **浏览器端到端**（`D:/Python39/python.exe` + Playwright，账号 `player/123456/TestHero`，8080 live）：**28 项全通过**，含
  - 建角：`_openCreate(1)` 后 9 个按钮齐备、初始高亮武士+男、切「道士/女」后状态文字与高亮同步、`_wanted` 含新职业 10 帧、关闭窗口恢复、面板背景/按钮底图可见、提示文字不再压住面板文字；职业/性别行切换前后像素差 1655 / 1741（可见反馈真实生效）。
  - 连点：10 次快速点击 → 立即发出 `move` **0** 条、`_pendingStep=true`、`_lastMoveAt` 未被归零；镜头差 3 格时 350 ms 后动作仍为 `walk`（`dx=2.39`），把镜头对齐后恢复 `stand`。
  - 黑条：地面缓存 1760×957 / 1178 格，采样纯黑像素占比 **0**；人为把 24 个可见纹理标记为 failed 后强制重绘，纯黑占比仍为 0、1178 格中 63 格保持未锁定（可重绘）、超 5 秒后 `_floorTex` 确实删占位并重新发起加载。
  - 大地图/菜单：Tab 三态、V 半透明、Esc 开关系统菜单、F9 开关背包；大地图标题「比奇省　(332, 480)」；**浏览器无 JS 异常**；截图 `_tmp_bigmap.png` / `_tmp_sysmenu.png` / `_tmp_create.png` 目视确认面板与立绘正常。
- **前端语法**：4 个改动文件 `node --check` 全通过；`node scripts/test_audio.mjs` **18/18**。
- **服务端回归**（按 §14.5 全部用 `D:/Python310/python.exe`）：`test_pyserver.py` **48/0**、`test_tick_systems.py` **53/0**、`test_all_messages.py --live 8080` **18/18 + ping→pong PASS**；`/api/migration` 仍 46/46。
- `git diff --check` 通过；`HANDOFF.md` 保持零改动。

### 16.4 已知边界（未硬改）

- 大地图/系统菜单用 DOM 覆盖层实现（原版是画面内绘制的自绘窗口），布局按 800×600 固定坐标；没有做窗口拖拽与分辨率自适应。
- 发型左右箭头只有「按下态」贴图，`Prguse[73]` 底图本身没有对应常态图案，所以不选中时看不见箭头；`hair` 参数服务端已接收但外观未逐发型区分。
- 真实带 GPU 浏览器的建角立绘切换观感、连点手感、黑条是否彻底消失仍需用户 `Ctrl+F5` 自测确认；本轮为无头 Chromium 定量 + 截图像素对比。
- 其余历史边界见 §13.3（PvP 永不 miss、逐技能施法音近似、rush 无专属身位动画等），本轮未动。

## 17. 六项实测反馈（BGM 交换 / 小地图 / 界面缩放 / 商店价格 / 选角文字 / 残留黑条）（2026-11-10 第六轮）

用户连续追加 6 条反馈，逐项定位后一次性落地。`HANDOFF.md` 仍保持零改动。

| 反馈 | 根因 | 改动 | 文件 |
| --- | --- | --- | --- |
| `__sound.setBgm('intro')` 听着像死亡曲、`gameover` 才像开场 | 本资源包 `Log-in-long2.wav` 与 `Game-over2.wav`（连字符）**MD5 完全相同**（34.3 s 死亡曲），空格版 `Game over2.wav`（68.6 s）才是玩家记忆中的开场曲；社区改包也正是把空格版改名当 `Log-in-long2` 用。Delphi `SoundUtil.pas` 常量本身仍是 `bmg_intro='wav\log-in-long2.wav'` | 按用户试听结论**只交换两个语义键指向的文件**，不改 Delphi 常量、不重新转码：`intro → game over2.wav / game_over2.mp3`，`gameover → log-in-long2.wav / log-in-long2.mp3`；同步自检断言与注释 | `scripts/convert_audio.py`、`frontend/audio/audio_map.json`、`scripts/test_audio.mjs`、`frontend/audio/SoundManager.mjs` |
| 小地图半透明和不透明「一样、都看不清」 | 0 号比奇省 700×700 格，101 号 mmap 底图只有 900×599；按写死的 1.5 px/格算，玩家 y=480 → `sy=660 > 599`，裁剪高度 **-61**，底图整块不画，两态只剩几个实体点（实测像素差 `0/19600`） | 改「纹理像素 / 地图格数」归一化换算，裁剪窗口以玩家为中心并**钳到底图边界**；不透明态垫深色底 + 描边，半透明态只降 alpha（`0.68`）让游戏画面透出；关闭时清空画布。大地图点位换算同步用同一口径 | `frontend/scene/PlayScene.mjs` |
| 整个界面能否等比例放大 | 原来 `#stage` 固定 800×600，窗口更大时四周留黑；且 flex 默认会压缩子项 | `#stage` 加 `flex:none` + `transform-origin:center`，`fitStage()` 按 `min(vw/800, vh/600)` 等比缩放（4:3 居中）；新增 `frontend/core/viewport.mjs` 统一换算，所有 `clientX/clientY` 与 `elementFromPoint` 都按 `rect.width/800` 折算；`window.__setUiScale(k)` / `localStorage['jootm2.uiScale']` 可固定倍率 | `frontend/index.html`、`frontend/main.mjs`、`frontend/core/viewport.mjs`（新）、`frontend/scene/PlayScene.mjs`、`frontend/gui/manager.mjs`、`frontend/gui/window.mjs` |
| 所有商店都是 10 块钱 | M2 `Market_Def` 脚本头 `%100` 才是「按 StdItems 原价出售」的倍率，`[goods]` 每行是 `物品名 库存数量 刷新时间`；**Java 版把第二列库存当成单价**直出，Python 直译后一样错，于是武器/首饰店清一色 10 | `NpcScript` 解析脚本头 `%N`（默认 100），`goods()` 价格改 `StdItems.Price × rate%`、第二列落到 `stock`；`_buy_price()` 沿用 goods 价。真实数据实测从「10 种价」变成 **49 种价**（木剑 50、青铜剑 900、金创药 80、强效金创药 540…） | `pyserver/npc_script.py`、`scripts/test_pyserver.py`、`scripts/test_all_messages.py` |
| 选角名字/职业/等级文字有偏移 | Delphi `IntroScn.pas` 的 `BoldTextOut` → Windows `TextOut(x,y)` 以**字顶**为 y；canvas 默认 `textBaseline='alphabetic'`，整行文字上浮约 10 px，正好压在背景虚线框上边框上 | `_text()` 显式 `textBaseline='top'` + `save/restore`，坐标改回 Delphi 原值：左槽 `117,493/522/552`、右槽 `671,495/524/554`（原代码各 +1）；建角预览同步 | `frontend/ui/SelectCharScene.mjs` |
| 地图还是有黑色条纹 | 第一轮 `failed` 锁死已修，但增量预取按 24 格分批提交，会出现**连续一整行纹理都未就绪**的窗口：四个邻格也都没画，`_borrowFloorCell()` 全部退化成 `#1e1a12` 近黑纯色填充，连成暗带；地图外格子仍涂 `#000`；每帧清屏也是 `#000` | 兜底地色调亮为 `#3a3222`；邻格都不可用再从 `f.lastPainted`（最近真正画过地砖的格）借像素；地图外改用地色；PlayScene 清屏改用地色；关闭/清空小地图画布 | `frontend/map/MapActor.mjs`、`frontend/scene/PlayScene.mjs` |

### 17.1 依据与边界

- `[goods]` 列义与 `%N` 倍率来自 M2 商人脚本文档（`Market_Def` 头 `%100` = 原价、`%150` = 1.5 倍；`[goods]` 行是 `物品名 数量 刷新时间`）+ 本仓库 283 个商店脚本每行都有 `%100` 的自证；**不是**凭空改数据。Java 源码 `NpcScriptEngine.goods()` 的 `// 物品名 价格 数量` 注释本身是错的，本轮未改 Java 源码（分支只维护 Python 端）。
- BGM 交换只动索引指向，Delphi 常量、`sound.lst` 编号 49 / 102 与 723 份 mp3 全部未改；如换回原版语义只需交换 `BGM_FILES` 两行。
- 界面缩放是 CSS transform，canvas 逻辑分辨率恒为 800×600，`image-rendering:pixelated` 保留像素味；DOM 文字会随缩放变平滑。缩放上限 4、下限 0.5。
- 黑条属于**纹理分批到达的瞬态**，无头 Chromium 正常帧无法复现；本轮用「人为把连续 4 行标记为未就绪」复现并验证不再出现暗带，真实 GPU 浏览器观感仍需用户 `Ctrl+F5` 自测确认。

### 17.2 验证（全部按 §14.5 用 `D:/Python310/python.exe` 跑服务端/回归，Playwright 用 `D:/Python39/python.exe`）

- 浏览器端到端 **23/23 PASS**：选角 `textBaseline=top` + 六个 Delphi 坐标；`intro/gameover` 索引与 `play=102.mp3`；小地图两态 `nonzero=14400/14400`、平均 alpha `173 vs 255`、关闭态 `0`；地面缓存纯黑像素 `0`，连续 4 行未就绪时纯黑 `0`、深色横带 `0`、20 格保持未锁定；商店面板显示 `50/900/1000/80 金币`；1200×900 缩放为 `[0,0,1200,900]`、鼠标换算 `(400,300)→(266.7,200)`、等价物理点方向一致；640×480 缩放为 `[640,480]`；两页面 `pageerrors: []`。
- `test_pyserver.py` **54/0**（新增 6 条商店列义用例）、`test_tick_systems.py` **53/0**、`test_all_messages.py --live 8080` **20/0 + ping→pong PASS**（新增「商店返回多种价格」「首个商品价格 = StdItems.Price × % 倍率」）。
- `node scripts/test_audio.mjs` **19/19**、`convert_audio.py --self-test` **12/12**；`build_plan()` 与 `audio_map.json` 的 4 个 BGM 键逐项一致；全部改动文件 `node --check` / `py_compile` 通过。
- `git diff --check` 通过；`HANDOFF.md` 零改动。注：`frontend/audio/audio_map.json` 从生成起**整文件就是 CRLF**（HEAD 与工作区均为 3654 行 CRLF），git 默认会把新增行行尾的 `\r` 判成 trailing whitespace；本仓已设置本地 `git config core.whitespace cr-at-eol`（只影响空白检查、不进版本库），否则可用 `git -c core.whitespace=cr-at-eol diff --check`。

## 18. 对照 Delphi 原版的地图/掉落/尸体与登录动画（2026-11-10 第七轮，实测反馈）

本轮起因：用户反馈「① 大地图位置不太对 ② 掉落位置似乎也不对 ③ 鹿等怪死后没有尸体、不能挖肉，需要参考 Delphi 原版对比」，随后补充「条纹还在，只是变浅了；再次登录就恢复了」「④ 主菜单返回人物选择无效 ⑤ 登录失败文字位置不对、登录成功应该有个开门动画」。

**取证方式**：下载 `StarboyChina/MIR2-1` 的 `GameOfMir/MirClient/*.pas` 与 `M2Server/*.pas`（`ClMain / PlayScn / IntroScn / Actor / AxeMon / Grobal2 / MapUnit / FState / SoundUtil / ObjBase / M2Share`），逐条核对 Pascal 原文与常量号；调研笔记为临时文件，结论全部沉淀在本节。用户约定「找不到权威依据不硬改」，本轮每条改动都对应到具体行号。

### 18.1 六项根因与修复

| 反馈 | 权威依据 / 根因 | 修复 |
|---|---|---|
| 大地图（及小地图）位置不对 | `MiniMap.txt` 的索引是 **1-based**；原版 `ClMain.pas` 收 `SM_READMINIMAP_OK` 后以 `g_WMMapImages.Images[g_nMiniMapIndex-1]` 取图。我们直接拿 `0 101` 的 101 去 `mmap.wil` 取图，取到的是另一张 **900×599** 的地图；正确应为 index 100（**1052×699** ≈ 比奇省 700 格 ×1.5 / 700 格 ×1）。§17 里「底图尺寸与 .map 不匹配」其实是**取错图**的误判 | `_loadMiniMapTexture()` 改 `index - 1`（`index > 0` 才取），注释写明 1-based；归一化换算 `纹理像素/地图格数` 保留（等价于原版 `mx=x*48 div 32, my=y*32 div 32`，且兼容兜底自生成图）；同步修正 §17 的错误注释。大地图点位与底图现在真正对齐 |
| 掉落位置不对 | 原版 `PlayScn.pas:1268-1297` 在**按行 `for j` 循环内**绘制 `DropedItemList`（`if j = DropItem.y - BlockTop`），锚点是格中心 `ix+HALFX-d.Width/2` / `iy+HALFY-d.Height/2`（`HALFX=24, HALFY=16`，Grobal2.pas）。我们以前在整帧末尾统一画，掉落物永远盖在同格角色/树木/建筑之上；服务端 `scatter_pos()` 又在 ±3 范围随机撒 50 次，物品常离尸体很远 | `MapActor.render()` 新增 `drawDrop + groundItems` 参数，按行调度（先于同行实体绘制）；`PlayScene._renderGroundItems()` 改为单格 `_drawGroundItem(ctx,g,sx,sy)` 并 `save/restore`。服务端 `scatter_pos()` 改为原版 `GetDropPosition`（ObjBase.pas:1534-1582）：半径 1 起逐圈环形扩散、避开已有掉落物，找不到才回退尸体格 |
| 动物死后没尸体、不能挖肉 | 原版 `SM_DEATH=32 / SM_SKELETON=33 / SM_NOWDEATH=34 / CM_BUTCH=1007 / SM_BUTCH=637 / RM_BUTCH=10119`（Grobal2.pas）；`Actor.pas:1595` 死亡只置 `m_boDeath` + `PlayScene.ActorDied()` **降绘制层级、不 Free**，`ClMain.pas:6506` 在 `SM_HIDE` 后 60 s 才释放；帧分 `ActDie`（倒地动画）与 `ActDeath`（尸体静止帧 `start + dir`，**不**乘 `frame+skip`）。挖肉是 **Alt+左键**点 3×3 内尸体，`MakeDefaultMsg(CM_BUTCH, actorid, x, y, dir)` 带 actor id；服务端 `ObjBase.pas:17458` 要求 `m_boDeath && !m_boSkeleton && m_boAnimal`，鸡 51/鹿 52/狼 53（M2Share.pas:148-150），**动物死亡不走 ScatterBagItems**，肉由 `TakeBagItems` 给「肉」(StdMode 40)。我们 `update_sight()` 里 `a.alive` 过滤在死亡瞬间就发 `entityRemove`，尸体根本没机会显示 | 服务端：`Monster` 加 `corpse_until / butch_count / skeleton`；`kill_monster()` 对 `butchable()`（race 51/52/53 + 动物 appr 43/70/160/161，排除「护卫」）保留尸体 60 s 且**跳过 `drop_loot()`**；新增 `_sight_actors()` 让快照/视野包含未过期尸体（`nearby()` 仍只含活体，避免怪打尸体）；`brief_json()` 下发 `alive/corpse`；新增 `butch` handler（距离 ≤2、挖 2 次出肉、鸡→「鸡肉」/其他→「肉」依据本地 `MonItems`，下发 `bagUpdate` + 广播 `butch` / `entityRemove`）。前端：`ActorSprite` 加 `death` 动作段（`ActDeath.start + dir`）与 `prefetchDeathFrame()`（否则 `getLayers` 回退到站立帧，看着像「怪还站着」）；`PlayScene` 收 `death{corpse:true}` 先播 `die` 1.4 s 再切 `death`、尸体不画名字/血条、Alt+左键 3×3 找尸体会带 `actorId` 发 `butch` |
| 条纹只是变浅、重登就恢复 | 用户这句定位了真因：冷缓存（首次登录）时首屏纹理常超 6 s，`_prefetchVisible()` 超时把**仍在飞的请求**标记 `failed`；`_floorTex()` 随即删占位重发，与增量预取 `Promise.race` 8 s 超时叠加成**重复请求风暴**，整片格子长期拿不到图 → 兜底色连成横带；重登时 HTTP 缓存已热才「恢复正常」。§17 只把兜底色改亮，所以条纹变浅但还在 | 超时**只放行显示、绝不判 failed**：原 promise 完成时自行写入同一个占位；`_prefetchAround()` 把缓存写入移进各 `getTexture().then()`，`Promise.race` 只用于不阻塞后续批次、结果不再覆盖；增量预取改**行优先**（24 格一批覆盖一整行而不是一列 24 行）；`_ensureFloor()` 增加与坐标解耦的 48×32 **真实地砖像素快照** `f.sample`，整行未就绪时铺真实地砖而非纯色（滚动搬运后 `lastPainted` 坐标会失效，sample 不会） |
| 主菜单返回人物选择无效 | 旧 `_logout()` 只发 `logout`，服务端直接 `session.close()`；客户端收到 close 没有任何场景切换，点了等于没反应 | 新增 `backToSelect` 消息：服务端 `on_disconnect()` 清理世界但**保留登录态与连接**，回 `backToSelectResult` + `characterList`；前端切 `SelectCharScene`，HUD 两个退出按钮统一走回选角（浏览器里无法真正关客户端） |
| 登录失败文字位置 / 缺开门动画 | 原版 `SM_PASSWD_FAIL` 走 `FrmDlg.DMessageDlg` **居中弹窗**（`FState.pas:2407` 白字黑边、`\` 分行、行距 14），`LoginScene.PassWdFail` 只重新显示输入框并不在登录框里画字。开门在 `IntroScn.pas::TLoginScene.OpenLoginDoor`：`m_nMaxFrame=10`、**300 ms/帧**，底图 `ChrSel[22]`，门帧 `ChrSel[23+frame]`（496×361），画在 `((800-w)/2+1, (600-h)/2-23)`，音 `s_rock_door_open=100`（SoundUtil.pas），播完淡出 `ChangeScene(stSelectChr)` | 登录失败改 `_showMsgDlg()` 居中模态框（320 px 宽、白字黑边、`\` 分行、确定按钮），删掉登录框底部 `left=24,top=224` 那行错位文字；登录成功改走 `_startDoor()`：隐藏登录框、播编号 100 音效、预取 10 帧门、逐帧绘制，10 帧后 300 ms 黑幕淡出再切选角 |

### 18.2 验证（服务端/回归 `D:/Python310/python.exe`，Playwright `D:/Python39/python.exe`）

- 浏览器端到端 **15/15 PASS**：登录失败弹窗文字与居中 `(400,300)`；登录成功 0.5 s 时仍在 `LoginScene` 且 `_door.active`、`frames=10`；3.3 s 后到 `SelectCharScene`；`_miniMapIndex=101 → _miniMapTex=[1052,699]`；小地图非零像素 `14400/14400`；大地图 `40965` 个亮像素、标题「比奇省 (296, 500)」；正常帧与「整片格子人为未就绪」两种情况下兜底纯色横带数均为 `0`、`f.sample=true`；主菜单返回后 `SelectCharScene` 且 `connected=true`、角色列表仍可用；`MapActor.render` 收到 `["function", true]`；`pageerrors: []`。
- 尸体专项 **8/8 PASS**：倒地动画 → `death` 帧（鹿 `mon17/700`，与 die 帧 `mon17/360` 不同）、纹理已预取 `hasTex=true`、可绘制、无名字血条（截图目视确认侧躺鹿尸）、Alt+左键发出 `{actorId, x, y}`。
- `test_pyserver.py` **54/0**、`test_tick_systems.py` **53/0**、`test_all_messages.py --live 8080` **27/0 + ping→pong PASS**（新增 8 条：掉落环形扩散、尸体保留/不掉物品、尸体在视野、战斗不含尸体、挖两次出肉、bagUpdate、backToSelect 不断线；消息类型 **46 → 48**，`messages_for()` 与执行顺序同步补齐）、`node scripts/test_audio.mjs` **19/19**、`convert_audio.py --self-test` **12/12**。

### 18.3 边界（未硬改）

- 只给**可挖肉动物**保留尸体；其他怪死亡仍按原逻辑移除（原版所有怪都有尸体，本移植为避免 actor id 与 15 s 重生冲突先不铺开）。尸体实际存活到重生（约 15 s）而非原版 60 s。
- 挖肉次数简化为 2 次（原版 `m_nBodyLeathery` 50~150、`m_nMeatQuality` 递减，本移植无这两个字段）；未实现 `SM_BUTCH` 让别人看到蹲下（`CM_SITDOWN` 动作段本移植没有），肉品质也未写进 `Dura`。
- 尸体帧按 `ActDeath.start + dir` 取一张；原版部分怪（骷髅类、`ActDeath.frame>1`）还有多帧，未逐怪适配。
- 大地图仍是「整张 mmap 底图等比铺进 DOM 面板」的简化实现；原版 `MapUnit.DrawMiniMap`（`SCALE=4`、`MX=12/MY=8` 的 540×360 surface）是另一套遗留代码，`TAB` 实际绑定在原版是 `M` 键，未照搬。
- 开门只做淡出，未做 `g_nFadeIndex` 的选角淡入；失败弹窗用 DOM 样式近似 `g_WMainImages[360/380/381]` 底图。
- 条纹是冷缓存竞态：无头 Chromium 本地资源秒开，只能用「人为把整片纹理置为未就绪」验证 `sample` 生效；真实冷启动观感仍需用户 `Ctrl+F5` 自测。

## 19. 怪物伤害观感排查 + 目标红框对齐原版 ceBright（2026-11-11 第八轮，实测反馈）

### 19.1 反馈与结论

用户反馈：①「怪物打我似乎不掉血，但有被击中的声音和动画帧」；②「选中怪物外面一圈红色边框是调试用的么？原版没有」。

**① 结论：不存在不掉血 bug，是伤害数值太小 + 自然回血抵消造成的观感问题。**

- 服务端链路完整：`_monster_ai` → `_apply_hit_to_player`（`target.hp -= dmg`）→ `_send_hits`（广播 `attack` + 单播 `stats`）→ 死亡走 `kill_player`。3.10 离线定量复现（新建 26 HP 角色、鸡贴身）连续 6 次：`hp 26→25→24→23→22→21→20`，每次成对下发 `attack(damage=1, targetId)` 与 `stats(id, hp, maxHp)`，无丢消息。
- 伤害公式 `max(1, m.roll_attack() - p.combat.acMax)`：新手怪实测区间 **鸡 1 / 稻草人 1-2 / 鹿 2-4 / 多钩猫 2-4 / 钉耙猫 2-4 / 半兽人 4-9 / 毒蜘蛛 6-9**；新建角色 29 HP、acMax=0，一次挨打血条（40 px 宽）只掉 **1.4~5.5 px**，血球约 2~6 px。
- 前端 `case 'attack'` 只负责攻击者动作、命中帧（170 ms）、受击动作与伤害飘字；**HP 由 `stats` 单播更新**——与原版一致：本地 `MirClient/Actor.pas:1550` 的 `SM_STRUCK` 分支里 `Abil.HP := msg.x` 等三行是被注释掉的，HP 走 `HealthSpellChanged()` 单独同步。
- Playwright（3.9）端到端验证：`entities.get(self.id) === self`（引用未分离）；注入 `stats(hp-10)` 后 `self.hp 29→19`、`_hudKey` 变化、`vitalsCanvas.toDataURL()` 像素变化；注入 `attack(damage=7)` 后 `damageNumbers` +1。全链路正常。
- 放大观感的是**自然回血**：`world.py::_regen` 每 `REGEN_INTERVAL_MS=2000` 回 1 HP，而鸡攻击间隔约 1000 ms、单次仅 1 点 → 净血量几乎不动，看起来「永远满血」。原版回复由 `m_nIncHealth/m_nPerHealth/m_dwIncHealthSpellTick` 控制（`M2Server/ObjBase.pas:3780-3861`，间隔 `600 - min(400, level*10)` ms），机制不同；本轮按「无充分依据不硬改」**未改回血逻辑**，只报告。另发现 `_regen` 的 `last_regen_at` 是 world 级共享计时（多玩家会互相抑制），同样留待后续。

### 19.2 目标红框：确认是移植期自加的调试可视化，已按原版替换

- 原版依据（本地 `D:\work\me\game\传奇\MIR2\GameOfMir`）：`MirClient/Actor.pas:2044` `if (g_FocusCret = self) or (g_MagicTarget = self) then ceff := ceBright;`；`cliUtil.pas:17` 定义 `TColorEffect` 含 `ceBright`、:1141 映射到 `BrightColorLevel`；`PlayScn.pas:1281` 地面焦点物品同样用 `DrawEffect(..., ceBright)`。**原版只让 sprite 变亮，从不画矩形框**；点击锁定的 `g_TargetCret` 没有任何视觉标记。
- 改动：`frontend/map/MapActor.mjs` 删除 `strokeStyle='#ff3b3b'` + `strokeRect(anchorX+4, anchorY-70, 40, 100)`；sprite 绘制时若 `e.isFocus` 用 `ctx.filter='brightness(1.28)'` 近似 ceBright（在 `save/restore` 内，只作用于精灵，不影响血条/名字）。`frontend/scene/PlayScene.mjs` 的 `isTarget`（点击锁定 `castTarget`）改为 `isFocus`（鼠标悬停 `magicTarget`，对应 `g_FocusCret`）；`castTarget` 逻辑保留用于锁定攻击/施法，只是不再画框。
- 验证：`node --check` 两文件通过；Playwright **PASS**——`MapActor.render` 收到的实体含 `isFocus`、无 `isTarget`；设置 `magicTarget` 后 `focusCount=1`；画面 `#ff3b3b` 近似像素 19 个（HUD 贴图，远少于 40×100 框至少 80 个），`castTarget` 与悬停两态均无红框；HP 回归 29→24 正常。
- 回归：`test_pyserver.py` 54/0、`test_tick_systems.py` 53/0、`test_all_messages.py --live 8080` 27/0 + ping→pong。

### 19.3 排障工具更新

- 用户提供本地 Delphi 源码 `D:\work\me\game\传奇\MIR2\GameOfMir`（已同步，GBK 编码），项目级 skill `jootm2-original-debug` 已改为**本地优先**：新增 `scripts/pas_lookup.py`（GBK 自动转码、目录/文件过滤、上下文输出），SKILL.md 第 3 步与 `references/delphi-lookup.md` 同步更新，GitHub raw 仅作兜底。本轮两条原版依据均由该脚本秒级定位，无需联网。

## 20. 1.76 / 1.80 客户端资源差异分析（2026-11-11，只读分析）

用户在 `client_original/` 新放入 1.76 客户端，与 `resources/`（1.80 提取）做全量对比。产出 `docs/CLIENT_176_VS_180_DIFF.md`、`scripts/compare_client_versions.py`、`data/client_diff_176_180.json`；**本轮未改动任何运行代码或资源**。

- 来源校验：7-Zip 列两个 rar 清单，与磁盘三组资源目录按文件名+大小逐项核对，`rar 独有 0 / 磁盘独有 0 / 大小不符 0` → `resources/` 确为 1.80 的完整提取。
- 总体：1.76 是 1.80 的严格子集（**0 个 1.76 独有文件**）；1167→1509 文件、955.4→1558.6 MB。1.80 新增 16 组图库 / 128 张地图 / 179 个音效，改动 14 组图库 / 2 张地图 / 5 个音频；**28 组图库 + 526 张地图 + 540 个音效 MD5 完全相同**。
- 结论①（不能换）：`verify_resources.py` 判 1.76「不可用」——缺 `prguse3`（F10 装备页底图）、缺 `mon19/20/21`、`Weapon.wix` 比 `48+count*4` 短 4 B（前端 `loadIndex()` 读末项会越界），关键帧 3/4；1.80「可用」4/4。项目 `resources.json`、UI 帧号、怪物 appr→图库/叫声映射、小地图 1-based 索引均按 1.80 写死。
- 结论②（帧号）：`hum / tiles / magic2 / magicon` 是尾部追加、旧帧号兼容（`hum` 10799/10800 帧字节不变）；`npc / effect / mmap / mon18 / prguse / prguse2 / items / dnitems / stateitem` 为重排重绘，`mon18` 帧数 3707→1630（部分怪迁到 `mon19~23`），`prguse` 帧数不变但 290/510 帧尺寸改变。项目关键帧 `prguse[370]`、`mon5[2669]`、`mon18[821]` 在 1.76 仍命中，`prguse3[29]` 缺库。
- 结论③（BGM 根因）：1.80 把两组曲子**内容对调了文件名**（MD5 交叉验证）：`log-in-long2.wav` ↔ `game over2.wav`（68.6 s ↔ 34.3 s）、`main_theme.wav` ↔ `sellect-loop2.wav`（43.9 s ↔ 36.0 s），`sound.lst` 14155→18660 B。这就是 §17「intro/gameover 索引对调」的根因：Delphi 曲目编号常量没变，变的是文件内容；若改用 1.76 Wav 必须把该对调还原。
- 结论④（地图）：仅 `0.map/1.map` 改动，尺寸不变、4831/1955 字节、2040/872 个 tile，差异落在 12B tile 的 0/1/4/5/10 字节（图层索引字段）；新增 128 张多为 `em000~em063` 活动模板图，均不在 `MapInfo.txt` 登记内，两版都缺同样 71 张，覆盖率无提升。
- 用法：1.76 保留为「1.80 资源是否被私服魔改」的字节级校验基准，不作运行资源；复跑 `D:/Python310/python.exe scripts/compare_client_versions.py`。

## 21. Docker 真机构建与部署（2026-11-11，第九轮）

§5.1 遗留的「Docker daemon 未启动、只做过等价目录模拟」在本轮补齐：用户本机 Docker 24.0.7 + Compose v2.23.3 真机构建并全量验证通过。**本轮只动 `.dockerignore` 与 `docker-compose.yml`，`Dockerfile` / 代码 / 协议零改动。**

### 21.1 resources 是否打入镜像的评估结论：**不打，维持卷挂载**

- 打入：镜像 122MB → ~1.8GB（resources 1.6G = Data 1.2G + Map 204M + Wav 191M + Audio 16M），且每次 build 都要把 1.6G 打包进 build context 传 daemon，构建极慢；换资源要重建镜像；镜像分享会把客户端素材带出去（版权边界）。
- 挂载：镜像保持 122MB；部署时 rsync 一次资源到服务器；资源与代码独立更新。`Dockerfile` 注释「resources/ 由卷挂载（客户端素材不随镜像分发）」的设计意图不变。

### 21.2 修掉的 3 个部署坑

| 坑 | 修复 |
|---|---|
| `.dockerignore` 未排除 `client_original/`（441MB 原始客户端存档）→ build context 被打进 daemon | `.dockerignore` 加 `client_original`、`docs` |
| `gamedata/Mir200/Map` 是 NTFS junction → Docker CLI 打包 context 会跟随 junction 把 204MB `.map` 打进镜像；且 **Linux 服务器 checkout 没有 junction**（gitignore 不跟踪），镜像内该目录为空 → 服务端回退 256×256 全通行图、阻挡/门失效（§5.3 的坑在容器里复现） | `.dockerignore` 加 `gamedata/Mir200/Map`（context 不带地图）；compose 嵌套挂载 `./resources/Map:/app/gamedata/Mir200/Map`，服务端 `.map` 与前端 `/app/resources/Map` 同源，服务器零手工操作。想单独维护服务器地图时删掉该挂载行即可 |
| Java 对照服务同样读 gamedata 地图 | `jootm2-java` 补同一行嵌套挂载 |

### 21.3 真机验证（全绿）

- `docker compose build` 成功，镜像 `jootm2:python` **122MB**。
- 启动日志：`445 map defs, 2173 gates`、`spawned monsters=704`（与 §5.3 本机直跑数字一致，证明嵌套挂载生效、非回退图）；`resources.json` 生成 images=58 / maps=656 / wavs=723。
- HTTP：`/api/status`（monsters=704）、`/api/migration` **48/48** pending=0、`resources.json` 200、`Hum.wil` Range 分片 **206**、`audio_map.json` 200、`Audio/103.mp3` 200 audio/mpeg、`Map/0.map` 200。
- WS：`smoke_ws.py --port 8080` **8/8**；`test_all_messages.py --live 8080` **27/0 + ping→pong**（按 §14.5 用 `D:/Python310/python.exe`）。
- 健康检查 `healthy`；`docker compose restart` 后 SQLite 数据持久（`./data` bind mount）、资源索引重建正常。
- 注：容器 `./data:/app/data` 与本机开发共用同一 `data/jootm2.db`，本轮冒烟在库里新增了测试账号；服务器部署不受影响。

### 21.4 服务器部署步骤（Linux）

```bash
# 1. 传代码（不含 resources/data/client_original）+ 资源
rsync -a --exclude resources --exclude data --exclude client_original --exclude target ./ user@server:/opt/jootm2/
rsync -a ./resources/ user@server:/opt/jootm2/resources/     # 1.6G，一次即可
# 2. 服务器上构建并启动
cd /opt/jootm2 && docker compose up -d --build
# 3. 验证
curl http://localhost:8080/api/migration    # 48/48
docker logs jootm2 | head                    # 应见 445 map defs / 2173 gates
```

带宽紧张时可先不传 `resources/Wav/` 内容（保留空目录）：音效走 `resources/Audio/*.mp3`（16MB），wav 仅是 404 回退；`Data/` 里 19 个未引用图库 476MB 也可先不传（见 §6.1.5，但 `verify_resources.py` 会告警）。

### 21.5 已实际部署到 gp-bench-3.hil（2026-11-11）

- 服务器：Ubuntu 22.04 / Docker 27.5.1 / **docker-compose v1（1.29.2，无 `docker compose` 插件，用 `docker-compose` 命令）**，compose 文件无 `version` 键 v1 可直接解析；磁盘 94% 已用（剩 15G），部署占 ~2.5G。
- 步骤：`rsync` 代码（排除 resources/data/client_original/target/.git）→ `docker-compose build` → `rsync resources/`（1.65G / 2233 文件）→ 部署在 `~/jootm2`，`docker-compose up -d`。
- 两个坑：① rsync 会跟随 NTFS junction，把 `resources/Map` 204MB 复制进 `gamedata/Mir200/Map`（已在服务器删除，compose 嵌套挂载遮住该路径，无影响）；② 传前实测 `ss -tln` 确认 8080 空闲。
- 验证（全绿）：启动日志 `445 map defs / 2173 gates`、monsters=704；`/api/migration` 48/48 pending 0；`resources.json`/mp3 200、WIL Range 206；容器 `healthy`；从本机走 LAN `/api/status` 31ms、`smoke_ws.py --host gp-bench-3.hil` **8/8**。
- 更新部署：在开发机改完代码后 `rsync` 增量代码（compose 的嵌套挂载让 resources 无需重传）→ `docker-compose up -d --build` 即可，SQLite 数据在 `~/jootm2/data/`。

### 21.6 已部署到阿里云 root@aliyun.notmmao.com + 镜像推送 ACR（2026-11-11）

- 服务器：Alibaba Cloud Linux 3 / Docker 25.0.3 / **Compose v2（`docker compose`）**；磁盘剩 7.4G（81%）、**内存仅 1.8G**（纯 asyncio 服务端实测正常，容器运行后宿主剩 ~920M available）；部署在 `/root/jootm2`，8080 空闲（部署前 `ss -tln` 实测）。
- **镜像构建的坑**：服务器 `/etc/docker/daemon.json` 配的加速源 `d.notmmao.com` 对 `python:3.12-slim` 返回 **429**，备用 aliyuncs 个人源未走通，直连 `registry-1.docker.io` 超时 → 服务器上 `docker compose build` **不可用**。解法：本机 `docker save jootm2:python | gzip`（122MB → 42MB）→ `rsync` → `docker load`。
- 资源传输同 §21.5（rsync 跟随 junction 多复制 204MB 地图，同样已删）；`docker compose up -d` 后验证全绿：445 map defs / 2173 gates、monsters=704、migration 48/48、Range 206、公网 `/api/status` 75ms、`smoke_ws.py --host aliyun.notmmao.com` **8/8**。
- **镜像已推送阿里云 ACR**（个人版，上海）：`crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python`，标签 `a5784cc` 与 `latest`（digest `sha256:9d25ee3141…`，与本地 `jootm2:python` 同 ID `d5237ece2fc0`）。镜像**不含客户端素材**（resources 不打入），无版权问题。已验证阿里云 ECS 可直接 `docker pull`。
- **以后部署/更新的推荐路径**（绕开 429，无需在服务器上构建）：
  ```bash
  # 开发机：代码改动后构建并推送（版本号用 git 短 hash）
  docker build -t crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python:<hash> .
  docker push ...  # 或按 §21.5 的 rsync + 服务器构建路径（gp-bench-3 可构建）
  # 服务器（任一台）：
  docker pull crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python:latest
  docker tag  crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python:latest jootm2:python
  cd ~/jootm2 && docker compose up -d     # resources/data 已就位则无需重传
  ```
  compose 的 `image: jootm2:python` 名字不变，`docker tag` 打回本地名即可复用现有 compose 文件，无需改动。

### 21.7 e8b8f62 更新部署 + ICP 备案拦截坑（2026-11-11）

- `e8b8f62`（IndexedDB 分片缓存 + ETag/304/gzip + Loading 预加载）已推送 ACR（标签 `e8b8f62` + `latest`，digest `sha256:a24227e39b…`，image `31b1f7db8cd3`）并按 §21.6 推荐路径部署到阿里云（pull → tag → `compose up -d`）。
- 新功能实测（服务器本机）：ETag/`If-None-Match` 二次请求 **304 (0B)**；gzip 后 `main.mjs` 4751B → 2440B。
- ⚠️ **重要坑：阿里云 ECS 的 ICP 备案拦截**。`notmmao.com` 未备案，阿里云大陆节点会拦截未备案域名在 80/443/8080 的 HTTP 流量：公网所有请求返回 `403 Forbidden` + `Server: Beaver` + 「Non-compliance ICP Filing」拦截页（HTML iframe 指向 beian-block）。**且拦截有延迟**——刚部署完域名访问是通的（还做了 8/8 冒烟），约一小时后才生效，极易误判成代码 bug（本次就先怀疑了新 net.py）。判别方法：服务器本机 `curl localhost:8080` 始终 200 即为链路拦截；响应头 `Server: Beaver` 不是 pyserver 发的。
- **约定：访问阿里云服务一律用 IP `139.224.24.88:8080`，不用域名**（用户明确要求）。冒烟/回归用 `--host 139.224.24.88`；SSH `root@aliyun.notmmao.com` 不受影响。恢复域名访问需 ICP 备案，或改用非拦截端口（未验证）。

### 21.8 7b6e4a5 部署（2026-11-12）

- `7b6e4a5`（进游戏地图加载优化：首屏纹理预载 + `.map` 字节提前 + 206 分片应用层 gzip）推送 ACR（`7b6e4a5` + `latest`，digest `sha256:0d0b2d9d73…`，image `4e3af0dc8080`），按 §21.6 标准路径部署阿里云。
- IP 直连验证：`/api/status` 41ms；`.map` Range 分片 **206 + ETag**；`smoke_ws.py --host 139.224.24.88` 全通。
- **流程约定（用户要求记住）**：今后「部署」默认指阿里云；标准流程 = 本机 `docker compose build` → 推 ACR（git 短 hash + `latest` 双标签）→ 阿里云 `docker pull` → `docker tag` 为 `jootm2:python` → `compose up -d` → IP 直连验证。gp-bench-3 只在用户点名时更新。

---

## §22 公网加载速度：持久缓存 + 启动预加载（2026-11-11 晚）

### 22.1 定量基线（改之前）

用 CDP 抓同一个浏览器上下文里两次页面加载的响应：

| | 第 1 次（冷） | 第 2 次（应命中） |
|---|---|---|
| `.mjs` 模块 | 23 个 **200 全量** | 23 个 **200 全量**（`no-cache` 且服务端没有 ETag，无法 304） |
| WIL 分片（206） | 3 个 | **3 个，0 命中** |
| WIX 索引（200） | 2 个 | 2 个全部命中磁盘缓存 |

**根因**：WIL 纹理走 HTTP Range → 206 Partial Content，**Chromium 不把 206 写进 HTTP 缓存**，所以服务端给的 `Cache-Control: immutable, max-age=31536000` 对分片完全无效。加上 `.mjs/.json` 是 `no-cache` 且无校验器，每次都全量回源。进游戏后有 120+ 个 512KB 分片，公网（ECS 带宽 + RTT）下就是几十秒。

### 22.2 三层改动

**① 服务端 HTTP（`pyserver/net.py`）**
- 所有静态文件加 `ETag`（`size-mtime_ns`）+ `Last-Modified`；`If-None-Match` / `If-Modified-Since` → **304**；支持 **HEAD**。
- `cache_control()` 细分：`.html` = `no-cache`；`.mjs/.js/.css/.json` = `public, max-age=300, must-revalidate`；素材 = `immutable` 一年。开发期改代码用 Ctrl+F5 强刷即可。
- 文本/JSON/`.map` 走进程内 **gzip 预压缩缓存**（`GzipCache`，`gzip.compress(level=1, mtime=0)`，上限 64MB，`Vary: Accept-Encoding`）。实测 `main.mjs` 4635B → 2390B，`resources.json` 这类索引省得更多。
- **Range 206 绝不压缩**：`Content-Range` 描述的是真实文件字节偏移，压缩会破坏 WIL 解包。
- 新接口 `GET /api/asset-version`：对 `resources_root` 下所有 `.wil/.wix/.map` 的 `(相对路径, size, mtime_ns)` 求 sha1 前 16 位，进程内缓存 60s。**故意排除 `resources.json` / `audio_map.json`**（每次启动 `generate_resources_index` 会重写它们，纳入会导致一重启就全量失效）。实测服务重启前后版本都是 `5246e4b3c930fe8b`。

**② 前端持久缓存（新增 `frontend/gfx/assetCache.mjs`）**
- IndexedDB `jootm2-assets` / `chunks` store，按 `assetVersion` 做命名空间；版本变化自动清空。
- 接入三处：`scheduler.mjs` 的 Range 分片（缓存 `{buf,total}`，`total` 必须存，WIL 末张图位深判定依赖它）、`wil.mjs` 的 WIX 索引、`Maps.mjs` 的 `.map` 字节。
- 两级结构：同步内存 Map（同会话复用，24MB 上限）+ IndexedDB（384MB 上限，按 `byTs` 索引淘汰到 80%）；写队列 150ms / 16 条批量事务，`pagehide` 兜底 flush。
- **任何异常（隐私模式 / 配额 / IDB 被禁）静默降级**，绝不阻塞启动。
- 调试：`window.__assetCache.stats()` / `await window.__assetCache.clear()` / `window.__scheduler.stats()`。

**③ 启动 Loading + 分阶段预加载（新增 `frontend/core/boot.mjs`、`index.html` 内联层、`main.mjs`）**
- `index.html` 内联 `#boot-layer`（HTML 一到就显示，JS 没下载完也不黑屏），真实进度条 + 状态文字，完成后 450ms 淡出。
- 阻塞阶段：`/api/asset-version` ∥ `resources.json` → `assetCache.init()` → WS 连接 ∥ 音频索引 → **登录首屏必需纹理** → flush → 淡出。
- 后台阶段（登录界面出现 400ms 后，低优先级）：① 开门/选角/立绘纹理（并发 8）→ ② 全部 58 个 WIX 索引（合计仅 **0.98MB**，并发 2）→ ③ `0.map` 字节（5.88MB，**只暖字节不解析**，700×700 tile 对象解析会卡主线程）→ flush。
- **关键取舍**：开门动画 `ChrSel[23..32]` 是 10 张 800×600 大图，每张独占一个 512KB 分片。一开始把它们放进阻塞阶段，模拟公网冷启动要下 8 个分片 / 9.27s；挪到后台后降到 6 个 / **6.17s**，而登录成功后才需要这些帧（输账号的几秒足够预热）。
- 开关：`localStorage['jootm2.preload'] = '0'` 关闭后台预热；`window.__warmDone` 可等待后台完成。

### 22.3 验证（全绿）

| 项目 | 结果 |
|---|---|
| 二次打开（同一浏览器，等后台预热完成） | 资源响应 99 → 28；**206 12 → 0**；网络分片 12 → 0；IndexedDB 命中 71 次，省 13.6MB；首屏 1.24s → 0.64s |
| 两轮完整「登录→选角→公告→进游戏」 | 首次进游戏 125 个网络分片 → **二次 0 个**（命中 128）；地图 `0` 700×700 就绪；**0 个 JS 错误** |
| 模拟公网 100ms RTT / 4Mbps | 冷启动 **8.95s → 6.17s**（调整预加载后），缓存命中 **1.00s**，206 12 → 0，提速 **89%** |
| HTTP 层 | `/api/asset-version` 200 `no-store`；`main.mjs` gzip + ETag + 304 + HEAD 同长度；206 无 `Content-Encoding`；`index.html` `no-cache` |
| 回归 | `test_pyserver` **67/0**（新增 12 条缓存用例）、`test_tick_systems` 53/0、`test_all_messages --live 8081` 27/0 + ping→pong |
| 版本稳定性 | 杀掉并重开 8081 服务后 `/api/asset-version` 不变 |

### 22.4 部署与已知边界

- **必须重新构建镜像**：`Dockerfile` 把 `frontend/` 和 `pyserver/` 都 `COPY` 进镜像，8080 上跑的是 Docker 容器（本机 8080 端口属 `com.docker.backend.exe` 转发），改代码后要按 §21.6 重新 `docker build` → 推 ACR → 服务器 `docker pull` → `docker compose up -d`，仅刷新浏览器无效。
- Java/Netty 版没有 `/api/asset-version`，前端自动回退 `version='default'`，IndexedDB 缓存照常工作但**不会随资源更新自动失效**，需 `await __assetCache.clear()`。
- 素材版本含 `mtime_ns`：用不保留时间戳的方式重传资源（如不带 `-a` 的 rsync）会让版本变化、缓存重下一次；属预期行为。
- 音频 MP3 / BGM 仍靠浏览器 HTTP 缓存（`immutable`），未纳入 IndexedDB；首次播放仍会产生一次下载。
- 缓存只覆盖 `/resources/` 的不可变素材，不含任何账号/角色/游戏状态；`window.__assetCache.clear()` 可随时清空。
- 未做：206 分片压缩（需自定义非 Range 端点绕开 `Content-Range` 语义，风险高）、HTTP/2、Service Worker（`http://` 非安全上下文不可用，且 IndexedDB 已够用）。§23 已用「应用层协商头」把 206 分片压缩做掉，无需新端点。

---

## §23 进游戏后的「正在加载地图资源」：首屏纹理预载 + 206 分片压缩（2026-11-11 深夜）

用户反馈：§22 之后登录/首屏明显变快，但**进游戏后仍长时间显示「正在加载地图资源 x/y」**。公网实测地址按 §21.7 用 IP `http://139.224.24.88:8080/`。

### 23.1 定量诊断（连真实公网 + 离线复算）

| 观测 | 数据 |
|---|---|
| `MapActor.enter()` 分解 | `prefetch` 每次都精确 **6003/6008ms** —— 即必然命中 6 秒超时兜底；`loadMap()` 另占 6~14s（现下 `0.map`） |
| 超时瞬间的调度器 | `active=20 / queue=79`、`deduped=259`，即放行时仍有近百个请求在排队，地图出来后继续一块块冒 |
| 首屏帧集合（离线按 `0.map` @ (289,618) 复算） | 23×49 格 → **244 个不同纹理帧** → 约 **16 个 512KB 分片 / 7.7MB**（`objects` 5.6MB、`tiles` 1.5MB、`smtiles` 0.09MB） |
| 冷启动两次进游戏 | 第 1 次 9.6s、第 2 次 10.4s，仍各下 10 / 4 个 206 分片；`assetCache.hits` 几乎不涨是因为超时前没下完的分片根本没写进缓存 |
| 公网分片速度 | 单个 512KB WIL 分片 **4~16 秒**；`Prguse/Prguse3` 的 HUD/开门帧与 `Objects` 分片互相抢 6 条 HTTP/1.1 连接 |
| WIL 分片 gzip 率（实测样本） | `Tiles` 2.07x、`SmTiles` 1.94x、`Objects` 1.66x、`Prguse` 1.97x、level 1 已接近 level 6 |

**结论**：不是「缓存没生效」，而是这 7.7MB 纹理 + 5.9MB `.map` 从来没在进游戏之前开始下载；§22 的后台预热只做了 WIX 索引和 `0.map` 字节，且排在开门帧之后，公网窗口期根本轮不到。

### 23.2 改动

**服务端**
- `/api/asset-version` 响应增加 `start: {map,x,y,miniMapIndex}`（取 `GameWorld.default_map/start_x/start_y/map_mini_index`），前端 Loading 阶段就知道该暖哪张图。无 `world` 时降级为空对象。
- `characterList` 每个角色附带 `map/x/y`（角色上次下线位置），选角界面可精确预载；老前端忽略新字段，协议兼容。
- **206 分片应用层协商压缩**：前端带 `X-Jootm2-Gzip: 1` 时，服务端把该 Range 的原始字节 `gzip(level=1)` 放进 body，返回 `X-Jootm2-Gzip: 1` + `Content-Length`（压缩后），`Content-Range` 仍描述**解压后**的真实文件偏移。**故意不用 `Content-Encoding`**，避开 206 + `Content-Range` 在代理上的语义歧义（§22「206 绝不 gzip」的旧结论由此被取代，但只限这条自定义协商路径）。仅 `.wil/.wix/.map` 且分片 ≤2MB、压缩率 >10% 才启用；不带头的客户端原样返回。

**前端**
- `MapActor.mjs` 导出 `visibleRange()` / `collectVisibleTextures()`；`_prefetchVisible` 与新增预载模块**共用同一个帧集合函数**，杜绝「预载了一堆用不上的帧」。
- 新增 `frontend/core/mapPreload.mjs`：`preloadMapTextures(map,x,y)` 无超时全量预取首屏纹理 + HUD 常驻帧（`Prguse[1,4..15,30..39,128..142,370,371]`、`Prguse3[29,30,32,33,207]`）+ `mmap[index-1]`；并发 6；同 key 复用、不同 key 让位（精确坐标优先于默认出生点）；`preloadState` / `waitIdle()` / 进度回调；`localStorage['jootm2.preload']='0'` 可关。
- `boot.mjs`：`warmMapBytes(mapId)` 在 Loading 阶段**不 await** 地发起 `.map` 下载（gzip 后约 600KB，进游戏时 `loadMap` 直接命中 IDB）；后台顺序改为 开门/选角帧 → 等 `.map` 字节 → **首屏地图纹理** → 全量 WIX → flush。
- `SelectCharScene.mjs`：`characterList` 到达 / 切换角色时按真实坐标触发预载（旧服务端无坐标则用 `start` 兜底）；点「开始」时若预载已过半，最多再等 5 秒并显示 `正在载入地图资源 x/y`，**进度不足一半就放行**（慢网首开白等无益）。
- `scheduler.mjs`：发 `X-Jootm2-Gzip: 1`（有 `DecompressionStream` 时），收到响应头才用 `DecompressionStream('gzip')` 解压；`assetCache` 仍缓存解压后的原始字节，缓存 key 不变。**旧服务端不回该头 → 自动走未压缩路径**，前后端可分批部署。

### 23.3 验证

| 场景 | 结果 |
|---|---|
| 本地 3.10 直连，新前端 + 新服务端 | 预载 **244/244 完成**、纹理就绪 261/318、`gzip` 分片 61、**进游戏→地图就绪 0.03s**（原 6s+）、0 JS 错误 |
| 模拟慢网 500KB/s + 80ms RTT，gzip 关 / 开 | 预载进度 48/244 → **176/244**；首屏纹理就绪 158/318 → **234/318**；线上 206 字节 16.42MB → 14.82MB |
| 真实公网 A/B（旧服务端，用 `page.route` 注入新前端模块） | 进游戏→地图就绪 **24.33s → 6.01s**，点开始→地图就绪 24.78s → **11.34s**，206 分片 27→19、13.4MB→9.7MB，0 JS 错误（旧服务端仍触发 6s 超时兜底，因其无 gzip 与角色坐标） |
| 回归 | `test_pyserver` **72/0**（新增 5 条：`start` 字段、降级、206 协商压缩解压一致性等）、`test_tick_systems` 53/0、`test_all_messages` 离线 28/0、live 8081 **28/0 + ping→pong** |

### 23.4 部署与边界

- **必须重新 build 镜像**（§21.7 / §22.4）。本轮公网 A/B 只注入了前端模块，服务端 `start`/`characterList`/206 gzip **尚未上线**；重新部署后预载会再快约 2 倍，本地直连已验证可做到进游戏零等待。
- 旧服务端完全兼容：无 `start` → 后台暖 `0.map` 字节；`characterList` 无坐标 → 用 `start` 兜底；不识别 `X-Jootm2-Gzip` → 原样返回 206。
- 未做 / 保留边界：`changeMap` 换到新地图仍会现下新图的纹理（服务端可在接近传送点时预告，待做）；`_prefetchVisible` 的 6 秒超时兜底保留（防极端慢网卡死）；动画对象的全帧预取、WIX 全量索引仍可能与其他请求抢 6 条 HTTP/1.1 连接（HTTP/2 / 多域名未做）；音频 MP3 仍只靠浏览器 HTTP 缓存。
- 新增调试接口：`window.__mapPreload`（预载进度）、`await window.__warmDone`、`__scheduler.stats().gzip/gzipSaved`。

---

## §24 选角「退出」回登录 + 地面条纹收口 + 卡顿定量定位（2026-11-12）

用户反馈三件事：① 选角界面点「退出」不切回登录界面（但能听见登录 BGM）② 灰色条纹仍在（返回角色再进就消失）③ 本地 `127.0.0.1` 也明显卡顿，「记得之前没有」，排除网络。

### 24.1 「退出」= 关客户端 → 浏览器改为回登录

原版依据：`IntroScn.pas:1237 SelChrExitClick` 只有一句 `FrmMain.Close`（直接关掉客户端）；`FrmDlg.DscSelect1Click` 把 `DscExit` 派发给它。浏览器不能关闭非脚本打开的标签页，因此改为「断开连接 → 新建连接 → 切回登录界面」，与用户预期一致。

- `SoundManager.install(conn)` 拆成 `attach(conn)`（每条连接一次：挂 `'*'` 路由与 `onClose`，重置 `selfId/prevGold/prevHp`）+ `install(conn)`（一次性全局监听 + 首次 `attach`）。旧代码 `if (this.installed) return this` 会让新连接收不到任何消息。
- `SceneManager` 新增 `bind(conn)` 与方法位 `onReturnLogin`（由 `main.mjs` 注入）。连接工厂留在 `main.mjs`，避免 `LoginScene ↔ SelectCharScene` 循环依赖。
- `SelectCharScene._exit()`：调 `manager.onReturnLogin()`，无注入时退回 `conn.close()`。`main.mjs` 里的实现：`conn.close()` → `new Connection('/ws')` → `sound.attach` → `sceneManager.bind` → `await connect()` → `switchTo(new LoginScene(...))`。

### 24.2 灰色条纹：地面齐备才放行 + 多样本兜底

条纹的唯一来源是「地面层格子没画真实地砖，而是用兜底填充」。三处收口：

1. `MapActor._prefetchVisible` 拆两段：**地面层（tiles bng + smtiles mid）必须等齐**（硬上限 15s，进度回传），对象/动画帧尽力而为（6s）。地面缺一块就是一条横带，对象晚到只是单个贴图冒出来。
2. 新增导出 `collectFloorTextures()`（地面子集），与 `collectVisibleTextures()` 共用 `visibleRange()`；`_prefetchVisible` 据此分批等待。
3. 兜底借像素由「单个 `f.sample` 快照」改成 **24 格样本池**（`f.samples` + 按世界格坐标散列取样本）。单个快照会把同一格地砖重复铺满整行——那正是用户截图里一条条横纹的观感；样本池铺出来是多种地砖交错。
4. 超时**仍然绝不把在飞请求标 failed**（那会删占位重发，形成请求风暴）。

### 24.3 卡顿根因（定量，不是网络）

CDP 采样 + `rAF`/`setTimeout` 心跳双测把范围缩到两处**每帧同步工作**：

| 现象 | 定位 | 量化 |
|---|---|---|
| 未就绪格子**每帧**重新借像素重绘 | `_paintFloorCells` 里 `cells` 只有 0/1 两态，借用后不记账，下一帧又借一遍：整屏 200+ 格 × 每格最多 4 次「1760×957 画布自拷贝」 | 200KB/s 冷进图：rAF p50 **650ms**、11 帧里 10 帧 >33ms |
| 每个 512KB 分片到达就**同步解包块内全部图** | `WIL._unpackRange` 遍历 `imageCount` 逐张建 canvas + `putImageData`；20 并发分片同时到达就是几十~几百毫秒的主线程停顿 | 实测单分片 `mon19` 363 张 14.1ms、`mon20` 88 张 10.7ms；×20 并发 ≈ 280ms |

修法：

- `_paintFloorCells`：`cells` 改三态 **0=未画 / 1=真实地砖 / 2=已兜底画过**，借用后打 2；只有纹理就绪时才升级为 1。维护 `f.open`（未画真实地砖的格数），`open===0` 时整函数直接 return。
- `WIL`：删掉 `_unpackRange`/空闲队列，**只解当前请求的那一张**（单张 0.1~1ms）。分片字节本来就在 `assetCache`（内存 24MB → IndexedDB 384MB），相邻图再次请求零成本。偏移表改为显式排序的 `byOff` 再派生 `sortedOffsets`（不假设 WIX 偏移有序）。

修后同一场景（200KB/s、20 个请求在飞 + 136 排队）：rAF p50 **16.7ms**、6 秒 361 帧、**0 帧 >33ms**。

### 24.4 验证

| 场景 | 结果 |
|---|---|
| 200KB/s + RTT60ms 冷进图（选角立刻点开始） | 未画格 0、兜底横带行 0、重复行 0、`textures 78 就绪 / 0 失败 / 157 在飞` 也能出完整地面 |
| 同上帧率 | 限速 p50 16.7ms（0 帧 >33ms）；解除限速 p50 16.7ms |
| 真实 GPU 窗口（headed，144Hz 屏） | 选角/进图/加载中 rAF p50 13.9ms、无 long task |
| 「退出」流程 | `LoginScene=True`、新连接 `readyState=1`、用新连接重新登录 → 选角成功 |
| 回归 | `test_pyserver` **72/0**、`test_tick_systems` **53/0**、`test_all_messages --live 8081` **28/0** + `ping→pong` |
| 服务端 tick | 离线 300 次 `service.tick()`：p50 **1.08ms** / p95 3.64ms / max 8.09ms（空世界，怪物按地图懒生成） |

### 24.5 边界

- 地面 15 秒硬上限仍在：公网极慢时宁可多等地面，也不放行半张图（用户明确抱怨的是条纹）。此时加载界面显示「正在加载地图资源 n/m」，`PlayScene` 会把进度打在左上角。
- 首帧若地面纹理全未到（极端），仍是 24 格样本池拼出的「假地面」——观感是地砖花纹不对但不出现色带；纹理到达后逐格替换为真图。
- `assetCache` 内存缓存 24MB 会淘汰；淘汰后相邻图请求要走一次 IndexedDB 反序列化（仍不走网络）。
- 未做：把 WIL 解包搬进 Worker（`createImageBitmap` + transfer）；HTTP/2；`.map` 解析仍在主线程（首进图 100~300ms 一次性开销）。

---

## §25 多人旁观者血条不减少（2026-11-12）

用户反馈：多人时别人被怪打、身上确实在播受击动作，但我这边看到他的血条不动。

### 25.1 原因

`World.send_stats()` 对 `Player` 只发给**本人 session**（`:817 if isinstance(a, Player) and a.session is not None: a.session.send_json(o)`），怪物才广播给周围。而原版 `Actor.pas:1550` 里 `SM_STRUCK` 的 HP 字段是被注释掉的，自己那份走 `SM_HEALTHSPELLCHANGED`——于是在我们的实现里，**旁观者永远拿不到别人的血量变化**，`entityAdd` 时那份 hp 就定了终身。

### 25.2 改动

- 服务端 `world.py` 新增 `HP_SYNC_MS = 400` 与 `_sync_player_hp(now)`，在 `tick()` 里 `_tick_pending_ranged_hits` 之后调用：按 `_hp_sync_cache[id] = (hp, max_hp)` 比对，变化就向周围 `broadcast_around` 一条 `{type:'entityHp', id, hp, maxHp}`；**只带血量**（不带 ac/dc/exp，避免泄露他人面板），按 400ms 合批，无变化不重发，玩家下线时清缓存。
- 客户端 `PlayScene.onMessage` 新增 `case 'entityHp'`：更新对应实体的 `hp/maxHp`（`msg.id === self.id` 时忽略，自己以 `stats` 为准）。
- 回归 `scripts/test_tick_systems.py` 新增 `[10] 旁观者血条同步`（4 条）：广播内容与字段集合、无变化不重发、`HP_SYNC_MS` 内合批。

### 25.3 验证

`test_tick_systems` **57/0**（原 53 + 新 4）、`test_pyserver` 72/0、`test_all_messages --live 8081` 28/0 + `ping→pong`。

### 25.4 边界

- 血条最大延迟 400ms（一个 tick 周期的整数倍），原版同样不是逐帧同步。
- 别人的 MP / 经验 / 面板仍不同步（原版也不给）。宠物/召唤物的血量仍走怪物 `stats` 广播，不受影响。

---

## §26 鸡/鹿不主动攻击 + 怪物伤害的 0 门槛（2026-11-12）

用户反馈：鸡、鹿这些不该主动攻击人，现在会；而且会在城里咬人。

### 26.1 原版依据（本地源码）

- `M2Server/M2Share.pas:148-150`：`ANIMAL_CHICKEN = 51`、`ANIMAL_DEER = 52`、`ANIMAL_WOLF = 53`。
- `M2Server/UsrEngn.pas` 的建怪派发：`ANIMAL_CHICKEN` → `TMonster` + `m_boAnimal:=True`；`ANIMAL_DEER` → 1/30 概率 `TChickenDeer`（只会 `m_boRunAwayMode:=True` 逃跑，`m_nViewRange:=5`），否则 `TMonster`；`ANIMAL_WOLF` → `TATMonster`（会攻击）。
- 鸡/鹿在原版服务端表里 DC 为 0，`ObjBase.pas:22470 StruckDamage` 开头就是 `if nDamage <= 0 then Exit`；`TSpitSpider.SpitAttack` 同样是 `if nDamage <= 0 then Exit` 才发飞矢。所以它们**造不成任何伤害**。

### 26.2 改动

- `util.py` 新增 `PASSIVE_ANIMAL_RACES = frozenset({51, 52})`（鸡/鹿；**不含 53 狼**）。
- `world._resolve_target`：race 命中该集合直接返回 `None`（不锁玩家，只自己晃悠）——原版它们即使锁定也打不动，这里省掉「鸡追着人咬」的观感。
- `world._ranged_damage` 去掉 `max(1, …)`，返回原始 `DC 掷点 − acMax`，可能 <= 0：
  - 近战：`dmg <= 0` 时仍广播 `attack`（原版 RM_HIT 照发，有挥击动作）但带 `noDamage: true`，不结算、不飘字；
  - 远程：`dmg <= 0` 直接 `return`（原版 `SpitAttack` 不发飞矢）。
- 客户端 `PlayScene` 的 `attack` 分支：`msg.noDamage` 时只播攻击者挥击，目标不播受击、不出伤害数字。

### 26.3 验证

`test_tick_systems` **61/0**（新增 4 条：鸡 51 / 鹿 52 不锁玩家、狼 53 仍锁玩家、DC 0 怪对 AC5 玩家 `_ranged_damage <= 0`）、`test_pyserver` 72/0。

### 26.4 边界

- 鹿的「1/30 概率只会逃跑」这一分支没有单独建模：现在 51/52 一律不锁定玩家，行为上等价于「无害」。
- 城里为什么会有怪：`MonGen` 刷怪点若落在城墙内，仍会刷出来（本项目的刷怪表来自原版 `Mongen.txt`，比奇省的部分刷怪点靠近城门）。目前只保证它们不会主动咬人，没有做「城内禁刷/禁攻」的额外规则（原版靠刷怪表本身避开城内）。

---

## §27 主动/被动怪的原版控制方式与「中立怪」补全（2026-11-12）

用户问三件事：鸡鹿不主动攻击是写死的还是数据库字段控制？打它会不会还击？还有哪些同类中立怪？

### 27.1 原版机制：由 Monster.DB 的 Race 字段决定，再派发成不同类

`M2Server/UsrEngn.pas:1819 AddBaseObject` 是本项目唯一的建怪入口，内部是 `case nMonRace of`：

| race | 常量 | 实例类 | 行为 |
|---|---|---|---|
| 11 / 12 / 20 / 112 | SUPREGUARD / PETSUPREGUARD / ARCHER_POLICE / RC_ARCHERGUARD | 守卫系 | 只打红名或打过自己的人 |
| 51 | ANIMAL_CHICKEN 鸡 | `TMonster` + `m_boAnimal` | **中立**：不主动，被打还击 |
| 52 | ANIMAL_DEER 鹿 | 29/30 `TMonster`；1/30 `TChickenDeer` | 中立；少数只会逃跑 |
| 53 | ANIMAL_WOLF 狼 | `TATMonster` | 主动 |
| 55 | TRAINER 练功师 | `TTrainer`（`ObjNpc.pas`，NPC 类） | 不攻击 |
| 80 | MONSTER_OMA 奥玛 | `TMonster` | **中立** |
| 81~ | 各类 | `TATMonster` 及其派生 | 主动 |

行为差异的代码位置：

- `ObjMon.pas:415 TMonster.Run` —— **完全没有 `SearchTarget` 调用**，只有一个分支 `if m_TargetCret = nil then Wondering()`（20% 概率转向、5% 概率走一步）。所以 `TMonster` 不会自己找人打。
- `ObjMon.pas:610 TATMonster.Run` —— 每 1.5~3 秒（`m_dwSearchTime = Random(1500)+1500`）调一次 `SearchTarget`，`m_TargetCret = nil` 时每 1 秒就会找一次。
- `ObjBase.pas:2795 TAnimalObject.Struck(hiter)` —— 挨打时 `if IsProperTarget(hiter) then SetTargetCreat(hiter)`。**这就是中立怪还击的唯一入口**。
- `ObjBase.pas:17476` —— 死亡掉落里 `if (m_btRaceServer >= RC_ANIMAL)` 才 `ScatterGolds`；`RC_ANIMAL=50`（`Grobal2.pas:1086`）。
- `m_boAnimal` **不代表被动**：它只管挖肉的肉品质与尸体处理。蝎子（`TScorpion`）、毒蜘蛛（`TSpitSpider`）都是 `m_boAnimal := True` 的主动怪。

本项目 `gamedata/mud2/DB/Monster.csv` 里 race=52 的并不只有鹿：还有「羊」「护卫」「鹿10/20/30」；race=51 只有「鸡」；race=80 一个都没有。所以中立名单按 race 走天然包含羊。

### 27.2 改动

- `util.py`：`PASSIVE_ANIMAL_RACES = {51, 52}` → `NEUTRAL_RACES = {51, 52, 80}`（语义从「不攻击」纠正为「中立：不主动、被打还击」）。
- `util.GUARD_RACES` 补 `11`（卫士）/`12`（宠物卫士）——之前漏了，导致比奇城的卫士对白名玩家也主动开打。
- `world._resolve_target`：中立怪**不再直接 return None**，而是只沿用 `m.target_id` 里已有的目标（存活、同图、未隐身、距离 ≤ AGGRO_RANGE），不搜索新目标；目标跑远/死亡/隐身即放弃，回到闲逛。
- `handlers_combat._hate(m, p)`：命中时同时写 `last_hiter_id` 与 `target_id`（对齐 `Struck → SetTargetCreat`）。近战、长Hit、魔法、范围伤害四条路径统一走它；**未命中不记仇**（原版 `StruckDamage` 只在真掉血时调用）。
- 回归 `test_tick_systems` 新增 3 条：鸡被打会还击、中立怪目标跑出仇恨范围后放弃、命中记仇同时写两个字段 → **64 PASS / 0 FAIL**。

### 27.3 边界

- 原版鹿「1/30 概率生成只会逃跑的 `TChickenDeer`」仍未建模，现在鹿一律是「中立还击」。
- 中立怪还击没有「打一会儿就放弃」的计时器，只有距离/死亡/隐身三种放弃条件；原版 `TMonster.Think` 每 3 秒用 `IsProperTarget` 复查，效果等价。
- 练功师 race=55 在原版是 `TTrainer`（NPC 类，不参与战斗）；项目把它当普通怪处理，本轮未动。

---

## §28 金币/物品掉落改成集中倍率表（2026-11-12）

用户要求：爆率别改单条，做成一张表按倍数调整。

### 28.1 原版数据保持不动

`gamedata/Mir200/Envir/MonItems/<怪物>.txt` 每行 `n/m 物品名 [数量]`，命中概率 `n/m`
（257 个文件、199 个里面带金币条目，例如 `1/4 金币 300`）。这份数据就是「原版爆率」，
本轮**一个字节都没改**。

### 28.2 新增 config/drop_rates.json

```json
{ "gold": 1.0, "item": 1.0, "goldAmount": 1.0,
  "monsters": { "鹿": {"gold": 2.0, "item": 2.0, "goldAmount": 1.5} },
  "items":    { "战神油": 0.5 } }
```

- `gold` / `item`：金币条目 / 其它物品条目的**概率倍率**；`goldAmount`：金币数量倍率。
- `monsters` 按怪物名覆盖，`items` 按物品名覆盖，两者都与全局**相乘**，概率封顶 100%。
- 倍率 `0` = 该类不掉；文件缺失/字段非法一律按 `1.0`，不影响启动。
- 判定：`p = rate * n / m`，`random() < p` 才掉。金币数量 `(count/2 + rand*count) * goldAmount`，最少 1。

实现：`pyserver/drop_rates.py`（`DropRates.probability/amount/hit` + `load_drop_rates`），
`world_extra.init_extra` 读 `config/drop_rates.json`，`drop_loot` 用 `rates.hit(...)` 替换原来的
`sel_point < int(random*max_point)`。Dockerfile 本来就 `COPY config/`，随镜像生效。

### 28.3 验证

`test_tick_systems` 新增 [11] 共 6 条：默认倍率等价原版、怪物覆盖相乘、物品覆盖相乘、
数量倍率覆盖、倍率 0 永不命中、倍率极大封顶必中，外加端到端 2 条（倍率 0 不产生掉落、
默认倍率下 `1/1` 条目必掉）→ **72 PASS / 0 FAIL**。

### 28.4 边界

- 倍率按**怪物模板名**匹配（`Monster.csv` 的 `Name`），不是刷怪名后缀（`鸡`/`鸡0` 视作不同条目）。
- 只影响怪物死亡掉落；商店价格、挖肉产出、任务奖励不走这张表。

---

## §29 新角色初始装备与服装店早期商品（2026-11-12）

用户反馈「人物不应该上来就光着身子」，并问服装店早期卖什么。

### 29.1 原版依据：新角色发 4 件东西

`ObjBase.pas:16400` 的 `if m_boNewHuman then` 分支，用 `!Setup.txt` 的
`Names/` 段名字发物品（默认值在 `M2Share.pas:1615-1619`）：

| 配置键 | 默认值 | 去向 |
|---|---|---|
| `Candle` | 蜡烛 | 背包 |
| `BasicDrug` | 金创药(小量) | 背包 |
| `WoodenSword` | 木剑 | 背包（本实现落到武器槽） |
| `ClothsMan` / `ClothsWoman` | 布衣(男) / 布衣(女) | 背包（本实现落到衣服槽） |

原版只塞背包，玩家自己穿。项目原来的建角代码按 **Idx 0/1/2/3** 发东西，而
`StdItems.csv` 里这四行是「命运之书 / 金创药(小量) / 魔法药(小量) / 肉」——
所以新角色背包里是一本书两块肉，没有衣服也没有武器，这就是「光身子」的直接原因。

### 29.2 改动

- `service._create_character`：改成**按名字**取 `蜡烛` / `金创药(小量)` / `木剑` /
  `布衣(男|女)`（按 `sex` 选），不再依赖 Idx。布衣写 `slot=U_DRESS(0)`、木剑写
  `slot=U_WEAPON(1)`，登录时 `Player.distribute_items` 会直接落到装备槽。
  与严格原版的差异只有一处：**布衣和木剑是穿上而不是留在背包**（用户明确要求不要光身子）。
- `gamedata/Mir200/Envir/Market_Def/3Tdr-0.txt`（比奇省「白家服装老板」）的 `[goods]`
  补 `布衣(男) 10 10` / `布衣(女) 10 10` 两行。该脚本原本**没有布衣**，最便宜的是
  轻型盔甲（11 级 / 3000 金币），1 级新人根本买不到能穿的东西。补的两条按
  `StdItems.Price=400 × 脚本 %100` 定价，与同版本 GM 服装店脚本 `gmyf1-8039.txt`
  的商品清单一致。文件保持 GBK + CRLF。

### 29.3 验证

- 临时脚本（已删）在数据库副本上真实走 `_create_character`，落库结果：
  `slot -1 蜡烛`、`slot -1 金创药(小量)`、`slot 1 木剑`、`slot 0 布衣(男)`，等级 1 / HP 26。
- `test_pyserver` 新增「比奇服装店卖 1 级布衣」→ **73 PASS / 0 FAIL**。

### 29.4 边界

- `蜡烛` 放背包（原版同）；玩家要自己装到右手槽才会点亮/消耗，未做自动装备。
- 只补了比奇这一家服装店；盟重/其他城的服装店脚本未动（它们本来就卖轻盔以上）。
- 新角色金币仍为 0（原版同），所以「买布衣」是给 400 金币之后的事。
