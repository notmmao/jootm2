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

Java 后端已完整迁移为**纯标准库 Python（asyncio）**实现，46 种 WebSocket 消息全部有对应 handler，与 Java 版共用同一套协议 / SQLite schema / 配置文件；默认部署已切到 Python，Java 版仅作为双栈回归对照保留。**代码层面的转换已完成**，剩余的都是「边界项」与「优化项」，见第 6 节。

---

## 1. 分支与提交

| commit | 内容 |
|---|---|
| `cdfac57` | `feat(pyserver): 纯标准库 Python 服务端骨架 + 双栈协议回归基线`（15 文件 / +4079 行） |
| `75390e1` | `feat(pyserver): 全量迁移 46 种消息并切换默认部署到 Python`（19 文件 / +4497 行） |
| `ae0b59d` | `feat(pyserver): 补齐 tick 全子系统、装备属性与 MapQuest 任务`（7 文件 / +1093 行） |
| `d294744` | `fix(resources): 对齐 1.80 客户端资源布局与 .map tile 步进推断`（7 文件） |

分支共 4 个提交，相对基线改动 32 个文件 / +9614 −29 行，其中 `pyserver/` 17 个模块共 **6343 行**（Java 侧约 10478 行）。

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
```

最后一次全量验证结果（全绿）：`48 PASS`（离线 `test_pyserver.py`，含 2 条地图用例）/ `53 PASS`（live）/ `18/18` 成功路径 / `53/0` tick 断言 / `8/8` WS 冒烟 / `10/10` 体检自检 / `--full` 46 步响应类型全一致、字段结构零差异 / golden 回放零差异 / `node --check frontend/map/Maps.mjs` 通过。

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
| `config/server.properties` | **未改**，两个服务端共用 | 无 |

### 4.3 新增文件（不会与上游冲突）

`pyserver/**`、`scripts/golden/**`、`Dockerfile.java`、`HANDOFF-PYSERVER.md`（本文件），以及 6 个新回归脚本：`scripts/smoke_ws.py`、`scripts/test_pyserver.py`、`scripts/test_all_messages.py`、`scripts/test_tick_systems.py`、`scripts/verify_resources.py`、`scripts/compare_stacks.py`。

> 这些回归脚本在本分支都是**新建**文件，不是修改上游脚本。上游 `scripts/` 里原有的是 `fx_*.py` / `taoist_test.py` / `paradox_to_csv.py` / `highwall_check.py` 等调试脚本，本分支未动它们。

### 4.4 Java 侧**已知但未修**的小毛病（等用户点头才动 Java 代码）

1. `ResourcesIndexGenerator` 会把 `.wzl/.wzx` 也写进 `resources.json`，但前端 `frontend/gfx/wil.mjs` 只支持 WIL/WIX → 「索引生成成功但画面全空」的假兼容。Python 侧 `gamedata.py` 已改为**只索引 WIL/WIX 并告警**。
2. `MapFileReader` 写死 12 字节 tile；前端自适应 12/14。Python 侧 `map_tile_stride` 已完整处理。

另外几项**是 Java 自身既有局限，不算转换遗漏**，Python 与之保持一致：`MessageIds.java`（装备槽 `U_*` 常量，`util.py` 已覆盖）、`makeFireWall(p, targetId, …)` 定义了但全项目无人调用、`questParams` 不落库、`teleportRing` 只声明不消费、音效 `wavUrl/wavCount` 有定义无调用。

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
5. **磁盘瘦身**（约 723 MB 死数据 + `client_original/` 441 MB）：19 个从未引用的图库 476.1 MB（`deco / dragon / hair2 / humeffect / magic3~5 / mon22 / mon23 / objects2~10 / prguse2`）、Wav 189.1 MB（音效链路根本没接，一个都不会被请求）、282 张 MapInfo 未引用的 `.map` 49.5 MB、`Data` 里非 WIL/WIX 的 8.1 MB。⚠️ **Wav/ 目录要保留（可清空文件）**。删文件前必须先列完整清单再动手，别递归删。
6. **Docker 真机构建验证**：daemon 未启动，目前只有 `compose config` 语法校验 + 等价目录模拟。
7. **浏览器端完整试玩回归**：资源链路 / WS 冒烟 / golden 都通了，但没做「登录 → 建角 → 进图 → 移动 → 战斗」的浏览器端 E2E（Playwright 脚本上游有，见 `scripts/fx_*.py`）。
8. **音效链路**：`wavUrl/wavCount` 只有定义没有调用，`_toggleSound()` 只打日志。这是 Java 版也有的功能缺口，但属于「可玩性」层面的实际待办。

### 6.2 已完成、「全部完成」的口径

- 46/46 消息注册且全部可执行；33 个技能分支无异常
- `world.tick` 全子系统、`Player.recompute` 全属性、MapQuest 任务触发
- 双栈 46 步协议形状/字段零差异 + golden 离线回放
- 默认部署切 Python、Java 版保留为对照、Docker 缺 `gamedata/` 修复
- 1.80 客户端资源布局与 `.map` 解析修复（此前「客户端资源缺失」这项边界**已解决**）
- 浏览器不会下载 1.5 G：按需懒加载（`resources.json` 35 KB + 前端 `.mjs` 253 KB + WIX 索引几 KB + 覆盖所需图片的 512 KB Range 分片 + 进入某图时该图 `.map` 整文件），且全是 `localhost` 本机读取、不占公网带宽

### 6.3 不算遗漏的项

`MessageIds.java` 常量、`makeFireWall` 未调用、`questParams` 不落库、`teleportRing` 未消费 —— 均为 Java 自身既有局限，Python 与其对齐。

---

## 7. 合并 `origin/main` 的注意事项

**用户核心关切：避免冲突。**

- 仓库远程是 `https://github.com/jootm2/jootm2`，**默认分支叫 `main`，没有 `master`**；本地还有 `main` 分支与 `remotes/origin/main`。
- 当前 `origin/main` = `217e125` = 本分支基点，**此刻合并没有新内容**。上游前进后再合并时：
  - `HANDOFF.md`：本分支零改动 → **必然不冲突**（本文已把说明全部搬走）。
  - 全部新增文件（`pyserver/**`、`HANDOFF-PYSERVER.md`、`Dockerfile.java`、新增 scripts）：除非上游恰好建同名文件，否则不冲突。
  - ⚠️ **可能冲突的 6 个上游文件**：`README.md`、`Dockerfile`、`docker-compose.yml`、`frontend/map/Maps.mjs`、`.gitignore`、`.dockerignore`。建议 `git fetch origin` 后先看 `git diff --stat HEAD..origin/main` 是否碰这些文件；若碰到，取舍原则是「保留 Python 运行说明 + 上游新增玩法记录」。
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
