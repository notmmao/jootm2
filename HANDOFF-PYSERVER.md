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
- BGM 独立 `<audio loop>`，随 `loginResult`/`characterList` → select、`enterGame` → field、连接关闭 → intro 切换；`gameover/theme/heroLogin/heroLogout` 可用 `__sound.setBgm(key)` 手动切。
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
5. 登录成功 → 选角 BGM；进游戏 → `Field2`；走两步、打一下怪、喝药、捡金币、点背包，逐项听声。
6. 右上角「声音」按钮是**开关**：默认开启，所以**第一次点击是关闭**，再点一次恢复；日志会提示。音量用 `__sound.setVolume(0.5)` / `__sound.setBgmVolume(0.2)`。
7. 回退链路：临时移走 `resources/Audio/103.mp3`，点按钮应自动改播 `Wav/103.wav`；把 `frontend/audio/audio_map.json` 移走则退化到 `resources.json` 的 `wavUrl()`（非数字文件名音效会缺）。测完记得恢复。
8. HTTP 自查：`/audio/audio_map.json` → 200 `application/json`；`/resources/Audio/103.mp3` → 200 `audio/mpeg`；带 `Range: bytes=0-99` → 206 + `Content-Range`。

### 10.8 已知边界

- 15 个缺失编号（291/292/294/295/943/1503/1922/2103/2141/2151/9224/9234/10110/10111/10521）静默跳过。
- 玩家受击/死亡固定用男声 138/144：WS 消息没带性别，要女声改 139/145。
- 怪物叫声、逐技能精细音效未做（全量 mp3 已备齐，缺的只是映射表）。
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

### 13.2 验证

- 经验表：`len(NEED_EXPS)=101`；`exp_for_level(1)=100 / (65)=1700000000 / (101)=2140000000 / (200)=2140000000`，与 Java `ExperienceTable` 逐值一致；`level_for_exp` 现循环 1..101 全表，不再停在 65。
- 前端：`node --check` 两个改动文件均通过；Playwright 实烟（自动登录 `player/123456/TestHero` 进图）**无 console/page 错误**，画面非黑占比 92.9%，地图 700×700、271/276 纹理就绪 —— 证明 blend 解耦与命中帧改动不破坏渲染。
- 回归全绿：`test_pyserver` 48 PASS / `test_all_messages` 18/18 / `test_tick_systems` 53/0 / `test_audio` 18/18 / `compileall` 通过 / `git diff --check` 通过。
- 服务已重启（原 PID 29560 停用，新进程 `python -m pyserver` 已就绪，`/api/migration` 46/46，`onlinePlayers=0`），`world.py` 改动已生效。
- MISS 通道 + WIL 末张图：live 回归 18/18 通过（攻击怪物循环重试命中，miss 广播不破坏「造成伤害」断言）；`node --check` 全部前端改动文件通过；WIL 末张图位深改用真实 `fileSize` 判定，8/16 位均正确。

### 13.3 仍属「已知边界、未在本轮处理」（评估认定的低/中风险残留）

- 战斗「0 伤害」仍保留 `max(1,…)`（与 Java 版对齐的简化；原版防御>攻击可显 0 伤害，需另补 0 伤害通道）；**攻击玩家分支仍无独立命中判定（PvP 永不 miss）**——与攻击怪物分支不一致，属 PvP 行为级改动，本轮未动，待用户确认是否对齐原版。MISS 通道已补齐（见 §13.1 第二项）。
- 施法音为 `MAGIC_SFX` 近似映射（火系→137、雷/冰→8301/8302/8226，非逐技能）；受击/死亡固定男声（WS 未带性别）；缺怪物叫声（mp3 已备齐，差映射表）。
- 网络层无单连接频率限流与 `maxPlayers`/地图人数硬上限（`net.py` 仅队列 512）；冲撞(rush)无专属身位动画；受击姿势比原版多保持 ~300ms。
- 树/建筑的**对象层**仍每帧按行绘制（保遮挡）；动画对象帧切换（每 200ms）是原版行为，观感仍有轻微明暗变化。
- 真实带 GPU 浏览器的命中帧观感、blend 辉光观感、MISS 飘字观感仍需用户确认；本轮是无头 Chromium 定量对比 + 截图像素比对。

### 13.4 提交

见 `git log --oneline -1`（最新一条 `feat(python-server): ` 提交，4 个文件：2 前端 + `world.py` + 本 handoff）。
